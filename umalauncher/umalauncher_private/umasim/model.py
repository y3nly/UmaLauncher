from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StatusType(str, Enum):
    SPEED = "SPEED"
    STAMINA = "STAMINA"
    POWER = "POWER"
    GUTS = "GUTS"
    WISDOM = "WISDOM"
    FRIEND = "FRIEND"
    GROUP = "GROUP"
    SKILL = "SKILL"
    NONE = "NONE"

    @classmethod
    def parse(cls, value: object) -> "StatusType":
        text = str(value).strip().upper()
        aliases = {
            "S": cls.SPEED,
            "SPD": cls.SPEED,
            "SPEED": cls.SPEED,
            "H": cls.STAMINA,
            "STA": cls.STAMINA,
            "STAMINA": cls.STAMINA,
            "P": cls.POWER,
            "PWR": cls.POWER,
            "POWER": cls.POWER,
            "G": cls.GUTS,
            "GUTS": cls.GUTS,
            "W": cls.WISDOM,
            "WIT": cls.WISDOM,
            "WIZ": cls.WISDOM,
            "WISDOM": cls.WISDOM,
            "FRIEND": cls.FRIEND,
            "GROUP": cls.GROUP,
            "SKILL": cls.SKILL,
            "SKILLPT": cls.SKILL,
            "SKILL_PT": cls.SKILL,
        }
        try:
            return aliases[text]
        except KeyError as exc:
            raise ValueError(f"unknown status type: {value!r}") from exc


@dataclass(frozen=True)
class Status:
    speed: int = 0
    stamina: int = 0
    power: int = 0
    guts: int = 0
    wisdom: int = 0
    skill_pt: int = 0
    hp: int = 0
    max_hp: int = 0
    motivation: int = 0
    fan_count: int = 0

    @property
    def status_total(self) -> int:
        return self.speed + self.stamina + self.power + self.guts + self.wisdom

    @property
    def total_plus_skill_pt(self) -> int:
        return self.status_total + self.skill_pt

    def get(self, status_type: StatusType) -> int:
        if status_type == StatusType.SPEED:
            return self.speed
        if status_type == StatusType.STAMINA:
            return self.stamina
        if status_type == StatusType.POWER:
            return self.power
        if status_type == StatusType.GUTS:
            return self.guts
        if status_type == StatusType.WISDOM:
            return self.wisdom
        if status_type == StatusType.SKILL:
            return self.skill_pt
        return 0

    def capped_gain(self, current: "Status", stat_cap: int | None) -> "Status":
        if stat_cap is None:
            return self

        def cap_delta(now: int, delta: int) -> int:
            return max(0, min(int(delta), int(stat_cap) - int(now)))

        return Status(
            speed=cap_delta(current.speed, self.speed),
            stamina=cap_delta(current.stamina, self.stamina),
            power=cap_delta(current.power, self.power),
            guts=cap_delta(current.guts, self.guts),
            wisdom=cap_delta(current.wisdom, self.wisdom),
            skill_pt=self.skill_pt,
            hp=self.hp,
            max_hp=self.max_hp,
            motivation=self.motivation,
            fan_count=self.fan_count,
        )

    def to_dict(self) -> dict[str, int]:
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

    def __add__(self, other: "Status") -> "Status":
        return Status(
            speed=self.speed + other.speed,
            stamina=self.stamina + other.stamina,
            power=self.power + other.power,
            guts=self.guts + other.guts,
            wisdom=self.wisdom + other.wisdom,
            skill_pt=self.skill_pt + other.skill_pt,
            hp=self.hp + other.hp,
            max_hp=self.max_hp + other.max_hp,
            motivation=self.motivation + other.motivation,
            fan_count=self.fan_count + other.fan_count,
        )

    def __sub__(self, other: "Status") -> "Status":
        return Status(
            speed=self.speed - other.speed,
            stamina=self.stamina - other.stamina,
            power=self.power - other.power,
            guts=self.guts - other.guts,
            wisdom=self.wisdom - other.wisdom,
            skill_pt=self.skill_pt - other.skill_pt,
            hp=self.hp - other.hp,
            max_hp=self.max_hp - other.max_hp,
            motivation=self.motivation - other.motivation,
            fan_count=self.fan_count - other.fan_count,
        )


@dataclass(frozen=True)
class SupportCardState:
    position: int
    support_card_id: int
    limit_break_count: int = 4
    relation: int | None = None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "id": self.support_card_id,
            "supportCardId": self.support_card_id,
            "limitBreak": self.limit_break_count,
            "relation": self.relation,
        }


@dataclass(frozen=True)
class TrainingFacility:
    command_id: int
    type: StatusType
    facility_index: int
    level: int
    failure_rate: int
    partner_ids: tuple[int, ...] = ()
    support_indexes: tuple[int, ...] = ()
    visible_stats: Status = field(default_factory=Status)
    item_bonus_stats: Status = field(default_factory=Status)
    scenario_bonus_stats: Status = field(default_factory=Status)
    raw_command: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def skill_gain(self) -> int:
        return self.visible_stats.skill_pt

    @property
    def energy_delta(self) -> int:
        return self.visible_stats.hp

    def to_dict(self) -> dict[str, Any]:
        return {
            "commandId": self.command_id,
            "type": self.type.value,
            "facilityIndex": self.facility_index,
            "level": self.level,
            "failureRate": self.failure_rate,
            "partnerIds": list(self.partner_ids),
            "supportIndexes": list(self.support_indexes),
            "visibleStats": self.visible_stats.to_dict(),
            "itemBonus": self.item_bonus_stats.to_dict(),
            "scenarioBonus": self.scenario_bonus_stats.to_dict(),
            "finalStats": (self.visible_stats + self.scenario_bonus_stats + self.item_bonus_stats).to_dict(),
        }


@dataclass(frozen=True)
class TrainingPacketState:
    chara_id: int | None
    chara_rarity: int
    chara_rank: int
    scenario_id: int | None
    turn: int | None
    current_status: Status
    fan_count: int
    motivation: int
    deck: tuple[SupportCardState, ...]
    facilities: tuple[TrainingFacility, ...]
    relations_by_position: Mapping[int, int] = field(default_factory=dict)
    stat_cap: int | None = None
    active_item_ids: frozenset[int] = field(default_factory=frozenset)
    active_item_id_order: tuple[int, ...] = ()
    megaphone: int = 0
    weight_types: frozenset[StatusType] = field(default_factory=frozenset)
    weight_type_order: tuple[StatusType, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "charaId": self.chara_id,
            "charaRarity": self.chara_rarity,
            "charaRank": self.chara_rank,
            "scenarioId": self.scenario_id,
            "turn": self.turn,
            "currentStatus": self.current_status.to_dict(),
            "fanCount": self.fan_count,
            "motivation": self.motivation,
            "statCap": self.stat_cap,
            "megaphone": self.megaphone,
            "weightTypes": [status_type.value for status_type in self.weight_type_order],
            "activeItemIds": sorted(self.active_item_ids),
            "activeItemIdOrder": list(self.active_item_id_order),
            "deck": [support.to_dict() for support in self.deck],
            "relationsByPosition": {str(key): value for key, value in self.relations_by_position.items()},
            "facilities": [facility.to_dict() for facility in self.facilities],
        }
