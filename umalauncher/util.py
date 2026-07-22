import os
import sys
import base64
import io
import win32event
from SteamPathFinder import get_steam_path, get_game_path
import win32con
import win32process
from PIL import Image
from loguru import logger
import constants

ignore_errors = False

relative_dir = os.path.abspath(os.getcwd())
unpack_dir = relative_dir
is_script = True
if hasattr(sys, "_MEIPASS"):
    unpack_dir = sys._MEIPASS
    is_script = False
    relative_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    os.chdir(relative_dir)
is_debug = is_script

appdata_dir = os.path.expandvars("%AppData%\\Uma-Launcher-Global\\")

if is_script:
    appdata_dir = os.path.join(relative_dir, "appdata")

os.makedirs(appdata_dir, exist_ok=True)

def get_appdata(relative_path):
    """Gets the absolute path of a file relative to the appdata directory.
    """
    return os.path.join(appdata_dir, relative_path)

def get_relative(relative_path):
    """Gets the absolute path of a file relative to the executable's directory.
    """
    return os.path.join(relative_dir, relative_path)

def get_asset(asset_path):
    """Gets the absolute path of an asset relative to the unpack directory.
    """
    path = os.path.join(unpack_dir, asset_path)
    if is_script and not os.path.exists(path):
        source_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            asset_path,
        )
        if os.path.exists(source_path):
            return source_path
    return path




def log_reset():
    logger.remove()
    if is_script:
        logger.add(sys.stderr, level="TRACE")
    return

def log_set_info():
    log_reset()
    logger.add(get_appdata("log.log"), rotation="1 week", compression="zip", retention="1 month", encoding='utf-8', level="INFO")
    return

def log_set_trace():
    log_reset()
    logger.add(get_appdata("log.log"), rotation="1 week", compression="zip", retention="1 month", encoding='utf-8', level="TRACE")
    return

if is_script:
    log_set_trace()
    logger.debug("Running from script, enabling debug logging.")
else:
    log_set_info()


# Import the rest of the modules after logging is set up.
import win32api
import win32gui
import win32con
import traceback
import math
import requests
from pywintypes import error as pywinerror  # pylint: disable=no-name-in-module
from PIL import Image
import mdb
import gui

TRAINING_LOGS_FOLDER = get_appdata("training_logs")

def do_get_request(url, error_title=None, error_message=None, ignore_timeout=False):
    try:
        logger.debug(f"GET request to {url}")
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response
    except Exception:
        logger.warning(f"Failed to connect to {url}")
        logger.warning(traceback.format_exc())
        show_warning_box(
            "Failed to connect to server" if error_title is None else error_title,
            "The requested server could not be reached." if error_message is None else error_message
        )
        return None


def get_game_folder():
    try:
        return get_game_path(get_steam_path(), "3224770", "UmamusumePrettyDerby")
    except (FileNotFoundError, OSError):
        logger.error("Could not locate the Global Steam game directory.")
        logger.error(traceback.format_exc())
        return None

def open_folder(path):
    try:
        os.startfile(path)
    except:
        logger.error(f"Failed to open training logs folder: {path}")



window_handle = None


def get_width_from_height(height, portrait):
    if portrait:
        return math.ceil((height * 0.5626065430) - 6.2123937177)
    return math.ceil((height * 1.7770777107) - 52.7501897551)


def _show_alert_box(error, message, icon):
    gui.show_widget(gui.UmaInfoPopup, error, message, icon)


def show_error_box(error, message, custom_traceback=None):
    logger.error(error)
    logger.error(message)
    traceback_str = traceback.format_exc() if custom_traceback is None else custom_traceback
    logger.error(traceback_str)

    global ignore_errors
    if ignore_errors:
        return

    if traceback_str is None or traceback_str == "" or traceback_str.strip() == "NoneType: None":
        traceback_str = "Stacktrace: " + "".join(traceback.format_stack())
    gui.show_widget(
        gui.UmaErrorPopup,
        error,
        message,
        traceback_str,
        gui.THREADER.settings["unique_id"] if gui.THREADER is not None and gui.THREADER.settings is not None else None,
        gui.ICONS.Critical
    )

def show_error_box_no_report(error, message):
    logger.error(error)
    logger.error(message)

    global ignore_errors
    if ignore_errors:
        return

    _show_alert_box(error, message, gui.ICONS.Critical)


