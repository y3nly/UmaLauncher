import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
import threading

from loguru import logger

import constants


PARAMETER_NAMES = {
    1: "Speed",
    2: "Stamina",
    3: "Power",
    4: "Guts",
    5: "Wisdom",
    10: "Energy",
    11: "Max Energy",
    20: "Mood",
    30: "Skill Pt",
    40: "Fans",
    51: "Speed Cap",
    52: "Stamina Cap",
    53: "Power Cap",
    54: "Guts Cap",
    55: "Wisdom Cap",
}

TRAINING_NAMES = {
    101: "Speed",
    105: "Stamina",
    102: "Power",
    103: "Guts",
    106: "Wisdom",
}

LIVE_PERFORMANCE_NAMES = {
    index: token_name.title()
    for index, token_name in enumerate(constants.GL_TOKEN_LIST, start=1)
}

EFFECT_VALUE_TYPE_NONE = 0
EFFECT_VALUE_TYPE_VALUE = 1
EFFECT_VALUE_TYPE_PARAMETER = 2
EFFECT_VALUE_TYPE_CHARACTER = 3
EFFECT_VALUE_TYPE_SKILL = 4
EFFECT_VALUE_TYPE_CHARACTER_EFFECT = 5
EFFECT_VALUE_TYPE_TRAINING = 6
EFFECT_VALUE_TYPE_TURN = 7
EFFECT_VALUE_TYPE_SCENARIO_LIVE_PERFORMANCE = 8
EFFECT_VALUE_TYPE_SCENARIO_VENUS_SPIRIT = 9
EFFECT_VALUE_TYPE_SCENARIO_LEGEND_BUFF_GAUGE = 10

DISPLAY_TYPE_TEXT = 0
DISPLAY_TYPE_SKILL = 1
DISPLAY_TYPE_ANOTHER_EVENT_SKILL = 2

