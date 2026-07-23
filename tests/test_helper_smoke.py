from __future__ import annotations

from pathlib import Path
import sys
import threading
import types
import unittest
import urllib.request
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "umalauncher"))

import carrotjuicer
import helper_table
import helper_table_defaults
import helper_table_elements
import umaserver


class HelperAndServerSmokeTests(unittest.TestCase):
    def test_akikawa_never_contributes_to_useful_bond(self):
        partner = helper_table.TrainingPartner.__new__(
            helper_table.TrainingPartner
        )
        partner.partner_id = 102

        for scenario_id in range(1, 14):
            with self.subTest(scenario_id=scenario_id):
                partner.chara_info = {"scenario_id": scenario_id}
                self.assertEqual(partner.calc_useful_bond(7, 0), 0)

    def test_helper_defaults_omit_progress_hiding_and_unity_score(self):
        preset_settings = helper_table_elements.PresetSettings()
        self.assertFalse(preset_settings.progress_bar.value)
        self.assertFalse(preset_settings.hide_support_bonds.value)

        preset = helper_table_defaults.DefaultPreset(
            helper_table_defaults.RowTypes
        )
        self.assertFalse(any(
            isinstance(row, helper_table_defaults.UnityScoreRow)
            for row in preset.initialized_rows
        ))

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

    def test_threaded_server_serves_fast_request_while_slow_request_waits(self):
        test_app = umaserver.Flask("threaded-server-smoke")
        slow_started = threading.Event()
        release_slow = threading.Event()
        slow_result = []
        slow_error = []

        @test_app.route("/slow")
        def slow():
            slow_started.set()
            release_slow.wait(timeout=3)
            return "slow"

        @test_app.route("/fast")
        def fast():
            return "fast"

        server = umaserver.UmaServer(types.SimpleNamespace(carrotjuicer=None))
        server_thread = threading.Thread(target=server.run, daemon=True)
        slow_thread = None

        with (
            mock.patch.object(umaserver, "app", test_app),
            mock.patch.object(umaserver, "domain", "127.0.0.1"),
            mock.patch.object(umaserver, "port", 0),
        ):
            try:
                server_thread.start()
                self.assertTrue(server.ready.wait(timeout=2))
                actual_port = server.server.server_port

                def request_slow():
                    try:
                        with urllib.request.urlopen(
                            f"http://127.0.0.1:{actual_port}/slow", timeout=3
                        ) as response:
                            slow_result.append(response.read())
                    except Exception as exc:  # pragma: no cover - assertion below
                        slow_error.append(exc)

                slow_thread = threading.Thread(target=request_slow, daemon=True)
                slow_thread.start()
                self.assertTrue(slow_started.wait(timeout=2))

                with urllib.request.urlopen(
                    f"http://127.0.0.1:{actual_port}/fast", timeout=2
                ) as response:
                    self.assertEqual(response.read(), b"fast")
            finally:
                release_slow.set()
                if slow_thread is not None:
                    slow_thread.join(timeout=3)
                server.stop()
                server_thread.join(timeout=3)

        self.assertEqual(slow_result, [b"slow"])
        self.assertEqual(slow_error, [])
        self.assertFalse(server_thread.is_alive())

    def test_legacy_modern_selection_and_reusable_events_window(self):
        created_windows = []

        class BrowserWindow:
            def __init__(self, url, threader, **kwargs):
                self.url = url
                self.threader = threader
                self.kwargs = kwargs
                self.focus_count = 0
                self.scripts = []
                created_windows.append(self)

            def alive(self):
                return True

            def focus(self):
                self.focus_count += 1

            def execute_script(self, script, *args):
                self.scripts.append((script, args))
                return True

            def set_topmost(self, value):
                self.topmost = value

            def set_pair(self, value):
                self.paired = value

        settings = {
            "training_helper_ui": carrotjuicer.CarrotJuicer.HELPER_UI_LEGACY,
            "browser_position": [10, 20, 800, 600],
            "browser_topmost": False,
            "browser_pair": True,
            "events_position": None,
        }
        juicer = carrotjuicer.CarrotJuicer.__new__(carrotjuicer.CarrotJuicer)
        juicer.threader = types.SimpleNamespace(settings=settings)
        juicer.should_stop = False
        juicer.helper_url = (
            "https://gametora.com/umamusume/training-event-helper?server=en"
        )
        juicer.browser = None
        juicer.event_browser = None
        juicer.active_helper_mode = None
        juicer.close_browser = mock.Mock(
            side_effect=lambda: setattr(juicer, "browser", None)
        )

        with mock.patch.object(
            carrotjuicer.horsium, "BrowserWindow", BrowserWindow
        ):
            juicer.open_helper()
            legacy_window = juicer.browser
            self.assertEqual(legacy_window.url, juicer.helper_url)
            self.assertIsNone(legacy_window.kwargs["window_title"])

            settings["training_helper_ui"] = (
                carrotjuicer.CarrotJuicer.HELPER_UI_MODERN
            )
            juicer.open_helper()
            modern_window = juicer.browser
            self.assertEqual(
                modern_window.url, carrotjuicer.CarrotJuicer.MODERN_HELPER_URL
            )
            self.assertEqual(
                modern_window.kwargs["window_title"],
                carrotjuicer.CarrotJuicer.MODERN_HELPER_TITLE,
            )

            self.assertTrue(juicer.update_event_window())
            event_window = juicer.event_browser
            self.assertTrue(juicer.update_event_window())

        self.assertEqual(len(created_windows), 3)
        self.assertIs(juicer.event_browser, event_window)
        self.assertEqual(event_window.focus_count, 2)
        self.assertEqual(
            event_window.kwargs["window_title"],
            carrotjuicer.CarrotJuicer.EVENTS_WINDOW_TITLE,
        )


if __name__ == "__main__":
    unittest.main()
