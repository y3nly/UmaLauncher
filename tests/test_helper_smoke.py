from __future__ import annotations

from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "umalauncher"))

import helper_table_elements
import umaserver


class HelperAndServerFunctionalTests(unittest.TestCase):
    def test_modern_grand_live_gauges_stack_below_races(self):
        setting = lambda value: types.SimpleNamespace(value=value)
        preset = helper_table_elements.Preset.__new__(
            helper_table_elements.Preset
        )
        preset.initialized_rows = []
        preset.settings = types.SimpleNamespace(
            support_bonds=setting(0),
            hide_support_bonds=setting(False),
            energy_enabled=setting(True),
            progress_bar=setting(False),
            schedule_enabled=setting(False),
            scenario_specific_enabled=setting(True),
        )
        facilities = ("speed", "stamina", "power", "guts", "wiz")
        main_info = {
            "scenario_id": 3,
            "scenario_name": "Grand Live",
            "trainee_name": "Fixture",
            "motivation": "Normal",
            "stats": {facility: 100 for facility in facilities},
            "turn": 20,
            "skillpt": 100,
            "fans": 1_000,
            "energy": 50,
            "max_energy": 100,
            "gl_stats": {
                "dance": 1,
                "passion": 2,
                "vocal": 3,
                "visual": 4,
                "mental": 5,
            },
            "g1_races": [{
                "name": "Fixture Stakes",
                "thumb_url": "https://example.test/race.png",
            }],
        }
        command_info = {
            facility: {"partners": []}
            for facility in facilities
        }

        rendered = preset.generate_modern_overlay(main_info, command_info)

        self.assertNotIn('id="gl-tokens"', rendered)
        self.assertIn('class="modern-lower-side"', rendered)
        self.assertIn('class="modern-gl-panel"', rendered)
        self.assertLess(
            rendered.index('class="modern-g1-panel"'),
            rendered.index('class="modern-gl-panel"'),
        )
        token_positions = [
            rendered.index(f'aria-label="{label} {value}"')
            for label, value in (
                ("Dance", 1),
                ("Passion", 2),
                ("Vocal", 3),
                ("Visual", 4),
                ("Mental", 5),
            )
        ]
        self.assertEqual(token_positions, sorted(token_positions))

    def test_modern_helper_assets_and_events_route_are_served(self):
        juicer = types.SimpleNamespace(open_event_window=False)
        previous_threader = umaserver.threader
        self.addCleanup(setattr, umaserver, "threader", previous_threader)
        umaserver.threader = types.SimpleNamespace(carrotjuicer=juicer)
        client = umaserver.app.test_client()

        dashboard = client.get("/training-helper")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"Training Helper | UmaLauncher", dashboard.data)
        self.assertIn(b'id="open-events"', dashboard.data)
        dashboard.close()

        icon = client.get("/training-helper/assets/guts.png")
        self.assertEqual(icon.status_code, 200)
        self.assertTrue(icon.data.startswith(b"\x89PNG"))
        icon.close()

        self.assertEqual(client.post("/open-event-window").status_code, 200)
        self.assertTrue(juicer.open_event_window)


if __name__ == "__main__":
    unittest.main()
