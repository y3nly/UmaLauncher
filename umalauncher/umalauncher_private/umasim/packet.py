from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .model import Status, StatusType, SupportCardState, TrainingFacility, TrainingPacketState

_TRAINING_COMMANDS_BY_TYPE = (
    (StatusType.SPEED, 0, (101, 601, 901, 1101, 2101, 2201, 2301, 3601)),
    (StatusType.STAMINA, 1, (105, 602, 905, 1102, 2102, 2202, 2302, 3602)),
    (StatusType.POWER, 2, (102, 603, 902, 1103, 2103, 2203, 2303, 3603)),
    (StatusType.GUTS, 3, (103, 604, 903, 1104, 2104, 2204, 2304, 3604)),
    (StatusType.WISDOM, 4, (106, 605, 906, 1105, 2105, 2205, 2305, 3605)),
)
COMMAND_TO_FACILITY = {
    command_id: facility_index
    for _status_type, facility_index, command_ids in _TRAINING_COMMANDS_BY_TYPE
    for command_id in command_ids
}
COMMAND_TO_TYPE = {
    command_id: status_type
    for status_type, _facility_index, command_ids in _TRAINING_COMMANDS_BY_TYPE
    for command_id in command_ids
}
ACTIVE_MEGAPHONE_BONUS_BY_ITEM_ID = {
    8001: 20,
    8002: 40,
    8003: 60,
}
ACTIVE_WEIGHT_TYPE_BY_ITEM_ID = {
    9001: StatusType.SPEED,
    9002: StatusType.STAMINA,
    9003: StatusType.POWER,
    9004: StatusType.GUTS,
}


class PacketParseError(ValueError):
    """Raised when a packet is missing fields required for training rows."""


def parse_training_packet(
    packet: Mapping[str, Any],
    *,
    require_all_training_commands: bool = False,
) -> TrainingPacketState:
    data = _packet_data(packet)
    chara = _obj(data.get("chara_info"))
    if chara is None:
        raise PacketParseError("packet is missing chara_info")
    home_info = _obj(data.get("home_info"))
    if home_info is None:
        raise PacketParseError("packet is missing home_info")

    deck = _parse_deck(chara)
    relations = _parse_relations(chara, len(deck))
    facilities = _parse_facilities(
        home_info,
        bonus_commands=_free_training_bonus_commands(data),
        scenario_bonus_commands=_scenario_training_bonus_commands(data),
        deck_size=len(deck),
        level_by_command=_level_by_command(chara),
    )
    if require_all_training_commands and len(facilities) < 5:
        raise PacketParseError(f"expected all five training commands, got {len(facilities)}")

    active_item_id_order = tuple(_active_training_item_ids(data))
    active_item_ids = frozenset(active_item_id_order)
    weight_type_order = tuple(
        status_type
        for item_id in active_item_id_order
        if (status_type := ACTIVE_WEIGHT_TYPE_BY_ITEM_ID.get(item_id)) is not None
    )
    current_status = _status_from_chara(chara)
    fan_count = _int(chara, "fans", "fan_count", default=0) or 0
    current_status = Status(
        speed=current_status.speed,
        stamina=current_status.stamina,
        power=current_status.power,
        guts=current_status.guts,
        wisdom=current_status.wisdom,
        skill_pt=current_status.skill_pt,
        hp=current_status.hp,
        max_hp=current_status.max_hp,
        motivation=current_status.motivation,
        fan_count=fan_count,
    )
    return TrainingPacketState(
        chara_id=_int(chara, "card_id", "chara_id", "single_mode_chara_id"),
        chara_rarity=_int(chara, "rarity", "chara_grade", default=3) or 3,
        chara_rank=_int(chara, "talent_level", "rank", default=5) or 5,
        scenario_id=_int(chara, "scenario_id"),
        turn=_int(chara, "turn"),
        current_status=current_status,
        fan_count=fan_count,
        motivation=_packet_motivation(chara),
        deck=tuple(deck),
        relations_by_position=relations,
        facilities=tuple(facilities),
        stat_cap=_packet_stat_cap(chara),
        active_item_ids=active_item_ids,
        active_item_id_order=active_item_id_order,
        megaphone=max((ACTIVE_MEGAPHONE_BONUS_BY_ITEM_ID.get(item_id, 0) for item_id in active_item_ids), default=0),
        weight_types=frozenset(weight_type_order),
        weight_type_order=weight_type_order,
    )


