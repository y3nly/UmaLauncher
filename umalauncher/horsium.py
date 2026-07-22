# Will handle selenium browser automation
# Before every action, we need to check if the browser is open
# and, in the case of multiple windows, which one is the one we want to use
# Each open window will be an instance of a custom class.
# Interacting with the browser will be done through this class.
import os
import traceback
import time
import threading
import queue
from functools import wraps
from urllib.parse import urlparse
from subprocess import CREATE_NO_WINDOW

import psutil
import win32con
import win32gui
from loguru import logger
from selenium.common.exceptions import (
    NoSuchElementException,
    NoSuchFrameException,
    StaleElementReferenceException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.options import ArgOptions
from selenium.webdriver.remote.webdriver import WebDriver as RemoteWebDriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.webdriver import WebDriver as ChromeWebDriver
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.edge.webdriver import WebDriver as EdgeWebDriver
from selenium.webdriver.firefox.firefox_profile import FirefoxProfile
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.firefox.webdriver import WebDriver as FirefoxWebDriver
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.common.exceptions import NoSuchWindowException
from urllib3.exceptions import ReadTimeoutError
import util
import socket

_DRIVER_REAPER_QUEUE = queue.Queue()
_DRIVER_REAPER_THREAD = None
_DRIVER_REAPER_LOCK = threading.Lock()
_DRIVER_REAPER_STOP = object()
_DRIVER_REAPER_STOPPING = False
_DRIVER_CLEANUP_THREADS = set()
_DRIVER_CLEANUP_LOCK = threading.Lock()
ADBLOCK_RULES_CACHE = None
ADBLOCK_MINIMUM_DOMAIN_COUNT = 1_000
WEBDRIVER_COMMAND_TIMEOUT = 5.0
PAGE_NAVIGATION_TIMEOUT = 5.0
PAGE_NAVIGATION_POLL_INTERVAL = 0.1


def set_browser_version(options: ArgOptions, settings):
    if not settings['browser_version'] or not settings['browser_version'].strip():
        return
    options.browser_version = settings['browser_version']

def _is_port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.15) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

def firefox_setup(helper_url, settings):
    driver_path = None
    if settings['enable_browser_override']:
        new_path = settings['browser_custom_driver']
        if new_path:
            driver_path = new_path

    firefox_service = FirefoxService(executable_path=driver_path)
    firefox_service.creation_flags = CREATE_NO_WINDOW
    profile = FirefoxProfile(util.get_asset("ff_profile"))
    profile.set_preference("security.fileuri.strict_origin_policy", False) # Disable CORS protections
    options = FirefoxOptions()
    set_browser_version(options, settings)
    options.profile = profile

    binary_path = None

    if settings['enable_browser_override']:
        binary_path = settings['browser_custom_binary']
        if binary_path:
            options.binary_location = binary_path
    
    logger.debug(f"Firefox driver path: {driver_path}")
    logger.debug(f"Firefox binary path: {binary_path}")
    
    browser = FirefoxWebDriver(service=firefox_service, options=options)
    browser.get(helper_url)
    browser.command_executor.client_config.timeout = WEBDRIVER_COMMAND_TIMEOUT
    return browser

def get_bundled_blocklist():
    global ADBLOCK_RULES_CACHE

    if ADBLOCK_RULES_CACHE is not None:
        return ADBLOCK_RULES_CACHE

    if util.is_script:
        asset_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "_assets",
            "adblock_domains.dev.txt",
        )
    else:
        asset_path = util.get_asset("_assets/adblock_domains.txt")

    try:
        with open(asset_path, "r", encoding="utf-8") as blocklist_file:
            domains = [line.strip().lower() for line in blocklist_file if line.strip()]
        if len(domains) < ADBLOCK_MINIMUM_DOMAIN_COUNT:
            raise ValueError(
                f"expected at least {ADBLOCK_MINIMUM_DOMAIN_COUNT} domains, got {len(domains)}"
            )
        if len(domains) != len(set(domains)):
            raise ValueError("domain list contains duplicates")
        if any(
            domain.startswith("#")
            or "://" in domain
            or any(character.isspace() for character in domain)
            for domain in domains
        ):
            raise ValueError("domain list contains an invalid entry")
    except (OSError, UnicodeError, ValueError) as exc:
        logger.warning(
            f"Failed to load bundled ad blocklist from {asset_path}; "
            f"using static rules only: {exc}"
        )
        ADBLOCK_RULES_CACHE = []
        return ADBLOCK_RULES_CACHE

    ADBLOCK_RULES_CACHE = [f"*://*.{domain}/*" for domain in domains]
    logger.info(f"Loaded {len(ADBLOCK_RULES_CACHE)} bundled ad blocklist rules.")
    return ADBLOCK_RULES_CACHE

