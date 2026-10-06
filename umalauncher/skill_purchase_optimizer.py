"""Final-purchase policy and grouped knapsack, shared with UmaLauncher.

Callers supply eligible endpoints with unpaid costs and rating/CM gains. They
mark inherited skills as SS-only and reserve affordable normal required ranks.
Keep this module identical to UmaLauncher's skill_purchase_optimizer.py.
"""

from __future__ import annotations

from concurrent.futures import CancelledError
from typing import Callable, Mapping, NamedTuple


class Priority(NamedTuple):
    good_families: int = 0
    good_bashin: int = 0
    other_bashin: int = 0
    green_families: int = 0
    green_ranks: int = 0
    active_families: int = 0
    active_ranks: int = 0
    exception_count: int = 0
    exception_cost: int = 0
    rating: int = 0

    def worth_buying(self) -> bool:
        return any((self.good_families, self.good_bashin, self.other_bashin,
                    self.green_families, self.green_ranks, self.active_families,
                    self.active_ranks, self.rating))


class _State(NamedTuple):
    priority: Priority
    choices: tuple
    gain: int


class PurchasePlan(NamedTuple):
    choices: tuple
    cost: int
    rating_gain: int
    maximum_rating_gain: int
    ss_without_exceptions: bool
    ss_exception: bool


def purchase_priority(option: Mapping, spend_remainder: bool) -> Priority:
    # Fixed-point Bashin keeps addition and comparison deterministic.
    bashin = round(max(0.0, float(option.get("cm_value", 0))) * 1_000_000_000)
    blocked = bool(option.get("blacklisted") or option.get("ss_only"))
    good = bool(option.get("good")) and not blocked
    fallback = (spend_remainder and not good and bashin == 0
                and bool(option.get("activates")) and not blocked)
    green = fallback and bool(option.get("green"))
    active = fallback and not green
    return Priority(
        good_families=int(good and bool(option.get("new_good"))),
        good_bashin=bashin if good else 0,
        other_bashin=bashin if not good and not blocked else 0,
        green_families=int(green and bool(option.get("new_family"))),
        green_ranks=int(option.get("rank_gain", 0)) if green else 0,
        active_families=int(active and bool(option.get("new_family"))),
        active_ranks=int(option.get("rank_gain", 0)) if active else 0,
        exception_count=-int(blocked),
        exception_cost=-int(option["cost"]) if blocked else 0,
        rating=int(option["gain"]) if spend_remainder and not blocked else 0,
    )


def purchase_tier(option: Mapping, required: Mapping, spend_remainder=True) -> str:
    if option["group_id"] in required:
        return "starred"
    if option.get("blacklisted") or option.get("ss_only"):
        return "SS padding"
    if option.get("good"):
        return "good upgrade" if option["ranked"] else "good skill"
    if float(option.get("cm_value", 0)) > 0:
        return "positive Bashin"
    if spend_remainder and option.get("activates"):
        return "activating green" if option.get("green") else "activating skill"
    return "rating fallback"


PURCHASE_TIERS = ("starred", "good skill", "good upgrade", "positive Bashin",
                  "activating green", "activating skill", "rating fallback", "SS padding")


def purchase_order(option: Mapping, required_order: Mapping) -> tuple:
    tier = option["tier"]
    value = (int(option["gain"]) / int(option["cost"])
             if tier in {"activating green", "activating skill", "rating fallback"}
             else max(0.0, float(option.get("cm_value", 0))))
    return (PURCHASE_TIERS.index(tier), required_order.get(option["group_id"], 0),
            -value, int(option["skill_id"]))


def _skill_ids(choices):
    return tuple(int(option["skill_id"]) for option in choices)


def _prune_states(candidates, check_cancelled):
    """Discard plans dominated by a cheaper plan with enough rating and priority."""
    ratings = {rating: index + 1 for index, rating in enumerate(
        sorted({key[1] for key in candidates}, reverse=True)
    )}
    # Prefix maxima over descending ratings avoid a quadratic comparison.
    frontier = [None] * (len(ratings) + 1)
    kept = {}
    for index, key in enumerate(sorted(candidates, key=lambda k: (k[0], -k[1]))):
        if index % 256 == 0:
            check_cancelled()
        priority = candidates[key].priority
        position = ratings[key[1]]
        prior = None
        cursor = position
        while cursor:
            value = frontier[cursor]
            if value is not None and (prior is None or value > prior):
                prior = value
            cursor -= cursor & -cursor
        if prior is not None and prior >= priority:
            continue
        kept[key] = candidates[key]
        while position < len(frontier):
            value = frontier[position]
            if value is None or priority > value:
                frontier[position] = priority
            position += position & -position
    return kept