def _packet_data(packet: Mapping[str, Any]) -> Mapping[str, Any]:
    data = packet.get("data")
    if isinstance(data, Mapping) and ("chara_info" in data or "home_info" in data):
        return data
    return packet


def _parse_deck(chara: Mapping[str, Any]) -> list[SupportCardState]:
    deck: list[SupportCardState] = []
    for index, raw_support in enumerate(_objects(chara.get("support_card_array")), start=1):
        support_id = _int(raw_support, "support_card_id", "id", "supportCardId")
        if support_id is None:
            continue
        position = _int(raw_support, "position", "index", default=index) or index
        deck.append(
            SupportCardState(
                position=position,
                support_card_id=support_id,
                limit_break_count=_int(raw_support, "limit_break_count", "limitBreak", "talent", default=4) or 4,
                raw=raw_support,
            )
        )
    return deck


def _parse_relations(chara: Mapping[str, Any], deck_size: int) -> dict[int, int]:
    relations: dict[int, int] = {}
    for row in _objects(chara.get("evaluation_info_array")):
        position = _int(row, "training_partner_id", "position", "index")
        value = _int(row, "evaluation", "relation", "bond")
        if position is not None and value is not None and 1 <= position <= deck_size:
            relations[position] = value
    return relations


def _level_by_command(chara: Mapping[str, Any]) -> dict[int, int]:
    out: dict[int, int] = {}
    for row in _objects(chara.get("training_level_info_array")):
        command_id = _int(row, "command_id", "commandId")
        level = _int(row, "level", "command_level", "commandLevel")
        if command_id is not None and level is not None:
            out[command_id] = level
    return out


def _parse_facilities(
    home_info: Mapping[str, Any],
    *,
    bonus_commands: Mapping[int, Mapping[str, Any]],
    scenario_bonus_commands: Mapping[int, Mapping[str, Any]],
    deck_size: int,
    level_by_command: Mapping[int, int],
) -> list[TrainingFacility]:
    facilities: list[TrainingFacility] = []
    for command in _objects(home_info.get("command_info_array")):
        command_id = _int(command, "command_id", "commandId")
        if command_id not in COMMAND_TO_TYPE:
            continue
        command_type = _int(command, "command_type", "commandType")
        if command_type not in (None, 1):
            continue
        partner_ids = tuple(_ints(command.get("training_partner_array")))
        facilities.append(
            TrainingFacility(
                command_id=command_id,
                type=COMMAND_TO_TYPE[command_id],
                facility_index=COMMAND_TO_FACILITY[command_id],
                level=_command_level(command_id, _int(command, "level", "command_level", "commandLevel"), level_by_command),
                failure_rate=_int(command, "failure_rate", "failureRate", default=0) or 0,
                partner_ids=partner_ids,
                support_indexes=tuple(partner for partner in partner_ids if 1 <= partner <= deck_size),
                visible_stats=status_from_packet_params(command.get("params_inc_dec_info_array")),
                item_bonus_stats=status_from_packet_params(
                    bonus_commands.get(command_id, {}).get("params_inc_dec_info_array")
                ),
                scenario_bonus_stats=status_from_packet_params(
                    scenario_bonus_commands.get(command_id, {}).get("params_inc_dec_info_array")
                ),
                raw_command={**command, **scenario_bonus_commands.get(command_id, {})},
            )
        )
    return sorted(facilities, key=lambda facility: facility.facility_index)


def _command_level(command_id: int, explicit_level: int | None, level_by_command: Mapping[int, int]) -> int:
    if 601 <= int(command_id) <= 605:
        return 5
    if explicit_level is not None and explicit_level > 0:
        return explicit_level
    return int(level_by_command.get(command_id, 1) or 1)


def status_from_packet_params(params: Any) -> Status:
    status = Status()
    for row in _objects(params):
        value = _int(row, "value")
        if value is None:
            continue
        status = _add_target_value(status, _int(row, "target_type", "targetType"), value)
    return status


