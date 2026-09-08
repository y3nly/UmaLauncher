from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "umalauncher"))

from umalauncher_private.event_prediction import EventPredictionExtension


class PrivatePredictionLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.browser = mock.Mock()
        self.browser.alive.return_value = True
        self.owner = SimpleNamespace(
            HELPER_UI_MODERN=1,
            _active_event_source_available=True,
            browser=self.browser,
            get_helper_ui_mode=lambda: 1,
        )
        self.parser = mock.Mock()
        self.parser.create_event_context.side_effect = lambda data, titles: {
            "event_id": data["event_id"],
            "event_titles": titles,
        }
        self.parser.parse_choice_reward_response.return_value = {
            "event_titles": ["Current event"],
            "choices": [{"choice_number": 1, "summary": "Speed +10"}],
        }
        self.extension = EventPredictionExtension(self.owner, parser=self.parser)
        self.extension.on_event_detected({"event_id": 42}, ["Current event"])
        self.extension.on_event_opened(7)

    def test_new_rewards_reach_browser_while_previewing_another_chain_card(self):
        self.assertTrue(self.extension.on_event_chain_changed(
            {"index": 0, "packetIndex": 1}, 7
        ))
        prediction = self.extension.on_response({"choice_reward_array": []}, 7)

        self.browser.execute_script.assert_called_once_with(
            "return window.UL_UPDATE_EVENT_REWARDS(arguments[0], arguments[1]);",
            7,
            prediction,
        )
        self.browser.reset_mock()

        # The existing UL_SET_GAMETORA_EVENT browser hook restores this cached
        # prediction on return. Python must not perform another remote call.
        self.assertTrue(self.extension.on_event_chain_changed(
            {"index": 1, "packetIndex": 1}, 7
        ))
        self.assertEqual(self.browser.mock_calls, [])

    def test_chain_navigation_does_not_check_browser_or_resend_rewards(self):
        for chain in (
            {"index": 0, "packetIndex": 1},
            {"index": 1, "packetIndex": 1},
        ):
            self.assertTrue(self.extension.on_event_chain_changed(chain, 7))
        self.assertFalse(self.extension.on_event_chain_changed({}, 6))
        self.assertEqual(self.browser.mock_calls, [])

    def test_stale_generation_does_not_deliver_rewards(self):
        self.extension.on_event_opened(8)
        self.assertIsNotNone(self.extension.on_response({"choice_reward_array": []}, 7))
        self.assertEqual(self.browser.mock_calls, [])

    def test_missing_event_source_does_not_deliver_rewards(self):
        self.owner._active_event_source_available = False
        self.assertIsNotNone(self.extension.on_response({"choice_reward_array": []}, 7))
        self.assertEqual(self.browser.mock_calls, [])

    def test_generation_closed_during_parse_does_not_deliver_rewards(self):
        prediction = self.parser.parse_choice_reward_response.return_value

        def close_event(*args, **kwargs):
            self.extension.on_event_closed(7)
            return prediction

        self.parser.parse_choice_reward_response.side_effect = close_event
        self.extension.on_response({"choice_reward_array": []}, 7)
        self.assertEqual(self.browser.mock_calls, [])

    def test_concurrently_staged_context_is_kept_for_next_response(self):
        prediction = self.parser.parse_choice_reward_response.return_value

        def stage_next_event(*args, **kwargs):
            self.extension.on_event_detected({"event_id": 43}, ["Next event"])
            return prediction

        self.parser.parse_choice_reward_response.side_effect = stage_next_event
        self.extension.on_response({"choice_reward_array": []}, 7)
        self.parser.parse_choice_reward_response.side_effect = None
        self.extension.on_event_opened(8)
        self.extension.on_response({"choice_reward_array": []}, 8)

        args, kwargs = self.parser.parse_choice_reward_response.call_args
        self.assertEqual(args[1]["event_id"], 43)
        self.assertEqual(kwargs["event_id"], 43)


if __name__ == "__main__":
    unittest.main()
