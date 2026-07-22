import time

from loguru import logger

import constants

from .umasim import evaluate_packet_training_quality
from .umasim.data import default_data_dir


class TrainingSimWorker:
    def __init__(self):
        self.reported_unavailable = False

    def evaluate(self, data, timeout_s=1.0):
        del timeout_s

        data_dir = self._resolve_data_dir()
        if not data_dir:
            if not self.reported_unavailable:
                logger.debug("Training sim data is unavailable; expected vendored data files.")
                self.reported_unavailable = True
            return None

        self.reported_unavailable = False
        start = time.monotonic()
        try:
            payload = (
                data
                if isinstance(data, dict) and isinstance(data.get("data"), dict)
                else {"data": data}
            )
            result = evaluate_packet_training_quality(
                packet_data_payload=payload,
            )
            logger.debug(f"Training sim evaluated in {(time.monotonic() - start) * 1000:.1f}ms.")
            return result
        except Exception:
            logger.exception("Training sim evaluation failed.")
            return None

    def enrich_commands(self, packet, command_info):
        """Evaluate a training packet and attach each result to its helper command."""
        result = self.evaluate(packet)
        if result is not None:
            attach_training_rows(command_info, result)
        return result

    def close(self):
        return

    def _resolve_data_dir(self):
        data_dir = default_data_dir()
        return data_dir if self._is_valid_data_dir(data_dir) else None

    def _is_valid_data_dir(self, path):
        if not path.is_dir():
            return False

        required_files = [
            "chara.txt",
            "support_card.txt",
        ]
        return all((path / file_name).is_file() for file_name in required_files)


def attach_training_rows(command_info, result):
    """Attach simulator rows to the matching helper-table facility in place."""
    if not isinstance(command_info, dict) or not isinstance(result, dict):
        return command_info

    for row in result.get("rows", ()):
        if not isinstance(row, dict):
            continue
        command_key = constants.COMMAND_ID_TO_KEY.get(row.get("commandId"))
        command = command_info.get(command_key)
        if isinstance(command, dict):
            command["training_sim"] = row

    return command_info


__all__ = ["TrainingSimWorker", "attach_training_rows"]
