from __future__ import annotations

from typing import Any


MAX_STAT_VALUE = 2500
STAT_RATES_50 = (
    5, 8, 10, 13, 16, 18, 21, 24, 26, 28, 29, 30, 31,
    33, 34, 35, 39, 41, 42, 43, 52, 55, 66, 68, 68,
)
STAT_RATES_10 = (
    79, 80, 81, 83, 84, 85, 86, 88, 89, 90, 92, 93, 94, 96, 97, 98,
    100, 101, 102, 103, 105, 106, 107, 109, 110, 111, 113, 114, 115,
    117, 118, 119, 121, 122, 123, 124, 126, 127, 128, 130, 131, 132,
    134, 135, 136, 138, 139, 140, 141, 143, 144, 145, 147, 148, 149,
    151, 152, 153, 155, 156, 157, 159, 160, 161, 162, 164, 165, 166,
    168, 169, 170, 172, 173, 174, 176, 177, 178, 179, 181, 182, 182,
)


def _build_stat_rating_table() -> tuple[int, ...]:
    scores = [0] * (MAX_STAT_VALUE + 1)
    scaled_score = 0
    for stat_value in range(1, MAX_STAT_VALUE + 1):
        if stat_value <= 1200:
            rate = STAT_RATES_50[stat_value // 50]
        elif stat_value <= 2000:
            rate = STAT_RATES_10[(stat_value - 1200) // 10]
        else:
            rate = 183 + ((stat_value - 2001) // 25)
        scaled_score += rate
        scores[stat_value] = (scaled_score + 5) // 10
    # These two displayed values are documented one-point game exceptions.
    scores[1643] = 8587
    scores[1865] = 11931
    return tuple(scores)


STAT_RATING_SCORES = _build_stat_rating_table()


def stat_rating(raw_value: Any) -> int:
    """Return the game's rating contribution for one displayed stat."""

    try:
        value = min(MAX_STAT_VALUE, max(0, int(raw_value)))
    except (TypeError, ValueError):
        return 0
    return STAT_RATING_SCORES[value]