def chromium_setup(service, options_class, driver_class, profile, helper_url, settings, binary_path=None, base_port=9222, max_port=9229):
    service.creation_flags = CREATE_NO_WINDOW
    options = options_class()
    set_browser_version(options, settings)

    options.page_load_strategy = 'eager'

    if binary_path:
        options.binary_location = binary_path

    # Find first free port
    for port in range(base_port, max_port):
        if not _is_port_open(port):
            break
    else:
        raise RuntimeError(f"No free debug port available between {base_port} and {max_port}")

    # Use per-port profile folder to avoid conflicts if same profile used multiple times
    per_port_profile = profile + f"_p{port}"

    options.add_argument(f"--user-data-dir={per_port_profile}")
    options.add_argument(f"--remote-debugging-port={port}")
    options.add_experimental_option("useAutomationExtension", False) # Disable browser being controlled warning
    options.add_experimental_option("excludeSwitches", ["enable-automation"]) # Disable browser being controlled warning
    options.add_argument("--disable-web-security") # Disable CORS protections

    if not settings['enable_browser_override']:
        options.add_argument("--app=" + helper_url)

    browser = driver_class(service=service, options=options)

    # List of patterns to block
    blocked_urls = [
        # Header bidding / prebid
        "*://*.presage.io/*",
        "*://presage.io/*",

        "*://*.prebid.media.net/*",
        "*://prebid.media.net/*",

        "*://*.a-mo.net/*",
        "*://a-mo.net/*",

        "*://*.onetag-sys.com/*",
        "*://onetag-sys.com/*",

        "*://*.33across.com/*",
        "*://33across.com/*",

        "*://*.rubiconproject.com/*",
        "*://rubiconproject.com/*",

        "*://*.openx.net/*",
        "*://openx.net/*",

        "*://*.adnxs.com/*",
        "*://adnxs.com/*",

        "*://*.gumgum.com/*",
        "*://gumgum.com/*",

        "*://*.media.net/*",
        "*://media.net/*",

        "*://*.richaudience.com/*",
        "*://richaudience.com/*",

        "*://*.connectad.io/*",
        "*://connectad.io/*",

        "*://*.kueezrtb.com/*",
        "*://kueezrtb.com/*",

        "*://*.cootlogix.com/*",
        "*://cootlogix.com/*",

        "*://*.ingage.tech/*",
        "*://ingage.tech/*",

        "*://*.marphezis.com/*",
        "*://marphezis.com/*",

        # Ad delivery / tracking
        "*://*.doubleclick.net/*",
        "*://doubleclick.net/*",

        "*://*.googlesyndication.com/*",
        "*://googlesyndication.com/*",

        "*://*.btloader.com/*",
        "*://btloader.com/*",

        "*://*.ad-delivery.net/*",
        "*://ad-delivery.net/*",

        "*://*.inmobi.com/*",
        "*://inmobi.com/*",

        "*://*.intentiq.com/*",
        "*://intentiq.com/*",

        "*://*.cloudflareinsights.com/*",
        "*://cloudflareinsights.com/*",

        "*://*.fuseplatform.net/*",
        "*://fuseplatform.net/*",

        "*://*.amazon-adsystem.com/*",
        "*://amazon-adsystem.com/*",

        "*://*.adsrvr.org/*",
        "*://adsrvr.org/*",

        "*://*.servenobid.com/*",
        "*://servenobid.com/*",

        "*://*.criteo.com/*",
        "*://criteo.com/*",

        "*://*.adtrafficquality.google/*",
        "*://adtrafficquality.google/*",

        "*://*.google-analytics.com/*",
        "*://google-analytics.com/*",

        "*://*.ay.delivery/*",
        "*://ay.delivery/*",

        "*://*.pubmatic.com/*",
        "*://*.casalemedia.com/*",
        "*://*.smartadserver.com/*",
        "*://*.tynt.com/*",
        "*://*.quantserve.com/*",
        "*://*.sharethrough.com/*",
        "*://*.outbrain.com/*",
    ]

    blocked_urls.extend(get_bundled_blocklist())
    blocked_urls = list(dict.fromkeys(blocked_urls))

    browser.execute_cdp_cmd("Network.enable", {})
    browser.execute_cdp_cmd(
        "Network.setBlockedURLs",
        {"urls": blocked_urls}
    )
    
    if settings['enable_browser_override']:
        browser.get(helper_url)

    # Browser startup, CDP configuration, and an explicit initial navigation
    # can legitimately take longer than an ordinary runtime command. Apply the
    # short deadline only after initialization so a stalled dashboard script
    # cannot freeze the packet thread without making startup brittle.
    browser.command_executor.client_config.timeout = WEBDRIVER_COMMAND_TIMEOUT

    logger.debug(f"Chromium started on debug port {port} using profile {per_port_profile}")
    return browser

