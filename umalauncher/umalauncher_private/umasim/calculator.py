from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from itertools import product

from .data import (
    AOHARU_TRAINING_DATA,
    CLIMAX_TRAINING_DATA,
    Chara,
    MemberState,
    SpecialUniqueCondition,
    SupportCard,
    TrainingBase,
)
from .model import Status, StatusType


@dataclass(frozen=True)
class ExpectedStatus:
    speed: float = 0.0
    stamina: float = 0.0
    power: float = 0.0
    guts: float = 0.0
    wisdom: float = 0.0
    skill_pt: float = 0.0
    hp: float = 0.0

    @property
    def status_total(self) -> float:
        return self.speed + self.stamina + self.power + self.guts + self.wisdom

    def add(self, rate: float, status: Status) -> "ExpectedStatus":
        return ExpectedStatus(
            speed=self.speed + rate * status.speed,
            stamina=self.stamina + rate * status.stamina,
            power=self.power + rate * status.power,
            guts=self.guts + rate * status.guts,
            wisdom=self.wisdom + rate * status.wisdom,
            skill_pt=self.skill_pt + rate * status.skill_pt,
            hp=self.hp + rate * status.hp,
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "speed": self.speed,
            "stamina": self.stamina,
            "power": self.power,
            "guts": self.guts,
            "wisdom": self.wisdom,
            "skillPt": self.skill_pt,
            "hp": self.hp,
            "total": self.status_total,
        }


@dataclass(frozen=True)
class CalcInfo:
    chara: Chara
    training: TrainingBase
    motivation: int
    member: tuple[MemberState, ...]
    support_count: dict[StatusType, int]
    fan_count: int
    current_status: Status
    total_relation: int
    speed_skill_count: int = 0
    heal_skill_count: int = 0
    accel_skill_count: int = 0
    total_training_level: int = 0
    is_level_up_turn: bool = False

    @property
    def support(self) -> tuple[MemberState, ...]:
        return self.member

    @property
    def support_type_count(self) -> int:
        return len(self.support_count)

    def base_special_unique_condition(
        self,
        *,
        training_support_count: int,
        friend_training: bool,
    ) -> SpecialUniqueCondition:
        return SpecialUniqueCondition(
            training_type=self.training.type,
            training_level=self.training.display_level,
            total_training_level=self.total_training_level,
            relation=0,
            support_count=self.support_count,
            fan_count=self.fan_count,
            status=self.current_status,
            total_relation=self.total_relation,
            training_support_count=training_support_count,
            speed_skill_count=self.speed_skill_count,
            heal_skill_count=self.heal_skill_count,
            accel_skill_count=self.accel_skill_count,
            friend_training=friend_training,
            friend_count=0,
        )

    def with_training(self, training: TrainingBase) -> "CalcInfo":
        return CalcInfo(
            chara=self.chara,
            training=training,
            motivation=self.motivation,
            member=self.member,
            support_count=self.support_count,
            fan_count=self.fan_count,
            current_status=self.current_status,
            total_relation=self.total_relation,
            speed_skill_count=self.speed_skill_count,
            heal_skill_count=self.heal_skill_count,
            accel_skill_count=self.accel_skill_count,
            total_training_level=self.total_training_level,
            is_level_up_turn=self.is_level_up_turn,
        )

    def with_member(self, member: tuple[MemberState, ...]) -> "CalcInfo":
        return CalcInfo(
            chara=self.chara,
            training=self.training,
            motivation=self.motivation,
            member=member,
            support_count=self.support_count,
            fan_count=self.fan_count,
            current_status=self.current_status,
            total_relation=self.total_relation,
            speed_skill_count=self.speed_skill_count,
            heal_skill_count=self.heal_skill_count,
            accel_skill_count=self.accel_skill_count,
            total_training_level=self.total_training_level,
            is_level_up_turn=self.is_level_up_turn,
        )


def climax_training_base(training_type: StatusType, level: int) -> TrainingBase:
    return training_base_for_scenario(4, training_type, level)


def training_base_for_scenario(scenario_id: int | None, training_type: StatusType, level: int) -> TrainingBase:
    training_data = AOHARU_TRAINING_DATA if scenario_id == 2 else CLIMAX_TRAINING_DATA
    for training in training_data:
        if training.type == training_type and training.level == level:
            return training
    scenario_name = "AOHARU" if scenario_id == 2 else "CLIMAX"
    raise KeyError(f"no {scenario_name} training data for {training_type.value} level {level}")


def member_state(card: SupportCard, index: int, relation: int | None = None) -> MemberState:
    return MemberState(index=index, card=card, relation=card.initial_relation if relation is None else int(relation))


