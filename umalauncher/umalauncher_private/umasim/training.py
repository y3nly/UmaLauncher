from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .calculator import (
    CalcInfo,
    ExpectedStatus,
    calc_expected_training_status,
    calc_item_bonus,
    calc_training_success_status_separated,
    cap_status,
    member_state,
    support_count,
    training_base_for_scenario,
)
from .data import MemberState, SupportCard, load_store
from .model import Status, StatusType, TrainingFacility, TrainingPacketState
from .packet import ACTIVE_WEIGHT_TYPE_BY_ITEM_ID, parse_training_packet

@dataclass(frozen=True)
class TrainingQualityRow:
    facility: TrainingFacility
    support_ids: tuple[int, ...]
    base_stats: Status
    visible_stats: Status
    item_bonus: Status
    final_stats: Status
    expected_stats: ExpectedStatus
    raw_total: int
    score: float
    expected_score: float
    risk_adjusted_score: float
    score_percentile: float
    status_percentile: float
    whistle_downgrade_probability: float
    friendship_enabled: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "commandId": self.facility.command_id,
            "type": self.facility.type.value,
            "level": self.facility.level,
            "failureRate": self.facility.failure_rate,
            "partnerIds": list(self.facility.partner_ids),
            "supportIndexes": list(self.facility.support_indexes),
            "supportIds": list(self.support_ids),
            "baseStats": self.base_stats.to_dict(),
            "skillPt": self.final_stats.skill_pt,
            "hpDelta": self.final_stats.hp,
            "itemBonus": self.item_bonus.to_dict(),
            "scenarioBonus": self.facility.scenario_bonus_stats.to_dict(),
            "finalStats": self.final_stats.to_dict(),
            "visibleStats": self.visible_stats.to_dict() if self.visible_stats is not None else None,
            "friendshipEnabled": self.friendship_enabled,
            "rawTotal": self.raw_total,
            "score": self.score,
            "riskAdjustedScore": self.risk_adjusted_score,
            "baseline": {
                "expected": self.expected_stats.to_dict(),
                "expectedScore": self.expected_score,
                "scorePercentile": self.score_percentile,
                "statusPercentile": self.status_percentile,
            },
            "whistle": {
                "downgradeProbability": self.whistle_downgrade_probability,
            },
        }


@dataclass(frozen=True)
class TrainingQualityReport:
    state: TrainingPacketState
    chara_name: str
    deck_members: tuple[MemberState, ...]
    deck_positions: tuple[int, ...]
    motivation: int
    megaphone: int
    stat_cap: int | None
    weight_types: tuple[StatusType, ...]
    rows: tuple[TrainingQualityRow, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": _scenario_name(self.state.scenario_id),
            "scenarioId": self.state.scenario_id,
            "chara": {
                "id": self.state.chara_id,
                "name": self.chara_name,
            },
            "motivation": self.motivation,
            "fanCount": self.state.fan_count,
            "statCap": self.stat_cap,
            "megaphone": self.megaphone,
            "weightTypes": [status_type.value for status_type in self.weight_types],
            "currentStatus": self.state.current_status.to_dict(),
            "deck": [
                {
                    "position": position,
                    "id": member.card.id,
                    "talent": member.card.talent,
                    "name": member.name,
                    "type": member.card.type.value,
                    "relation": member.relation,
                }
                for position, member in zip(self.deck_positions, self.deck_members)
            ],
            "rows": [row.to_dict() for row in self.rows],
        }


