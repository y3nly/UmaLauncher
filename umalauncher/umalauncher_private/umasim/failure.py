from __future__ import annotations

import math
from collections.abc import Iterable

WISDOM_COMMAND_IDS = {106, 605}
PRACTICE_POOR_EFFECT_ID = 6
PRACTICE_PERFECT_EFFECT_ID = 10
PRACTICE_PERFECT_DOUBLE_EFFECT_ID = 11


def _failure_coefficients(command_id: int) -> tuple[float, float, float]:
    if int(command_id) in WISDOM_COMMAND_IDS:
        return 0.04235394, 1.00556871, 5.34198799
    return 0.02945217, 0.99784467, 2.41134696


def condition_failure_adjust_pct(chara_effect_ids: Iterable[int] | None = None) -> float:
    effect_ids: set[int] = set()
    for raw_effect_id in chara_effect_ids or ():
        try:
            effect_ids.add(int(raw_effect_id))
        except (TypeError, ValueError):
            continue

    adjust = 0.0
    if PRACTICE_POOR_EFFECT_ID in effect_ids:
        adjust += 2.0
    if PRACTICE_PERFECT_DOUBLE_EFFECT_ID in effect_ids:
        adjust -= 4.0
    elif PRACTICE_PERFECT_EFFECT_ID in effect_ids:
        adjust -= 2.0
    return adjust


def failure_raw_decimal(
    command_id: int,
    failure_rate: float,
    energy: float,
    max_energy: float = 100,
    *,
    support_failure_down_pct: float = 0.0,
    condition_adjust_pct: float = 0.0,
    chara_effect_ids: Iterable[int] | None = None,
) -> float:
    if max_energy <= 0:
        return 99.0
    if float(energy) >= float(max_energy):
        return 0.0

    energy_pct = float(energy) / float(max_energy) * 100.0
    x = float(failure_rate) - energy_pct
    a, b, c = _failure_coefficients(int(command_id))

    # Keep the fitted parabola monotonic on the high-energy side.
    x_vertex = -b / (2.0 * a)
    if x < x_vertex:
        x = x_vertex

    y = max(0.0, min(99.0, float(a * x * x + b * x + c)))
    support_multiplier = max(0.0, 100.0 - float(support_failure_down_pct or 0.0)) / 100.0
    y *= support_multiplier
    y += float(condition_adjust_pct or 0.0)
    y += condition_failure_adjust_pct(chara_effect_ids)
    return max(0.0, min(99.0, float(y)))


def failure_decimal(
    command_id: int,
    failure_rate: float,
    energy: float,
    max_energy: float = 100,
    *,
    support_failure_down_pct: float = 0.0,
    condition_adjust_pct: float = 0.0,
    chara_effect_ids: Iterable[int] | None = None,
) -> float:
    return failure_raw_decimal(
        int(command_id),
        float(failure_rate),
        float(energy),
        float(max_energy),
        support_failure_down_pct=support_failure_down_pct,
        condition_adjust_pct=condition_adjust_pct,
        chara_effect_ids=chara_effect_ids,
    )


def failure_decimal_to_display_pct(value: float) -> int:
    return int(math.floor(max(0.0, min(99.0, float(value)))))


def failure_display_pct(
    command_id: int,
    failure_rate: float,
    energy: float,
    max_energy: float = 100,
    *,
    support_failure_down_pct: float = 0.0,
    condition_adjust_pct: float = 0.0,
    chara_effect_ids: Iterable[int] | None = None,
) -> float:
    return float(
        failure_decimal_to_display_pct(
            failure_raw_decimal(
                int(command_id),
                float(failure_rate),
                float(energy),
                float(max_energy),
                support_failure_down_pct=support_failure_down_pct,
                condition_adjust_pct=condition_adjust_pct,
                chara_effect_ids=chara_effect_ids,
            )
        )
    )


def failure_rate_from_observed(
    command_id: int,
    observed_failure: float,
    energy: float,
    max_energy: float = 100,
) -> float | None:
    if max_energy <= 0:
        return None
    observed = max(0.0, min(99.0, float(observed_failure) + 0.5))
    a, b, c = _failure_coefficients(int(command_id))
    discriminant = b * b - 4.0 * a * (c - observed)
    if discriminant < 0:
        return None

    sqrt_discriminant = math.sqrt(discriminant)
    roots = (
        (-b + sqrt_discriminant) / (2.0 * a),
        (-b - sqrt_discriminant) / (2.0 * a),
    )
    energy_pct = float(energy) / float(max_energy) * 100.0
    return energy_pct + max(roots)


def failure_after_recovery(
    command_id: int,
    failure_rate: float,
    energy: float,
    recovery: float,
    max_energy: float = 100,
    *,
    support_failure_down_pct: float = 0.0,
    condition_adjust_pct: float = 0.0,
    chara_effect_ids: Iterable[int] | None = None,
) -> float:
    recovered_energy = min(float(max_energy), max(0.0, float(energy) + float(recovery)))
    return failure_decimal(
        int(command_id),
        float(failure_rate),
        recovered_energy,
        float(max_energy),
        support_failure_down_pct=support_failure_down_pct,
        condition_adjust_pct=condition_adjust_pct,
        chara_effect_ids=chara_effect_ids,
    )
