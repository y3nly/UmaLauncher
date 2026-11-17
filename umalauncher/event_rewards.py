"""Format all possible game-provided rewards; never select a random branch."""
import re
import constants

PARAMETER_NAMES = {
    1: "Speed",
    2: "Stamina",
    3: "Power",
    4: "Guts",
    5: "Wisdom",
    10: "Energy",
    11: "Max Energy",
    20: "Mood",
    30: "Skill Pt",
    40: "Fans",
    51: "Speed Cap",
    52: "Stamina Cap",
    53: "Power Cap",
    54: "Guts Cap",
    55: "Wisdom Cap",
}

TRAINING_NAMES = {
    101: "Speed",
    105: "Stamina",
    102: "Power",
    103: "Guts",
    106: "Wisdom",
}

LIVE_PERFORMANCE_NAMES = {
    index: token_name.title()
    for index, token_name in enumerate(constants.GL_TOKEN_LIST, start=1)
}

EFFECT_VALUE_TYPE_NONE = 0
EFFECT_VALUE_TYPE_VALUE = 1
EFFECT_VALUE_TYPE_PARAMETER = 2
EFFECT_VALUE_TYPE_CHARACTER = 3
EFFECT_VALUE_TYPE_SKILL = 4
EFFECT_VALUE_TYPE_CHARACTER_EFFECT = 5
EFFECT_VALUE_TYPE_TRAINING = 6
EFFECT_VALUE_TYPE_TURN = 7
EFFECT_VALUE_TYPE_SCENARIO_LIVE_PERFORMANCE = 8
EFFECT_VALUE_TYPE_SCENARIO_VENUS_SPIRIT = 9
EFFECT_VALUE_TYPE_SCENARIO_LEGEND_BUFF_GAUGE = 10

DISPLAY_TYPE_TEXT = 0
DISPLAY_TYPE_SKILL = 1
DISPLAY_TYPE_ANOTHER_EVENT_SKILL = 2

