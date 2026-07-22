import re


DARK_THEME = {
    "text": "#F4F5F7",
    "border": "#3B404D",
    "background": "#171923",
    "panel": "#242735",
}

THEME_SETTING_KEYS = {
    "text": "helper_theme_text_color",
    "border": "helper_theme_border_color",
    "background": "helper_theme_background_color",
    "panel": "helper_theme_panel_color",
}

THEME_DARK = 0
THEME_CUSTOM = 1
_HEX_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")


def normalize_theme_mode(value):
    try:
        return THEME_CUSTOM if int(value) == THEME_CUSTOM else THEME_DARK
    except (TypeError, ValueError):
        return THEME_DARK


def normalize_color(value, fallback):
    value = str(value or "").strip()
    if not _HEX_COLOR_PATTERN.fullmatch(value):
        return fallback
    return value.upper()


def _setting_value(settings, key, fallback):
    try:
        value = settings[key]
    except (KeyError, TypeError, AttributeError):
        return fallback
    return getattr(value, "value", value)


def build_theme(settings):
    mode = normalize_theme_mode(
        _setting_value(settings, "helper_theme", THEME_DARK)
    )
    colors = dict(DARK_THEME)
    if mode == THEME_CUSTOM:
        for theme_key, setting_key in THEME_SETTING_KEYS.items():
            colors[theme_key] = normalize_color(
                _setting_value(settings, setting_key, colors[theme_key]),
                colors[theme_key],
            )
    return {
        "mode": "custom" if mode == THEME_CUSTOM else "dark",
        **colors,
    }
