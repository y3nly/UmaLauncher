from __future__ import annotations

import gc
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "umalauncher"))

import mdb
import settings
import training_tracker


def create_master_fixture(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE skill_data (
                id INTEGER PRIMARY KEY,
                group_id INTEGER,
                rarity INTEGER,
                group_rate REAL,
                skill_category INTEGER,
                disp_order INTEGER
            );
            CREATE TABLE card_data (
                id INTEGER PRIMARY KEY,
                available_skill_set_id INTEGER
            );
            CREATE TABLE available_skill_set (
                available_skill_set_id INTEGER,
                skill_id INTEGER,
                need_rank INTEGER
            );
            CREATE TABLE single_mode_hint_gain (
                id INTEGER PRIMARY KEY,
                hint_id INTEGER,
                support_card_id INTEGER,
                hint_group INTEGER,
                hint_gain_type INTEGER,
                hint_value_1 INTEGER,
                hint_value_2 INTEGER
            );
            CREATE TABLE text_data (
                id INTEGER PRIMARY KEY,
                category INTEGER,
                "index" INTEGER,
                text TEXT
            );
            CREATE TABLE single_mode_program (
                id INTEGER PRIMARY KEY,
                race_instance_id INTEGER,
                base_program_id INTEGER
            );
            CREATE TABLE race_instance (
                id INTEGER PRIMARY KEY,
                race_id INTEGER
            );
            CREATE TABLE race (
                id INTEGER PRIMARY KEY,
                grade INTEGER,
                thumbnail_id INTEGER
            );
            """
        )
        connection.executemany(
            "INSERT INTO skill_data VALUES (?, ?, ?, ?, ?, ?)",
            [
                (300050, 30, 1, 1, 1, 10),
                (300007, 30, 1, 2, 1, 20),
            ],
        )
        connection.execute("INSERT INTO card_data VALUES (500, 50)")
        connection.execute(
            "INSERT INTO available_skill_set VALUES (50, 300050, 1)"
        )
        connection.execute(
            "INSERT INTO text_data VALUES (1, 47, 300007, 'Corner Adept')"
        )
        connection.execute(
            "INSERT INTO single_mode_hint_gain "
            "VALUES (1, 700, 30001, 1, 0, 300007, 2)"
        )
        connection.execute(
            "INSERT INTO single_mode_program VALUES (10, 1000, 0)"
        )
        connection.execute("INSERT INTO race_instance VALUES (1000, 100)")
        connection.execute("INSERT INTO race VALUES (100, 100, 9020)")


class PersistenceFunctionalTests(unittest.TestCase):
    def test_temporary_master_database_serves_skill_hint_and_race_queries(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "master.mdb"
            create_master_fixture(database)

            with mock.patch.multiple(
                mdb,
                DB_PATH=str(database),
                _QUERY_CATALOG_FINGERPRINT=None,
                _SKILL_CATALOG={},
                _SKILLS_BY_GROUP={},
                _SKILLS_BY_GROUP_RARITY={},
                _CARD_INHERENT_SKILLS={},
                _SUPPORT_HINT_POOL_DICT={},
                PROGRAM_ID_DICT={},
            ):
                self.assertEqual(
                    mdb.get_next_skill_id_in_chain(300050), 300007
                )
                hints = mdb.get_support_hint_pool_dict()
                self.assertEqual(hints[30001][0]["name"], "Corner Adept")
                self.assertEqual(hints[30001][0]["granted_level"], 2)
                race = mdb.get_program_id_data(10)
                self.assertEqual(race["race_grade"], 100)
                self.assertEqual(race["race_thumbnail_id"], 9020)

            # Release SQLite cursor/connection cycles before Windows removes
            # the temporary database.
            gc.collect()

    def test_settings_round_trip_migrates_helper_mode_and_theme(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            settings_path = Path(temporary_directory) / "umasettings.json"
            handler = settings.SettingsHandler.__new__(settings.SettingsHandler)
            handler.threader = types.SimpleNamespace(stop=mock.Mock())
            handler.loaded_settings = settings.DefaultSettings()
            values = handler.loaded_settings.to_dict()
            values.update(
                {
                    "training_helper_ui": "both",
                    "training_helper_scale": 120,
                    "helper_theme": 1,
                    "helper_theme_text_color": "#abcdef",
                    "browser_pair": True,
                }
            )
            settings_path.write_text(json.dumps(values), encoding="utf-8")

            path_lookup = mock.Mock(return_value=str(settings_path))
            with (
                mock.patch.object(settings.util, "get_appdata", path_lookup),
                mock.patch.object(settings.util, "get_relative", path_lookup),
                mock.patch.object(settings.util, "is_script", True),
                mock.patch.object(settings.util, "log_set_trace"),
                mock.patch.object(settings.util, "log_set_info"),
            ):
                handler.load_settings()
                self.assertEqual(handler["training_helper_ui"], 1)
                self.assertEqual(
                    handler["helper_theme_text_color"], "#ABCDEF"
                )
                handler["browser_pair"] = False

                reloaded = settings.SettingsHandler.__new__(
                    settings.SettingsHandler
                )
                reloaded.threader = types.SimpleNamespace(stop=mock.Mock())
                reloaded.loaded_settings = settings.DefaultSettings()
                reloaded.load_settings()

            self.assertFalse(reloaded["browser_pair"])
            self.assertEqual(reloaded["training_helper_ui"], 1)
            saved = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertNotIn("training_helper_scale", saved)

    def test_training_log_round_trip_uses_safe_gzip_filename(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            tracker = training_tracker.TrainingTracker(
                "CON", training_log_folder=temporary_directory
            )
            tracker.add_request({"device_id": "redacted", "turn": 1})
            tracker.add_response({"data": {"turn": 1}})

            log_path = Path(tracker.get_sav_path())
            self.assertEqual(log_path.name, "CON_.gz")
            self.assertTrue(log_path.is_file())
            self.assertEqual(
                tracker.load_packets(),
                [
                    {"turn": 1, "_direction": 0},
                    {"data": {"turn": 1}, "_direction": 1},
                ],
            )


if __name__ == "__main__":
    unittest.main()
