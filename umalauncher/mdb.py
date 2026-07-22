import sqlite3
import os
import threading
import traceback

from loguru import logger
import util
import constants
import gui

DB_PATH = None
_MDB_CACHE_LOCK = threading.RLock()
_MDB_CACHE_FINGERPRINT = None


def get_db_path():
    global DB_PATH
    if DB_PATH:
        return DB_PATH
    DB_PATH = os.path.expandvars("%userprofile%\\AppData\\LocalLow\\Cygames\\Umamusume\\master\\master.mdb")
    # New installs have the files in the game directory
    if not os.path.exists(DB_PATH):
        logger.debug( f"Could not find mdb at path: {DB_PATH}, trying game install directory")
        game_install_path = util.get_game_folder()
        if game_install_path is None:
            logger.error(f"Could not find game install path")
            util.show_error_box_no_report("Error",f"Could not the game database file at path: {DB_PATH}, and could not find game install directory. Ensure you have the game installed. <br>Uma Launcher will now close.")
            if gui.THREADER:
                gui.THREADER.stop()
            return DB_PATH
        DB_PATH = os.path.join(game_install_path, "UmamusumePrettyDerby_Data\\Persistent\\master\\master.mdb")
        if not os.path.exists(DB_PATH):
            logger.error(f"Could not find mdb at game install path: {DB_PATH}")
            util.show_error_box_no_report("Error",f"Could not find the game database file.<br>Make sure the game is installed. If the game is updating, try restarting Uma Launcher after the update finishes.<br>Uma Launcher will now close.")
            if gui.THREADER:
                gui.THREADER.stop()
    logger.debug( f"Using mdb path: {DB_PATH}")
    return DB_PATH


def _get_db_fingerprint():
    """Return the stable identity used to invalidate master-data caches."""
    db_path = os.path.normcase(os.path.realpath(os.path.abspath(get_db_path())))
    stat = os.stat(db_path)
    return db_path, stat.st_mtime_ns, stat.st_size


def update_mdb_cache(force=False):
    """Refresh all master-data caches when master.mdb has changed.

    The lock covers the fingerprint check and the complete refresh so the two
    CarrotBlender startup paths cannot both perform the same expensive reload.
    ``force`` remains available for callers that explicitly need a rebuild.
    """
    global _MDB_CACHE_FINGERPRINT

    with _MDB_CACHE_LOCK:
        fingerprint = _get_db_fingerprint()
        if not force and fingerprint == _MDB_CACHE_FINGERPRINT:
            logger.debug("master.mdb is unchanged; keeping cached data.")
            return

        logger.info("Reloading cached dicts.")
        _MDB_CACHE_FINGERPRINT = None
        _clear_update_caches()
        for func in UPDATE_FUNCS:
            func(force=True)
        _refresh_query_catalogs(force=True, fingerprint=fingerprint)
        _MDB_CACHE_FINGERPRINT = fingerprint

class Connection():
    def __init__(self):
        try:
            db_path = get_db_path()
            if db_path is None:
                raise sqlite3.OperationalError("Database path could not be determined.")
            self.conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.OperationalError:
            util.show_error_box_no_report("Connection Error", "Could not connect to the game database.<br>Try restarting Uma Launcher after the game updates.<br>Uma Launcher will now close.")
            if gui.THREADER:
                gui.THREADER.stop()
    def __enter__(self):
        return self.conn, self.conn.cursor()
    def __exit__(self, type, value, traceback):
        self.conn.close()
        
        if type is not None:
            logger.error(f"Error: {type} {value}")
            util.show_error_box("Connection Error", "Could not connect to the game database.")
            return True

def create_support_card_string(rarity, command_id, support_card_type, chara_id):
    return f"{constants.SUPPORT_CARD_RARITY_DICT[rarity]} {constants.SUPPORT_CARD_TYPE_DISPLAY_DICT[constants.SUPPORT_CARD_TYPE_DICT[(command_id, support_card_type)]]} {util.get_character_name_dict()[chara_id]}"

def get_columns(cursor):
    return [desc[0] for desc in cursor.description]

def rows_to_dict(rows, columns, keep_newline=False):
    return [{columns[i]: data if not isinstance(data, str) or keep_newline else data.replace("\\n", "") for i, data in enumerate(row)} for row in rows]


_QUERY_CATALOG_FINGERPRINT = None
_SKILL_CATALOG = {}
_SKILLS_BY_GROUP = {}
_SKILLS_BY_GROUP_RARITY = {}
_CARD_INHERENT_SKILLS = {}
_SUPPORT_HINT_POOL_DICT = {}

# _SKILL_CATALOG tuple indexes.
_SKILL_GROUP_ID = 0
_SKILL_RARITY = 1
_SKILL_GROUP_RATE = 2
_SKILL_CATEGORY = 3
_SKILL_DISPLAY_ORDER = 4


