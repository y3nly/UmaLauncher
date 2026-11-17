from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "umalauncher"))
from event_rewards import RewardFormatter
from runtime_extensions import RuntimeExtensions


class EventRewardTests(unittest.TestCase):
    def test_all_variants_and_choice_identity(self):
        formatter = RewardFormatter(SimpleNamespace())
        context = {"event_contents_info": {"choice_array": [
            {"gain_select_id_index": 2, "select_index": 1}, {"gain_select_id_index": 1}]}}
        rows = [{"select_index": 1, "gain_param_array": [
            {"display_id": 1, "effect_value_0": 10, "effect_value_1": energy}]}
            for energy in (20, 15)]
        result = formatter.format_event({"choice_reward_array": rows}, context)
        self.assertIsNone(result["outcomes"][0])
        texts = [line["text"] for line in result["outcomes"][1]["lines"]]
        self.assertIn("Energy +20", texts)
        self.assertIn("Energy +15", texts)
        rows[0]["gain_param_array"][0]["display_id"] = 99999
        self.assertIsNone(formatter.format_event({"choice_reward_array": rows}, context))

    def test_empty_and_bad_identity_preserve_catalog(self):
        formatter = RewardFormatter(SimpleNamespace())
        self.assertIsNone(formatter.format_event({}, {}))
        self.assertIsNone(formatter.format_event({}, {"event_contents_info": {
            "choice_array": [{"gain_select_id_index": None}]}}))

    def test_stale_or_closed_generation_never_updates(self):
        owner = SimpleNamespace(browser=Mock(), HELPER_UI_MODERN=1, get_helper_ui_mode=lambda: 1)
        extension = RuntimeExtensions(owner)
        extension.on_event_detected({"event_contents_info": {"choice_array": [{"gain_select_id_index": 1}]}}, [])
        extension.on_event_opened(3)
        data = {"choice_reward_array": [{"select_index": 1, "gain_param_array": []}]}
        extension.on_response(data, 2)
        owner.browser.execute_script.assert_not_called()
        extension.on_response(data, 3)
        owner.browser.execute_script.assert_called_once()
        owner.browser.reset_mock()
        extension.on_event_closed(3)
        extension.on_response(data, 3)
        owner.browser.execute_script.assert_not_called()