def show_warning_box(error, message):
    logger.warning(f"{error}")
    logger.warning(f"{message}")

    global ignore_errors
    if ignore_errors:
        return
    _show_alert_box(error, message, gui.ICONS.Warning)


def show_info_box(error, message):
    logger.info(f"{error}")
    logger.info(f"{message}")
    _show_alert_box(error, message, gui.ICONS.Information)

def get_process_path(hwnd: int) -> str:
    # Get the process ID of the window
    pid = win32process.GetWindowThreadProcessId(hwnd)[1]
    # Open the process, and get the executable path
    proc_path = win32process.GetModuleFileNameEx(win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid), 0)
    return os.path.abspath(proc_path)


def _get_window_exact(hwnd: int, query: str):
    global window_handle
    if win32gui.IsWindowVisible(hwnd):
        if win32gui.GetWindowText(hwnd) == query:
            logger.debug(f"Found window {query}!")
            window_handle = hwnd


def _get_window_lazy(hwnd: int, query: str):
    global window_handle
    if win32gui.IsWindowVisible(hwnd):
        if query.lower() in win32gui.GetWindowText(hwnd).lower():
            logger.debug(f"Found window {query}!")
            window_handle = hwnd


def _get_window_startswith(hwnd: int, query: str):
    global window_handle
    if win32gui.IsWindowVisible(hwnd):
        if win32gui.GetWindowText(hwnd).startswith(query):
            logger.debug(f"Found window {query}!")
            window_handle = hwnd

def _get_window_by_executable(hwnd: int, query: str):
    global window_handle
    if win32gui.IsWindowVisible(hwnd):
        try:
            proc_path = get_process_path(hwnd)
        except pywinerror:
            return
        executable = os.path.basename(proc_path)
        if executable == query:
            logger.debug(f"Found window {query}!")
            window_handle = hwnd

def _get_window_by_pid( hwnd: int, pid: int):
    global window_handle
    if win32gui.IsWindowVisible(hwnd):
        proc_pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        if proc_pid == pid:
            logger.debug(f"Found window with PID {pid}!")
            window_handle = hwnd

LAZY = _get_window_lazy
EXACT = _get_window_exact
STARTSWITH = _get_window_startswith
EXEC_MATCH = _get_window_by_executable

def get_window_handle(query: str, type=LAZY) -> str:
    global window_handle

    window_handle = None
    win32gui.EnumWindows(type, query)
    return window_handle

def get_window_handle_from_pid( pid: int ) -> str:
    global window_handle
    window_handle = None
    win32gui.EnumWindows( _get_window_by_pid, pid )
    return window_handle

def get_game_handle():
    return get_window_handle("Umamusume", type=EXACT)


def get_position_rgb(image: Image.Image, position: tuple[float,float]) -> tuple[int,int,int]:
    pixel_color = None
    pixel_pos = (round(image.width * position[0]), round(image.height * position[1]))
    try:
        pixel_color = image.getpixel(pixel_pos)
    except IndexError:
        pass
    return pixel_color


def similar_color(col1: tuple[int,int,int], col2: tuple[int,int,int], threshold: int = 32) -> bool:
    total_diff = 0
    for i in range(3):
        total_diff += abs(col1[i] - col2[i])
    return total_diff < threshold


def turn_to_string(turn):
    turn = turn - 1

    if turn < 12:
        turn /= 2
        turn += 6
        turn = math.floor(turn)

    second_half = turn % 2 != 0
    if second_half:
        turn -= 1
    turn /= 2

    month = int(turn) % 12 + 1
    year = math.floor(turn / 12) + 1

    return f"Y{year}, {'Late' if second_half else 'Early'} {constants.MONTH_DICT[month]}"


def get_window_rect(*args, **kwargs):
    try:
        return win32gui.GetWindowRect(*args, **kwargs)
    except pywinerror:
        return None

def move_window(*args, **kwargs):
    try:
        win32gui.MoveWindow(*args, **kwargs)
        return True
    except pywinerror:
        return False

def monitor_from_window(*args, **kwargs):
    try:
        return win32api.MonitorFromWindow(*args, **kwargs)
    except pywinerror:
        return None

def get_monitor_info(*args, **kwargs):
    try:
        return win32api.GetMonitorInfo(*args, **kwargs)
    except pywinerror:
        return None

