"""Activation evidence from simulations and published race/course metadata.

No race time is supplied for individual CMs, so time-dependent skills remain
unknown rather than assumed active. Pools use observed simulation evidence only.
"""

from __future__ import annotations

import math
import operator
import re
from typing import Any, Mapping


_STYLES = {"front": 1, "pace": 2, "late": 3, "end": 4}
_COMPARISONS = {
    "==": operator.eq, "!=": operator.ne, ">=": operator.ge,
    "<=": operator.le, ">": operator.gt, "<": operator.lt,
}
_ATOM = re.compile(r"([a-z_]+)(==|!=|>=|<=|>|<)(\d+)")


def race_conditions(
    cm: Mapping[str, Any] | None, course: Mapping[str, Any] | None,
) -> dict[str, int]:
    """Translate the loaded single-race profile into master condition values."""
    if not cm or cm.get("kind") in ("tt", "cm_pool") or not course:
        return {}
    track_id = course["raceTrackId"]
    return dict(
        track_id=track_id, rotation=course["turn"], ground_type=course["surface"],
        distance_type=course["distanceType"],
        is_basis_distance=int(course["distance"] % 400 == 0),
        season=cm["season"], weather=cm["weather"],
        ground_condition={"GOOD": 1, "YAYAOMO": 2, "OMO": 3, "BAD": 4}[cm["ground_condition"]],
        # Match the simulator's trackDetail.isDirtGrade venue rule.
        is_dirtgrade=int(track_id in (10101, 10103, 10104, 10105)),
    )


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
    row: Mapping[str, Any], conditions: Mapping[str, int], style: str | None,
    chara: Mapping[str, Any],
) -> bool:
    """Whether a green has a supported, possible activation in this CM.

    Draw, popularity, opponent composition, and random procs need only be
    possible; this is not a promise of activation in every race. Other skill
    categories and unknown condition variables are outside this first pass.
    """
    return row.get("skill_category") == 0 and skill_can_activate(row, conditions, style, chara)


def skill_can_activate(
    row: Mapping[str, Any], conditions: Mapping[str, int], style: str | None,
    chara: Mapping[str, Any],
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
    if not conditions:
        return False
    domains = {key: {value} for key, value in conditions.items()}
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
