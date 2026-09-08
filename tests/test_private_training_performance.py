from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "umalauncher"))

from umalauncher_private.umasim.data import load_store
from umalauncher_private.umasim.training import evaluate_training_packet


def training_packet(index=3):
    commands = (101, 105, 102, 103, 106)
    cards = (30028, 30052, 30047, 30016, 30020, 30001)
    return {
        "statCap": 1200 if index == 3 else None,
        "megaphone": 0 if index == 3 else 20,
        "weightTypes": ["SPEED", "POWER"] if index == 3 else [],
        "speedSkillCount": index,
        "healSkillCount": index // 2,
        "accelSkillCount": index // 3,
        "data": {
            "chara_info": {
                "card_id": 100101, "rarity": 3, "talent_level": 5,
                "scenario_id": 1 if index == 3 else 2,
                "speed": 1195 if index == 3 else 650,
                "stamina": 400, "power": 1180, "guts": 330, "wiz": 660,
                "skill_point": 450, "vital": index * 7,
                "max_vital": 100 if index == 3 else 110,
                "motivation": index + 1, "fans": 10000 * index,
                "support_card_array": [
                    {"position": position, "support_card_id": card_id, "limit_break_count": 4}
                    for position, card_id in enumerate(cards, 1)
                ],
                "evaluation_info_array": [
                    {"training_partner_id": position, "evaluation": (79, 80, 100)[(index + position) % 3]}
                    for position in range(1, 7)
                ],
                "training_level_info_array": [
                    {"command_id": command, "level": index + 1} for command in commands
                ],
            },
            "home_info": {"command_info_array": [
                {"command_id": command, "command_type": 1, "failure_rate": index,
                 "training_partner_array": [position for position in range(1, 7) if (index + position + slot) % 3 == 0]}
                for slot, command in enumerate(commands)
            ]},
            "team_data_set": {"command_info_array": [
                {"command_id": command, "params_inc_dec_info_array": [
                    {"target_type": slot + 1, "value": 4}, {"target_type": 30, "value": 1}]}
                for slot, command in enumerate(commands)
            ]} if index == 4 else {},
        },
    }


class PrivateTrainingPerformanceTests(unittest.TestCase):
    def test_capped_weighted_report_preserves_training_distribution(self):
        # Golden results captured before consolidating the per-stat support bonuses.
        report = evaluate_training_packet(training_packet())
        self.assertEqual([row.final_stats.status_total for row in report.rows], [20, 24, 36, 27, 20])
        self.assertEqual([row.final_stats.hp for row in report.rows], [-27, -19, -26, -24, 5])
        self.assertEqual([row.final_stats.skill_pt for row in report.rows], [4, 3, 4, 3, 4])
        expected_scores = [19.458752643712756, 25.40910078796753, 29.636017683258405, 21.946778576383583, 17.62582723646843]
        for row, expected in zip(report.rows, expected_scores):
            with self.subTest(facility=row.facility.type):
                self.assertAlmostEqual(row.expected_score, expected)
        self.assertAlmostEqual(report.rows[2].score_percentile, 0.9072893035730015)
        self.assertAlmostEqual(report.rows[2].whistle_downgrade_probability, 0.9227265640541712)

    def test_scenario_bonus_and_megaphone_preserve_training_distribution(self):
        report = evaluate_training_packet(training_packet(4))
        self.assertEqual([row.final_stats.status_total for row in report.rows], [43, 72, 61, 41, 35])
        self.assertEqual([row.final_stats.skill_pt for row in report.rows], [8, 12, 10, 8, 12])
        expected_scores = [45.57890282990457, 52.16650223320235, 45.22176210533683, 41.03395189249043, 33.163321383014036]
        for row, expected in zip(report.rows, expected_scores):
            with self.subTest(facility=row.facility.type):
                self.assertAlmostEqual(row.expected_score, expected)
        self.assertAlmostEqual(report.rows[4].score_percentile, 0.8634324068417949)
        self.assertAlmostEqual(report.rows[4].status_percentile, 0.8361143677826202)

    def test_all_limit_break_levels_select_the_matching_vendored_card(self):
        store = load_store()
        for limit_break in range(5):
            with self.subTest(limit_break=limit_break):
                packet = training_packet()
                card = packet["data"]["chara_info"]["support_card_array"][0]
                card["limit_break_count"] = limit_break
                report = evaluate_training_packet(packet)
                self.assertEqual(report.state.deck[0].limit_break_count, limit_break)
                self.assertEqual(report.deck_members[0].card, store.get_support(card["support_card_id"], limit_break))

    def test_limit_break_default_and_zero_aliases(self):
        for key, value, expected in [
            (None, None, 4), ("limit_break_count", "invalid", 4),
            ("limitBreak", 0, 0), ("talent", "0", 0),
        ]:
            with self.subTest(key=key, value=value):
                packet = training_packet()
                card = packet["data"]["chara_info"]["support_card_array"][0]
                del card["limit_break_count"]
                if key is not None:
                    card[key] = value
                self.assertEqual(evaluate_training_packet(packet).deck_members[0].card.talent, expected)


if __name__ == "__main__":
    unittest.main()
