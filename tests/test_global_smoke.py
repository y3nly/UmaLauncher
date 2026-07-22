from __future__ import annotations

from pathlib import Path
import tempfile
import types
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "umalauncher"))

import horsium
import steam
import util
import version


class GlobalRuntimeSmokeTests(unittest.TestCase):
    def test_global_helper_url_and_steam_launch_command(self):
        self.assertEqual(
            util.create_gametora_helper_url(100101, 1, [1, 2, 3, 4, 5, 6]),
            (
                "https://gametora.com/umamusume/training-event-helper?"
                "deck=lgdv-3f-co&server=en"
            ),
        )

        with mock.patch.object(steam.os, "system") as system:
            steam.start()
        system.assert_called_once_with("Start steam://rungameid/3224770")

    def test_updater_relaunches_global_executable_with_clean_environment(self):
        asset = {
            "name": "UmaLauncher-Global.exe",
            "browser_download_url": (
                "https://example.invalid/UmaLauncher-Global.exe"
            ),
        }
        process = mock.Mock()
        process.poll.return_value = 0

        with tempfile.TemporaryDirectory() as temporary_directory:
            executable = str(Path(temporary_directory) / "UmaLauncher-Global.exe")
            with (
                mock.patch.object(version.sys, "executable", executable),
                mock.patch.object(
                    version.util,
                    "get_appdata",
                    side_effect=lambda name: str(
                        Path(temporary_directory) / name
                    ),
                ),
                mock.patch.object(version.urllib.request, "urlretrieve"),
                mock.patch.object(
                    version.subprocess, "Popen", return_value=process
                ) as popen,
            ):
                version.Updater([asset]).run()

            self.assertTrue(
                (Path(temporary_directory) / "update.tmp").is_file()
            )
            self.assertEqual(
                popen.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"],
                "1",
            )
            self.assertTrue(popen.call_args.kwargs["shell"])
            replacement_command = popen.call_args.args[0]
            self.assertIn(f'"{executable}"', replacement_command)

    def test_chromium_setup_applies_bundled_blocklist(self):
        class Options:
            def __init__(self):
                self.arguments = []
                self.experimental_options = {}
                self.page_load_strategy = None

            def add_argument(self, argument):
                self.arguments.append(argument)

            def add_experimental_option(self, name, value):
                self.experimental_options[name] = value

        class Driver:
            def __init__(self, service, options):
                self.service = service
                self.options = options
                self.commands = []
                self.command_executor = types.SimpleNamespace(
                    client_config=types.SimpleNamespace(timeout=None)
                )

            def execute_cdp_cmd(self, name, payload):
                self.commands.append((name, payload))

        service = types.SimpleNamespace(creation_flags=None)
        settings = {
            "browser_version": None,
            "enable_browser_override": False,
        }

        with (
            mock.patch.object(horsium, "ADBLOCK_RULES_CACHE", None),
            mock.patch.object(horsium.util, "is_script", True),
            mock.patch.object(horsium, "_is_port_open", return_value=False),
        ):
            browser = horsium.chromium_setup(
                service=service,
                options_class=Options,
                driver_class=Driver,
                profile="browser-smoke-profile",
                helper_url="http://127.0.0.1:3150/training-helper",
                settings=settings,
                base_port=39000,
                max_port=39001,
            )

        blocked_command = next(
            payload
            for name, payload in browser.commands
            if name == "Network.setBlockedURLs"
        )
        self.assertGreater(len(blocked_command["urls"]), 1_000)
        self.assertIn("*://*.101com.com/*", blocked_command["urls"])
        self.assertEqual(
            browser.command_executor.client_config.timeout,
            horsium.WEBDRIVER_COMMAND_TIMEOUT,
        )


if __name__ == "__main__":
    unittest.main()
