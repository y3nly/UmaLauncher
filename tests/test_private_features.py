from __future__ import annotations

import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "umalauncher"))


class PrivateFeatureSmokeTests(unittest.TestCase):
    def test_event_reward_packet_produces_renderable_choice_summaries(self):
        from umalauncher_private.event_prediction import (
            EventPredictionExtension,
            EventRewardParser,
        )

        parser = EventRewardParser(skill_name_dict={1001: "Corner Adept"})
        browser = mock.Mock()
        browser.alive.return_value = True
        owner = SimpleNamespace(
            HELPER_UI_MODERN=1,
            _active_event_source_available=True,
            browser=browser,
            get_helper_ui_mode=lambda: 1,
        )
        extension = EventPredictionExtension(owner, parser=parser)
        context = extension.on_event_detected(
            {
                "event_id": 42,
                "story_id": 99,
                "event_contents_info": {
                    "choice_array": [
                        {"select_index": 1},
                        {"select_index": 1},
                    ]
                },
            },
            ["Fixture Event"],
        )
        extension.on_event_opened(7)
        prediction = extension.on_response(
            {
                "choice_reward_array": [
                    {
                        "select_index": 1,
                        "gain_param_array": [
                            {
                                "display_id": 1,
                                "effect_value_0": 1,
                                "effect_value_1": 10,
                            },
                            {
                                "display_id": 6,
                                "effect_value_0": 1001,
                                "effect_value_1": 1,
                            },
                        ],
                    },
                    {
                        "select_index": 2,
                        "gain_param_array": [{"display_id": 12}],
                    },
                ]
            },
            7,
        )

        self.assertEqual(context["event_id"], 42)
        self.assertEqual(context["story_id"], 99)
        self.assertEqual(prediction["event_titles"], ["Fixture Event"])
        self.assertEqual(len(prediction["choices"]), 2)

        first, second = prediction["choices"]
        self.assertEqual(
            first["summary"],
            "Speed +10, Corner Adept hint level +1",
        )
        self.assertEqual(
            [reward["tone"] for reward in first["rewards"]],
            ["positive", "hint"],
        )
        self.assertEqual(
            first["rewards"][1]["highlights"],
            ["Corner Adept"],
        )
        self.assertEqual(
            second["summary"],
            "End this Support Card's chain event",
        )
        self.assertEqual(second["rewards"][0]["tone"], "negative")
        self.assertIsInstance(json.dumps(prediction, sort_keys=True), str)
        browser.execute_script.assert_called_once()
        script, generation, rendered = browser.execute_script.call_args.args
        self.assertIn("UL_UPDATE_EVENT_REWARDS", script)
        self.assertEqual(generation, 7)
        self.assertEqual(rendered, prediction)

        legacy_browser = mock.Mock()
        legacy_browser.alive.return_value = True
        legacy_anchor = object()
        legacy_owner = SimpleNamespace(
            HELPER_UI_MODERN=1,
            browser=legacy_browser,
            get_helper_ui_mode=lambda: 0,
            determine_event_element=mock.Mock(return_value=legacy_anchor),
        )
        legacy_extension = EventPredictionExtension(legacy_owner, parser=parser)
        legacy_extension.on_event_detected(
            {
                "event_id": 42,
                "story_id": 99,
                "event_contents_info": {
                    "choice_array": [
                        {"select_index": 1},
                        {"select_index": 1},
                    ]
                },
            },
            ["Fixture Event"],
        )
        legacy_prediction = legacy_extension.on_response(
            {
                "choice_reward_array": [
                    {
                        "select_index": 1,
                        "gain_param_array": [
                            {
                                "display_id": 1,
                                "effect_value_0": 1,
                                "effect_value_1": 10,
                            }
                        ],
                    },
                    {
                        "select_index": 2,
                        "gain_param_array": [{"display_id": 12}],
                    },
                ]
            },
            None,
        )
        legacy_owner.determine_event_element.assert_called_once_with(
            ["Fixture Event"]
        )
        legacy_script, anchor, rendered = legacy_browser.execute_script.call_args.args
        self.assertIn("ul-choice-reward-inline", legacy_script)
        self.assertIs(anchor, legacy_anchor)
        self.assertEqual(rendered, legacy_prediction)

    def test_training_sim_worker_evaluates_vendored_packet_fixture(self):
        from umalauncher_private.training_sim import TrainingSimWorker

        payload = {
            "data": {
                "chara_info": {
                    "card_id": 100101,
                    "rarity": 3,
                    "talent_level": 5,
                    "scenario_id": 1,
                    "speed": 100,
                    "stamina": 100,
                    "power": 100,
                    "guts": 100,
                    "wiz": 100,
                    "skill_point": 0,
                    "vital": 100,
                    "max_vital": 100,
                    "motivation": 3,
                    "support_card_array": [],
                    "evaluation_info_array": [],
                    "training_level_info_array": [
                        {"command_id": 101, "level": 1}
                    ],
                },
                "home_info": {
                    "command_info_array": [
                        {
                            "command_id": 101,
                            "command_type": 1,
                            "failure_rate": 0,
                            "training_partner_array": [],
                            "params_inc_dec_info_array": [
                                {"target_type": 1, "value": 10},
                                {"target_type": 3, "value": 5},
                                {"target_type": 30, "value": 2},
                                {"target_type": 10, "value": -20},
                            ],
                        }
                    ]
                },
            }
        }

        worker = TrainingSimWorker()
        self.addCleanup(worker.close)
        command_info = {"speed": {}}
        result = worker.enrich_commands(payload, command_info)

        self.assertIsNotNone(result)
        self.assertEqual(result["scenario"], "TRAINING")
        self.assertEqual(result["chara"]["id"], 100101)
        self.assertEqual(len(result["rows"]), 1)
        row = result["rows"][0]
        self.assertEqual(row["commandId"], 101)
        self.assertEqual(row["type"], "SPEED")
        self.assertTrue(math.isfinite(float(row["score"])))
        self.assertEqual(command_info["speed"]["training_sim"], row)


if __name__ == "__main__":
    unittest.main()