def calc_training_success_status(info: CalcInfo) -> Status:
    base, scenario, _friend_training = calc_training_success_status_separated(info)
    return base + scenario


def calc_training_success_status_separated(info: CalcInfo) -> tuple[Status, Status, bool]:
    friend_training = any(member.is_friend_training(info.training.type) for member in info.support)
    conditions = _member_conditions(info, friend_training)
    factors = _training_factors(info, conditions)
    base = Status(
        speed=int(_calc_training_status(info, StatusType.SPEED, factors)),
        stamina=int(_calc_training_status(info, StatusType.STAMINA, factors)),
        power=int(_calc_training_status(info, StatusType.POWER, factors)),
        guts=int(_calc_training_status(info, StatusType.GUTS, factors)),
        wisdom=int(_calc_training_status(info, StatusType.WISDOM, factors)),
        skill_pt=int(_calc_training_status(info, StatusType.SKILL, factors)),
        hp=_calc_training_hp(info, conditions),
    )
    return base, Status(), friend_training


@dataclass(frozen=True)
class _TrainingFactors:
    conditions: tuple[tuple[MemberState, SpecialUniqueCondition], ...]
    friend: float
    motivation: float
    training: float


def _member_conditions(
    info: CalcInfo,
    friend_training: bool,
) -> tuple[tuple[MemberState, SpecialUniqueCondition], ...]:
    base_condition = info.base_special_unique_condition(
        training_support_count=len(info.support),
        friend_training=friend_training,
    )
    return tuple((member, base_condition.apply_member(member)) for member in info.support)


def _training_factors(
    info: CalcInfo,
    conditions: tuple[tuple[MemberState, SpecialUniqueCondition], ...],
) -> _TrainingFactors:
    # These bonuses depend on the joined members, not the stat being calculated.
    friend = 1.0
    for member, condition in conditions:
        if member.is_friend_training(info.training.type):
            friend *= member.card.friend_factor(condition)
    motivation_base = 0.55 if info.motivation == 3 else info.motivation / 10.0
    motivation_support = sum(member.card.motivation_factor(condition) for member, condition in conditions)
    motivation_bonus = 1 + motivation_base * (1 + motivation_support / 100.0)
    training_bonus = 1 + sum(member.card.training_factor(condition) for member, condition in conditions) / 100.0
    return _TrainingFactors(conditions, friend, motivation_bonus, training_bonus)


def calc_training_status(
    info: CalcInfo,
    target_type: StatusType,
    friend_training: bool,
    *,
    ignore_base_bonus: bool = False,
    max_value: float = 100.0,
) -> float:
    if info.training.status.get(target_type) == 0:
        return 0.0
    factors = _training_factors(info, _member_conditions(info, friend_training))
    return _calc_training_status(
        info, target_type, factors, ignore_base_bonus=ignore_base_bonus, max_value=max_value,
    )


def _calc_training_status(
    info: CalcInfo,
    target_type: StatusType,
    factors: _TrainingFactors,
    *,
    ignore_base_bonus: bool = False,
    max_value: float = 100.0,
) -> float:
    base_status = info.training.status.get(target_type)
    if base_status == 0:
        return 0.0
    base = base_status
    if not ignore_base_bonus:
        base += sum(member.card.get_base_bonus(target_type, condition) for member, condition in factors.conditions)
    chara_bonus = 1.0 if ignore_base_bonus else info.chara.get_bonus(target_type) / 100.0
    count = 1 + len(info.member) * 0.05
    raw = base * chara_bonus * factors.friend * factors.motivation * factors.training * count
    return min(max_value, raw + 0.0002)


def calc_training_hp(info: CalcInfo, friend_training: bool) -> int:
    return _calc_training_hp(info, _member_conditions(info, friend_training))


def _calc_training_hp(
    info: CalcInfo,
    conditions: tuple[tuple[MemberState, SpecialUniqueCondition], ...],
) -> int:
    base_hp = info.training.status.hp
    if base_hp == 0:
        return 0
    if info.training.type == StatusType.WISDOM:
        return base_hp + sum(
            member.card.wisdom_friend_recovery(condition)
            for member, condition in conditions
            if member.is_friend_training(StatusType.WISDOM)
        )
    support_hp_cost = sum(member.card.hp_cost(condition) for member, condition in conditions)
    return base_hp - int(base_hp * support_hp_cost / 100.0)


