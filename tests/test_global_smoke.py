from __future__ import annotations

from pathlib import Path
import tempfile
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "umalauncher"))

import version


class GlobalRuntimeFunctionalTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
