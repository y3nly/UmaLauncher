"""Functional rating checks using recorded veterans and the installed master."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'umalauncher'))


class RecordedVeteranRatingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_path = os.environ.get('UMALAUNCHER_TEST_MASTER_DB')
        if not db_path or not Path(db_path).is_file():
            raise unittest.SkipTest(
                'Set UMALAUNCHER_TEST_MASTER_DB to an installed Global master.mdb'
            )
        import skill_planner
        from skill_simulator_data import SkillDataSnapshot

        cls.planner = skill_planner
        cache = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cache.cleanup)
        snapshot = SkillDataSnapshot(cache.name)
        snapshot.refresh(db_path)
        cls.catalog = snapshot.catalog
        recording = json.loads(
            (ROOT / 'tests' / 'fixtures' / 'skill_ratings' / 'terrain_veterans.json')
            .read_text(encoding='utf-8')
        )
        cls.veterans = recording['veterans']

    def test_complete_rating_matches_recorded_server_score(self):
        for veteran in self.veterans:
            with self.subTest(career=veteran['single_mode_chara_id']):
                rating, _groups = self.planner.current_rating(veteran, self.catalog)
                self.assertEqual(rating['score'], veteran['rank_score'])


if __name__ == '__main__':
    unittest.main()
