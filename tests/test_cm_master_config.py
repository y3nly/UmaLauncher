import gc
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "umalauncher"))

import mdb


def create_cm_master_fixture(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE champions_schedule (id INTEGER PRIMARY KEY);
            CREATE TABLE champions_race_condition (
                champions_id INTEGER,
                round_id INTEGER,
                race_instance_id INTEGER,
                race_condition_id INTEGER
            );
            CREATE TABLE race_instance (id INTEGER PRIMARY KEY, race_id INTEGER);
            CREATE TABLE race (id INTEGER PRIMARY KEY, course_set INTEGER);
            CREATE TABLE race_course_set (
                id INTEGER PRIMARY KEY,
                race_track_id INTEGER
            );
            CREATE TABLE race_condition (
                id INTEGER PRIMARY KEY,
                season INTEGER,
                weather INTEGER,
                ground INTEGER
            );
            CREATE TABLE text_data (
                category INTEGER,
                "index" INTEGER,
                text TEXT
            );
            """
        )
        for cm_id in (16, 17, 18, 19):
            connection.execute(
                "INSERT INTO champions_schedule VALUES (?)",
                (cm_id,),
            )
            connection.execute(
                "INSERT INTO text_data VALUES (206, ?, ?)",
                (cm_id, f"CM {cm_id}"),
            )

        for cm_id, condition_id, ground in (
            (16, 116, 1),
            (17, 117, 2),
            (18, 118, 3),
        ):
            race_instance_id = 600000 + cm_id
            race_id = 6000 + cm_id
            course_id = 10000 + cm_id
            connection.execute(
                "INSERT INTO champions_race_condition VALUES (?, 0, ?, ?)",
                (cm_id, race_instance_id, condition_id),
            )
            connection.execute(
                "INSERT INTO race_instance VALUES (?, ?)",
                (race_instance_id, race_id),
            )
            connection.execute(
                "INSERT INTO race VALUES (?, ?)",
                (race_id, course_id),
            )
            connection.execute(
                "INSERT INTO race_course_set VALUES (?, ?)",
                (course_id, 1000 + cm_id),
            )
            connection.execute(
                "INSERT INTO race_condition VALUES (?, 3, 2, ?)",
                (condition_id, ground),
            )


class ChampionsMeetingMasterConfigTests(unittest.TestCase):
    def test_latest_two_complete_cms_are_loaded_from_master(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "master.mdb"
            create_cm_master_fixture(database)

            with mock.patch.multiple(
                mdb,
                DB_PATH=str(database),
                CHAMPIONS_MEETING_CONFIGS={},
            ):
                configs = mdb.get_champions_meeting_configs(
                    force=True,
                    limit=2,
                )

            self.assertEqual(list(configs), [17, 18])
            self.assertEqual(
                configs[17],
                {
                    "name": "CM 17",
                    "location": 1017,
                    "course": 10017,
                    "season": 3,
                    "weather": 2,
                    "ground_condition": "YAYAOMO",
                },
            )
            self.assertEqual(configs[18]["ground_condition"], "OMO")

            # Release SQLite cursor/connection cycles before Windows removes
            # the temporary database.
            gc.collect()


if __name__ == "__main__":
    unittest.main()
