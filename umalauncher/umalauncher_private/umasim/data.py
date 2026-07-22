from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .model import Status, StatusType


TRAINING_TYPES = (
    StatusType.SPEED,
    StatusType.STAMINA,
    StatusType.POWER,
    StatusType.GUTS,
    StatusType.WISDOM,
)


def support_type_from_text(value: str) -> StatusType:
    text = value.strip()
    aliases = {
        "S": StatusType.SPEED,
        "SPD": StatusType.SPEED,
        "SPEED": StatusType.SPEED,
        "スピード": StatusType.SPEED,
        "H": StatusType.STAMINA,
        "STA": StatusType.STAMINA,
        "STAMINA": StatusType.STAMINA,
        "スタミナ": StatusType.STAMINA,
        "P": StatusType.POWER,
        "PWR": StatusType.POWER,
        "POWER": StatusType.POWER,
        "パワー": StatusType.POWER,
        "G": StatusType.GUTS,
        "GUTS": StatusType.GUTS,
        "根性": StatusType.GUTS,
        "W": StatusType.WISDOM,
        "WIZ": StatusType.WISDOM,
        "WISDOM": StatusType.WISDOM,
        "賢さ": StatusType.WISDOM,
        "友人": StatusType.FRIEND,
        "FRIEND": StatusType.FRIEND,
        "グループ": StatusType.GROUP,
        "GROUP": StatusType.GROUP,
        "スキルPT": StatusType.SKILL,
        "スキルPt": StatusType.SKILL,
        "SKILL": StatusType.SKILL,
        "SKILLPT": StatusType.SKILL,
    }
    return aliases.get(text, aliases.get(text.upper(), StatusType.NONE))


@dataclass(frozen=True)
class Chara:
    id: int
    name: str
    chara_id: int
    chara_name: str
    rarity: int
    rank: int
    speed_bonus: int
    stamina_bonus: int
    power_bonus: int
    guts_bonus: int
    wisdom_bonus: int
    initial_status: Status
    image_color: str

    def get_bonus(self, status_type: StatusType) -> int:
        if status_type == StatusType.SPEED:
            return 100 + self.speed_bonus
        if status_type == StatusType.STAMINA:
            return 100 + self.stamina_bonus
        if status_type == StatusType.POWER:
            return 100 + self.power_bonus
        if status_type == StatusType.GUTS:
            return 100 + self.guts_bonus
        if status_type == StatusType.WISDOM:
            return 100 + self.wisdom_bonus
        return 100


@dataclass(frozen=True)
class SupportStatus:
    friend: int
    motivation: int
    speed_bonus: int
    stamina_bonus: int
    power_bonus: int
    guts_bonus: int
    wisdom_bonus: int
    training: int
    initial_speed: int
    initial_stamina: int
    initial_power: int
    initial_guts: int
    initial_wisdom: int
    initial_relation: int
    race: int
    fan: int
    hint_level: int
    hint_frequency: int
    specialty_rate: int
    event_recovery: int
    event_effect: int
    failure_rate: int
    hp_cost: int
    skill_pt_bonus: int
    wisdom_friend_recovery: int
    initial_skill_pt: int


@dataclass(frozen=True)
class SpecialUniqueCondition:
    training_type: StatusType
    training_level: int
    total_training_level: int
    relation: int
    support_count: Mapping[StatusType, int]
    fan_count: int
    status: Status
    total_relation: int
    training_support_count: int
    speed_skill_count: int
    heal_skill_count: int
    accel_skill_count: int
    friend_training: bool
    friend_count: int

    @property
    def support_type_count(self) -> int:
        return len(self.support_count)

    def apply_member(self, member: "MemberState") -> "SpecialUniqueCondition":
        return SpecialUniqueCondition(
            training_type=self.training_type,
            training_level=self.training_level,
            total_training_level=self.total_training_level,
            relation=member.relation,
            support_count=self.support_count,
            fan_count=self.fan_count,
            status=self.status,
            total_relation=self.total_relation,
            training_support_count=self.training_support_count,
            speed_skill_count=self.speed_skill_count,
            heal_skill_count=self.heal_skill_count,
            accel_skill_count=self.accel_skill_count,
            friend_training=self.friend_training,
            friend_count=member.friend_count,
        )