EVENT_CHOICE_REWARD_META = {
    1: ("{0} +{1}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_PARAMETER, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE)),
    2: ("{0} -{1}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_PARAMETER, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE)),
    3: ("+{0} Fans", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE, EFFECT_VALUE_TYPE_NONE)),
    4: ("Friendship with {0} +{1}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_CHARACTER, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE)),
    5: ("Friendship with {0} +{1} (Friendship cannot change for Support Cards not in your deck)", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_CHARACTER, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE)),
    6: ("Hint lvl +{0}", DISPLAY_TYPE_SKILL, (EFFECT_VALUE_TYPE_SKILL, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE)),
    7: ("Gain", DISPLAY_TYPE_SKILL, (EFFECT_VALUE_TYPE_SKILL, EFFECT_VALUE_TYPE_NONE, EFFECT_VALUE_TYPE_NONE)),
    8: "Chance to gain a random skill",
    9: ("Become {0}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_CHARACTER_EFFECT, EFFECT_VALUE_TYPE_NONE, EFFECT_VALUE_TYPE_NONE)),
    10: ("Cures {0}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_CHARACTER_EFFECT, EFFECT_VALUE_TYPE_NONE, EFFECT_VALUE_TYPE_NONE)),
    11: ("Unlock recreation with {0}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_CHARACTER, EFFECT_VALUE_TYPE_NONE, EFFECT_VALUE_TYPE_NONE)),
    12: "End this Support Card's chain event",
    13: ("All attributes +{0}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE, EFFECT_VALUE_TYPE_NONE)),
    14: ("Random {0} attribute(s) +{1}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE)),
    15: "Cures all bad conditions",
    16: ("Randomly cures {0} bad condition(s)", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE, EFFECT_VALUE_TYPE_NONE)),
    17: ("Restrict {0} training", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_TRAINING, EFFECT_VALUE_TYPE_NONE, EFFECT_VALUE_TYPE_NONE)),
    18: "Restrict race entry",
    19: ("Previously trained attribute +{0}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE, EFFECT_VALUE_TYPE_NONE)),
    20: ("Previously trained attribute -{0}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE, EFFECT_VALUE_TYPE_NONE)),
    21: "Stat gains based on race grade",
    22: "Stat gains based on race grade and result",
    23: ("{0} +{1}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_SCENARIO_LIVE_PERFORMANCE, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE)),
    27: ("Friendship with {1} lowest-friendship Support Card(s) +{2} (Excludes {0})", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_CHARACTER, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_VALUE)),
    28: ("Friendship with {0} lowest-friendship Support Card(s) +{1}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE)),
    29: ("Friendship with lowest-friendship Support Card +{1} (Excludes {0})", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_CHARACTER, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE)),
    30: ("Friendship with lowest-friendship Support Card +{0}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE, EFFECT_VALUE_TYPE_NONE)),
    31: ("Receive the following skill hints in chain event {0}: {1}", DISPLAY_TYPE_ANOTHER_EVENT_SKILL, (EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_SKILL, EFFECT_VALUE_TYPE_NONE)),
    32: ("Receive the following skill hints in chain event {0}: {1}, {2}", DISPLAY_TYPE_ANOTHER_EVENT_SKILL, (EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_SKILL, EFFECT_VALUE_TYPE_SKILL)),
    34: ("Random {0} attribute(s) -{1}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE)),
    35: ("Friendship with {0} -{1}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_CHARACTER, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE)),
    36: ("Friendship with {0} -{1} (Friendship cannot change for Support Cards not in your deck)", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_CHARACTER, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_NONE)),
    37: ("Become {0}", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_CHARACTER_EFFECT, EFFECT_VALUE_TYPE_NONE, EFFECT_VALUE_TYPE_NONE)),
    38: ("{0} -{1} (Prevented by {2})", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_PARAMETER, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_CHARACTER_EFFECT)),
    39: ("{0} +{1} (Prevented by {2})", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_PARAMETER, EFFECT_VALUE_TYPE_VALUE, EFFECT_VALUE_TYPE_CHARACTER_EFFECT)),
    40: ("Become {0} (Prevented by {1})", DISPLAY_TYPE_TEXT, (EFFECT_VALUE_TYPE_CHARACTER_EFFECT, EFFECT_VALUE_TYPE_CHARACTER_EFFECT, EFFECT_VALUE_TYPE_NONE)),
}


class EventRewardParser:
    def __init__(self, status_name_dict=None, skill_name_dict=None, character_name_dict=None, logger=None):
        self.status_name_dict = status_name_dict or {}
        self.skill_name_dict = skill_name_dict or {}
        self.character_name_dict = character_name_dict or {}
        self.logger = logger

    def create_event_context(self, event_data, event_titles=None):
        if not isinstance(event_data, dict):
            return None

        event_contents_info = event_data.get("event_contents_info") or {}
        choice_array = normalize_choice_array(event_contents_info.get("choice_array"))

        return {
            "event_id": event_data.get("event_id"),
            "story_id": event_data.get("story_id"),
            "event_titles": event_titles or [],
            "choice_array": choice_array,
        }

    def parse_choice_reward_response(self, response_data, event_context=None, event_id=None):
        if not isinstance(response_data, dict):
            return None

        choice_reward_array = response_data.get("choice_reward_array")
        if not isinstance(choice_reward_array, list):
            return None

        rewards_by_choice = defaultdict(list)
        rewards_without_choice = []
        for reward in choice_reward_array:
            if not isinstance(reward, dict):
                continue

            choice_number = _safe_int(reward.get("select_index"))
            if choice_number is None:
                rewards_without_choice.append(reward)
                continue

            rewards_by_choice[choice_number].append(reward)

        event_context = event_context or {}
        choice_array = normalize_choice_array(event_context.get("choice_array"))
        choices = []

        if choice_array:
            uses_sequential_outcome_indices = _uses_sequential_outcome_indices(choice_array, rewards_by_choice)
            next_sequential_outcome_index = 1
            for index, choice in enumerate(choice_array, start=1):
                variants = rewards_by_choice.get(index, [])
                # choice_reward_array[].select_index is the option number.
                # Most events use choice_array[n].select_index as the branch within that option.
                # Some events use a flattened reward index across all option variants instead.
                raw_predicted_select_index = _safe_int(choice.get("select_index"), default=1)
                predicted_branch_index = _resolve_choice_outcome_index(
                    raw_predicted_select_index,
                    variants,
                    next_sequential_outcome_index,
                    uses_sequential_outcome_indices,
                )
                next_sequential_outcome_index += _sequential_variant_count(variants)
                choices.append(
                    self._build_choice_prediction(
                        choice_number=index,
                        choice=choice,
                        variants=variants,
                        outcome_index=predicted_branch_index,
                        predicted_branch_index=predicted_branch_index,
                    )
                )

            for choice_number in sorted(rewards_by_choice):
                if choice_number <= len(choice_array):
                    continue

                choices.append(
                    self._build_choice_prediction(
                        choice_number=choice_number,
                        choice={},
                        variants=rewards_by_choice[choice_number],
                        outcome_index=1,
                    )
                )
        else:
            for choice_number in sorted(rewards_by_choice):
                variants = rewards_by_choice[choice_number]
                for outcome_index, reward in enumerate(variants, start=1):
                    choices.append(
                        self._build_choice_prediction(
                            choice_number=choice_number,
                            choice={},
                            variants=variants,
                            outcome_index=outcome_index,
                            selected_reward=reward,
                        )
                    )

        if rewards_without_choice:
            choices.append(
                {
                    "choice_number": None,
                    "outcome_index": None,
                    "variant_count": len(rewards_without_choice),
                    "summary": "Ungrouped reward data",
                    "rewards": [
                        self._format_reward_part(effect)
                        for reward in rewards_without_choice
                        for effect in reward.get("gain_param_array", [])
                    ],
                }
            )

        return {
            "event_id": event_id or event_context.get("event_id"),
            "story_id": event_context.get("story_id"),
            "event_titles": event_context.get("event_titles") or [],
            "choices": choices,
        }

    def _build_choice_prediction(
            self,
            choice_number,
            choice,
            variants,
            outcome_index,
            predicted_branch_index=None,
            selected_reward=None):
        if selected_reward is None:
            selected_reward = _select_variant(variants, outcome_index)

        reward_parts = []
        if selected_reward:
            reward_parts = [
                self._format_reward_part(effect)
                for effect in selected_reward.get("gain_param_array", [])
                if isinstance(effect, dict)
            ]

        summary = ", ".join(part["text"] for part in reward_parts)
        if not summary:
            if selected_reward:
                summary = "No visible rewards"
            elif variants:
                summary = f"Outcome {outcome_index} was not present in reward packet"
            else:
                summary = "No reward data"

        return {
            "choice_number": choice_number,
            "outcome_index": outcome_index,
            "predicted_branch_index": predicted_branch_index,
            "variant_count": len(variants),
            "summary": summary,
            "rewards": reward_parts,
        }

    def _format_reward_part(self, effect):
        display_id = _safe_int(effect.get("display_id"))
        effect_value_0 = _safe_int(effect.get("effect_value_0"), default=0)
        effect_value_1 = _safe_int(effect.get("effect_value_1"), default=0)
        effect_value_2 = _safe_int(effect.get("effect_value_2"), default=0)

        metadata = EVENT_CHOICE_REWARD_META.get(display_id)
        if metadata is not None:
            values = (effect_value_0, effect_value_1, effect_value_2)
            tone = "neutral"
            if display_id in (6, 31, 32):
                tone = "hint"
            elif display_id in (
                1, 3, 4, 5, 9, 10, 11, 13, 14, 15, 16, 19,
                23, 27, 28, 29, 30, 39,
            ):
                tone = "positive"
            elif display_id in (2, 12, 17, 18, 20, 34, 35, 36, 37, 38, 40):
                tone = "negative"
            reward_part = {
                "kind": _reward_kind(display_id),
                "tone": tone,
                "text": self._format_event_choice_reward_text(metadata, values),
            }
            highlights = self._format_reward_highlights(metadata, values)
            if highlights:
                reward_part["highlights"] = highlights
            return reward_part

        return {
            "kind": "unknown",
            "tone": "neutral",
            "text": self._format_unknown_effect(display_id, effect_value_0, effect_value_1, effect_value_2),
        }

    def _format_event_choice_reward_text(self, metadata, values):
        if isinstance(metadata, str):
            return metadata

        template, display_type, value_types = metadata

        if display_type == DISPLAY_TYPE_SKILL:
            skill_name = self._format_effect_value(EFFECT_VALUE_TYPE_SKILL, values[0])
            if values[1]:
                return f"{skill_name} hint level +{values[1]}"
            return f"Gain {skill_name}"

        args = [
            self._format_effect_value(value_type, value)
            for value_type, value in zip(value_types, values)
        ]

        try:
            text = template.format(*args)
        except Exception:
            text = f"{template}: {', '.join(str(value) for value in values)}"

        return _clean_template_text(text)

    def _format_reward_highlights(self, metadata, values):
        if isinstance(metadata, str):
            return []

        _template, display_type, value_types = metadata
        if display_type == DISPLAY_TYPE_SKILL:
            if not values[0]:
                return []
            return [self._format_effect_value(EFFECT_VALUE_TYPE_SKILL, values[0])]

        if display_type == DISPLAY_TYPE_ANOTHER_EVENT_SKILL:
            highlights = []
            for value_type, value in zip(value_types, values):
                if value_type == EFFECT_VALUE_TYPE_SKILL and value:
                    highlights.append(self._format_effect_value(EFFECT_VALUE_TYPE_SKILL, value))
            return highlights

        return []

    def _format_effect_value(self, value_type, value):
        if value_type == EFFECT_VALUE_TYPE_NONE:
            return ""

        if value_type == EFFECT_VALUE_TYPE_VALUE:
            return str(value)

        if value_type == EFFECT_VALUE_TYPE_PARAMETER:
            return PARAMETER_NAMES.get(value, f"Param {value}")

        if value_type == EFFECT_VALUE_TYPE_CHARACTER:
            return self._name_from_dict(self.character_name_dict, value) or f"Character {value}"

        if value_type == EFFECT_VALUE_TYPE_SKILL:
            return self._name_from_dict(self.skill_name_dict, value) or f"Skill {value}"

        if value_type == EFFECT_VALUE_TYPE_CHARACTER_EFFECT:
            return self._name_from_dict(self.status_name_dict, value) or f"Status {value}"

        if value_type == EFFECT_VALUE_TYPE_TRAINING:
            return TRAINING_NAMES.get(value, f"Training {value}")

        if value_type == EFFECT_VALUE_TYPE_TURN:
            return f"Turn {value}"

        if value_type == EFFECT_VALUE_TYPE_SCENARIO_LIVE_PERFORMANCE:
            return LIVE_PERFORMANCE_NAMES.get(
                value,
                f"Grand Live performance {value}",
            )

        if value_type == EFFECT_VALUE_TYPE_SCENARIO_VENUS_SPIRIT:
            return f"Grand Masters spirit {value}"

        if value_type == EFFECT_VALUE_TYPE_SCENARIO_LEGEND_BUFF_GAUGE:
            return f"Legend buff gauge {value}"

        return str(value)

    def _format_unknown_effect(self, display_id, effect_value_0, effect_value_1, effect_value_2):
        values = [effect_value_0, effect_value_1, effect_value_2]
        values = [value for value in values if value]
        value_text = ", ".join(str(value) for value in values) if values else "0"
        return f"Effect {display_id}: {value_text}"

    def _name_from_dict(self, name_dict, key):
        if key in name_dict:
            return name_dict[key]

        string_key = str(key)
        if string_key in name_dict:
            return name_dict[string_key]

        return None


def normalize_choice_array(choice_array):
    if isinstance(choice_array, list):
        return [choice for choice in choice_array if isinstance(choice, dict)]

    if isinstance(choice_array, dict):
        return [choice_array]

    return []


def _select_variant(variants, outcome_index):
    if not variants:
        return None

    if outcome_index is not None and 1 <= outcome_index <= len(variants):
        return variants[outcome_index - 1]

    if len(variants) == 1:
        return variants[0]

    return None


def _uses_sequential_outcome_indices(choice_array, rewards_by_choice):
    next_sequential_outcome_index = 1
    for index, choice in enumerate(choice_array, start=1):
        variants = rewards_by_choice.get(index, [])
        variant_count = len(variants)
        select_index = _safe_int(choice.get("select_index"))

        if select_index is not None and variant_count:
            is_local_branch = 1 <= select_index <= variant_count
            is_sequential_branch = (
                    next_sequential_outcome_index
                    <= select_index
                    < next_sequential_outcome_index + variant_count
            )
            if is_sequential_branch and not is_local_branch:
                return True

        next_sequential_outcome_index += _sequential_variant_count(variants)

    return False


def _sequential_variant_count(variants):
    variant_count = len(variants)
    if variant_count > 1:
        return variant_count

    return 0


def _resolve_choice_outcome_index(
        select_index,
        variants,
        sequential_start_index,
        prefer_sequential=False):
    if select_index is None:
        return 1

    if variants:
        sequential_outcome_index = select_index - sequential_start_index + 1
        if prefer_sequential and 1 <= sequential_outcome_index <= len(variants):
            return sequential_outcome_index

    if variants and 1 <= select_index <= len(variants):
        return select_index

    if variants:
        sequential_outcome_index = select_index - sequential_start_index + 1
        if 1 <= sequential_outcome_index <= len(variants):
            return sequential_outcome_index

    if len(variants) == 1:
        return 1

    return select_index


def _safe_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _reward_kind(display_id):
    if display_id in (1, 2, 3, 13, 14, 19, 20, 21, 22, 23, 34, 38, 39):
        return "parameter"

    if display_id in (4, 5, 11, 27, 28, 29, 30, 35, 36):
        return "bond"

    if display_id in (6, 7, 8, 31, 32):
        return "skill"

    if display_id in (9, 10, 15, 16, 37, 40):
        return "status"

    return "unknown"


def _clean_template_text(text):
    text = text.replace("\\n", " ")
    text = re.sub(r"</?color[^>]*>", "", text)
    return " ".join(text.split())


def sanitize_event_prediction(prediction):
    """Return the bounded, browser-safe subset of a parsed prediction."""
    if not isinstance(prediction, dict):
        return None

    def clean_text(value, limit=500):
        return str(value or "").replace("\x00", "")[:limit]

    def clean_choice_number(value):
        if isinstance(value, bool):
            return None
        try:
            value = int(value)
        except (TypeError, ValueError):
            return None
        return value if 1 <= value <= 8 else None

    choices = []
    for choice in (prediction.get("choices") or [])[:8]:
        if not isinstance(choice, dict):
            continue
        rewards = []
        for reward in (choice.get("rewards") or [])[:16]:
            if not isinstance(reward, dict):
                continue
            tone = clean_text(reward.get("tone"), 16)
            if tone not in {"positive", "negative", "hint", "neutral"}:
                tone = "neutral"
            rewards.append({
                "kind": clean_text(reward.get("kind"), 40),
                "tone": tone,
                "text": clean_text(reward.get("text")),
                "highlights": [
                    clean_text(highlight, 120)
                    for highlight in (reward.get("highlights") or [])[:8]
                ],
            })
        choices.append({
            "choice_number": clean_choice_number(choice.get("choice_number")),
            "outcome_index": choice.get("outcome_index"),
            "summary": clean_text(choice.get("summary")),
            "rewards": rewards,
        })

    return {
        "event_titles": [
            clean_text(title, 240)
            for title in (prediction.get("event_titles") or [])[:8]
        ],
        "choices": choices,
    }


@lru_cache(maxsize=2)
def _load_asset(name):
    asset_path = Path(__file__).with_name("assets") / name
    return asset_path.read_text(encoding="utf-8")


class EventPredictionExtension:
    """Private event-prediction state and browser rendering hooks.

    ``on_event_detected`` deliberately stages the newly parsed context until
    ``on_response``.  CarrotJuicer invokes those hooks in that order for the
    same decoded packet, so a chained event's reward array is never paired
    with the event that was active before it.
    """

    def __init__(self, owner, parser=None):
        self.owner = owner
        if parser is None:
            try:
                import mdb
                character_name_dict = mdb.get_chara_name_dict()
            except Exception:
                logger.warning(
                    "Could not load character names for private event predictions"
                )
                character_name_dict = {}
            parser = EventRewardParser(
                status_name_dict=getattr(owner, "status_name_dict", {}),
                skill_name_dict=getattr(owner, "skill_name_dict", {}),
                character_name_dict=character_name_dict,
                logger=logger,
            )
        self.parser = parser
        self._lock = threading.RLock()
        self._staged_context = None
        self._last_context = None
        self._pending_event_id = None
        self._active_generation = None
        self._active_context = None

    def on_event_detected(self, event_data, event_titles):
        context = self.parser.create_event_context(event_data, event_titles)
        with self._lock:
            self._staged_context = context
            if context:
                self._last_context = context
                self._pending_event_id = context.get("event_id")
        return context

    def on_event_opened(self, generation):
        with self._lock:
            self._active_generation = generation
            self._active_context = self._staged_context or self._last_context

    def on_event_chain_changed(self, chain, generation):
        # UL_SET_GAMETORA_EVENT already updates chain visibility and renders the
        # browser's cached prediction. Repeating that work here adds Selenium
        # round trips for every navigation without changing the displayed event.
        with self._lock:
            return generation == self._active_generation

    def on_event_closed(self, generation):
        with self._lock:
            if generation != self._active_generation:
                return
            self._active_generation = None
            self._active_context = None

    def on_response(self, data, generation):
        if not isinstance(data, dict):
            return None

        with self._lock:
            staged_context = self._staged_context
            context = (
                staged_context
                or (
                    self._active_context
                    if generation == self._active_generation
                    else None
                )
                or self._last_context
            )
            event_id = (
                context.get("event_id")
                if isinstance(context, dict)
                else self._pending_event_id
            )

        try:
            if "choice_reward_array" not in data:
                return None
            prediction = self.parser.parse_choice_reward_response(
                data,
                context,
                event_id=event_id,
            )
            prediction = sanitize_event_prediction(prediction)
            if not prediction:
                return None

            if self._is_modern():
                with self._lock:
                    if generation != self._active_generation:
                        return prediction
                if not getattr(
                    self.owner,
                    "_active_event_source_available",
                    False,
                ):
                    return prediction
                browser = self._live_browser()
                if browser:
                    # Keep the browser's cache current even while a different
                    # chain card is visible; its generation and chain guards
                    # decide when the prediction can be displayed.
                    browser.execute_script(
                        "return window.UL_UPDATE_EVENT_REWARDS(arguments[0], arguments[1]);",
                        generation,
                        prediction,
                    )
                return prediction

            self._render_legacy(prediction)
            return prediction
        finally:
            # Only consume the context used by this response.  If another
            # event was staged concurrently, leave the newer context intact.
            with self._lock:
                if self._staged_context is staged_context:
                    self._staged_context = None

    def configure_modern_page(self, browser):
        if not browser:
            return False
        try:
            return browser.execute_script(_load_asset("modern_prediction.js"))
        except Exception:
            logger.exception("Could not install private Modern event predictions")
            return False

    def close(self):
        with self._lock:
            self._staged_context = None
            self._last_context = None
            self._pending_event_id = None
            self._active_generation = None
            self._active_context = None

    def _is_modern(self):
        try:
            return (
                self.owner.get_helper_ui_mode()
                == getattr(self.owner, "HELPER_UI_MODERN", 1)
            )
        except Exception:
            return False

    def _live_browser(self):
        browser = getattr(self.owner, "browser", None)
        if not browser:
            return None
        try:
            return browser if browser.alive() else None
        except Exception:
            return None

    def _render_legacy(self, prediction):
        browser = self._live_browser()
        if not browser:
            return False
        event_titles = prediction.get("event_titles") or []
        anchor = None
        if event_titles:
            try:
                anchor = self.owner.determine_event_element(event_titles)
            except Exception:
                logger.exception(
                    "Could not locate the Legacy event for private predictions"
                )
        try:
            browser.execute_script(
                _load_asset("legacy_prediction.js"),
                anchor,
                prediction,
            )
            return True
        except Exception:
            logger.exception("Could not render private Legacy event predictions")
            return False
