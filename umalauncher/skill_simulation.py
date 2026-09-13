"""Local simulator inputs for the skill helper."""

import hashlib
import copy
import json
import math
import os
from urllib.parse import urljoin
from skill_simulator_data import SkillDataSnapshot


def fingerprint(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()


BASHIN_DATA_ROOT = urljoin(os.environ.get(
    'UMALAUNCHER_SKILL_VISUALIZER_URL', 'https://bashin.app/visualizer/?launcher=1'), '/cm/')
CM_INDEX_URL = urljoin(BASHIN_DATA_ROOT, 'profile_index.json')


def load_cm_configs(load_json):
    """Load the complete published selector/config from Bashin, without a fallback."""
    data = load_json(CM_INDEX_URL)
    entries = data.get("profiles") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError("Bashin CM index is empty or invalid")
    configs = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Invalid Bashin CM definition")
        if entry.get("kind") == "cm_pool":
            pool = entry.get("racePool", {})
            scenarios = pool.get("scenarios", [])
            if (entry.get("cmId") != "cm_all" or not isinstance(scenarios, list)
                    or [s.get('id') for s in scenarios] != [str(i) for i in range(1, 49)]
                    or any(pool.get(key) for key in ('courses', 'conditions', 'seasonWeights'))
                    or any(s.get('season') not in range(1, 6) or s.get('weather') not in range(1, 5)
                        or s.get('time') != 2 or s.get('track', {}).get('gateCount') != 9
                        or s['track'].get('location', 0) <= 0 or s['track'].get('course', 0) <= 0
                        or s['track'].get('distanceType') not in range(1, 5)
                        or s['track'].get('surface') not in (1, 2)
                        or s['track'].get('condition') not in ('GOOD', 'YAYAOMO', 'OMO', 'BAD') for s in scenarios)
                    or not entry.get('weightingPolicy') or not entry.get('file') or not entry.get('name')):
                raise ValueError('Invalid Bashin CM pool')
            if entry['cmId'] in configs:
                raise ValueError('Duplicate Bashin profile')
            configs[entry['cmId']] = dict(entry, meta=entry)
            continue
        if entry.get("kind") == "tt":
            pool = entry.get("racePool", {})
            courses = pool.get("courses", [])
            conditions = pool.get("conditions", [])
            seasons = pool.get("seasonWeights", {})
            if (entry.get("cmId") not in ("tt_sprint", "tt_mile", "tt_medium", "tt_long", "tt_dirt")
                    or not courses or len({c['course'] for c in courses}) != len(courses)
                    or any(c.get('gateCount') != 12 or c.get('location', 0) <= 0 or c.get('course', 0) <= 0 for c in courses)
                    or len({(c['distanceType'], c['surface']) for c in courses}) != 1
                    or not conditions or any(c.get('weather') not in (1, 2, 3, 4)
                        or c.get('condition') not in ('GOOD', 'YAYAOMO', 'OMO', 'BAD')
                        or not math.isfinite(c.get('weight', 0)) or c.get('weight', 0) <= 0 for c in conditions)
                    or not math.isclose(sum(c['weight'] for c in conditions), 1)
                    or set(seasons) != {'1', '2', '3', '4', '5'}
                    or any(not math.isfinite(w) or w <= 0 for w in seasons.values())
                    or not math.isclose(sum(seasons.values()), 1) or pool.get('time') != 2
                    or not entry.get('weightingPolicy') or not entry.get('file') or not entry.get('name')):
                raise ValueError('Invalid Bashin TT pool')
            if entry['cmId'] in configs:
                raise ValueError('Duplicate Bashin profile')
            configs[entry['cmId']] = dict(entry, meta=entry)
            continue
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
                              weather=entry["weather"], ground_condition=entry["groundCondition"],
                              meta=entry, file=entry['file'])
    return configs


STYLE_APTITUDES = {
    "NIGE": "proper_running_style_nige", "SEN": "proper_running_style_senko",
    "SASI": "proper_running_style_sashi", "OI": "proper_running_style_oikomi",
}
STYLES = tuple(STYLE_APTITUDES)
GRADES = "GFEDCBAS"
DISCOUNTS = (0, 10, 20, 30, 35, 40)


def load_course_data(load_json):
    data = load_json(urljoin(BASHIN_DATA_ROOT, "course_data.json"))
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

    fixed_pool = cm.get('kind') == 'cm_pool'
    pooled = cm.get('kind') in ('tt', 'cm_pool')
    pool = copy.deepcopy(cm['racePool']) if pooled else None
    if fixed_pool:
        for scenario in pool['scenarios']:
            track = scenario['track']
            scenario['distanceFit'] = aptitude('proper_distance_' + {1:'short',2:'mile',3:'middle',4:'long'}[track['distanceType']])
            scenario['surfaceFit'] = aptitude('proper_ground_' + {1:'turf',2:'dirt'}[track['surface']])

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
        if fixed_pool and field in ('distanceFit', 'surfaceFit'):
            if any(s[field] != 'S' for s in pool['scenarios']):
                key = field[:-3] + '_up'
                comparisons.append({'id': key, 'candidate': {field + 'Steps': 1}})
                definitions.append({'id': key, 'label': label + ' +1 grade',
                    'description': f'Improve the {label.lower()} aptitude used by each CM by one grade, capped at S.'})
            continue
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
    initial = pool['scenarios'][0] if fixed_pool else {}
    payload = {
        "baseSetting": {
            "umaStatus": status,
            "track": initial['track'] if fixed_pool else pool['courses'][0] if pooled else {"location": cm["location"], "course": cm["course"],
                      "condition": cm["ground_condition"], "gateCount": 9},
            "season": initial.get('season', 0) if pooled else cm["season"],
            "weather": initial.get('weather', 1) if pooled else cm["weather"], "positionKeepMode": "NONE",
        },
        "acquiredSkillIds": sorted(learned),
        "unacquiredSkillIds": sorted(int(row["id"]) for row in rows),
        "iterations": 2000, "collectLocationTelemetry": not pooled,
        "evaluationComparisons": comparisons,
        "effectivenessThresholdSeconds": 0.001,
    }
    if pooled:
        payload['racePool'] = pool
        payload['poolWeightingPolicy'] = cm['weightingPolicy']
        payload['baseSetting']['time'] = 2
    return payload, rows, definitions