def _refresh_query_catalogs(force=False, fingerprint=None):
    """Load frequently queried skill/card data through one SQLite connection."""
    global _QUERY_CATALOG_FINGERPRINT
    global _SKILL_CATALOG
    global _SKILLS_BY_GROUP
    global _SKILLS_BY_GROUP_RARITY
    global _CARD_INHERENT_SKILLS
    global _SUPPORT_HINT_POOL_DICT

    with _MDB_CACHE_LOCK:
        if not force and _QUERY_CATALOG_FINGERPRINT is not None:
            return
        if fingerprint is None:
            fingerprint = _get_db_fingerprint()

        with Connection() as (_, cursor):
            cursor.execute(
                """SELECT id, group_id, rarity, group_rate, skill_category, disp_order
                FROM skill_data"""
            )
            skill_rows = cursor.fetchall()
            cursor.execute(
                """SELECT cd.id, ass.skill_id, ass.need_rank
                FROM card_data cd
                JOIN available_skill_set ass
                  ON cd.available_skill_set_id = ass.available_skill_set_id"""
            )
            card_rows = cursor.fetchall()
            try:
                cursor.execute(
                    """SELECT smhg.support_card_id,
                              smhg.hint_value_1,
                              td.text,
                              smhg.hint_value_2,
                              sd.group_id,
                              sd.rarity,
                              sd.group_rate,
                              sd.disp_order
                       FROM single_mode_hint_gain smhg
                       JOIN skill_data sd
                         ON sd.id = smhg.hint_value_1
                       JOIN text_data td
                         ON td.category = 47
                        AND td."index" = smhg.hint_value_1
                       WHERE smhg.hint_gain_type = 0
                       ORDER BY smhg.support_card_id, smhg.hint_group, smhg.id"""
                )
                support_hint_rows = cursor.fetchall()
            except sqlite3.OperationalError:
                # Some test fixtures and older master-data snapshots do not
                # contain the support hint catalog. The other hot-query
                # catalogs remain useful and should still be published.
                support_hint_rows = []

        skill_catalog = {}
        skills_by_group = {}
        skills_by_group_rarity = {}
        for skill_id, group_id, rarity, group_rate, category, disp_order in skill_rows:
            skill_catalog[skill_id] = (
                group_id,
                rarity,
                group_rate,
                category,
                disp_order,
            )
            if group_rate is not None and group_rate > 0:
                group_row = (group_rate, skill_id, category)
                skills_by_group.setdefault(group_id, []).append(group_row)
                skills_by_group_rarity.setdefault((group_id, rarity), []).append(group_row)

        for group_rows in skills_by_group.values():
            group_rows.sort(key=lambda row: (row[0], row[1]))
        for group_rows in skills_by_group_rarity.values():
            group_rows.sort(key=lambda row: (row[0], row[1]))

        card_inherent_skills = {}
        for card_id, skill_id, need_rank in card_rows:
            card_inherent_skills.setdefault(card_id, []).append((skill_id, need_rank))

        support_hint_pool_dict = {}
        for (
            support_card_id,
            skill_id,
            name,
            granted_level,
            group_id,
            rarity,
            group_rate,
            display_order,
        ) in support_hint_rows:
            entry = {
                "skill_id": skill_id,
                "name": name.replace("\\n", "") if isinstance(name, str) else name,
                "granted_level": granted_level,
                "group_id": group_id,
                "rarity": rarity,
                "group_rate": group_rate,
                "display_order": display_order,
            }
            support_hint_pool_dict.setdefault(support_card_id, []).append(entry)
        support_hint_pool_dict = {
            support_card_id: tuple(entries)
            for support_card_id, entries in support_hint_pool_dict.items()
        }

        # Publish complete snapshots only after all queries and processing
        # succeed. Readers therefore see either the old catalog or the new one.
        _SKILL_CATALOG = skill_catalog
        _SKILLS_BY_GROUP = skills_by_group
        _SKILLS_BY_GROUP_RARITY = skills_by_group_rarity
        _CARD_INHERENT_SKILLS = card_inherent_skills
        _SUPPORT_HINT_POOL_DICT = support_hint_pool_dict
        _QUERY_CATALOG_FINGERPRINT = fingerprint


def get_support_hint_pool_dict(force=False):
    """Return display-ready hint pools keyed by the exact support-card ID."""
    with _MDB_CACHE_LOCK:
        _refresh_query_catalogs(force=force)
        return _SUPPORT_HINT_POOL_DICT


def _get_event_titles_special(story_id, card_id):
    # Determine if it's a L'Arc special outfit event.
    # First, determine if there is a dress icon.
    event_titles = _get_event_titles_default(story_id)

    with Connection() as (_, cursor):
        cursor.execute(
            """SELECT event_title_dress_icon FROM single_mode_story_data WHERE story_id = ? AND card_id = ? LIMIT 1""",
            (story_id, card_id)
        )
        row = cursor.fetchone()
        if row is None:
            return event_titles

        dress_icon = row[0]

        if dress_icon == 0:
            return event_titles
        
        # Now match up the events.
        cursor.execute(
            """SELECT story_id FROM single_mode_story_data WHERE event_title_dress_icon = ? ORDER BY id""",
            (dress_icon,)
        )
        rows = cursor.fetchall()

        if not rows:
            return event_titles
        
        default_ids = []
        larc_ids = []

        for row in rows:
            str_id = str(row[0])
            if str_id.startswith("40"):
                larc_ids.append(str_id)
            elif str_id.startswith("50"):
                default_ids.append(str_id)

        # Scenario stories also use 40-prefixed IDs. Some dress-icon groups
        # therefore have no corresponding default 50-prefixed story at all;
        # they are not L'Arc outfit variants and must keep their own title.
        if not default_ids:
            return event_titles

        try:
            index = larc_ids.index(str(story_id)) % len(default_ids)
        except ValueError:
            return event_titles
        
        if index >= len(default_ids):
            return event_titles
        
        event_titles.extend(_get_event_titles_default(default_ids[index]))
        return event_titles


