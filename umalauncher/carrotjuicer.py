import io
import os
import time
import glob
import traceback
import math
import json
from datetime import datetime

import msgpack
import select
from loguru import logger
from msgpack import Unpacker
from selenium.common.exceptions import NoSuchWindowException
import screenstate_utils
import util
import constants
import mdb
import helper_table
import training_tracker
import horsium
import socket
import subprocess

from Cryptodome.Cipher import AES


STAT_BLOCK_MULTIPLIERS = [
    0.5, 0.8, 1.0, 1.3, 1.6, 1.8, 2.1, 2.4, 2.6, 2.8, 
    2.9, 3.0, 3.1, 3.3, 3.4, 3.5, 3.9, 4.1, 4.2, 4.3, 
    5.2, 5.5, 6.6, 6.8
]

STAT_SCORES = [0] * 1202
_score_scaled = 0
for _i in range(1, 1201):
    _block = (_i - 1) // 50
    _score_scaled += int(STAT_BLOCK_MULTIPLIERS[_block] * 10)
    STAT_SCORES[_i] = _score_scaled // 10
STAT_SCORES[1200] = 3841



def unpack(data: bytes, key: bytes, iv: bytes) -> bytes:
    logger.debug(f"Unpacking:\nData: {data.hex()}\nKey: {key.hex()}\nIV: {iv.hex()}")
    cipher = AES.new(key, AES.MODE_CBC, iv=iv)
    decrypted = cipher.decrypt(data)
    decrypted = decrypted[4:]
    b = io.BytesIO(decrypted)
    unpacker = Unpacker(file_like=b)
    return unpacker.unpack()