def show_window(*args, **kwargs):
    try:
        win32gui.ShowWindow(*args, **kwargs)
        return True
    except pywinerror:
        return False

def hide_window_from_taskbar(window_handle):
    try:
        style = win32gui.GetWindowLong(window_handle, win32con.GWL_EXSTYLE)
        style |= win32con.WS_EX_TOOLWINDOW
        win32gui.ShowWindow(window_handle, win32con.SW_HIDE)
        win32gui.SetWindowLong(window_handle, win32con.GWL_EXSTYLE, style)
        return True
    except pywinerror:
        return False

def unhide_window_from_taskbar(window_handle):
    try:
        style = win32gui.GetWindowLong(window_handle, win32con.GWL_EXSTYLE)
        style &= ~win32con.WS_EX_TOOLWINDOW
        win32gui.ShowWindow(window_handle, win32con.SW_SHOW)
        win32gui.SetWindowLong(window_handle, win32con.GWL_EXSTYLE, style)
        return True
    except pywinerror:
        return False


def is_minimized(handle):
    try:
        tup = win32gui.GetWindowPlacement(handle)
        if tup[1] == win32con.SW_SHOWMINIMIZED:
            return True
        return False
    except pywinerror as e:
        logger.warning("Failed to get window placement.")
        logger.warning(e)
        logger.warning(traceback.format_exc())
        # Default to it being minimized as to not save the game window.
        return True

def get_character_name_dict(force=False):
    return mdb.get_chara_name_dict(force=force)

def get_outfit_name_dict(force=False):
    return mdb.get_outfit_name_dict(force=force)

downloaded_race_name_dict = {}
def get_race_name_dict(force=False):
    global downloaded_race_name_dict

    if force or not downloaded_race_name_dict:
        race_name_dict = mdb.get_race_program_name_dict()
        downloaded_race_name_dict.update(race_name_dict)
        
    return downloaded_race_name_dict

def _base36(value):
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    value = int(value)
    if value == 0:
        return "0"
    digits = []
    while value:
        value, remainder = divmod(value, 36)
        digits.append(alphabet[remainder])
    return "".join(reversed(digits))


def create_gametora_helper_url(card_id, scenario_id, support_ids):
    support_ids = list(map(str, support_ids))
    if len(support_ids) < 6:
        logger.error("Support_ids list does not contain 6 items!")
        logger.error(support_ids)
        # Pad it to length of 6 with zeros
        support_ids += ['0'] * (6 - len(support_ids))
    return f"https://gametora.com/umamusume/training-event-helper?deck={_base36(str(card_id) + str(scenario_id))}-{_base36(support_ids[0] + support_ids[1] + support_ids[2])}-{_base36(support_ids[3] + support_ids[4] + support_ids[5])}&server=en".lower()

gm_fragment_dict = {}
def get_gm_fragment_dict(force=False):
    global gm_fragment_dict

    if force or not gm_fragment_dict:
        logger.debug("Loading Grand Master fragment images...")
        tmp_gm_fragment_dict = {}
        for i in range(0, 23):
            fragment_img_path = f"_assets/gm/frag_{i:02}.png"
            asset_path = get_asset(fragment_img_path)

            if not os.path.exists(asset_path):
                continue

            img = Image.open(asset_path)
            img.thumbnail((36, 36))

            # Save the image in memory in PNG format
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
            img.close()

            # Encode PNG image to base64 string
            b64 = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("utf-8")
            tmp_gm_fragment_dict[i] = b64

            buffer.close()
        
        gm_fragment_dict.update(tmp_gm_fragment_dict)
    return gm_fragment_dict

def assets_folder_images_to_dict(folder, size=None):
    img_dict = {}

    assets_folder = get_asset(folder)
    if not os.path.exists(assets_folder):
        logger.error(f"Could not find folder {folder}")
        show_error_box_no_report( "Could not find assets folder", f"Could not find assets folder {folder}. Try restarting Uma Launcher if this is the first launch after an update. Otherwise, report this error to the developer.")
        return img_dict
    for image_path in os.listdir(assets_folder):
        if not image_path.endswith(".png"):
            continue

        img_key = image_path[:-4]

        img = Image.open(os.path.join(assets_folder, image_path))

        if size is not None:
            img.thumbnail(size)

        # Save the image in memory in PNG format
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        img.close()

        # Encode PNG image to base64 string
        b64 = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("utf-8")
        img_dict[img_key] = b64

        buffer.close()

    return img_dict