def _get_event_titles_default(story_id):
    with Connection() as (_, cursor):
        cursor.execute(
            """SELECT text FROM text_data WHERE category = 181 AND "index" = ? LIMIT 1""",
            (story_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return [None]
        
        return [row[0]]
    
def convert_short_story_id(story_id):
    with Connection() as (_, cursor):
        cursor.execute(
            """SELECT story_id FROM single_mode_story_data WHERE short_story_id = ? LIMIT 1""",
            (story_id,)
        )
        row = cursor.fetchone()

        if row is None:
            return story_id
        
        return row[0]

def get_event_titles(story_id, card_id):
    story_id = convert_short_story_id(story_id)

    str_event_title = str(story_id)

    if str_event_title.startswith("40"):
        event_titles = _get_event_titles_special(story_id, card_id)

    else:
        event_titles = _get_event_titles_default(story_id)
    
    event_titles = [event_title for event_title in event_titles if event_title]

    # Replace the line breaks with spaces
    event_titles = [event_title.replace("\\n", " ") for event_title in event_titles]

    if not event_titles:
        event_titles = ["NO EVENT TITLE"]
        logger.warning(f"Event title not found for story_id: {story_id}")  # TODO: Fix stories that aren't found.

    return event_titles

def get_status_name(status_id):
    with Connection() as (_, cursor):
        cursor.execute(
            """SELECT text FROM text_data WHERE category = 142 AND "index" = ? LIMIT 1""",
            (status_id,)
        )
        status_name = cursor.fetchone()[0]
    return status_name

def get_skill_name(skill_id):
    with Connection() as (_, cursor):
        cursor.execute(
            """SELECT text FROM text_data WHERE category = 47 AND "index" = ? LIMIT 1""",
            (skill_id,)
        )
        skill_name = cursor.fetchone()[0]
    return skill_name

def get_skill_rarity(skill_id):
    with _MDB_CACHE_LOCK:
        _refresh_query_catalogs()
        skill_data = _SKILL_CATALOG.get(skill_id)
        return skill_data[_SKILL_RARITY] if skill_data else 1

def get_skill_hint_name(group_id, rarity):
    with Connection() as (_, cursor):
        cursor.execute(
            """SELECT td.text FROM skill_data sd INNER JOIN text_data td ON sd.id = td."index" AND td.category = 47 WHERE sd.group_id = ? AND sd.rarity = ? LIMIT 1""",
            (group_id, rarity)
        )
        skill_hint_name = cursor.fetchone()[0]
    return skill_hint_name

def get_race_program_name(program_id):
    with Connection() as (_, cursor):
        cursor.execute(
            """SELECT t.text FROM single_mode_program s INNER JOIN text_data t ON s.race_instance_id = t."index" AND t.category = 28 WHERE s.id = ? LIMIT 1""",
            (program_id,)
        )
        program_name = cursor.fetchone()[0]
    return program_name

def get_outfit_name(card_id):
    with Connection() as (_, cursor):
        cursor.execute(
            """SELECT text FROM text_data WHERE category = 14 AND "index" = ? LIMIT 1""",
            (card_id,)
        )
        outfit_name = cursor.fetchone()[0]
    return outfit_name

def get_support_card_string(support_id):
    with Connection() as (_, cursor):
        cursor.execute(
            """SELECT rarity, command_id, support_card_type, chara_id FROM support_card_data WHERE id = ? LIMIT 1""",
            (support_id,)
        )
        row = cursor.fetchone()

        if row is None:
            logger.warning(f"Support card not found for id: {support_id}")
            return "SUPPORT CARD NOT FOUND"

    return create_support_card_string(*row)

EVENT_TITLE_DICT = {}
def get_event_title_dict(force=False):
    global EVENT_TITLE_DICT
    if force or not EVENT_TITLE_DICT:
        with Connection() as (_, cursor):
            cursor.execute(
                """SELECT s.story_id, s.short_story_id, t.text FROM text_data t JOIN single_mode_story_data s ON t."index" = s.story_id WHERE category = 181"""
            )
            rows = cursor.fetchall()

        out = {}
        for row in rows:
            out[row[0]] = row[2]
            if row[1] != 0:
                out[row[1]] = row[2]
        EVENT_TITLE_DICT.update(out)
    return EVENT_TITLE_DICT

RACE_PROGRAM_NAME_DICT = {}
def get_race_program_name_dict(force=False):
    global RACE_PROGRAM_NAME_DICT
    if force or not RACE_PROGRAM_NAME_DICT:
        with Connection() as (_, cursor):
            cursor.execute(
                """SELECT s.id, t.text FROM single_mode_program s INNER JOIN text_data t ON s.race_instance_id = t."index" AND t.category = 28"""
            )
            rows = cursor.fetchall()
        RACE_PROGRAM_NAME_DICT.update({row[0]: row[1] for row in rows})
    return RACE_PROGRAM_NAME_DICT

SKILL_NAME_DICT = {}
def get_skill_name_dict(force=False):
    global SKILL_NAME_DICT
    if force or not SKILL_NAME_DICT:
        with Connection() as (_, cursor):
            cursor.execute(
                """SELECT sd.id, td.text FROM skill_data sd INNER JOIN text_data td ON sd.id = td."index" AND td.category = 47"""
            )
            rows = cursor.fetchall()

        SKILL_NAME_DICT.update({row[0]: row[1] for row in rows})

    return SKILL_NAME_DICT

SKILL_COSTS_DICT = {}
def get_skill_costs_dict(force=False):
    global SKILL_COSTS_DICT
    if force or not SKILL_COSTS_DICT:
        with Connection() as (_, cursor):
            cursor.execute("SELECT id, need_skill_point FROM single_mode_skill_need_point")
            rows = cursor.fetchall()

        # Update the global cache
        SKILL_COSTS_DICT.update({str(row[0]): row[1] for row in rows})

    return SKILL_COSTS_DICT

SKILL_SCORE_DICT = {}
def get_skill_score_dict(force=False):
    global SKILL_SCORE_DICT
    if force or not SKILL_SCORE_DICT:
        with Connection() as (_, cursor):
            try:
                cursor.execute("SELECT id, grade_value FROM skill_data WHERE grade_value > 0")
                rows = cursor.fetchall()
                SKILL_SCORE_DICT.update({row[0]: row[1] for row in rows})
            except Exception as e:
                logger.error(f"Error fetching skill scores: {e}")
    return SKILL_SCORE_DICT




SKILL_CONDITIONS_DICT = {}
def get_skill_conditions_dict(force=False):
    global SKILL_CONDITIONS_DICT
    if force or not SKILL_CONDITIONS_DICT:
        with Connection() as (_, cursor):
            # Fetch condition_1 from your local DB
            cursor.execute("SELECT id, condition_1 FROM skill_data;")
            rows = cursor.fetchall()

        if rows:
            tmp = {}
            for row in rows:
                skill_id = row[0]
                logic_str = row[1]

                if logic_str:
                    # Apply logic from generate_skill_data.py: split by @, wrap in [], join with OR
                    blocks = str(logic_str).split('@')
                    formatted_blocks = [f"{b.strip().replace('&', ' & ')}" for b in blocks if b.strip()]
                    tmp[skill_id] = " OR ".join(formatted_blocks)
                else:
                    tmp[skill_id] = "Guaranteed"

            SKILL_CONDITIONS_DICT.update(tmp)

    return SKILL_CONDITIONS_DICT

SKILL_EFFECTS_DICT = {}
def get_skill_effects_dict(force=False):
    global SKILL_EFFECTS_DICT
    if force or not SKILL_EFFECTS_DICT:
        EFFECT_NAMES = {
            0: "Noop",
            1: "SpeedUp",
            2: "StaminaUp",
            3: "PowerUp",
            4: "GutsUp",
            5: "WisdomUp",
            8: "Vision",
            9: "Recovery",
            10: "MultiplyStartDelay",
            13: "ExtendKakari",
            14: "SetStartDelay",
            21: "CurrentSpeed",
            22: "CurrentSpeedWithNaturalDeceleration",
            27: "TargetSpeed",
            29: "ModifyKakariChance",
            31: "Accel",
            37: "ActivateRandomGold",
            42: "ExtendEvolvedDuration"
        }

        SKILL_EFFECTS_DICT = {}

        with Connection() as (_, cursor):
            try:
                cursor.execute(
                    """SELECT id, 
                    ability_type_1_1, float_ability_value_1_1,
                    ability_type_1_2, float_ability_value_1_2,
                    ability_type_1_3, float_ability_value_1_3,
                    float_ability_time_1,
                    ability_type_2_1, float_ability_value_2_1,
                    ability_type_2_2, float_ability_value_2_2,
                    ability_type_2_2, float_ability_value_2_2,
                    ability_type_2_3, float_ability_value_2_3,
                    float_ability_time_2
                    FROM skill_data"""
                )
                rows = cursor.fetchall()

                for r in rows:
                    sid = str(r[0])
                    eff_strs = []
                    max_duration = 0.0

                    # Phase 1
                    for i in range(1, 7, 2):
                        a_type = r[i]
                        a_val = r[i+1]
                        if a_type != 0:
                            eff_name = EFFECT_NAMES.get(a_type, f"Type {a_type}")
                            eff_strs.append(f"{eff_name} {a_val}")
                    if r[7] > max_duration:
                        max_duration = r[7]

                    # Phase 2
                    for i in range(8, 14, 2):
                        a_type = r[i]
                        a_val = r[i+1]
                        if a_type != 0:
                            eff_name = EFFECT_NAMES.get(a_type, f"Type {a_type}")
                            eff_strs.append(f"{eff_name} {a_val}")
                    if r[14] > max_duration:
                        max_duration = r[14]

                    if not eff_strs:
                        SKILL_EFFECTS_DICT[sid] = {"effects": "No Effects", "conditions": ""}
                        continue

                    eff_text = ", ".join(eff_strs)
                    if max_duration > 0:
                        dur_text = f"Duration {max_duration / 10000.0}s"
                        summary = f"{eff_text}, {dur_text}"
                    else:
                        summary = eff_text

                    SKILL_EFFECTS_DICT[sid] = {
                        "effects": summary,
                        "conditions": ""
                    }
            except sqlite3.OperationalError as e:
                logger.error(f"Failed to parse skill_data logic from DB: {e}")
                return {}

    return SKILL_EFFECTS_DICT

SKILL_HINT_NAME_DICT = {}
def get_skill_hint_name_dict(force=False):
    global SKILL_HINT_NAME_DICT
    if force or not SKILL_HINT_NAME_DICT:
        with Connection() as (_, cursor):
            cursor.execute(
                """SELECT sd.group_id, sd.rarity, td.text FROM skill_data sd INNER JOIN text_data td ON sd.id = td."index" AND td.category = 47"""
            )
            rows = cursor.fetchall()
        
        SKILL_HINT_NAME_DICT.update({(row[0], row[1]): row[2] for row in rows})

    return SKILL_HINT_NAME_DICT

STATUS_NAME_DICT = {}
def get_status_name_dict(force=False):
    global STATUS_NAME_DICT
    if force or not STATUS_NAME_DICT:
        with Connection() as (_, cursor):
            cursor.execute(
                """SELECT "index", text FROM text_data WHERE category = 142"""
            )
            rows = cursor.fetchall()
        
        STATUS_NAME_DICT.update({row[0]: row[1] for row in rows})

    return STATUS_NAME_DICT

OUTFIT_NAME_DICT = {}
def get_outfit_name_dict(force=False):
    global OUTFIT_NAME_DICT
    if force or not OUTFIT_NAME_DICT:
        with Connection() as (_, cursor):
            cursor.execute(
                """SELECT "index", text FROM text_data WHERE category = 5"""
            )
            rows = cursor.fetchall()
        
        OUTFIT_NAME_DICT.update({row[0]: row[1] for row in rows})

    return OUTFIT_NAME_DICT

SUPPORT_CARD_DICT = {}
def get_support_card_dict(force=False):
    global SUPPORT_CARD_DICT
    if force or not SUPPORT_CARD_DICT:
        with Connection() as (_, cursor):
            cursor.execute(
                """SELECT id, rarity, command_id, support_card_type, chara_id FROM support_card_data"""
            )
            rows = cursor.fetchall()
        SUPPORT_CARD_DICT.update({row[0]: row[1:] for row in rows})
    return SUPPORT_CARD_DICT

def get_support_card_type(support_data):
    return constants.SUPPORT_CARD_TYPE_DICT[(support_data[1], support_data[2])]

SUPPORT_CARD_STRING_DICT = {}
def get_support_card_string_dict(force=False):
    global SUPPORT_CARD_STRING_DICT
    if force or not SUPPORT_CARD_STRING_DICT:
        support_card_dict = get_support_card_dict()

        # Forcefully clear the character name dict cache if needed.
        util.get_character_name_dict(force=force)

        SUPPORT_CARD_STRING_DICT.update({id: create_support_card_string(*data) for id, data in support_card_dict.items()})
    
    return SUPPORT_CARD_STRING_DICT

CHARA_NAME_DICT = {}
def get_chara_name_dict(force=False):
    global CHARA_NAME_DICT
    if force or not CHARA_NAME_DICT:
        with Connection() as (_, cursor):
            cursor.execute(
                """SELECT "index", text FROM text_data WHERE category = 170"""
            )
            rows = cursor.fetchall()

        CHARA_NAME_DICT.update({row[0]: row[1] for row in rows})
    
    return CHARA_NAME_DICT


RACE_NAME_DICT = {}
def get_race_name_dict(force=False):
    global RACE_NAME_DICT
    if force or not RACE_NAME_DICT:
        with Connection() as (_, cursor):
            cursor.execute(
                """SELECT smp.id, text FROM single_mode_program smp JOIN text_data td on td."index" = smp.race_instance_id WHERE category = 28"""
            )
            rows = cursor.fetchall()

        RACE_NAME_DICT.update({row[0]: row[1] for row in rows})

    return RACE_NAME_DICT


RACE_DISTANCE_DICT = {}
def get_race_distance_dict(force=False):
    global RACE_DISTANCE_DICT
    if force or not RACE_DISTANCE_DICT:
        with Connection() as (_, cursor):
            cursor.execute(
                """SELECT smp.id, rcs.distance FROM single_mode_program smp JOIN race_instance ri on smp.race_instance_id = ri.id JOIN race r on ri.race_id = r.id JOIN race_course_set rcs on r.course_set = rcs.id"""
            )
            rows = cursor.fetchall()

        RACE_DISTANCE_DICT.update({row[0]: row[1] for row in rows})

    return RACE_DISTANCE_DICT


RACE_SURFACE_DICT = {}
def get_race_surface_dict(force=False):
    global RACE_SURFACE_DICT
    if force or not RACE_SURFACE_DICT:
        with Connection() as (_, cursor):
            cursor.execute(
                """SELECT smp.id, rcs.ground FROM single_mode_program smp JOIN race_instance ri on smp.race_instance_id = ri.id JOIN race r on ri.race_id = r.id JOIN race_course_set rcs on r.course_set = rcs.id"""
            )
            rows = cursor.fetchall()

        RACE_SURFACE_DICT.update({row[0]: row[1] for row in rows})

    return RACE_SURFACE_DICT



MANT_ITEM_STRING_DICT = {}
def get_mant_item_string_dict(force=False):
    global MANT_ITEM_STRING_DICT
    if force or not MANT_ITEM_STRING_DICT:
        with Connection() as (_, cursor):
            try:
                cursor.execute(
                    """SELECT "index", text FROM text_data WHERE category = 225"""
                )
                rows = cursor.fetchall()
                MANT_ITEM_STRING_DICT.update({row[0]: row[1] for row in rows})
            except sqlite3.OperationalError as e:
                logger.error(f"get_mant_item_string_dict failed: {e}\n{traceback.format_exc()}")


    return MANT_ITEM_STRING_DICT

GL_LESSON_DICT = {}
def get_gl_lesson_dict(force=False):
    global GL_LESSON_DICT
    if force or not GL_LESSON_DICT:
        with Connection() as (_, cursor):
            try:
                cursor.execute(
                    """SELECT s.id, t.text, s.square_type FROM single_mode_live_square s JOIN text_data t ON t."index" = s.square_title_text_id AND t.category = 209"""
                )
                rows = cursor.fetchall()

                GL_LESSON_DICT.update({row[0]: (row[1], row[2]) for row in rows})
            except sqlite3.OperationalError as e:
                logger.error( f"get_gl_lesson_dict failed: {e}\n{traceback.format_exc()}")
    
    return GL_LESSON_DICT

GROUP_CARD_EFFECT_IDS = []
def get_group_card_effect_ids(force=False):
    global GROUP_CARD_EFFECT_IDS
    if force or not GROUP_CARD_EFFECT_IDS:
        with Connection() as (_, cursor):
            try:
                cursor.execute(
                    """SELECT id, effect_id FROM support_card_data WHERE support_card_type = 3"""
                )
                rows = cursor.fetchall()
                if rows:
                    GROUP_CARD_EFFECT_IDS[:] = rows  # Thanks StellatedCube
            except sqlite3.OperationalError as e:
                logger.error(f"get_group_card_effect_ids failed: {e}\n{traceback.format_exc()}")

    return GROUP_CARD_EFFECT_IDS

def get_program_id_grade(program_id):
    program_data = get_program_id_data(program_id)
    return program_data.get("race_grade") if program_data else None


PROGRAM_ID_DICT = {}
def get_program_id_dict(force=False):
    global PROGRAM_ID_DICT
    if not PROGRAM_ID_DICT or force:
        with Connection() as (_, cursor):
            try:
                cursor.execute(
                    """
                    SELECT
                        smp.*,
                        r.grade AS race_grade,
                        r.thumbnail_id AS race_thumbnail_id
                    FROM single_mode_program smp
                    LEFT JOIN race_instance ri ON smp.race_instance_id = ri.id
                    LEFT JOIN race r ON ri.race_id = r.id;
                    """
                )
                rows = cursor.fetchall()
                columns = get_columns(cursor)
                if not rows:
                    temp = None
                else:
                    races = rows_to_dict(rows, columns)
                    PROGRAM_ID_DICT.update( { race["id"] : race for race in races } )
            except sqlite3.OperationalError as e:
                logger.error( f"get_program_id_dict failed: {e}\n{traceback.format_exc()}")

    return PROGRAM_ID_DICT

def get_program_id_data(program_id):
    return get_program_id_dict().get(program_id)

SKILL_ID_DICT = {}
def get_skill_id_dict(force=False):
    global SKILL_ID_DICT
    if force or not SKILL_ID_DICT:
        with Connection() as (_, cursor):
            try:
                cursor.execute(
                    """SELECT id, group_id, rarity, unique_skill_id_1 FROM skill_data ORDER BY group_rate DESC;"""
                )
                rows = cursor.fetchall()
            except sqlite3.OperationalError as e:
                logger.error(f"get_group_card_effect_ids failed: {e}\n{traceback.format_exc()}")
                util.show_error_box_no_report("Error","Failed to read the master.mdb file.<br>Try restarting Uma Launcher after the game updates.<br>Uma Launcher will now close.")
                if gui.THREADER:
                    gui.THREADER.stop()
                rows = []
        if rows:
            tmp = {}
            for row in rows:
                true_id = row[0]
                # if row[3] != 0:
                #     true_id = row[3]
                
                skill_key = (row[1], row[2])
                if skill_key not in tmp:
                    tmp[skill_key] = true_id
            SKILL_ID_DICT.update(tmp)
    
    return SKILL_ID_DICT

def get_deck_race_bonus(deck_array):
    if not deck_array:
        return 0
    
    total_rb = 0
    with Connection() as (_, cur):
        levels = ["init", "limit_lv5", "limit_lv10", "limit_lv15", "limit_lv20", "limit_lv25", "limit_lv30", "limit_lv35", "limit_lv40", "limit_lv45", "limit_lv50"]
        for card in deck_array:
            cid = card.get("support_card_id")
            lb = card.get("limit_break_count", 0)
            if not cid: continue
            
            cur.execute("SELECT rarity, effect_id FROM support_card_data WHERE id=?", (cid,))
            data = cur.fetchone()
            if not data: continue
            
            rarity, effect_id = data
            if effect_id == 0: effect_id = cid
            
            base_levels = {1: 20, 2: 25, 3: 30}
            max_lv = base_levels.get(rarity, 30) + (5 * lb)
            
            cur.execute("SELECT init, limit_lv5, limit_lv10, limit_lv15, limit_lv20, limit_lv25, limit_lv30, limit_lv35, limit_lv40, limit_lv45, limit_lv50 FROM support_card_effect_table WHERE id=? AND type=15", (effect_id,))
            row = cur.fetchone()
            
            rb_value = 0
            if row:
                for i, lv_name in enumerate(levels):
                    current_col_lv = 0 if i == 0 else int(lv_name.replace("limit_lv", ""))
                    if current_col_lv > max_lv:
                        break
                    val = row[i]
                    if val != -1:
                        rb_value = val
                        
            try:
                cur.execute("SELECT * FROM support_card_unique_effect WHERE id=?", (cid,))
                u_row = cur.fetchone()
                if u_row:
                    col_names = [desc[0] for desc in cur.description]
                    u_data = dict(zip(col_names, u_row))
                    if max_lv >= u_data.get('lv', 0):
                        for i in range(5):
                            t_col = f'type_{i}'
                            v_col = f'value_{i}'
                            if t_col in u_data and u_data[t_col] == 15:
                                rb_value += u_data.get(v_col, 0)
            except sqlite3.OperationalError:
                pass
                
            total_rb += rb_value
            
    return total_rb

def get_card_inherent_skills(card_id, level=99):
    with _MDB_CACHE_LOCK:
        _refresh_query_catalogs()
        return [
            skill_id
            for skill_id, need_rank in _CARD_INHERENT_SKILLS.get(card_id, ())
            if need_rank is not None and need_rank <= level
        ]

def sort_skills_by_display_order(skill_id_list):
    with _MDB_CACHE_LOCK:
        _refresh_query_catalogs()
        requested_ids = set(skill_id_list)
        rows = [
            (skill_id, skill_data)
            for skill_id, skill_data in _SKILL_CATALOG.items()
            if skill_id in requested_ids
        ]

    if not rows:
        return None

    rows.sort(
        key=lambda row: (
            row[1][_SKILL_DISPLAY_ORDER] is not None,
            row[1][_SKILL_DISPLAY_ORDER] or 0,
            row[0],
        )
    )
    return [row[0] for row in rows]


def determine_skill_id_from_group_id(group_id, rarity, skills_id_list):
    with _MDB_CACHE_LOCK:
        _refresh_query_catalogs()
        rows = _SKILLS_BY_GROUP_RARITY.get((group_id, rarity), ())

    if not rows:
        return None

    skill_id = None
    skill_category = None

    for _, row_skill_id, row_skill_category in rows:
        skill_id = row_skill_id
        skill_category = row_skill_category

        if skill_id not in skills_id_list:
            break
        else:
            skills_id_list.remove(skill_id)

    # Uniques
    if skill_id is not None and skill_category == 5 and 100000 <= skill_id < 300000:
        skill_id += 800000

    return skill_id

def get_prerequisite_skill_ids(skill_id):
    true_id = skill_id
    if 900000 <= true_id < 1000000:
        true_id -= 800000

    with _MDB_CACHE_LOCK:
        _refresh_query_catalogs()
        skill_data = _SKILL_CATALOG.get(true_id)
        if not skill_data:
            return []

        group_id = skill_data[_SKILL_GROUP_ID]
        group_rate = skill_data[_SKILL_GROUP_RATE]
        if group_rate is None:
            return []
        rows = [
            row
            for row in _SKILLS_BY_GROUP.get(group_id, ())
            if row[0] < group_rate
        ]

    prereqs = []
    for _, pid, pcat in rows:
        if pcat == 5 and 100000 <= pid < 300000:
            pid += 800000
        prereqs.append(pid)

    return prereqs


def get_next_skill_id_in_chain(skill_id):
    """Return the next positive-rate skill defined by the same master group."""
    true_id = skill_id
    if 900000 <= true_id < 1000000:
        true_id -= 800000

    with _MDB_CACHE_LOCK:
        _refresh_query_catalogs()
        skill_data = _SKILL_CATALOG.get(true_id)
        if not skill_data:
            return None

        group_id = skill_data[_SKILL_GROUP_ID]
        group_rate = skill_data[_SKILL_GROUP_RATE]
        if group_rate is None:
            return None

        next_row = next(
            (
                row
                for row in _SKILLS_BY_GROUP.get(group_id, ())
                if row[0] > group_rate
            ),
            None,
        )

    if next_row is None:
        return None

    _, next_id, next_category = next_row
    if next_category == 5 and 100000 <= next_id < 300000:
        next_id += 800000
    return next_id

DOUBLE_CIRCLE_UPGRADE_DICT = {}
def get_double_circle_upgrade_dict(force=False):
    global DOUBLE_CIRCLE_UPGRADE_DICT
    if force or not DOUBLE_CIRCLE_UPGRADE_DICT:
        with Connection() as (_, cursor):
            try:
                cursor.execute(
                    """
                    SELECT base.id, upgrade.id
                    FROM skill_data base
                    JOIN skill_data upgrade
                        ON base.group_id = upgrade.group_id
                    JOIN single_mode_skill_need_point upgrade_cost
                        ON upgrade_cost.id = upgrade.id
                    WHERE base.group_rate = 1
                        AND upgrade.group_rate = 2
                        AND base.rarity = 1
                        AND upgrade.rarity = 1
                        AND base.skill_category != 5
                        AND upgrade.skill_category != 5
                        AND base.grade_value > 0
                        AND upgrade.grade_value > 0
                    """
                )
                rows = cursor.fetchall()
            except sqlite3.OperationalError as e:
                logger.error(f"get_double_circle_upgrade_dict failed: {e}\\n{traceback.format_exc()}")
                rows = []

        if rows:
            DOUBLE_CIRCLE_UPGRADE_DICT.clear()
            DOUBLE_CIRCLE_UPGRADE_DICT.update({row[0]: row[1] for row in rows})
    return DOUBLE_CIRCLE_UPGRADE_DICT

GROUP_ID_DICT = {}
def get_group_id_dict(force=False):
    global GROUP_ID_DICT
    if force or not GROUP_ID_DICT:
        with Connection() as (_, cursor):
            try:
                cursor.execute("SELECT id, group_id FROM skill_data")
                rows = cursor.fetchall()
            except sqlite3.OperationalError as e:
                logger.error(f"get_group_id_dict failed: {e}\\n{traceback.format_exc()}")
                rows = []
        if rows:
            tmp = {}
            for row in rows:
                tmp[str(row[0])] = row[1]
            GROUP_ID_DICT.update(tmp)
    return GROUP_ID_DICT

def get_total_minigame_plushies(force=False):
    with Connection() as (_, cursor):
        cursor.execute(
            """SELECT chara_id FROM card_data c WHERE default_rarity != 0;"""
        )
        rows = cursor.fetchall()
    
    total_charas = set()
    total_plushies = len(rows)

    for row in rows:
        total_charas.add(row[0])
    
    return 3 * (total_plushies + len(total_charas))

def get_uaf_required_rank_for_turn(force=False):
    with Connection() as (_, cursor):
        cursor.execute(
            "SELECT turn,win_sport_rank FROM single_mode_sport_competition"
        )
        rows = cursor.fetchall()
    
    if not rows:
        return None
        
    return rows

def get_uaf_training_effects(force=False):
    with Connection() as (_, cursor):
        cursor.execute(
            "SELECT id, effect_value_2 FROM single_mode_sport_compe_effect"
        )
        rows = cursor.fetchall()
    
    if not rows:
        return None

    # Convert rows to a dictionary
    effects_map = {row[0]: row[1] for row in rows}
    return effects_map

def get_cooking_success_rate(power: int) -> int:
    with Connection() as (_, cursor):
        cursor.execute(
            "SELECT success_rate FROM single_mode_cook_success_odds WHERE ? BETWEEN power_min AND power_max",
            (power,)
        )
        row = cursor.fetchone()

    if not row:
        return 0
    
    return row[0]

def get_cooking_tasting_success_thresholds(turn_num: int) -> list[int]:
    with Connection() as (_, cursor):
        cursor.execute(
            "SELECT success_num, great_success_num FROM single_mode_cook_power_data WHERE ? < turn_num",
            (turn_num,)
        )
    
        row = cursor.fetchone()

    if not row:
        return [0, 0]
    
    return [row[0], row[1]]

def get_cooking_vegetable_max_count(veg_id: int, veg_lv: int) -> int:
    # Get the max count of a vegetable at specified level.

    with Connection() as (_, cursor):
        cursor.execute(
            """
            SELECT e.effect_value_2
            FROM single_mode_cook_garden_effect e
            JOIN single_mode_cook_garden_level l on l.effect_group_id = e.effect_group_id
            WHERE l.facility_id = ? AND l.facility_lv = ? AND e.effect_type == 110
            """,
            (veg_id, veg_lv)
        )
        row = cursor.fetchone()
    
    if not row:
        return 0
    
    return row[0]


SINGLE_MODE_UNIQUE_CHARA_DICT = {}
def get_single_mode_unique_chara_dict(force=False):
    global SINGLE_MODE_UNIQUE_CHARA_DICT
    if force or not SINGLE_MODE_UNIQUE_CHARA_DICT:
        with Connection() as (_, cursor):
            cursor.execute(
                """SELECT scenario_id, partner_id, chara_id FROM single_mode_unique_chara;"""
            )
            rows = cursor.fetchall()

        tmp_dict = {}
        for row in rows:
            if row[0] not in tmp_dict:
                tmp_dict[row[0]] = {}
            
            tmp_dict[row[0]][row[1]] = row[2]
        
        SINGLE_MODE_UNIQUE_CHARA_DICT.update(tmp_dict)

    return SINGLE_MODE_UNIQUE_CHARA_DICT


def _clear_update_caches():
    """Remove stale rows before UPDATE_FUNCS repopulates its caches."""
    global _QUERY_CATALOG_FINGERPRINT
    global _SKILL_CATALOG
    global _SKILLS_BY_GROUP
    global _SKILLS_BY_GROUP_RARITY
    global _CARD_INHERENT_SKILLS
    global _SUPPORT_HINT_POOL_DICT

    for cache in (
        CHARA_NAME_DICT,
        EVENT_TITLE_DICT,
        RACE_PROGRAM_NAME_DICT,
        SKILL_NAME_DICT,
        SKILL_HINT_NAME_DICT,
        STATUS_NAME_DICT,
        OUTFIT_NAME_DICT,
        SUPPORT_CARD_DICT,
        SUPPORT_CARD_STRING_DICT,
        MANT_ITEM_STRING_DICT,
        GL_LESSON_DICT,
        GROUP_CARD_EFFECT_IDS,
        SKILL_ID_DICT,
        DOUBLE_CIRCLE_UPGRADE_DICT,
        GROUP_ID_DICT,
        SKILL_EFFECTS_DICT,
        SKILL_SCORE_DICT,
        SINGLE_MODE_UNIQUE_CHARA_DICT,
        PROGRAM_ID_DICT,
        RACE_NAME_DICT,
        RACE_DISTANCE_DICT,
        RACE_SURFACE_DICT,
        SKILL_COSTS_DICT,
        SKILL_CONDITIONS_DICT,
    ):
        cache.clear()

    _SKILL_CATALOG = {}
    _SKILLS_BY_GROUP = {}
    _SKILLS_BY_GROUP_RARITY = {}
    _CARD_INHERENT_SKILLS = {}
    _SUPPORT_HINT_POOL_DICT = {}
    _QUERY_CATALOG_FINGERPRINT = None


UPDATE_FUNCS = [
    get_chara_name_dict,
    get_event_title_dict,
    get_race_program_name_dict,
    get_skill_name_dict,
    get_skill_hint_name_dict,
    get_status_name_dict,
    get_outfit_name_dict,
    get_support_card_dict,
    get_support_card_string_dict,
    get_mant_item_string_dict,
    get_gl_lesson_dict,
    get_group_card_effect_ids,
    get_skill_id_dict,
    get_double_circle_upgrade_dict,
    get_group_id_dict,
    get_skill_effects_dict,
    get_skill_score_dict,
    get_single_mode_unique_chara_dict,
    get_program_id_dict,
    get_race_name_dict,
    get_race_distance_dict,
    get_race_surface_dict,
    get_skill_costs_dict,
    get_skill_conditions_dict
]

def has_carotene_table():
    with Connection() as (_, cursor):
        cursor.execute(
            """SELECT name FROM sqlite_master WHERE type='table' AND name='carotene';"""
        )
        row = cursor.fetchone()
    
    if row:
        return True
    return False