def evaluate_training_packet(
    packet: Mapping[str, Any],
    *,
    stat_cap: int | None = None,
    motivation: int | None = None,
    skill_pt_weight: float = 0.4,
    hp_weight: float = 0.0,
    data_dir: str | Path | None = None,
) -> TrainingQualityReport:
    state = parse_training_packet(packet)
    store = load_store(data_dir)
    if state.chara_id is None:
        raise ValueError("packet is missing chara id")
    chara = store.get_chara(state.chara_id, state.chara_rarity, state.chara_rank)
    effective_stat_cap = (
        stat_cap
        if stat_cap is not None
        else _int_option(packet, "statCap", "stat_cap", "maxStat", "max_stat")
        or state.stat_cap
    )
    root_motivation = _int_option(packet, "motivation")
    effective_motivation = (
        motivation
        if motivation is not None
        else root_motivation if root_motivation is not None else state.motivation
    )
    effective_megaphone = (
        root_megaphone
        if (root_megaphone := _int_option(packet, "megaphone")) is not None
        else state.megaphone
    )
    speciality_rate_up = _int_option(packet, "specialityRateUp", "specialtyRateUp", "speciality_rate_up") or 0
    position_rate_up = _int_option(packet, "positionRateUp", "position_rate_up") or 0
    speed_skill_count = _int_option(packet, "speedSkillCount", "speed_skill_count") or 0
    heal_skill_count = _int_option(packet, "healSkillCount", "heal_skill_count") or 0
    accel_skill_count = _int_option(packet, "accelSkillCount", "accel_skill_count") or 0
    weight, weight_types = _weight_options(packet, state.weight_type_order)
    weight_type_set = frozenset(weight_types)
    deck: list[tuple[int, SupportCard, MemberState]] = []
    for index, support_state in enumerate(sorted(state.deck, key=lambda item: item.position)):
        card = store.get_support(support_state.support_card_id, support_state.limit_break_count)
        relation = state.relations_by_position.get(
            support_state.position,
            support_state.relation if support_state.relation is not None else card.initial_relation,
        )
        deck.append((support_state.position, card, member_state(card, index, relation)))

    deck_members = tuple(member for _position, _card, member in deck)
    member_by_position = {position: member for position, _card, member in deck}
    cards = tuple(card for _position, card, _member in deck)
    base_info = CalcInfo(
        chara=chara,
        training=training_base_for_scenario(state.scenario_id, state.facilities[0].type, state.facilities[0].level) if state.facilities else training_base_for_scenario(state.scenario_id, StatusType.SPEED, 1),
        motivation=effective_motivation,
        member=(),
        support_count=support_count(cards),
        fan_count=state.fan_count,
        current_status=state.current_status,
        total_relation=sum(member.relation for member in deck_members),
        speed_skill_count=speed_skill_count,
        heal_skill_count=heal_skill_count,
        accel_skill_count=accel_skill_count,
        total_training_level=_int_option(packet, "totalTrainingLevel", "total_training_level")
        or sum(facility.level for facility in state.facilities),
        is_level_up_turn=any(601 <= facility.command_id <= 605 for facility in state.facilities),
    )
    rows: list[TrainingQualityRow] = []
    for facility in state.facilities:
        joined = tuple(member_by_position[position] for position in facility.support_indexes if position in member_by_position)
        info = base_info.with_training(training_base_for_scenario(state.scenario_id, facility.type, facility.level)).with_member(joined)
        base_result, scenario_result, friendship_enabled = calc_training_success_status_separated(info)
        scenario_result = scenario_result + facility.scenario_bonus_stats
        base_result = base_result + scenario_result
        item_bonus = calc_item_bonus(
            facility.type,
            base_result,
            megaphone=effective_megaphone,
            weight=weight,
            weight_types=weight_type_set,
        )
        final_result = base_result + item_bonus
        effective_base = cap_status(base_result, state.current_status, effective_stat_cap)
        effective_final = cap_status(final_result, state.current_status, effective_stat_cap)
        all_members_info = info.with_member(deck_members)
        _raw_expected, expected_detail = calc_expected_training_status(
            all_members_info,
            speciality_rate_up=lambda _status_type: speciality_rate_up,
            position_rate_up=position_rate_up,
        )
        expected_detail = [
            (rate, status + facility.scenario_bonus_stats)
            for rate, status in expected_detail
        ]
        expected_rate_total = sum(rate for rate, _status in expected_detail) or 1.0
        expected_with_items = ExpectedStatus()
        expected_score = 0.0
        percentile = 0.0
        status_percentile = 0.0
        final_score = score_status(effective_final, skill_pt_weight=skill_pt_weight, hp_weight=hp_weight)
        for rate, status in expected_detail:
            rate_share = rate / expected_rate_total
            expected_final = cap_status(
                status
                + calc_item_bonus(
                    facility.type,
                    status,
                    megaphone=effective_megaphone,
                    weight=weight,
                    weight_types=weight_type_set,
                ),
                state.current_status,
                effective_stat_cap,
            )
            expected_with_items = expected_with_items.add(rate_share, expected_final)
            outcome_score = score_status(
                expected_final,
                skill_pt_weight=skill_pt_weight,
                hp_weight=hp_weight,
            )
            expected_score += rate_share * outcome_score
            if outcome_score < final_score:
                percentile += rate
            if expected_final.status_total < effective_final.status_total:
                status_percentile += rate
        whistle_downgrade_probability = _whistle_downgrade_probability(
            expected_detail,
            effective_base.status_total,
            state.current_status,
            effective_stat_cap,
        )
        rows.append(
            TrainingQualityRow(
                facility=facility,
                support_ids=tuple(member.card.id for member in joined),
                base_stats=effective_base,
                visible_stats=facility.visible_stats,
                item_bonus=item_bonus,
                final_stats=effective_final,
                expected_stats=expected_with_items,
                raw_total=effective_final.status_total,
                score=final_score,
                expected_score=expected_score,
                risk_adjusted_score=final_score * (100.0 - max(0.0, min(100.0, float(facility.failure_rate)))) / 100.0,
                score_percentile=percentile / expected_rate_total,
                status_percentile=status_percentile / expected_rate_total,
                whistle_downgrade_probability=whistle_downgrade_probability,
                friendship_enabled=friendship_enabled,
            )
        )
    return TrainingQualityReport(
        state=state,
        chara_name=chara.name,
        deck_members=deck_members,
        deck_positions=tuple(position for position, _card, _member in deck),
        motivation=effective_motivation,
        megaphone=effective_megaphone,
        stat_cap=effective_stat_cap,
        weight_types=tuple(weight_types),
        rows=tuple(rows),
    )