def _add_target_value(status: Status, target_type: int | None, value: int) -> Status:
    if target_type == 1:
        return Status(**{**status.__dict__, "speed": status.speed + value})
    if target_type == 2:
        return Status(**{**status.__dict__, "stamina": status.stamina + value})
    if target_type == 3:
        return Status(**{**status.__dict__, "power": status.power + value})
    if target_type == 4:
        return Status(**{**status.__dict__, "guts": status.guts + value})
    if target_type == 5:
        return Status(**{**status.__dict__, "wisdom": status.wisdom + value})
    if target_type == 10:
        return Status(**{**status.__dict__, "hp": status.hp + value})
    if target_type == 30:
        return Status(**{**status.__dict__, "skill_pt": status.skill_pt + value})
    return status


def _status_from_chara(chara: Mapping[str, Any]) -> Status:
    return Status(
        speed=_int(chara, "speed", default=0) or 0,
        stamina=_int(chara, "stamina", default=0) or 0,
        power=_int(chara, "power", default=0) or 0,
        guts=_int(chara, "guts", default=0) or 0,
        wisdom=_int(chara, "wiz", "wisdom", default=0) or 0,
        skill_pt=_int(chara, "skill_point", "skillPt", "skill_pt", default=0) or 0,
        hp=_int(chara, "vital", "hp", default=0) or 0,
        max_hp=_int(chara, "max_vital", "maxHp", "max_hp", default=100) or 100,
        motivation=_int(chara, "motivation", default=0) or 0,
    )


def _packet_motivation(chara: Mapping[str, Any]) -> int:
    value = _int(chara, "motivation")
    if value is None:
        return 2
    return max(-2, min(3, int(value) - 3))


def _packet_stat_cap(chara: Mapping[str, Any]) -> int | None:
    for key in ("max_speed", "max_stamina", "max_power", "max_guts", "max_wiz", "max_wisdom"):
        value = _int(chara, key)
        if value is not None and value > 0:
            return value
    return None


def _active_training_item_ids(data: Mapping[str, Any]) -> tuple[int, ...]:
    free_data = _obj(data.get("free_data_set"))
    if free_data is None:
        return ()
    out: list[int] = []
    seen: set[int] = set()
    for row in _objects(free_data.get("item_effect_array")):
        item_id = _int(row, "item_id", "itemId")
        if item_id is None or item_id <= 0 or item_id in seen:
            continue
        out.append(item_id)
        seen.add(item_id)
    return tuple(out)


def _free_training_bonus_commands(data: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    free_data = _obj(data.get("free_data_set"))
    if free_data is None:
        return {}

    out: dict[int, Mapping[str, Any]] = {}
    for command in _objects(free_data.get("command_info_array")):
        command_id = _int(command, "command_id", "commandId")
        if command_id in COMMAND_TO_TYPE:
            out[int(command_id)] = command
    return out


def _scenario_training_bonus_commands(data: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    out: dict[int, Mapping[str, Any]] = {}
    for key, value in data.items():
        if key == "free_data_set" or not str(key).endswith("_data_set"):
            continue

        scenario_data = _obj(value)
        if scenario_data is None:
            continue

        for command in _objects(scenario_data.get("command_info_array")):
            command_id = _int(command, "command_id", "commandId")
            if command_id not in COMMAND_TO_TYPE:
                continue

            current = dict(out.get(int(command_id), {}))
            merged = {**current, **command}
            current_params = _objects(current.get("params_inc_dec_info_array"))
            command_params = _objects(command.get("params_inc_dec_info_array"))
            if current_params or command_params:
                merged["params_inc_dec_info_array"] = current_params + command_params
            out[int(command_id)] = merged

    return out


def _obj(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _objects(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def _int(mapping: Mapping[str, Any], *keys: str, default: int | None = None) -> int | None:
    for key in keys:
        if key not in mapping:
            continue
        value = mapping.get(key)
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return default


def _ints(value: Any) -> Iterable[int]:
    if isinstance(value, (str, bytes, bytearray)) or value is None:
        return ()
    if not isinstance(value, Iterable):
        return ()
    out: list[int] = []
    for item in value:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return tuple(out)