@dataclass(frozen=True)
class SupportCardSpecialUnique:
    type: int
    value0: int
    value1: int
    value2: int
    value3: int
    value4: int

    @property
    def target_relation(self) -> int | None:
        if self.type in (101, 102):
            return self.value0
        if self.type in (118, 120):
            return self.value1
        if self.type == 119:
            return self.value2
        return None

    def friend_factor(self, card: "SupportCard", condition: SpecialUniqueCondition) -> int:
        del card
        if self.type == 107 and self.value0 == 1:
            return 15 - int((max(30, condition.status.hp) - 30) * 15 / 100.0)
        return self._get_value(condition, 1)

    def get_motivation(self, card: "SupportCard", condition: SpecialUniqueCondition) -> int:
        del card
        return self._get_value(condition, 2)

    def get_base_bonus(
        self,
        status_type: StatusType,
        card: "SupportCard",
        condition: SpecialUniqueCondition,
    ) -> int:
        del card
        if self.type == 120:
            if condition.relation < self.value1:
                return 0
            if status_type == StatusType.SKILL:
                count = condition.support_count.get(StatusType.FRIEND, 0) + condition.support_count.get(StatusType.GROUP, 0)
            else:
                count = condition.support_count.get(status_type, 0)
            return min(2, count)

        target = {
            StatusType.SPEED: 3,
            StatusType.STAMINA: 4,
            StatusType.POWER: 5,
            StatusType.GUTS: 6,
            StatusType.WISDOM: 7,
            StatusType.SKILL: 30,
        }.get(status_type, 0)
        if target == 0:
            return 0
        all_bonus = 0
        if self.type == 101 and condition.relation >= self.value0 and status_type != StatusType.SKILL:
            all_bonus += self.value2 if self.value1 == 41 else 0
            all_bonus += self.value4 if self.value3 == 41 else 0
        return all_bonus + self._get_value(condition, target)

    def training_factor(self, card: "SupportCard", condition: SpecialUniqueCondition) -> int:
        if self.type == 102:
            return self.value1 if condition.relation >= self.value0 and condition.training_type != card.type else 0
        if self.type == 103:
            return self.value1 if condition.support_type_count >= self.value0 else 0
        if self.type == 104:
            return min(self.value1, condition.fan_count // self.value0)
        if self.type == 117 and self.value0 == 8:
            return min(self.value2, condition.total_training_level)
        return self._get_value(condition, 8)

    def speciality_rate(self, card: "SupportCard", condition: SpecialUniqueCondition) -> int:
        del card
        return self._get_value(condition, 19)

    def hp_cost(self, card: "SupportCard", condition: SpecialUniqueCondition) -> int:
        del card
        return self._get_value(condition, 28)

    def wisdom_friend_recovery(self, card: "SupportCard", condition: SpecialUniqueCondition) -> int:
        del card
        return self._get_value(condition, 31)

    def has_second_position(self, relation: int) -> bool:
        return self.type == 118 and relation >= self.value1

    def position_rate_up(self, relation: int) -> int:
        return self.value0 if self.type == 119 and relation >= self.value2 else 0

    def hint_count_up(self, relation: int) -> int:
        if self.type == 101 and relation >= self.value0:
            return (self.value2 if self.value1 == 33 else 0) + (self.value4 if self.value3 == 33 else 0)
        return 0

    @property
    def initial_relation_all(self) -> int:
        return self.value1 if self.type == 115 and self.value0 == 14 else 0

    @property
    def training_relation_all(self) -> int:
        return self.value0 if self.type == 121 else 0

    @property
    def training_relation_join(self) -> int:
        return self.value1 if self.type == 121 else 0

    @property
    def training_next_turn_speciality_rate_up(self) -> int:
        return self.value1 if self.type == 122 and self.value0 == 19 else 0

    def _get_value(self, condition: SpecialUniqueCondition, target: int) -> int:
        if self.type == 101:
            if condition.relation < self.value0:
                return 0
            return (self.value2 if self.value1 == target else 0) + (self.value4 if self.value3 == target else 0)
        if self.type == 106:
            return min(self.value0, condition.friend_count) * self.value2 if self.value1 == target else 0
        if self.type == 108:
            if self.value0 != target:
                return 0
            return min(self.value4, int((condition.status.max_hp - self.value1) * self.value2 / 100.0 + self.value3))
        if self.type == 109:
            return condition.total_relation // self.value1 if self.value0 == target else 0
        if self.type == 110:
            return condition.training_support_count * self.value1 if self.value0 == target else 0
        if self.type == 111:
            return min(5, condition.training_level) * self.value1 if self.value0 == target else 0
        if self.type == 113:
            return self.value1 if self.value0 == target and condition.friend_training else 0
        if self.type == 114:
            if self.value0 != target:
                return 0
            return self.value2 - max(0, int((100 - condition.status.hp) / self.value1))
        if self.type == 116:
            return self.value2 * min(self._skill_count(condition, self.value0), self.value3) if self.value1 == target else 0
        return 0

    @staticmethod
    def _skill_count(condition: SpecialUniqueCondition, skill_type: int) -> int:
        if skill_type == 1:
            return condition.speed_skill_count
        if skill_type == 2:
            return condition.accel_skill_count
        if skill_type == 3:
            return condition.heal_skill_count
        return 0


@dataclass(frozen=True)
class SupportCard:
    id: int
    name: str
    chara: str
    rarity: int
    talent: int
    max_level: int
    type: StatusType
    status: SupportStatus
    unique: SupportStatus
    skills: tuple[str, ...]
    base_hint_status: Status
    special_unique: tuple[SupportCardSpecialUnique, ...]

    @property
    def initial_relation(self) -> int:
        return self.status.initial_relation + self.unique.initial_relation

    @property
    def target_relation(self) -> tuple[int, ...]:
        values = [0]
        if self.type != StatusType.FRIEND:
            values.append(80)
        values.extend(value for unique in self.special_unique if (value := unique.target_relation) is not None)
        return tuple(sorted(set(values)))

    def friend_factor(self, condition: SpecialUniqueCondition) -> float:
        special = sum(unique.friend_factor(self, condition) for unique in self.special_unique)
        return (100 + self.status.friend) * (100 + self.unique.friend) * (100 + special) / 1_000_000.0

    def motivation_factor(self, condition: SpecialUniqueCondition) -> int:
        return self.status.motivation + self.unique.motivation + sum(
            unique.get_motivation(self, condition) for unique in self.special_unique
        )

    def get_base_bonus(self, status_type: StatusType, condition: SpecialUniqueCondition) -> int:
        base = {
            StatusType.SPEED: self.status.speed_bonus + self.unique.speed_bonus,
            StatusType.STAMINA: self.status.stamina_bonus + self.unique.stamina_bonus,
            StatusType.POWER: self.status.power_bonus + self.unique.power_bonus,
            StatusType.GUTS: self.status.guts_bonus + self.unique.guts_bonus,
            StatusType.WISDOM: self.status.wisdom_bonus + self.unique.wisdom_bonus,
            StatusType.SKILL: self.status.skill_pt_bonus + self.unique.skill_pt_bonus,
        }.get(status_type, 0)
        return base + sum(unique.get_base_bonus(status_type, self, condition) for unique in self.special_unique)

    def training_factor(self, condition: SpecialUniqueCondition) -> int:
        return self.status.training + self.unique.training + sum(
            unique.training_factor(self, condition) for unique in self.special_unique
        )

    def hp_cost(self, condition: SpecialUniqueCondition) -> int:
        return self.status.hp_cost + self.unique.hp_cost + sum(
            unique.hp_cost(self, condition) for unique in self.special_unique
        )

    def specialty_rate(self, bonus: int, condition: SpecialUniqueCondition) -> int:
        special = sum(unique.speciality_rate(self, condition) for unique in self.special_unique)
        return (100 + self.status.specialty_rate) * (100 + self.unique.specialty_rate) * (100 + special) * (100 + bonus) // 10000

    def wisdom_friend_recovery(self, condition: SpecialUniqueCondition) -> int:
        return self.status.wisdom_friend_recovery + self.unique.wisdom_friend_recovery + sum(
            unique.wisdom_friend_recovery(self, condition) for unique in self.special_unique
        )


@dataclass(frozen=True)
class MemberState:
    index: int
    card: SupportCard
    relation: int
    friend_count: int = 0

    @property
    def name(self) -> str:
        return self.card.name

    @property
    def friend_training_enabled(self) -> bool:
        return self.relation >= 80

    def is_friend_training(self, status_type: StatusType) -> bool:
        if self.card.type in (StatusType.FRIEND, StatusType.GROUP):
            return False
        return self.friend_training_enabled and status_type == self.card.type


@dataclass(frozen=True)
class TrainingBase:
    type: StatusType
    level: int
    failure_rate: int
    status: Status

    @property
    def display_level(self) -> int:
        if self.level > 10:
            return self.level % 10
        if self.level > 5:
            return 5
        return self.level


CLIMAX_TRAINING_DATA = (
    TrainingBase(StatusType.SPEED, 1, 520, Status(8, 0, 4, 0, 0, 2, -19)),
    TrainingBase(StatusType.SPEED, 2, 524, Status(9, 0, 4, 0, 0, 2, -20)),
    TrainingBase(StatusType.SPEED, 3, 528, Status(10, 0, 4, 0, 0, 2, -21)),
    TrainingBase(StatusType.SPEED, 4, 532, Status(11, 0, 5, 0, 0, 2, -23)),
    TrainingBase(StatusType.SPEED, 5, 536, Status(12, 0, 6, 0, 0, 2, -25)),
    TrainingBase(StatusType.POWER, 1, 516, Status(0, 4, 6, 0, 0, 2, -18)),
    TrainingBase(StatusType.POWER, 2, 520, Status(0, 4, 7, 0, 0, 2, -19)),
    TrainingBase(StatusType.POWER, 3, 524, Status(0, 4, 8, 0, 0, 2, -20)),
    TrainingBase(StatusType.POWER, 4, 528, Status(0, 5, 9, 0, 0, 2, -22)),
    TrainingBase(StatusType.POWER, 5, 532, Status(0, 6, 10, 0, 0, 2, -24)),
    TrainingBase(StatusType.GUTS, 1, 532, Status(3, 0, 3, 6, 0, 2, -20)),
    TrainingBase(StatusType.GUTS, 2, 536, Status(3, 0, 3, 7, 0, 2, -21)),
    TrainingBase(StatusType.GUTS, 3, 540, Status(3, 0, 3, 8, 0, 2, -22)),
    TrainingBase(StatusType.GUTS, 4, 544, Status(4, 0, 3, 9, 0, 2, -24)),
    TrainingBase(StatusType.GUTS, 5, 548, Status(4, 0, 4, 10, 0, 2, -26)),
    TrainingBase(StatusType.STAMINA, 1, 507, Status(0, 7, 0, 3, 0, 2, -17)),
    TrainingBase(StatusType.STAMINA, 2, 511, Status(0, 8, 0, 3, 0, 2, -18)),
    TrainingBase(StatusType.STAMINA, 3, 515, Status(0, 9, 0, 3, 0, 2, -19)),
    TrainingBase(StatusType.STAMINA, 4, 519, Status(0, 10, 0, 4, 0, 2, -21)),
    TrainingBase(StatusType.STAMINA, 5, 523, Status(0, 11, 0, 5, 0, 2, -23)),
    TrainingBase(StatusType.WISDOM, 1, 320, Status(2, 0, 0, 0, 6, 3, 5)),
    TrainingBase(StatusType.WISDOM, 2, 321, Status(2, 0, 0, 0, 7, 3, 5)),
    TrainingBase(StatusType.WISDOM, 3, 322, Status(2, 0, 0, 0, 8, 3, 5)),
    TrainingBase(StatusType.WISDOM, 4, 323, Status(3, 0, 0, 0, 9, 3, 5)),
    TrainingBase(StatusType.WISDOM, 5, 324, Status(4, 0, 0, 0, 10, 3, 5)),
)


AOHARU_TRAINING_DATA = (
    TrainingBase(StatusType.SPEED, 1, 520, Status(8, 0, 4, 0, 0, 4, -19)),
    TrainingBase(StatusType.SPEED, 2, 524, Status(9, 0, 4, 0, 0, 4, -20)),
    TrainingBase(StatusType.SPEED, 3, 528, Status(10, 0, 4, 0, 0, 4, -21)),
    TrainingBase(StatusType.SPEED, 4, 532, Status(11, 0, 5, 0, 0, 4, -23)),
    TrainingBase(StatusType.SPEED, 5, 536, Status(12, 0, 6, 0, 0, 4, -25)),
    TrainingBase(StatusType.POWER, 1, 516, Status(0, 4, 9, 0, 0, 4, -20)),
    TrainingBase(StatusType.POWER, 2, 520, Status(0, 4, 10, 0, 0, 4, -21)),
    TrainingBase(StatusType.POWER, 3, 524, Status(0, 4, 11, 0, 0, 4, -22)),
    TrainingBase(StatusType.POWER, 4, 528, Status(0, 5, 12, 0, 0, 4, -24)),
    TrainingBase(StatusType.POWER, 5, 532, Status(0, 6, 13, 0, 0, 4, -26)),
    TrainingBase(StatusType.GUTS, 1, 532, Status(3, 0, 3, 6, 0, 4, -20)),
    TrainingBase(StatusType.GUTS, 2, 536, Status(3, 0, 3, 7, 0, 4, -21)),
    TrainingBase(StatusType.GUTS, 3, 540, Status(3, 0, 3, 8, 0, 4, -22)),
    TrainingBase(StatusType.GUTS, 4, 544, Status(4, 0, 3, 9, 0, 4, -24)),
    TrainingBase(StatusType.GUTS, 5, 548, Status(4, 0, 4, 10, 0, 4, -26)),
    TrainingBase(StatusType.STAMINA, 1, 507, Status(0, 8, 0, 6, 0, 4, -20)),
    TrainingBase(StatusType.STAMINA, 2, 511, Status(0, 9, 0, 6, 0, 4, -21)),
    TrainingBase(StatusType.STAMINA, 3, 515, Status(0, 10, 0, 6, 0, 4, -22)),
    TrainingBase(StatusType.STAMINA, 4, 519, Status(0, 11, 0, 7, 0, 4, -24)),
    TrainingBase(StatusType.STAMINA, 5, 523, Status(0, 12, 0, 8, 0, 4, -26)),
    TrainingBase(StatusType.WISDOM, 1, 320, Status(2, 0, 0, 0, 6, 5, 5)),
    TrainingBase(StatusType.WISDOM, 2, 321, Status(2, 0, 0, 0, 7, 5, 5)),
    TrainingBase(StatusType.WISDOM, 3, 322, Status(2, 0, 0, 0, 8, 5, 5)),
    TrainingBase(StatusType.WISDOM, 4, 323, Status(3, 0, 0, 0, 9, 5, 5)),
    TrainingBase(StatusType.WISDOM, 5, 324, Status(4, 0, 0, 0, 10, 5, 5)),
)


class UmasimDataStore:
    def __init__(self, chara: tuple[Chara, ...], support: tuple[SupportCard, ...]) -> None:
        self._chara = {(item.id, item.rarity, item.rank): item for item in chara}
        self._support = {(item.id, item.talent): item for item in support}

    def get_chara(self, chara_id: int, rarity: int = 3, rank: int = 5) -> Chara:
        key = (int(chara_id), int(rarity), int(rank))
        try:
            return self._chara[key]
        except KeyError as exc:
            raise KeyError(f"unknown chara id/rarity/rank: {key}") from exc

    def get_support(self, support_id: int, talent: int = 4) -> SupportCard:
        key = (int(support_id), int(talent))
        try:
            return self._support[key]
        except KeyError as exc:
            raise KeyError(f"unknown support id/talent: {key}") from exc


def default_data_dir() -> Path:
    return Path(__file__).resolve().parent / "data"


@lru_cache(maxsize=8)
def load_store(data_dir: str | Path | None = None) -> UmasimDataStore:
    base = Path(data_dir) if data_dir is not None else default_data_dir()
    chara_path = base / "chara.txt"
    support_path = base / "support_card.txt"
    return UmasimDataStore(
        chara=tuple(load_chara_text(chara_path.read_text(encoding="utf-8"))),
        support=tuple(load_support_card_text(support_path.read_text(encoding="utf-8"))),
    )


def load_chara_text(text: str) -> list[Chara]:
    out: list[Chara] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        data = line.rstrip("\n").split("\t")
        if len(data) < 18:
            continue
        i = 0
        out.append(
            Chara(
                id=int(data[i]),
                name=data[i + 1],
                chara_id=int(data[i + 2]),
                chara_name=data[i + 3],
                rarity=int(data[i + 4]),
                rank=int(data[i + 5]),
                speed_bonus=int(data[i + 6]),
                stamina_bonus=int(data[i + 7]),
                power_bonus=int(data[i + 8]),
                guts_bonus=int(data[i + 9]),
                wisdom_bonus=int(data[i + 10]),
                initial_status=Status(
                    speed=int(data[i + 11]),
                    stamina=int(data[i + 12]),
                    power=int(data[i + 13]),
                    guts=int(data[i + 14]),
                    wisdom=int(data[i + 15]),
                ),
                image_color=data[i + 17],
            )
        )
    return out


def load_support_card_text(text: str) -> list[SupportCard]:
    out: list[SupportCard] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        data = line.rstrip("\n").split("\t")
        if len(data) < 59:
            continue
        i = 0
        support_id = int(data[i])
        name = data[i + 1]
        chara = data[i + 2]
        rarity = int(data[i + 3])
        talent = int(data[i + 4])
        max_level = int(data[i + 5])
        support_type = support_type_from_text(data[i + 6])
        i += 7
        status = _support_status(data[i : i + 26])
        i += 26
        unique = _support_status(data[i : i + 26])
        i += 26
        skills = _read_skills(data[i] if i < len(data) else None)
        i += 1
        hint_status = _read_hint_status(data[i] if i < len(data) else None)
        i += 1
        special_unique = _read_special_unique(data[i] if i < len(data) else None)
        if support_type != StatusType.NONE:
            out.append(
                SupportCard(
                    id=support_id,
                    name=name,
                    chara=chara,
                    rarity=rarity,
                    talent=talent,
                    max_level=max_level,
                    type=support_type,
                    status=status,
                    unique=unique,
                    skills=skills,
                    base_hint_status=hint_status,
                    special_unique=special_unique,
                )
            )
    return out


def _support_status(values: list[str]) -> SupportStatus:
    ints = [int(value) for value in values]
    return SupportStatus(*ints)


def _read_skills(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(skill.strip() for skill in value.split(", ") if skill.strip())


def _read_hint_status(value: str | None) -> Status:
    if not value:
        return Status()
    status = Status()
    for raw in value.split(", "):
        parts = raw.split(":", 1)
        if len(parts) != 2:
            continue
        amount = int(parts[1])
        status_type = support_type_from_text(parts[0])
        if status_type == StatusType.SPEED:
            status = status + Status(speed=amount)
        elif status_type == StatusType.STAMINA:
            status = status + Status(stamina=amount)
        elif status_type == StatusType.POWER:
            status = status + Status(power=amount)
        elif status_type == StatusType.GUTS:
            status = status + Status(guts=amount)
        elif status_type == StatusType.WISDOM:
            status = status + Status(wisdom=amount)
    return status


def _read_special_unique(value: str | None) -> tuple[SupportCardSpecialUnique, ...]:
    if not value or not value.strip():
        return ()
    raw = [int(part) for part in value.split(",") if part.strip()]
    out: list[SupportCardSpecialUnique] = []
    for offset in (0, 6):
        if len(raw) >= offset + 6 and raw[offset] > 0:
            out.append(SupportCardSpecialUnique(*raw[offset : offset + 6]))
    return tuple(out)