uaf_sport_image_dict = {}
def get_uaf_sport_image_dict(force=False):
    global uaf_sport_image_dict

    if force or not uaf_sport_image_dict:
        logger.debug("Loading Uma Ability Fragment images...")
        uaf_sport_image_dict.update(assets_folder_images_to_dict("_assets/uaf/sports"))
    return uaf_sport_image_dict

uaf_genre_image_dict = {}
def get_uaf_genre_image_dict(force=False):
    global uaf_genre_image_dict

    if force or not uaf_genre_image_dict:
        logger.debug("Loading Uma Ability Fragment images...")
        uaf_genre_image_dict.update(assets_folder_images_to_dict("_assets/uaf/genres"))
    return uaf_genre_image_dict

gl_token_dict = {}
def get_gl_token_dict(force=False):
    global gl_token_dict

    if force or not gl_token_dict:
        logger.debug("Loading Grand Live token images...")
        token_folder = "_assets/gl/tokens"
        if not os.path.exists(get_asset(token_folder)):
            return gl_token_dict
        gl_token_dict.update(assets_folder_images_to_dict(token_folder, (36, 36)))

    return gl_token_dict

gff_veg_image_dict = {}
def get_gff_veg_image_dict(force=False):
    global gff_veg_image_dict

    if force or not gff_veg_image_dict:
        logger.debug("Loading Vegetable images...")
        gff_veg_image_dict.update(assets_folder_images_to_dict("_assets/gff/vegetables"))
    return gff_veg_image_dict

rmu_image_dict = {}
def get_rmu_image_dict(force=False):
    global rmu_image_dict

    if force or not rmu_image_dict:
        logger.debug("Loading RMU images...")
        rmu_image_dict.update(assets_folder_images_to_dict("_assets/rmu"))
    return rmu_image_dict

dreams_image_dict = {}
def get_dreams_image_dict(force=False):
    global dreams_image_dict

    if force or not dreams_image_dict:
        logger.debug("Loading Beyond Dreams images...")
        dreams_image_dict.update(assets_folder_images_to_dict("_assets/dreams"))
    return dreams_image_dict

mant_image_dict = {}
def get_mant_image_dict(force=False):
    global mant_image_dict

    if force or not mant_image_dict:
        logger.debug("Loading MANT images...")
        mant_image_dict.update(assets_folder_images_to_dict("_assets/mant"))
    return mant_image_dict

GROUP_SUPPORT_ID_TO_PASSION_ZONE_EFFECT_ID_DICT = {}
def get_group_support_id_to_passion_zone_effect_id_dict(force=False):
    global GROUP_SUPPORT_ID_TO_PASSION_ZONE_EFFECT_ID_DICT

    if force or not GROUP_SUPPORT_ID_TO_PASSION_ZONE_EFFECT_ID_DICT:
        cards = mdb.get_group_card_effect_ids()
        GROUP_SUPPORT_ID_TO_PASSION_ZONE_EFFECT_ID_DICT.update({card[0]: card[1] for card in cards})

    return GROUP_SUPPORT_ID_TO_PASSION_ZONE_EFFECT_ID_DICT

def get_game_variant_string():
    return "Global"

commit_hash = None
branch = None
build_date = None
def get_commit_hash(force=False):
    global commit_hash
    if force or commit_hash is None:
        file_path = get_asset("_assets/commit_hash.txt")
        if os.path.exists(file_path):
            commit_hash = open(file_path, 'r').read().strip()
    if commit_hash is None:
        return "(Unknown)"
    return commit_hash

def get_branch(force=False):
    global branch
    if force or branch is None:
        file_path = get_asset("_assets/branch.txt")
        if os.path.exists(file_path):
            branch = open(file_path, 'r').read().strip()
    if branch is None:
        return "(Unknown)"
    return branch

def get_build_date(force=False):
    global build_date
    if force or build_date is None:
        file_path = get_asset("_assets/build_date.txt")
        if os.path.exists(file_path):
            build_date = open(file_path, 'r').read().strip()
    if build_date is None:
        return "(Unknown)"
    return build_date