def optimize_purchases(option_groups, budget, required, needed_gain, *,
                       spend_remainder=True, cancelled: Callable | None = None) -> PurchasePlan:
    """Preserve priority order; require SS exactly when the eligible budget can reach it."""
    def check_cancelled():
        if cancelled and cancelled():
            raise CancelledError()

    def group_choices(allow_blacklisted=False, allow_inherited=False):
        groups = []
        for group in sorted(option_groups):
            floor = required.get(group)
            options = [option for option in option_groups[group]
                       if option["cost"] <= budget
                       and (allow_blacklisted or not option.get("blacklisted"))
                       and (allow_inherited or not option.get("ss_only"))
                       and (floor is None or option.get("rate", 0) >= floor.get("rate", 0))]
            if options or floor:
                groups.append(options if floor else [None, *options])
        return groups

    def rating_bounds(groups):
        # Maximum possible rating with each remaining SP capacity. Required
        # ranks and mutually exclusive ranks are honored in every bound.
        bounds = [[0] * (budget + 1) for _ in range(len(groups) + 1)]
        for index in range(len(groups) - 1, -1, -1):
            check_cancelled()
            following = bounds[index + 1]
            bound = [-10**9] * (budget + 1)
            for option in groups[index]:
                cost, gain = (option["cost"], option["gain"]) if option else (0, 0)
                for capacity in range(cost, budget + 1):
                    bound[capacity] = max(bound[capacity], gain + following[capacity - cost])
            bounds[index] = bound
        return bounds

    check_cancelled()
    groups = group_choices()
    bounds = rating_bounds(groups)
    ss_without_exceptions = bounds[0][budget] >= needed_gain
    ss_exception = False
    if not ss_without_exceptions:
        has_blacklist = any(option.get("blacklisted") and not option.get("ss_only")
                            for options in option_groups.values() for option in options)
        has_inherited = any(option.get("ss_only")
                            for options in option_groups.values() for option in options)
        # Inherited skills are considered only after exhausting every way to
        # reach SS without them, including existing blacklist exceptions.
        for allow_inherited in (False, True):
            if not (has_inherited if allow_inherited else has_blacklist):
                continue
            exception_groups = group_choices(True, allow_inherited)
            exception_bounds = rating_bounds(exception_groups)
            if exception_bounds[0][budget] >= needed_gain:
                groups, bounds = exception_groups, exception_bounds
                ss_exception = True
                break
    require_ss = ss_without_exceptions or ss_exception

    def optimize(enforce_ss):
        states = {(0, 0): _State(Priority(), (), 0)}
        for index, options in enumerate(groups):
            check_cancelled()
            candidates = {}
            choices = [(option, purchase_priority(option, spend_remainder)
                        if option else Priority()) for option in options]
            for state_index, ((spent, _rating), state) in enumerate(states.items()):
                if state_index % 256 == 0:
                    check_cancelled()
                for option, priority in choices:
                    if (option and option["group_id"] not in required and not enforce_ss
                            and not priority.worth_buying()):
                        continue
                    total = spent + (option["cost"] if option else 0)
                    if total > budget:
                        continue
                    gain = state.gain + (option["gain"] if option else 0)
                    if enforce_ss and gain + bounds[index + 1][budget - total] < needed_gain:
                        continue
                    combined = Priority(*(a + b for a, b in zip(state.priority, priority)))
                    picked = state.choices + ((option,) if option else ())
                    key = (total, min(needed_gain, gain) if enforce_ss else 0)
                    existing = candidates.get(key)
                    if (existing is None or combined > existing.priority
                            or (combined == existing.priority
                                and _skill_ids(picked) < _skill_ids(existing.choices))):
                        candidates[key] = _State(combined, picked, gain)
            states = _prune_states(candidates, check_cancelled)
        return max(
            ((key, state) for key, state in states.items()
             if not enforce_ss or state.gain >= needed_gain),
            key=lambda item: (item[1].priority, -item[0][0],
                              tuple(-sid for sid in _skill_ids(item[1].choices))),
        )

    (spent, _rating), state = optimize(False)
    if require_ss and state.gain < needed_gain:
        (spent, _rating), state = optimize(True)
    return PurchasePlan(state.choices, spent, state.gain, bounds[0][budget],
                        ss_without_exceptions, ss_exception)
