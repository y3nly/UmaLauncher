from event_rewards import RewardFormatter


class RuntimeExtensions:
    """Display current game-provided reward possibilities when available."""

    def __init__(self, owner):
        self.owner = owner
        self.reward_formatter = RewardFormatter(owner)
        if hasattr(owner, "status_name_dict"):
            try:
                import mdb
                self.reward_formatter.character_name_dict = mdb.get_chara_name_dict()
                with mdb.Connection() as (_, cursor):
                    cursor.execute('SELECT "index", text FROM text_data WHERE category = 47')
                    self.reward_formatter.skill_name_dict = dict(cursor.fetchall())
            except Exception:
                # Missing name data must not prevent the catalog fallback.
                pass
        self.event_context = None
        self.next_event_context = None
        self.event_generation = None

    def on_event_detected(self, event_data, event_titles):
        self.next_event_context = event_data

    def on_event_opened(self, generation):
        self.event_generation = generation
        self.event_context = self.next_event_context
        self.next_event_context = None

    def on_event_chain_changed(self, chain, generation):
        pass

    def on_event_closed(self, generation):
        if generation == self.event_generation:
            self.event_generation = None
            self.event_context = None

    def on_response(self, data, generation):
        if (generation is None or generation != self.event_generation
                or not self.event_context or "choice_reward_array" not in data):
            return
        if self.owner.get_helper_ui_mode() != self.owner.HELPER_UI_MODERN:
            return
        event = self.reward_formatter.format_event(data, self.event_context)
        browser = getattr(self.owner, "browser", None)
        if event and browser and browser.alive():
            browser.execute_script("return window.UL_SET_GAME_REWARDS(arguments[0], arguments[1]);", event, generation)

    def enrich_training_commands(self, packet, command_info):
        pass

    def configure_modern_page(self, browser):
        pass

    def close(self):
        self.event_generation = None
        self.event_context = None
        self.next_event_context = None


def get_release_config():
    """Return build-specific updater policy without branching version.py."""
    return True, ("UmaLauncher-Global.exe",)


def create(owner):
    return RuntimeExtensions(owner)
