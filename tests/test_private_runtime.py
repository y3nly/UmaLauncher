from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "umalauncher"))

from helper_table import HelperTable
from helper_table_defaults import RowTypes
from helper_table_elements import Preset
from umalauncher_private.runtime import PrivateRuntimeExtensions
from umalauncher_private.training_rows import TrainingSimScoreRow


class PrivateTrainingPresetTests(unittest.TestCase):
    def setUp(self):
        self.preset = Preset(RowTypes)
        self.preset.name = "General"
        self.owner = SimpleNamespace(
            helper_table=SimpleNamespace(selected_preset=self.preset),
        )
        self.extension = PrivateRuntimeExtensions.__new__(PrivateRuntimeExtensions)
        self.extension.owner = self.owner
        self.extension.training_sim = mock.Mock()
        self.owner.runtime_extensions = self.extension

    def test_only_enabled_simulator_rows_trigger_evaluation(self):
        commands = {"speed": {}}
        packet = {"chara_info": {"scenario_id": 1}}
        self.preset.initialized_rows = [RowTypes.GAINED_STATS.value()]
        self.extension.enrich_training_commands(packet, commands)
        self.extension.training_sim.enrich_commands.assert_not_called()

        row = TrainingSimScoreRow()
        row.disabled = True
        self.preset.initialized_rows.append(row)
        self.extension.enrich_training_commands(packet, commands)
        self.extension.training_sim.enrich_commands.assert_not_called()

        row.disabled = False
        self.extension.enrich_training_commands(packet, {})
        self.extension.training_sim.enrich_commands.assert_not_called()
        self.extension.enrich_training_commands(packet, commands)
        self.extension.training_sim.enrich_commands.assert_called_once_with(
            packet, commands,
        )

    def test_scenario_preset_is_selected_before_training_enrichment(self):
        scenario_preset = Preset(RowTypes)
        scenario_preset.name = "Scenario"
        scenario_preset.initialized_rows = [TrainingSimScoreRow()]
        settings = mock.MagicMock()
        values = {
            "training_helper_table_scenario_presets_enabled": True,
            "training_helper_table_scenario_presets": {"1": "Scenario"},
        }
        settings.__getitem__.side_effect = values.__getitem__
        settings.get_preset_with_name.return_value = scenario_preset
        self.owner.threader = SimpleNamespace(settings=settings)
        self.owner.HELPER_UI_MODERN = 1
        self.owner.get_helper_ui_mode = lambda: 0
        table = HelperTable.__new__(HelperTable)
        table.carrotjuicer = self.owner
        table.selected_preset = self.preset
        self.owner.helper_table = table
        packet = {
            "chara_info": {
                "card_id": 100101, "turn": 1, "scenario_id": 1,
                "vital": 100, "max_vital": 100, "fans": 1, "skill_point": 0,
                "evaluation_info_array": [], "support_card_array": [],
                **{
                    "proper_" + name: 1
                    for name in (
                        "ground_turf", "ground_dirt", "distance_short",
                        "distance_mile", "distance_middle", "distance_long",
                    )
                },
            },
            "home_info": {"command_info_array": [{"command_id": 101}]},
        }
        with mock.patch.object(Preset, "generate_overlay", return_value="rendered"):
            self.assertEqual(table.create_helper_elements(packet, None), "rendered")
            self.assertIs(table.selected_preset, scenario_preset)
            self.extension.training_sim.enrich_commands.assert_called_once()

            # Removing the simulator row must take effect on the next refresh.
            scenario_preset.initialized_rows.clear()
            self.extension.training_sim.reset_mock()
            table.create_helper_elements(packet, None)
            self.extension.training_sim.enrich_commands.assert_not_called()


if __name__ == "__main__":
    unittest.main()