def calc_card_position_selection(
    info: CalcInfo,
    member: MemberState,
    speciality_rate_up: int,
    position_rate_up: int,
) -> tuple[tuple[StatusType, int], ...]:
    card = member.card
    if card.type == StatusType.FRIEND:
        return (
            (StatusType.SPEED, 100),
            (StatusType.STAMINA, 100),
            (StatusType.POWER, 100),
            (StatusType.GUTS, 100),
            (StatusType.WISDOM, 100),
            (StatusType.NONE, 100 - position_rate_up),
        )
    main_rate = card.specialty_rate(
        speciality_rate_up,
        info.base_special_unique_condition(training_support_count=0, friend_training=False).apply_member(member),
    )
    other_rate = 10000
    none_rate = 50 * (100 - position_rate_up)
    return (
        (StatusType.SPEED, main_rate if card.type == StatusType.SPEED else other_rate),
        (StatusType.STAMINA, main_rate if card.type == StatusType.STAMINA else other_rate),
        (StatusType.POWER, main_rate if card.type == StatusType.POWER else other_rate),
        (StatusType.GUTS, main_rate if card.type == StatusType.GUTS else other_rate),
        (StatusType.WISDOM, main_rate if card.type == StatusType.WISDOM else other_rate),
        (StatusType.NONE, none_rate),
    )


def calc_expected_training_status(
    info: CalcInfo,
    *,
    speciality_rate_up: Callable[[StatusType], int] | None = None,
    position_rate_up: int = 0,
) -> tuple[ExpectedStatus, list[tuple[float, Status]]]:
    speciality_rate_up = speciality_rate_up or (lambda _status_type: 0)
    status = ExpectedStatus()
    detail: list[tuple[float, Status]] = []
    if not info.member:
        return _add_expected_status(status, detail, 1.0, calc_training_success_status(info))

    join_rate = [
        _calc_rate(
            info.training.type,
            calc_card_position_selection(info, member, speciality_rate_up(member.card.type), position_rate_up),
        )
        for member in info.member
    ]
    all_join_rate = _product(join_rate) if len(info.member) >= 6 else 0.0
    for pattern in product((True, False), repeat=len(info.member)):
        if sum(pattern) >= 6:
            continue
        rate = _product(
            join_rate[index] if join else 1.0 - join_rate[index]
            for index, join in enumerate(pattern)
        )
        rate += rate * all_join_rate
        joined = tuple(member for index, member in enumerate(info.member) if pattern[index])
        status, detail = _add_expected_status(status, detail, rate, calc_training_success_status(info.with_member(joined)))
    return status, detail


def calc_item_bonus(training_type: StatusType, status: Status, *, megaphone: int = 0, weight: bool = False, weight_types: set[StatusType] | frozenset[StatusType] = frozenset()) -> Status:
    status_factor = int(megaphone or 0)
    use_weight = bool(weight) or training_type in weight_types
    if use_weight:
        status_factor += 50
    if status_factor == 0:
        return Status()
    hp_factor = 20 if use_weight else 0
    return Status(
        speed=int(status.speed * status_factor / 100.0),
        stamina=int(status.stamina * status_factor / 100.0),
        power=int(status.power * status_factor / 100.0),
        guts=int(status.guts * status_factor / 100.0),
        wisdom=int(status.wisdom * status_factor / 100.0),
        skill_pt=int(status.skill_pt * status_factor / 100.0),
        hp=int(status.hp * hp_factor / 100.0),
    )


def cap_status(status: Status, current_status: Status, stat_cap: int | None) -> Status:
    if stat_cap is None:
        return status

    def cap_gain(current: int, gain: int) -> int:
        if gain <= 0:
            return gain
        return max(0, min(int(stat_cap) - int(current), gain))

    return Status(
        speed=cap_gain(current_status.speed, status.speed),
        stamina=cap_gain(current_status.stamina, status.stamina),
        power=cap_gain(current_status.power, status.power),
        guts=cap_gain(current_status.guts, status.guts),
        wisdom=cap_gain(current_status.wisdom, status.wisdom),
        skill_pt=status.skill_pt,
        hp=status.hp,
        max_hp=status.max_hp,
        motivation=status.motivation,
        fan_count=status.fan_count,
    )


def support_count(cards: tuple[SupportCard, ...]) -> dict[StatusType, int]:
    out: dict[StatusType, int] = {}
    for card in cards:
        out[card.type] = out.get(card.type, 0) + 1
    return out


def _add_expected_status(
    result: ExpectedStatus,
    detail: list[tuple[float, Status]],
    rate: float,
    status: Status,
) -> tuple[ExpectedStatus, list[tuple[float, Status]]]:
    detail.append((rate, status))
    return result.add(rate, status), detail


def _calc_rate(value: StatusType, values: tuple[tuple[StatusType, int], ...]) -> float:
    total = sum(rate for _status_type, rate in values)
    if total == 0:
        return 0.0
    for status_type, rate in values:
        if status_type == value:
            return rate / total
    return 0.0


def _product(values) -> float:
    result = 1.0
    for value in values:
        result *= value
    return result
