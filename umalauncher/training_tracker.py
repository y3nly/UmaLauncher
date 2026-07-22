import gzip
import json
import os
import re

from loguru import logger

import constants
import util


_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_FILENAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def _sanitize_filename(filename: str, max_length: int = 255) -> str:
    """Return a portable filename using only the standard library."""
    filename = _INVALID_FILENAME_CHARS.sub("_", filename).rstrip(" .")
    stem, suffix = os.path.splitext(filename)
    stem = stem.rstrip(" .")

    if stem.upper() in _WINDOWS_RESERVED_FILENAMES:
        stem += "_"

    if not stem:
        stem = "_"

    max_stem_length = max(1, max_length - len(suffix))
    return f"{stem[:max_stem_length]}{suffix}"


class TrainingTracker:

    def __init__(
        self,
        training_id: str,
        card_id: int = None,
        training_log_folder: str = util.TRAINING_LOGS_FOLDER,
        full_path: str = None,
    ):
        self.full_path = full_path
        if not training_log_folder:
            training_log_folder = util.TRAINING_LOGS_FOLDER
        self.training_log_folder = training_log_folder
        self.card_id = card_id

        if not full_path:
            # Create training_logs folder if it doesn't exist.
            os.makedirs(self.training_log_folder, exist_ok=True)

        self.training_id = self.make_string_safe(training_id)

    def make_string_safe(self, training_id: str):
        def convert_char(character: str):
            if character.isalnum():
                return character
            return "_"

        return "".join(convert_char(character) for character in training_id)

    def training_id_matches(self, training_id: str):
        return self.make_string_safe(training_id) == self.training_id

    def add_packet(self, packet: dict):
        self.write_packet(packet)

    def add_request(self, request: dict):
        logger.debug("Adding request.")
        request["_direction"] = 0

        # Remove keys that should not be saved.
        for key in constants.REQUEST_KEYS_TO_BE_REMOVED:
            if key in request:
                del request[key]

        self.add_packet(request)

    def add_response(self, response: dict):
        logger.debug("Adding response.")
        response["_direction"] = 1
        self.add_packet(response)

    def get_training_path(self, suffix=""):
        if self.full_path:
            return self.full_path + suffix

        card_segment = ""
        if self.card_id:
            character_id = int(str(self.card_id)[:4])
            character_name = util.get_character_name_dict().get(character_id, "Unknown Chara")
            outfit_name = util.get_outfit_name_dict().get(self.card_id, "[Unknown Outfit]")[1:-1]
            card_segment = f"{character_name} [{outfit_name}] - "

        filename = _sanitize_filename(card_segment + self.training_id + suffix)
        return str(os.path.join(self.training_log_folder, filename))

    def get_sav_path(self):
        return self.get_training_path(".gz")

    def write_packet(self, packet: dict):
        # Packets are stored as comma-separated JSON objects inside one gzip stream.
        is_first = not os.path.exists(self.get_sav_path())
        if packet is not None:
            with gzip.open(self.get_sav_path(), "ab") as save_file:
                if not is_first:
                    save_file.write(b",")
                save_file.write(json.dumps(packet, ensure_ascii=False).encode("utf-8"))

    def load_packets(self):
        packet_list = []
        logger.debug("Loading packets from file")
        if os.path.exists(self.get_sav_path()):
            with gzip.open(self.get_sav_path(), "rb") as save_file:
                packet_list = json.loads(f"[{save_file.read().decode('utf-8')}]")
        logger.debug(f"Amount of packets loaded: {len(packet_list)}")
        return packet_list
