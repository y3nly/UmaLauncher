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
import statistics

from Cryptodome.Cipher import AES


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
        self.skill_costs_dict = mdb.get_skill_costs_dict()
        self.skill_conditions_dict = mdb.get_skill_conditions_dict()


        self.acquired_skills_list = []
        self.skill_hints = dict()
        self.style = ''

        self.screen_state_handler = threader.screenstate
        self.restart_time()

        self.helper_table = helper_table.HelperTable(self)

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
            if (rect_var['x'] == -32000 and rect_var['y'] == -32000):
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
            return [f"{self.EVENT_ID_TO_POS_STRING[event_id]} ({grade_text})", f"{self.EVENT_ID_TO_POS_STRING[event_id]}"]

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

                self.skills_list = []
                self.acquired_skills_list = []
                self.skill_hints = {}
                self.style = data['chara_info']['race_running_style']

                # Acquired skills
                for skill_data in data['chara_info']['skill_array']:
                    skill_id = skill_data['skill_id']
                    self.acquired_skills_list.append(skill_id)
                    self.skills_list.append(skill_id)

                self.skills_list += mdb.get_card_inherent_skills(data['chara_info']['card_id'],
                                                                 data['chara_info']['talent_level'])

                # Hints
                for skill_tip in data['chara_info']['skill_tips_array']:
                    if skill_tip['rarity'] > 1:
                        skill_id = self.skill_id_dict[(skill_tip['group_id'], skill_tip['rarity'])]
                        white_id = mdb.determine_skill_id_from_group_id(skill_tip['group_id'], 1, self.skills_list)
                        if white_id not in self.skills_list:
                            self.skills_list.append(white_id)
                    else:
                        skill_id = mdb.determine_skill_id_from_group_id(skill_tip['group_id'], skill_tip['rarity'],
                                                                        self.skills_list)

                    self.skills_list.append(skill_id)
                    self.skill_hints[skill_id] = skill_tip.get('level', 0)

                self.skills_list = mdb.sort_skills_by_display_order(self.skills_list)

                # Fix certain skills for GameTora
                for i in range(len(self.skills_list)):
                    old_id = self.skills_list[i]
                    if 900000 <= old_id < 1000000:
                        new_id = old_id - 800000
                        self.skills_list[i] = new_id

                        # Keep the hint dictionary synced if the ID changes
                        if old_id in self.skill_hints:
                            self.skill_hints[new_id] = self.skill_hints.pop(old_id)

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

                if len(data['unchecked_event_array']) > 1:
                    logger.warning(f"Packet has more than 1 unchecked event! {message}")

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
                                var cont = document.getElementById("30021").parentElement.parentElement.parentElement;
                                var rSupportsCheckbox = cont.lastChild?.children[1]?.children[1]?.querySelector('input');
                                var showUpcomingSupportsCheckbox = cont.lastChild?.children[1]?.children[1]?.querySelector('input');
                                if( rSupportsCheckbox && !rSupportsCheckbox.checked ) {
                                 rSupportsCheckbox.click(); 
                                }
                                if( showUpcomingSupportsCheckbox && !showUpcomingSupportsCheckbox.checked ) {
                                 showUpcomingSupportsCheckbox.click(); 
                                }
                            """)
                        self.browser.execute_script(
                            """
                            var cont = document.getElementById("30021").parentElement.parentElement.parentElement;
                            
                            var ele = document.getElementById(arguments[0].toString());

                            if (ele) {
                                ele.click();
                                return;
                            }
                            cont.querySelector('img[src="/images/ui/close.png"]').click();
                            """,
                            event_data['event_contents_info']['support_card_id']
                        )
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

            if 'reserved_race_array' in data and 'chara_info' not in data and self.last_helper_data:
                # User changed reserved races
                self.last_helper_data['reserved_race_array'] = data['reserved_race_array']
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

        # Initialize or refocus the skill tracking browser
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

        # Filter for unacquired skills to optimize simulation throughput
        unacquired_skills_list = [s for s in self.skills_list if s not in self.acquired_skills_list]

        STYLE_INTERNAL_MAP = {
            1: "NIGE",
            2: "SEN",
            3: "SASI",
            4: "OI"
        }

        # Define simulation parameters and environmental state
        mock_payload = {
            "baseSetting": {
                "umaStatus": {
                    "charaName": "Place Holder",
                    "speed": 1200, "stamina": 1200, "power": 1000, "guts": 400, "wisdom": 1200,
                    "condition": "BEST", "style": STYLE_INTERNAL_MAP.get(self.style, "NIGE"),
                    "distanceFit": "A", "surfaceFit": "A", "styleFit": "A",
                    "popularity": 1, "gateNumber": 1,
                },
                "track": {
                    "location": 10006, "course": 10611, "condition": "GOOD", "gateCount": 9
                }
            },
            "acquiredSkillIds": self.acquired_skills_list,
            "unacquiredSkillIds": unacquired_skills_list,
            "skillHints": self.skill_hints,
            "iterations": 2000
        }

        # Execute external simulation engine
        results = self.run_simulation(util.get_asset("_assets/umasim-cli.exe"), mock_payload)

        sim_summary = {}
        global_min = 0.0
        global_max = 0.0

        # Pre-process condition strings for ALL skills
        conditions_payload = {}
        for s_id in self.skills_list:
            conditions_payload[str(s_id)] = self.skill_conditions_dict.get(s_id, "Guaranteed")

        if results and "baselineStats" in results and "candidates" in results:
            for skill_id_str, candidate_result in results["candidates"].items():
                skill_id_int = int(skill_id_str)
                time_saved_stats = candidate_result.get("timeSavedStats", {})

                if not time_saved_stats:
                    continue

                base_cost = self.skill_costs_dict.get(skill_id_str, 0)
                hint_level = self.skill_hints.get(skill_id_int, 0)

                effective_hint_level = min(hint_level, 5)
                discount_map = {0: 0, 1: 10, 2: 20, 3: 30, 4: 35, 5: 40}
                discount_percent = discount_map.get(effective_hint_level, 0)
                sp_cost = int(base_cost * (100 - discount_percent) / 100)

                raw_mean = time_saved_stats.get("mean", 0.0)
                s_mean = -raw_mean

                efficiency = (s_mean / max(sp_cost, 1)) * 100

                val_min = time_saved_stats.get("min", 0.0)
                val_max = time_saved_stats.get("max", 0.0)
                frequencies = time_saved_stats.get("frequencies", [])

                data_obj = {
                    "saved": round(s_mean, 4),
                    "wMin": val_min,
                    "wMax": val_max,
                    "binMin": time_saved_stats.get("binMin", 0.0),
                    "binWidth": time_saved_stats.get("binWidth", 0.0),
                    "frequencies": frequencies,
                    "maxFreq": max(frequencies) if frequencies else 1,
                    "sp_cost": sp_cost,
                    "hint_level": hint_level,
                    "efficiency": round(efficiency, 4)
                }

                global_min = min(global_min, val_min)
                global_max = max(global_max, val_max)

                sim_summary[skill_id_str] = data_obj

        self.skill_browser.execute_script(
            """
            let skills_list = arguments[0];
            let sim_results = arguments[1] || {};
            let globalMin = arguments[2];
            let globalMax = arguments[3];
            let acquired_list = arguments[4] || [];
            let conditions_map = arguments[5] || {};

            let range = globalMax - globalMin;
            if (range === 0) range = 1;
            let pad = range * 0.05; 
            let scaleMin = globalMin - pad;
            let scaleMax = globalMax + pad;
            let scaleRange = scaleMax - scaleMin;
            let getPct = (val) => Math.max(0, Math.min(100, ((val - scaleMin) / scaleRange) * 100));
            let zeroPct = getPct(0); 

            // Bulletproof Syntax Highlighting
            let highlightCondition = (text) => {
                if (!text || text === "Guaranteed") return `<span style="color: #9ca3af; font-style: italic;">Guaranteed</span>`;

                // 1. Safely parse Key (Yellow), Operator (Blue), and Value (Purple)
                let res = text.replace(/([a-zA-Z0-9_]+)\s*(==|!=|<=|>=|<|>)\s*(-?[0-9.]+)/g, (match, p1, p2, p3) => {
                    let safeOp = p2.replace('<', '&lt;').replace('>', '&gt;');
                    return `<span style="color: #34d399;">${p1}</span><span style="color: #60a5fa; font-weight: bold;">${safeOp}</span><span style="color: #a78bfa;">${p3}</span>`;
                });

                // 2. Highlight standalone '&' (Blue)
                res = res.replace(/&(?!(lt;|gt;|amp;))/g, '<span style="color: #60a5fa; font-weight: bold;"> &amp; </span>');

                // 3. Highlight OR (Pink)
                res = res.replace(/ OR /g, '<span style="color: #f472b6; font-weight: bold;"> OR </span>');

                return res;
            };

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
                let skill_string = "(" + skill_id + ")";
                for (const item of skill_rows) {
                    if (item.textContent.includes(skill_string)) {
                        let row = item.parentNode;
                        skill_elements.push(row);
                        row.remove();

                        let descCell = row.querySelector("[class^='skills_table_desc_']");
                        let moreBtn = row.querySelector("[class*='skills_more_']");
                        if (moreBtn) moreBtn.remove();
                        if (descCell) descCell.innerHTML = "";

                        // Uniform condition injection with standard font size
                        let rawCond = conditions_map[skill_id.toString()] || "Guaranteed";
                        if (descCell) {
                            descCell.innerHTML = `
                                <div style="padding: 4px 0;">
                                    <div style="word-break: break-word; font-family: monospace; color: var(--c-text-main); line-height: 1.4;">
                                        ${highlightCondition(rawCond)}
                                    </div>
                                </div>
                            `;
                        }

                        let existingBadge = row.querySelector('.sim-data-badge');
                        if (existingBadge) existingBadge.remove();

                        let badge = document.createElement("div");
                        badge.className = "sim-data-badge";
                        badge.style.gridArea = "badge";
                        badge.style.width = "180px";
                        badge.style.height = "40px"; 
                        badge.style.marginRight = "10px"; 
                        badge.style.boxSizing = "border-box";
                        badge.style.display = "flex";
                        badge.style.flexDirection = "column";
                        badge.style.justifyContent = "center";

                        if (acquired_list.includes(skill_id)) {
                            badge.style.padding = "2px 8px";
                            badge.style.backgroundColor = "rgba(156, 163, 175, 0.1)"; 
                            badge.style.border = "1px solid #9ca3af";
                            badge.style.color = "#9ca3af";
                            badge.style.borderRadius = "4px";
                            badge.style.fontWeight = "bold";
                            badge.style.alignItems = "center";
                            badge.innerHTML = `Acquired`;
                        } else {
                            let data = sim_results[skill_id.toString()]; 
                            if (data) {
                                let color = "#9ca3af";
                                let eff = data.efficiency;
                                if (eff >= 0.04) color = "#4ade80";      
                                else if (eff >= 0.02) color = "#86efac"; 
                                else if (eff >= 0.01) color = "#bbf7d0"; 
                                else if (eff <= -0.01) color = "#ca8a8a";                  

                                badge.style.padding = "2px 8px";
                                badge.style.backgroundColor = "var(--c-bg-main-hover)"; 
                                badge.style.border = `1px solid ${color}`;
                                badge.style.color = color;
                                badge.style.borderRadius = "4px";
                                badge.style.fontWeight = "bold";
                                badge.style.alignItems = "stretch";

                                let sign = data.saved > 0 ? "-" : (data.saved < 0 ? "+" : "");
                                let absSaved = Math.abs(data.saved);

                                let freqs = data.frequencies || [];
                                let maxFreq = data.maxFreq || 1;
                                let bMin = data.binMin;
                                let bWid = data.binWidth;

                                let barsHtml = freqs.map((freq, i) => {
                                    if (freq === 0) return "";
                                    let binStart = bMin + i * bWid;
                                    let binEnd = binStart + bWid;
                                    let binCenter = binStart + (bWid / 2);

                                    let pStart = getPct(binStart);
                                    let pEnd = getPct(binEnd);
                                    let pWidth = pEnd - pStart;
                                    let pHeight = (freq / maxFreq) * 100;

                                    let barColor = binCenter < 0 ? color : "#ca8a8a"; 

                                    return `<div style="position: absolute; left: ${pStart}%; width: ${pWidth}%; height: ${pHeight}%; bottom: 0; background: ${barColor}; opacity: 0.85; border-radius: 1px 1px 0 0;"></div>`;
                                }).join("");

                                badge.innerHTML = `
                                    <div style="display: flex; justify-content: space-between; font-size: 0.8em;">
                                        <span>${sign}${absSaved.toFixed(3)}s (${eff.toFixed(3)})</span>
                                        <span>Lv ${data.hint_level || 0} | ${data.sp_cost || "?"} SP</span>
                                    </div>
                                    <div style="position: relative; width: 100%; height: 16px; margin-top: 2px; background: rgba(0,0,0,0.15); border-radius: 4px; display: flex; align-items: flex-end;">
                                        <div style="position: absolute; left: ${zeroPct}%; top: 0; bottom: 0; width: 1px; background: rgba(255,255,255,0.3); z-index: 1;"></div>
                                        ${barsHtml}
                                    </div>
                                `;
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
                item.style.gridTemplateAreas = '"badge image jpname desc"';
                item.style.gridTemplateColumns = "190px 40px 250px 1fr";
                item.style.padding = "2px";
                item.style.alignItems = "center";

                if (color_class) {
                    if (i % 2 == 0) item.classList.add(color_class);
                    else item.classList.remove(color_class);
                }
                skills_table.appendChild(item);
            }
            """, self.skills_list, sim_summary, global_min, global_max, self.acquired_skills_list, conditions_payload)

    def run_simulation(self, exe_path, payload):
        json_payload = json.dumps(payload)

        try:
            process = subprocess.run(
                [exe_path, json_payload],
                capture_output=True,
                text=True,
                encoding='utf-8',
                check=True
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
                        pass
                        # self.browser.set_window_rect(self.get_browser_reset_position())
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
                    logger.debug("Waiting for message...")
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

    # Enable all cards
    browser.execute_script("""
    var settings = document.querySelector("[class^='filters_settings_button_']");
    if( settings == null )
    {
       settings = document.getElementById("teh-settings-open");
    }
    if( settings == null )
    {
       settings = Array.from(document.querySelectorAll('div')).find( el => el.textContent === "Settings");
       if( settings == null ) return;
       settings = settings.childNodes[0];
    }
    if( settings != null )
    {
       settings.click();
    }
    """)
    while not browser.execute_script("""return document.getElementById("allAtOnceCheckbox");"""):
        time.sleep(0.125)
    all_cards_enabled = browser.execute_script("""return document.getElementById("allAtOnceCheckbox").checked;""")
    if not all_cards_enabled:
        browser.execute_script("""document.getElementById("allAtOnceCheckbox").click()""")
    browser.execute_script("""document.querySelector("[class^='filters_confirm_button_']").click()""")

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


def gametora_dark_mode(browser: horsium.BrowserWindow):
    # Enable dark mode (the only reasonable color scheme)
    browser.execute_script("""document.querySelector("[class^='styles_header_settings_']").click()""")
    while not browser.execute_script("""return document.querySelector("[class^='filters_toggle_button_']");"""):
        time.sleep(0.25)

    dark_enabled = browser.execute_script(
        """return document.querySelector("[class^='tooltips_tooltip_']").querySelector("[class^='filters_toggle_button_']").childNodes[0].querySelector("input").checked;""")
    if dark_enabled != browser.threader.settings["gametora_dark_mode"]:
        browser.execute_script(
            """document.querySelector("[class^='tooltips_tooltip_']").querySelector("[class^='filters_toggle_button_']").childNodes[0].querySelector("input").click()""")
    browser.execute_script("""document.querySelector("[class^='styles_header_settings_']").click()""")


def gametora_remove_cookies_banner(browser: horsium.BrowserWindow):
    # Hide the cookies banner
    browser.execute_script("""
            if( window.removeCookiesId == null ) {
                window.removeCookiesId = setInterval( function() {
                    if( document.getElementById("adnote") != null) {
                        document.getElementById("adnote").style.display = 'none';
                    }
                }, 5 * 1000);
            }
            """)


def gametora_close_ad_banner(browser: horsium.BrowserWindow):
    if 'training-event-helper' in browser.url:
        # Close the top support cards thing, super jank
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

    browser.execute_script("""
    if (!window.__gtAdBlocker) {
        window.__gtAdBlocker = true;

        const removeAds = () => {
            document.querySelectorAll(
                '.top-ad, .footer-ad, ' +
                '.publift-widget-sticky_footer-container, ' +
                '[class*="publift"]'
            ).forEach(e => e.remove());
        };

        // Run once immediately
        removeAds();

        // Watch for dynamic injection
        const observer = new MutationObserver(removeAds);
        observer.observe(document.body, { childList: true, subtree: true });
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
