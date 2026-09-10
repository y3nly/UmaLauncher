"""Local simulator inputs for the skill helper."""

import hashlib
import json
from skill_simulator_data import SkillDataSnapshot


def fingerprint(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()


CM_INDEX_URL = "https://bashin.app/cm/cm_index.json"


def load_cm_configs(load_json):
    """Load the complete published selector/config from Bashin, without a fallback."""
    data = load_json(CM_INDEX_URL)
    entries = data.get("cms") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError("Bashin CM index is empty or invalid")
    configs = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Invalid Bashin CM definition")
        for field in ("cmId", "courseId", "location", "season", "weather"):
            if type(entry.get(field)) is not int or entry[field] <= 0:
                raise ValueError(f"Invalid Bashin CM field: {field}")
        if not isinstance(entry.get("name"), str) or not entry["name"].strip():
            raise ValueError("Bashin CM name is missing")
        if entry.get("groundCondition") not in ("GOOD", "YAYAOMO", "OMO", "BAD"):
            raise ValueError("Invalid Bashin CM ground condition")
        cm_id = entry["cmId"]
        if cm_id in configs:
            raise ValueError(f"Duplicate Bashin CM definition: {cm_id}")
        configs[cm_id] = dict(name=entry["name"], course=entry["courseId"],
                              location=entry["location"], season=entry["season"],
                              weather=entry["weather"], ground_condition=entry["groundCondition"])
    return configs


STYLE_APTITUDES = {
    "NIGE": "proper_running_style_nige", "SEN": "proper_running_style_senko",
    "SASI": "proper_running_style_sashi", "OI": "proper_running_style_oikomi",
}
STYLES = tuple(STYLE_APTITUDES)
GRADES = "GFEDCBAS"
DISCOUNTS = (0, 10, 20, 30, 35, 40)


def load_course_data(load_json):
    data = load_json("https://bashin.app/cm/course_data.json")
    if not isinstance(data, dict) or not isinstance(data.get("courses"), dict) or not data["courses"]:
        raise ValueError("Bashin course geometry is empty or invalid")
    return data


def career_id(chara):
    return f"{chara['start_time']}:{chara['card_id']}"


def skill_change_key(chara):
    # Only skill/hint identities trigger evaluations, not levels or array order.
    learned = sorted({entry["skill_id"] for entry in chara["skill_array"]})
    hints = sorted({(entry["group_id"], entry["rarity"])
                    for entry in chara["skill_tips_array"]})
    return fingerprint([career_id(chara), learned, hints])


def build_evaluation(chara, available, cm, course, style, metadata, prerequisites):
    """Actual Ace inputs and display rows; prerequisite effects never enter the baseline."""
    if style not in STYLES:
        raise ValueError("Invalid running style")
    def aptitude(field):
        value = chara[field]
        if type(value) is not int or not 1 <= value <= 8:
            raise ValueError(f"Invalid trainee aptitude: {field}")
        return GRADES[value - 1]

    distance_field = {1: "short", 2: "mile", 3: "middle", 4: "long"}[course["distanceType"]]
    surface_field = {1: "turf", 2: "dirt"}[course["surface"]]
    learned = {entry["skill_id"] for entry in chara["skill_array"]}
    missing = (learned | set(available)) - metadata.keys()
    if missing:
        raise ValueError(f"Skills missing from the installed Global database: {sorted(missing)}")
    unique_levels = [entry["level"] for entry in chara["skill_array"]
                     if metadata[entry["skill_id"]]["skillKind"] == "unique"]
    status = {
        "speed": chara["speed"], "stamina": chara["stamina"], "power": chara["power"],
        "guts": chara["guts"], "wisdom": chara["wiz"], "condition": "BEST", "style": style,
        "distanceFit": aptitude("proper_distance_" + distance_field),
        "surfaceFit": aptitude("proper_ground_" + surface_field),
        "styleFit": aptitude(STYLE_APTITUDES[style]),
        "uniqueLevel": max(unique_levels, default=1), "popularity": 1, "gateNumber": 0,
    }
    comparisons, definitions = [], []
    for field, label in (("speed", "Speed"), ("power", "Power"), ("guts", "Guts"), ("wisdom", "Wit")):
        key = field + "_100"
        comparisons.append({"id": key, "candidate": {field + "Delta": 100}})
        definitions.append({"id": key, "label": label + " +100",
                            "description": f"Bashin gain from increasing {label} by 100."})
    for field, label in (("distanceFit", "Distance"), ("surfaceFit", "Surface"), ("styleFit", "Style")):
        grade = status[field]
        if grade == "S":
            continue
        next_grade = GRADES[GRADES.index(grade) + 1]
        key = f"{field[:-3]}_{grade.lower()}_to_{next_grade.lower()}"
        comparisons.append({"id": key, "candidate": {field: next_grade}})
        definitions.append({"id": key, "label": f"{label} {grade} > {next_grade}",
                            "description": f"Bashin gain from improving {label} aptitude from {grade} to {next_grade}."})
    rows = []
    for sid, info in available.items():
        if info["is_acquired"]:
            continue
        remaining_cost = 0
        for rank in [sid, *prerequisites(sid)]:
            rank_info = available.get(rank, {})
            if rank_info.get("is_acquired") or rank in learned:
                continue
            hint = max(0, min(int(rank_info.get("hint_level", 0)), 5))
            remaining_cost += int(metadata[rank]["baseCost"] * (100 - DISCOUNTS[hint]) / 100)
        rows.append(dict(metadata[sid], baseCost=remaining_cost))
    rows.sort(key=lambda row: (row["order"], row["id"]))
    payload = {
        "baseSetting": {
            "umaStatus": status,
            "track": {"location": cm["location"], "course": cm["course"],
                      "condition": cm["ground_condition"], "gateCount": 9},
            "season": cm["season"], "weather": cm["weather"], "positionKeepMode": "NONE",
        },
        "acquiredSkillIds": sorted(learned),
        "unacquiredSkillIds": sorted(int(row["id"]) for row in rows),
        "iterations": 2000, "collectLocationTelemetry": True,
        "evaluationComparisons": comparisons,
    }
    return payload, rows, definitions