def chrome_setup(helper_url, settings):
    driver_path = None
    if settings['enable_browser_override']:
        new_path = settings['browser_custom_driver']
        if new_path:
            driver_path = new_path
    
    binary_path = None
    if settings['enable_browser_override']:
        binary_path = settings['browser_custom_binary']

    logger.debug(f"Chrome driver path: {driver_path}")
    logger.debug(f"Chrome binary path: {binary_path}")

    return chromium_setup(
        service=ChromeService(executable_path=driver_path) if driver_path else ChromeService(),
        options_class=ChromeOptions,
        driver_class=ChromeWebDriver,
        profile=util.get_appdata("chr_profile"),
        helper_url=helper_url,
        settings=settings,
        binary_path=binary_path
    )

def edge_setup(helper_url, settings):
    return chromium_setup(
        service=EdgeService(),
        options_class=EdgeOptions,
        driver_class=EdgeWebDriver,
        profile=util.get_appdata("edg_profile"),
        helper_url=helper_url,
        settings=settings
    )

BROWSER_LIST = {
    'Chrome': chrome_setup,
    'Firefox': firefox_setup,
    'Edge': edge_setup,
}

def urls_match(url1, url2):
    url1 = url1[:-1] if url1.endswith('/') else url1
    url2 = url2[:-1] if url2.endswith('/') else url2
    urlparse1 = urlparse(url1)
    urlparse2 = urlparse(url2)
    return (urlparse1.netloc, urlparse1.path) == (urlparse2.netloc, urlparse2.path)



