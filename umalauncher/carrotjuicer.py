import io
import functools
import hashlib
import math
import os
import time
import traceback
import json
import threading
from collections import Counter
from datetime import datetime

import msgpack
import select
from loguru import logger
from msgpack import Unpacker
from selenium.common.exceptions import NoSuchWindowException
import util
import constants
import mdb
import helper_table
import skill_simulation
import uma_rating
import training_tracker
import helper_theme
import horsium
import runtime_extensions
import socket
import subprocess

from Cryptodome.Cipher import AES


def is_valid_window_rect(rect):
    if not rect:
        return False
    try:
        if isinstance(rect, dict):
            values = (
                rect["x"], rect["y"], rect["width"], rect["height"]
            )
        else:
            values = rect[:4]
        x, y, width, height = (float(value) for value in values)
    except (KeyError, TypeError, ValueError):
        return False
    return (
        all(math.isfinite(value) for value in (x, y, width, height))
        and width > 0
        and height > 0
    )


def normalize_choice_array(value):
    """Return well-formed choice dictionaries from a packet value."""
    if isinstance(value, dict):
        value = value.get("choice_array") or value.get("choices") or []
    if not isinstance(value, list):
        return []
    return [choice for choice in value if isinstance(choice, dict)]


BASE_RANKS = [
    (300, "G"), (600, "G+"), (900, "F"), (1300, "F+"), (1800, "E"),
    (2300, "E+"), (2900, "D"), (3500, "D+"), (4900, "C"), (6500, "C+"),
    (8200, "B"), (10000, "B+"), (12100, "A"), (14500, "A+"), (15900, "S"),
    (17500, "S+"), (19200, "SS"), (19600, "SS+")
]

HIGH_RANKS = [
    (19600, 400, 23900, "UG"),
    (23900, 500, 28800, "UF"),
    (28800, 560, 34400, "UE"),
    (34400, 630, 40700, "UD"),
    (40700, 700, 47600, "UC"),
    (47600, 760, 55200, "UB"),
    (55200, 800, float('inf'), "UA")
]

def unpack(data: bytes, key: bytes, iv: bytes) -> bytes:
    # logger.debug(f"Unpacking:\nData: {data.hex()}\nKey: {key.hex()}\nIV: {iv.hex()}")
    cipher = AES.new(key, AES.MODE_CBC, iv=iv)
    decrypted = cipher.decrypt(data)
    decrypted = decrypted[4:]
    b = io.BytesIO(decrypted)
    unpacker = Unpacker(file_like=b)
    return unpacker.unpack()


