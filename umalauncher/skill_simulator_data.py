"""Convert the installed Global master into simulator inputs and visualizer metadata.

The mechanics conversion follows umasim/scripts/run_cm_pipeline.py; the display
formatting follows Bashin's extract_skill_eval_data.py. No remote skill source
or maintained skill snapshot is used.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter
from pathlib import Path

NORMAL_SKILL_ID_MIN = 200000


NORMAL_SKILL_ID_MAX = 899999


UNIQUE_SKILL_ID_MIN = 100000


UNIQUE_SKILL_ID_MAX = 199999


INHERITED_SKILL_ID_MIN = 900000


INHERITED_SKILL_ID_MAX = 999999


UNIQUE_SKILL_CATEGORY = 5


RARITY_NAMES = {1: "normal", 2: "rare"}


DEFAULT_SP_BY_RARITY = {1: 160, 2: 320}


SKILL_KIND_NORMAL = "normal"


SKILL_KIND_UNIQUE = "unique"


SKILL_KIND_INHERITED_UNIQUE = "inheritedUnique"


EFFECT_TYPES = {
    1: "passiveSpeed",
    2: "passiveStamina",
    3: "passivePower",
    4: "passiveGuts",
    5: "passiveWisdom",
    6: "oonige",
    9: "heal",
    10: "startMultiply",
    14: "startAdd",
    21: "currentSpeed",
    22: "speedWithDecel",
    27: "targetSpeed",
    28: "laneChangeSpeed",
    29: "temptationRate",
    31: "acceleration",
    35: "fixLane",
    37: "invokeRare",
}


SUPPORTED_OPPONENT_DEBUFF_EFFECTS = {
    "heal",
    "currentSpeed",
    "targetSpeed",
    "acceleration",
}


IGNORED_EFFECT_NOTICES = {
    8: "Vision effects are ignored.",
    13: "Opponent strategy disruption effects are ignored.",
    501: "Carnival bonus effects are ignored.",
    502: "Carnival bonus effects are ignored.",
    503: "Carnival bonus effects are ignored.",
}


MANAGED_NOTICES = set(IGNORED_EFFECT_NOTICES.values()) | {
    "Unsupported opponent-targeted effects are ignored.",
    "他者に対する効果は無視",
}


EFFECT_DISPLAY_NAMES = {
    "targetSpeed": "Target Speed",
    "speedWithDecel": "Speed With Decel",
    "currentSpeed": "Current Speed",
    "acceleration": "Acceleration",
    "heal": "Stamina",
    "laneChangeSpeed": "Lane Change Speed",
    "passiveSpeed": "Passive Speed",
    "passiveStamina": "Passive Stamina",
    "passivePower": "Passive Power",
    "passiveGuts": "Passive Guts",
    "passiveWisdom": "Passive Wisdom",
    "temptationRate": "Temptation Rate",
    "startMultiply": "Start Multiply",
    "startAdd": "Start Add",
    "fixLane": "Fix Lane",
    "oonige": "Runaway",
    "invokeRare": "Invoke Rare",
}


CONDITION_PATTERN = re.compile(
    r"([A-Za-z0-9_]+)(==|!=|>=|<=|>|<)(-?\d+(?:\.\d+)?)"
)


class PipelineError(RuntimeError):
    pass


def is_candidate_row(row: dict) -> bool:
    skill_id = int(row["id"])
    category = row.get("skill_category")
    return (
        NORMAL_SKILL_ID_MIN <= skill_id <= NORMAL_SKILL_ID_MAX
        or (
            UNIQUE_SKILL_ID_MIN <= skill_id <= UNIQUE_SKILL_ID_MAX
            and category == UNIQUE_SKILL_CATEGORY
        )
        or (
            INHERITED_SKILL_ID_MIN <= skill_id <= INHERITED_SKILL_ID_MAX
            and category == UNIQUE_SKILL_CATEGORY
        )
    )


def classify_skill_kind(row: dict) -> str:
    skill_id = int(row["id"])
    if row.get("unique_skill_id_1") or row.get("unique_skill_id_2"):
        return SKILL_KIND_INHERITED_UNIQUE
    if INHERITED_SKILL_ID_MIN <= skill_id <= INHERITED_SKILL_ID_MAX:
        return SKILL_KIND_INHERITED_UNIQUE
    if UNIQUE_SKILL_ID_MIN <= skill_id <= UNIQUE_SKILL_ID_MAX:
        return SKILL_KIND_UNIQUE
    if 300000 <= skill_id < 400000 and not row.get("is_general_skill", 1):
        return SKILL_KIND_UNIQUE
    return SKILL_KIND_NORMAL


def parse_condition_logic(value) -> list[list[dict]]:
    if not value:
        return []
    result = []
    for raw_or_block in str(value).split("@"):
        raw_or_block = raw_or_block.strip()
        if not raw_or_block:
            continue
        conditions = []
        for raw_condition in raw_or_block.split("&"):
            raw_condition = raw_condition.strip()
            if not raw_condition:
                continue
            match = CONDITION_PATTERN.fullmatch(raw_condition)
            if not match:
                raise PipelineError(f"Unsupported condition syntax: {raw_condition!r}")
            condition_type, operator, raw_number = match.groups()
            number = float(raw_number)
            if not number.is_integer():
                raise PipelineError(
                    f"Non-integer condition value is unsupported: {raw_condition!r}"
                )
            conditions.append(
                {"type": condition_type, "operator": operator, "value": int(number)}
            )
        if conditions:
            result.append(conditions)
    return result


def scaled_time(value) -> float:
    return 0.0 if value in (None, "") else float(value) / 10000.0


def compact_number(value):
    if value in (None, ""):
        return 0
    number = float(value)
    return int(number) if number.is_integer() else number


def is_self_target(row: dict, invoke_index: int, slot: int) -> bool:
    return row.get(f"target_type_{invoke_index}_{slot}") in (None, "", 0, 1)


def build_effect(
    row: dict,
    invoke_index: int,
    slot: int,
    skipped_effects: Counter,
) -> dict | None:
    ability_type = row.get(f"ability_type_{invoke_index}_{slot}")
    if not ability_type:
        return None
    effect_type = EFFECT_TYPES.get(ability_type)
    if effect_type is None:
        skipped_effects[f"ability_type_{ability_type}"] += 1
        return None

    value = row.get(f"float_ability_value_{invoke_index}_{slot}")
    if value is None:
        value = row.get(f"ability_value_usage_{invoke_index}_{slot}") or 0
    self_target = is_self_target(row, invoke_index, slot)
    supported_debuff = (
        not self_target
        and effect_type in SUPPORTED_OPPONENT_DEBUFF_EFFECTS
        and float(value) < 0
    )
    if not self_target and not supported_debuff:
        skipped_effects["unsupported_opponent_target"] += 1
        return None

    effect = {"type": effect_type, "value": compact_number(value)}
    target_type = row.get(f"target_type_{invoke_index}_{slot}")
    target_value = row.get(f"target_value_{invoke_index}_{slot}")
    if target_type not in (None, ""):
        effect["targetType"] = int(target_type)
    if target_value not in (None, ""):
        effect["targetValue"] = int(target_value)
    special = row.get(f"ability_value_usage_{invoke_index}_{slot}")
    if special not in (None, "", 0, 1):
        effect["special"] = int(special)
    additional = row.get(f"additional_activate_type_{invoke_index}_{slot}")
    if additional not in (None, "", 0):
        effect["additional"] = int(additional)
    return effect


def build_invokes(row: dict, skipped_effects: Counter) -> list[dict]:
    invokes = []
    for invoke_index in (1, 2):
        condition_text = row.get(f"condition_{invoke_index}") or ""
        precondition_text = row.get(f"precondition_{invoke_index}") or ""
        effects = [
            effect
            for slot in (1, 2, 3)
            if (
                effect := build_effect(
                    row,
                    invoke_index,
                    slot,
                    skipped_effects,
                )
            )
            is not None
        ]
        duration = scaled_time(row.get(f"float_ability_time_{invoke_index}"))
        cooldown = scaled_time(row.get(f"float_cooldown_time_{invoke_index}"))
        if not condition_text and not precondition_text and not effects and duration == 0:
            continue
        invoke = {"skillId": str(row["id"]), "index": invoke_index}
        conditions = parse_condition_logic(condition_text)
        preconditions = parse_condition_logic(precondition_text)
        if conditions:
            invoke["conditions"] = conditions
        if preconditions:
            invoke["preConditions"] = preconditions
        if effects:
            invoke["effects"] = effects
        if duration > 0:
            invoke["duration"] = duration
        duration_special = row.get(f"ability_time_usage_{invoke_index}")
        if duration_special not in (None, "", 0, 1):
            invoke["durationSpecial"] = int(duration_special)
        if cooldown > 0 and cooldown != 500:
            invoke["cd"] = cooldown
        invokes.append(invoke)
    return invokes


def skill_type(invokes: list[dict]) -> str:
    effect_types = {
        effect.get("type")
        for invoke in invokes
        for effect in invoke.get("effects", [])
    }
    categories = set()
    if effect_types & {
        "passiveSpeed",
        "passiveStamina",
        "passivePower",
        "passiveGuts",
        "passiveWisdom",
    }:
        categories.add("passive")
    if effect_types & {
        "targetSpeed",
        "speedWithDecel",
        "currentSpeed",
        "startMultiply",
        "startAdd",
        "oonige",
    }:
        categories.add("speed")
    if "acceleration" in effect_types:
        categories.add("acceleration")
    if "heal" in effect_types:
        categories.add("heal")
    if not categories:
        return "other"
    return next(iter(categories)) if len(categories) == 1 else "multi"


def invoke_summary(row: dict, invoke: dict) -> str:
    condition_text = row.get(f"condition_{invoke['index']}") or ""
    conditions = " OR ".join(
        f"[{block.strip()}]"
        for block in condition_text.split("@")
        if block.strip()
    )
    effects = []
    for effect in invoke.get("effects", []):
        text = f"{EFFECT_DISPLAY_NAMES.get(effect['type'], effect['type'])} {effect['value']}"
        if effect.get("special", 1) != 1:
            text += f" special={effect['special']}"
        if effect.get("additional", 0) != 0:
            text += f" additional={effect['additional']}"
        effects.append(text)
    duration = invoke.get("duration", 0)
    parts = [conditions, ", ".join(effects), f"Duration {duration}" if duration else "Instant"]
    return ", ".join(part for part in parts if part)


def apply_master_record(
    skill: dict,
    row: dict,
    name: str,
    description: str | None,
    need_points: dict[str, int],
    skipped_effects: Counter,
) -> dict:
    skill_id = int(row["id"])
    invokes = build_invokes(row, skipped_effects)
    skill.update(
        {
            "id": str(skill_id),
            "name": name,
            "group": int(row.get("group_id") or 0),
            "type": skill_type(invokes),
            "activateLot": int(row.get("activate_lot") or 0),
            "invokes": invokes,
        }
    )
    if (
        UNIQUE_SKILL_ID_MIN <= skill_id <= UNIQUE_SKILL_ID_MAX
        and row.get("skill_category") == UNIQUE_SKILL_CATEGORY
    ):
        skill["rarity"] = "unique"
        skill.pop("sp", None)
    elif (
        INHERITED_SKILL_ID_MIN <= skill_id <= INHERITED_SKILL_ID_MAX
        and row.get("skill_category") == UNIQUE_SKILL_CATEGORY
    ):
        skill["rarity"] = "inherit"
        skill["group"] -= 80000
        skill["sp"] = need_points.get(str(skill_id), 200)
        skill.pop("holder", None)
    else:
        rarity = row.get("rarity")
        skill["rarity"] = RARITY_NAMES.get(rarity, "normal")
        skill["sp"] = need_points.get(
            str(skill_id),
            DEFAULT_SP_BY_RARITY.get(rarity, 160),
        )

    info = [description] if description else []
    info.extend(invoke_summary(row, invoke) for invoke in invokes)
    skill["info"] = info

    notices = [
        notice
        for notice in skill.get("description", [])
        if notice not in MANAGED_NOTICES
    ]
    for invoke_index in (1, 2):
        for slot in (1, 2, 3):
            ability_type = row.get(f"ability_type_{invoke_index}_{slot}")
            notice = IGNORED_EFFECT_NOTICES.get(ability_type)
            if notice and notice not in notices:
                notices.append(notice)
    if notices:
        skill["description"] = notices
    else:
        skill.pop("description", None)
    return skill


EFFECT_NAMES = {
    "targetSpeed": "Target Speed",
    "acceleration": "Acceleration",
    "heal": "Stamina",
    "currentSpeed": "Current Speed",
    "speedWithDecel": "Current Speed",
    "laneChangeSpeed": "Lane Change Speed",
    "passiveSpeed": "Passive Speed",
    "passiveStamina": "Passive Stamina",
    "passivePower": "Passive Power",
    "passiveGuts": "Passive Guts",
    "passiveWisdom": "Passive Wisdom",
    "vision": "Vision",
    "strategyDisruption": "Strategy Disruption",
    "startMultiply": "Start Multiply",
    "startAdd": "Start Add",
    "temptationRate": "Temptation Rate",
    "fixLane": "Fix Lane",
    "oonige": "Runaway",
}


def compact_value(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def format_condition_blocks(blocks):
    if not isinstance(blocks, list) or not blocks:
        return ""

    formatted_blocks = []
    for block in blocks:
        if not isinstance(block, list):
            continue
        parts = []
        for condition in block:
            if not isinstance(condition, dict):
                continue
            condition_type = condition.get("type")
            operator = condition.get("operator")
            value = condition.get("value")
            if condition_type and operator is not None and value is not None:
                parts.append(f"{condition_type}{operator}{compact_value(value)}")
        if parts:
            formatted_blocks.append(" & ".join(parts))

    return " OR ".join(formatted_blocks)


def format_effects(effects, duration):
    parts = []
    for effect in effects or []:
        if not isinstance(effect, dict):
            continue
        effect_type = effect.get("type")
        if not effect_type:
            continue
        name = EFFECT_NAMES.get(effect_type, effect_type)
        value = effect.get("value")
        text = name if value is None else f"{name} {compact_value(value)}"
        if effect.get("additional"):
            text += " +"
        parts.append(text)

    if duration is not None:
        try:
            duration_value = float(duration)
            parts.append(f"Duration {compact_value(duration_value)}" if duration_value > 0 else "Instant")
        except (TypeError, ValueError):
            pass

    return ", ".join(parts)


def structured_effect_types(effects):
    result = []
    seen = set()
    for effect in effects or []:
        if not isinstance(effect, dict):
            continue
        effect_type = effect.get("type")
        if not effect_type or effect_type in seen:
            continue
        seen.add(effect_type)
        result.append(effect_type)
    return result

class SkillDataSnapshot:
    """One derived local file, invalidated by the installed database fingerprint."""

    def __init__(self, cache_dir):
        self.cache_dir = Path(cache_dir)
        self.path = None
        self.metadata = {}
        self.catalog = {}
        self._stamp = None

    def refresh(self, db_path):
        db_path = Path(db_path).resolve()
        stat = db_path.stat()
        stamp = (str(db_path), stat.st_mtime_ns, stat.st_size)
        if stamp == self._stamp and self.path and self.path.is_file():
            return
        self.path = self._stamp = None
        self.metadata = {}
        self.catalog = {}
        with sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            rows = [dict(row) for row in connection.execute("SELECT * FROM skill_data")]
            texts = {(row["category"], str(row["index"])): row["text"] for row in connection.execute(
                "SELECT category, [index], text FROM text_data WHERE category IN (47, 48)"
            )}
            costs = {str(row[0]): int(row[1]) for row in connection.execute(
                "SELECT id, need_skill_point FROM single_mode_skill_need_point"
            )}
        skipped = Counter()
        skills = []
        metadata = {}
        for row in sorted(rows, key=lambda row: row["id"]):
            if not is_candidate_row(row):
                continue
            sid = str(row["id"])
            name = texts.get((47, sid)) or f"Skill {sid}"
            description = texts.get((48, sid)) or ""
            skill = apply_master_record({}, row, name, description, costs, skipped)
            skills.append(skill)
            metadata[int(sid)] = {
                "id": sid, "name": name, "description": description,
                "rarity": int(row["rarity"]), "groupId": str(row["group_id"]),
                "baseCost": costs.get(sid, 0), "iconId": str(row["icon_id"]),
                "order": row["disp_order"], "skillKind": classify_skill_kind(row),
                "isFuture": False,
                "activations": [{
                    "conditions": format_condition_blocks(invoke.get("conditions")),
                    "preconditions": format_condition_blocks(invoke.get("preConditions")),
                    "effects": format_effects(invoke.get("effects"), invoke.get("duration", 0)),
                    "effectTypes": structured_effect_types(invoke.get("effects")),
                } for invoke in skill["invokes"]],
            }
        if not skills:
            raise ValueError("The installed Global database contains no skill definitions")
        # Do not retain a snapshot produced across a game database replacement.
        after = db_path.stat()
        if (after.st_mtime_ns, after.st_size) != stamp[1:]:
            raise RuntimeError("The game database changed while loading skills; click Run again")
        content = json.dumps(skills, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / "master-skills.json"
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_bytes(content)
        os.replace(temporary, path)
        self.path = path
        self.metadata, self._stamp = metadata, stamp
        self.catalog = {row['id']: dict(row, name=texts.get((47, str(row['id'])), ''),
                                       baseCost=costs.get(str(row['id']), 0)) for row in rows}