class BrowserWindow:
    DEFAULT_HELPER_TITLE = "Training Event Helper | Uma Musume | GameTora"
    PAGE_SENTINEL_SCRIPT = """
        return {
            ready: window.from_script === true,
            url: location.href
        };
    """

    def __init__(
        self,
        url,
        threader,
        rect=None,
        run_at_launch=None,
        window_title=None,
    ):
        self.url = url
        self.threader = threader
        self.settings = threader.settings
        self.window_title = window_title
        self._driver_lock = threading.RLock()
        self.driver: RemoteWebDriver = None
        self.active_tab_handle = None
        self.last_window_rect = {'x': rect[0], 'y': rect[1], 'width': rect[2], 'height': rect[3]} if rect else None
        self.run_at_launch = run_at_launch
        self.browser_name = "Auto"
        self.latest_error = ""
        self.last_foreground_hwnd = None
        
        self.ensure_tab_open()

    def init_browser(self) -> RemoteWebDriver:
        driver = None

        if self.settings['enable_browser_override']:
            selection = self.settings['custom_browser_type']
        else:
            selection = self.settings['selected_browser']
        browser_name = [
                    browser
                    for browser, selected in selection.items()
                    if selected
                ][0]
        self.browser_name = browser_name

        # Hack to convert override Chromium to Chrome
        if browser_name == 'Other (Chromium)':
            browser_name = 'Chrome'

        browser_list = []
        if browser_name == "Auto":
            browser_list = BROWSER_LIST.items()
        else:
            browser_list = [(browser_name, BROWSER_LIST[browser_name])]

        for browser_data in browser_list:
            browser_name, browser_setup = browser_data
            try:
                logger.info("Attempting " + str(browser_setup.__name__))
                driver = browser_setup(self.url, self.settings)
                self.browser_name = browser_name
                break
            except Exception as e:
                logger.error(f"Failed to start {browser_name}")
                logger.error(traceback.format_exc())
                self.latest_error = traceback.format_exception_only(type(e), e)[-1]
        # if not driver:
        #     util.show_warning_box("Uma Launcher: Unable to start browser.", "Selected webbrowser cannot be started.")
        return driver

    def alive(self):
        with self._driver_lock:
            if self.driver is None:
                return False
            try:
                if self.active_tab_handle in self.driver.window_handles:
                    return True
            except Exception:
                pass
            return False


    def ensure_tab_open(self):
        with self._driver_lock:
            if self.driver:
                try:
                    window_handles = self.driver.window_handles
                    if self.active_tab_handle not in window_handles:
                        raise NoSuchWindowException("Tracked browser tab is closed")
                    if self.driver.current_window_handle != self.active_tab_handle:
                        self.driver.switch_to.window(self.active_tab_handle)

                    # Only recovery needs to normalize the browsing context.
                    # Healthy commands use the lightweight page sentinel in
                    # ensure_focus and avoid this remote call.
                    self.driver.switch_to.default_content()
                    page_state = self.driver.execute_script(
                        self.PAGE_SENTINEL_SCRIPT
                    )
                    if (
                        isinstance(page_state, dict)
                        and page_state.get("ready") is True
                        and urls_match(page_state.get("url", ""), self.url)
                    ):
                        return

                    self.driver.execute_script(
                        """
                        document.still_the_old_page_haha = true;
                        window.location.assign(arguments[0]);
                        """,
                        self.url,
                    )
                    deadline = time.monotonic() + PAGE_NAVIGATION_TIMEOUT
                    while time.monotonic() < deadline:
                        navigation_state = self.driver.execute_script(
                            """
                            return {
                                oldPage: Boolean(document.still_the_old_page_haha),
                                readyState: document.readyState
                            };
                            """
                        )
                        if (
                            isinstance(navigation_state, dict)
                            and not navigation_state.get("oldPage")
                            and navigation_state.get("readyState") == "complete"
                        ):
                            self.run_script_at_launch()
                            return
                        time.sleep(PAGE_NAVIGATION_POLL_INTERVAL)
                    raise TimeoutError(
                        "Browser page did not load within "
                        f"{PAGE_NAVIGATION_TIMEOUT:.0f}s"
                    )
                except Exception:
                    logger.warning(
                        "Browser tab recovery failed; starting a fresh session:\n"
                        f"{traceback.format_exc()}"
                    )
                    self._retire_driver()

            self.driver = self.init_browser()

            try:
                if not self.driver:
                    return
                if not self.driver.window_handles:
                    self._retire_driver()
                    return
            except WebDriverException:
                logger.error("Failed to get window handles")
                logger.error(traceback.format_exc())
                self._retire_driver()
                return

            self.active_tab_handle = self.driver.window_handles[0]
            self.driver.switch_to.window(self.active_tab_handle)
            self.run_script_at_launch()
            self.last_window_rect = self.driver.get_window_rect()

    def run_script_at_launch(self):
        with self._driver_lock:
            self.driver.execute_script("""window.from_script = true;""")
            saved_rect = dict(self.last_window_rect) if self.last_window_rect else None
            if saved_rect:
                # Selenium and window.screen* report the same logical-pixel
                # coordinate space. Passing those values to Win32 SetWindowPos
                # treats them as physical pixels and shrinks the helper on
                # scaled displays (318px became 213px at 150% DPI).
                self.driver.set_window_rect(
                    saved_rect['x'],
                    saved_rect['y'],
                    saved_rect['width'],
                    saved_rect['height'],
                )

            if self.run_at_launch is not None:
                self.run_at_launch(self)

            # only want to do this for the training-event-helper
            if 'training-event-helper' in self.url:
                self.set_topmost(self.settings["browser_topmost"])
                self.stop_taskbar_flash()

    def ensure_focus(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            with self._driver_lock:
                def run_action_once():
                    try:
                        return func(self, *args, **kwargs)
                    except ReadTimeoutError:
                        # The command may have reached the browser, so replace
                        # the stalled transport without replaying a potentially
                        # mutating action.
                        logger.warning(
                            "WebDriver action timed out; replacing the browser "
                            "session without replaying the action:\n"
                            f"{traceback.format_exc()}"
                        )
                        self._retire_driver()
                        self.ensure_tab_open()
                        return None

                for _attempt in range(3):
                    if self.driver:
                        try:
                            page_state = self.driver.execute_script(
                                self.PAGE_SENTINEL_SCRIPT
                            )
                        except ReadTimeoutError:
                            # The sentinel is read-only, so it is safe to open a
                            # fresh session and then run the requested action.
                            logger.warning(
                                "WebDriver health check timed out; replacing "
                                "the browser session:\n"
                                f"{traceback.format_exc()}"
                            )
                            self._retire_driver()
                            page_state = None
                        except WebDriverException:
                            page_state = None
                        if (
                            isinstance(page_state, dict)
                            and page_state.get("ready") is True
                            and urls_match(page_state.get("url", ""), self.url)
                        ):
                            # Do not replay func after an error: several browser
                            # actions click or mutate state and are not idempotent.
                            return run_action_once()

                    self.ensure_tab_open()
                    if self.driver:
                        return run_action_once()

            util.show_warning_box("Uma Launcher: Unable to reach browser.", f"Webbrowser is unable to open.<br><br>If this problem persists, try restarting your computer<br>or selecting a different browser in the preferences.<br><br>Extra info:<br>{self.latest_error}")
        return wrapper

    def get_browser_pid(self):
        with self._driver_lock:
            if self.driver is not None and 'moz:processID' in self.driver.capabilities:
                return self.driver.capabilities['moz:processID']

        browsers = ['chrome.exe', 'msedge.exe', 'chromium.exe']
        if self.settings['enable_browser_override'] and self.settings['browser_custom_binary']:
            browsers.append( os.path.basename(self.settings['browser_custom_binary'])  )
        # Chromium-based (chrome/edge) browsers should be launched with the --app= flag.
        # The app flag isn't passed to custom browser binaries, so we check for that later
        for process in psutil.process_iter():
            try:
                if process.name() in browsers:
                    if f'--app={self.url}' in process.cmdline():
                        return process.pid
            except Exception as e:
                logger.warning( "Error getting browser PID:" )
                logger.warning(traceback.format_exc())
        # If we didn't find a process with the --app= flag (likely a custom browser binary), try to find it by looking for the webdriver flag
        for process in psutil.process_iter():
            try:
                if (process.name() in browsers
                    and '--test-type=webdriver' in process.cmdline()
                    and process.parent().name() not in browsers):
                    # Look for the top-level browser process only (it's what has the window)
                    return process.pid
            except Exception as e:
                logger.warning( "Error getting browser PID:" )
                logger.warning(traceback.format_exc())
        logger.warning("Could not get browser PID!")
        return None

    def get_helper_hwnd(self):
        target_title = getattr(self, "window_title", None) or self.DEFAULT_HELPER_TITLE
        hwnd = win32gui.FindWindow(None, target_title)
        if hwnd and win32gui.IsWindowVisible(hwnd):
            return hwnd

        matches = []

        def collect(candidate, _extra):
            if (
                win32gui.IsWindowVisible(candidate)
                and target_title in win32gui.GetWindowText(candidate)
            ):
                matches.append(candidate)

        win32gui.EnumWindows(collect, None)
        return matches[0] if matches else 0

    def focus(self):
        """Restore and activate this browser window without creating a new tab."""
        with self._driver_lock:
            if not self.driver or not self.alive():
                return False
            try:
                self.driver.switch_to.window(self.active_tab_handle)
                self.driver.execute_script("window.focus();")
            except Exception:
                logger.debug(
                    "Could not focus the browser through WebDriver:\n"
                    f"{traceback.format_exc()}"
                )

        try:
            hwnd = self.get_helper_hwnd()
            if not hwnd:
                return False
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
            return True
        except Exception:
            # Windows can reject foreground activation when another process
            # owns the input queue. The WebDriver focus above is still useful,
            # so keep this best-effort and let the existing window be reused.
            logger.debug(
                "Could not activate the browser window through Win32:\n"
                f"{traceback.format_exc()}"
            )
            return False

    def get_game_hwnd(self):
        return win32gui.FindWindow("UnityWndClass", "Umamusume")

    def set_topmost(self, is_topmost):
        """Updates whether the helper should remain above other windows."""
        self.settings["browser_topmost"] = is_topmost

    def set_pair(self, is_paired):
        """Updates whether the helper follows the game's focus state."""
        self.settings["browser_pair"] = is_paired

    def enforce_z_order(self):
        """
        Keeps topmost and game-pairing behavior independent.

        Pairing minimizes the helper when focus leaves the game/helper context,
        and restores it when focus returns to the game. Top keeps the helper
        above other windows regardless of pairing.
        """
        try:
            helper_hwnd = self.get_helper_hwnd()
            game_hwnd = self.get_game_hwnd()

            if not helper_hwnd or not game_hwnd:
                return

            foreground_hwnd = win32gui.GetForegroundWindow()
            is_minimized = win32gui.IsIconic(helper_hwnd)

            # Transition Detection
            # We need to capture the previous state locally before we update self.last_foreground_hwnd
            previous_hwnd = self.last_foreground_hwnd
            just_switched_to_game = (foreground_hwnd == game_hwnd) and (previous_hwnd != game_hwnd)
            focus_changed = (previous_hwnd != foreground_hwnd)

            # Update Global Tracker
            self.last_foreground_hwnd = foreground_hwnd

            is_context_active = (
                foreground_hwnd == game_hwnd
                or foreground_hwnd == helper_hwnd
            )
            is_pair_mode = self.settings["browser_pair"]

            if is_pair_mode:
                if not is_context_active:
                    if not is_minimized:
                        win32gui.ShowWindow(helper_hwnd, win32con.SW_MINIMIZE)
                    return

                # Don't immediately undo a manual minimize performed from the helper.
                if (
                    just_switched_to_game
                    and is_minimized
                    and previous_hwnd != helper_hwnd
                ):
                    win32gui.ShowWindow(helper_hwnd, win32con.SW_RESTORE)
                    is_minimized = False

            if is_minimized:
                return

            flags = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE

            current_ex_style = win32gui.GetWindowLong(helper_hwnd, win32con.GWL_EXSTYLE)
            is_currently_topmost = (current_ex_style & win32con.WS_EX_TOPMOST) != 0

            if self.settings["browser_topmost"]:
                if focus_changed or not is_currently_topmost:
                    win32gui.SetWindowPos(helper_hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags)
            else:
                if is_currently_topmost:
                    win32gui.SetWindowPos(helper_hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags)

                if is_pair_mode and foreground_hwnd == game_hwnd:
                    win32gui.SetWindowPos(helper_hwnd, game_hwnd, 0, 0, 0, 0, flags)

        except Exception:
            pass

    def stop_taskbar_flash(self):
        """
        Stops the taskbar entry from blinking/flashing (orange alert).
        """
        try:
            hwnd = self.get_helper_hwnd()
            if not hwnd:
                return

            # FLASHW_STOP = 0
            # Arguments: (hwnd, flags, count, timeout)
            win32gui.FlashWindowEx(hwnd, 0, 0, 0)
        except Exception:
            pass

    @ensure_focus
    def execute_script(self, *args, **kwargs):
        return self.driver.execute_script(*args, **kwargs)

    def run_frame_operation(self, frame_reference, operation, timeout):
        if not callable(operation):
            raise TypeError("Frame operation must be callable")

        driver = self.driver

        def switch_when_available(current_driver):
            try:
                current_driver.switch_to.default_content()
                if isinstance(frame_reference, str):
                    try:
                        frame = current_driver.find_element(By.ID, frame_reference)
                    except NoSuchElementException:
                        frame = current_driver.find_element(By.NAME, frame_reference)
                else:
                    frame = frame_reference
                current_driver.switch_to.frame(frame)
                return True
            except (
                NoSuchElementException,
                NoSuchFrameException,
                StaleElementReferenceException,
            ):
                return False

        try:
            WebDriverWait(driver, timeout, poll_frequency=0.1).until(
                switch_when_available
            )
            return operation(driver)
        finally:
            # Do not let a successful or failing iframe operation leak its
            # browsing context into the next dashboard command.
            driver.switch_to.default_content()

    @ensure_focus
    def run_in_frame(self, frame_reference, operation, timeout=10):
        """Run ``operation(driver)`` in a frame, then restore top-level context."""
        return self.run_frame_operation(frame_reference, operation, timeout)

    @ensure_focus
    def execute_script_in_frame(
        self,
        frame_reference,
        script,
        *args,
        timeout=10,
    ):
        """Execute JavaScript in a frame selected by id/name or WebElement."""
        return self.run_frame_operation(
            frame_reference,
            lambda driver: driver.execute_script(script, *args),
            timeout,
        )

    @ensure_focus
    def set_window_rect(self, rect):
        return self.driver.set_window_rect(*rect)

    @ensure_focus
    def fit_modern_table_width(self, dashboard_metrics=None):
        """Fit the helper to the fixed Modern table without moving it."""
        metrics = dashboard_metrics
        if not (
            isinstance(metrics, dict)
            and isinstance(metrics.get("contentWidth"), (int, float))
            and isinstance(metrics.get("scrollbarWidth"), (int, float))
            and isinstance(metrics.get("innerWidth"), (int, float))
        ):
            metrics = self.driver.execute_script(
                """
                const helper = document.getElementById("modern-training-helper");
                const dashboard = document.getElementById("dashboard-scroll");
                let contentWidth = 304;
                if (helper) {
                  const style = getComputedStyle(helper);
                  const gridWidth = Number.parseFloat(
                    style.getPropertyValue("--mth-grid-width")
                  );
                  if (Number.isFinite(gridWidth) && gridWidth > 0) {
                    contentWidth = gridWidth + 2
                      + (Number.parseFloat(style.paddingLeft) || 0)
                      + (Number.parseFloat(style.paddingRight) || 0);
                  }
                }
                return {
                  contentWidth,
                  scrollbarWidth: dashboard
                    ? Math.max(0, dashboard.offsetWidth - dashboard.clientWidth)
                    : 0,
                  innerWidth: window.innerWidth
                };
                """
            )
        rect = self.driver.get_window_rect()
        frame_width = max(
            0.0,
            float(rect["width"]) - float(metrics["innerWidth"]),
        )
        target_width = round(
            float(metrics["contentWidth"])
            + float(metrics["scrollbarWidth"])
            + frame_width
        )
        target_width = max(160, min(2000, target_width))
        if abs(float(rect["width"]) - target_width) <= 1:
            self.last_window_rect = dict(rect)
            return rect
        # Keep the fit in Selenium's logical-pixel coordinate space. Pass the
        # complete rectangle because ChromeDriver can ignore a width-only
        # update even though the equivalent full startup rectangle succeeds.
        applied = self.driver.set_window_rect(
            int(rect["x"]),
            int(rect["y"]),
            target_width,
            int(rect["height"]),
        )

        def width_was_applied(candidate):
            return (
                isinstance(candidate, dict)
                and isinstance(candidate.get("width"), (int, float))
                and abs(float(candidate["width"]) - target_width) <= 2
            )
        if not width_was_applied(applied):
            logger.warning(
                "The browser clamped the Modern helper width instead of "
                f"applying {target_width}px; started at {rect} and returned "
                f"{applied}. The fit will be retried."
            )
            return False
        self.last_window_rect = dict(applied)
        return applied
    
    @ensure_focus
    def get_window_rect(self):
        return self.driver.get_window_rect()

    def get_last_window_rect(self):
        return self.last_window_rect
    
    def current_url(self):
        return self.url

    def close(self, rect_callback=None):
        """Detach immediately and perform every Selenium close call off-thread."""
        with self._driver_lock:
            driver = self.driver
            active_tab_handle = self.active_tab_handle
            self.driver = None
            self.active_tab_handle = None
        if driver is None:
            return
        # A replacement helper may be created immediately after close()
        # returns. Modern has an explicit title and Legacy has a distinct
        # helper URL; untitled auxiliary windows must rely on the async reaper.
        can_identify_helper = bool(
            getattr(self, "window_title", None)
            or "training-event-helper" in getattr(self, "url", "")
        )
        if can_identify_helper:
            try:
                hwnd = self.get_helper_hwnd()
                if hwnd:
                    win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
            except Exception:
                logger.debug(
                    "Could not hide retiring browser window:\n"
                    f"{traceback.format_exc()}"
                )
        retire_driver(
            driver,
            close_tab=True,
            active_tab_handle=active_tab_handle,
            window=self,
            rect_callback=rect_callback,
        )

    def quit(self, rect_callback=None):
        self.close(rect_callback=rect_callback)

    def _retire_driver(self):
        with self._driver_lock:
            driver = self.driver
            self.driver = None
            self.active_tab_handle = None
        retire_driver(driver)


def quit_one_driver(
    driver,
    close_tab=False,
    active_tab_handle=None,
    window=None,
    rect_callback=None,
):
    logger.debug(f"Closing driver in thread {threading.get_ident()}")
    if driver:
        if close_tab:
            try:
                if active_tab_handle in driver.window_handles:
                    driver.switch_to.window(active_tab_handle)
                    last_window_rect = driver.get_window_rect()
                    if window is not None and last_window_rect:
                        window.last_window_rect = last_window_rect
                    if rect_callback is not None and last_window_rect:
                        try:
                            rect_callback(last_window_rect)
                        except Exception:
                            logger.debug(
                                f"WebDriver rect callback failed:\n{traceback.format_exc()}"
                            )
                    driver.close()
            except Exception:
                logger.debug(f"WebDriver tab cleanup failed:\n{traceback.format_exc()}")
        try:
            driver.quit()
        except Exception:
            logger.debug(f"WebDriver cleanup failed:\n{traceback.format_exc()}")
    logger.debug(f"Finished closing driver in thread {threading.get_ident()}")


def _run_driver_cleanup(job):
    try:
        quit_one_driver(*job)
    except Exception:
        # Keep a bad cleanup implementation or unusual driver exception from
        # terminating the dispatcher or affecting later sessions.
        logger.error(f"Unexpected WebDriver cleanup failure:\n{traceback.format_exc()}")
    finally:
        with _DRIVER_CLEANUP_LOCK:
            _DRIVER_CLEANUP_THREADS.discard(threading.current_thread())


def _start_driver_cleanup(job):
    cleanup_thread = threading.Thread(
        target=_run_driver_cleanup,
        args=(job,),
        name="webdriver-cleanup",
        daemon=True,
    )
    with _DRIVER_CLEANUP_LOCK:
        _DRIVER_CLEANUP_THREADS.add(cleanup_thread)
    try:
        cleanup_thread.start()
    except Exception:
        with _DRIVER_CLEANUP_LOCK:
            _DRIVER_CLEANUP_THREADS.discard(cleanup_thread)
        raise


def _driver_reaper():
    while True:
        job = _DRIVER_REAPER_QUEUE.get()
        try:
            if job is _DRIVER_REAPER_STOP:
                return
            # Each driver gets an independent daemon cleanup thread. A hung
            # Selenium IPC call therefore cannot hold up later retirements.
            _start_driver_cleanup(job)
        except Exception:
            logger.error(f"WebDriver reaper failed to dispatch cleanup:\n{traceback.format_exc()}")
        finally:
            _DRIVER_REAPER_QUEUE.task_done()


def _ensure_driver_reaper_locked():
    """Start the dispatcher while the caller holds _DRIVER_REAPER_LOCK."""
    global _DRIVER_REAPER_THREAD

    if _DRIVER_REAPER_THREAD and _DRIVER_REAPER_THREAD.is_alive():
        return
    _DRIVER_REAPER_THREAD = threading.Thread(
        target=_driver_reaper,
        name="webdriver-reaper",
        daemon=True,
    )
    _DRIVER_REAPER_THREAD.start()


def retire_driver(
    driver,
    close_tab=False,
    active_tab_handle=None,
    window=None,
    rect_callback=None,
):
    """Queue a dead/replaced WebDriver for prompt cleanup off the caller thread."""
    if driver is None:
        return
    job = (driver, close_tab, active_tab_handle, window, rect_callback)
    with _DRIVER_REAPER_LOCK:
        if _DRIVER_REAPER_STOPPING:
            # Register direct cleanup before shutdown can take its worker
            # snapshot. Lock order is reaper -> cleanup everywhere.
            _start_driver_cleanup(job)
        else:
            _ensure_driver_reaper_locked()
            # Keep enqueue atomic with the stopping check so the sentinel can
            # never overtake a driver accepted by the live dispatcher.
            _DRIVER_REAPER_QUEUE.put(job)


def quit_all_drivers(timeout=10.0):
    """Drain the reaper and join it without hanging application shutdown."""
    global _DRIVER_REAPER_THREAD, _DRIVER_REAPER_STOPPING

    deadline = time.monotonic() + max(0.0, timeout)

    with _DRIVER_REAPER_LOCK:
        reaper = _DRIVER_REAPER_THREAD
        _DRIVER_REAPER_STOPPING = True
        if reaper and reaper.is_alive():
            _DRIVER_REAPER_QUEUE.put(_DRIVER_REAPER_STOP)

    if reaper and reaper.is_alive():
        reaper.join(timeout=max(0.0, deadline - time.monotonic()))
    if reaper and reaper.is_alive():
        logger.warning("Timed out waiting for WebDriver reaper to stop")

    with _DRIVER_REAPER_LOCK:
        if reaper is None or not reaper.is_alive():
            _DRIVER_REAPER_THREAD = None

    # Cleanup workers are independent so one hung driver does not delay the
    # others. Join them only within the caller's shared shutdown budget. The
    # empty-snapshot and STOPPING reset share the reaper lock with retire_driver;
    # this is the shutdown linearization point.
    while True:
        with _DRIVER_REAPER_LOCK:
            if _DRIVER_REAPER_THREAD is not None:
                return
            with _DRIVER_CLEANUP_LOCK:
                cleanup_threads = [
                    thread for thread in _DRIVER_CLEANUP_THREADS
                    if thread.is_alive() and thread is not threading.current_thread()
                ]
                if not cleanup_threads:
                    _DRIVER_REAPER_STOPPING = False
                    return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.warning(
                f"Timed out waiting for {len(cleanup_threads)} WebDriver cleanup thread(s)"
            )
            return
        # Small slices avoid giving one hung driver the entire remaining budget.
        for cleanup_thread in cleanup_threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            cleanup_thread.join(timeout=min(0.05, remaining))

# Chromium Webdriver is a poopyhead