class CarrotJuicer:
    HELPER_UI_LEGACY = 0
    HELPER_UI_MODERN = 1
    MODERN_HELPER_TITLE = "Training Helper | UmaLauncher"
    MODERN_HELPER_URL = "http://127.0.0.1:3150/training-helper"
    EVENTS_WINDOW_TITLE = "Events | UmaLauncher"
    GAMETORA_EVENT_FRAME_ID = "gametora-event-frame"
    EVENT_FOCUS_VERIFY_TIMEOUT = 2.0
    WINDOW_MAINTENANCE_INTERVAL = 0.5
    SELENIUM_HEALTH_INTERVAL = 2.0
    WINDOW_RECT_FIELDS = {
        "helper": ("last_browser_rect", "browser_position"),
        "skill": ("last_skills_rect", "skills_position"),
        "events": ("last_events_rect", "events_position"),
        "schedule": ("last_schedule_rect", "schedule_position"),
    }
    browser: horsium.BrowserWindow = None
    previous_element = None
    threader = None
    helper_table = None
    should_stop = False
    last_browser_rect = None
    browser_topmost = False
    browser_pair = True
    reset_browser = False
    helper_url = None
    last_training_id = None
    training_tracker = None
    previous_request = None
    last_helper_data = None
    active_helper_mode = None
    skills_list = []
    previous_skills_list = []
    previous_race_program_id = None
    last_data = None
    open_skill_window = False
    skill_browser = None
    last_skills_rect = None
    open_event_window = False
    event_browser = None
    last_events_rect = None
    selected_cm_definition = None
    open_schedule_window = False
    schedule_browser = None
    last_schedule_rect = None

    sock: socket = None
    MAX_BUFFER_SIZE = 65535

    key = None
    iv = None
    encrypted_data = None

    def __init__(self, threader):
        self.threader = threader
        self.should_stop = False
        self.browser = None
        self.skill_browser = None
        self.event_browser = None
        self.schedule_browser = None
        self.open_skill_window = False
        self.open_event_window = False
        self.open_schedule_window = False
        self.previous_skills_list = []
        self.last_browser_rect = None
        self.last_skills_rect = None
        self.last_events_rect = None
        self.last_schedule_rect = None
        self._rect_lock = threading.Lock()
        self._rect_versions = {name: 0 for name in self.WINDOW_RECT_FIELDS}
        self.encrypted_data = None
        self._multipart_chunks = None
        self._close_transients_requested = False
        self._force_next_skill_simulation = False
        self.active_helper_mode = None

        self._initialize_skill_simulation()

        # Establish the master.mdb fingerprint before binding the cached dict
        # references below. Later attestation/training refreshes can then use
        # the cheap unchanged-database fast path instead of rebuilding twice.
        mdb.update_mdb_cache()
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
        self.selected_cm_definition = None


        self.runtime_extensions = runtime_extensions.create(self)
        self.helper_table = helper_table.HelperTable(self)
        self._event_generation = 0
        self._active_event_generation = None
        self._active_event_id = None
        self._active_event_source_available = False
        self._modern_event_chain = None
        self._modern_gametora_event = None
        self._pending_event_selection = None
        self._event_drawer_close_pending = None
        self._skill_sim_thread.start()

    def _initialize_skill_simulation(self):
        # The simulator is CPU-heavy and runs as a subprocess. Keep exactly one
        # worker so CarrotBlender packet handling remains responsive, coalescing
        # queued requests down to the newest payload.
        self._skill_sim_condition = threading.Condition()
        self._skill_sim_pending = None
        self._skill_sim_active_key = None
        self._skill_sim_completion = None
        self._skill_sim_cache = skill_simulation.CandidateCache(limit=12)
        self._skill_sim_data = skill_simulation.SkillDataSnapshot(
            util.get_asset("_assets/skill_data.txt"),
            util.get_appdata("skill-simulator"),
        )
        self._skill_sim_exe_stamp = None
        self._skill_sim_exe_digest = None
        self._skill_sim_active_generation = None
        self._skill_window_last_state_key = None
        self._skill_sim_generation = 0
        self._skill_sim_latest_generation = 0
        self._skill_sim_stop = False
        self._skill_sim_process = None
        self._skill_sim_thread = threading.Thread(
            target=self._skill_simulation_worker,
            name="skill-simulation-worker",
            daemon=True,
        )

    @staticmethod
    def _skill_simulation_key(payload):
        return skill_simulation.fingerprint(skill_simulation.canonical_payload(payload))

    def _skill_sim_engine_identity(self):
        exe_path = util.get_asset("_assets/umasim-cli.exe")
        stat = os.stat(exe_path)
        stamp = (exe_path, stat.st_size, stat.st_mtime_ns)
        if stamp != self._skill_sim_exe_stamp:
            with open(exe_path, "rb") as executable:
                self._skill_sim_exe_digest = hashlib.file_digest(executable, "sha256").hexdigest()
            self._skill_sim_exe_stamp = stamp
        return self._skill_sim_exe_digest, self._skill_sim_data.digest

    def _invalidate_skill_simulation_locked(self):
        self._skill_sim_generation += 1
        self._skill_sim_latest_generation = self._skill_sim_generation
        self._skill_sim_pending = None
        self._skill_sim_completion = None
        process = self._skill_sim_process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        return self._skill_sim_generation

    def _queue_skill_simulation(self, payload, force=False):
        """Reuse completed candidates, or cancel obsolete work and queue the latest input."""
        payload = skill_simulation.canonical_payload(payload)
        cache_key = self._skill_simulation_key(payload)
        with self._skill_sim_condition:
            engine = self._skill_sim_engine_identity()
            if force:
                self._skill_sim_cache.discard(payload, engine)
            elif not self._skill_sim_data.refresh_due():
                cached_result = self._skill_sim_cache.get(payload, engine)
                if cached_result is not None:
                    self._invalidate_skill_simulation_locked()
                    return cache_key, cached_result, False

            pending_key = self._skill_sim_pending[1] if self._skill_sim_pending else None
            active_matches = (
                cache_key == self._skill_sim_active_key
                and self._skill_sim_active_generation == self._skill_sim_latest_generation
            )
            if not force and (active_matches or cache_key == pending_key):
                return cache_key, None, True

            generation = self._invalidate_skill_simulation_locked()
            self._skill_sim_pending = (generation, cache_key, payload, force)
            self._skill_sim_condition.notify()
        return cache_key, None, True

    def _skill_simulation_worker(self):
        while True:
            with self._skill_sim_condition:
                self._skill_sim_condition.wait_for(
                    lambda: self._skill_sim_stop or self._skill_sim_pending is not None
                )
                if self._skill_sim_stop:
                    return
                generation, cache_key, payload, force = self._skill_sim_pending
                self._skill_sim_pending = None
                self._skill_sim_active_key = cache_key
                self._skill_sim_active_generation = generation

            result = {}
            context_key = None
            request = None
            try:
                if force or self._skill_sim_data.refresh_due():
                    self._skill_sim_data.refresh()
                with self._skill_sim_condition:
                    if self._skill_sim_stop or generation != self._skill_sim_latest_generation:
                        continue
                    engine = self._skill_sim_engine_identity()
                    if force:
                        self._skill_sim_cache.discard(payload, engine)
                    result = self._skill_sim_cache.get(payload, engine)
                    if result is None:
                        context_key, request = self._skill_sim_cache.missing_request(payload, engine)
                    snapshot_path = str(self._skill_sim_data.path)

                if request is not None:
                    start = time.monotonic()
                    result = self.run_simulation(
                        util.get_asset("_assets/umasim-cli.exe"), request,
                        expected_generation=generation, skill_data_path=snapshot_path,
                    )
                    logger.debug(
                        f"Skill simulation: {len(request['unacquiredSkillIds'])}/"
                        f"{len(payload['unacquiredSkillIds'])} candidates, "
                        f"{time.monotonic() - start:.3f}s, seed {request['seedBase']}"
                    )
                with self._skill_sim_condition:
                    if self._skill_sim_stop or generation != self._skill_sim_latest_generation:
                        continue
                    if result and context_key is not None:
                        self._skill_sim_cache.merge(context_key, request, result)
                        result = self._skill_sim_cache.get(payload, engine)
            except Exception:
                logger.error(f"Unexpected skill simulation failure:\n{traceback.format_exc()}")
                result = {}
            finally:
                with self._skill_sim_condition:
                    self._skill_sim_active_key = None
                    self._skill_sim_active_generation = None
                    if not self._skill_sim_stop and generation == self._skill_sim_latest_generation:
                        self._skill_sim_completion = (generation, cache_key, result)

    def _skill_window_state_key(self):
        chara_info = (self.last_data or {}).get("chara_info", {})
        # Include learned state and display costs as well as IDs. Hash locally;
        # only a changed value causes browser work or a simulation cache lookup.
        return skill_simulation.fingerprint({
            "chara": chara_info,
            "skills": self.skill_data,
            "engine": self._skill_sim_engine_identity(),
            "data_refresh_due": self._skill_sim_data.refresh_due(),
        })

    def _take_skill_simulation_completion(self):
        with self._skill_sim_condition:
            completion = self._skill_sim_completion
            self._skill_sim_completion = None
            return completion

    def request_skill_simulation_rerun(self):
        """Request an explicit rerun, bypassing a deterministic cached result."""
        with self._skill_sim_condition:
            self._force_next_skill_simulation = True
        self.previous_skills_list = None
        self.open_skill_window = True

    def _stop_skill_simulation_worker(self):
        thread = getattr(self, "_skill_sim_thread", None)
        if not thread:
            return

        with self._skill_sim_condition:
            self._skill_sim_stop = True
            self._skill_sim_pending = None
            process = self._skill_sim_process
            self._skill_sim_condition.notify_all()

        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
            except OSError:
                pass

        if thread is not threading.current_thread():
            thread.join(timeout=5.0)
        if thread.is_alive():
            logger.warning("Timed out waiting for the skill simulation worker to stop")

    def set_skill_window_sim_status(self, status, message=None):
        if not self.skill_browser or not self.skill_browser.alive():
            return

        self.skill_browser.execute_script(
            """
            window.UL_SIM_STATUS = arguments[0];
            window.UL_SIM_MESSAGE = arguments[1] || "";

            window.rerunSkillSimulation = () => {
                if (window.UL_SIM_STATUS === "running") return;
                if (typeof window.resetSkillPlanner === "function") {
                    window.resetSkillPlanner();
                }
                window.UL_SIM_STATUS = "running";
                window.UL_SIM_MESSAGE = "";
                window.updateSimStatus();
                fetch('http://127.0.0.1:3150/rerun-skill-simulation', { method: 'POST' });
            };

            window.ensureSimControls = () => {
                document.querySelectorAll('button[aria-label="Open Umamusume menu"]').forEach(button => {
                    let wrapper = button.parentElement && button.parentElement.parentElement
                        ? button.parentElement.parentElement
                        : button;
                    wrapper.remove();
                });

                let simControlDiv = document.getElementById("ul-sim-controls");
                if (!simControlDiv) {
                    simControlDiv = document.createElement("div");
                    simControlDiv.id = "ul-sim-controls";
                    simControlDiv.style.display = "inline-flex";
                    simControlDiv.style.alignItems = "center";
                    simControlDiv.style.gap = "4px";
                    simControlDiv.style.marginRight = "0";
                    simControlDiv.style.fontSize = "12px";
                    simControlDiv.style.zIndex = "10000";

                    let statusEl = document.createElement("button");
                    statusEl.id = "ul-sim-status";
                    statusEl.type = "button";
                    statusEl.title = "Rerun simulation";
                    statusEl.style.padding = "4px 5px";
                    statusEl.style.border = "1px solid #6b7280";
                    statusEl.style.borderRadius = "4px";
                    statusEl.style.whiteSpace = "nowrap";
                    statusEl.style.lineHeight = "1";
                    statusEl.style.cursor = "pointer";
                    statusEl.onclick = (event) => {
                        event.preventDefault();
                        event.stopPropagation();
                        window.rerunSkillSimulation();
                    };

                    simControlDiv.appendChild(statusEl);
                }

                let staleRerunButton = document.getElementById("ul-sim-rerun");
                if (staleRerunButton) staleRerunButton.remove();

                let settingsButton = document.querySelector("div.styles_header_settings__hx4QQ[aria-expanded='false'], div.styles_header_settings__hx4QQ");
                let header = settingsButton ? (settingsButton.closest("header") || settingsButton.parentElement) : null;
                if (header) {
                    simControlDiv.style.position = "";
                    simControlDiv.style.top = "";
                    simControlDiv.style.right = "";
                    if (simControlDiv.parentElement !== header) {
                        header.insertBefore(simControlDiv, settingsButton);
                    }
                    header.style.gridTemplateColumns = "1fr max-content 75px";
                    header.style.alignItems = "center";
                    simControlDiv.style.gridColumn = "2";
                    simControlDiv.style.gridRow = "1";
                    simControlDiv.style.justifySelf = "end";
                    settingsButton.style.gridColumn = "3";
                    settingsButton.style.gridRow = "1";
                    settingsButton.style.justifySelf = "end";
                }
            };

            window.updateSimStatus = () => {
                window.ensureSimControls();
                let statusEl = document.getElementById("ul-sim-status");
                if (!statusEl) return;

                let status = window.UL_SIM_STATUS || "idle";
                let text = "Sim: Ready";
                let color = "#9ca3af";
                let border = "#6b7280";
                let background = "rgba(107, 114, 128, 0.16)";

                if (status === "running") {
                    text = "Sim: Running...";
                    color = "#fcd34d";
                    border = "#f59e0b";
                    background = "rgba(245, 158, 11, 0.18)";
                } else if (status === "done") {
                    text = "Sim: Done \\u21bb";
                    color = "#86efac";
                    border = "#22c55e";
                    background = "rgba(34, 197, 94, 0.14)";
                } else if (status === "rating") {
                    text = "Sim: Rating mode \\u21bb";
                    color = "#c084fc";
                    border = "#a855f7";
                    background = "rgba(168, 85, 247, 0.16)";
                } else if (status === "waiting") {
                    text = "Sim: Waiting";
                    color = "#93c5fd";
                    border = "#60a5fa";
                    background = "rgba(96, 165, 250, 0.14)";
                } else if (status === "error") {
                    text = "Sim: Error";
                    color = "#fca5a5";
                    border = "#ef4444";
                    background = "rgba(239, 68, 68, 0.14)";
                }

                statusEl.textContent = text;
                statusEl.style.color = color;
                statusEl.style.borderColor = border;
                statusEl.style.background = background;
                statusEl.disabled = false;
                statusEl.style.cursor = status === "running" ? "default" : "pointer";
                statusEl.style.opacity = status === "running" ? "0.75" : "1";
            };

            window.updateSimStatus();
            """,
            status,
            message or ""
        )

    def get_stat_score(self, val):
        return uma_rating.stat_rating(val)

    def get_aptitude_multiplier(self, apt_val):
        if apt_val >= 7: return 1.1     # S or A
        if apt_val >= 5: return 0.9   # B or C
        if apt_val >= 2: return 0.8   # D, E, F
        return 0.7                    # G

    def get_rank_str(self, score):
        for threshold, rank in BASE_RANKS:
            if score < threshold:
                return rank
        for base, step, bound, prefix in HIGH_RANKS:
            if score < bound:
                sub = (score - base) // step
                return f"{prefix}{sub}" if sub > 0 else prefix
        return "UA"

    def get_next_rank_req(self, score):
        for threshold, _ in BASE_RANKS:
            if score < threshold:
                return threshold - score
        for base, step, bound, _ in HIGH_RANKS:
            if score < bound:
                return min(bound, base + ((score - base) // step + 1) * step) - score
        return 0

    def get_rank_score_range(self, score):
        rank_min = 0
        for threshold, _ in BASE_RANKS:
            if score < threshold:
                return rank_min, threshold - 1
            rank_min = threshold

        for base, step, bound, _ in HIGH_RANKS:
            if score < bound:
                sub_rank = (score - base) // step
                rank_min = base + (sub_rank * step)
                rank_max = rank_min + step - 1
                if bound != float('inf'):
                    rank_max = min(rank_max, bound - 1)
                return rank_min, rank_max

        return score, score

    def get_skill_rating_score(self, chara_info, skill_id):
        base_score = self.skill_score_dict.get(int(skill_id), 0)
        cond = self.skill_conditions_dict.get(int(skill_id), "")

        cond_map = {
            "distance_type==1": 'proper_distance_short',
            "distance_type==2": 'proper_distance_mile',
            "distance_type==3": 'proper_distance_middle',
            "distance_type==4": 'proper_distance_long',
            "ground_type==1": 'proper_ground_turf',
            "ground_type==2": 'proper_ground_dirt',
            "running_style==1": 'proper_running_style_nige',
            "running_style==2": 'proper_running_style_senko',
            "running_style==3": 'proper_running_style_sashi',
            "running_style==4": 'proper_running_style_oikomi',
        }

        multiplier = 1.0
        for cond_str, apt_key in cond_map.items():
            if cond_str in cond:
                multiplier = self.get_aptitude_multiplier(chara_info.get(apt_key, 1))
                break

        return round(base_score * multiplier)

    def get_discounted_skill_cost(self, skill_id, skill_data, discount_map, fallback_hint_level=0):
        skill_id = int(skill_id)
        skill_info = skill_data.get(skill_id, {})
        base_cost = skill_info.get("base_cost", self.skill_costs_dict.get(str(skill_id), 0)) or 0
        hint_level = skill_info.get("hint_level", fallback_hint_level) or 0
        discount_percent = discount_map.get(min(hint_level, 5), 0)
        return int(base_cost * (100 - discount_percent) / 100)

    def calculate_max_rating_projection(self, chara_info, skill_data, rating_scores, rating_data, available_sp,
                                        discount_map):
        id_to_group = mdb.get_group_id_dict()
        def get_group_id(skill_id):
            return id_to_group.get(str(skill_id), str(skill_id))

        def get_skill_name(skill_id):
            return self.skill_name_dict.get(int(skill_id), str(skill_id))

        def build_candidate(final_skill_id, hidden_upgrade=False, fallback_hint_level=0, source_skill_id=None):
            final_skill_id = int(final_skill_id)
            if skill_data.get(final_skill_id, {}).get("is_acquired", False):
                return None

            group_id = get_group_id(final_skill_id)
            final_score = rating_scores.get(str(final_skill_id))
            if final_score is None:
                final_score = self.get_skill_rating_score(chara_info, final_skill_id)

            chain_ids = [int(sid) for sid in mdb.get_prerequisite_skill_ids(final_skill_id)]
            chain_ids.append(final_skill_id)

            highest_acquired_score = 0
            total_sp_cost = 0
            full_chain = []
            purchase_chain = []
            for chain_skill_id in chain_ids:
                chain_info = skill_data.get(chain_skill_id, {})
                chain_score = rating_scores.get(str(chain_skill_id))
                if chain_score is None:
                    chain_score = self.get_skill_rating_score(chara_info, chain_skill_id)

                chain_cost = self.get_discounted_skill_cost(
                    chain_skill_id,
                    skill_data,
                    discount_map,
                    fallback_hint_level
                )
                is_acquired = chain_info.get("is_acquired", False)

                full_chain.append({
                    "id": chain_skill_id,
                    "name": get_skill_name(chain_skill_id),
                    "sp_cost": 0 if is_acquired else chain_cost,
                    "score": chain_score,
                    "is_acquired": is_acquired
                })

                if is_acquired:
                    highest_acquired_score = max(highest_acquired_score, chain_score)
                    continue

                total_sp_cost += chain_cost
                purchase_chain.append({
                    "id": chain_skill_id,
                    "name": get_skill_name(chain_skill_id),
                    "sp_cost": chain_cost,
                    "score": chain_score
                })

            score_gain = final_score - highest_acquired_score
            if total_sp_cost <= 0 or score_gain <= 0:
                return None

            return {
                "skill_id": final_skill_id,
                "name": get_skill_name(final_skill_id),
                "group_id": group_id,
                "sp_cost": total_sp_cost,
                "score": score_gain,
                "final_score": final_score,
                "hidden_upgrade": hidden_upgrade,
                "source_skill_id": source_skill_id,
                "chain": purchase_chain,
                "all_chain": full_chain
            }

        groups = {}
        candidates = []
        seen_final_skill_ids = set()

        for skill_id_str, detail in rating_data.items():
            skill_id = int(skill_id_str)
            if skill_data.get(skill_id, {}).get("is_acquired", False):
                continue
            if detail.get("sp_cost", 0) <= 0 or detail.get("score", 0) <= 0:
                continue

            candidate = build_candidate(skill_id)
            if not candidate:
                continue

            seen_final_skill_ids.add(skill_id)
            candidates.append(candidate)
            groups.setdefault(candidate["group_id"], []).append(candidate)

        for single_circle_id, double_circle_id in mdb.get_double_circle_upgrade_dict().items():
            if single_circle_id not in skill_data:
                continue
            if double_circle_id in seen_final_skill_ids:
                continue
            if skill_data.get(double_circle_id, {}).get("is_acquired", False):
                continue

            source_hint_level = skill_data.get(single_circle_id, {}).get("hint_level", 0)
            candidate = build_candidate(
                double_circle_id,
                hidden_upgrade=True,
                fallback_hint_level=source_hint_level,
                source_skill_id=single_circle_id
            )
            if not candidate:
                continue

            seen_final_skill_ids.add(double_circle_id)
            candidates.append(candidate)
            groups.setdefault(candidate["group_id"], []).append(candidate)

        if available_sp <= 0:
            return {"score_gain": 0, "choices": [], "candidates": candidates}

        dp = [0] * (available_sp + 1)
        history = []
        for _, items_in_group in groups.items():
            new_dp = list(dp)
            choices = [None] * (available_sp + 1)
            for candidate in items_in_group:
                cost = candidate["sp_cost"]
                gain = candidate["score"]
                for budget in range(cost, available_sp + 1):
                    candidate_score = dp[budget - cost] + gain
                    if candidate_score > new_dp[budget]:
                        new_dp[budget] = candidate_score
                        choices[budget] = (budget - cost, candidate)
            dp = new_dp
            history.append(choices)

        selected_choices = []
        budget = available_sp
        for choices in reversed(history):
            choice = choices[budget]
            if not choice:
                continue
            budget, candidate = choice
            selected_choices.append(candidate)

        selected_choices.reverse()
        selected_choices.sort(key=lambda item: item["score"], reverse=True)
        return {"score_gain": dp[available_sp], "choices": selected_choices, "candidates": candidates}

    def calculate_uma_rank_score(self, chara_info, skill_data):
        skill_scores_map = {}
        
        stat_keys = ('speed', 'stamina', 'power', 'guts', 'wiz')
        total_score = sum(self.get_stat_score(chara_info.get(k, 0)) for k in stat_keys)

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

        ignored_sids = set()
        for sid, info in skill_data.items():
            if info.get('is_acquired') and not str(sid).startswith('1'):
                ignored_sids.update(mdb.get_prerequisite_skill_ids(int(sid)))

        cond_map = {
            "distance_type==1": 'proper_distance_short',
            "distance_type==2": 'proper_distance_mile',
            "distance_type==3": 'proper_distance_middle',
            "distance_type==4": 'proper_distance_long',
            "ground_type==1": 'proper_ground_turf',
            "ground_type==2": 'proper_ground_dirt',
            "running_style==1": 'proper_running_style_nige',
            "running_style==2": 'proper_running_style_senko',
            "running_style==3": 'proper_running_style_sashi',
            "running_style==4": 'proper_running_style_oikomi',
        }

        for sid, info in skill_data.items():
            if str(sid).startswith('1'):
                continue
                
            base_score = skill_scores.get(sid, 0)
            cond = skill_conditions.get(sid, "")
            
            multiplier = 1.0
            for cond_str, apt_key in cond_map.items():
                if cond_str in cond:
                    multiplier = self.get_aptitude_multiplier(chara_info.get(apt_key, 1))
                    break
                    
            final_s = round(base_score * multiplier)
            skill_scores_map[str(sid)] = final_s
            
            if info.get('is_acquired') and int(sid) not in ignored_sids:
                total_score += final_s
                # print(f"UMA RANK CALC DEBUG -> Acquired Skill ID: {sid} | Base Score: {base_score} | Multiplier: {multiplier} | Score Added: {final_s}")

        unique_mult = 170 if stars >= 3 else 120
        u_score = unique_skill_level * unique_mult
        total_score += u_score
        # print(f"UMA RANK CALC DEBUG -> Unique Skill ID: {unique_skill_id} | Lvl: {unique_skill_level} | Stars: {stars} | Score Added: {u_score} | Base Mult: {unique_mult}")
        
        if unique_skill_id:
            skill_scores_map[str(unique_skill_id)] = u_score
            
        return {"score": total_score, "rank": self.get_rank_str(total_score), "skill_scores": skill_scores_map}

    def load_request(self, message, is_json=False):
        """Decode a request received from CarrotBlender."""
        try:
            unpacked = message if isinstance(message, dict) else msgpack.unpackb(message[4:], strict_map_key=False)
            for key in constants.REQUEST_KEYS_TO_BE_REMOVED:
                unpacked.pop(key, None)
            return unpacked
        except Exception as exc:
            logger.error(f"Error unpacking request: {exc}\n{traceback.format_exc()}")
            return None

    def create_gametora_helper_url_from_start(self, packet_data):
        if 'start_chara' not in packet_data:
            return None
        d = packet_data['start_chara']
        supports = d['support_card_ids'] + [d['friend_support_card_info']['support_card_id']]

        return util.create_gametora_helper_url(d['card_id'], d['scenario_id'], supports)

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

    def _is_stopping(self):
        return self.should_stop or getattr(self.threader, "should_stop", False)

    def open_helper(self):
        if self._is_stopping():
            return
        self.close_browser()

        helper_mode = self.get_helper_ui_mode()
        event_browser = getattr(self, "event_browser", None)
        if event_browser and (
            helper_mode != self.HELPER_UI_MODERN
            or getattr(event_browser, "url", None) != self.helper_url
        ):
            try:
                reopen_events = (
                    helper_mode == self.HELPER_UI_MODERN
                    and event_browser.alive()
                )
            except Exception:
                reopen_events = False
            self.close_event_window()
            # A new training can arrive without the previous finish packet.
            # Do not leave the visible catalog on the old deck in that case.
            self.open_event_window = reopen_events

        start_pos = self.threader.settings["browser_position"]
        topmost = self.threader.settings["browser_topmost"]
        paired = self.threader.settings["browser_pair"]
        if not is_valid_window_rect(start_pos):
            start_pos = self.get_browser_reset_position()

        if helper_mode == self.HELPER_UI_MODERN:
            browser_url = self.MODERN_HELPER_URL
            window_title = self.MODERN_HELPER_TITLE
            page_setup = functools.partial(
                setup_modern_helper_page,
                gametora_url=self.helper_url,
            )
        else:
            browser_url = self.helper_url
            window_title = None
            page_setup = setup_helper_page

        self.browser = horsium.BrowserWindow(
            browser_url,
            self.threader,
            rect=start_pos,
            run_at_launch=page_setup,
            window_title=window_title,
        )

        self.active_helper_mode = helper_mode
        self.set_browser_topmost(topmost)
        self.set_browser_pair(paired)

    def get_helper_ui_mode(self):
        try:
            selected_mode = int(
                self.threader.settings["training_helper_ui"]
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            return self.HELPER_UI_LEGACY
        if selected_mode in (self.HELPER_UI_LEGACY, self.HELPER_UI_MODERN):
            return selected_mode
        return self.HELPER_UI_LEGACY

    def apply_modern_helper_theme(self):
        if (
            not self.browser
            or getattr(self, "active_helper_mode", None) != self.HELPER_UI_MODERN
        ):
            return False
        try:
            return bool(self.browser.execute_script(
                "return window.UL_SET_THEME?.(arguments[0]) || false;",
                helper_theme.build_theme(self.threader.settings),
            ))
        except Exception:
            logger.warning(
                "Could not apply the Modern helper theme:\n"
                f"{traceback.format_exc()}"
            )
            return False

    def _notify_event_window_open_result(self, opened):
        browser = getattr(self, "browser", None)
        if (
            getattr(self, "active_helper_mode", None) != self.HELPER_UI_MODERN
            or not browser
        ):
            return
        try:
            if not browser.alive():
                return
            browser.execute_script(
                "window.UL_EVENT_WINDOW_OPEN_RESULT?.(Boolean(arguments[0]));",
                bool(opened),
            )
        except Exception:
            logger.debug(
                "Could not acknowledge the Events window request:\n"
                f"{traceback.format_exc()}"
            )

    def close_event_window(self):
        event_browser = getattr(self, "event_browser", None)
        self.event_browser = None
        self.open_event_window = False
        if not event_browser:
            return

        rect_callback = self._prepare_async_rect_capture(
            "events", event_browser
        )
        self.save_rect(
            getattr(self, "last_events_rect", None),
            "events_position",
        )
        event_browser.close(rect_callback=rect_callback)

    def update_event_window(self):
        """Create, refresh, or focus the isolated current-run Events window."""
        if (
            self.should_stop
            or self.get_helper_ui_mode() != self.HELPER_UI_MODERN
            or not self.helper_url
        ):
            self._notify_event_window_open_result(False)
            return False

        event_browser = getattr(self, "event_browser", None)
        if event_browser:
            try:
                is_current_run = event_browser.url == self.helper_url
                is_alive = event_browser.alive()
            except Exception:
                is_current_run = False
                is_alive = False

            if is_current_run and is_alive:
                event_browser.focus()
                self._notify_event_window_open_result(True)
                return True

            # A stale URL must never remain visible after the configured run
            # changes. Retiring it also captures its independent geometry.
            self.close_event_window()

        try:
            start_pos = self.threader.settings["events_position"]
        except (AttributeError, KeyError, TypeError):
            start_pos = None
        if not is_valid_window_rect(start_pos):
            start_pos = None

        try:
            event_browser = horsium.BrowserWindow(
                self.helper_url,
                self.threader,
                rect=start_pos,
                run_at_launch=setup_event_window,
                window_title=self.EVENTS_WINDOW_TITLE,
            )
        except Exception:
            logger.error(
                "Could not open the Events window:\n"
                f"{traceback.format_exc()}"
            )
            self._notify_event_window_open_result(False)
            return False

        self.event_browser = event_browser
        if event_browser.alive():
            event_browser.focus()
            self._notify_event_window_open_result(True)
            return True
        self._notify_event_window_open_result(False)
        return False

    def get_browser_reset_position(self):
        game_handle = getattr(self.threader, "game_handle", None) or util.get_game_handle()

        if not game_handle:
            return None

        game_rect = util.get_window_rect(game_handle)
        monitor = util.monitor_from_window(game_handle, 2)
        monitor_info = util.get_monitor_info(monitor) if monitor else None
        workspace_rect = monitor_info.get("Work") if monitor_info else None

        if not game_rect or not workspace_rect:
            return None

        left_side = abs(workspace_rect[0] - game_rect[0])
        right_side = abs(game_rect[2] - workspace_rect[2])
        if left_side > right_side:
            left_x = workspace_rect[0] - 5
            width = left_side
        else:
            left_x = game_rect[2] + 5
            width = right_side
        if width <= 0:
            return None
        return [left_x, workspace_rect[1], width, workspace_rect[3] - workspace_rect[1] + 6]

    def close_browser(self):
        browser = self.browser
        rect_callback = (
            self._prepare_async_rect_capture("helper", browser)
            if browser else None
        )
        self.browser = None
        self.active_helper_mode = None
        self.clear_event_drawer_state()
        if browser:
            self.save_last_browser_rect()
            browser.close(rect_callback=rect_callback)
        return

    def _ensure_rect_tracking(self):
        """Lazily initialize tracking for lightweight test/deserialization paths."""
        if not hasattr(self, "_rect_lock"):
            self._rect_lock = threading.Lock()
        if not hasattr(self, "_rect_versions"):
            self._rect_versions = {name: 0 for name in self.WINDOW_RECT_FIELDS}

    def record_window_rect(self, window_name, rect):
        """Record a browser HTTP rect and invalidate older async captures."""
        self._ensure_rect_tracking()
        rect_attr, setting = self.WINDOW_RECT_FIELDS[window_name]
        recorded_rect = dict(rect) if rect is not None else None
        if recorded_rect:
            if not is_valid_window_rect(recorded_rect):
                logger.debug(
                    f"Ignoring transient invalid {window_name} window rect: "
                    f"{recorded_rect}"
                )
                return
        with self._rect_lock:
            setattr(self, rect_attr, recorded_rect)
            self._rect_versions[window_name] += 1
            # Persist state and its generation in one ordered critical section
            # so an older async callback cannot write after this newer rect.
            self.save_rect(recorded_rect, setting)

    def _prepare_async_rect_capture(self, window_name, browser):
        """Create a final-rect callback guarded by the HTTP rect generation."""
        self._ensure_rect_tracking()
        rect_attr, setting = self.WINDOW_RECT_FIELDS[window_name]
        with self._rect_lock:
            if getattr(self, rect_attr) is None and browser.get_last_window_rect():
                setattr(self, rect_attr, browser.get_last_window_rect())
            expected_version = self._rect_versions[window_name]

        def apply_captured_rect(rect):
            if not is_valid_window_rect(rect):
                return
            with self._rect_lock:
                if self._rect_versions[window_name] != expected_version:
                    return
                captured_rect = dict(rect)
                setattr(self, rect_attr, captured_rect)
                self._rect_versions[window_name] += 1
                self.save_rect(captured_rect, setting)

        return apply_captured_rect

    def save_rect(self, rect_var, setting):
        if rect_var:
            if not is_valid_window_rect(rect_var):
                logger.warning(f"Browser size is invalid for {setting}: {rect_var}")
                return
            if rect_var['x'] <= -10666 and rect_var['y'] <= -10666:
                logger.warning(f"Browser minimized, cannot save position for {setting}: {rect_var}")
                rect_var = None
                return
            rect_list = [rect_var['x'], rect_var['y'], rect_var['width'], rect_var['height']]
            try:
                saved_rect = self.threader.settings[setting]
            except KeyError:
                saved_rect = None
            if saved_rect != rect_list:
                self.threader.settings[setting] = rect_list
            rect_var = None

    def save_last_browser_rect(self):
        self.save_rect(self.last_browser_rect, "browser_position")
        if self.threader.settings["browser_topmost"] != self.browser_topmost:
            self.threader.settings["browser_topmost"] = self.browser_topmost
        if self.threader.settings["browser_pair"] != self.browser_pair:
            self.threader.settings["browser_pair"] = self.browser_pair

    def save_skill_window_rect(self):
        if self.skill_browser:
            self.skill_browser.last_window_rect = self.last_skills_rect
        self.save_rect(self.last_skills_rect, "skills_position")

    def end_training(self):
        if self.training_tracker:
            self.training_tracker = None
        self.open_event_window = False
        self.close_active_event_drawer()
        self._close_transients_requested = False

        with self._skill_sim_condition:
            self._skill_sim_generation += 1
            self._skill_sim_latest_generation = self._skill_sim_generation
            self._skill_sim_pending = None
            self._skill_sim_completion = None
            process = self._skill_sim_process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

        skill_browser = self.skill_browser
        self.skill_browser = None
        if skill_browser:
            rect_callback = self._prepare_async_rect_capture("skill", skill_browser)
            self.save_rect(self.last_skills_rect, "skills_position")
            skill_browser.close(rect_callback=rect_callback)

        schedule_browser = self.schedule_browser
        self.schedule_browser = None
        if schedule_browser:
            rect_callback = self._prepare_async_rect_capture("schedule", schedule_browser)
            self.save_rect(self.last_schedule_rect, "schedule_position")
            schedule_browser.close(rect_callback=rect_callback)

        self.close_event_window()

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

    EVENT_ID_TO_POS_STRING_GLB = {
        7005: 'Victory!',
        7006: 'Solid Showing',
        7007: 'Defeat'
    }

    def get_after_race_event_title(self, event_id):
        if not self.previous_race_program_id:
            return ["PREVIOUS RACE UNKNOWN"]

        race_grade = mdb.get_program_id_grade(self.previous_race_program_id)

        if not race_grade:
            logger.error(f"Race grade not found for program id {self.previous_race_program_id}")
            return ["RACE GRADE NOT FOUND"]

        # These aren't on Gametora anymore, but keep them around in case they update the page again.
        grade_text = ""
        if race_grade > 300:
            grade_text = "Pre/OP"
        elif race_grade > 100:
            grade_text = "G2/G3"
        else:
            grade_text = "G1"
        return [f"{self.EVENT_ID_TO_POS_STRING_GLB[event_id]} ({grade_text})", f"{self.EVENT_ID_TO_POS_STRING_GLB[event_id]}"]

    def clear_event_drawer_state(self):
        active_generation = getattr(self, "_active_event_generation", None)
        if active_generation is not None:
            self.runtime_extensions.on_event_closed(active_generation)
        self._active_event_generation = None
        self._active_event_id = None
        self._active_event_source_available = False
        self._modern_event_chain = None
        self._modern_gametora_event = None
        self._pending_event_selection = None
        self._event_drawer_close_pending = None

    @staticmethod
    def sanitize_modern_event_chain(chain, fallback_title="Event"):
        if not isinstance(chain, dict):
            return None

        title = str(chain.get("title") or fallback_title or "Event")[:240]
        total = chain.get("total", 1)
        index = chain.get("index", 0)
        packet_index = chain.get("packetIndex", chain.get("packet_index", index))
        if (
            isinstance(total, bool)
            or isinstance(index, bool)
            or isinstance(packet_index, bool)
        ):
            return None
        try:
            total = max(1, min(20, int(total)))
            index = max(0, min(total - 1, int(index)))
            packet_index = max(0, min(total - 1, int(packet_index)))
        except (TypeError, ValueError):
            return None
        return {
            "title": title,
            "index": index,
            "total": total,
            "packetIndex": packet_index,
        }

    @staticmethod
    def sanitize_gametora_event(event, fallback_title="Event"):
        if not isinstance(event, dict):
            return None

        def clean_text(value, limit):
            return str(value or "").replace("\x00", "")[:limit].strip()

        outcomes = []
        for raw_outcome in (event.get("outcomes") or [])[:8]:
            if not isinstance(raw_outcome, dict):
                continue
            lines = []
            for raw_line in (raw_outcome.get("lines") or [])[:32]:
                if isinstance(raw_line, dict):
                    text = clean_text(raw_line.get("text"), 500)
                    owned = bool(raw_line.get("owned"))
                else:
                    text = clean_text(raw_line, 500)
                    owned = False
                if text:
                    lines.append({"text": text, "owned": owned})
            if lines:
                outcomes.append({
                    "label": clean_text(raw_outcome.get("label"), 80),
                    "lines": lines,
                })

        if not outcomes:
            return None
        return {
            "title": clean_text(event.get("title") or fallback_title, 240),
            "outcomes": outcomes,
        }

    def close_active_event_drawer(self, generation=None):
        active_generation = getattr(self, "_active_event_generation", None)
        if active_generation is None:
            return True
        if generation is not None and generation != active_generation:
            return False
        page_closed = True
        if (
            self.get_helper_ui_mode() == self.HELPER_UI_MODERN
            and self.browser
            and self.browser.alive()
        ):
            try:
                self.browser.execute_script_in_frame(
                    self.GAMETORA_EVENT_FRAME_ID,
                    "document.getElementById('ul-event-focus-state')?.clear?.();",
                    timeout=1,
                )
            except Exception:
                # Force the next event through full frame setup, which also
                # clears any stale focus CSS before opening support selectors.
                self.browser.gametora_frame_ready = False
                logger.debug(
                    "Could not clear modern GameTora event focus:\n"
                    f"{traceback.format_exc()}"
                )
            try:
                page_closed = self.browser.execute_script(
                    "if (window.UL_CLOSE_EVENT_DRAWER) "
                    "return window.UL_CLOSE_EVENT_DRAWER(arguments[0]);",
                    active_generation,
                )
                page_closed = page_closed is not False
            except Exception:
                logger.debug(f"Could not close modern event drawer:\n{traceback.format_exc()}")
                page_closed = False
        if not page_closed:
            self._event_drawer_close_pending = active_generation
            return False
        self.clear_event_drawer_state()
        return True

    def finish_event_selection_response(self, selection):
        """Resolve the drawer associated with the response to a choice request."""
        if not selection or getattr(self, "_pending_event_selection", None) is not selection:
            return
        generation = selection.get("generation")
        self._pending_event_selection = None
        if getattr(self, "_active_event_generation", None) == generation:
            self.close_active_event_drawer(generation)

    def open_modern_event_drawer(self, generation, event_titles):
        fallback_title = str((event_titles or ["Event"])[0])[:240]
        chain = self.sanitize_modern_event_chain(
            getattr(self, "_modern_event_chain", None),
            fallback_title,
        )
        title = chain["title"] if chain else fallback_title
        payload = {
            "generation": generation,
            "title": title,
            # The configured iframe already contains every applicable event.
            # Open the drawer only after the matching GameTora card is ready;
            # reward packets are annotations on that live source, not fallback
            # event content.
            "eventSourceAvailable": True,
        }
        gametora_event = self.sanitize_gametora_event(
            getattr(self, "_modern_gametora_event", None),
            title,
        )
        if gametora_event:
            payload["gametoraEvent"] = gametora_event
        if chain:
            payload["chain"] = chain
        self.browser.execute_script(
            "return window.UL_OPEN_EVENT_DRAWER(arguments[0]);", payload
        )
        self._active_event_source_available = True

    def open_modern_event_error(
        self,
        generation,
        event_titles,
        event_data,
        reason,
    ):
        title = str((event_titles or ["Event"])[0])[:240]
        story_id = event_data.get("story_id") if isinstance(event_data, dict) else None
        event_id = event_data.get("event_id") if isinstance(event_data, dict) else None
        payload = {
            "generation": generation,
            "title": title,
            "fallbackTitle": "GameTora event unavailable",
            "fallbackMessage": (
                f"Could not display {title!r} in GameTora.\n"
                f"Story ID: {story_id or 'Unknown'}\n"
                f"Event ID: {event_id or 'Unknown'}\n"
                f"Reason: {reason}"
            ),
            "eventSourceAvailable": False,
        }
        self.browser.execute_script(
            "return window.UL_OPEN_EVENT_DRAWER(arguments[0]);", payload
        )
        self._active_event_source_available = False

    def navigate_event_chain(self, direction, generation):
        """Show an adjacent GameTora chain card without changing game-event state."""
        if (
            direction not in (-1, 1)
            or self.get_helper_ui_mode() != self.HELPER_UI_MODERN
            or generation != getattr(self, "_active_event_generation", None)
            or not getattr(self, "_active_event_source_available", False)
            or not self.browser
            or not self.browser.alive()
        ):
            return False

        def navigate_with_driver(driver):
            chain = driver.execute_script(
                """
                const state = document.getElementById("ul-event-focus-state");
                return state?.navigate?.(arguments[0]) || null;
                """,
                direction,
            )
            deadline = time.monotonic() + self.EVENT_FOCUS_VERIFY_TIMEOUT
            latest = {"chain": chain, "event": None}
            while True:
                latest = driver.execute_script(
                    """
                    const state = document.getElementById("ul-event-focus-state");
                    state?.install?.();
                    return {
                        chain: state?.chainInfo?.() || null,
                        event: state?.extract?.() || null
                    };
                    """
                ) or latest
                event = latest.get("event") if isinstance(latest, dict) else None
                if self.sanitize_gametora_event(event):
                    return latest
                if time.monotonic() >= deadline:
                    return latest
                time.sleep(0.05)

        try:
            result = self.browser.run_in_frame(
                self.GAMETORA_EVENT_FRAME_ID, navigate_with_driver
            )
        except Exception:
            logger.warning(
                "Could not navigate the GameTora event chain.\n"
                f"{traceback.format_exc()}"
            )
            return False

        if (
            generation != getattr(self, "_active_event_generation", None)
            or not isinstance(result, dict)
        ):
            return False
        chain = self.sanitize_modern_event_chain(result.get("chain"))
        gametora_event = self.sanitize_gametora_event(
            result.get("event"),
            chain.get("title") if chain else "Event",
        )
        if not chain or not gametora_event:
            return False

        self._modern_event_chain = chain
        self._modern_gametora_event = gametora_event
        self.browser.execute_script(
            "return window.UL_SET_GAMETORA_EVENT(arguments[0], arguments[1], arguments[2]);",
            gametora_event,
            chain,
            generation,
        )
        self.runtime_extensions.on_event_chain_changed(chain, generation)
        return True

    def ensure_modern_event_frame(self):
        if not self.browser:
            return False
        if getattr(self.browser, "gametora_frame_ready", False):
            try:
                healthy = self.browser.execute_script_in_frame(
                    self.GAMETORA_EVENT_FRAME_ID,
                    """
                    const expected = new URL(arguments[0]);
                    const navigationEntry = performance.getEntriesByType("navigation")[0];
                    let requested = null;
                    try {
                        requested = new URL(navigationEntry?.name || "");
                    } catch (_error) {}
                    const allAtOnce = document.getElementById("allAtOnceCheckbox");
                    const expandEvents = document.getElementById("expandEventsCheckbox");
                    const viewer = document.getElementById("viewer-box-main");
                    const eventButtons = viewer?.querySelectorAll(
                        "button[aria-expanded], button[class^='sc-']"
                    ).length || 0;
                    return Boolean(requested)
                        && requested.origin === expected.origin
                        && requested.pathname.replace(/\\/$/, '') === expected.pathname.replace(/\\/$/, '')
                        && requested.search === expected.search
                        && location.origin === expected.origin
                        && location.pathname.replace(/\\/$/, '') === expected.pathname.replace(/\\/$/, '')
                        && (document.readyState === 'interactive' || document.readyState === 'complete')
                        && Boolean(document.getElementById('__next'))
                        && Boolean(allAtOnce?.checked)
                        && !Boolean(expandEvents?.checked)
                        && eventButtons > 0;
                    """,
                    self.helper_url,
                    timeout=3,
                )
                if healthy:
                    return True
            except Exception:
                pass
            self.browser.gametora_frame_ready = False
        try:
            setup_gametora_event_frame(self.browser, self.helper_url, timeout=8)
            return True
        except Exception:
            logger.warning(
                "GameTora event frame is unavailable; the event drawer will remain hidden.\n"
                f"{traceback.format_exc()}"
            )
            return False

    def select_random_support_event(self, event_data, supports):
        support_card_id = (event_data.get('event_contents_info') or {}).get(
            'support_card_id'
        )
        if not support_card_id or support_card_id in supports:
            return True
        logger.info("Random support card detected")

        if self.get_helper_ui_mode() == self.HELPER_UI_MODERN:
            def select_with_driver(driver):
                deadline = time.monotonic() + self.EVENT_FOCUS_VERIFY_TIMEOUT
                last_state = None
                while time.monotonic() < deadline:
                    last_state = driver.execute_script(
                        """
                        const supportId = String(arguments[0]);
                        const quickLookup = document.getElementById("viewer-box-quick");
                        const extraSupport = document.getElementById("boxSupportExtra");
                        if (!quickLookup || !extraSupport) return "waiting-page";

                        const expectedImage = "support_card_s_" + supportId + ".png";
                        if ([...quickLookup.querySelectorAll("img")].some(image =>
                            String(image.getAttribute("src") || "").includes(expectedImage)
                        )) return "ready";

                        const supportDialog = [...document.querySelectorAll(
                            '[role="dialog"][data-gt-modal-root="true"]'
                        )].find(dialog => {
                            const style = window.getComputedStyle(dialog);
                            return dialog.querySelector('button[data-modal-tab="all"]')
                                && dialog.getClientRects().length > 0
                                && style.display !== "none"
                                && style.visibility !== "hidden";
                        });
                        if (!supportDialog) {
                            extraSupport.click();
                            return "opening";
                        }

                        const allTab = supportDialog.querySelector(
                            'button[data-modal-tab="all"]'
                        );
                        if (allTab?.getAttribute("aria-selected") !== "true") {
                            allTab.click();
                            return "changing-filters";
                        }

                        const activeType = [...supportDialog.querySelectorAll(
                            'input[id="speed"], input[id="stamina"], '
                            + 'input[id="power"], input[id="guts"], '
                            + 'input[id="intelligence"], input[id="friend"], '
                            + 'input[id="group"]'
                        )].map(input => supportDialog.querySelector(
                            'label[for="' + input.id + '"]'
                        )).find(label => label?.className.includes("_active"));
                        if (activeType) {
                            activeType.click();
                            return "changing-filters";
                        }

                        const upcoming = supportDialog.querySelector(
                            '#showUpcomingCBSPO, [id*="showUpcoming"]'
                        );
                        if (upcoming && !upcoming.checked) {
                            upcoming.click();
                            return "changing-filters";
                        }

                        const uncheckedRarity = [...supportDialog.querySelectorAll(
                            'input[id^="spo_rarity_"]'
                        )].find(input => !input.checked);
                        if (uncheckedRarity) {
                            uncheckedRarity.click();
                            return "changing-filters";
                        }

                        const card = document.getElementById(supportId);
                        if (card && supportDialog.contains(card)) {
                            card.click();
                            return "selected";
                        }
                        return "waiting-card";
                        """,
                        support_card_id,
                    )
                    if last_state == "ready":
                        return True
                    time.sleep(0.05)

                driver.execute_script(
                    """
                    const supportDialog = [...document.querySelectorAll(
                        '[role="dialog"][data-gt-modal-root="true"]'
                    )].find(dialog =>
                        dialog.querySelector('button[data-modal-tab="all"]')
                        && dialog.getClientRects().length > 0
                    );
                    supportDialog?.querySelector('img[src="/images/ui/close.png"]')
                        ?.closest("button, div")?.click();
                    """
                )
                logger.warning(
                    "GameTora did not finish selecting random support card "
                    f"{support_card_id} (last state: {last_state})."
                )
                return False

            self.browser.execute_script(
                "return window.UL_SET_GAMETORA_LAYOUT_ACTIVE?.(arguments[0]);",
                True,
            )
            try:
                return self.browser.run_in_frame(
                    self.GAMETORA_EVENT_FRAME_ID,
                    select_with_driver,
                )
            finally:
                try:
                    self.browser.execute_script(
                        "return window.UL_SET_GAMETORA_LAYOUT_ACTIVE?.(arguments[0]);",
                        False,
                    )
                except Exception:
                    logger.debug(
                        "Could not restore the hidden GameTora support selector:\n"
                        f"{traceback.format_exc()}"
                    )

        self.browser.execute_script(
            """
            document.getElementById("boxSupportExtra")?.click();
            var cont = document.querySelector('button[data-modal-tab="all"]')?.parentElement?.parentElement?.parentElement;
            if (!cont) cont = document.querySelector('[class^="filters_hide"]')?.parentElement?.parentElement?.parentElement;
            if (!cont) cont = document.querySelector('div[class*="filters_checkbox"]')?.parentElement?.parentElement?.parentElement?.parentElement;
            if (cont) {
                cont.querySelector('button[data-modal-tab="all"]')?.click();
                var rarity = cont.querySelector('[id*="ShowR"]');
                var upcoming = cont.querySelector('[id*="showUpcoming"]');
                var onlyOwned = cont.querySelector('[id*="onlyOwned"]');
                var supportFilters = cont.querySelector('[id*="spo_rarity_3"]')?.parentElement?.parentElement;
                var generalFilters = supportFilters?.previousSibling
                    || document.querySelector('[class^="filters_hide"]')?.parentElement?.parentElement;
                [supportFilters, generalFilters].forEach(group => {
                    if (!group) return;
                    for (let child of group.children) {
                        let filter = child?.querySelector('label');
                        if (filter?.className.includes("_active")) filter.click();
                    }
                });
                if (rarity && !rarity.checked) rarity.click();
                if (upcoming && !upcoming.checked) upcoming.click();
                if (onlyOwned && onlyOwned.checked) onlyOwned.click();
            }
            var card = document.getElementById(String(arguments[0]));
            if (card) card.click();
            else cont?.parentElement?.parentElement?.querySelector('img[src="/images/ui/close.png"]')?.click();
            """,
            support_card_id,
        )
        return True

    @staticmethod
    def determine_event_element_with_executor(execute_script, event_titles):
        ranked_elements = []
        for event_title in event_titles:
            possible_elements = execute_script(
                """
                const buttons = document.querySelectorAll(
                    "div[id^='event-viewer-'] button[aria-expanded], " +
                    "div[id^='event-viewer-'] button[class^='sc-'], " +
                    "div[class^='compatibility_result_box_'] button[aria-expanded], " +
                    "div[class^='compatibility_result_box_'] button[class^='sc-'], " +
                    "#viewer-box-main button[aria-expanded]"
                );
                let matches = [];
                for (let item of buttons) {
                    if (item.textContent.includes(arguments[0])) {
                        matches.push([
                            item.textContent.length - arguments[0].length,
                            item,
                            item.textContent
                        ]);
                    }
                }
                return matches;
                """,
                event_title,
            )
            if possible_elements:
                possible_elements.sort(key=lambda item: item[0])
                ranked_elements.append(possible_elements[0])
        if not ranked_elements:
            return None
        ranked_elements.sort(key=lambda item: item[0])
        logger.info(f"Event element: {ranked_elements[0][2]}")
        return ranked_elements[0][1]

    @staticmethod
    def event_choices_are_visible(execute_script, event_element):
        """Verify that GameTora opened the title-matched event popup."""
        return bool(execute_script(
            """
            const anchor = arguments[0];
            if (!anchor) {
                return false;
            }

            const isOpenOutcome = element => {
                if (!element?.isConnected) return false;
                const style = window.getComputedStyle(element);
                const box = element.matches?.(".tippy-box")
                    ? element
                    : element.querySelector?.(":scope > .tippy-box");
                return style.display !== "none"
                    && box?.getAttribute("data-state") === "visible";
            };
            const normalize = value => String(value || "")
                .replace(/\\s+/g, " ").trim().toLowerCase();
            const eventTitle = normalize(anchor.textContent);
            const linkedIds = [
                anchor.getAttribute("aria-controls"),
                anchor.getAttribute("aria-describedby")
            ].filter(Boolean);
            const candidates = [
                anchor._tippy?.popper,
                ...linkedIds.map(id => document.getElementById(id)),
                ...document.querySelectorAll("div[data-tippy-root]")
            ].filter((element, index, all) =>
                element && all.indexOf(element) === index && isOpenOutcome(element)
            );
            return candidates.some(root =>
                normalize(root.textContent).includes(eventTitle)
            );
            """,
            event_element,
        ))

    def focus_event_in_gametora(
        self,
        event_titles,
        status_names,
    ):
        self._modern_gametora_event = None
        self._last_event_focus_failure = None

        def focus_with_driver(driver):
            event_element = None
            deadline = time.monotonic() + 1.5
            while event_element is None and time.monotonic() < deadline:
                event_element = self.determine_event_element_with_executor(
                    driver.execute_script, event_titles
                )
                if event_element is None:
                    time.sleep(0.1)
            if not event_element:
                self._last_event_focus_failure = (
                    "No title-matched event card was found on the configured GameTora page."
                )
                return False
            chain_info = driver.execute_script(
                """
                const anchor = arguments[0];
                const ownedStatuses = arguments[1] || [];
                let state = document.getElementById("ul-event-focus-state");
                if (!state) {
                    state = document.createElement("div");
                    state.id = "ul-event-focus-state";
                    state.hidden = true;
                    document.body.appendChild(state);
                }
                state.clear?.();
                state.anchor = anchor;
                state.packetAnchor = anchor;
                const active = anchor.closest(
                    "div[id^='event-viewer-'], div[class^='compatibility_result_box_']"
                );
                state.activeContainer = active;
                const previousScroll = {x: window.scrollX, y: window.scrollY};
                const changed = [];
                document.querySelectorAll(
                    "div[id^='event-viewer-'], div[class^='compatibility_result_box_']"
                ).forEach(container => {
                    if (container !== active && !container.contains(anchor)) {
                        changed.push([container, container.style.display]);
                        container.style.display = "none";
                    }
                });
                const isOpenOutcome = element => {
                    if (!element?.isConnected) return false;
                    const style = getComputedStyle(element);
                    const box = element.matches?.(".tippy-box")
                        ? element
                        : element.querySelector?.(":scope > .tippy-box");
                    return style.display !== "none"
                        && box?.getAttribute("data-state") === "visible";
                };
                const normalize = value => String(value || "")
                    .replace(/\\s+/g, " ").trim().toLowerCase();
                const chainMarker = /^\\(\\s*[\\u276f>]+\\s*\\)\\s*/;
                const siblingButtons = Array.from(
                    anchor.parentElement?.querySelectorAll(":scope > button") || []
                );
                const markedChain = siblingButtons.filter(button =>
                    chainMarker.test(String(button.textContent || "").trim())
                );
                const markedIndex = markedChain.indexOf(anchor);
                state.chainAnchors = markedIndex >= 0 ? markedChain : [anchor];
                state.chainIndex = markedIndex >= 0 ? markedIndex : 0;
                state.packetChainIndex = state.chainIndex;
                state.chainInfo = () => ({
                    title: String(state.anchor?.textContent || "Event")
                        .replace(/\\s+/g, " ").trim(),
                    index: state.chainIndex,
                    total: state.chainAnchors.length,
                    packetIndex: state.packetChainIndex
                });
                state.clearFocus = () => {
                    state.activeOutcome?.classList.remove("ul-active-event-outcome");
                    state.focusStyle?.remove();
                    const outcome = state.activeOutcome;
                    const parent = state.outcomeParent;
                    const next = state.outcomeNext;
                    if (outcome?.isConnected && parent?.isConnected) {
                        if (next && next.parentNode === parent) {
                            parent.insertBefore(outcome, next);
                        } else {
                            parent.appendChild(outcome);
                        }
                    }
                    delete state.activeOutcome;
                    delete state.outcomeParent;
                    delete state.outcomeNext;
                    delete state.focusStyle;
                };
                state.install = () => {
                    const currentAnchor = state.anchor;
                    if (!currentAnchor) return null;
                    const eventTitle = normalize(currentAnchor.textContent);
                    const roots = Array.from(
                        document.querySelectorAll("div[data-tippy-root]")
                    ).filter(isOpenOutcome);
                    const linkedOutcome = currentAnchor._tippy?.popper;
                    const outcome = (isOpenOutcome(linkedOutcome) ? linkedOutcome : null)
                    || roots.find(root =>
                        normalize(root.textContent).startsWith(eventTitle)
                    ) || roots.find(root =>
                        normalize(root.textContent).includes(eventTitle)
                    );
                    if (!outcome) return null;
                    if (
                        state.activeOutcome === outcome
                        && state.focusStyle?.isConnected
                    ) {
                        return outcome;
                    }

                    state.clearFocus?.();
                    state.activeOutcome = outcome;
                    state.outcomeParent = outcome.parentNode;
                    state.outcomeNext = outcome.nextSibling;
                    outcome.classList.add("ul-active-event-outcome");
                    if (outcome.parentNode !== document.body) {
                        document.body.appendChild(outcome);
                    }

                    const focusStyle = document.createElement("style");
                    focusStyle.id = "ul-event-focus-style";
                    focusStyle.textContent = `
                        html, body {
                            width: 100% !important;
                            height: auto !important;
                            min-height: 0 !important;
                            margin: 0 !important;
                            padding: 0 !important;
                            overflow: hidden !important;
                            background: var(--c-bg-main, #11141b) !important;
                        }
                        body > :not(.ul-active-event-outcome):not(script):not(style) {
                            display: none !important;
                        }
                        .ul-active-event-outcome {
                            position: static !important;
                            inset: auto !important;
                            transform: none !important;
                            width: 100% !important;
                            max-width: none !important;
                            margin: 0 !important;
                            padding: 0 !important;
                            visibility: visible !important;
                        }
                        .ul-active-event-outcome > .tippy-box {
                            width: 100% !important;
                            max-width: none !important;
                            margin: 0 !important;
                            border-radius: 0 !important;
                            background: #11141b !important;
                            color: #f3f5f8 !important;
                            box-shadow: none !important;
                        }
                        .ul-active-event-outcome .tippy-content {
                            padding: 0 !important;
                            font-family: Inter, "Segoe UI", system-ui, sans-serif !important;
                            font-size: 13px !important;
                        }
                        .ul-active-event-outcome .tippy-content > div > div > div:first-child {
                            display: none !important;
                        }
                        .ul-active-event-outcome .tippy-content > div > div > div:last-child {
                            display: grid !important;
                            gap: 6px !important;
                            padding: 8px !important;
                            background: #11141b !important;
                        }
                        .ul-active-event-outcome .tippy-content > div > div > div:last-child > div {
                            margin: 0 !important;
                            padding: 8px 10px !important;
                            border: 1px solid rgba(255, 255, 255, 0.11) !important;
                            border-radius: 6px !important;
                            background: #202430 !important;
                            line-height: 1.35 !important;
                        }
                        .ul-active-event-outcome .tippy-content > div > div > div:last-child > div
                            > div:first-child:not(:last-child) {
                            margin: 0 0 4px !important;
                            color: #aeb5c2 !important;
                            font-size: 11px !important;
                            font-weight: 700 !important;
                            letter-spacing: 0.04em !important;
                            text-transform: uppercase !important;
                        }
                        .ul-active-event-outcome .tippy-content > div > div > div:last-child > div
                            > div:last-child {
                            line-height: 1.4 !important;
                        }
                        .ul-active-event-outcome .tippy-arrow {
                            display: none !important;
                        }
                        .ul-active-event-outcome * {
                            max-height: none !important;
                            overflow-y: visible !important;
                        }
                    `;
                    document.head.appendChild(focusStyle);
                    state.focusStyle = focusStyle;
                    window.scrollTo(0, 0);
                    setTimeout(() => state.greyOwned?.(), 0);
                    return outcome;
                };
                state.extract = () => {
                    const outcome = state.activeOutcome || state.install?.();
                    if (!outcome) return null;
                    const content = outcome.querySelector(".tippy-content");
                    const card = content?.firstElementChild?.firstElementChild;
                    const header = card?.firstElementChild;
                    const outcomeList = header?.nextElementSibling;
                    if (!card || !outcomeList) return null;

                    const clean = value => String(value || "")
                        .replace(/\\s+/g, " ").trim();
                    const owned = node => Array.from(
                        node?.querySelectorAll?.('span[class^="utils_linkcolor"]') || []
                    ).some(element => ownedStatuses.includes(clean(element.textContent)));
                    const lineValues = node => {
                        const childLines = Array.from(node?.children || [])
                            .filter(child => child.tagName !== "BR")
                            .map(child => ({
                                text: clean(child.textContent),
                                owned: owned(child)
                            }))
                            .filter(line => line.text);
                        if (childLines.length) return childLines;
                        const text = clean(node?.textContent);
                        return text ? [{text, owned: owned(node)}] : [];
                    };
                    const outcomes = Array.from(outcomeList.children || [])
                        .map(group => {
                            const children = Array.from(group.children || []);
                            let label = "";
                            let body = group;
                            if (children.length >= 2) {
                                label = clean(children[0].textContent);
                                body = children[1];
                            } else if (children.length === 1) {
                                body = children[0];
                            }
                            return {label, lines: lineValues(body)};
                        })
                        .filter(item => item.lines.length);
                    if (!outcomes.length) return null;
                    return {
                        title: clean(header.textContent) || clean(state.anchor?.textContent),
                        outcomes
                    };
                };
                state.ensureOpen = target => {
                    if (!target || state.anchor !== target) return null;
                    const outcome = state.install?.();
                    if (outcome) return outcome;
                    if (target.getAttribute("aria-expanded") !== "true") {
                        target.click();
                    }
                    setTimeout(() => state.install?.(), 0);
                    return null;
                };
                state.openAnchor = (target, index) => {
                    if (!target) return state.chainInfo?.() || null;
                    const previousAnchor = state.anchor;
                    state.clearFocus?.();
                    if (
                        previousAnchor
                        && previousAnchor !== target
                        && previousAnchor.getAttribute("aria-expanded") === "true"
                    ) {
                        previousAnchor.click();
                    }
                    state.anchor = target;
                    state.chainIndex = index;
                    target.scrollIntoView({block: "start", behavior: "auto"});
                    state.ensureOpen(target);
                    setTimeout(() => state.ensureOpen?.(target), 200);
                    setTimeout(() => state.ensureOpen?.(target), 600);
                    return state.chainInfo();
                };
                state.navigate = direction => {
                    const step = Number(direction);
                    if (![-1, 1].includes(step)) return state.chainInfo();
                    const index = state.chainIndex + step;
                    if (index < 0 || index >= state.chainAnchors.length) {
                        return state.chainInfo();
                    }
                    return state.openAnchor(state.chainAnchors[index], index);
                };
                state.clear = () => {
                    const currentAnchor = state.anchor;
                    const hadOutcome = Boolean(state.activeOutcome);
                    state.clearFocus?.();
                    if (
                        currentAnchor
                        && (
                            hadOutcome
                            || currentAnchor.getAttribute("aria-expanded") === "true"
                        )
                    ) {
                        currentAnchor.click();
                    }
                    changed.forEach(([element, display]) => element.style.display = display);
                    window.scrollTo(previousScroll.x, previousScroll.y);
                    delete state.activeContainer;
                    delete state.anchor;
                    delete state.packetAnchor;
                    delete state.chainAnchors;
                    delete state.chainIndex;
                    delete state.packetChainIndex;
                    delete state.chainInfo;
                    delete state.clearFocus;
                    delete state.install;
                    delete state.extract;
                    delete state.ensureOpen;
                    delete state.openAnchor;
                    delete state.navigate;
                    delete state.greyOwned;
                    delete state.clear;
                };
                state.openAnchor(anchor, state.chainIndex);
                state.greyOwned = () => {
                    document.querySelectorAll(
                        'div[data-tippy-root] span[class^="utils_linkcolor"]'
                    ).forEach(element => {
                        if (ownedStatuses.includes(element.textContent.trim())) {
                            element.style.color = "gray";
                        }
                    });
                };
                state.greyOwned();
                setTimeout(() => state.greyOwned?.(), 100);
                return state.chainInfo();
                """,
                event_element,
                status_names,
            )
            self._modern_event_chain = self.sanitize_modern_event_chain(
                chain_info,
                str((event_titles or ["Event"])[0]),
            )
            verification_deadline = (
                time.monotonic() + self.EVENT_FOCUS_VERIFY_TIMEOUT
            )
            popup_seen = False
            while True:
                if self.event_choices_are_visible(
                    driver.execute_script,
                    event_element,
                ):
                    popup_seen = True
                    extracted_event = driver.execute_script(
                        """
                        const state = document.getElementById("ul-event-focus-state");
                        state?.install?.();
                        return state?.extract?.() || null;
                        """,
                        event_element,
                    )
                    gametora_event = self.sanitize_gametora_event(
                        extracted_event,
                        str((event_titles or ["Event"])[0]),
                    )
                    if gametora_event:
                        self._modern_gametora_event = gametora_event
                        self._last_event_focus_failure = None
                        return True
                if time.monotonic() >= verification_deadline:
                    break
                time.sleep(0.1)

            # Do not show a partial or unrelated GameTora page when the event
            # popup never became usable. Undo the iframe-only focus changes so
            # a later event starts from a clean page.
            try:
                driver.execute_script(
                    """
                    document.getElementById("ul-event-focus-state")?.clear?.();
                    """,
                    event_element,
                )
            except Exception:
                pass
            self._last_event_focus_failure = (
                "The title-matched GameTora popup could not be isolated for display."
                if popup_seen
                else "The title-matched GameTora card did not open a visible popup."
            )
            return False

        if self.get_helper_ui_mode() == self.HELPER_UI_MODERN:
            return self.browser.run_in_frame(
                self.GAMETORA_EVENT_FRAME_ID, focus_with_driver
            )

        event_element = self.determine_event_element_with_executor(
            self.browser.execute_script, event_titles
        )
        if not event_element:
            return False
        self.browser.execute_script(
            """
            if (arguments[0].getAttribute("aria-expanded") !== "true") arguments[0].click();
            arguments[0].scrollIntoView({block: "end", behavior: "smooth"});
            arguments[0].parentElement.querySelectorAll(
                'div[data-tippy-root] span[class^="utils_linkcolor"]'
            ).forEach(element => {
                if (arguments[1].includes(element.textContent.trim())) element.style.color = "gray";
            });
            """,
            event_element,
            status_names,
        )
        return True

    def show_multi_choice_event(
        self,
        data,
        event_data,
        event_titles,
        supports,
    ):
        modern = self.get_helper_ui_mode() == self.HELPER_UI_MODERN
        chara_info = (
            data.get("chara_info")
            if isinstance(data, dict) and isinstance(data.get("chara_info"), dict)
            else {}
        )
        generation = None
        if modern:
            previous_generation = getattr(self, "_active_event_generation", None)
            if previous_generation is not None:
                self.close_active_event_drawer(previous_generation)
            self._event_generation += 1
            generation = self._event_generation
            self._active_event_generation = generation
            self._active_event_id = event_data.get("event_id")
            self._modern_event_chain = None
            self._modern_gametora_event = None
            self._pending_event_selection = None
            self._event_drawer_close_pending = None
            self.runtime_extensions.on_event_opened(generation)
            if not self.ensure_modern_event_frame():
                if self._active_event_generation == generation:
                    self.open_modern_event_error(
                        generation,
                        event_titles,
                        event_data,
                        "The configured GameTora frame did not become ready.",
                    )
                return
        source_available = False
        try:
            random_support_ready = self.select_random_support_event(
                event_data, supports
            )
            if random_support_ready is False:
                support_card_id = (
                    event_data.get('event_contents_info') or {}
                ).get('support_card_id')
                self._last_event_focus_failure = (
                    "GameTora could not load random support card "
                    f"{support_card_id} in Quick Lookup."
                )
            else:
                status_names = [
                    self.status_name_dict[status_id]
                    for status_id in chara_info.get('chara_effect_id_array', [])
                    if status_id in self.status_name_dict
                ]
                source_available = self.focus_event_in_gametora(
                    event_titles,
                    status_names,
                )
        except Exception:
            logger.warning(
                "Could not focus the GameTora event; leaving the event drawer hidden.\n"
                f"{traceback.format_exc()}"
            )

        if modern and self._active_event_generation == generation:
            if source_available:
                self.open_modern_event_drawer(generation, event_titles)
            else:
                self.browser.gametora_frame_ready = False
                self.open_modern_event_error(
                    generation,
                    event_titles,
                    event_data,
                    getattr(self, "_last_event_focus_failure", None)
                    or "No usable title-matched event card was found.",
                )
        if not source_available:
            logger.info(
                f"Could not find event on GT page: {event_data.get('story_id')} - "
                f"{event_data.get('event_id')} : {event_titles}"
            )


    def handle_response(self, message, is_json=False):
        if self._is_stopping():
            return

        data = message
        pending_selection = getattr(self, "_pending_event_selection", None)
        response_has_data = False

        if not data:
            return

        if self.threader.settings["save_packets"]:
            # logger.debug("Response:")
            # logger.debug(json.dumps(data))
            self.to_json(data, str(datetime.now()).replace(":", "-") + "_packet_in.json")

        try:
            if 'data' not in data:
                # logger.info("This packet doesn't have data :)")
                return

            data = data['data']
            response_has_data = True

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

            self._prepare_transient_cleanup(data)

            # Run ended
            if 'single_mode_factor_select_common' in data or 'single_mode_finish_common' in data:
                self.end_training()
                return

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
                    logger.warning("No start_time, using strftime")
                    training_id = time.strftime("%Y-%m-%d %H:%M:%S")
                if not self.training_tracker or not self.training_tracker.training_id_matches(training_id):
                    # Update cached dicts first
                    mdb.update_mdb_cache()
                    self.training_tracker = training_tracker.TrainingTracker(training_id, data['chara_info']['card_id'])

                is_new_training = getattr(self, 'current_training_id', None) != training_id
                if is_new_training:
                    self.current_training_id = training_id
                    self.completed_races = {}
                    self.extra_race_info = {}
                    
                    self.current_race_bonus = 0
                    self.last_synced_turn = -1
                    deck = data['chara_info'].get('support_card_array', [])
                    if deck:
                        self.current_race_bonus = mdb.get_deck_race_bonus(deck)

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
                    # Higher ranks imply every lower positive-rate rank in the
                    # same master-data chain. Negative/X rows stay excluded by
                    # get_prerequisite_skill_ids().
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
                        next_rank_id = mdb.get_next_skill_id_in_chain(skill_id)
                        if (
                            next_rank_id is not None
                            and str(next_rank_id) in self.skill_costs_dict
                            and next_rank_id not in self.skill_data
                        ):
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

                if not self.browser or not self.browser.current_url().startswith(self.browser.url.split("?", 1)[0]):
                    logger.info("GT tab not open, opening tab")
                    self.helper_url = util.create_gametora_helper_url(outfit_id, scenario_id, supports)
                    logger.debug(f"Helper URL: {self.helper_url}")
                    self.open_helper()

                self.update_helper_table(data)

                schedule_optimizer_enabled = self.helper_table.show_schedule_optimizer_button
                if schedule_optimizer_enabled and is_new_training and self.schedule_browser and self.schedule_browser.alive():
                    self.schedule_browser.execute_script("window.clearCompletedRaces();")

                history = (data['chara_info'].get('race_history') or data.get('race_history')) if schedule_optimizer_enabled else None
                if history:
                    for race in history:
                        if race.get('result_rank', 0) == 1:
                            turn = race.get('turn', 1) - 1
                            program_id = race.get('program_id')
                            grade = mdb.get_program_id_grade(program_id)
                            if grade in (100, 200, 300, 900):
                                race_name = mdb.get_race_program_name_dict().get(program_id, "")
                                if race_name:
                                    if race_name == "Tokyo Yushun (Japanese Derby)":
                                        race_name = "Japanese Derby (Tokyo Yushun)"
                                    elif race_name == "Milers Cup":
                                        race_name = "Milers Cup (Yomiuri)"
                                    elif race_name == "JBC Ladies’ Classic" or race_name == "JBC Ladies' Classic":
                                        race_name = "JBC Ladies' Classic"
                                        
                                    self.completed_races[str(turn)] = race_name
                                    
                                    if grade == 900:
                                        length = mdb.get_race_distance_dict().get(program_id)
                                        surface_id = mdb.get_race_surface_dict().get(program_id)
                                        surface = "Turf" if surface_id == 1 else "Dirt"
                                        
                                        dist_str = "Long"
                                        if length <= 1400: dist_str = "Sprint"
                                        elif length <= 1800: dist_str = "Mile"
                                        elif length <= 2400: dist_str = "Medium"
                                        
                                        self.extra_race_info[race_name] = {
                                            "name": race_name,
                                            "length": length,
                                            "distance": dist_str,
                                            "surface": surface,
                                            "grade": "Pre-OP" # Treat Debut as Pre-OP/EX for scheduler display
                                        }
                                    
                                    logger.debug(f"Trackblazer Sync: Locked race '{race_name}' at turn index {turn}")
                
                current_turn = data['chara_info'].get('turn', 1) - 1
                if schedule_optimizer_enabled and getattr(self, 'last_synced_turn', -1) != current_turn:
                    self.sync_schedule_window(current_turn)
                    self.last_synced_turn = current_turn

            response_event_generation = None
            if 'unchecked_event_array' in data and data['unchecked_event_array']:
                logger.debug("Training event detected")
                event_data = data['unchecked_event_array'][0]
                event_chara_info = data.get("chara_info")
                if not isinstance(event_chara_info, dict):
                    cached_data = (
                        self.last_helper_data
                        if isinstance(self.last_helper_data, dict)
                        else {}
                    )
                    event_chara_info = cached_data.get("chara_info") or {}
                event_titles = mdb.get_event_titles(
                    event_data.get('story_id'), event_chara_info.get('card_id')
                )
                if event_data.get('event_id') in (7005, 7006, 7007):
                    logger.debug("After-race event detected.")
                    event_titles = self.get_after_race_event_title(event_data['event_id'])
                logger.debug(f"Event titles: {event_titles}")
                self.runtime_extensions.on_event_detected(
                    event_data,
                    event_titles,
                )

                choice_array = normalize_choice_array(
                    (event_data.get('event_contents_info') or {}).get('choice_array')
                )
                if len(choice_array) > 1:
                    current_supports = [
                        card.get('support_card_id')
                        for card in event_chara_info.get('support_card_array', [])
                    ]
                    event_packet_data = data
                    if not isinstance(data.get("chara_info"), dict):
                        event_packet_data = dict(data)
                        event_packet_data["chara_info"] = event_chara_info
                    self.show_multi_choice_event(
                        event_packet_data,
                        event_data,
                        event_titles,
                        current_supports,
                    )
                    response_event_generation = getattr(
                        self, "_active_event_generation", None
                    )
                elif (
                    self.get_helper_ui_mode() == self.HELPER_UI_MODERN
                    and getattr(self, "_active_event_generation", None) is not None
                ):
                    # Automatic and one-choice events never open the drawer,
                    # but they do resolve any previous multi-choice event.
                    self.close_active_event_drawer()

            self.runtime_extensions.on_response(
                data,
                response_event_generation
                or getattr(self, "_active_event_generation", None),
            )

            if 'chara_info' not in data and self.last_helper_data:
                if 'reserved_race_array' in data:
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
        finally:
            if response_has_data:
                # Race/end fast paths and parser errors still belong to the
                # response that followed the outgoing choice. A chained event
                # replaces this pending record and is therefore left open.
                self.finish_event_selection_response(pending_selection)

    def handle_request(self, message, is_json=False):
        if self._is_stopping():
            return

        data = self.load_request(message, is_json=is_json)

        if not data:
            return

        if self.threader.settings["save_packets"]:
            # logger.debug("Request:")
            # logger.debug(json.dumps(data))
            self.to_json(data, str(datetime.now()).replace(":", "-") + "_packet_out.json")

        self.previous_request = data
        choice_number = None
        if isinstance(data, dict) and "choice_number" in data:
            try:
                choice_number = int(data.get("choice_number"))
            except (TypeError, ValueError):
                choice_number = None
        if choice_number is not None and choice_number > 0:
            active_generation = getattr(self, "_active_event_generation", None)
            active_event_id = getattr(self, "_active_event_id", None)
            request_event_id = data.get("event_id")
            if (
                active_generation is not None
                and (request_event_id is None or request_event_id == active_event_id)
            ):
                self._pending_event_selection = {
                    "generation": active_generation,
                }

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

    def update_helper_table(self, data):
        if self._is_stopping():
            return

        overlay_html = self.helper_table.create_helper_elements(
            data, self.last_helper_data
        )
        self.last_helper_data = data
        if not overlay_html:
            return

        helper_mode = self.get_helper_ui_mode()
        mode_changed = helper_mode != self.active_helper_mode
        # BrowserWindow performs a one-command page sentinel before each
        # action and recovers a missing/stale session itself. Avoid a separate
        # window_handles round trip on every training packet.
        primary_missing = not self.browser
        if mode_changed or primary_missing:
            self.open_helper()

        if helper_mode == self.HELPER_UI_MODERN:
            self.update_modern_dashboard(self.browser, overlay_html)
        elif self.browser:
            self.browser.execute_script("""
                window.UL_DATA.overlay_html = arguments[0];
                if (window.set_schedule_button_enabled) {
                    window.set_schedule_button_enabled(arguments[1]);
                }
                window.update_overlay();
                """,
                overlay_html,
                self.helper_table.show_schedule_optimizer_button,
            )

    def update_modern_dashboard(self, browser, dashboard_html):
        if browser and dashboard_html:
            dashboard_metrics = browser.execute_script(
                "return window.UL_UPDATE_DASHBOARD(arguments[0], arguments[1]);",
                dashboard_html,
                self.helper_table.show_schedule_optimizer_button,
            )
            fit_signature = None
            if isinstance(dashboard_metrics, dict):
                grid_width = dashboard_metrics.get("gridWidth")
                scrollbar_width = dashboard_metrics.get("scrollbarWidth")
                if (
                    isinstance(grid_width, (int, float))
                    and not isinstance(grid_width, bool)
                    and isinstance(scrollbar_width, (int, float))
                    and not isinstance(scrollbar_width, bool)
                ):
                    fit_signature = (grid_width, scrollbar_width)
            if (
                fit_signature is not None
                and fit_signature
                != getattr(browser, "modern_dashboard_fit_signature", None)
            ):
                try:
                    applied_rect = browser.fit_modern_table_width(
                        dashboard_metrics
                    )
                    if is_valid_window_rect(applied_rect):
                        self.record_window_rect("helper", applied_rect)
                        browser.modern_dashboard_fit_signature = fit_signature
                except Exception:
                    logger.warning(
                        "Could not fit the Modern helper to its first dashboard:\n"
                        f"{traceback.format_exc()}"
                    )

    def update_skill_window(self, simulation_completion=None):
        if self.should_stop:
            return

        if not self.last_data:
            return
        chara_info = self.last_data.get('chara_info') if isinstance(self.last_data, dict) else None

        if not self.skill_browser:
            self.skill_browser = horsium.BrowserWindow(
                "https://gametora.com/umamusume/skills",
                self.threader,
                rect=self.threader.settings['skills_position'],
                run_at_launch=setup_skill_window
            )
        else:
            self.skill_browser.ensure_tab_open()

        if (
            self.active_helper_mode == self.HELPER_UI_LEGACY
            and self.browser
            and self.browser.alive()
        ):
            self.browser.execute_script("""window.skill_window_opened();""")

        if not chara_info:
            logger.debug("Skill window opened before character info was available; skipping skill data update.")
            self.previous_skills_list = None
            self.set_skill_window_sim_status("waiting", "for character data")
            return

        self._skill_window_last_state_key = self._skill_window_state_key()
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

        CM_CONFIGS = mdb.get_champions_meeting_configs(limit=2)
        available_cm_definitions = tuple(CM_CONFIGS)
        if not available_cm_definitions:
            logger.error("No complete Champions Meeting definitions found in master.mdb")
            self.set_skill_window_sim_status("error", "CM data unavailable")
            return

        default_cm_definition = available_cm_definitions[0]
        cm_pref = self.skill_browser.execute_script(
            "return window.localStorage.getItem('UL_CM_DEF') || arguments[0];",
            str(default_cm_definition),
        )
        try:
            selected_cm_definition = int(cm_pref)
        except (TypeError, ValueError):
            selected_cm_definition = self.selected_cm_definition

        if selected_cm_definition not in available_cm_definitions:
            selected_cm_definition = default_cm_definition

        self.selected_cm_definition = selected_cm_definition
        cm_options = [
            {"id": cm_id, "label": f"{cm_id} {CM_CONFIGS[cm_id]['name'].replace(' Cup', '')}"}
            for cm_id in available_cm_definitions
        ]

        total_iterations = 2000

        if is_ace_mode:
            u_speed = chara_info.get('speed', 0)
            u_stamina = chara_info.get('stamina', 0)
            u_power = chara_info.get('power', 0)
            u_guts = chara_info.get('guts', 0)
            u_wisdom = chara_info.get('wiz', 0)
            u_condition = "GOOD"
        else:
            u_speed = 1500
            u_stamina = 1200
            u_power = 1155
            u_guts = 600
            u_wisdom = 1000
            u_condition = "BEST"

        cm_data = CM_CONFIGS[selected_cm_definition]
        mock_payload = {
            "baseSetting": {
                "umaStatus": {
                    "charaName": "Place Holder",
                    "speed": u_speed, "stamina": u_stamina, "power": u_power, "guts": u_guts, "wisdom": u_wisdom,
                    "condition": u_condition, "style": STYLE_INTERNAL_MAP[self.style],
                    "distanceFit": "S", "surfaceFit": "A", "styleFit": "A",
                    "popularity": 1, "gateNumber": 0,
                },
                "track": {
                    # GOOD(1, "良"),
                    # YAYAOMO(2, "稍重"),
                    # OMO(3, "重"),
                    # BAD(4, "不良"),
                    "location": cm_data["location"], 
                    "course": cm_data["course"], 
                    "condition": cm_data["ground_condition"], 
                    "gateCount": 9
                },
                "season": cm_data["season"],
                "weather": cm_data["weather"],
                "positionKeepMode": "NONE"
            },
            # UI-acquired prerequisites prevent duplicate purchases, but they
            # are not extra learned effects in the simulator baseline.
            "acquiredSkillIds": sorted({skill["skill_id"] for skill in chara_info["skill_array"]}),
            "unacquiredSkillIds": unacquired_skills_list,
            "iterations": total_iterations
        }

        sim_failed = False
        if is_rating_mode:
            results = {"candidates": {}}
        else:
            cache_key = self._skill_simulation_key(mock_payload)
            completion_matches = (
                simulation_completion is not None
                and simulation_completion[0] == self._skill_sim_latest_generation
                and simulation_completion[1] == cache_key
            )
            if completion_matches:
                results = simulation_completion[2]
            else:
                with self._skill_sim_condition:
                    force_simulation = self._force_next_skill_simulation
                    self._force_next_skill_simulation = False
                _, results, is_pending = self._queue_skill_simulation(
                    mock_payload,
                    force=force_simulation,
                )
                if is_pending:
                    self.set_skill_window_sim_status("running")
                    return
            sim_failed = not results

        discount_map = {0: 0, 1: 10, 2: 20, 3: 30, 4: 35, 5: 40}
        rating_calc = self.calculate_uma_rank_score(chara_info, self.skill_data)
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

        available_sp = chara_info.get('skill_point', 0)
        projection = self.calculate_max_rating_projection(
            chara_info,
            self.skill_data,
            rating_scores,
            rating_data,
            available_sp,
            discount_map
        )
        max_score_gain = projection.get("score_gain", 0)
        projected_choices = projection.get("choices", [])
        planner_candidates = projection.get("candidates", [])
        projected_score = uma_score + max_score_gain
        projected_rank = self.get_rank_str(projected_score)
        projected_rank_min, projected_rank_max = self.get_rank_score_range(projected_score)
        
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
        sim_status = "rating" if is_rating_mode else ("error" if sim_failed else "done")
        sim_status_message = ""

        # The skill planner's renderer is still coupled to its changing data
        # arguments. Keep this single update call intact until the renderer is
        # split into a separately versioned browser asset; changing it here is
        # substantially riskier than the other round-trip reductions.
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
            let proj_choices = arguments[14] || [];
            let uma_next = arguments[15] || 0;
            let proj_next = arguments[16] || 0;
            let cmOptions = arguments[17] || [];
            let selectedCmDefinition = String(arguments[18] || 17);
            let projRankMin = arguments[19] || 0;
            let projRankMax = arguments[20] || 0;
            let availableSp = arguments[21] || 0;
            let plannerCandidates = arguments[22] || [];
            window.UL_SIM_STATUS = arguments[23] || "done";
            window.UL_SIM_MESSAGE = arguments[24] || "";

            const ownedSkillIds = new Set((acquired_list || []).map(id => String(id)));
            const plannerRows = new Map();
            const plannerSelectedByGroup = new Map();
            const candidateBySkillId = new Map();

            for (const candidate of plannerCandidates || []) {
                candidate.skill_id = String(candidate.skill_id);
                candidate.group_id = String(candidate.group_id);
                candidate.source_skill_id = candidate.source_skill_id ? String(candidate.source_skill_id) : null;
                candidate.chain = candidate.chain || [];
                candidate.all_chain = candidate.all_chain || candidate.chain || [];
                candidate.sp_cost = Number(candidate.sp_cost || 0);
                candidate.score = Number(candidate.score || 0);
                candidate.final_score = Number(candidate.final_score || 0);
                for (const step of candidate.chain) {
                    step.id = String(step.id);
                    step.sp_cost = Number(step.sp_cost || 0);
                    step.score = Number(step.score || 0);
                }
                for (const step of candidate.all_chain) {
                    step.id = String(step.id);
                    step.sp_cost = Number(step.sp_cost || 0);
                    step.score = Number(step.score || 0);
                }

                candidateBySkillId.set(candidate.skill_id, candidate);
            }

            function collectChoiceSkillIds(choices) {
                let picked = new Set();
                for (const choice of choices || []) {
                    if (choice.skill_id) picked.add(String(choice.skill_id));
                    if (choice.source_skill_id) picked.add(String(choice.source_skill_id));
                    for (const step of (choice.chain || [])) {
                        if (step.id) picked.add(String(step.id));
                    }
                }
                return picked;
            }

            const baseMaxPickedSkillIds = collectChoiceSkillIds(proj_choices);
            let currentMaxPickedSkillIds = new Set(baseMaxPickedSkillIds);
    
            function formatCondition(cond) {
                if (!cond) return "";
                return cond.replace(/([a-zA-Z_]+)|(==|>=|<=|!=|>|<|=|&|!)|(\\d+(?:\\.\\d+)?s?)/g, function(match, word, op, num) {
                    if (word) {
                        if (word === 'OR') return `<span style="color: #d65d8a;">${word}</span>`;
                        return `<span style="color: #c084fc;">${word}</span>`;
                    }
                    if (op) return `<span style="color: #60a5fa;">${op}</span>`;
                    if (num) return `<span style="color: #73c991;">${num}</span>`;
                    return match;
                });
            }

            let plannerStyle = document.getElementById("ul-planner-style");
            if (!plannerStyle) {
                plannerStyle = document.createElement("style");
                plannerStyle.id = "ul-planner-style";
                plannerStyle.innerHTML = `
                    .sim-data-badge.ul-planner-clickable { cursor: pointer !important; }
                    .sim-data-badge.ul-planner-selected {
                        background: rgba(56, 189, 248, 0.16) !important;
                        border-color: #38bdf8 !important;
                        box-shadow: inset 0 0 0 1px rgba(56, 189, 248, 0.45), 0 0 8px rgba(56, 189, 248, 0.22) !important;
                    }
                    .sim-data-badge.ul-planner-unaffordable {
                        cursor: not-allowed !important;
                        opacity: 0.42 !important;
                        filter: grayscale(0.45) !important;
                    }
                    .ul-planner-icon-selected {
                        opacity: 1 !important;
                        filter: drop-shadow(0 0 5px rgba(56, 189, 248, 0.85)) !important;
                    }
                    .ul-planner-row-selected {
                        background: linear-gradient(90deg, rgba(56, 189, 248, 0.20), rgba(56, 189, 248, 0.06)) !important;
                        box-shadow: inset 3px 0 0 #38bdf8, inset 0 0 0 1px rgba(56, 189, 248, 0.24) !important;
                        border-radius: 4px !important;
                    }
                    .ul-planner-row-dim {
                        opacity: 0.46 !important;
                        filter: grayscale(0.55) !important;
                    }
                    .ul-planner-icon-dim {
                        opacity: 0.35 !important;
                        filter: grayscale(0.7) !important;
                    }
                    #ul-planner-toolbox {
                        position: fixed;
                        top: 118px;
                        right: 14px;
                        width: 196px;
                        box-sizing: border-box;
                        padding: 8px 10px;
                        border: 1px solid rgba(56, 189, 248, 0.45);
                        border-radius: 6px;
                        background: rgba(17, 24, 39, 0.94);
                        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.32), inset 3px 0 0 rgba(56, 189, 248, 0.9);
                        color: #e5e7eb;
                        font-size: 12px;
                        line-height: 1.2;
                        text-shadow: 1px 1px 2px black;
                        z-index: 10020;
                        pointer-events: none;
                    }
                    #ul-planner-toolbox[data-active="0"] {
                        opacity: 0.82;
                    }
                    .ul-planner-toolbox-title {
                        display: flex;
                        align-items: center;
                        justify-content: space-between;
                        gap: 8px;
                        color: #93c5fd;
                        font-size: 11px;
                        font-weight: bold;
                        letter-spacing: 0;
                        margin-bottom: 6px;
                    }
                    .ul-planner-toolbox-reload {
                        pointer-events: auto;
                        width: 20px;
                        height: 20px;
                        border: 1px solid rgba(96, 165, 250, 0.75);
                        border-radius: 4px;
                        background: rgba(96, 165, 250, 0.14);
                        color: #bfdbfe;
                        font-size: 13px;
                        line-height: 1;
                        padding: 0;
                        cursor: pointer;
                    }
                    .ul-planner-toolbox-reload:hover {
                        background: rgba(96, 165, 250, 0.25);
                        color: #e0f2fe;
                    }
                    .ul-planner-toolbox-row {
                        display: flex;
                        align-items: baseline;
                        justify-content: space-between;
                        gap: 8px;
                        padding: 3px 0;
                        border-top: 1px solid rgba(75, 85, 99, 0.45);
                    }
                    .ul-planner-toolbox-label {
                        color: #9ca3af;
                        font-size: 11px;
                        white-space: nowrap;
                    }
                    .ul-planner-toolbox-value {
                        color: #fcd34d;
                        font-weight: bold;
                        text-align: right;
                        white-space: nowrap;
                    }
                    .ul-planner-toolbox-value.ul-planner-toolbox-rating {
                        display: flex;
                        align-items: flex-end;
                        gap: 4px;
                        line-height: 1.1;
                    }
                    .ul-planner-toolbox-rank {
                        color: #fcd34d;
                        font-weight: bold;
                    }
                    .ul-planner-toolbox-score {
                        color: #d1d5db;
                        font-weight: 600;
                    }
                    .ul-planner-toolbox-sp {
                        color: #d1d5db;
                    }
                    .ul-planner-toolbox-next {
                        color: #93c5fd;
                    }
                    .ul-planner-toolbox-picked {
                        margin-top: 6px;
                        padding-top: 5px;
                        border-top: 1px solid rgba(75, 85, 99, 0.55);
                    }
                    .ul-planner-toolbox-pick {
                        display: flex;
                        justify-content: space-between;
                        gap: 8px;
                        padding: 2px 0;
                        color: #d1d5db;
                        font-size: 11px;
                    }
                    .ul-planner-toolbox-pick-name {
                        min-width: 0;
                        overflow: hidden;
                        text-overflow: ellipsis;
                        white-space: nowrap;
                    }
                    .ul-planner-toolbox-pick-cost {
                        color: #93c5fd;
                        white-space: nowrap;
                    }
                `;
                document.head.appendChild(plannerStyle);
            }

            function escapeHtml(value) {
                return String(value == null ? "" : value).replace(/[&<>"']/g, function(ch) {
                    return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch];
                });
            }

            function isPlannerMode() {
                return window.UL_MODE_PREF === 'rating';
            }

            const BASE_RANKS_JS = [
                [300, "G"], [600, "G+"], [900, "F"], [1300, "F+"], [1800, "E"],
                [2300, "E+"], [2900, "D"], [3500, "D+"], [4900, "C"], [6500, "C+"],
                [8200, "B"], [10000, "B+"], [12100, "A"], [14500, "A+"], [15900, "S"],
                [17500, "S+"], [19200, "SS"], [19600, "SS+"]
            ];
            const HIGH_RANKS_JS = [
                [19600, 400, 23900, "UG"],
                [23900, 500, 28800, "UF"],
                [28800, 560, 34400, "UE"],
                [34400, 630, 40700, "UD"],
                [40700, 700, 47600, "UC"],
                [47600, 760, 55200, "UB"],
                [55200, 800, Infinity, "UA"]
            ];

            function getRankStrJs(score) {
                for (const [threshold, rank] of BASE_RANKS_JS) {
                    if (score < threshold) return rank;
                }
                for (const [base, step, bound, prefix] of HIGH_RANKS_JS) {
                    if (score < bound) {
                        let sub = Math.floor((score - base) / step);
                        return sub > 0 ? `${prefix}${sub}` : prefix;
                    }
                }
                return "UA";
            }

            function getNextRankReqJs(score) {
                for (const [threshold] of BASE_RANKS_JS) {
                    if (score < threshold) return threshold - score;
                }
                for (const [base, step, bound] of HIGH_RANKS_JS) {
                    if (score < bound) {
                        return Math.min(bound, base + ((Math.floor((score - base) / step) + 1) * step)) - score;
                    }
                }
                return 0;
            }

            function getRankScoreRangeJs(score) {
                let rankMin = 0;
                for (const [threshold] of BASE_RANKS_JS) {
                    if (score < threshold) return [rankMin, threshold - 1];
                    rankMin = threshold;
                }
                for (const [base, step, bound] of HIGH_RANKS_JS) {
                    if (score < bound) {
                        let sub = Math.floor((score - base) / step);
                        rankMin = base + (sub * step);
                        let rankMax = rankMin + step - 1;
                        if (bound !== Infinity) rankMax = Math.min(rankMax, bound - 1);
                        return [rankMin, rankMax];
                    }
                }
                return [score, score];
            }

            function getSelectedCandidates(excludeGroupId = null) {
                let selected = [];
                let excluded = excludeGroupId == null ? null : String(excludeGroupId);
                for (const [groupId, candidate] of plannerSelectedByGroup.entries()) {
                    if (excluded !== null && String(groupId) === excluded) continue;
                    selected.push(candidate);
                }
                return selected;
            }

            function getPlannedSkillIds(excludeGroupId = null) {
                let planned = new Set();
                for (const candidate of getSelectedCandidates(excludeGroupId)) {
                    for (const step of candidate.chain || []) {
                        planned.add(String(step.id));
                    }
                }
                return planned;
            }

            function getPlanTotals(excludeGroupId = null) {
                let cost = 0;
                let score = 0;
                for (const candidate of getSelectedCandidates(excludeGroupId)) {
                    cost += Number(candidate.sp_cost || 0);
                    score += Number(candidate.score || 0);
                }
                return { cost, score };
            }

            function getPlannedSkillRows() {
                let rows = [];
                let seen = new Set();
                for (const candidate of getSelectedCandidates()) {
                    for (const step of candidate.chain || []) {
                        let stepId = String(step.id);
                        if (seen.has(stepId)) continue;
                        seen.add(stepId);
                        rows.push({
                            name: step.name || stepId,
                            spCost: Number(step.sp_cost || 0)
                        });
                    }
                }
                return rows;
            }

            function candidateHasSkill(candidate, skillId) {
                let target = String(skillId);
                return (candidate.chain || []).some(step => String(step.id) === target);
            }

            function getCandidateEffective(candidate, excludeGroupId = null) {
                let plannedIds = getPlannedSkillIds(excludeGroupId);
                let baselineScore = 0;
                let cost = 0;
                for (const step of candidate.all_chain || candidate.chain || []) {
                    let stepId = String(step.id);
                    let stepScore = Number(step.score || 0);
                    if (ownedSkillIds.has(stepId) || plannedIds.has(stepId)) {
                        baselineScore = Math.max(baselineScore, stepScore);
                    } else {
                        cost += Number(step.sp_cost || 0);
                    }
                }

                let finalScore = Number(candidate.final_score || 0);
                let gain = finalScore - baselineScore;
                return { cost, gain, candidate };
            }

            function canSelectCandidate(candidate) {
                if (!candidate) return false;
                let groupId = String(candidate.group_id);
                let totalsWithoutGroup = getPlanTotals(groupId);
                let effective = getCandidateEffective(candidate, groupId);
                return effective.gain > 0 && effective.cost <= Math.max(0, availableSp - totalsWithoutGroup.cost);
            }

            function computeExpectedMax() {
                let planTotals = getPlanTotals();
                let remainingSp = Math.max(0, availableSp - planTotals.cost);
                let grouped = new Map();

                for (const candidate of plannerCandidates || []) {
                    let effective = getCandidateEffective(candidate);
                    if (effective.cost <= 0 || effective.gain <= 0 || effective.cost > remainingSp) continue;

                    let groupId = String(candidate.group_id);
                    if (!grouped.has(groupId)) grouped.set(groupId, []);
                    grouped.get(groupId).push({
                        candidate,
                        cost: effective.cost,
                        gain: effective.gain
                    });
                }

                let dp = Array(remainingSp + 1).fill(0);
                let history = [];
                for (const items of grouped.values()) {
                    let newDp = dp.slice();
                    let choices = Array(remainingSp + 1).fill(null);
                    for (const item of items) {
                        for (let budget = item.cost; budget <= remainingSp; budget++) {
                            let candidateScore = dp[budget - item.cost] + item.gain;
                            if (candidateScore > newDp[budget]) {
                                newDp[budget] = candidateScore;
                                choices[budget] = { prevBudget: budget - item.cost, candidate: item.candidate };
                            }
                        }
                    }
                    dp = newDp;
                    history.push(choices);
                }

                let choices = [];
                let budget = remainingSp;
                for (let i = history.length - 1; i >= 0; i--) {
                    let choice = history[i][budget];
                    if (!choice) continue;
                    budget = choice.prevBudget;
                    choices.push(choice.candidate);
                }
                choices.reverse();
                choices.sort((a, b) => Number(b.score || 0) - Number(a.score || 0));

                return {
                    planCost: planTotals.cost,
                    planScoreGain: planTotals.score,
                    remainingSp,
                    maxScoreGain: dp[remainingSp] || 0,
                    choices
                };
            }

            function updateRankHeader(expected) {
                let rankDispExists = document.getElementById("ul-rank-display");
                if (!rankDispExists) return;

                if (plannerSelectedByGroup.size === 0) {
                    if (proj_score > uma_score) {
                        let maxTitle = `Expected score: ${proj_score.toLocaleString()}\\n${proj_rank} range: ${projRankMin.toLocaleString()} - ${projRankMax.toLocaleString()}`;
                        rankDispExists.innerHTML = `<span style="font-size:0.5em;">Rating: ${uma_rank} ${uma_score}<span style="font-weight:normal;color:#d1d5db;margin-left:4px;font-size:0.85em;"> +${uma_next}</span></span> <span title="${maxTitle}" style="color:#a8a29e;font-size:0.5em;margin-left:8px;">Max: ${proj_rank}<span style="font-weight:normal;margin-left:4px;font-size:0.85em;"> +${proj_next}</span></span>`;
                    } else {
                        rankDispExists.innerHTML = `<span style="font-size:0.5em;">Rating: ${uma_rank} ${uma_score}<span style="font-weight:normal;color:#d1d5db;margin-left:4px;font-size:0.85em;"> +${uma_next}</span></span>`;
                    }
                    return;
                }

                let plannedScore = uma_score + expected.planScoreGain;
                let expectedScore = plannedScore + expected.maxScoreGain;
                let expectedRank = getRankStrJs(expectedScore);
                let expectedNext = getNextRankReqJs(expectedScore);
                let [expectedMin, expectedMax] = getRankScoreRangeJs(expectedScore);
                let maxTitle = `Expected score: ${expectedScore.toLocaleString()}\\n${expectedRank} range: ${expectedMin.toLocaleString()} - ${expectedMax.toLocaleString()}`;

                rankDispExists.innerHTML = `<span style="font-size:0.5em;">Rating: ${uma_rank} ${uma_score}<span style="font-weight:normal;color:#d1d5db;margin-left:4px;font-size:0.85em;"> +${uma_next}</span></span> <span title="${maxTitle}" style="color:#a8a29e;font-size:0.5em;margin-left:8px;">Max: ${expectedRank}<span style="font-weight:normal;margin-left:4px;font-size:0.85em;"> +${expectedNext}</span></span>`;
            }

            function updatePlannerToolbox(expected) {
                let toolbox = document.getElementById("ul-planner-toolbox");
                if (!isPlannerMode()) {
                    if (toolbox) toolbox.style.display = "none";
                    return;
                }

                if (!toolbox) {
                    toolbox = document.createElement("div");
                    toolbox.id = "ul-planner-toolbox";
                    document.body.appendChild(toolbox);
                }
                toolbox.style.display = "block";

                let skillsTable = document.querySelector("[class^='skills_skill_table_']");
                if (skillsTable) {
                    let tableTop = Math.round(skillsTable.getBoundingClientRect().top);
                    toolbox.style.top = `${Math.max(92, tableTop)}px`;
                }

                let plannedScore = uma_score + expected.planScoreGain;
                let plannedRank = getRankStrJs(plannedScore);
                let plannedNext = getNextRankReqJs(plannedScore);
                let expectedScore = plannedScore + expected.maxScoreGain;
                let expectedRank = getRankStrJs(expectedScore);
                let plannedSkillRows = getPlannedSkillRows();
                let plannedSkillsHtml = plannedSkillRows.length > 0
                    ? plannedSkillRows.map(skill => `
                        <div class="ul-planner-toolbox-pick">
                            <span class="ul-planner-toolbox-pick-name">${escapeHtml(skill.name)}</span>
                            <span class="ul-planner-toolbox-pick-cost">${skill.spCost.toLocaleString()} SP</span>
                        </div>
                    `).join("")
                    : `<div class="ul-planner-toolbox-pick"><span class="ul-planner-toolbox-pick-name">No planned skills</span><span class="ul-planner-toolbox-pick-cost"></span></div>`;
                toolbox.dataset.active = plannerSelectedByGroup.size > 0 ? "1" : "0";
                toolbox.innerHTML = `
                    <div class="ul-planner-toolbox-title">
                        <span>Planner</span>
                        <button type="button" class="ul-planner-toolbox-reload" title="Rerun simulation">\\u21bb</button>
                    </div>
                    <div class="ul-planner-toolbox-row">
                        <span class="ul-planner-toolbox-label">Planned</span>
                        <span class="ul-planner-toolbox-value ul-planner-toolbox-rating">
                            <span class="ul-planner-toolbox-rank">${plannedRank}</span> <span class="ul-planner-toolbox-score">${plannedScore.toLocaleString()}</span>
                        </span>
                    </div>
                    <div class="ul-planner-toolbox-row">
                        <span class="ul-planner-toolbox-label">Next</span>
                        <span class="ul-planner-toolbox-value ul-planner-toolbox-next">
                            ${plannedNext.toLocaleString()}
                        </span>
                    </div>
                    <div class="ul-planner-toolbox-row">
                        <span class="ul-planner-toolbox-label">SP Left</span>
                        <span class="ul-planner-toolbox-value ul-planner-toolbox-sp">${expected.remainingSp.toLocaleString()}</span>
                    </div>
                    <div class="ul-planner-toolbox-row">
                        <span class="ul-planner-toolbox-label">Expected Max</span>
                        <span class="ul-planner-toolbox-value ul-planner-toolbox-rating">
                            <span class="ul-planner-toolbox-rank">${expectedRank}</span> <span class="ul-planner-toolbox-score">${expectedScore.toLocaleString()}</span>
                        </span>
                    </div>
                    <div class="ul-planner-toolbox-picked">
                        ${plannedSkillsHtml}
                    </div>
                `;

                let reloadButton = toolbox.querySelector(".ul-planner-toolbox-reload");
                if (reloadButton) {
                    reloadButton.onclick = (event) => {
                        event.preventDefault();
                        event.stopPropagation();
                        window.rerunSkillSimulation();
                    };
                }
            }

            function togglePlannerSkill(skillId) {
                if (!isPlannerMode()) return;

                let candidate = candidateBySkillId.get(String(skillId));
                if (!candidate) return;

                let groupId = String(candidate.group_id);
                let selected = plannerSelectedByGroup.get(groupId);
                if (selected && candidateHasSkill(selected, skillId)) {
                    plannerSelectedByGroup.delete(groupId);
                    refreshPlannerUi();
                    return;
                }

                if (!canSelectCandidate(candidate)) return;
                plannerSelectedByGroup.set(groupId, candidate);
                refreshPlannerUi();
            }

            function refreshPlannerUi() {
                if (!isPlannerMode()) {
                    plannerSelectedByGroup.clear();
                    let expected = computeExpectedMax();
                    updateRankHeader(expected);
                    updatePlannerToolbox(expected);
                    clearPlannerVisualState(false);
                    return;
                }

                let expected = computeExpectedMax();
                let plannedSkillIds = getPlannedSkillIds();
                currentMaxPickedSkillIds = plannerSelectedByGroup.size > 0
                    ? collectChoiceSkillIds(expected.choices)
                    : new Set(baseMaxPickedSkillIds);

                updateRankHeader(expected);
                updatePlannerToolbox(expected);

                for (const [skillId, refs] of plannerRows.entries()) {
                    let candidate = refs.candidate;
                    let selected = plannedSkillIds.has(skillId);
                    let affordable = candidate ? canSelectCandidate(candidate) : false;
                    let acquired = !!refs.acquired;
                    let dim = acquired || (candidate && !selected && !affordable);
                    let clickable = candidate && (selected || affordable);

                    refs.row.classList.toggle("ul-planner-row-selected", selected);
                    refs.row.classList.toggle("ul-planner-row-dim", dim && !selected);
                    refs.badge.classList.toggle("ul-planner-selected", selected);
                    refs.badge.classList.toggle("ul-planner-unaffordable", dim && !acquired);
                    refs.badge.classList.toggle("ul-planner-clickable", clickable);
                    refs.badge.title = selected ? "Remove from plan" : (acquired ? "Already acquired" : (dim ? "Not enough skill points" : (candidate ? "Add to plan" : "")));
                    refs.badge.onclick = clickable ? (event) => {
                        event.preventDefault();
                        event.stopPropagation();
                        togglePlannerSkill(skillId);
                    } : null;

                    if (refs.icon) {
                        refs.icon.classList.toggle("ul-planner-icon-selected", selected);
                        refs.icon.classList.toggle("ul-planner-icon-dim", dim && !selected);
                        refs.icon.style.cursor = clickable ? "pointer" : "";
                        refs.icon.title = refs.badge.title;
                        refs.icon.onclick = clickable ? (event) => {
                            event.preventDefault();
                            event.stopPropagation();
                            togglePlannerSkill(skillId);
                        } : null;
                    }

                    refs.badge.querySelectorAll(".ul-max-pick-icon").forEach(icon => {
                        icon.style.display = currentMaxPickedSkillIds.has(skillId) ? "inline-block" : "none";
                    });
                }
            }

            function clearPlannerVisualState(enablePlanner = isPlannerMode()) {
                currentMaxPickedSkillIds = new Set(baseMaxPickedSkillIds);
                for (const [skillId, refs] of plannerRows.entries()) {
                    let candidate = refs.candidate;
                    let affordable = candidate ? canSelectCandidate(candidate) : false;
                    let acquired = !!refs.acquired;
                    let clickable = enablePlanner && candidate && affordable;

                    refs.row.classList.remove("ul-planner-row-selected", "ul-planner-row-dim");
                    refs.badge.classList.remove("ul-planner-selected", "ul-planner-unaffordable", "ul-planner-clickable");
                    refs.badge.classList.toggle("ul-planner-clickable", clickable);
                    refs.badge.title = enablePlanner ? (acquired ? "Already acquired" : (candidate ? (affordable ? "Add to plan" : "Not enough skill points") : "")) : "";
                    refs.badge.onclick = clickable ? (event) => {
                        event.preventDefault();
                        event.stopPropagation();
                        togglePlannerSkill(skillId);
                    } : null;

                    if (refs.icon) {
                        refs.icon.classList.remove("ul-planner-icon-selected", "ul-planner-icon-dim");
                        refs.icon.style.cursor = clickable ? "pointer" : "";
                        refs.icon.title = refs.badge.title;
                        refs.icon.onclick = clickable ? (event) => {
                            event.preventDefault();
                            event.stopPropagation();
                            togglePlannerSkill(skillId);
                        } : null;
                    }

                    refs.badge.querySelectorAll(".ul-max-pick-icon").forEach(icon => {
                        icon.style.display = enablePlanner && baseMaxPickedSkillIds.has(skillId) ? "inline-block" : "none";
                    });
                }
            }

            window.resetSkillPlanner = () => {
                plannerSelectedByGroup.clear();
                let expected = computeExpectedMax();
                updateRankHeader(expected);
                updatePlannerToolbox(expected);
                clearPlannerVisualState(isPlannerMode());
            };
    
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
            window.UL_CM_DEF = localStorage.getItem('UL_CM_DEF') || selectedCmDefinition;
            if (!cmOptions.some(option => String(option.id) === String(window.UL_CM_DEF))) {
                window.UL_CM_DEF = selectedCmDefinition;
                localStorage.setItem('UL_CM_DEF', window.UL_CM_DEF);
            }

            function syncCmDefinitionSelect(cmSelect) {
                cmSelect.replaceChildren();
                for (const option of cmOptions) {
                    let cmOption = document.createElement("option");
                    cmOption.value = String(option.id);
                    cmOption.textContent = option.label;
                    cmSelect.appendChild(cmOption);
                }
                cmSelect.value = String(window.UL_CM_DEF);
            }

            window.rerunSkillSimulation = () => {
                if (window.UL_SIM_STATUS === "running") return;
                if (typeof window.resetSkillPlanner === "function") {
                    window.resetSkillPlanner();
                }
                window.UL_SIM_STATUS = "running";
                window.UL_SIM_MESSAGE = "";
                window.updateSimStatus();
                fetch('http://127.0.0.1:3150/rerun-skill-simulation', { method: 'POST' });
            };

            window.ensureSimControls = () => {
                document.querySelectorAll('button[aria-label="Open Umamusume menu"]').forEach(button => {
                    let wrapper = button.parentElement && button.parentElement.parentElement
                        ? button.parentElement.parentElement
                        : button;
                    wrapper.remove();
                });

                let simControlDiv = document.getElementById("ul-sim-controls");
                if (!simControlDiv) {
                    simControlDiv = document.createElement("div");
                    simControlDiv.id = "ul-sim-controls";
                    simControlDiv.style.display = "inline-flex";
                    simControlDiv.style.alignItems = "center";
                    simControlDiv.style.gap = "4px";
                    simControlDiv.style.marginRight = "0";
                    simControlDiv.style.fontSize = "12px";
                    simControlDiv.style.zIndex = "10000";

                    let statusEl = document.createElement("button");
                    statusEl.id = "ul-sim-status";
                    statusEl.type = "button";
                    statusEl.title = "Rerun simulation";
                    statusEl.style.padding = "4px 5px";
                    statusEl.style.border = "1px solid #6b7280";
                    statusEl.style.borderRadius = "4px";
                    statusEl.style.whiteSpace = "nowrap";
                    statusEl.style.lineHeight = "1";
                    statusEl.style.cursor = "pointer";
                    statusEl.onclick = (event) => {
                        event.preventDefault();
                        event.stopPropagation();
                        window.rerunSkillSimulation();
                    };

                    simControlDiv.appendChild(statusEl);
                }

                let staleRerunButton = document.getElementById("ul-sim-rerun");
                if (staleRerunButton) staleRerunButton.remove();

                let settingsButton = document.querySelector("div.styles_header_settings__hx4QQ[aria-expanded='false'], div.styles_header_settings__hx4QQ");
                let header = settingsButton ? (settingsButton.closest("header") || settingsButton.parentElement) : null;
                if (header) {
                    simControlDiv.style.position = "";
                    simControlDiv.style.top = "";
                    simControlDiv.style.right = "";
                    if (simControlDiv.parentElement !== header) {
                        header.insertBefore(simControlDiv, settingsButton);
                    }
                    header.style.gridTemplateColumns = "1fr max-content 75px";
                    header.style.alignItems = "center";
                    simControlDiv.style.gridColumn = "2";
                    simControlDiv.style.gridRow = "1";
                    simControlDiv.style.justifySelf = "end";
                    settingsButton.style.gridColumn = "3";
                    settingsButton.style.gridRow = "1";
                    settingsButton.style.justifySelf = "end";
                }
                return simControlDiv;
            };

            window.updateSimStatus = () => {
                window.ensureSimControls();
                let statusEl = document.getElementById("ul-sim-status");
                if (!statusEl) return;

                let status = window.UL_SIM_STATUS || "idle";
                let text = "Sim: Ready";
                let color = "#9ca3af";
                let border = "#6b7280";
                let background = "rgba(107, 114, 128, 0.16)";

                if (status === "running") {
                    text = "Sim: Running...";
                    color = "#fcd34d";
                    border = "#f59e0b";
                    background = "rgba(245, 158, 11, 0.18)";
                } else if (status === "done") {
                    text = "Sim: Done \\u21bb";
                    color = "#86efac";
                    border = "#22c55e";
                    background = "rgba(34, 197, 94, 0.14)";
                } else if (status === "rating") {
                    text = "Sim: Rating mode \\u21bb";
                    color = "#c084fc";
                    border = "#a855f7";
                    background = "rgba(168, 85, 247, 0.16)";
                } else if (status === "waiting") {
                    text = "Sim: Waiting";
                    color = "#93c5fd";
                    border = "#60a5fa";
                    background = "rgba(96, 165, 250, 0.14)";
                } else if (status === "error") {
                    text = "Sim: Error";
                    color = "#fca5a5";
                    border = "#ef4444";
                    background = "rgba(239, 68, 68, 0.14)";
                }

                statusEl.textContent = text;
                statusEl.style.color = color;
                statusEl.style.borderColor = border;
                statusEl.style.background = background;
                statusEl.disabled = false;
                statusEl.style.cursor = status === "running" ? "default" : "pointer";
                statusEl.style.opacity = status === "running" ? "0.75" : "1";
            };

            window.ensureCmDefinitionSelect = () => {
                let cmSelect = document.getElementById("ul-cm-definition-select");
                if (!cmSelect) {
                    cmSelect = document.createElement("select");
                    cmSelect.id = "ul-cm-definition-select";
                    cmSelect.style.padding = "4px 5px";
                    cmSelect.style.borderRadius = "4px";
                    cmSelect.style.border = "1px solid #60a5fa";
                    cmSelect.style.background = "transparent";
                    cmSelect.style.color = "var(--c-text)";
                    cmSelect.style.cursor = "pointer";
                    cmSelect.style.fontSize = "12px";
                    cmSelect.style.verticalAlign = "middle";
                    cmSelect.style.maxWidth = "76px";

                    cmSelect.onchange = () => {
                        window.UL_CM_DEF = cmSelect.value;
                        localStorage.setItem('UL_CM_DEF', window.UL_CM_DEF);
                        window.UL_SIM_STATUS = "running";
                        window.UL_SIM_MESSAGE = "";
                        window.updateSimStatus();
                        cmSelect.disabled = true;
                        fetch('http://127.0.0.1:3150/skill-window-cm-definition', {
                            method: 'POST',
                            body: window.UL_CM_DEF,
                            headers: { 'Content-Type': 'text/plain' }
                        }).finally(() => {
                            setTimeout(() => { cmSelect.disabled = false; }, 300);
                        });
                    };
                }
                syncCmDefinitionSelect(cmSelect);
                return cmSelect;
            };
    
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

                let cmSelect = window.ensureCmDefinitionSelect();
    
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

                    if (typeof refreshPlannerUi === "function") {
                        refreshPlannerUi();
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

            let simControlDiv = window.ensureSimControls();
            let cmSelect = window.ensureCmDefinitionSelect();
            let simStatus = document.getElementById("ul-sim-status");
            if (simControlDiv && cmSelect) {
                if (simStatus && cmSelect.nextSibling !== simStatus) {
                    simControlDiv.insertBefore(cmSelect, simStatus);
                } else if (!simStatus && cmSelect.parentElement !== simControlDiv) {
                    simControlDiv.appendChild(cmSelect);
                }
            }

            window.updateSimStatus();
            
            updateRankHeader(computeExpectedMax());
    
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

                        row.classList.remove("ul-planner-row-selected", "ul-planner-row-dim");
                        let iconCell = row.children.length > 0 ? row.children[0] : null;
                        if (iconCell) {
                            iconCell.classList.remove("ul-planner-icon-selected", "ul-planner-icon-dim");
                            iconCell.classList.add("ul-planner-skill-icon");
                            iconCell.style.cursor = "";
                            iconCell.title = "";
                            iconCell.onclick = null;
                        }
    
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

                        let maxPickIcon = `<span class="ul-max-pick-icon" data-skill-id="${skill_id}" style="display:none;color:#fcd34d;font-size:0.85em;line-height:1;margin-right:4px;text-shadow:1px 1px 2px black, -1px -1px 2px black;">&#9733;</span>`;
                        let isAcquired = ownedSkillIds.has(String(skill_id));
    
                        if (isAcquired) {
                            badge.style.padding = "2px 8px";
                            badge.style.backgroundColor = "rgba(156, 163, 175, 0.1)"; 
                            badge.style.border = "1px solid #9ca3af";
                            badge.style.color = "#9ca3af";
                            badge.style.borderRadius = "4px";
                            badge.style.fontWeight = "bold";
                            badge.style.display = "flex";
                            badge.style.alignItems = "center";
                            badge.style.justifyContent = "center";
                            badge.innerHTML = `${maxPickIcon}Acquired`;
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
                                            ${maxPickIcon}${rdata.score} Pt
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
                        plannerRows.set(String(skill_id), {
                            row: row,
                            badge: badge,
                            icon: iconCell,
                            candidate: candidateBySkillId.get(String(skill_id)) || null,
                            acquired: isAcquired
                        });
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
            refreshPlannerUi();
            """, self.skills_list, sim_summary, global_hist_min, global_hist_max, global_box_min, global_box_max,
            acquired_skills_list, all_sk_data, base_median_abs, rating_data, uma_score, uma_rank, projected_score,
            projected_rank, projected_choices, uma_next, proj_next, cm_options, selected_cm_definition,
            projected_rank_min, projected_rank_max, available_sp, planner_candidates, sim_status, sim_status_message)

    def run_simulation(self, exe_path, payload, timeout=90, expected_generation=None, skill_data_path=None):
        json_payload = json.dumps(payload)
        process = None

        try:
            command = [exe_path, json_payload]
            environment = os.environ.copy()
            if skill_data_path is not None:
                environment["UMASIM_SKILL_DATA_PATH"] = skill_data_path
            startup_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

            if hasattr(self, "_skill_sim_condition"):
                # Atomically gate process creation against shutdown.  Holding
                # the condition across the short Popen call closes the race
                # where stop() could otherwise miss a just-dequeued child.
                with self._skill_sim_condition:
                    if (
                        self._skill_sim_stop
                        or (
                            expected_generation is not None
                            and expected_generation != self._skill_sim_latest_generation
                        )
                    ):
                        return {}
                    process = subprocess.Popen(
                        command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding='utf-8', env=environment, creationflags=startup_flags,
                    )
                    self._skill_sim_process = process
            else:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding='utf-8', env=environment, creationflags=startup_flags,
                )

            stdout, stderr = process.communicate(timeout=timeout)

            if expected_generation is not None:
                with self._skill_sim_condition:
                    if self._skill_sim_stop or expected_generation != self._skill_sim_latest_generation:
                        return {}  # Expected cancellation, not a simulator failure.
            if stderr:
                diagnostics = Counter(stderr.splitlines())
                summary = "\n".join(
                    f"{line} ({count} occurrences)" if count > 1 else line
                    for line, count in diagnostics.items()
                )
                logger.debug(f"Sim Output: {summary}")
            if process.returncode:
                logger.error(f"Sim crashed with exit code {process.returncode}: {stderr}")
                return {}

            result = json.loads(stdout)
            if "baselineStats" not in result or "candidates" not in result:
                logger.error(f"Simulator returned an invalid result: {result}")
                return {}
            return result

        except subprocess.TimeoutExpired:
            logger.error(f"Simulation timed out after {timeout} seconds")
            if process is not None:
                process.kill()
                process.communicate()
            return {}

        except json.JSONDecodeError as exc:
            logger.error("Failed to parse JSON response!")
            logger.error(f"Sim output was not valid JSON: {exc}")
            return {}

        except OSError as exc:
            logger.error(f"Could not run simulator {exe_path}: {exc}")
            return {}
        finally:
            if hasattr(self, "_skill_sim_condition"):
                with self._skill_sim_condition:
                    if self._skill_sim_process is process:
                        self._skill_sim_process = None

    def determine_event_element(self, event_titles):
        return self.determine_event_element_with_executor(
            self.browser.execute_script, event_titles
        )

    def save_schedule_window_rect(self):
        if self.schedule_browser:
            self.schedule_browser.last_window_rect = self.last_schedule_rect
        self.save_rect(self.last_schedule_rect, "schedule_position")

    def sync_schedule_window(self, current_turn=None):
        if not self.helper_table.show_schedule_optimizer_button:
            return

        if self.schedule_browser and self.schedule_browser.alive():
            try:
                if current_turn is None and self.last_data and 'chara_info' in self.last_data:
                    current_turn = self.last_data['chara_info'].get('turn', 1) - 1
                
                logger.debug(f"Syncing schedule window. Current turn index: {current_turn}")

                race_bonus = getattr(self, 'current_race_bonus', None)
                aptitudes = None
                
                if self.last_data and 'chara_info' in self.last_data:
                    cinfo = self.last_data['chara_info']
                    if 'proper_distance_short' in cinfo:
                        apt_map = {1: 'G', 2: 'F', 3: 'E', 4: 'D', 5: 'C', 6: 'B', 7: 'A', 8: 'S'}
                        aptitudes = {
                            "Sprint": apt_map.get(cinfo.get('proper_distance_short', 1), 'G'),
                            "Mile": apt_map.get(cinfo.get('proper_distance_mile', 1), 'G'),
                            "Medium": apt_map.get(cinfo.get('proper_distance_middle', 1), 'G'),
                            "Long": apt_map.get(cinfo.get('proper_distance_long', 1), 'G'),
                            "Turf": apt_map.get(cinfo.get('proper_ground_turf', 1), 'G'),
                            "Dirt": apt_map.get(cinfo.get('proper_ground_dirt', 1), 'G')
                        }
                        logger.debug(f"Trackblazer API payload: RB {race_bonus}% | Aptitudes {aptitudes}")
                
                json_data = json.dumps(self.completed_races)
                json_extra = json.dumps(self.extra_race_info)
                json_apt = json.dumps(aptitudes) if aptitudes else 'null'
                rb_val = race_bonus if race_bonus is not None else 'null'
                
                ct = current_turn if current_turn is not None else 'null'
                self.schedule_browser.execute_script(f"""window.setAutoSchedulerSettings({rb_val}, {json_apt}); window.syncCompletedRaces({json_data}, {ct}, {json_extra});""")
            except Exception as e:
                logger.error(f"Failed to sync trackblazer schedule: {e}")

    def update_schedule_window(self):
        if self.should_stop or not self.helper_table.show_schedule_optimizer_button:
            return

        if not self.schedule_browser:
            url = f"file:///{util.get_asset('_assets/trackblazer_scheduler/index.html').replace(chr(92), '/')}"
            rect = self.threader.settings['schedule_position']
            # if not rect:
            #     rect = [50, 50, 1000, 800]
            self.schedule_browser = horsium.BrowserWindow(
                url,
                self.threader,
                rect=rect,
                run_at_launch=setup_schedule_window
            )
        else:
            self.schedule_browser.ensure_tab_open()

        if (
            self.active_helper_mode == self.HELPER_UI_LEGACY
            and self.browser
            and self.browser.alive()
        ):
            self.browser.execute_script("""window.schedule_window_opened();""")
            
        self.sync_schedule_window()

    def set_browser_topmost(self, is_topmost):
        self.browser_topmost = is_topmost
        logger.debug(f"Setting browser topmost to {is_topmost}")
        if self.browser:
            self.browser.set_topmost(is_topmost)

    def set_browser_pair(self, is_paired):
        self.browser_pair = is_paired
        logger.debug(f"Setting browser pairing to {is_paired}")
        if self.browser:
            self.browser.set_pair(is_paired)

    def run_with_catch(self):
        try:
            self.run()
        except Exception:
            util.show_error_box("Critical Error", "Uma Launcher has encountered a critical error and will now close.")
            self.threader.stop()

    def process_carrotblender_datagram(self, datagram, chunks_left=0):
        """Process one framed CarrotBlender UDP datagram and return multipart chunks remaining."""
        if not datagram:
            logger.error("Socket read no data")
            return chunks_left

        msg_type = datagram[0]
        if msg_type == 4:
            if len(datagram) < 2:
                logger.error(f"Invalid multipart header: {datagram.hex()}")
                return chunks_left
            chunks_left = datagram[1]
            self._multipart_chunks = bytearray()
            self.encrypted_data = self._multipart_chunks
            logger.debug(f"Got multipart response header with {chunks_left} chunks")
            return chunks_left

        if len(datagram) < 3:
            logger.error(f"Invalid message (invalid length): {datagram.hex()}")
            return chunks_left

        msg_len = int.from_bytes(datagram[1:3], "big")
        if len(datagram) < msg_len + 3:
            logger.error(f"Invalid message (incomplete): {datagram.hex()}")
            return chunks_left
        message = datagram[3:msg_len + 3]

        if msg_type == 0:
            self.encrypted_data = message
            self._multipart_chunks = None
        elif msg_type == 1:
            self.key = message
        elif msg_type == 2:
            self.iv = message
            if self.key is None or self.encrypted_data in (None, b""):
                logger.warning("Ignoring message: data, key and/or IV is not set")
                return chunks_left
            try:
                self.handle_response(unpack(self.encrypted_data, self.key, self.iv), is_json=True)
            except Exception as exc:
                logger.error(f"Error decoding and handling message: {exc}\n{traceback.format_exc()}")
            finally:
                self.key = None
                self.iv = None
                self.encrypted_data = None
                self._multipart_chunks = None
        elif msg_type == 3:
            self.handle_request(message, is_json=True)
        elif msg_type == 5:
            if chunks_left < 1:
                logger.error("Got unexpected multipart message chunk")
                return chunks_left
            chunks_left -= 1
            if self._multipart_chunks is None:
                self._multipart_chunks = bytearray(self.encrypted_data or b"")
                self.encrypted_data = self._multipart_chunks
            self._multipart_chunks.extend(message)
        else:
            logger.error(f"Invalid message type {msg_type}")
        return chunks_left

    def _prepare_transient_cleanup(self, data):
        """Close stale event UI before a new event can open its replacement."""
        if self.get_helper_ui_mode() == self.HELPER_UI_MODERN:
            # Modern owns drawer lifetime explicitly through event generations.
            self._close_transients_requested = False
            return
        if "choice_reward_array" in data:
            # Prospective reward metadata should not close the open event.
            self._close_transients_requested = False
            return

        if data.get("unchecked_event_array"):
            # This must happen before handle_response clicks the new event open.
            # Deferring it to maintenance would close the new popup instead.
            self._close_transients_requested = False
            if self.browser and self.browser.alive():
                try:
                    self.browser.execute_script(
                        "if (window.UL_CLOSE_TRANSIENTS) window.UL_CLOSE_TRANSIENTS(true);"
                    )
                except Exception:
                    # A page transition can invalidate the old popup. Do not
                    # queue a retry that could close the replacement event.
                    logger.debug(
                        f"Stale event cleanup failed before opening replacement:\n"
                        f"{traceback.format_exc()}"
                    )
            return

        # Generic packet cleanup remains coalesced with browser maintenance.
        self._close_transients_requested = True

    def _poll_browser_health(self):
        """Retire Selenium sessions whose tracked browser tab disappeared."""
        windows = {
            "helper": self.browser,
            "skill": self.skill_browser,
            "events": getattr(self, "event_browser", None),
            "schedule": self.schedule_browser,
        }
        alive = {
            name: bool(window and window.alive())
            for name, window in windows.items()
        }

        browser = windows["helper"]
        if browser and alive["helper"] and self.browser is browser:
            close_generation = getattr(self, "_event_drawer_close_pending", None)
            if close_generation is not None:
                self.close_active_event_drawer(close_generation)
            if getattr(self, "_close_transients_requested", False):
                try:
                    browser.execute_script(
                        "if (window.UL_CLOSE_TRANSIENTS) window.UL_CLOSE_TRANSIENTS(true);"
                    )
                except Exception:
                    # Page transitions can briefly invalidate the script
                    # context. Keep the flag set and retry next health poll.
                    logger.debug(
                        f"Transient popup cleanup failed; will retry:\n{traceback.format_exc()}"
                    )
                else:
                    self._close_transients_requested = False
        elif (
            browser
            and not alive["helper"]
            and self.browser is browser
            and not browser.should_preserve_recovery_state()
        ):
            rect_callback = self._prepare_async_rect_capture("helper", browser)
            self.browser = None
            self.active_helper_mode = None
            self.clear_event_drawer_state()
            self._close_transients_requested = False
            self.save_last_browser_rect()
            browser.quit(rect_callback=rect_callback)

        skill_browser = windows["skill"]
        if skill_browser and not alive["skill"] and self.skill_browser is skill_browser:
            self.skill_browser = None
            rect_callback = self._prepare_async_rect_capture("skill", skill_browser)
            self.save_rect(self.last_skills_rect, "skills_position")
            skill_browser.quit(rect_callback=rect_callback)

        event_browser = windows["events"]
        if (
            event_browser
            and not alive["events"]
            and getattr(self, "event_browser", None) is event_browser
        ):
            self.event_browser = None
            self.open_event_window = False
            rect_callback = self._prepare_async_rect_capture(
                "events", event_browser
            )
            self.save_rect(
                getattr(self, "last_events_rect", None),
                "events_position",
            )
            event_browser.quit(rect_callback=rect_callback)

        schedule_browser = windows["schedule"]
        if schedule_browser and not alive["schedule"] and self.schedule_browser is schedule_browser:
            self.schedule_browser = None
            rect_callback = self._prepare_async_rect_capture("schedule", schedule_browser)
            self.save_rect(self.last_schedule_rect, "schedule_position")
            schedule_browser.quit(rect_callback=rect_callback)

    def _maintain_windows(self):
        """Run lightweight browser/window maintenance once per cadence tick."""
        if self.browser:
            self.browser.enforce_z_order()
            if self.reset_browser:
                reset_position = self.get_browser_reset_position()
                if reset_position:
                    self.browser.set_window_rect(reset_position)
        elif self.last_browser_rect:
            self.save_last_browser_rect()

        self.reset_browser = False

        if getattr(self, "open_event_window", False):
            self.open_event_window = False
            self.update_event_window()

        simulation_completion = self._take_skill_simulation_completion()
        if self.open_skill_window:
            self.open_skill_window = False
            self.previous_skills_list = list(self.skills_list)
            self.update_skill_window()
        elif simulation_completion is not None and self.skill_browser:
            self.update_skill_window(simulation_completion=simulation_completion)
        elif self.skill_browser and self._skill_window_last_state_key != self._skill_window_state_key():
            self.previous_skills_list = list(self.skills_list)
            self.update_skill_window()

        if self.open_schedule_window:
            self.open_schedule_window = False
            self.update_schedule_window()

        if util.is_debug:
            debug_path = util.get_relative("debug.in")
            if os.path.exists(debug_path):
                try:
                    with open(debug_path, "r", encoding="utf-8") as debug_file:
                        data = json.load(debug_file)
                    self.handle_response(data, is_json=True)
                    os.remove(debug_path)
                except Exception:
                    logger.error(traceback.format_exc())

    def run(self):
        try:
            port = self.threader.settings["carrotblender_port"]
            ip_address = self.threader.settings["carrotblender_host"]
            warned = False
            while not self.should_stop:
                try:
                    self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF,
                                         self.threader.settings["carrotblender_max_buffer_size"])
                    self.sock.bind((ip_address, port))
                    logger.info(
                        f"Listening for CarrotBlender on {ip_address}:{port}; "
                        f"receive buffer {self.sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)}"
                    )
                    break
                except OSError as exc:
                    if self.sock is not None:
                        self.sock.close()
                        self.sock = None
                    logger.error(f"Could not bind CarrotBlender to {ip_address}:{port}: {exc}")
                    if not warned:
                        warned = True
                        util.show_warning_box(
                            "Uma Launcher: CarrotBlender unavailable",
                            f"Could not bind to {ip_address}:{port}. Uma Launcher will keep retrying."
                        )
                    for _ in range(50):
                        if self.should_stop:
                            return
                        time.sleep(0.1)

            chunks_left = 0
            maintenance_interval = self.WINDOW_MAINTENANCE_INTERVAL
            health_interval = self.SELENIUM_HEALTH_INTERVAL
            next_maintenance = time.monotonic()
            next_health_check = next_maintenance
            while not self.should_stop:
                now = time.monotonic()
                if now >= next_health_check:
                    self._poll_browser_health()
                    next_health_check += health_interval
                    if next_health_check <= now:
                        next_health_check = now + health_interval

                if now >= next_maintenance:
                    self._maintain_windows()
                    next_maintenance += maintenance_interval
                    if next_maintenance <= now:
                        next_maintenance = now + maintenance_interval

                try:
                    select_timeout = max(
                        0.0,
                        min(next_maintenance, next_health_check) - time.monotonic(),
                    )
                    ready = select.select([self.sock], [], [], select_timeout)
                    if not ready[0]:
                        continue
                    datagram = self.sock.recv(self.MAX_BUFFER_SIZE)
                    chunks_left = self.process_carrotblender_datagram(datagram, chunks_left)
                except OSError as exc:
                    if not self.should_stop:
                        logger.error(f"CarrotBlender socket interrupted: {exc}\n{traceback.format_exc()}")

        except NoSuchWindowException:
            pass

        self.end_training()
        self._stop_skill_simulation_worker()

        return

    def stop(self):
        self.should_stop = True
        self._stop_skill_simulation_worker()
        self.runtime_extensions.close()
        if self.sock is not None:
            logger.info("Stopping CarrotBlender socket")
            try:
                self.sock.close()
            except OSError:
                logger.debug("CarrotBlender socket was already closed")


def setup_gametora_event_frame(browser, gametora_url, timeout=8):
    def configure_driver(driver):
        document_deadline = time.monotonic() + timeout
        document_ready = False
        while time.monotonic() < document_deadline:
            try:
                document_ready = driver.execute_script(
                    """
                    const expected = new URL(arguments[0]);
                    const navigationEntry = performance.getEntriesByType("navigation")[0];
                    let requested = null;
                    try {
                        requested = new URL(navigationEntry?.name || "");
                    } catch (_error) {}
                    return Boolean(requested)
                        && requested.origin === expected.origin
                        && requested.pathname.replace(/\\/$/, '') === expected.pathname.replace(/\\/$/, '')
                        && requested.search === expected.search
                        && location.origin === expected.origin
                        && location.pathname.replace(/\\/$/, '') === expected.pathname.replace(/\\/$/, '')
                        && (document.readyState === 'interactive' || document.readyState === 'complete')
                        && Boolean(document.getElementById('__next'));
                    """,
                    gametora_url,
                )
                if document_ready:
                    break
            except Exception:
                pass
            time.sleep(0.1)
        if not document_ready:
            raise TimeoutError("GameTora event frame did not finish loading")

        driver.execute_script(
            "document.getElementById('ul-event-focus-state')?.clear?.();"
        )
        gametora_dark_mode(driver)

        viewer_deadline = time.monotonic() + timeout
        configured = False
        while time.monotonic() < viewer_deadline:
            configured = driver.execute_script(
                """
                const allAtOnce = document.getElementById("allAtOnceCheckbox");
                const expandEvents = document.getElementById("expandEventsCheckbox");
                if (!allAtOnce || !expandEvents) return false;
                if (!allAtOnce.checked) allAtOnce.click();
                if (expandEvents.checked) expandEvents.click();
                const viewer = document.getElementById("viewer-box-main");
                const eventButtons = viewer?.querySelectorAll(
                    "button[aria-expanded], button[class^='sc-']"
                ).length || 0;
                return allAtOnce.checked
                    && !expandEvents.checked
                    && eventButtons > 0;
                """
            )
            if configured:
                break
            time.sleep(0.1)
        if not configured:
            raise TimeoutError(
                "GameTora event viewer options did not become ready"
            )

        gametora_remove_cookies_banner(driver)
        gametora_close_ad_banner(driver, is_training_helper=True)

    browser.gametora_frame_ready = False
    browser.run_in_frame(
        CarrotJuicer.GAMETORA_EVENT_FRAME_ID,
        configure_driver,
        timeout=max(1, timeout),
    )
    browser.gametora_frame_ready = True


def setup_modern_helper_page(browser, gametora_url):
    browser.modern_dashboard_fit_signature = None
    browser.execute_script(
        """
        return window.UL_CONFIGURE({
            gametoraUrl: arguments[0],
            topmost: arguments[1],
            paired: arguments[2],
            theme: arguments[3]
        });
        """,
        gametora_url,
        browser.threader.settings["browser_topmost"],
        browser.threader.settings["browser_pair"],
        helper_theme.build_theme(browser.threader.settings),
    )
    carrotjuicer = getattr(browser.threader, "carrotjuicer", None)
    extensions = getattr(carrotjuicer, "runtime_extensions", None)
    if extensions:
        extensions.configure_modern_page(browser)
    browser.gametora_frame_ready = False


def setup_event_window(browser: horsium.BrowserWindow, timeout=8):
    """Configure the isolated GameTora catalog used by the Modern Events window."""
    try:
        browser.execute_script(
            """
            document.title = "Events | UmaLauncher";

            if (window.UL_EVENT_RECT_TIMER) {
                clearTimeout(window.UL_EVENT_RECT_TIMER);
            }
            window.UL_SEND_EVENT_RECT = () => {
                const rect = {
                    x: window.screenX,
                    y: window.screenY,
                    width: window.outerWidth,
                    height: window.outerHeight
                };
                const serialized = JSON.stringify(rect);
                if (serialized !== window.UL_LAST_EVENT_SCREEN_RECT) {
                    window.UL_LAST_EVENT_SCREEN_RECT = serialized;
                    fetch("http://127.0.0.1:3150/events-window-rect", {
                        method: "POST",
                        body: serialized,
                        headers: {"Content-Type": "text/plain"}
                    }).catch(() => {});
                }
                window.UL_EVENT_RECT_TIMER = setTimeout(
                    window.UL_SEND_EVENT_RECT,
                    2000
                );
            };
            window.UL_EVENT_RECT_TIMER = setTimeout(
                window.UL_SEND_EVENT_RECT,
                2000
            );
            """
        )

        gametora_dark_mode(browser)

        deadline = time.monotonic() + timeout
        options_ready = False
        while time.monotonic() < deadline:
            state = browser.execute_script(
                """
                document.title = "Events | UmaLauncher";
                const allAtOnce = document.getElementById("allAtOnceCheckbox");
                const expandEvents = document.getElementById("expandEventsCheckbox");
                const onlyChoices = document.getElementById("onlyChoicesCheckbox");
                if (!allAtOnce || !expandEvents || !onlyChoices) {
                    return {ready: false};
                }
                if (!allAtOnce.checked) allAtOnce.click();
                if (!expandEvents.checked) expandEvents.click();
                if (onlyChoices.checked) onlyChoices.click();
                return {
                    ready: allAtOnce.checked
                        && expandEvents.checked
                        && !onlyChoices.checked,
                    sourceCount: document.querySelectorAll(
                        "#viewer-box-main div[id^='event-viewer-']"
                    ).length
                };
                """
            )
            options_ready = bool(
                isinstance(state, dict) and state.get("ready")
            )
            if options_ready and state.get("sourceCount", 0) > 0:
                break
            time.sleep(0.1)
        if not options_ready:
            logger.warning(
                "The Events window could not confirm GameTora's event viewer options."
            )

        gametora_remove_cookies_banner(browser)
        gametora_close_ad_banner(browser, is_training_helper=True)

        browser.execute_script(
            """
            (() => {
                const normalize = value => String(value || "")
                    .replace(/\\s+/g, " ")
                    .trim()
                    .toLocaleLowerCase();
                const previousInput = document.getElementById("ul-event-search-input");
                window.UL_EVENT_SEARCH_QUERY = String(
                    window.UL_EVENT_SEARCH_QUERY
                    ?? previousInput?.value
                    ?? ""
                );
                window.UL_EVENT_SEARCH_OBSERVER?.disconnect?.();

                let style = document.getElementById("ul-event-search-style");
                if (!style) {
                    style = document.createElement("style");
                    style.id = "ul-event-search-style";
                    style.textContent = `
                        #ul-event-search-toolbar {
                            position: sticky;
                            top: 0;
                            z-index: 110;
                            display: grid;
                            grid-template-columns: minmax(180px, 1fr) auto;
                            gap: 6px 12px;
                            align-items: center;
                            margin: 0 10px 10px;
                            padding: 10px;
                            color: var(--c-text-main);
                            background: color-mix(
                                in srgb,
                                var(--c-bg-main) 94%,
                                transparent
                            );
                            border: 1px solid var(--c-outline-neutral);
                            border-radius: 6px;
                            box-shadow: var(--c-shadow-low);
                            backdrop-filter: blur(5px);
                        }
                        #ul-event-search-label {
                            grid-column: 1 / -1;
                            font-size: 0.875rem;
                            font-weight: 700;
                        }
                        #ul-event-search-input {
                            min-width: 0;
                            width: 100%;
                            box-sizing: border-box;
                            padding: 8px 10px;
                            border: 1px solid var(--c-outline-neutral);
                            border-radius: 5px;
                            color: var(--c-text-main);
                            background: var(--c-bg-alt);
                            font: inherit;
                        }
                        #ul-event-search-input:focus {
                            outline: 2px solid var(--c-topnav);
                            outline-offset: 1px;
                        }
                        #ul-event-search-count {
                            min-width: 76px;
                            text-align: right;
                            white-space: nowrap;
                            font-size: 0.875rem;
                        }
                        #ul-event-search-notice {
                            grid-column: 1 / -1;
                            color: var(--c-text-subtle, #d6a85f);
                            font-size: 0.8rem;
                            line-height: 1.35;
                        }
                        #ul-event-search-notice[hidden] {
                            display: none !important;
                        }
                        .ul-event-search-hidden {
                            display: none !important;
                        }
                        @media (max-width: 560px) {
                            #ul-event-search-toolbar {
                                grid-template-columns: 1fr;
                            }
                            #ul-event-search-count {
                                text-align: left;
                            }
                        }
                    `;
                    document.head.appendChild(style);
                }

                const collectText = node => {
                    if (!node) return "";
                    const values = [node.textContent || ""];
                    node.querySelectorAll?.(
                        "[aria-label], [title], img[alt]"
                    ).forEach(element => {
                        values.push(
                            element.getAttribute("aria-label") || "",
                            element.getAttribute("title") || "",
                            element.getAttribute("alt") || ""
                        );
                    });
                    return normalize(values.join(" "));
                };

                const setText = (element, text) => {
                    if (element && element.textContent !== text) {
                        element.textContent = text;
                    }
                };

                const ensureOptions = () => {
                    const allAtOnce = document.getElementById(
                        "allAtOnceCheckbox"
                    );
                    const expandEvents = document.getElementById(
                        "expandEventsCheckbox"
                    );
                    const onlyChoices = document.getElementById(
                        "onlyChoicesCheckbox"
                    );
                    if (allAtOnce && !allAtOnce.checked) allAtOnce.click();
                    if (expandEvents && !expandEvents.checked) {
                        expandEvents.click();
                    }
                    if (onlyChoices?.checked) onlyChoices.click();
                };

                const ensureToolbar = () => {
                    const viewer = document.getElementById("viewer-box-main");
                    if (!viewer) return null;

                    let toolbar = document.getElementById(
                        "ul-event-search-toolbar"
                    );
                    if (!toolbar) {
                        toolbar = document.createElement("section");
                        toolbar.id = "ul-event-search-toolbar";
                        toolbar.setAttribute("aria-label", "Event search");

                        const label = document.createElement("label");
                        label.id = "ul-event-search-label";
                        label.htmlFor = "ul-event-search-input";
                        label.textContent = "Search all events";

                        const input = document.createElement("input");
                        input.id = "ul-event-search-input";
                        input.type = "search";
                        input.placeholder = "Title, outcome, reward, trainee, support, or scenario";
                        input.autocomplete = "off";
                        input.value = window.UL_EVENT_SEARCH_QUERY;
                        input.addEventListener("input", () => {
                            window.UL_EVENT_SEARCH_QUERY = input.value;
                            window.UL_APPLY_EVENT_SEARCH?.();
                        });

                        const count = document.createElement("output");
                        count.id = "ul-event-search-count";
                        count.setAttribute("aria-live", "polite");

                        const notice = document.createElement("div");
                        notice.id = "ul-event-search-notice";
                        notice.setAttribute("role", "status");
                        notice.hidden = true;

                        toolbar.append(label, input, count, notice);
                    }
                    if (toolbar.nextElementSibling !== viewer) {
                        viewer.before(toolbar);
                    }
                    const input = toolbar.querySelector(
                        "#ul-event-search-input"
                    );
                    if (
                        input
                        && input !== document.activeElement
                        && input.value !== window.UL_EVENT_SEARCH_QUERY
                    ) {
                        input.value = window.UL_EVENT_SEARCH_QUERY;
                    }
                    return {viewer, toolbar};
                };

                const stableEventMarkers = source => Array.from(
                    source.querySelectorAll(
                        "[data-event-id], [data-training-event-id], "
                        + "[data-event-name]"
                    )
                );

                const eventEntryForMarker = (marker, source) => {
                    let candidate = marker;
                    while (
                        candidate
                        && candidate.parentElement
                        && candidate.parentElement.parentElement !== source
                    ) {
                        candidate = candidate.parentElement;
                    }
                    return candidate?.parentElement?.parentElement === source
                        ? candidate
                        : null;
                };

                const validEventEntry = entry => {
                    const children = Array.from(entry?.children || []);
                    if (children.length < 2) return false;
                    return Boolean(
                        collectText(children[0])
                        && normalize(children.slice(1).map(
                            child => collectText(child)
                        ).join(" "))
                    );
                };

                const eventEntries = source => {
                    const stableEntries = stableEventMarkers(source)
                        .map(marker => eventEntryForMarker(marker, source))
                        .filter(Boolean);
                    const structuralEntries = [];
                    // GameTora's expanded layout keeps the source header as
                    // the first child. Each following category list is a
                    // direct child, and its direct children are event cards
                    // with a title followed by one or more outcome blocks.
                    Array.from(source.children || []).slice(1).forEach(group => {
                        Array.from(group.children || []).forEach(entry => {
                            if (validEventEntry(entry)) {
                                structuralEntries.push(entry);
                            }
                        });
                    });
                    return Array.from(new Set([
                        ...stableEntries,
                        ...structuralEntries
                    ])).filter(validEventEntry);
                };

                const categoryContext = (entry, source) => {
                    const group = entry?.parentElement;
                    if (!group || group.parentElement !== source) {
                        return {group: null, label: null, text: ""};
                    }
                    const label = group.previousElementSibling;
                    if (!label || label.parentElement !== source) {
                        return {group, label: null, text: ""};
                    }
                    return {group, label, text: collectText(label)};
                };

                window.UL_APPLY_EVENT_SEARCH = () => {
                    document.title = "Events | UmaLauncher";
                    ensureOptions();
                    const mounted = ensureToolbar();
                    if (!mounted) return false;

                    const {viewer, toolbar} = mounted;
                    const count = toolbar.querySelector(
                        "#ul-event-search-count"
                    );
                    const notice = toolbar.querySelector(
                        "#ul-event-search-notice"
                    );
                    const query = normalize(window.UL_EVENT_SEARCH_QUERY);
                    const tokens = query ? query.split(" ").filter(Boolean) : [];
                    const matches = text => tokens.every(
                        token => text.includes(token)
                    );
                    const categoryKeywords = new Set(["event", "events"]);
                    const categoryQueryTokens = tokens.filter(
                        token => !categoryKeywords.has(token)
                    );
                    const hasCategoryKeyword = (
                        categoryQueryTokens.length !== tokens.length
                    );
                    const sources = Array.from(viewer.querySelectorAll(
                        "div[id^='event-viewer-']"
                    ));

                    let totalEvents = 0;
                    let visibleEvents = 0;
                    const indexedSources = sources.map(source => {
                        const entries = eventEntries(source);
                        totalEvents += entries.length;
                        return {source, entries};
                    });

                    if (!sources.length) {
                        notice.hidden = true;
                        setText(count, "Loading events…");
                        return true;
                    }

                    if (!totalEvents) {
                        let visibleSources = 0;
                        indexedSources.forEach(({source}) => {
                            const visible = !tokens.length
                                || matches(collectText(source));
                            source.classList.toggle(
                                "ul-event-search-hidden",
                                !visible
                            );
                            if (visible) visibleSources += 1;
                        });
                        notice.hidden = false;
                        setText(
                            notice,
                            "Limited filtering: GameTora's event layout changed, "
                            + "so results are filtered by source section."
                        );
                        setText(
                            count,
                            tokens.length
                                ? `${visibleSources} result${visibleSources === 1 ? "" : "s"}`
                                : `${visibleSources} source${visibleSources === 1 ? "" : "s"}`
                        );
                        return true;
                    }

                    notice.hidden = true;
                    indexedSources.forEach(({source, entries}) => {
                        const sourceText = collectText(
                            source.firstElementChild
                        );
                        let sourceMatches = 0;
                        const categoryVisibility = new Map();
                        entries.forEach(entry => {
                            const category = categoryContext(entry, source);
                            const generalText = (
                                `${sourceText} ${collectText(entry)}`
                            );
                            const generalMatch = matches(generalText);
                            const mixedMatch = !hasCategoryKeyword && tokens.every(
                                token => generalText.includes(token)
                                    || category.text.includes(token)
                            );
                            const categoryIntentMatch = hasCategoryKeyword
                                && categoryQueryTokens.length > 0
                                && categoryQueryTokens.some(
                                    token => category.text.includes(token)
                                )
                                && categoryQueryTokens.every(
                                    token => category.text.includes(token)
                                        || generalText.includes(token)
                                );
                            const visible = !tokens.length
                                || generalMatch
                                || mixedMatch
                                || categoryIntentMatch;
                            entry.classList.toggle(
                                "ul-event-search-hidden",
                                !visible
                            );
                            if (category.group) {
                                const current = categoryVisibility.get(
                                    category.group
                                );
                                categoryVisibility.set(category.group, {
                                    label: category.label,
                                    visible: Boolean(current?.visible || visible)
                                });
                            }
                            if (visible) {
                                sourceMatches += 1;
                                visibleEvents += 1;
                            }
                        });
                        categoryVisibility.forEach((category, group) => {
                            group.classList.toggle(
                                "ul-event-search-hidden",
                                !category.visible
                            );
                            category.label?.classList.toggle(
                                "ul-event-search-hidden",
                                !category.visible
                            );
                        });
                        source.classList.toggle(
                            "ul-event-search-hidden",
                            sourceMatches === 0
                        );
                    });
                    setText(
                        count,
                        tokens.length
                            ? `${visibleEvents} result${visibleEvents === 1 ? "" : "s"}`
                            : `${totalEvents} event${totalEvents === 1 ? "" : "s"}`
                    );
                    return true;
                };

                let applyQueued = false;
                const queueApply = () => {
                    if (applyQueued) return;
                    applyQueued = true;
                    requestAnimationFrame(() => {
                        applyQueued = false;
                        window.UL_APPLY_EVENT_SEARCH?.();
                    });
                };
                if (window.UL_EVENT_SEARCH_KEY_HANDLER) {
                    document.removeEventListener(
                        "keydown",
                        window.UL_EVENT_SEARCH_KEY_HANDLER,
                        true
                    );
                }
                window.UL_EVENT_SEARCH_KEY_HANDLER = event => {
                    const target = event.target;
                    const isEditing = target instanceof HTMLElement && (
                        target.isContentEditable
                        || target.tagName === "INPUT"
                        || target.tagName === "TEXTAREA"
                        || target.tagName === "SELECT"
                    );
                    if (
                        event.defaultPrevented
                        || event.isComposing
                        || event.ctrlKey
                        || event.metaKey
                        || event.altKey
                        || isEditing
                        || event.key.length !== 1
                        || event.key === " "
                    ) {
                        return;
                    }

                    const mounted = ensureToolbar();
                    const input = mounted?.toolbar.querySelector(
                        "#ul-event-search-input"
                    );
                    if (!input || input.disabled) return;

                    event.preventDefault();
                    input.focus({preventScroll: true});
                    const start = input.selectionStart ?? input.value.length;
                    const end = input.selectionEnd ?? start;
                    input.setRangeText(event.key, start, end, "end");
                    input.dispatchEvent(new Event("input", {bubbles: true}));
                };
                document.addEventListener(
                    "keydown",
                    window.UL_EVENT_SEARCH_KEY_HANDLER,
                    true
                );
                window.UL_EVENT_SEARCH_OBSERVER = new MutationObserver(
                    queueApply
                );
                const viewer = document.getElementById("viewer-box-main");
                window.UL_EVENT_SEARCH_OBSERVER.observe(
                    viewer?.parentElement
                        || document.getElementById("__next")
                        || document.body,
                    {childList: true, subtree: true, characterData: true}
                );
                window.UL_QUEUE_EVENT_SEARCH = queueApply;
                window.UL_APPLY_EVENT_SEARCH();
                setTimeout(queueApply, 100);
                setTimeout(queueApply, 500);
            })();
            """
        )
    except Exception:
        # The Events window is auxiliary. A GameTora layout/network problem
        # must not interrupt packet handling or the automatic event drawer.
        logger.error(
            "Could not finish configuring the Events window:\n"
            f"{traceback.format_exc()}"
        )


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

    var ul_schedule = document.createElement("div");
    ul_schedule.id = "ul-schedule";
    ul_schedule.classList.add("ul-overlay-button");
    ul_schedule.style = "position: fixed; right: 108px; top: 0; width: 4.5rem; height: 1.6rem; background-color: var(--c-bg-main); text-align: center; z-index: 101; line-height: 1.5rem; border-left: 2px solid var(--c-topnav); border-bottom: 2px solid var(--c-topnav); border-right: 2px solid var(--c-topnav); border-bottom-left-radius: 0.5rem; border-bottom-right-radius: 0.5rem; cursor: pointer; transition: top 0.5s ease 0s;";
    ul_schedule.textContent = "Sched";
    window.UL_OVERLAY.appendChild(ul_schedule);
    
    var ul_topmost_div = document.createElement("div");
    ul_topmost_div.classList.add("ul-overlay-button");
    ul_topmost_div.style = "position: fixed; right: 182px; top: 0; width: 9rem; height: 1.6rem; background-color: var(--c-bg-main); text-align: center; z-index: 101; line-height: 1.5rem; border-left: 2px solid var(--c-topnav); border-bottom: 2px solid var(--c-topnav); border-right: 2px solid var(--c-topnav); border-bottom-left-radius: 0.5rem; border-bottom-right-radius: 0.5rem; transition: top 0.5s ease 0s;";
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
    ul_topmost_label.textContent = "Top";
    ul_topmost_label.id = "ul-topmost-label"
    ul_topmost_label.style = "cursor: pointer;"
    
    ul_topmost_div.appendChild(ul_topmost);
    ul_topmost_div.appendChild(ul_topmost_label);

    var ul_pair = document.createElement("input");
    ul_pair.id = "ul-pair";
    ul_pair.type = "checkbox";
    ul_pair.checked = """ +
                       str(browser.threader.settings["browser_pair"]).lower()
                       + """;
    ul_pair.style = "cursor: pointer; margin-left: 0.75rem;"
    ul_pair.classList.add("ul-overlay-button");

    var ul_pair_label = document.createElement("label");
    ul_pair_label.setAttribute("for", "ul-pair");
    ul_pair_label.textContent = "Pair";
    ul_pair_label.id = "ul-pair-label"
    ul_pair_label.style = "cursor: pointer;"

    ul_topmost_div.appendChild(ul_pair);
    ul_topmost_div.appendChild(ul_pair_label);
    window.UL_OVERLAY.appendChild(ul_topmost_div);

    window.set_schedule_button_enabled = function(enabled) {
        ul_schedule.style.display = enabled ? "" : "none";
        ul_topmost_div.style.right = enabled ? "182px" : "108px";
        if (!enabled) {
            ul_schedule.style.filter = "";
            if (window.await_schedule_window_timeout) {
                clearTimeout(window.await_schedule_window_timeout);
                window.await_schedule_window_timeout = null;
            }
        }
    }
    window.set_schedule_button_enabled(arguments[0]);
    

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

    // Trackblazer Schedule window.
    window.await_schedule_window_timeout = null;
    window.await_schedule_window = function() {
        window.await_schedule_window_timeout = setTimeout(function() {
            ul_schedule.style.filter = "";
        }, 15000);

        ul_schedule.style.filter = "brightness(0.5)";
        fetch('http://127.0.0.1:3150/open-schedule-window', { method: 'POST' });
    }
    window.schedule_window_opened = function() {
        if (window.await_schedule_window_timeout) {
            clearTimeout(window.await_schedule_window_timeout);
        }
        ul_schedule.style.filter = "";
    }

    ul_schedule.addEventListener("click", window.await_schedule_window);

    // Top and game-pairing toggles
    window.await_topmost = function() {
        var checkbox = document.getElementById("ul-topmost");
        fetch('http://127.0.0.1:3150/topmost', { method: 'POST', body: checkbox.checked, headers: { 'Content-Type': 'text/plain'  } } );
    }
    ul_topmost.addEventListener("click", window.await_topmost);

    window.await_pair = function() {
        var checkbox = document.getElementById("ul-pair");
        fetch('http://127.0.0.1:3150/pair', { method: 'POST', body: checkbox.checked, headers: { 'Content-Type': 'text/plain'  } } );
    }
    ul_pair.addEventListener("click", window.await_pair);
    
    window.send_screen_rect = function() {
        let rect = {
            'x': window.screenX,
            'y': window.screenY,
            'width': window.outerWidth,
            'height': window.outerHeight
        };
        let serializedRect = JSON.stringify(rect);
        if (serializedRect !== window.UL_LAST_SCREEN_RECT) {
            window.UL_LAST_SCREEN_RECT = serializedRect;
            fetch('http://127.0.0.1:3150/helper-window-rect', { method: 'POST', body: serializedRect, headers: { 'Content-Type': 'text/plain' } });
        }
        setTimeout(window.send_screen_rect, 2000);
    }
    setTimeout(window.send_screen_rect, 2000);

    """, False)

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
        let serializedRect = JSON.stringify(rect);
        if (serializedRect !== window.UL_LAST_SCREEN_RECT) {
            window.UL_LAST_SCREEN_RECT = serializedRect;
            fetch('http://127.0.0.1:3150/skills-window-rect', { method: 'POST', body: serializedRect, headers: { 'Content-Type': 'text/plain' } });
        }
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


def setup_schedule_window(browser: horsium.BrowserWindow):
    browser.execute_script("""
    window.send_screen_rect = function() {
        let rect = {
            'x': window.screenX,
            'y': window.screenY,
            'width': window.outerWidth,
            'height': window.outerHeight
        };
        let serializedRect = JSON.stringify(rect);
        if (serializedRect !== window.UL_LAST_SCREEN_RECT) {
            window.UL_LAST_SCREEN_RECT = serializedRect;
            fetch('http://127.0.0.1:3150/schedule-window-rect', { method: 'POST', body: serializedRect, headers: { 'Content-Type': 'text/plain' } });
        }
        setTimeout(window.send_screen_rect, 2000);
    }
    setTimeout(window.send_screen_rect, 2000);
    
    if (window.clearCompletedRaces) {
        window.clearCompletedRaces();
    }
    """)

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


def gametora_close_ad_banner(browser, is_training_helper=None):
    # Install permanent CSS and a lightweight observer once. Dynamic ads are
    # hidden locally as they appear instead of requiring a WebDriver round trip
    # for every CarrotBlender packet.
    if is_training_helper is None:
        is_training_helper = "training-event-helper" in browser.url
    browser.execute_script("""
        const isTrainingHelper = Boolean(arguments[0]);
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

        window.UL_HIDE_DYNAMIC_ADS = () => {
            if (!isTrainingHelper) return;
            let pageMain = document.querySelector("[id^='styles_page-main_']");
            let content = pageMain?.children?.[1];
            let supportPromo = content?.children?.[content.childElementCount - 1];
            if (supportPromo) supportPromo.style.display = "none";
        };

        window.UL_CLOSE_TRANSIENTS = (includeEvents) => {
            if (!includeEvents) return;
            document.getElementById("ul-event-focus-state")?.clear?.();
            document.querySelectorAll(
                "div[id^='event-viewer-'] button[class^='sc-'][aria-expanded=true], " +
                "div[class^='compatibility_result_box_'] button[class^='sc-'][aria-expanded=true]"
            ).forEach(element => element.click());
        };

        if (!window.UL_AD_OBSERVER) {
            let cleanupQueued = false;
            window.UL_AD_OBSERVER = new MutationObserver(() => {
                if (cleanupQueued) return;
                cleanupQueued = true;
                requestAnimationFrame(() => {
                    cleanupQueued = false;
                    window.UL_HIDE_DYNAMIC_ADS();
                });
            });
            window.UL_AD_OBSERVER.observe(document.documentElement, {
                childList: true,
                subtree: true
            });
        }
        window.UL_HIDE_DYNAMIC_ADS();
    """, is_training_helper)
