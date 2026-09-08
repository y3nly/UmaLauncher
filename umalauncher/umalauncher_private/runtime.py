"""Private runtime features composed behind the public extension interface."""

from .event_prediction import EventPredictionExtension
from .training_rows import TrainingSimValueRow
from .training_sim import TrainingSimWorker


class PrivateRuntimeExtensions:
    def __init__(self, owner):
        self.owner = owner
        self.event_prediction = EventPredictionExtension(owner)
        self.training_sim = TrainingSimWorker()

    def on_event_detected(self, event_data, event_titles):
        return self.event_prediction.on_event_detected(event_data, event_titles)

    def on_event_opened(self, generation):
        return self.event_prediction.on_event_opened(generation)

    def on_event_chain_changed(self, chain, generation):
        return self.event_prediction.on_event_chain_changed(chain, generation)

    def on_event_closed(self, generation):
        return self.event_prediction.on_event_closed(generation)

    def on_response(self, data, generation):
        return self.event_prediction.on_response(data, generation)

    def enrich_training_commands(self, packet, command_info):
        preset = self.owner.helper_table.selected_preset
        if not command_info or not any(
            isinstance(row, TrainingSimValueRow) and not row.disabled
            for row in preset
        ):
            return None
        return self.training_sim.enrich_commands(packet, command_info)

    def configure_modern_page(self, browser):
        return self.event_prediction.configure_modern_page(browser)

    def close(self):
        try:
            self.event_prediction.close()
        finally:
            self.training_sim.close()


__all__ = ["PrivateRuntimeExtensions"]
