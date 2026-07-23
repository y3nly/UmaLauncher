class RuntimeExtensions:
    """Optional runtime feature hooks; the public build intentionally does nothing."""

    def __init__(self, owner):
        self.owner = owner

    def on_event_detected(self, event_data, event_titles):
        pass

    def on_event_opened(self, generation):
        pass

    def on_event_chain_changed(self, chain, generation):
        pass

    def on_event_closed(self, generation):
        pass

    def on_response(self, data, generation):
        pass

    def enrich_training_commands(self, packet, command_info):
        pass

    def configure_modern_page(self, browser):
        pass

    def close(self):
        pass


def get_release_config():
    """Return build-specific updater policy without branching version.py."""
    return True, "UmaLauncher-Private.exe"


def create(owner):
    from umalauncher_private.runtime import PrivateRuntimeExtensions

    return PrivateRuntimeExtensions(owner)