EVENT_CHOICE_REWARD_META = {
    1: ("{0} +{1}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_PARAMETER, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE)),
    2: ("{0} -{1}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_PARAMETER, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE)),
    3: ("+{0} Fans", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE, EFFECT_VALUE_TYPE_NONE)),
    4: ("Friendship with {0} +{1}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_CHARACTER, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE)),
    5: ("Friendship with {0} +{1} (Friendship cannot change for Support Cards not in your deck)", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_CHARACTER, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE)),
    6: ("Hint lvl +{0}", DISPLAY_TYPE_SKILL, (EFFECT_VALUE_TYPE_SKILL, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE)),
    7: ("Gain", DISPLAY_TYPE_SKILL, (EFFECT_VALUE_TYPE_SKILL, EFFECT_VALUE_TYPE_NONE, EFFECT_VALUE_TYPE_NONE)),
    8: "Chance to gain a random skill",
    9: ("Become {0}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_CHARACTER_EFFECT, EFFECT_VALUE_TYPE_NONE, EFFECT_VALUE_TYPE_NONE)),
    10: ("Cures {0}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_CHARACTER_EFFECT, EFFECT_VALUE_TYPE_NONE, EFFECT_VALUE_TYPE_NONE)),
    11: ("Unlock recreation with {0}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_CHARACTER, EFFECT_VALUE_TYPE_NONE, EFFECT_VALUE_TYPE_NONE)),
    12: "End this Support Card's chain event",
    13: ("All attributes +{0}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE, EFFECT_VALUE_TYPE_NONE)),
    14: ("Random {0} attribute(s) +{1}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE)),
    15: "Cures all bad conditions",
    16: ("Randomly cures {0} bad condition(s)", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE, EFFECT_VALUE_TYPE_NONE)),
    17: ("Restrict {0} training", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_TRAINING, EFFECT_VALUE_TYPE_NONE, EFFECT_VALUE_TYPE_NONE)),
    18: "Restrict race entry",
    19: ("Previously trained attribute +{0}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE, EFFECT_VALUE_TYPE_NONE)),
    20: ("Previously trained attribute -{0}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE, EFFECT_VALUE_TYPE_NONE)),
    21: "Stat gains based on race grade",
    22: "Stat gains based on race grade and result",
    23: ("{0} +{1}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_SCENARIO_LIVE_PERFORMANCE, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE)),
    27: ("Friendship with {1} lowest-friendship Support Card(s) +{2} (Excludes {0})", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_CHARACTER, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_VALUE)),
    28: ("Friendship with {0} lowest-friendship Support Card(s) +{1}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE)),
    29: ("Friendship with lowest-friendship Support Card +{1} (Excludes {0})", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_CHARACTER, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE)),
    30: ("Friendship with lowest-friendship Support Card +{0}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE, EFFECT_VALUE_TYPE_NONE)),
    31: ("Receive the following skill hints in chain event {0}: {1}", DISPLAY_TYPE_ANOTHER_EVENT_SKILL, (EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_SKILL, EFFECT_VALUE_TYPE_NONE)),
    32: ("Receive the following skill hints in chain event {0}: {1}, {2}", DISPLAY_TYPE_ANOTHER_EVENT_SKILL, (EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_SKILL, EFFECT_VALUE_TYPE_SKILL)),
    34: ("Random {0} attribute(s) -{1}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE)),
    35: ("Friendship with {0} -{1}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_CHARACTER, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE)),
    36: ("Friendship with {0} -{1} (Friendship cannot change for Support Cards not in your deck)", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_CHARACTER, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE)),
    37: ("Become {0}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_CHARACTER_EFFECT, EFFECT_VALUE_TYPE_NONE, EFFECT_VALUE_TYPE_NONE)),
    38: ("{0} -{1} (Prevented by {2})", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_PARAMETER, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_CHARACTER_EFFECT)),
    39: ("{0} +{1} (Prevented by {2})", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_PARAMETER, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_CHARACTER_EFFECT)),
    40: ("Become {0} (Prevented by {1})", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_CHARACTER_EFFECT, EFFECT_VALUE_TYPE_CHARACTER_EFFECT, EFFECT_VALUE_TYPE_NONE)),
}


class RewardFormatter:
    def __init__(self, owner):
        self.status_name_dict = getattr(owner, "status_name_dict", {})
        self.skill_name_dict = getattr(owner, "skill_name_dict", {})
        self.character_name_dict = getattr(owner, "character_name_dict", {})

    def format_event(self, data, context):
        choices = (context.get("event_contents_info") or {}).get("choice_array") or []
        if isinstance(choices, dict):
            choices = [choices]
        ids = [choice.get("gain_select_id_index") for choice in choices]
        if (not ids or any(type(value) is not int for value in ids)
                or sorted(ids) != list(range(1, len(ids) + 1))):
            return None
        grouped = {index: [] for index in range(1, len(ids) + 1)}
        for reward in data.get("choice_reward_array") or []:
            if not isinstance(reward, dict) or reward.get("select_index") not in grouped:
                return None
            if not isinstance(reward.get("gain_param_array"), list):
                return None
            grouped[reward["select_index"]].append(reward)
        outcomes = [None] * len(ids)
        for index, choice_id in enumerate(ids, 1):
            variants = grouped[index]
            if not variants:
                continue
            lines = []
            for number, variant in enumerate(variants, 1):
                if len(variants) > 1:
                    lines.append({"text": f"Possible outcome {number}:"})
                parts = [self._format_reward_part(effect) for effect in variant["gain_param_array"]
                         if isinstance(effect, dict)]
                # Unknown effect codes must not replace a useful catalog description.
                if any(part["kind"] == "unknown" for part in parts):
                    lines = []
                    break
                lines.extend(parts or [{"text": "No visible rewards"}])
            if lines:
                outcomes[choice_id - 1] = {"label": "In-game possible rewards" if len(variants) > 1 else "In-game rewards", "lines": lines}
        return {"outcomes": outcomes} if any(outcomes) else None

    def _format_reward_part(self, effect):
        display_id = _safe_int(effect.get("display_id"))
        effect_value_0 = _safe_int(effect.get("effect_value_0"), default=0)
        effect_value_1 = _safe_int(effect.get("effect_value_1"), default=0)
        effect_value_2 = _safe_int(effect.get("effect_value_2"), default=0)

        metadata = EVENT_CHOICE_REWARD_META.get(display_id)
        if metadata is not None:
            values = (effect_value_0, effect_value_1, effect_value_2)
            tone = "neutral"
            if display_id in (6, 31, 32):
                tone = "hint"
            elif display_id in (
                1, 3, 4, 5, 9, 10, 11, 13, 14, 15, 16, 19,
                23, 27, 28, 29, 30, 39,
            ):
                tone = "positive"
            elif display_id in (2, 12, 17, 18, 20, 34, 35, 36, 37, 38, 40):
                tone = "negative"
            reward_part = {
                "kind": _reward_kind(display_id),
                "tone": tone,
                "text": self._format_event_choice_reward_text(metadata, values),
            }
            highlights = self._format_reward_highlights(metadata, values)
            if highlights:
                reward_part["highlights"] = highlights
            return reward_part

        return {
            "kind": "unknown",
            "tone": "neutral",
            "text": self._format_unknown_effect(display_id, effect_value_0, effect_value_1, effect_value_2),
        }

    def _format_event_choice_reward_text(self, metadata, values):
        if isinstance(metadata, str):
            return metadata

        template, display_type, value_types = metadata

        if display_type == DISPLAY_TYPE_SKILL:
            skill_name = self._format_effect_value(EFFECT_VALUE_TYPE_SKILL, values[0])
            if values[1]:
                return f"{skill_name} hint level +{values[1]}"
            return f"Gain {skill_name}"

        args = [
            self._format_effect_value(value_type, value)
            for value_type, value in zip(value_types, values)
        ]

        try:
            text = template.format(*args)
        except Exception:
            text = f"{template}: {', '.join(str(value) for value in values)}"

        return _clean_template_text(text)

    def _format_reward_highlights(self, metadata, values):
        if isinstance(metadata, str):
            return []

        _template, display_type, value_types = metadata
        if display_type == DISPLAY_TYPE_SKILL:
            if not values[0]:
                return []
            return [self._format_effect_value(EFFECT_VALUE_TYPE_SKILL, values[0])]

        if display_type == DISPLAY_TYPE_ANOTHER_EVENT_SKILL:
            highlights = []
            for value_type, value in zip(value_types, values):
                if value_type == EFFECT_VALUE_TYPE_SKILL and value:
                    highlights.append(self._format_effect_value(EFFECT_VALUE_TYPE_SKILL, value))
            return highlights

        return []

    def _format_effect_value(self, value_type, value):
        if value_type == EFFECT_VALUE_TYPE_NONE:
            return ""

        if value_type == EFFECT_VALUE_TYPE_VALUE:
            return str(value)

        if value_type == EFFECT_VALUE_TYPE_PARAMETER:
            return PARAMETER_NAMES.get(value, f"Param {value}")

        if value_type == EFFECT_VALUE_TYPE_CHARACTER:
            return self._name_from_dict(self.character_name_dict, value) or f"Character {value}"

        if value_type == EFFECT_VALUE_TYPE_SKILL:
            return self._name_from_dict(self.skill_name_dict, value) or f"Skill {value}"

        if value_type == EFFECT_VALUE_TYPE_CHARACTER_EFFECT:
            return self._name_from_dict(self.status_name_dict, value) or f"Status {value}"

        if value_type == EFFECT_VALUE_TYPE_TRAINING:
            return TRAINING_NAMES.get(value, f"Training {value}")

        if value_type == EFFECT_VALUE_TYPE_TURN:
            return f"Turn {value}"

        if value_type == EFFECT_VALUE_TYPE_SCENARIO_LIVE_PERFORMANCE:
            return LIVE_PERFORMANCE_NAMES.get(
                value,
                f"Grand Live performance {value}",
            )

        if value_type == EFFECT_VALUE_TYPE_SCENARIO_VENUS_SPIRIT:
            return f"Grand Masters spirit {value}"

        if value_type == EFFECT_VALUE_TYPE_SCENARIO_LEGEND_BUFF_GAUGE:
            return f"Legend buff gauge {value}"

        return str(value)

    def _format_unknown_effect(self, display_id, effect_value_0, effect_value_1, effect_value_2):
        values = [effect_value_0, effect_value_1, effect_value_2]
        values = [value for value in values if value]
        value_text = ", ".join(str(value) for value in values) if values else "0"
        return f"Effect {display_id}: {value_text}"

    def _name_from_dict(self, name_dict, key):
        if key in name_dict:
            return name_dict[key]

        string_key = str(key)
        if string_key in name_dict:
            return name_dict[string_key]

        return None


def _safe_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _reward_kind(display_id):
    if display_id in (1, 2, 3, 13, 14, 19, 20, 21, 22, 23, 34, 38, 39):
        return "parameter"

    if display_id in (4, 5, 11, 27, 28, 29, 30, 35, 36):
        return "bond"

    if display_id in (6, 7, 8, 31, 32):
        return "skill"

    if display_id in (9, 10, 15, 16, 37, 40):
        return "status"

    return "unknown"


def _clean_template_text(text):
    text = text.replace("\\n", " ")
    text = re.sub(r"</?color[^>]*>", "", text)
    return " ".join(text.split())