class CarrotJuicer:
    browser: horsium.BrowserWindow = None
    previous_element = None
    threader = None
    screen_state_handler = None
    helper_table = None
    should_stop = False
    last_browser_rect = None
    browser_topmost = False
    reset_browser = False
    helper_url = None
    last_training_id = None
    training_tracker = None
    previous_request = None
    last_helper_data = None
    skills_list = []
    previous_skills_list = []
    previous_race_program_id = None
    last_data = None
    open_skill_window = False
    skill_browser = None
    last_skills_rect = None
    skipped_msgpacks = []

    sock: socket = None
    MAX_BUFFER_SIZE = 65535

    key = None
    iv = None
    encrypted_data = None

    def __init__(self, threader):
        self.threader = threader

        self.start_time = 0

        self.skill_id_dict = mdb.get_skill_id_dict()
        self.status_name_dict = mdb.get_status_name_dict()
        self.skill_name_dict = mdb.get_skill_name_dict()
        self.skill_costs_dict = mdb.get_skill_costs_dict()
        self.skill_conditions_dict = mdb.get_skill_conditions_dict()
        self.skill_score_dict = mdb.get_skill_score_dict()
        self.group_id_dict = mdb.get_group_id_dict()

        self.skill_data = {}
        self.skills_list = []
        self.style = ''

        self.screen_state_handler = threader.screenstate
        self.restart_time()

        self.helper_table = helper_table.HelperTable(self)

    def get_stat_score(self, val):
        x = val + 1
        if x <= 0: return 0
        if x <= 1200:
            return STAT_SCORES[x]
        if x <= 1209:
            return round((x - 1200) * 7.888 + 3841)
        
        multipliers_10 = {
            1210: 8.0, 1220: 8.1, 1230: 8.3, 1240: 8.4, 1250: 8.5, 1260: 8.6, 1270: 8.8, 1280: 8.9, 1290: 9.0,
            1300: 9.2, 1310: 9.3, 1320: 9.4, 1330: 9.6, 1340: 9.7, 1350: 9.8, 1360: 10.0, 1370: 10.1, 1380: 10.2, 1390: 10.3,
            1400: 10.5, 1410: 10.6, 1420: 10.7, 1430: 10.9, 1440: 11.0, 1450: 11.1, 1460: 11.3, 1470: 11.4, 1480: 11.5, 1490: 11.7,
            1500: 11.8, 1510: 11.9, 1520: 12.1, 1530: 12.2, 1540: 12.3, 1550: 12.4, 1560: 12.6, 1570: 12.7, 1580: 12.8, 1590: 13.0,
            1600: 13.1, 1610: 13.2, 1620: 13.4, 1630: 13.5, 1640: 13.6, 1650: 13.8, 1660: 13.9, 1670: 14.0, 1680: 14.1, 1690: 14.3,
            1700: 14.4, 1710: 14.5, 1720: 14.7, 1730: 14.8, 1740: 14.9, 1750: 15.1, 1760: 15.2, 1770: 15.3, 1780: 15.5, 1790: 15.6,
            1800: 15.7, 1810: 15.9, 1820: 16.0, 1830: 16.1, 1840: 16.2, 1850: 16.4, 1860: 16.5, 1870: 16.6, 1880: 16.8, 1890: 16.9,
            1900: 17.0, 1910: 17.2, 1920: 17.3, 1930: 17.4, 1940: 17.6, 1950: 17.7, 1960: 17.8, 1970: 17.9, 1980: 18.1, 1990: 18.2,
            2000: 18.3
        }
        block_key = (x // 10) * 10
        mult = multipliers_10.get(block_key, multipliers_10[min(multipliers_10.keys(), key=lambda k: abs(k-block_key))]) # Fallback to nearest
        return round((x - 1209) * mult + 3912)

    def get_aptitude_multiplier(self, apt_val):
        if apt_val >= 7: return 1.1     # S or A
        if apt_val >= 5: return 0.9   # B or C
        if apt_val >= 2: return 0.8   # D, E, F
        return 0.7                    # G

    def get_rank_str(self, score):
        if score < 300: return "G"
        if score < 600: return "G+"
        if score < 900: return "F"
        if score < 1300: return "F+"
        if score < 1800: return "E"
        if score < 2300: return "E+"
        if score < 2900: return "D"
        if score < 3500: return "D+"
        if score < 4900: return "C"
        if score < 6500: return "C+"
        if score < 8200: return "B"
        if score < 10000: return "B+"
        if score < 12100: return "A"
        if score < 14500: return "A+"
        if score < 15900: return "S"
        if score < 17500: return "S+"
        if score < 19200: return "SS"
        if score < 19600: return "SS+"
        if score < 23900:
            sub = (score - 19600) // 400
            return f"UG{sub}" if sub > 0 else "UG"
        if score < 28800:
            sub = (score - 23900) // 500
            return f"UF{sub}" if sub > 0 else "UF"
        if score < 34400:
            sub = (score - 28800) // 560
            return f"UE{sub}" if sub > 0 else "UE"
        if score < 40700:
            sub = (score - 34400) // 630
            return f"UD{sub}" if sub > 0 else "UD"
        if score < 47600:
            sub = (score - 40700) // 700
            return f"UC{sub}" if sub > 0 else "UC"
        if score < 55200:
            sub = (score - 47600) // 760
            return f"UB{sub}" if sub > 0 else "UB"
        sub = (score - 55200) // 800
        return f"UA{sub}" if sub > 0 else "UA"

    def get_next_rank_req(self, score):
        if score < 300: return 300 - score
        if score < 600: return 600 - score
        if score < 900: return 900 - score
        if score < 1300: return 1300 - score
        if score < 1800: return 1800 - score
        if score < 2300: return 2300 - score
        if score < 2900: return 2900 - score
        if score < 3500: return 3500 - score
        if score < 4900: return 4900 - score
        if score < 6500: return 6500 - score
        if score < 8200: return 8200 - score
        if score < 10000: return 10000 - score
        if score < 12100: return 12100 - score
        if score < 14500: return 14500 - score
        if score < 15900: return 15900 - score
        if score < 17500: return 17500 - score
        if score < 19200: return 19200 - score
        if score < 19600: return 19600 - score
        if score < 23900:
            base, step, bound = 19600, 400, 23900
            return min(bound, base + ((score - base) // step + 1) * step) - score
        if score < 28800:
            base, step, bound = 23900, 500, 28800
            return min(bound, base + ((score - base) // step + 1) * step) - score
        if score < 34400:
            base, step, bound = 28800, 560, 34400
            return min(bound, base + ((score - base) // step + 1) * step) - score
        if score < 40700:
            base, step, bound = 34400, 630, 40700
            return min(bound, base + ((score - base) // step + 1) * step) - score
        if score < 47600:
            base, step, bound = 40700, 700, 47600
            return min(bound, base + ((score - base) // step + 1) * step) - score
        if score < 55200:
            base, step, bound = 47600, 760, 55200
            return min(bound, base + ((score - base) // step + 1) * step) - score
        base, step = 55200, 800
        return base + ((score - base) // step + 1) * step - score

    def calculate_uma_rank_score(self, chara_info, skill_data):
        total_score = 0
        skill_scores_map = {}
        
        s_speed = self.get_stat_score(chara_info.get('speed', 0))
        s_stamina = self.get_stat_score(chara_info.get('stamina', 0))
        s_power = self.get_stat_score(chara_info.get('power', 0))
        s_guts = self.get_stat_score(chara_info.get('guts', 0))
        s_wiz = self.get_stat_score(chara_info.get('wiz', 0))
        
        total_score += s_speed + s_stamina + s_power + s_guts + s_wiz

        skill_scores = self.skill_score_dict
        skill_conditions = self.skill_conditions_dict
        unique_skill_id = None
        unique_skill_level = 1
        stars = chara_info.get('talent_level', 1)
        
        for skill in chara_info.get('skill_array', []):
            if str(skill.get('skill_id', '')).startswith('1'):
                unique_skill_id = skill['skill_id']
                unique_skill_level = skill.get('level', 1)
                break

        for sid, info in skill_data.items():
            if str(sid).startswith('1'):
                continue
            base_score = skill_scores.get(sid, 0)
            cond = skill_conditions.get(sid, "")
            multiplier = 1.0
            if "distance_type==" in cond:
                if "distance_type==1" in cond: multiplier = self.get_aptitude_multiplier(chara_info.get('proper_distance_short', 1))
                elif "distance_type==2" in cond: multiplier = self.get_aptitude_multiplier(chara_info.get('proper_distance_mile', 1))
                elif "distance_type==3" in cond: multiplier = self.get_aptitude_multiplier(chara_info.get('proper_distance_middle', 1))
                elif "distance_type==4" in cond: multiplier = self.get_aptitude_multiplier(chara_info.get('proper_distance_long', 1))
            elif "ground_type==" in cond:
                if "ground_type==1" in cond: multiplier = self.get_aptitude_multiplier(chara_info.get('proper_ground_turf', 1))
                elif "ground_type==2" in cond: multiplier = self.get_aptitude_multiplier(chara_info.get('proper_ground_dirt', 1))
            elif "running_style==" in cond:
                if "running_style==1" in cond: multiplier = self.get_aptitude_multiplier(chara_info.get('proper_running_style_nige', 1))
                elif "running_style==2" in cond: multiplier = self.get_aptitude_multiplier(chara_info.get('proper_running_style_senko', 1))
                elif "running_style==3" in cond: multiplier = self.get_aptitude_multiplier(chara_info.get('proper_running_style_sashi', 1))
                elif "running_style==4" in cond: multiplier = self.get_aptitude_multiplier(chara_info.get('proper_running_style_oikomi', 1))
            final_s = round(base_score * multiplier)
            if info.get('is_acquired'):
                total_score += final_s
            skill_scores_map[str(sid)] = final_s
        unique_mult = 170 if stars >= 3 else 120
        u_score = unique_skill_level * unique_mult
        total_score += u_score
        print(f"UMA RANK CALC DEBUG -> Unique Skill ID: {unique_skill_id} | Lvl: {unique_skill_level} | Stars: {stars} | Score Added: {u_score} | Base Mult: {unique_mult}")
        if unique_skill_id:
            skill_scores_map[str(unique_skill_id)] = u_score
        return {"score": total_score, "rank": self.get_rank_str(total_score), "skill_scores": skill_scores_map}

        # Remove existing geckodriver.log
        if os.path.exists("geckodriver.log"):
            try:
                os.remove("geckodriver.log")
            except PermissionError:
                logger.warning("Could not delete geckodriver.log because it is already in use!")
                return

    def restart_time(self):
        self.start_time = math.floor(time.time() * 1000)

    def load_request(self, msg_path, is_json=False):
        if is_json:
            # First 4 bytes are a header
            try:
                unpacked = msgpack.unpackb(msg_path[4:])
                for key in constants.REQUEST_KEYS_TO_BE_REMOVED:
                    if key in unpacked:
                        del unpacked[key]
                return unpacked
            except Exception as e:
                logger.error(f"Error unpacking request: {e}\n{traceback.format_exc()}")
                return None
        try:
            with open(msg_path, "rb") as in_file:
                unpacked = msgpack.unpackb(in_file.read()[170:], strict_map_key=False)
                # Remove keys that are not needed
                for key in constants.REQUEST_KEYS_TO_BE_REMOVED:
                    if key in unpacked:
                        del unpacked[key]
                return unpacked
        except PermissionError:
            logger.warning("Could not load request because it is already in use!")
            time.sleep(0.1)
            return self.load_request(msg_path)
        except FileNotFoundError:
            logger.warning(f"Could not find request file: {msg_path}")
            return None

    def load_response(self, msg_path):
        try:
            with open(msg_path, "rb") as in_file:
                return msgpack.unpackb(in_file.read(), strict_map_key=False)
        except PermissionError:
            logger.warning("Could not load response because it is already in use!")
            time.sleep(0.1)
            return self.load_response(msg_path)
        except FileNotFoundError:
            logger.warning(f"Could not find response file: {msg_path}")
            return None

    def create_gametora_helper_url_from_start(self, packet_data):
        if 'start_chara' not in packet_data:
            return None
        d = packet_data['start_chara']
        supports = d['support_card_ids'] + [d['friend_support_card_info']['support_card_id']]

        return util.create_gametora_helper_url(d['card_id'], d['scenario_id'], supports, self.get_gt_language(),
                                               "en" if 'IS_UL_GLOBAL' in os.environ else "ja")

    def get_gt_language(self):
        lang = "English"
        for key, value in self.threader.settings['gametora_language'].items():
            if value:
                lang = key
                break
        return lang

    def to_json(self, packet, out_name="packet.json"):
        packets_dir = os.path.join(util.get_relative(""), "packets")
        os.makedirs(packets_dir, exist_ok=True)

        out_path = os.path.join(packets_dir, out_name)

        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(json.dumps(packet, indent=4, ensure_ascii=False))

    def to_txt(self, data, out_name=('packet.json',)):
        packets_dir = os.path.join(util.get_relative(''), 'races')
        os.makedirs(packets_dir, exist_ok=True)
        out_path = os.path.join(packets_dir, out_name)

        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(data)

    def open_helper(self):
        if self.should_stop:
            return
        self.close_browser()

        start_pos = self.threader.settings["browser_position"]
        topmost = self.threader.settings["browser_topmost"]
        if not start_pos:
            start_pos = self.get_browser_reset_position()

        self.browser = horsium.BrowserWindow(self.helper_url, self.threader, rect=start_pos,
                                             run_at_launch=setup_helper_page)
        self.set_browser_topmost(topmost)

    def get_browser_reset_position(self):
        if self.threader.windowmover.window is None:
            return None
        game_rect, _ = self.threader.windowmover.window.get_rect()
        workspace_rect = self.threader.windowmover.window.get_workspace_rect()
        left_side = abs(workspace_rect[0] - game_rect[0])
        right_side = abs(game_rect[2] - workspace_rect[2])
        if left_side > right_side:
            left_x = workspace_rect[0] - 5
            width = left_side
        else:
            left_x = game_rect[2] + 5
            width = right_side
        return [left_x, workspace_rect[1], width, workspace_rect[3] - workspace_rect[1] + 6]

    def close_browser(self):
        if self.browser and self.browser.alive():
            self.browser.close()
            self.save_last_browser_rect()
            self.browser = None
        return

    def save_rect(self, rect_var, setting):
        if rect_var:
            if rect_var['x'] <= -10666 and rect_var['y'] <= -10666:
                logger.warning(f"Browser minimized, cannot save position for {setting}: {rect_var}")
                rect_var = None
                return
            if rect_var['height'] < 0 or rect_var['width'] < 0:
                logger.warning(f"Browser size is invalid for {setting}: {rect_var}")
                rect_var = None
                return
            rect_list = [rect_var['x'], rect_var['y'], rect_var['width'], rect_var['height']]
            if self.threader.settings[setting] != rect_list:
                self.threader.settings[setting] = rect_list
            rect_var = None

    def save_last_browser_rect(self):
        self.save_rect(self.last_browser_rect, "browser_position")
        if self.threader.settings["browser_topmost"] != self.browser_topmost:
            self.threader.settings["browser_topmost"] = self.browser_topmost

    def save_skill_window_rect(self):
        if self.skill_browser:
            self.skill_browser.last_window_rect = self.last_skills_rect
        self.save_rect(self.last_skills_rect, "skills_position")

    def end_training(self):
        if self.training_tracker:
            self.training_tracker = None
        if self.skill_browser and self.skill_browser.alive():
            self.skill_browser.close()
        self.close_browser()
        return

    def add_response_to_tracker(self, data):
        should_track = self.threader.settings["track_trainings"]
        if self.previous_request:
            if should_track:
                self.training_tracker.add_request(self.previous_request)
            self.previous_request = None
        if should_track:
            self.training_tracker.add_response(data)

    EVENT_ID_TO_POS_STRING = {
        7005: 'レース勝利！',  # (1st)
        7006: 'レース入着',  # (2nd-5th)
        7007: 'レース敗北'  # (6th or worse)
    }
    EVENT_ID_TO_POS_STRING_GLB = {
        7005: 'Victory!',
        7006: 'Solid Showing',
        7007: 'Defeat'
    }

    def get_after_race_event_title(self, event_id):
        if not self.previous_race_program_id:
            return "PREVIOUS RACE UNKNOWN"

        race_grade = mdb.get_program_id_grade(self.previous_race_program_id)

        if not race_grade:
            logger.error(f"Race grade not found for program id {self.previous_race_program_id}")
            return "RACE GRADE NOT FOUND"

        # These aren't on Gametora anymore, but keep them around in case they update the page again.
        grade_text = ""
        if race_grade > 300:
            grade_text = "Pre/OP"
        elif race_grade > 100:
            grade_text = "G2/G3"
        else:
            grade_text = "G1"
        if 'IS_UL_GLOBAL' in os.environ:
            return [f"{self.EVENT_ID_TO_POS_STRING_GLB[event_id]} ({grade_text})", f"{self.EVENT_ID_TO_POS_STRING_GLB[event_id]}"]
        else:
            return [f"{self.EVENT_ID_TO_POS_STRING[event_id]} ({grade_text})"]


    def handle_response(self, message, is_json=False):
        if is_json:
            data = message
        else:
            data = self.load_response(message)

        if not data:
            return

        if self.threader.settings["save_packets"]:
            logger.debug("Response:")
            logger.debug(json.dumps(data))
            self.to_json(data, str(datetime.now()).replace(":", "-") + "_packet_in.json")

        try:
            if 'data' not in data:
                # logger.info("This packet doesn't have data :)")
                return

            data = data['data']

            if self.threader.settings['save_veteran_packets']:
                if 'trained_chara_array' in data:
                    self.to_json(data['trained_chara_array'], "veteran.json")

            if self.threader.settings['save_friend_veteran_packets']:
                if 'succession_trained_chara_data' in data and 'friend_support_card_data' in data:
                    trained_chara_array = data['succession_trained_chara_data'].get('succession_trained_chara_array', [])
                    summary_user_info_array = data['friend_support_card_data'].get('summary_user_info_array', [])

                    if summary_user_info_array:
                        # Create mapping from viewer_id to name
                        viewer_id_to_name = {user.get('viewer_id'): user.get('name') for user in summary_user_info_array}

                        # Add name to each character entry
                        for chara in trained_chara_array:
                            viewer_id = chara.get('viewer_id')
                            if viewer_id in viewer_id_to_name:
                                chara['name'] = viewer_id_to_name[viewer_id]

                    self.to_json(trained_chara_array, "friend.json")

            if self.threader.settings['save_race_schedule_packets']:
                if 'reserved_race_array' in data:
                    self.to_json(data['reserved_race_array'], "race_schedule.json")

            if self.threader.settings['save_race_packets']:
                if data.get('race_scenario') or data.get('room_info') or data.get('race_result_info'):
                    race_array = (
                            data.get('race_horse_data_array')
                            or data.get('race_start_info', {}).get('race_horse_data')
                            or data.get('race_result_info', {}).get('race_horse_data_array')
                    )
                    scenario = (
                            data.get('race_scenario')
                            or data.get('room_info', {}).get('race_scenario')
                            or data.get('race_result_info', {}).get('race_scenario')
                    )
                    if race_array and scenario:
                        content = json.dumps(race_array) + '\n' + scenario
                        filename = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_race_in.txt")
                        self.to_txt(content, filename)

            # Detect leaving the initial loading screen
            # if data.get('common_define'):
            # Game just started.

            # New loading behavior?
            if 'single_mode_load_common' in data:
                for key, value in data['single_mode_load_common'].items():
                    data[key] = value

            # Close whatever popup is open
            if self.browser and self.browser.alive():
                # Don't close event popups if the response is the choice outcomes
                if "choice_reward_array" not in data:
                    self.browser.execute_script(
                        # Janky way to get open event popups
                        """
                        document.querySelectorAll("div[id^='event-viewer-'] button[class^='sc-'][aria-expanded=true], div[class^='compatibility_result_box_'] button[class^='sc-'][aria-expanded=true]").forEach(e => { e.click()});
                        """
                    )
                gametora_close_ad_banner(self.browser)

            # Run ended
            if 'single_mode_factor_select_common' in data or 'single_mode_finish_common' in data:
                self.end_training()
                return

            # Concert Theater
            if "live_theater_save_info_array" in data:
                if self.screen_state_handler:
                    new_state = screenstate_utils.ss.ScreenState(self.screen_state_handler)
                    new_state.location = screenstate_utils.ss.Location.THEATER
                    new_state.main = "Concert Theater"
                    new_state.sub = "Vibing"

                    self.screen_state_handler.carrotjuicer_state = new_state
                return

            # Team Building
            if 'scout_ranking_state' in data:
                if data.get("own_team_info") and data['own_team_info'].get('team_score') and self.screen_state_handler:
                    team_score = data['own_team_info'].get('team_score')
                    leader_chara_id = data['own_team_info'].get('entry_chara_array', [{}])[0].get('trained_chara',
                                                                                                  {}).get('card_id')

                    if team_score and leader_chara_id:
                        logger.debug(f"Team score: {team_score}, leader chara id: {leader_chara_id}")
                        self.screen_state_handler.carrotjuicer_state = screenstate_utils.make_scouting_state(
                            self.screen_state_handler, team_score, leader_chara_id)

            # League of Heroes
            if 'heroes_id' in data:
                if data.get("own_team_info") and data['own_team_info']['team_name'] and data['own_team_info'][
                    'league_score'] and self.screen_state_handler:
                    self.screen_state_handler.carrotjuicer_state = screenstate_utils.make_league_of_heroes_state(
                        self.screen_state_handler,
                        data['own_team_info']['team_name'],
                        data['own_team_info']['league_score']
                    )
                return

            if data.get('stage1_grand_result'):
                if self.screen_state_handler and \
                        self.screen_state_handler.screen_state and \
                        self.screen_state_handler.screen_state.location == screenstate_utils.ss.Location.LEAGUE_OF_HEROES and \
                        data['stage1_grand_result'].get('after_league_score'):
                    tmp = self.screen_state_handler.screen_state
                    tmp2 = screenstate_utils.ss.ScreenState(self.screen_state_handler)
                    tmp2.location = screenstate_utils.ss.Location.LEAGUE_OF_HEROES
                    tmp2.main = tmp.main
                    tmp2.sub = screenstate_utils.get_league_of_heroes_substate(
                        data['stage1_grand_result']['after_league_score'])
                    self.screen_state_handler.carrotjuicer_state = tmp2
                    return

            # Claw Machine
            if 'collected_plushies' in data:
                if self.screen_state_handler:
                    self.screen_state_handler.carrotjuicer_state = screenstate_utils.make_claw_machine_state(data,
                                                                                                             self.threader.screenstate)

            # Race starts.
            if self.training_tracker and 'race_scenario' in data and 'race_start_info' in data and data[
                'race_scenario']:
                self.previous_race_program_id = data['race_start_info']['program_id']
                # Currently starting a race. Add packet to training tracker.
                logger.debug("Race packet received.")
                self.add_response_to_tracker(data)
                return

            # Update history
            if 'race_history' in data and data['race_history']:
                self.previous_race_program_id = data['race_history'][-1]['program_id']

            # Gametora
            # limited_shop_info check is for edge case where chara_info is present when returning to home after training
            if 'chara_info' in data and not 'limited_shop_info' in data:
                # Inside training run.

                training_id = ""
                if 'start_time' in data['chara_info']:
                    training_id = data['chara_info']['start_time']
                else:
                    # TODO: fix this!
                    logger.debug("No start_time, using strftime")
                    training_id = time.strftime("%Y-%m-%d %H:%M:%S")
                if 'IS_UL_GLOBAL' not in os.environ and (
                        not self.training_tracker or not self.training_tracker.training_id_matches(training_id)):
                    # Update cached dicts first
                    mdb.update_mdb_cache()
                    self.training_tracker = training_tracker.TrainingTracker(training_id, data['chara_info']['card_id'])

                self.skill_data = {}
                self.style = data['chara_info']['race_running_style']

                for skill_data in data['chara_info']['skill_array']:
                    skill_id = skill_data['skill_id']
                    skill_rarity = mdb.get_skill_rarity(skill_id)

                    self.skill_data[skill_id] = {
                        "is_acquired": True,
                        "hint_level": 0,
                        "rarity": skill_rarity,
                        "base_cost": self.skill_costs_dict.get(str(skill_id), 0)
                    }
                    # If it's a Gold/Upgraded skill, automatically grant the prerequisite skill(s)
                    if skill_rarity >= 2:
                        prereq_ids = mdb.get_prerequisite_skill_ids(skill_id)
                        for pid in prereq_ids:
                            self.skill_data[pid] = {
                                "is_acquired": True,
                                "hint_level": 0,
                                "rarity": mdb.get_skill_rarity(pid),
                                "base_cost": self.skill_costs_dict.get(str(pid), 0)
                            }

                inherent_skills = mdb.get_card_inherent_skills(data['chara_info']['card_id'],
                                                               data['chara_info']['talent_level'])
                for skill_id in inherent_skills:
                    if skill_id not in self.skill_data:
                        self.skill_data[skill_id] = {
                            "is_acquired": False,
                            "hint_level": 0,
                            "rarity": mdb.get_skill_rarity(skill_id),
                            "base_cost": self.skill_costs_dict.get(str(skill_id), 0)
                        }

                    if self.skill_data[skill_id]["is_acquired"]:
                        next_rank_id = skill_id - 1
                        if str(next_rank_id) in self.skill_costs_dict and next_rank_id not in self.skill_data:
                            next_rarity = mdb.get_skill_rarity(next_rank_id)

                            if next_rarity == 1:
                                self.skill_data[next_rank_id] = {
                                    "is_acquired": False,
                                    "hint_level": 0,
                                    "rarity": next_rarity,
                                    "base_cost": self.skill_costs_dict.get(str(next_rank_id), 0)
                                }

                for skill_tip in data['chara_info']['skill_tips_array']:
                    tip_rarity = skill_tip['rarity']
                    tip_level = skill_tip.get('level', 0)

                    if tip_rarity > 1:
                        skill_id = self.skill_id_dict[(skill_tip['group_id'], tip_rarity)]
                        prereq_ids = mdb.get_prerequisite_skill_ids(skill_id)

                        for pid in prereq_ids:
                            if pid not in self.skill_data:
                                self.skill_data[pid] = {
                                    "is_acquired": False,
                                    "hint_level": 0,
                                    "rarity": mdb.get_skill_rarity(pid),
                                    "base_cost": self.skill_costs_dict.get(str(pid), 0)
                                }
                    else:
                        skill_id = mdb.determine_skill_id_from_group_id(skill_tip['group_id'], tip_rarity,
                                                                        list(self.skill_data.keys()))

                    if skill_id not in self.skill_data:
                        self.skill_data[skill_id] = {
                            "is_acquired": False,
                            "hint_level": tip_level,
                            "rarity": tip_rarity,
                            "base_cost": self.skill_costs_dict.get(str(skill_id), 0)
                        }
                    else:
                        self.skill_data[skill_id]["hint_level"] = tip_level
                        self.skill_data[skill_id]["rarity"] = tip_rarity

                self.skills_list = mdb.sort_skills_by_display_order(list(self.skill_data.keys()))

                # # Fix certain skills for GameTora
                # for i in range(len(self.skills_list)):
                #     old_id = self.skills_list[i]
                #     if 900000 <= old_id < 1000000:
                #         new_id = old_id - 800000
                #         self.skills_list[i] = new_id
                #
                #         # Keep the hint dictionary synced if the ID changes
                #         if old_id in self.skill_hints:
                #             self.skill_hints[new_id] = self.skill_hints.pop(old_id)

                logger.debug(f"Skills list: {self.skills_list}")

                # Add request to tracker
                if self.training_tracker:
                    self.add_response_to_tracker(data)

                # Training info
                outfit_id = data['chara_info']['card_id']
                supports = [card_data['support_card_id'] for card_data in data['chara_info']['support_card_array']]
                scenario_id = data['chara_info']['scenario_id']

                # Training stats
                if self.screen_state_handler:
                    if data.get('race_start_info', None):
                        self.screen_state_handler.carrotjuicer_state = screenstate_utils.make_training_race_state(data,
                                                                                                                  self.threader.screenstate)
                    else:
                        self.screen_state_handler.carrotjuicer_state = screenstate_utils.make_training_state(data,
                                                                                                             self.threader.screenstate)

                if not self.browser or not self.browser.current_url().startswith(self.browser.url.split("?", 1)[0]):
                    logger.info("GT tab not open, opening tab")
                    self.helper_url = util.create_gametora_helper_url(outfit_id, scenario_id, supports,
                                                                      self.get_gt_language(),
                                                                      "en" if 'IS_UL_GLOBAL' in os.environ else "ja")
                    logger.debug(f"Helper URL: {self.helper_url}")
                    self.open_helper()

                self.update_helper_table(data)

            if 'unchecked_event_array' in data and data['unchecked_event_array']:
                # Training event.
                logger.debug("Training event detected")
                event_data = data['unchecked_event_array'][0]
                event_titles = mdb.get_event_titles(event_data['story_id'], data['chara_info']['card_id'])
                logger.debug(f"Event titles: {event_titles}")

                if len(event_data['event_contents_info']['choice_array']) > 1:
                    # Event has choices

                    # If character is the trained character
                    if event_data['event_contents_info']['support_card_id'] and event_data['event_contents_info'][
                        'support_card_id'] not in supports:
                        # Random support card event
                        logger.info("Random support card detected")

                        self.browser.execute_script("""document.getElementById("boxSupportExtra").click();""")
                        self.browser.execute_script(
                            """
                                var cont = document.querySelector('[class^="filters_hide"]').parentElement.parentElement.parentElement;
                                var rSupportsCheckbox = cont.querySelector('[id*="ShowR"]');
                                var showUpcomingSupportsCheckbox = cont.querySelector('[id*="showUpcoming"]');
                                var onlyOwned = cont.querySelector('[id*="onlyOwned"]');
                                var filtersDiv = document.querySelector('[class^="filters_hide"]').parentElement.parentElement;
                                if( filtersDiv ) 
                                {
                                    for( var i = 0; i < filtersDiv.children.length; i++ )
                                    {
                                        var filter = filtersDiv.children[i]?.querySelector('label');
                                        if( filter && filter.className.includes("_active") )
                                        {
                                            filter.click();
                                        }
                                    }
                                }
                                if( rSupportsCheckbox && !rSupportsCheckbox.checked ) {
                                    rSupportsCheckbox.click(); 
                                }
                                if( showUpcomingSupportsCheckbox && !showUpcomingSupportsCheckbox.checked ) {
                                    showUpcomingSupportsCheckbox.click(); 
                                }
                                if( onlyOwned && onlyOwned.checked ) {
                                    onlyOwned.click(); 
                                }

                                var ele = document.getElementById(arguments[0].toString());

                                if (ele) {
                                    ele.click();
                                    return;
                                }
                                cont.parentElement.parentElement.querySelector('img[src="/images/ui/close.png"]').click();
                            """,
                            event_data['event_contents_info']['support_card_id'])
                    else:
                        logger.debug("Trained character or support card detected")


                    # Check for after-race event.
                    if event_data['event_id'] in (7005, 7006, 7007):
                        logger.debug("After-race event detected.")
                        event_titles = self.get_after_race_event_title(event_data['event_id'])

                    # Activate and scroll to the outcome.
                    event_element = self.determine_event_element(event_titles)

                    if not event_element:
                        logger.info(f"Could not find event on GT page: {event_data['story_id']} - {event_data['event_id']} : {event_titles}")
                    self.browser.execute_script("""
                        if (arguments[0]) {
                            arguments[0].click();
                            window.scrollBy({top: arguments[0].getBoundingClientRect().bottom - window.innerHeight + 32, left: 0, behavior: 'smooth'});
                        }
                        """,
                                                event_element
                                                )

                    # Check to see if you already have the status.
                    status_ids = data['chara_info']['chara_effect_id_array']
                    if status_ids:
                        self.browser.execute_script("""
                        if(arguments[0])
                        {
                            arguments[0].parentElement.querySelectorAll('div[data-tippy-root] span[class^="utils_linkcolor"]')
                                .forEach(el => {
                                    if (arguments[1].includes(el.textContent.trim())) {
                                        el.style.color = 'gray';
                                    }
                                });
                        } 
                        """, event_element, [self.status_name_dict[i] for i in status_ids if
                                             i in self.status_name_dict])

            if 'chara_info' not in data and self.last_helper_data:
                if 'IS_UL_GLOBAL' in os.environ:
                    if 'reserved_race_array' in data:
                        # User changed reserved races
                        self.last_helper_data['reserved_race_array'] = data['reserved_race_array']
                        data = self.last_helper_data
                        self.update_helper_table(data)
                else:
                    if 'reserved_race_info' in data and 'reserved_race_array' in data['reserved_race_info']:
                        # User changed reserved races
                        self.last_helper_data['reserved_race_array'] = data['reserved_race_info']['reserved_race_array']
                        data = self.last_helper_data
                        self.update_helper_table(data)

            self.last_data = data
        except Exception:
            logger.error("ERROR IN HANDLING RESPONSE MSGPACK")
            logger.error(data)
            exception_string = traceback.format_exc()
            logger.error(exception_string)
            util.show_error_box("Uma Launcher: Error in response msgpack.",
                                f"This should not happen. You may contact the developer about this issue.")
            # self.close_browser()

    def start_concert(self, music_id):
        logger.debug("Starting concert")
        self.screen_state_handler.carrotjuicer_state = screenstate_utils.make_concert_state(music_id,
                                                                                            self.threader.screenstate)
        return

    def handle_request(self, message, is_json=False):
        data = self.load_request(message, is_json=is_json)

        if not data:
            return

        if self.threader.settings["save_packets"]:
            logger.debug("Request:")
            logger.debug(json.dumps(data))
            self.to_json(data, str(datetime.now()).replace(":", "-") + "_packet_out.json")

        self.previous_request = data

        try:
            if 'attestation_type' in data:
                mdb.update_mdb_cache()

            if 'single_mode_finish_request_common' in data:
                if 'is_force_delete' in data['single_mode_finish_request_common']:
                    self.end_training()
                    return
            if 'is_force_delete' in data:
                # Packet is a request to delete a training
                self.end_training()
                return

            # Watching a concert
            if "live_theater_save_info" in data:
                self.start_concert(data['live_theater_save_info']['music_id'])
                return

            if "music_id" in data:
                self.start_concert(data['music_id'])
                return

            if 'start_chara' in data:
                # Packet is a request to start a training
                logger.debug("Start of training detected")
                if 'exec_count' in data['start_chara']:
                    logger.debug("Auto-training detected, not starting training")
                    return
                self.helper_url = self.create_gametora_helper_url_from_start(data)
                logger.debug(f"Helper URL: {self.helper_url}")
                self.open_helper()
                return

        except Exception:
            logger.error("ERROR IN HANDLING REQUEST MSGPACK")
            logger.error(data)
            exception_string = traceback.format_exc()
            logger.error(exception_string)
            util.show_error_box("Uma Launcher: Error in request msgpack.",
                                f"This should not happen. You may contact the developer about this issue.")
            # self.close_browser()

    def remove_message(self, message_path):
        if message_path in self.skipped_msgpacks:
            return

        tries = 0
        last_exception = None
        while tries < 5:
            try:
                if os.path.exists(message_path):
                    os.remove(message_path)
                    return
                else:
                    logger.warning(f"Attempted to delete non-existent msgpack file: {message_path}. Skipped.")
                    return
            except Exception as e:
                last_exception = e
                tries += 1
                time.sleep(1)

        logger.warning(f"Failed to remove msgpack file: {message_path}.")
        logger.warning(''.join(traceback.format_tb(last_exception.__traceback__)))
        self.skipped_msgpacks.append(message_path)

    def process_message(self, message: str):
        if message in self.skipped_msgpacks:
            return

        try:
            message_time = int(str(os.path.basename(message))[:-9])
        except ValueError:
            return
        if message_time < self.start_time:
            # Delete old msgpack files.
            self.remove_message(message)
            return

        # logger.info(f"New Packet: {os.path.basename(message)}")

        if message.endswith("R.msgpack"):
            # Response
            self.handle_response(message)

        else:
            # Request
            self.handle_request(message)

        self.remove_message(message)
        return

    def get_msgpack_batch(self, msg_path):
        return sorted(glob.glob(os.path.join(msg_path, "*.msgpack")), key=os.path.getmtime)

    def update_helper_table(self, data):
        helper_table = self.helper_table.create_helper_elements(data, self.last_helper_data)
        self.last_helper_data = data
        if helper_table:
            self.browser.execute_script("""
                window.UL_DATA.overlay_html = arguments[0];
                window.update_overlay();
                """,
                                        helper_table)

    def update_skill_window(self):
        if self.should_stop:
            return

        if not self.last_data:
            return

        if not self.skill_browser:
            self.skill_browser = horsium.BrowserWindow(
                "https://gametora.com/umamusume/skills",
                self.threader,
                rect=self.threader.settings['skills_position'],
                run_at_launch=setup_skill_window
            )
        else:
            self.skill_browser.ensure_tab_open()

        if self.browser and self.browser.alive():
            self.browser.execute_script("""window.skill_window_opened();""")

        mode_pref = self.skill_browser.execute_script("return window.localStorage.getItem('UL_MODE_PREF') || 'parent';")
        is_ace_mode = (mode_pref == 'ace')
        is_rating_mode = (mode_pref == 'rating')

        acquired_skills_list = [sid for sid, data in self.skill_data.items() if data["is_acquired"]]
        unacquired_skills_list = [sid for sid, data in self.skill_data.items() if not data["is_acquired"]]

        STYLE_INTERNAL_MAP = {
            1: "NIGE",
            2: "SEN",
            3: "SASI",
            4: "OI"
        }

        total_iterations = 2000

        if is_ace_mode:
            u_speed = self.last_data['chara_info'].get('speed', 0)
            u_stamina = self.last_data['chara_info'].get('stamina', 0)
            u_power = self.last_data['chara_info'].get('power', 0)
            u_guts = self.last_data['chara_info'].get('guts', 0)
            u_wisdom = self.last_data['chara_info'].get('wiz', 0)
        else:
            u_speed = 1200
            u_stamina = 2000
            u_power = 1100
            u_guts = 1100
            u_wisdom = 1100

        mock_payload = {
            "baseSetting": {
                "umaStatus": {
                    "charaName": "Place Holder",
                    "speed": u_speed, "stamina": u_stamina, "power": u_power, "guts": u_guts, "wisdom": u_wisdom,
                    "condition": "BEST", "style": STYLE_INTERNAL_MAP[self.style],
                    "distanceFit": "A", "surfaceFit": "A", "styleFit": "A",
                    "popularity": 1, "gateNumber": 0,
                },
                "track": {
                    "location": 10009, "course": 10914, "condition": "BAD", "gateCount": 9
                }
            },
            "acquiredSkillIds": acquired_skills_list,
            "unacquiredSkillIds": unacquired_skills_list,
            "iterations": total_iterations
        }

        if is_rating_mode:
            results = {"candidates": {}}
        else:
            results = self.run_simulation(util.get_asset("_assets/umasim-cli.exe"), mock_payload)

        discount_map = {0: 0, 1: 10, 2: 20, 3: 30, 4: 35, 5: 40}
        rating_calc = self.calculate_uma_rank_score(self.last_data['chara_info'], self.skill_data)
        rating_scores = rating_calc.get("skill_scores", {})
        uma_score = rating_calc.get("score", 0)
        uma_rank = rating_calc.get("rank", "")
        rating_data = {}
        for skill_id in self.skills_list:
            skill_id_int = int(skill_id)
            skill_id_str = str(skill_id)
            score = rating_scores.get(skill_id_str, 0)
            skill_info = self.skill_data.get(skill_id_int, {})
            skill_rarity = skill_info.get("rarity", 1)
            # Calculate Delta Score from highest acquired prerequisite
            prereq_ids = mdb.get_prerequisite_skill_ids(skill_id_int)
            highest_acquired_score = 0
            for pid in reversed(prereq_ids):
                pinfo = self.skill_data.get(pid, {})
                if pinfo.get("is_acquired", False):
                    highest_acquired_score = rating_scores.get(str(pid), 0)
                    break
            score -= highest_acquired_score

            # Calculate total SP cost including all unacquired prerequisites
            base_cost = skill_info.get("base_cost", 0)
            hint_level = skill_info.get("hint_level", 0)
            effective_hint_level = min(hint_level, 5)
            discount_percent = discount_map.get(effective_hint_level, 0)
            total_sp_cost = int(base_cost * (100 - discount_percent) / 100)

            for pid in prereq_ids:
                pinfo = self.skill_data.get(pid, {})
                if not pinfo.get("is_acquired", False):
                    p_base_cost = pinfo.get("base_cost", 0)
                    p_hint_level = pinfo.get("hint_level", 0)
                    p_discount_percent = discount_map.get(min(p_hint_level, 5), 0)
                    p_sp_cost = int(p_base_cost * (100 - p_discount_percent) / 100)
                    total_sp_cost += p_sp_cost

            eff = (score / total_sp_cost) if total_sp_cost > 0 else 0
            rating_data[skill_id_str] = {
                "score": score,
                "sp_cost": total_sp_cost,
                "efficiency": round(eff, 3),
                "hint_level": hint_level
            }

        available_sp = self.last_data['chara_info'].get('skill_point', 0)
        
        skill_ids = [int(sid) for sid in rating_data.keys()]
        id_to_group = mdb.get_group_id_dict(skill_ids)

        groups = {}
        for sid_str, detail in rating_data.items():
            cost = detail.get('sp_cost', 0)
            gain = detail.get('score', 0)
            if cost > 0 and gain > 0:
                gid = id_to_group.get(sid_str, sid_str)
                groups.setdefault(gid, []).append((cost, gain))
        
        dp = [0] * (available_sp + 1)
        for gid, items_in_group in groups.items():
            new_dp = list(dp)
            for cost, gain in items_in_group:
                for j in range(available_sp, cost - 1, -1):
                    new_dp[j] = max(new_dp[j], dp[j - cost] + gain)
            dp = new_dp
                
        max_score_gain = dp[available_sp] if available_sp >= 0 else 0
        projected_score = uma_score + max_score_gain
        projected_rank = self.get_rank_str(projected_score)
        
        uma_next = self.get_next_rank_req(uma_score)
        proj_next = self.get_next_rank_req(projected_score)

        sim_summary = {}

        # Dual Scales
        global_hist_min = 0.0
        global_hist_max = 0.0
        global_box_min = float('inf')
        global_box_max = float('-inf')
        base_median_abs = 0.0

        if results and "baselineStats" in results and "candidates" in results:
            # Fetch Baseline Stats to anchor the boxplot scale (Using MEDIAN)
            base_stats = results.get("baselineStats", {})
            base_median_abs = base_stats.get("median", 0.0)

            # Ensure the baseline min/max/outliers are included in the global boxplot scale
            b_min_arr = [base_stats.get("min", base_median_abs), base_median_abs] + base_stats.get("outliers", [])
            b_max_arr = [base_stats.get("max", base_median_abs), base_median_abs] + base_stats.get("outliers", [])
            global_box_min = min(b_min_arr)
            global_box_max = max(b_max_arr)

            for skill_id_str, candidate_data in results["candidates"].items():
                if not candidate_data:
                    continue

                skill_id_int = int(skill_id_str)

                time_saved_stats = candidate_data.get("timeSavedStats", {})
                race_time_stats = candidate_data.get("raceTimeStats", {})
                eff_rate = candidate_data.get("effectiveRate", 0.0)
                conn_rate = candidate_data.get("connectionRate", 0.0)
                conn_time = candidate_data.get("avgConnectionTime", 0.0)

                if not time_saved_stats or not race_time_stats:
                    continue

                skill_info = self.skill_data.get(skill_id_int, {})
                base_cost = skill_info.get("base_cost", 0)
                hint_level = skill_info.get("hint_level", 0)
                skill_rarity = skill_info.get("rarity", 1)

                effective_hint_level = min(hint_level, 5)
                discount_percent = discount_map.get(effective_hint_level, 0)
                total_sp_cost = int(base_cost * (100 - discount_percent) / 100)

                if skill_rarity == 2:
                    white_skill_id = skill_id_int + 1
                    white_skill_info = self.skill_data.get(white_skill_id, {})

                    # If the white skill exists in our dictionary AND is not acquired yet
                    if white_skill_info and not white_skill_info.get("is_acquired", False):
                        white_base_cost = white_skill_info.get("base_cost", 0)
                        white_hint_level = white_skill_info.get("hint_level", 0)

                        white_discount_percent = discount_map.get(min(white_hint_level, 5), 0)
                        white_sp_cost = int(white_base_cost * (100 - white_discount_percent) / 100)

                        total_sp_cost += white_sp_cost

                # Absolute Boxplot Stats
                k_min_val = race_time_stats.get("min", 0.0)
                k_max_val = race_time_stats.get("max", 0.0)

                # Extract explicitly calculated whiskers from Kotlin for drawing the lines
                k_wMin = race_time_stats.get("whiskerMin", k_min_val)
                k_wMax = race_time_stats.get("whiskerMax", k_max_val)

                k_q1 = race_time_stats.get("q1", 0.0)
                k_median = race_time_stats.get("median", 0.0)
                k_q3 = race_time_stats.get("q3", 0.0)
                k_outliers = [round(x, 3) for x in race_time_stats.get("outliers", [])]

                # Histogram Stats (Negative means faster)
                saved_mean_display = time_saved_stats.get("mean", 0.0)

                # Efficiency calculation: Seconds Saved per 100 SP
                efficiency = (-saved_mean_display / max(total_sp_cost, 1)) * 100

                data_obj = {
                    "saved": round(saved_mean_display, 4),
                    "mean": saved_mean_display,
                    "binMin": time_saved_stats.get("binMin", 0.0),
                    "binWidth": time_saved_stats.get("binWidth", 1.0),
                    "frequencies": time_saved_stats.get("frequencies", []),
                    "maxFreq": max(time_saved_stats.get("frequencies", [0])) if time_saved_stats.get(
                        "frequencies") else 1,
                    "vMax": total_iterations,
                    "wMin": round(k_wMin, 4),
                    "q1": round(k_q1, 4),
                    "median": round(k_median, 4),
                    "q3": round(k_q3, 4),
                    "wMax": round(k_wMax, 4),
                    "outliers": k_outliers,
                    "sp_cost": total_sp_cost,
                    "hint_level": hint_level,
                    "efficiency": round(efficiency, 4),
                    "eff_rate": int(round(eff_rate * 100)),
                    "conn_rate": int(round(conn_rate * 100)),
                    "conn_time": conn_time
                }

                # Update Histogram Scale Bounds
                bin_max = data_obj["binMin"] + (len(data_obj["frequencies"]) * data_obj["binWidth"]) if data_obj[
                    "frequencies"] else 0
                global_hist_min = min([global_hist_min, data_obj["binMin"], saved_mean_display, 0.0])
                global_hist_max = max([global_hist_max, bin_max, saved_mean_display, 0.0])

                # Update Boxplot Scale Bounds (using absolute min/max to keep outliers on screen)
                global_box_min = min([global_box_min, k_min_val] + k_outliers)
                global_box_max = max([global_box_max, k_max_val] + k_outliers)

                sim_summary[skill_id_str] = data_obj

        all_sk_data = {}
        effects_dict = mdb.get_skill_effects_dict()
        for skill_id in self.skills_list:
            sk_id_str = str(skill_id)
            cond_old = self.skill_conditions_dict.get(int(skill_id), "") or self.skill_conditions_dict.get(sk_id_str, "")
            eff_info = effects_dict.get(sk_id_str, {})
            eff_str = eff_info.get("effects", "")
            cond_new = eff_info.get("conditions", "")
            if not cond_new:
                cond_new = cond_old
                
            all_sk_data[sk_id_str] = {
                "effects": eff_str,
                "conditions": cond_new
            }

        if global_box_min == float('inf'): global_box_min = 0.0
        if global_box_max == float('-inf'): global_box_max = 0.0

        self.skill_browser.execute_script(
            """
            let skills_list = arguments[0];
            let sim_results = arguments[1] || {};
            let globalHistMin = arguments[2];
            let globalHistMax = arguments[3];
            let globalBoxMin = arguments[4];
            let globalBoxMax = arguments[5];
            let acquired_list = arguments[6] || [];
            let all_sk_data = arguments[7] || {};

            let baseMedianAbs = arguments[8] || 0.0;
            let rating_data = arguments[9] || {};
            let uma_score = arguments[10] || 0;
            let uma_rank = arguments[11] || "";
            let proj_score = arguments[12] || 0;
            let proj_rank = arguments[13] || "";
            let uma_next = arguments[14] || 0;
            let proj_next = arguments[15] || 0;
    
            function formatCondition(cond) {
                if (!cond) return "";
                return cond.replace(/([a-zA-Z_]+)|(==|>=|<=|!=|>|<|=|&|!)|(\d+(?:\.\d+)?s?)/g, function(match, word, op, num) {
                    if (word) {
                        if (word === 'OR') return `<span style="color: #d65d8a;">${word}</span>`;
                        return `<span style="color: #c084fc;">${word}</span>`;
                    }
                    if (op) return `<span style="color: #60a5fa;">${op}</span>`;
                    if (num) return `<span style="color: #73c991;">${num}</span>`;
                    return match;
                });
            }
    
            let hRange = globalHistMax - globalHistMin;
            if (hRange === 0) hRange = 1;
            let hPad = hRange * 0.05; 
            let hScaleMin = globalHistMin - hPad;
            let hScaleMax = globalHistMax + hPad;
            let hScaleRange = hScaleMax - hScaleMin;
            let getHistPct = (val) => Math.max(0, Math.min(100, ((val - hScaleMin) / hScaleRange) * 100));
            let hZeroPct = getHistPct(0); 
    
            let bRange = globalBoxMax - globalBoxMin;
            if (bRange === 0) bRange = 1;
            let bPad = bRange * 0.05;
            let bScaleMin = globalBoxMin - bPad;
            let bScaleMax = globalBoxMax + bPad;
            let bScaleRange = bScaleMax - bScaleMin;
            let getBoxPct = (val) => Math.max(0, Math.min(100, ((val - bScaleMin) / bScaleRange) * 100));
            let baseMedianPct = getBoxPct(baseMedianAbs);
    
            window.UL_BADGE_PREF = localStorage.getItem('UL_BADGE_PREF') || 'hist';
            window.UL_MODE_PREF = localStorage.getItem('UL_MODE_PREF') || 'parent';
    
            let pageTitle = document.querySelector("h1");
            if (pageTitle && !document.getElementById("ul-badge-toggle")) {
            
                let tooltipStyle = document.createElement("style");
                tooltipStyle.id = "ul-custom-tooltips";
                tooltipStyle.innerHTML = `
                    .ul-tooltip { position: relative; cursor: help; width: 100%; display: block; }
                    .ul-tooltip-row-text { 
                        overflow: hidden; text-overflow: ellipsis; white-space: nowrap; 
                        width: 100%; display: block; 
                    }
                    .ul-tooltip-content {
                        position: absolute; bottom: 100%; left: 0;
                        background: rgba(17, 24, 39, 0.95); color: #e5e7eb; padding: 8px 12px; border-radius: 6px;
                        font-size: 13px; white-space: pre-wrap; z-index: 9999;
                        opacity: 0; visibility: hidden; pointer-events: none;
                        transition: none; width: max-content; max-width: 100%;
                        text-shadow: none; font-weight: normal; font-family: monospace;
                        text-align: left; border: 1px solid #4b5563;
                        line-height: 1.4;
                        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
                        box-sizing: border-box;
                    }
                    .ul-tooltip:hover .ul-tooltip-content { opacity: 1; visibility: visible; }
                    /* Dont truncate tooltip content inside */
                    .ul-tooltip-content * { white-space: pre-wrap !important; }
                    
                    /* Utility for elements at the right edge of the screen */
                    .ul-tooltip-right .ul-tooltip-content { right: 0; left: auto; }
                    
                    /* Layout Fix: Force full width by removing sidebar padding */
                    div[class*='Layout_content_'], div[class*='Layout_container_'], main { 
                        padding-right: 0 !important; 
                        padding-left: 0 !important; 
                        max-width: 100% !important; 
                        width: 100% !important;
                    }
                `;
                document.head.appendChild(tooltipStyle);
                
                let toggleDiv = document.createElement("div");
                toggleDiv.id = "ul-badge-toggle";
                toggleDiv.style.display = "inline-flex";
                toggleDiv.style.marginLeft = "15px";
                toggleDiv.style.fontSize = "0.5em";
                toggleDiv.style.verticalAlign = "middle";
    
                let btnHist = document.createElement("button");
                btnHist.innerText = "Time Saved";
                btnHist.style.padding = "4px 8px";
                btnHist.style.borderRadius = "4px 0 0 4px";
                btnHist.style.border = "1px solid var(--c-topnav)";
                btnHist.style.cursor = "pointer";
    
                let btnBox = document.createElement("button");
                btnBox.innerText = "Race Time";
                btnBox.style.padding = "4px 8px";
                btnBox.style.borderRadius = "0 4px 4px 0";
                btnBox.style.border = "1px solid var(--c-topnav)";
                btnBox.style.cursor = "pointer";
    
                let modeToggleDiv = document.createElement("div");
                modeToggleDiv.id = "ul-mode-toggle";
                modeToggleDiv.style.display = "inline-flex";
                modeToggleDiv.style.marginLeft = "10px";
                modeToggleDiv.style.fontSize = "0.5em";
                modeToggleDiv.style.verticalAlign = "middle";
    
                let btnParent = document.createElement("button");
                btnParent.innerText = "Parent";
                btnParent.style.padding = "4px 8px";
                btnParent.style.borderRadius = "4px 0 0 4px";
                btnParent.style.border = "1px solid #c084fc";
                btnParent.style.cursor = "pointer";
    
                let btnAce = document.createElement("button");
                btnAce.innerText = "Ace";
                btnAce.style.padding = "4px 8px";
                btnAce.style.borderRadius = "0";
                btnAce.style.border = "1px solid #c084fc";
                btnAce.style.borderLeft = "none";
                btnAce.style.cursor = "pointer";

                let btnRating = document.createElement("button");
                btnRating.innerText = "Rating";
                btnRating.style.padding = "4px 8px";
                btnRating.style.borderRadius = "0 4px 4px 0";
                btnRating.style.border = "1px solid #c084fc";
                btnRating.style.borderLeft = "none";
                btnRating.style.cursor = "pointer";
    
                window.updateToggleColors = () => {
                    if (window.UL_BADGE_PREF === 'hist') {
                        btnHist.style.background = "var(--c-topnav)";
                        btnHist.style.color = "white";
                        btnBox.style.background = "transparent";
                        btnBox.style.color = "var(--c-text)";
                    } else {
                        btnBox.style.background = "var(--c-topnav)";
                        btnBox.style.color = "white";
                        btnHist.style.background = "transparent";
                        btnHist.style.color = "var(--c-text)";
                    }
    
                    if (window.UL_MODE_PREF === 'ace') {
                        btnAce.style.background = "#c084fc";
                        btnAce.style.color = "white";
                        btnParent.style.background = "transparent";
                        btnParent.style.color = "var(--c-text)";
                        btnRating.style.background = "transparent";
                        btnRating.style.color = "var(--c-text)";
                        document.querySelectorAll(".ul-badge-rating").forEach(el => el.style.display = "none");
                        if (window.UL_BADGE_PREF === 'hist') {
                            document.querySelectorAll(".ul-badge-hist").forEach(el => el.style.display = "block");
                            document.querySelectorAll(".ul-badge-box").forEach(el => el.style.display = "none");
                        } else {
                            document.querySelectorAll(".ul-badge-hist").forEach(el => el.style.display = "none");
                            document.querySelectorAll(".ul-badge-box").forEach(el => el.style.display = "flex");
                        }
                    } else if (window.UL_MODE_PREF === 'rating') {
                        btnRating.style.background = "#c084fc";
                        btnRating.style.color = "white";
                        btnParent.style.background = "transparent";
                        btnParent.style.color = "var(--c-text)";
                        btnAce.style.background = "transparent";
                        btnAce.style.color = "var(--c-text)";
                        document.querySelectorAll(".ul-badge-rating").forEach(el => el.style.display = "flex");
                        document.querySelectorAll(".ul-badge-hist").forEach(el => el.style.display = "none");
                        document.querySelectorAll(".ul-badge-box").forEach(el => el.style.display = "none");
                    } else {
                        btnParent.style.background = "#c084fc";
                        btnParent.style.color = "white";
                        btnAce.style.background = "transparent";
                        btnAce.style.color = "var(--c-text)";
                        btnRating.style.background = "transparent";
                        btnRating.style.color = "var(--c-text)";
                        document.querySelectorAll(".ul-badge-rating").forEach(el => el.style.display = "none");
                        if (window.UL_BADGE_PREF === 'hist') {
                            document.querySelectorAll(".ul-badge-hist").forEach(el => el.style.display = "block");
                            document.querySelectorAll(".ul-badge-box").forEach(el => el.style.display = "none");
                        } else {
                            document.querySelectorAll(".ul-badge-hist").forEach(el => el.style.display = "none");
                            document.querySelectorAll(".ul-badge-box").forEach(el => el.style.display = "flex");
                        }
                    }
                };
    
                btnHist.onclick = () => {
                    window.UL_BADGE_PREF = 'hist';
                    localStorage.setItem('UL_BADGE_PREF', 'hist');
                    window.updateToggleColors();
                };
    
                btnBox.onclick = () => {
                    window.UL_BADGE_PREF = 'box';
                    localStorage.setItem('UL_BADGE_PREF', 'box');
                    window.updateToggleColors();
                };
    
                btnParent.onclick = () => {
                    window.UL_MODE_PREF = 'parent';
                    localStorage.setItem('UL_MODE_PREF', 'parent');
                    window.updateToggleColors();
                };
    
                btnAce.onclick = () => {
                    window.UL_MODE_PREF = 'ace';
                    localStorage.setItem('UL_MODE_PREF', 'ace');
                    window.updateToggleColors();
                };

                btnRating.onclick = () => {
                    window.UL_MODE_PREF = 'rating';
                    localStorage.setItem('UL_MODE_PREF', 'rating');
                    window.updateToggleColors();
                };
    
                window.updateToggleColors();
                
                let rankDisp = document.createElement("div");
                rankDisp.id = "ul-rank-display";
                rankDisp.style.marginLeft = "20px";
                rankDisp.style.marginRight = "auto";
                rankDisp.style.fontSize = "1em";
                rankDisp.style.fontWeight = "bold";
                rankDisp.style.color = "#fcd34d";
                rankDisp.style.textAlign = "left";
                rankDisp.style.lineHeight = "0.9";
                rankDisp.style.whiteSpace = "normal";
                rankDisp.style.textShadow = "1px 1px 2px black, -1px -1px 2px black";

                toggleDiv.appendChild(btnHist);
                toggleDiv.appendChild(btnBox);
                modeToggleDiv.appendChild(btnParent);
                modeToggleDiv.appendChild(btnAce);
                modeToggleDiv.appendChild(btnRating);
                
                pageTitle.appendChild(toggleDiv);
                pageTitle.appendChild(modeToggleDiv);
                pageTitle.appendChild(rankDisp);
                pageTitle.style.display = "flex";
                pageTitle.style.alignItems = "center";
                pageTitle.style.width = "100%";
    
            } else if (window.updateToggleColors) {
                window.updateToggleColors();
            }
            
            let rankDispExists = document.getElementById("ul-rank-display");
            if (rankDispExists) {
                if (proj_score > uma_score) {
                    rankDispExists.innerHTML = `<span style="font-size:0.5em;">Rating: ${uma_rank} ${uma_score}<span style="font-weight:normal;color:#d1d5db;margin-left:4px;font-size:0.85em;"> +${uma_next}</span></span> <span style="color:#a8a29e;font-size:0.5em;margin-left:8px;">Max: ${proj_rank}<span style="font-weight:normal;margin-left:4px;font-size:0.85em;"> +${proj_next}</span></span>`;
                } else {
                    rankDispExists.innerHTML = `<span style="font-size:0.5em;">Rating: ${uma_rank} ${uma_score}<span style="font-weight:normal;color:#d1d5db;margin-left:4px;font-size:0.85em;"> +${uma_next}</span></span>`;
                }
            }
    
            let skill_elements = [];
            let skills_table = document.querySelector("[class^='skills_skill_table_']");
            let skill_rows = document.querySelectorAll("[class^='skills_table_desc_']");
            let stripes_element = document.querySelector("[class*='skills_stripes_']");
            let color_class = stripes_element ? [...stripes_element.classList].filter(item => item.startsWith("skills_stripes_"))[0] : null;
    
            if (!skills_table || skill_rows.length === 0) return;
    
            for (const item of skill_rows) {
                if (item.parentNode) item.parentNode.style.display = "none";
            }
    
            for (const skill_id of skills_list) {
                let display_id = skill_id;
                if (display_id >= 900000 && display_id < 1000000) {
                    display_id = display_id - 800000;
                }
                
                let display_string = "(" + display_id + ")";
                let true_skill_string = "(" + skill_id + ")";
    
                for (const item of skill_rows) {
                    if (item.textContent.includes(display_string) || item.textContent.includes(true_skill_string)) {
                        let row = item.parentNode;
                        skill_elements.push(row);
                        row.remove();
    
                        let existingBadge = row.querySelector('.sim-data-badge');
                        if (existingBadge) existingBadge.remove();
    
                        let badge = document.createElement("div");
                        badge.className = "sim-data-badge";
                        badge.style.gridArea = "badge";
                        badge.style.width = "155px"; 
                        badge.style.height = "40px";
                        badge.style.marginRight = "10px"; 
                        badge.style.boxSizing = "border-box";
                        badge.style.display = "block";
    
                        let skData = all_sk_data[skill_id.toString()] || {};
                        let effRaw = skData.effects || "";
                        let condRaw = skData.conditions || "";
                        
                        let displayVal = effRaw ? effRaw : condRaw;
                        
                        let condHtml = `<div class="ul-tooltip"><div class="ul-tooltip-row-text" style="color: #d1d5db; font-family: monospace; line-height: 1.2;">${formatCondition(displayVal)} <span style="color: #6b7280; font-size: 0.85em;">(${skill_id})</span></div><div class="ul-tooltip-content">${formatCondition(condRaw)}</div></div>`;
    
                        if (row.children.length >= 3) {
                            let descCell = row.children[2];
                            if (descCell) {
                                descCell.innerHTML = condHtml;
                            }
                        }
    
                        if (row.children.length >= 4) {
                            row.children[3].style.display = "none";
                        }
    
                        if (acquired_list.includes(skill_id)) {
                            badge.style.padding = "2px 8px";
                            badge.style.backgroundColor = "rgba(156, 163, 175, 0.1)"; 
                            badge.style.border = "1px solid #9ca3af";
                            badge.style.color = "#9ca3af";
                            badge.style.borderRadius = "4px";
                            badge.style.fontWeight = "bold";
                            badge.style.display = "flex";
                            badge.style.alignItems = "center";
                            badge.style.justifyContent = "center";
                            badge.innerHTML = `Acquired`;
                        } else {
                            let rdata = rating_data[skill_id.toString()];
                            let ratingHtml = '';
                            if (rdata) {
                                let eff = rdata.efficiency || 0;
                                let t = Math.max(0, Math.min(1, (eff - 1) / 1));
                                let saturation = 30 + t * 70;
                                let lightness = 85 - t * 45;
                                let bgColor = `hsla(45, ${saturation}%, ${lightness}%, ${0.08 + t * 0.17})`;
                                let borderColor = `hsl(45, ${saturation}%, ${Math.max(lightness - 20, 30)}%)`;
                                let rColor = eff >= 2.5 ? "#ffbe28" : (eff >= 2 ? "#fcd34d" : (eff >= 1 ? "#fff59d" : "#94a3b8"));
                                let ratingDisplay = window.UL_MODE_PREF === 'rating' ? 'flex' : 'none';
                                ratingHtml = `
                                    <div class="ul-badge-rating" style="display: ${ratingDisplay}; position: relative; width: 100%; height: 100%; background: #313131; border: 1px solid ${borderColor}; border-radius: 4px; box-sizing: border-box; overflow: hidden; align-items: center; justify-content: space-between; padding: 0 8px;">
                                        <div style="display: flex; flex-direction: column; justify-content: center; z-index: 4; text-shadow: 1px 1px 2px black, -1px -1px 2px black; text-align: left;">
                                            <div style="font-size: 0.7em; color: rgba(255,255,255,0.9);">Lv ${rdata.hint_level || 0} | ${rdata.sp_cost || "?"} SP</div>
                                            <div style="font-size: 0.65em; color: ${rColor}; white-space: nowrap;">${rdata.efficiency.toFixed(2)} Pt/SP</div>
                                        </div>
                                        <div style="font-size: 1.1em; font-weight: bold; color: ${rColor}; z-index: 4; text-shadow: 1px 1px 2px black, -1px -1px 2px black; text-align: right; margin-top: 2px;">
                                            ${rdata.score} Pt
                                        </div>
                                    </div>
                                `;
                            }

                            let data = sim_results[skill_id.toString()]; 
                            if (data) {
                                let color = "#9ca3af";
                                let eff = data.efficiency;
                                if (eff >= 0.04) color = "#4ade80";      
                                else if (eff >= 0.02) color = "#86efac"; 
                                else if (eff >= 0.01) color = "#bbf7d0"; 
                                else if (eff <= -0.01) color = "#ca8a8a";                  
    
                                let sign = data.saved < 0 ? "-" : (data.saved > 0 ? "+" : "");
                                let absSaved = Math.abs(data.saved);
    
                                let histogramHtml = '';
                                if (data.frequencies && data.frequencies.length > 0) {
                                    let bMin = data.binMin;
                                    let bWid = data.binWidth;
    
                                    for (let j = 0; j < data.frequencies.length; j++) {
                                        let freq = data.frequencies[j];
                                        if (freq === 0) continue;
    
                                        let hPct = (freq / data.vMax) * 100;
                                        hPct = Math.min(hPct, 100);
    
                                        let leftEdge = bMin + (j * bWid); 
                                        let rightEdge = leftEdge + bWid;  
    
                                        let xLeft = getHistPct(leftEdge);
                                        let xWidth = getHistPct(rightEdge) - xLeft;
    
                                        let barColor;
                                        if (leftEdge <= 0 && rightEdge > 0) {
                                            barColor = '#9ca3af'; 
                                        } else if (rightEdge <= 0) {
                                            barColor = '#86efac'; 
                                        } else {
                                            barColor = '#fca5a5'; 
                                        }
    
                                        histogramHtml += `<div style="position: absolute; left: ${xLeft}%; width: ${xWidth}%; bottom: 0; height: ${hPct}%; background-color: ${barColor}; opacity: 0.85;"></div>`;
                                    }
    
                                    let meanX = getHistPct(data.mean);
    
                                    histogramHtml += `<div style="position: absolute; left: ${hZeroPct}%; top: 0; bottom: 0; width: 0px; border-left: 1px dashed white; z-index: 2;"></div>`;
                                    histogramHtml += `<div style="position: absolute; left: ${meanX}%; top: 0; bottom: 0; width: 0px; border-left: 1px dashed #60a5fa; z-index: 3;"></div>`;
                                }
    
                                let histDisplay = window.UL_BADGE_PREF === 'hist' ? 'block' : 'none';
                                let boxDisplay = window.UL_BADGE_PREF === 'box' ? 'flex' : 'none';
    
                                // Format the whole number percents passed from Python
                                let effStr = data.eff_rate + "%";
                                let connStr = data.conn_rate + "%";
                                let timeStr = data.conn_time.toFixed(1) + "s";
    
                                badge.innerHTML = `
                                    ${ratingHtml}
                                    <div class="ul-badge-hist" style="display: ${histDisplay}; position: relative; width: 100%; height: 100%; background: #313131; border: 1px solid ${color}; border-radius: 4px; overflow: hidden;">
                                        ${histogramHtml}
                                        
                                        <div style="position: absolute; top: 1px; left: 4px; z-index: 4; text-shadow: 1px 1px 2px black, -1px -1px 2px black;">
                                            <div style="font-size: 0.7em; color: rgba(255,255,255,0.9);">Lv ${data.hint_level || 0} | ${data.sp_cost || "?"} SP</div>
                                            <div style="font-size: 0.6em; color: rgba(200,200,200,0.9); white-space: nowrap;">E: ${effStr} | C: ${connStr} | ${timeStr}</div>
                                        </div>
    
                                        <div style="position: absolute; top: 2px; right: 4px; font-size: 0.75em; text-align: right; line-height: 1.15; z-index: 4; text-shadow: 1px 1px 2px black, -1px -1px 2px black;">
                                            <div style="font-weight: bold; color: ${color};">${sign}${absSaved.toFixed(3)}s</div>
                                            <div style="color: ${color}; font-weight: bold; font-size: 0.9em;">(${eff.toFixed(3)})</div>
                                        </div>
                                    </div>
    
                                    <div class="ul-badge-box" style="display: ${boxDisplay}; position: relative; width: 100%; height: 100%; background: #313131; border: 1px solid ${color}; border-radius: 4px; box-sizing: border-box; overflow: hidden;">
                                        
                                        <div style="position: absolute; left: ${baseMedianPct}%; top: 0; bottom: 0; width: 0px; border-left: 1px dashed white; z-index: 2;"></div>
    
                                        <div style="position: absolute; top: 1px; left: 4px; z-index: 4; text-shadow: 1px 1px 2px black, -1px -1px 2px black;">
                                            <div style="font-size: 0.7em; color: rgba(255,255,255,0.9);">Lv ${data.hint_level || 0} | ${data.sp_cost || "?"} SP</div>
                                            <div style="font-size: 0.6em; color: rgba(200,200,200,0.9); white-space: nowrap;">E: ${effStr} | C: ${connStr} | ${timeStr}</div>
                                        </div>
    
                                        <div style="position: absolute; top: 2px; right: 4px; font-size: 0.75em; text-align: right; line-height: 1.15; z-index: 4; text-shadow: 1px 1px 2px black, -1px -1px 2px black;">
                                            <div style="font-weight: bold; color: ${color};">${sign}${absSaved.toFixed(3)}s</div>
                                            <div style="color: ${color}; font-weight: bold; font-size: 0.9em;">(${eff.toFixed(3)})</div>
                                        </div>
    
                                        <div style="position: absolute; bottom: 0px; left: 0px; right: 0px; height: 20px; z-index: 3;">
                                            <div style="position: absolute; left: ${getBoxPct(data.wMin)}%; width: ${Math.max(0, getBoxPct(data.q1) - getBoxPct(data.wMin))}%; top: 50%; height: 2px; background: rgba(255,255,255,0.6); transform: translateY(-50%);"></div>
                                            <div style="position: absolute; left: ${getBoxPct(data.q1)}%; width: ${Math.max(0, getBoxPct(data.q3) - getBoxPct(data.q1))}%; top: 2px; bottom: 2px; background: ${color}; opacity: 0.85; border-radius: 1px;"></div>
                                            
                                            <div style="position: absolute; left: ${getBoxPct(data.median)}%; top: 2px; bottom: 2px; width: 1px; background: #60a5fa; z-index: 2;"></div>
                                            
                                            <div style="position: absolute; left: ${getBoxPct(data.q3)}%; width: ${Math.max(0, getBoxPct(data.wMax) - getBoxPct(data.q3))}%; top: 50%; height: 2px; background: rgba(255,255,255,0.6); transform: translateY(-50%);"></div>
                                            ${(data.outliers || []).map(val => `<div style="position: absolute; left: ${getBoxPct(val)}%; top: 50%; width: 1px; height: 10px; background: white; opacity: 0.2; border-radius: 1px; transform: translate(-50%, -50%);"></div>`).join("")}
                                        </div>
                                    </div>
                                `;
                            } else if (ratingHtml !== '') {
                                badge.innerHTML = ratingHtml;
                            }
                        }
                        row.prepend(badge);
                        break;
                    }
                }
            }
    
            for (let i = 0; i < skill_elements.length; i++) {
                const item = skill_elements[i];
                item.style.display = "grid";
                item.style.width = "100%";
                item.style.boxSizing = "border-box";
                item.style.gridTemplateAreas = '"badge image jpname desc"';
                item.style.gridTemplateColumns = "165px 40px 250px minmax(0, 1fr)";
    
                if (color_class) {
                    if (i % 2 == 0) item.classList.add(color_class);
                    else item.classList.remove(color_class);
                }
                skills_table.appendChild(item);
            }
            """, self.skills_list, sim_summary, global_hist_min, global_hist_max, global_box_min, global_box_max,
            acquired_skills_list, all_sk_data, base_median_abs, rating_data, uma_score, uma_rank, projected_score, projected_rank, uma_next, proj_next)

    def run_simulation(self, exe_path, payload):
        json_payload = json.dumps(payload)

        try:
            command = [exe_path, json_payload]

            process = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8'
            )

            if process.stderr:
                logger.debug(f"Sim Output: {process.stderr}")

            return json.loads(process.stdout)

        except subprocess.CalledProcessError as e:
            logger.error(f"Sim Crashed! Exit Code: {e.returncode}")
            logger.error(f"Sim Error Output: {e.stderr}")
            return {}  # Return empty dict instead of killing the app

        except json.JSONDecodeError:
            logger.error("Failed to parse JSON response!")
            logger.error(f"Sim Output: {process.stdout}")  # process is bound here, this is safe
            return {}

        except FileNotFoundError:
            logger.error(f"Could not find {exe_path}.")
            return {}

    def determine_event_element(self, event_titles):
        ranked_elements = []
        for event_title in event_titles:
            # The class names are mangled with React now :(
            # We need to filter by buttons to just exclude the divs containing them
            possible_elements = self.browser.execute_script(
                """
                let a = document.querySelectorAll("div[id^='event-viewer-'] button[class^='sc-'], div[class^='compatibility_result_box_'] button[class^='sc-']");
                let ele = [];
                for (let i = 0; i < a.length; i++) {
                    let item = a[i];
                    if (item.textContent.includes(arguments[0])) {
                        let diff = item.textContent.length - arguments[0].length;
                        ele.push([diff, item, item.textContent]);
                    }
                }
                return ele;
                """,
                event_title
            )
            if possible_elements:
                possible_elements.sort(key=lambda x: x[0])
                ranked_elements.append(possible_elements[0])

        if not ranked_elements:
            return None

        ranked_elements.sort(key=lambda x: x[0])
        logger.info(f"Event element: {ranked_elements[0][2]}")
        return ranked_elements[0][1]

    def set_browser_topmost(self, is_topmost):
        self.browser_topmost = is_topmost
        logger.debug(f"Setting browser topmost to {is_topmost}")
        self.browser.set_topmost(is_topmost)

    def run_with_catch(self):
        try:
            self.run()
        except Exception:
            util.show_error_box("Critical Error", "Uma Launcher has encountered a critical error and will now close.")
            self.threader.stop()

    def run(self):
        try:
            base_path = None
            if 'IS_UL_GLOBAL' not in os.environ:
                base_path = util.get_game_folder()

                if not base_path:
                    logger.error("Packet intercept enabled but no game path found")
                    util.show_error_box("Uma Launcher: No game install path found.",
                                        "Ensure you have the game installed.")
                    return

            if 'IS_UL_GLOBAL' in os.environ:
                port = self.threader.settings["carrotblender_port"]
                ip_address = self.threader.settings["carrotblender_host"]
                try:
                    self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF,
                                         self.threader.settings["carrotblender_max_buffer_size"])
                    logger.info(f"Max buffer size: {self.sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)}")
                    self.sock.bind((ip_address, port))
                except socket.error as message:
                    util.show_warning_box("Uma Launcher: Error initializing CarrotJuicer.",
                                          f"Could not bind to {ip_address}:{port}")

            chunks_left = 0
            while not self.should_stop:

                msg_path = None
                if 'IS_UL_GLOBAL' not in os.environ:
                    time.sleep(0.25)
                    msg_path = os.path.join(base_path, "CarrotJuicer")

                if not self.threader.settings["enable_carrotjuicer"] or not self.threader.settings['enable_browser']:
                    if self.browser and self.browser.alive():
                        self.browser.quit()
                    if self.skill_browser and self.skill_browser.alive():
                        self.skill_browser.quit()
                    continue

                if self.browser and self.browser.alive():
                    self.browser.enforce_z_order()

                    if self.reset_browser:
                        self.browser.set_window_rect(self.get_browser_reset_position())
                elif self.last_browser_rect:
                    self.save_last_browser_rect()

                self.reset_browser = False

                # Skill window.
                if self.open_skill_window:
                    self.open_skill_window = False
                    self.previous_skills_list = self.skills_list
                    self.update_skill_window()
                elif self.skill_browser and self.skill_browser.alive() and self.previous_skills_list != self.skills_list:
                    self.previous_skills_list = self.skills_list
                    self.update_skill_window()

                if self.skill_browser:
                    if self.skill_browser.alive():
                        pass
                    else:
                        self.save_skill_window_rect()

                if os.path.exists(util.get_relative("debug.in")) and util.is_debug:
                    try:
                        with open(util.get_relative("debug.in"), "r", encoding="utf-8") as f:
                            data = json.load(f)
                        self.handle_response(data, is_json=True)
                        os.remove(util.get_relative("debug.in"))
                    except Exception as e:
                        logger.error(traceback.format_exc())
                        pass

                if 'IS_UL_GLOBAL' not in os.environ:
                    messages = self.get_msgpack_batch(msg_path)
                    for message in messages:
                        self.process_message(message)
                else:
                    #logger.debug("Waiting for message...")
                    try:
                        ready = select.select([self.sock], [], [], 0.5)
                        if ready[0]:
                            message = self.sock.recv(self.MAX_BUFFER_SIZE)
                            logger.debug(f"Received {len(message)} bytes of data")
                        else:
                            continue
                    except Exception as e:
                        logger.error(f"Socket interrupted: {e}\n{traceback.format_exc()}")
                        continue
                    if message == b'':
                        logger.error(f"Socket read no data!")
                        continue
                    if len(message) < 2:
                        logger.error(f"Invalid message (invalid length): {message.hex()}")
                        continue
                    msg_type = message[0]
                    msg_len = 0
                    if msg_type != 4:
                        msg_len = message[1] * 256 + message[2]
                        if len(message) < msg_len:
                            logger.error(f"Invalid message (incomplete): {message.hex()}")
                            continue
                        message = message[3:msg_len + 3]

                    if msg_type == 0:
                        logger.debug(f"Processing data: {message.hex()}")
                        self.encrypted_data = message
                    elif msg_type == 1:
                        logger.debug(f"Processing key: {message.hex()}")
                        self.key = message
                    elif msg_type == 2:
                        logger.debug(f"Processing IV: {message.hex()}")
                        self.iv = message

                        if self.key is not None and self.iv is not None and self.encrypted_data is not None and self.encrypted_data != b'':
                            try:
                                unpacked = unpack(self.encrypted_data, self.key, self.iv)
                                logger.debug("Unpacked message:")
                                logger.debug(unpacked)
                                self.handle_response(unpacked, is_json=True)
                                self.key = None
                                self.iv = None
                                self.encrypted_data = None
                            except Exception as e:
                                logger.error(f"Error decoding and handling message: {e}")
                                logger.error(traceback.format_exc())
                                self.key = None
                                self.iv = None
                                self.encrypted_data = None
                        else:
                            logger.warning(f"Ignoring message: data, key and/or IV is not set!")
                    elif msg_type == 3:
                        logger.debug(f"Processing request: {message.hex()}")
                        logger.debug(f"Unpacked request: {msgpack.unpackb(message[4:])}")
                        self.handle_request(message, is_json=True)
                    elif msg_type == 4:
                        chunks_left = message[1]
                        self.encrypted_data = b''
                        logger.debug(f"Got multipart response header with {chunks_left} chunks")
                    elif msg_type == 5:
                        if chunks_left < 1:
                            logger.error("Got unexpected multipart message chunk!")
                            continue
                        chunks_left -= 1
                        logger.debug(f"Got chunk of size {msg_len}: {message.hex()}")
                        self.encrypted_data += message
                    else:
                        logger.error(f"Invalid message (invalid type): {message.hex()}")
                        continue

        except NoSuchWindowException:
            pass

        if self.browser:
            logger.debug("Closing browser.")
            self.browser.quit()

        if self.skill_browser:
            logger.debug("Closing skill browser.")
            self.skill_browser.quit()

        self.save_last_browser_rect()
        self.save_skill_window_rect()

        return

    def stop(self):
        self.should_stop = True
        if self.sock is not None:
            logger.info("Stopping CarrotBlender socket")
            self.sock.shutdown(socket.SHUT_RDWR)
            self.sock.close()


def setup_helper_page(browser: horsium.BrowserWindow):
    browser.execute_script("""
    if (window.UL_OVERLAY) {
        window.UL_OVERLAY.remove();
    }
    window.UL_OVERLAY = document.createElement("div");
    window.GT_PAGE = document.getElementById("__next");
    window.OVERLAY_HEIGHT = "15rem";
    window.UL_OVERLAY.style.height = "max_content";
    window.UL_OVERLAY.style.width = "100%";
    window.UL_OVERLAY.style.padding = "0.5rem 0";
    window.UL_OVERLAY.style.position = "fixed";
    window.UL_OVERLAY.style.bottom = "100%";
    window.UL_OVERLAY.style.zIndex = 100;
    window.UL_OVERLAY.style.backgroundColor = "var(--c-bg-main)";
    window.UL_OVERLAY.style.borderBottom = "2px solid var(--c-topnav)";

    var ul_data = document.createElement("div");
    ul_data.id = "ul-data";
    window.UL_OVERLAY.appendChild(ul_data);

    window.UL_OVERLAY.ul_data = ul_data;

    ul_data.style.display = "flex";
    ul_data.style.alignItems = "center";
    ul_data.style.justifyContent = "center";
    ul_data.style.flexDirection = "column";
    ul_data.style.gap = "0.5rem";
    ul_data.style.fontSize = "0.9rem";

    var ul_dropdown = document.createElement("div");
    ul_dropdown.id = "ul-dropdown";
    ul_dropdown.classList.add("ul-overlay-button");
    ul_dropdown.style = "position: fixed;right: 0;top: 0;width: 3rem;height: 1.6rem;background-color: var(--c-bg-main);text-align: center;z-index: 101;line-height: 1.5rem;border-left: 2px solid var(--c-topnav);border-bottom: 2px solid var(--c-topnav);border-bottom-left-radius: 0.5rem;cursor: pointer;";
    ul_dropdown.textContent = "⯅";
    window.UL_OVERLAY.appendChild(ul_dropdown);

    var ul_skills = document.createElement("div");
    ul_skills.id = "ul-skills";
    ul_skills.classList.add("ul-overlay-button");
    ul_skills.style = "position: fixed; right: 50px; top: 0; width: 3.5rem; height: 1.6rem; background-color: var(--c-bg-main); text-align: center; z-index: 101; line-height: 1.5rem; border-left: 2px solid var(--c-topnav); border-bottom: 2px solid var(--c-topnav); border-right: 2px solid var(--c-topnav); border-bottom-left-radius: 0.5rem; border-bottom-right-radius: 0.5rem; cursor: pointer; transition: top 0.5s ease 0s;";
    ul_skills.textContent = "Skills";
    window.UL_OVERLAY.appendChild(ul_skills);
    
    var ul_topmost_div = document.createElement("div");
    ul_topmost_div.classList.add("ul-overlay-button");
    ul_topmost_div.style = "position: fixed; right: 108px; top: 0; width: 9rem; height: 1.6rem; background-color: var(--c-bg-main); text-align: center; z-index: 101; line-height: 1.5rem; border-left: 2px solid var(--c-topnav); border-bottom: 2px solid var(--c-topnav); border-right: 2px solid var(--c-topnav); border-bottom-left-radius: 0.5rem; border-bottom-right-radius: 0.5rem; transition: top 0.5s ease 0s;";
    ul_topmost_div.id = "ul-topmost-div"
    
    var ul_topmost = document.createElement("input");
    ul_topmost.id = "ul-topmost";
    ul_topmost.type = "checkbox";
    ul_topmost.checked = """ +
                           str(browser.threader.settings["browser_topmost"]).lower()
                           + """;
    ul_topmost.style = "cursor: pointer;"
    ul_topmost.classList.add("ul-overlay-button");
    
    var ul_topmost_label = document.createElement("label");
    ul_topmost_label.setAttribute("for", "ul-topmost");
    ul_topmost_label.textContent = "Always on top";
    ul_topmost_label.id = "ul-topmost-label"
    ul_topmost_label.style = "cursor: pointer;"
    
    ul_topmost_div.appendChild(ul_topmost);
    ul_topmost_div.appendChild(ul_topmost_label);
    window.UL_OVERLAY.appendChild(ul_topmost_div);
    

    window.hide_overlay = function() {
        window.UL_DATA.expanded = false;
        document.getElementById("ul-dropdown").textContent = "⯆";
        // document.getElementById("ul-dropdown").style.top = "-2px";
        [...document.querySelectorAll(".ul-overlay-button")].forEach(div => {
            div.style.top = "-2px";
        })
        window.GT_PAGE.style.paddingTop = "0";
        window.UL_OVERLAY.style.bottom = "100%";
    }

    window.expand_overlay = function() {
        window.UL_DATA.expanded = true;

        var height = window.UL_OVERLAY.offsetHeight;
        window.OVERLAY_HEIGHT = height + "px";

        document.getElementById("ul-dropdown").textContent = "⯅";
        // document.getElementById("ul-dropdown").style.top = "calc(" + window.OVERLAY_HEIGHT + " - 2px)";
        [...document.querySelectorAll(".ul-overlay-button")].forEach(div => {
            div.style.top = "calc(" + window.OVERLAY_HEIGHT + " - 2px)";
        })
        window.GT_PAGE.style.paddingTop = window.OVERLAY_HEIGHT;
        window.UL_OVERLAY.style.bottom = "calc(100% - " + window.OVERLAY_HEIGHT + ")";
    }

    ul_dropdown.addEventListener("click", function() {
        if (window.UL_DATA.expanded) {
            window.hide_overlay();
        } else {
            window.expand_overlay();
        }
    });

    window.UL_DATA = {
        energy: 100,
        max_energy: 100,
        table: "",
        expanded: true
    };

    document.body.prepend(window.UL_OVERLAY);

    window.UL_OVERLAY.querySelector("#ul-dropdown").style.transition = "top 0.5s";
    window.UL_OVERLAY.style.transition = "bottom 0.5s";
    window.GT_PAGE.style.transition = "padding-top 0.5s";

    window.update_overlay = function() {
        window.UL_OVERLAY.ul_data.replaceChildren();
        window.UL_OVERLAY.ul_data.insertAdjacentHTML("afterbegin", window.UL_DATA.overlay_html)
        //window.UL_OVERLAY.ul_data.innerHTML = window.UL_DATA.overlay_html;

        if (window.UL_DATA.expanded) {
            window.expand_overlay();
            //setTimeout(window.expand_overlay, 100);
        }
    };

    // Skill window.
    window.await_skill_window_timeout = null;
    window.await_skill_window = function() {
        window.await_skill_window_timeout = setTimeout(function() {
            ul_skills.style.filter = "";
        }, 15000);

        ul_skills.style.filter = "brightness(0.5)";
        fetch('http://127.0.0.1:3150/open-skill-window', { method: 'POST' });
    }
    window.skill_window_opened = function() {
        if (window.await_skill_window_timeout) {
            clearTimeout(window.await_skill_window_timeout);
        }
        ul_skills.style.filter = "";
    }

    ul_skills.addEventListener("click", window.await_skill_window);

    // Always on top toggle
    window.await_topmost = function() {
        var checkbox = document.getElementById("ul-topmost");
        fetch('http://127.0.0.1:3150/topmost', { method: 'POST', body: checkbox.checked, headers: { 'Content-Type': 'text/plain'  } } );
    }
    ul_topmost.addEventListener("click", window.await_topmost);
    
    window.send_screen_rect = function() {
        let rect = {
            'x': window.screenX,
            'y': window.screenY,
            'width': window.outerWidth,
            'height': window.outerHeight
        };
        fetch('http://127.0.0.1:3150/helper-window-rect', { method: 'POST', body: JSON.stringify(rect), headers: { 'Content-Type': 'text/plain' } });
        setTimeout(window.send_screen_rect, 2000);
    }
    setTimeout(window.send_screen_rect, 2000);

    """)

    gametora_dark_mode(browser)

    # # Enable all cards
    # browser.execute_script("""
    # var settings = document.querySelector("[class^='filters_settings_button_']");
    # if( settings == null )
    # {
    #    settings = document.getElementById("teh-settings-open");
    # }
    # if( settings == null )
    # {
    #    settings = Array.from(document.querySelectorAll('div')).find( el => el.textContent === "Settings");
    #    if( settings == null ) return;
    #    settings = settings.childNodes[0];
    # }
    # if( settings != null )
    # {
    #    settings.click();
    # }
    # """)
    # while not browser.execute_script("""return document.getElementById("allAtOnceCheckbox");"""):
    #     time.sleep(0.125)
    # all_cards_enabled = browser.execute_script("""return document.getElementById("allAtOnceCheckbox").checked;""")
    # if not all_cards_enabled:
    #     browser.execute_script("""document.getElementById("allAtOnceCheckbox").click()""")
    # browser.execute_script("""document.querySelector("[class^='filters_confirm_button_']").click()""")

    browser.execute_script("""
            let checkbox = document.getElementById("allAtOnceCheckbox");
            if (checkbox && !checkbox.checked) {
                checkbox.click();
            }
        """)

    gametora_remove_cookies_banner(browser)
    gametora_close_ad_banner(browser)

def setup_skill_window(browser: horsium.BrowserWindow):
    # Setup callback for window position
    browser.execute_script("""
    window.send_screen_rect = function() {
        let rect = {
            'x': window.screenX,
            'y': window.screenY,
            'width': window.outerWidth,
            'height': window.outerHeight
        };
        fetch('http://127.0.0.1:3150/skills-window-rect', { method: 'POST', body: JSON.stringify(rect), headers: { 'Content-Type': 'text/plain' } });
        setTimeout(window.send_screen_rect, 2000);
    }
    setTimeout(window.send_screen_rect, 2000);
    """)

    # Hide filters by finding the search box and hiding its parent container
    browser.execute_script("""
        let searchBox = document.querySelector("input[class*='filters_search_box']");
        if (searchBox && searchBox.parentElement) {
            searchBox.parentElement.style.display = "none";
        }
    """)

    # Hide navigation and collapse the empty space it leaves behind
    browser.execute_script("""
        let navBar = document.querySelector("nav");
        if (navBar) navBar.style.display = "none";

        let navBg = document.querySelector("div[id^='styles_page-topnav-bg']");
        if (navBg) navBg.style.display = "none";
        
        let rightNav = document.querySelector("div[id*='page-rightnav']");
        if (rightNav) rightNav.style.display = "none";

        let pageWrapper = document.querySelector("div[class^='styles_page__']");
        if (pageWrapper) {
            // Replacing fixed pixel heights with 'auto' tells the grid to shrink empty rows to 0px
            pageWrapper.style.gridTemplateRows = "auto auto 1fr"; 
            pageWrapper.style.gridTemplateColumns = "[main-page] 1fr";
            
            pageWrapper.style.maxWidth = "none";
            pageWrapper.style.width = "100%";
            pageWrapper.style.padding = "0";
        }

        let mainContent = document.querySelector("main[id^='styles_page-main']");
        if (mainContent) {
            mainContent.style.paddingTop = "0px";
            mainContent.style.marginTop = "0px";
            mainContent.style.width = "100%";
            mainContent.style.maxWidth = "none";
        }
    """)

    # Hide the result count
    browser.execute_script("""
        let possibleDivs = document.querySelectorAll('div[style*="margin-bottom: 20px"]');
        for (let div of possibleDivs) {
            if (div.textContent.includes("Found") && div.textContent.includes("results")) {
                div.style.display = "none";
                break;
            }
        }
    """)

    gametora_dark_mode(browser)

    # Enable all settings checkboxes
    browser.execute_script("""
        const settingsIds = [
            'highlightCheckbox',
            'showIdCheckbox',
            'showCondViewerCheckbox',
            'alwaysShowAllCheckbox'
        ];

        settingsIds.forEach(id => {
            let cb = document.getElementById(id);
            if (cb && !cb.checked) {
                cb.click();
            }
        });
    """)

    gametora_remove_cookies_banner(browser)
    gametora_close_ad_banner(browser)


def gametora_dark_mode(browser):
    browser.execute_script("""
        localStorage.setItem('theme', 'dark');
        document.documentElement.setAttribute('data-theme', 'dark');
        document.documentElement.style.colorScheme = 'dark';
    """)


def gametora_remove_cookies_banner(browser: horsium.BrowserWindow):
    # Inject a permanent CSS rule to instantly hide the cookie banner
    browser.execute_script("""
        if (!document.getElementById('ul-cookie-hider')) {
            let style = document.createElement('style');
            style.id = 'ul-cookie-hider';
            style.innerHTML = '#adnote { display: none !important; }';
            document.head.appendChild(style);
        }
    """)


def gametora_close_ad_banner(browser: horsium.BrowserWindow):
    if 'training-event-helper' in browser.url:
        # Close the top support cards thing
        browser.execute_script("""
            let a = document.querySelector("[id^='styles_page-main_']");
            if( a != null ){
                let b = a.children[1]; //First element is top ad
                if( b != null )
                {
                    let c = b.children[b.childElementCount - 1]; //Last element is the support cards thing
                    if( c != null )
                    {
                        c.style.display = "none";
                    }
                }
            }
        """)

    # Permanent CSS rule to instantly hide ads across all pages
    browser.execute_script("""
        if (!document.getElementById('ul-ad-hider')) {
            let style = document.createElement('style');
            style.id = 'ul-ad-hider';
            style.innerHTML = `
                .top-ad, 
                .footer-ad, 
                .publift-widget-sticky_footer-container, 
                [class*="publift"] { 
                    display: none !important; 
                }
            `;
            document.head.appendChild(style);
        }
    """)


def setup_gametora(browser: horsium.BrowserWindow):
    gametora_dark_mode(browser)
    gametora_remove_cookies_banner(browser)
    gametora_close_ad_banner(browser)


def set_gametora_server_to_jp(browser):
    logger.info("Setting GameTora page to the Japan server")
    browser.execute_script("""
        let settings_button = document.querySelectorAll("[class^='styles_header_settings_']");
        if(settings_button.length > 0) {
            settings_button[0].click();
        }
        //delay to let it the settings open
        setTimeout(function(){
            let server_button = document.querySelectorAll("[id^='serverJaCheckbox']");
            if(server_button.length > 0) {
                server_button[0].click();
            }
        }, 1000);
        """)


def is_gametora_jp(browser):
    result = browser.execute_script("""
    let header = document.querySelectorAll("[class^='styles_header_text_']");
    if(header.length > 0)
    {
        //Can't use innerHtml here for some reason?
        return header[0].innerText.includes("Japan")
    }
    return false 
    """)
    logger.info('GameTora page is' + ("" if result else " NOT") + ' set to the Japan server.')
    return result
