"""CM activation evidence from simulations and supported master conditions.

Race values match the CM21/22 master race definitions and the course
metadata used to generate the bundled CM simulations. No race time is supplied
there, so time-dependent skills remain unknown rather than assumed active.
"""

from __future__ import annotations

import math
import operator
import re
from typing import Any, Mapping


_CM_CONDITIONS = {
    21: dict(track_id=10007, rotation=2, ground_type=1, distance_type=1,
             is_basis_distance=1, season=4, weather=1, ground_condition=1,
             is_dirtgrade=0),
    22: dict(track_id=10006, rotation=2, ground_type=2, distance_type=2,
             is_basis_distance=1, season=4, weather=4, ground_condition=3,
             is_dirtgrade=0),
}
_STYLES = {"front": 1, "pace": 2, "late": 3, "end": 4}
_COMPARISONS = {
    "==": operator.eq, "!=": operator.ne, ">=": operator.ge,
    "<=": operator.le, ">": operator.gt, "<": operator.lt,
}
_ATOM = re.compile(r"([a-z_]+)(==|!=|>=|<=|>|<)(\d+)")


def _possible(expression: str, domains: Mapping[str, set[int]]) -> bool:
    # Master syntax: @ separates alternatives; & joins requirements. Intersect
    # each variable's domain so contradictory bounds cannot qualify a skill.
    for branch in expression.split("@"):
        remaining = dict(domains)
        for atom in branch.split("&"):
            match = _ATOM.fullmatch(atom.strip())
            if match is None or match[1] not in remaining:
                break
            key, op, value = match.groups()
            allowed = {v for v in remaining[key] if _COMPARISONS[op](v, int(value))}
            if not allowed:
                break
            remaining[key] = allowed
        else:
            return True
    return False


def green_skill_can_activate(
    row: Mapping[str, Any], cm_id: int, style: str | None, chara: Mapping[str, Any],
) -> bool:
    """Whether a green has a supported, possible activation in this CM.

    Draw, popularity, opponent composition, and random procs need only be
    possible; this is not a promise of activation in every race. Other skill
    categories and unknown condition variables are outside this first pass.
    """
    return row.get("skill_category") == 0 and skill_can_activate(row, cm_id, style, chara)


def skill_can_activate(
    row: Mapping[str, Any], cm_id: int, style: str | None, chara: Mapping[str, Any],
    simulation: Mapping[str, Any] | None = None,
) -> bool:
    """Require observed activation or supported, satisfiable master conditions.

    A zero/missing simulation rate is not proof of impossibility. Unknown
    condition variables never qualify through the master-condition fallback.
    """
    style_key = {"front": "NIGE", "pace": "SEN", "late": "SASI", "end": "OI"}.get(style)
    try:
        rate = float((simulation or {}).get(f"{style_key}_rate", 0) or 0)
    except (ValueError, TypeError):
        rate = 0.0
    if math.isfinite(rate) and rate > 0:
        return True
    if cm_id not in _CM_CONDITIONS:
        return False
    domains = {key: {value} for key, value in _CM_CONDITIONS[cm_id].items()}
    domains.update({
        "always": {1}, "post_number": set(range(1, 10)),
        "popularity": set(range(1, 10)), "random_lot": set(range(1, 101)),
        "running_style_count_same": set(range(1, 10)),
        "running_style_count_same_rate": set(range(0, 101)),
        "running_style_equal_popularity_one": {0, 1},
        "same_skill_horse_count": set(range(1, 10)),
    })
    if style in _STYLES:
        domains["running_style"] = {_STYLES[style]}
    for condition, stat in (("base_speed", "speed"), ("base_stamina", "stamina"),
                            ("base_power", "power"), ("base_guts", "guts"),
                            ("base_wiz", "wiz")):
        if isinstance(chara.get(stat), int):
            domains[condition] = {chara[stat]}
    for index in (1, 2):
        condition = str(row.get(f"condition_{index}") or "")
        precondition = str(row.get(f"precondition_{index}") or "")
        # Evaluate both expressions together so their requirements cannot
        # contradict one another while independently appearing possible.
        combined = "@".join(
            f"{before}&{after}" if before else after
            for before in (precondition.split("@") if precondition else [""])
            for after in condition.split("@")
        )
        if condition and _possible(combined, domains):
            return True
    return False