def evaluate_packet_training_quality(
    *,
    packet_data_payload: Mapping[str, Any],
    stat_cap: int | None = None,
    skill_pt_weight: float = 0.4,
    hp_weight: float = 0.0,
    data_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Return a dict shaped like Kotlin's TrainingQualityCli BatchReport.

    App-specific overrides such as ``motivation``, ``ankleTypes``, and
    ``megaphone`` are read from the root payload.
    """
    return evaluate_training_packet(
        packet_data_payload,
        stat_cap=stat_cap,
        skill_pt_weight=skill_pt_weight,
        hp_weight=hp_weight,
        data_dir=data_dir,
    ).to_dict()


def score_status(
    status: Status,
    *,
    skill_pt_weight: float = 0.4,
    hp_weight: float = 0.0,
) -> float:
    return (
        float(status.status_total)
        + float(status.skill_pt) * float(skill_pt_weight)
        + float(status.hp) * float(hp_weight)
    )


def _scenario_name(scenario_id: int | None) -> str:
    return {
        2: "AOHARU",
        4: "CLIMAX",
    }.get(scenario_id, "TRAINING")


def _whistle_downgrade_probability(
    expected: list[tuple[float, Status]],
    current_raw_total: int,
    current_status: Status,
    stat_cap: int | None,
) -> float:
    total = sum(rate for rate, _status in expected)
    if total <= 0.0:
        return 0.0
    return (
        sum(
            rate
            for rate, status in expected
            if cap_status(status, current_status, stat_cap).status_total < current_raw_total
        )
        / total
    )


def _int_option(mapping: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        if key not in mapping:
            continue
        try:
            return int(mapping[key])
        except (TypeError, ValueError):
            continue
    return None


def _weight_options(packet: Mapping[str, Any], active_weight_types: tuple[StatusType, ...]) -> tuple[bool, tuple[StatusType, ...]]:
    explicit_weight = _bool_option(packet, "weight")
    explicit_types: list[StatusType] = []

    def add_explicit(status_type: StatusType) -> None:
        if status_type not in explicit_types:
            explicit_types.append(status_type)

    for key in ("weightType", "weight_type", "ankleType", "ankle_type"):
        value = packet.get(key)
        if isinstance(value, str):
            add_explicit(StatusType.parse(value))
    for key in ("weightTypes", "weight_types", "ankleTypes", "ankle_types"):
        value = packet.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    add_explicit(StatusType.parse(item))
    item_id = _int_option(packet, "weightItemId", "weight_item_id", "ankleItemId", "ankle_item_id")
    if item_id is not None and item_id in ACTIVE_WEIGHT_TYPE_BY_ITEM_ID:
        add_explicit(ACTIVE_WEIGHT_TYPE_BY_ITEM_ID[item_id])
    has_explicit_type = bool(explicit_types) or item_id is not None
    if explicit_weight is not None or has_explicit_type:
        return bool(explicit_weight), tuple(explicit_types)
    return False, tuple(active_weight_types)


def _bool_option(mapping: Mapping[str, Any], *keys: str) -> bool | None:
    for key in keys:
        if key not in mapping:
            continue
        value = mapping[key]
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            if value.lower() in ("true", "1", "yes", "y"):
                return True
            if value.lower() in ("false", "0", "no", "n"):
                return False
    return None
