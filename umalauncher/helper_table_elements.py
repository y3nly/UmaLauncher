import enum
import html as html_lib
import json
import re
import traceback

from loguru import logger
import gui
import util
import constants
import mdb
import settings_elements as se

TABLE_HEADERS = {
    "fac": "Facility",
    "speed": "Speed",
    "stamina": "Stamina",
    "power": "Power",
    "guts": "Guts",
    "wiz": "Wisdom",
    "ss_match": "SS Match",
    "ticket": "Ticket",
    "pr_activities": "PR Activities",
}

MODERN_FACILITY_ICON_FILES = {
    "speed": "speed.png",
    "stamina": "stamina.png",
    "power": "power.png",
    "guts": "guts.png",
    "wiz": "wisdom.png",
}
MODERN_UNITY_CHARGE_FILES = {
    1: "unity_charge_1.png",
    2: "unity_charge_2.png",
    3: "unity_charge_3.png",
    4: "unity_charge_4.png",
}
MODERN_MOOD_ICON_FILES = {
    "Awful": "mood_0.png",
    "Bad": "mood_1.png",
    "Normal": "mood_2.png",
    "Good": "mood_3.png",
    "Great": "mood_4.png",
}
TRAINING_HELPER_ASSET_URL = "/training-helper/assets"
LEGACY_CELL_STYLE = (
    "text-overflow: clip;white-space: nowrap;overflow: hidden;"
)


def render_modern_hint_marker():
    """Render GameTora's in-game hint badge attached to its source portrait."""
    size = 20
    source = f"{TRAINING_HELPER_ASSET_URL}/hint.png"
    return (
        f'<span class="hint-marker" aria-label="Hint available" title="Hint available" '
        f'style="position:absolute;right:-3px;top:-3px;width:{size}px;height:{size}px;'
        'filter:drop-shadow(0 1px 2px rgba(0,0,0,.55));z-index:10;pointer-events:none;">'
        f'<img src="{source}" alt="" width="{size}" height="{size}"></span>'
    )


def render_modern_scenario_marker(status):
    """Render only the active blue/purple Unity Cup burst badge."""
    marker_assets = {
        "spirit_burst": ("Spirit Burst", "spirit_burst.png"),
        "extreme_burst": ("Extreme Spirit Burst", "extreme_spirit_burst.png"),
    }
    marker = marker_assets.get(status)
    if not marker:
        return ""
    label, filename = marker
    source = f"{TRAINING_HELPER_ASSET_URL}/{filename}"
    css_status = status.replace("_", "-")
    escaped_label = html_lib.escape(label, quote=True)
    return (
        f'<span class="scenario-marker scenario-marker-{css_status}" '
        f'aria-label="{escaped_label}" title="{escaped_label}">'
        f'<img src="{source}" alt="" width="14" height="15"></span>'
    )


def render_modern_unity_charge(value):
    """Render the game's Unity Cup soul bubble at its current fill stage."""
    try:
        value = max(0, min(4, int(value)))
    except (TypeError, ValueError):
        return ""

    base = f"{TRAINING_HELPER_ASSET_URL}/unity_charge_base.png"

    label = html_lib.escape(f"Spirit gauge {value} of 4", quote=True)
    layers = [
        f'<img class="unity-charge-base" src="{html_lib.escape(base, quote=True)}" alt="">'
    ]
    fill_filename = MODERN_UNITY_CHARGE_FILES.get(value)
    if fill_filename:
        fill = f"{TRAINING_HELPER_ASSET_URL}/{fill_filename}"
        frame = f"{TRAINING_HELPER_ASSET_URL}/unity_charge_frame.png"
        layers.append(
            f'<img class="unity-charge-fill unity-charge-fill-{value}" '
            f'src="{fill}" alt="">'
        )
        layers.append(
            f'<img class="unity-charge-frame" src="{frame}" alt="">'
        )
    return (
        f'<span class="unity-charge unity-charge-{value}" role="img" '
        f'aria-label="{label}" title="{label}">{"".join(layers)}</span>'
    )


def render_modern_mood(motivation):
    """Render the matching in-game Global mood badge with a text fallback."""
    label = str(motivation or "Unknown")
    escaped_label = html_lib.escape(label)
    filename = MODERN_MOOD_ICON_FILES.get(label)
    source = f"{TRAINING_HELPER_ASSET_URL}/{filename}" if filename else ""
    if not source:
        return f'<strong class="modern-mood-fallback">{escaped_label}</strong>'
    source = html_lib.escape(source, quote=True)
    accessible_label = html_lib.escape(f"Mood: {label}", quote=True)
    return (
        f'<img class="modern-mood-icon" src="{source}" '
        f'alt="{accessible_label}" title="{accessible_label}">'
    )


def render_modern_bond_indicator(value, display_type):
    """Render the configured Off/Number/Bar/Both bond representation."""
    if display_type not in (1, 2, 3):
        return ""

    try:
        value = max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return ""

    color = ""
    for cutoff, candidate in constants.BOND_COLOR_DICT.items():
        if value < cutoff:
            break
        color = candidate
    number = f'<span class="bond-number" style="color:{color};">{value}</span>'
    if display_type == 1:
        return (
            '<span class="bond-indicator bond-indicator-number">'
            f'{number}</span>'
        )

    segments = ''.join(
        f'<i class="bond-bar-segment" style="left:{cutoff}%"></i>'
        for cutoff in (20, 40, 60, 80)
    )
    overlay = (
        f'<span class="bond-number bond-number-overlay" aria-hidden="true">'
        f'{value}</span>'
        if display_type == 3 else ""
    )
    bar = (
        '<span class="bond-bar" '
        f'role="meter" aria-label="Bond {value}" aria-valuemin="0" '
        f'aria-valuemax="100" aria-valuenow="{value}" '
        f'style="--bond-value:{value}%;--bond-color:{color};">'
        '<span class="bond-bar-fill" aria-hidden="true"></span>'
        f'{segments}</span>'
    )
    mode = "both" if display_type == 3 else "bar"
    return (
        f'<span class="bond-indicator bond-indicator-{mode}">'
        f'{bar}{overlay}</span>'
    )


class Colors(enum.Enum):
    """Defines the colors used in the helper table.
    """
    ALERT = "red"
    WARNING = "orange"
    GOOD = "lightgreen"
    GREAT = "aqua"


class Cell():
    def __init__(self, value="", bold=False, color=None, background=None, percent=False, title="", style=LEGACY_CELL_STYLE):
        self.value = value
        self.bold = bold
        self.color = color
        self.percent = percent
        self.background = background
        self.style = style
        self.title = title

    def to_td(self):
        style = self.style
        if self.bold:
            style += "font-weight:bold;"
        if self.color:
            style += f"color:{self.color};"
        if self.background:
            style += f"background:{self.background};"
        if style:
            style = f" style=\"{style}\""
        
        title = self.title
        if title:
            title = title.replace('\n','')
            title = f" title=\"{title}\""
        return f"<td{style if style else ''}{title if title else ''}>{self.value}{'%' if self.percent else ''}</td>"


class Row():
    long_name = None
    short_name = None
    description = None
    settings = None
    cells = None

    dialog = None
    style = None
    modern_column_width = 44

    """Defines a row in the helper table.
    """
    def __init__(self):
        self.dialog = None
        self.style = None
        self.disabled = False

    def _generate_cells(self, command_info) -> list[Cell]:
        """Returns a list of cells for this row.
        """
        cells = [Cell(self.short_name)]

        for command in command_info:
            cells.append(Cell())
        
        return cells

    def get_cells(self, command_info) -> list[Cell]:
        """Returns the value of the row at the given column index.
        """
        return self._generate_cells(command_info)

    def get_modern_column_width(self):
        """Return the stable facility-column width needed by this row."""
        return self.modern_column_width

    def display_settings_dialog(self, parent):
        """Displays the settings dialog for this row.
        """
        settings_var = [self.settings]
        self.dialog = gui.UmaPresetSettingsDialog(parent, settings_var, window_title="Change row options")
        self.dialog.exec()
        self.dialog = None
        self.settings = settings_var[0]
    
    def to_tr(self, command_info):
        td = ''.join(cell.to_td() for cell in self.get_cells(command_info))
        return f"<tr{self.get_style()}>{td}</tr>"
    
    def get_style(self):
        if self.style:
            return f" style=\"{self.style}\""
        return ""
    
    def to_dict(self, row_types):
        return {
            "type": row_types(type(self)).name,
            "settings": self.settings.to_dict() if self.settings else {}
        }


class PresetSettings(se.NewSettings):
    _settings = {
        "progress_bar": se.Setting(
            "Show progress bar",
            "Displays the training run progress.",
            False,
            se.SettingType.BOOL,
        ),
        "energy_enabled": se.Setting(
            "Show energy",
            "Displays energy in the event helper.",
            True,
            se.SettingType.BOOL,
        ),
        "support_bonds": se.Setting(
            "Show support bonds",
            "Choose how to display support bonds.",
            3,
            se.SettingType.COMBOBOX,
            choices=["Off", "Number", "Bar", "Both"],
        ),
        "hide_support_bonds": se.Setting(
            "Auto-hide maxed supports",
            "When support bonds are enabled, automatically hide characters when they reach 100.",
            False,
            se.SettingType.BOOL,
        ),
        "displayed_value": se.Setting(
            "Displayed value(s) for stats",
            "Which value(s) to display for stats rows.",
            0,
            se.SettingType.COMBOBOX,
            choices=["Raw gained stats", "Overcap-compensated gained stats", "Both"],
        ),
        "skillpt_enabled": se.Setting(
            "Show skill points",
            "Displays skill points in the event helper.",
            False,
            se.SettingType.BOOL,
        ),
        "fans_enabled": se.Setting(
            "Show fans",
            "Displays fans in the event helper.",
            False,
            se.SettingType.BOOL,
        ),
        "schedule_enabled": se.Setting(
            "Show schedule countdown",
            "Displays the amount of turns until your next scheduled race. (If there is one.)",
            True,
            se.SettingType.BOOL,
        ),
        "scenario_specific_enabled": se.Setting(
            "Show scenario specific elements",
            "Show scenario specific elements in the event helper.",
            True,
            se.SettingType.BOOL,
        ),
    }


class Preset():
    name = None
    rows = None
    initialized_rows: list[Row] = None
    row_types = None

    gm_fragment_dict = util.get_gm_fragment_dict()
    gl_token_dict = util.get_gl_token_dict()

    def __init__(self, row_types):
        self.settings = PresetSettings()

        self.row_types = row_types
        if self.rows:
            self.initialized_rows = [row.value() for row in self.rows]
        else:
            self.initialized_rows = []

    def __iter__(self):
        return iter(self.initialized_rows)
    
    def __gt__(self, other):
        return self.name > other.name
    
    def __lt__(self, other):
        return self.name < other.name
    
    def __eq__(self, other):
        return self.name == other.name
    
    def display_settings_dialog(self, parent):
        settings_var = [self.settings]
        self.dialog = gui.UmaPresetSettingsDialog(parent, settings_var, window_title="Toggle elements")
        self.dialog.exec()
        self.dialog = None
        self.settings = settings_var[0]
    
    def generate_overlay(self, main_info, command_info):
        html_elements = []

        if self.settings.progress_bar.value:
            html_elements.append(self.generate_progress_bar(main_info))

        if self.settings.energy_enabled.value:
            html_elements.append(self.generate_energy(main_info))

        if self.settings.skillpt_enabled.value:
            html_elements.append(self.generate_skillpt(main_info))

        if self.settings.fans_enabled.value:
            html_elements.append(self.generate_fans(main_info))
        
        if self.settings.schedule_enabled.value:
            html_elements.append(self.generate_schedule(main_info))
        
        if self.settings.support_bonds.value:
            html_elements.append(self.generate_bonds(main_info, display_type=self.settings.support_bonds.value))

        if self.settings.scenario_specific_enabled.value:
            html_elements.append(self.generate_gm_table(main_info))
            html_elements.append(self.generate_gl_table(main_info))
            html_elements.append(self.generate_arc(main_info))
            html_elements.append(self.generate_uaf(main_info))
            html_elements.append(self.generate_gff(main_info))

        html_elements.append(self.generate_table(command_info, main_info))

        if self.settings.scenario_specific_enabled.value:
            # Put MANT after the table
            html_elements.append(self.generate_mant(main_info))

        # html_elements.append("""<button id="btn-skill-window" onclick="window.await_skill_window();">Open Skills Window</button>""")

        return ''.join(html_elements)

    def show_schedule_optimizer_button(self, main_info):
        return self.settings.scenario_specific_enabled.value and main_info['scenario_id'] == 4

    def generate_progress_bar(self, main_info):

        sections = constants.DEFAULT_TRAINING_SECTIONS

        if main_info['scenario_id'] == 6:
            sections = constants.DEFAULT_ARC_SECTIONS

        if main_info['scenario_id'] == 13:
            sections = constants.DEFAULT_DREAMS_SECTIONS

        tot_turns = sections[-1][0] - 1
        turn_len = 100. / tot_turns
        start_dist = 0.
        rects = []

        for i in range(len(sections)):
            if sections[i][2] == "END":
                break
            
            end_dist = (sections[i+1][0] - 1) * turn_len

            cur_rect = f"""<rect x="{start_dist}" y="0" width="{end_dist - start_dist}" height="2" fill="{sections[i][1]}" mask="url(#mask)"/>"""
            rects.append(cur_rect)

            start_dist = end_dist
        
        rects = ''.join(rects)

        dark_start = main_info['turn'] * turn_len
        dark_rect = f"""<rect x="{dark_start}" y="0" width="{100 - dark_start}" height="2" fill="rgba(0, 0, 0, 0.6)" mask="url(#mask)" />"""


        bar_svg = f"""
        <svg width="100" height="2" viewBox="0 0 100 2" style="width: 100%; max-width: 700px; height: auto;">
            <mask id="mask" x="0" y="0" width="100" height="2">
                <rect x="0" y="0" width="100" height="2" fill="black" />
                <rect x="0" y="0" width="100" height="2" fill="white" rx="1" ry="1" />
            </mask>
            {rects}
            {dark_rect}
        </svg>
        """

        bar_div = f"<div id=\"progress-bar-container\" style=\"width: 100%; padding: 0 1rem; display:flex; align-items: center; justify-content: center; gap: 0.5rem;\"><p style=\"white-space: nowrap; margin: 0;\">Progress: </p>{bar_svg}</div>"

        return bar_div
    
    def generate_energy(self, main_info):
        return f"<div id=\"energy\"><b>Energy:</b> {main_info['energy']}/{main_info['max_energy']}</div>"
    
    def generate_skillpt(self, main_info):
        return f"<div id=\"skill-pt\"><b>Skill Points:</b> {main_info['skillpt']:,}</div>"

    def generate_fans(self, main_info):
        return f"<div id=\"fans\"><b>Fans:</b> {main_info['fans']:,}</div>"
    
    def generate_table(self, command_info, main_info):
        if not command_info:
            return ""
        
        headers = [TABLE_HEADERS['fac']]
        if main_info['scenario_id'] == 7:
            headers = [f"""<th style="text-overflow: clip;white-space: nowrap;overflow: hidden;">{header}</th>""" for header in headers]

            # Use icons as headers
            for command_id in list(main_info['all_commands'].keys())[:5]:
                color_block_part = f"<div style=\"width: 100%;height: 100%;background-color: {constants.UAF_COLOR_DICT[str(command_id)[1]]};position: absolute;top: 0;left: 0;z-index: -1\"></div>"
                img_part = f"<img src=\"{util.get_uaf_sport_image_dict()[str(command_id)]}\" width=\"32\" height=\"32\" style=\"display:inline-block; width: auto; height: 1.5rem; margin-top: 1px;\"/>"
                text_part = f"<br>{TABLE_HEADERS[constants.COMMAND_ID_TO_KEY[command_id]]}"
                header = f"""<th style="position: relative; text-overflow: clip;white-space: nowrap;overflow: hidden; z-index: 0; font-size: 0.8rem; min-width:50px">{color_block_part}{img_part}{text_part}</th>"""
                headers.append(header)

        else:
            headers += [TABLE_HEADERS[command] for command in command_info]
            headers = [f"""<th style="text-overflow: clip;white-space: nowrap;overflow: hidden;">{header}</th>""" for header in headers]


        table_header = ''.join(headers)
        table = [f"<tr>{table_header}</tr>"]

        for row in self.initialized_rows:
            if not row.disabled:
                try:
                    table.append(row.to_tr(command_info))
                except KeyError as e:
                    logger.error(f"Error generating table row: {e}\n{traceback.format_exc()}")
                    continue

        thead = f"<thead>{table[0]}</thead>"
        tbody = f"<tbody>{''.join(table[1:])}</tbody>"
        return f"<table id=\"training-table\">{thead}{tbody}</table>"

    def generate_bonds(self, main_info, display_type):
        eval_dict = main_info['eval_dict']
        ids = []
        for key in eval_dict.keys():
            if self.settings.hide_support_bonds.value and eval_dict[key].starting_bond == 100:
                continue

            if key < 100:
                ids.append(key)
            elif key == 102 and main_info['scenario_id'] not in (6,):  # Filter out non-cards except Akikawa
                ids.append(key)
        
        if not ids:
            return ""
    
        ids = sorted(ids)

        partners = []

        for id in ids:
            partner = eval_dict[id]

            bond_color = ""
            for cutoff, color in constants.BOND_COLOR_DICT.items():
                if partner.starting_bond < cutoff:
                    break
                bond_color = color

            img = f"<img src=\"{partner.img}\" width=\"56\" height=\"56\" style=\"display:inline-block;\"/>"
            hint_icon = """<div style="position:absolute;right:0px;width:20px;height:20px;border-radius:50%;background:linear-gradient(135deg,#ff6b8f,#ff3b6f);color:#fff;font-weight:700;font-size:14px;line-height:20px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,.3);z-index:10;pointer-events:none;transform: rotate(20deg);">!</div>"""
            bond_ele = ""
            if display_type in (2, 3):
                # Bars
                bond_ele += f"""
<div style="width: 100%;height: 0.75rem;position: relative;background-color: #4A494B;border-radius: 0.5rem;">
    <div style="position: absolute;width:calc(100% - 4px);height:calc(100% - 4px);top:2px;left:50%;transform: translateX(-50%);">
        <div style="position: absolute;width:100%;height:100%;background-color:#6E6B79;border-radius: 1rem;"></div>
        <div style="position: absolute;width:{partner.starting_bond}%;height:100%;background-color:{bond_color};"></div>
        <div style="position: absolute;width:2px;height:100%;background-color:#4A494B;top:0px;left:20%;transform: translateX(-50%);"></div>
        <div style="position: absolute;width:2px;height:100%;background-color:#4A494B;top:0px;left:40%;transform: translateX(-50%);"></div>
        <div style="position: absolute;width:2px;height:100%;background-color:#4A494B;top:0px;left:60%;transform: translateX(-50%);"></div>
        <div style="position: absolute;width:2px;height:100%;background-color:#4A494B;top:0px;left:80%;transform: translateX(-50%);"></div>
        <div style="position: absolute;width:100%;height:100%;border: 2px solid #4A494B;box-sizing: content-box;left: -2px;top: -2px;border-radius: 1rem;"></div>
    </div>
</div>""".replace("\n", "").replace("    ", "")
            if display_type in (1, 3):
                # Numbers
                bond_ele += f"<p style=\"margin:0;padding:0;color:{bond_color};font-weight:bold;\">{partner.starting_bond}</p>"
            
            ele = f"<div style=\"position:relative;display:flex;flex-direction:column;align-items:center;gap:0.2rem;\">{img}{hint_icon if id in main_info['hint_partners'] else ''}{bond_ele}</div>"
            partners.append(ele)
        
        inner = ''.join(partners)

        return f"<div id=\"support-bonds\" style=\"max-width: 100vw; display: flex; flex-direction: row; flex-wrap: nowrap; overflow-x: auto; gap:0.3rem; scrollbar-width: none;\">{inner}</div>"

    def generate_modern_overlay(self, main_info, command_info):
        """Render the packet-derived sidecar without exposing raw packet data."""
        facility_info = {
            key: value
            for key, value in command_info.items()
            if key in TABLE_HEADERS and key != "fac"
        }
        if not facility_info:
            return ""

        facility_count = len(facility_info)
        modern_column_width = Row.modern_column_width

        row_cells = []
        for row in self.initialized_rows:
            if row.disabled:
                continue
            if type(row).__name__ == "CurrentStatsRow":
                continue
            try:
                cells = row.get_cells(facility_info)
            except Exception as exc:
                logger.error(f"Error generating modern helper row {type(row).__name__}: {exc}")
                continue
            if not cells:
                continue
            width_getter = getattr(row, "get_modern_column_width", None)
            width_hint = (
                width_getter()
                if callable(width_getter)
                else getattr(row, "modern_column_width", Row.modern_column_width)
            )
            if isinstance(width_hint, (int, float)) and not isinstance(width_hint, bool):
                modern_column_width = max(
                    modern_column_width,
                    max(Row.modern_column_width, min(120, int(width_hint))),
                )
            label = str(cells[0].value or row.short_name or "")
            values = list(cells[1:])
            if len(values) < len(facility_info):
                values.extend(Cell() for _ in range(len(facility_info) - len(values)))
            row_cells.append((
                label,
                cells[0].title or row.description or "",
                values,
            ))

        modern_grid_width = 78 + (modern_column_width * facility_count)

        display_type = self.settings.support_bonds.value
        rainbow_glow_source = (
            f"{TRAINING_HELPER_ASSET_URL}/rainbow_training_ring.png"
        )
        rainbow_facility_glow_source = (
            f"{TRAINING_HELPER_ASSET_URL}/rainbow_facility_glow.svg"
        )
        facility_headers = []
        facility_partner_cells = []
        has_visible_partners = False
        current_stats = main_info.get("stats", {})
        for facility, info in facility_info.items():
            partners = info.get("partners", [])
            has_extreme = bool(info.get("extreme_spirit_burst_partner_count")) or any(
                partner.get("extreme_burst") for partner in partners
            )
            has_spirit = bool(info.get("spirit_burst_partner_count")) or any(
                partner.get("spirit_burst") for partner in partners
            )
            has_rainbow = any(partner.get("rainbow") for partner in partners)
            if has_extreme:
                state = "extreme"
                state_description = "Extreme Burst available"
            elif has_spirit:
                state = "spirit"
                state_description = "Spirit Burst available"
            elif has_rainbow:
                state = "rainbow"
                state_description = "Rainbow training available"
            else:
                state = "normal"
                state_description = "No special training state"
            state_classes = [f"state-{state}"]
            if has_rainbow and state != "rainbow":
                state_classes.append("state-rainbow")
            facility_state_classes = " ".join(state_classes)

            partner_html = []
            for partner in partners:
                statuses = []
                for key, label in (
                    ("rainbow", "Rainbow"),
                    ("unity", "Unity"),
                    ("near_ready", "Near Ready"),
                    ("spirit_burst", "Spirit Burst"),
                    ("extreme_burst", "Extreme Burst"),
                ):
                    if partner.get(key):
                        statuses.append(label)
                scenario_status = next(
                    (
                        key for key in ("extreme_burst", "spirit_burst")
                        if partner.get(key)
                    ),
                    None,
                )

                name = html_lib.escape(str(partner.get("name") or "Unknown"), quote=True)
                image = html_lib.escape(str(partner.get("img") or ""), quote=True)
                bond = partner.get("bond")
                hide_maxed_bond = (
                    self.settings.hide_support_bonds.value
                    and bond is not None
                    and int(bond) >= 100
                )
                details = [name]
                if partner.get("show_bond") and bond is not None and not hide_maxed_bond:
                    details.append(f"Bond {int(bond)}")
                details.extend(statuses)
                accessible_details = html_lib.escape(", ".join(details), quote=True)
                rainbow_glow = (
                    '<span class="rainbow-portrait-glow" aria-hidden="true">'
                    f'<img class="rainbow-portrait-glow-outer" src="{rainbow_glow_source}" alt="">'
                    f'<img class="rainbow-portrait-glow-inner" src="{rainbow_glow_source}" alt="">'
                    '</span>'
                    if partner.get("rainbow") and rainbow_glow_source else ""
                )
                hint = render_modern_hint_marker() if partner.get("hint") else ""
                scenario_marker = render_modern_scenario_marker(scenario_status)
                unity_charge_html = render_modern_unity_charge(
                    partner.get("unity_charge")
                )
                bond_html = ""
                if partner.get("show_bond") and not hide_maxed_bond:
                    bond_html = render_modern_bond_indicator(bond, display_type)
                bond_overlay_html = (
                    f'<span class="modern-partner-bond">{bond_html}</span>'
                    if bond_html else ""
                )
                hint_details = partner.get("hint_details") or {
                    "title": partner.get("name") or "Unknown",
                    "message": "Hint details are unavailable.",
                    "skills": [],
                }
                hint_details_attr = html_lib.escape(
                    json.dumps(hint_details, ensure_ascii=False, separators=(",", ":")),
                    quote=True,
                )
                if partner.get("hint"):
                    opening_tag = (
                        '<button type="button" class="modern-partner modern-hint-partner" '
                        f'data-hint-details="{hint_details_attr}" '
                        f'aria-label="{accessible_details}. Show possible skill hints" '
                        f'title="{accessible_details}" aria-haspopup="dialog">'
                    )
                    closing_tag = "</button>"
                else:
                    opening_tag = (
                        '<div class="modern-partner" '
                        f'aria-label="{accessible_details}" title="{accessible_details}">'
                    )
                    closing_tag = "</div>"
                partner_html.append(
                    opening_tag
                    + '<span class="portrait-frame">'
                    + f'{rainbow_glow}<img src="{image}" alt="" loading="lazy">{hint}{scenario_marker}'
                    + f'{unity_charge_html}'
                    + f'{bond_overlay_html}</span>'
                    + closing_tag
                )
            has_visible_partners = has_visible_partners or bool(partner_html)

            facility_name = TABLE_HEADERS.get(facility, facility.title())
            facility_label = html_lib.escape(
                f"{facility_name} training. {state_description}.", quote=True
            )
            icon_filename = MODERN_FACILITY_ICON_FILES.get(facility)
            icon = (
                f"{TRAINING_HELPER_ASSET_URL}/{icon_filename}"
                if icon_filename else ""
            )
            icon_html = (
                f'<img class="facility-icon" src="{icon}" alt="">'
                if icon else (
                    '<span class="modern-facility-name" aria-hidden="true">'
                    f'{html_lib.escape(facility_name)}</span>'
                )
            )
            facility_rainbow_glow = (
                '<span class="rainbow-facility-glow" aria-hidden="true">'
                f'<img src="{rainbow_facility_glow_source}" alt=""></span>'
                if has_rainbow else ""
            )
            current_stat_html = ""
            if facility in current_stats:
                current_stat = html_lib.escape(str(current_stats[facility]))
                stat_value = html_lib.escape(
                    str(current_stats[facility]), quote=True
                )
                current_stat_html = (
                    '<span class="modern-facility-stat" '
                    f'aria-label="Current stat {current_stat}">'
                    f'<span class="modern-stat-value" data-stat-key="{facility}" '
                    f'data-stat-value="{stat_value}">{current_stat}</span></span>'
                )
            facility_headers.append(
                f'<section class="modern-facility {facility_state_classes}" data-facility="{facility}" '
                f'role="columnheader" aria-label="{facility_label}" '
                f'title="{facility_label}">'
                f'<div class="facility-icon-shell">{facility_rainbow_glow}{icon_html}</div>'
                f'{current_stat_html}'
                '</section>'
            )
            partner_label = html_lib.escape(f"{facility_name} partners", quote=True)
            facility_partner_cells.append(
                f'<div class="modern-partners" data-facility="{facility}" role="cell" '
                f'aria-label="{partner_label}">{"".join(partner_html)}</div>'
            )

        partner_strip_html = ""
        if has_visible_partners:
            partner_strip_html = (
                '<section class="modern-partner-strip" aria-label="Training partners">'
                '<div class="modern-partner-spacer" aria-hidden="true"></div>'
                f'{"".join(facility_partner_cells)}</section>'
            )

        g1_race_items = []
        g1_race_names = []
        for race in main_info.get("g1_races") or ():
            if not isinstance(race, dict):
                continue
            name = str(race.get("name") or "G1 race")
            thumb_url = str(race.get("thumb_url") or "")
            if not thumb_url:
                continue
            escaped_name = html_lib.escape(name, quote=True)
            escaped_url = html_lib.escape(thumb_url, quote=True)
            g1_race_names.append(name)
            g1_race_items.append(
                '<span class="modern-g1-race" title="'
                f'{escaped_name}"><img src="{escaped_url}" alt="{escaped_name}" '
                'width="68" height="34" loading="lazy" decoding="async"></span>'
            )

        g1_panel_html = ""
        if g1_race_items:
            panel_label = html_lib.escape(
                "Races: " + ", ".join(g1_race_names), quote=True
            )
            g1_panel_html = (
                f'<section class="modern-g1-panel" aria-label="{panel_label}">'
                '<span class="modern-g1-label" aria-hidden="true">Races</span>'
                '<div class="modern-g1-races">'
                f'{"".join(g1_race_items)}</div></section>'
            )

        gl_panel_html = (
            self.generate_modern_gl_panel(main_info)
            if self.settings.scenario_specific_enabled.value else ""
        )
        lower_side_html = ""
        if g1_panel_html or gl_panel_html:
            lower_side_html = (
                '<aside class="modern-lower-side" '
                'aria-label="Current training context">'
                f'{g1_panel_html}{gl_panel_html}</aside>'
            )

        lower_stage_html = ""
        if lower_side_html or partner_strip_html:
            lower_stage_html = (
                '<div class="modern-lower-stage">'
                f'{lower_side_html}{partner_strip_html}</div>'
            )

        metric_rows = []
        for label, description, values in row_cells:
            row_label = label.replace("<br>", " ").replace("<br/>", " ")
            title_attr = html_lib.escape(
                str(description or row_label).replace("\n", " "), quote=True
            )
            value_cells = []
            rich_row = False
            multiline_row = False
            for facility_index, facility in enumerate(facility_info):
                cell = values[facility_index] if facility_index < len(values) else Cell()
                style = (
                    "" if cell.style == LEGACY_CELL_STYLE else cell.style or ""
                )
                if cell.bold:
                    style += "font-weight:bold;"
                if cell.color:
                    style += f"color:{cell.color};"
                if cell.background:
                    style += f"background:{cell.background};"
                style_attr = (
                    f' style="{html_lib.escape(style, quote=True)}"'
                    if style else ""
                )
                value = "" if cell.value is None else str(cell.value)
                suffix = "%" if cell.percent else ""
                rich_row = rich_row or bool(
                    re.search(r"<(?:br|div|img|span)\b", value, re.IGNORECASE)
                )
                multiline_row = multiline_row or bool(
                    re.search(r"<br\s*/?>", value, re.IGNORECASE)
                )
                value_cells.append(
                    f'<div class="modern-row-value" data-facility="{facility}" '
                    f'role="cell"{style_attr}>'
                    f'{value}{suffix}</div>'
                )
            row_classes = []
            if rich_row:
                row_classes.append("modern-rich-row")
            if multiline_row:
                row_classes.append("modern-multiline-row")
            rich_class = (
                " " + " ".join(row_classes)
                if row_classes else ""
            )
            metric_rows.append(
                f'<div class="modern-training-row{rich_class}" role="row">'
                f'<div class="modern-row-label" role="rowheader" title="{title_attr}">{label}</div>'
                f'{"".join(value_cells)}'
                '</div>'
            )

        raw_scenario_name = str(main_info.get("scenario_name") or "")
        if main_info.get("scenario_id") == 2 or raw_scenario_name == "Aoharu Cup":
            raw_scenario_name = "Unity Cup"
        scenario_name = html_lib.escape(raw_scenario_name)
        raw_motivation = str(main_info.get("motivation") or "-")
        raw_motivation = {
            "Very Low": "Awful",
            "Low": "Bad",
            "High": "Good",
            "Very High": "Great",
        }.get(raw_motivation, raw_motivation)
        mood_html = render_modern_mood(raw_motivation)
        energy = main_info.get("energy", 0)
        max_energy = main_info.get("max_energy", 0)
        energy_value = html_lib.escape(str(energy), quote=True)
        try:
            energy_percent = max(0, min(100, float(energy) / float(max_energy) * 100))
        except (TypeError, ValueError, ZeroDivisionError):
            energy_percent = 0
        energy_text = html_lib.escape(f"{energy}/{max_energy}")

        # SP and Fans are core trainee context in Modern. The shared preset
        # switches continue to control Legacy's optional standalone blocks.
        skillpt = main_info.get("skillpt", 0)
        skillpt_value = html_lib.escape(str(skillpt), quote=True)
        fans = main_info.get("fans", 0)
        fans_value = html_lib.escape(str(fans), quote=True)
        vitals_html = (
            '<div class="modern-vitals">'
            '<span class="modern-vital"><small>SP</small>'
            '<strong class="modern-stat-value" data-stat-key="skillpt" '
            f'data-stat-value="{skillpt_value}">{skillpt:,}</strong></span>'
            '<span class="modern-vital"><small>Fans</small>'
            '<strong class="modern-stat-value" data-stat-key="fans" '
            f'data-stat-value="{fans_value}">{fans:,}</strong></span>'
            '</div>'
        )

        energy_bar_html = ""
        energy_row_class = "modern-energy-row modern-energy-row-mood-only"
        if self.settings.energy_enabled.value:
            energy_row_class = "modern-energy-row"
            energy_bar_html = (
                '<span class="modern-vital-energy">'
                '<span class="modern-energy-bar" role="progressbar" '
                'aria-label="Energy" aria-valuemin="0" '
                f'aria-valuemax="{html_lib.escape(str(max_energy), quote=True)}" '
                f'aria-valuenow="{html_lib.escape(str(energy), quote=True)}">'
                f'<span class="modern-energy-fill" style="width:{energy_percent:.2f}%"></span>'
                '<span class="modern-energy-text">'
                '<span class="modern-energy-value" data-stat-key="energy" '
                f'data-stat-value="{energy_value}">{energy_text}</span></span>'
                '</span></span>'
            )
        energy_row_html = (
            f'<div class="{energy_row_class}">{energy_bar_html}'
            f'<span class="modern-vital-mood">{mood_html}</span></div>'
        )

        context_panels = []
        if self.settings.progress_bar.value:
            context_panels.append(self.generate_progress_bar(main_info))
        if self.settings.schedule_enabled.value:
            context_panels.append(self.generate_schedule(main_info))
        mant_panel = ""
        if self.settings.scenario_specific_enabled.value:
            context_panels.extend((
                self.generate_gm_table(main_info),
                self.generate_arc(main_info),
                self.generate_uaf(main_info),
                self.generate_gff(main_info),
            ))
            mant_panel = self.generate_mant(main_info)
        context_html = "".join(panel for panel in context_panels if panel)
        context_panel_html = (
            '<section class="modern-context-panels" '
            f'aria-label="Training context">{context_html}</section>'
            if context_html else ""
        )
        mant_panel_html = (
            '<section class="modern-context-panels modern-context-after" '
            f'aria-label="Scenario details">{mant_panel}</section>'
            if mant_panel else ""
        )

        stat_context = html_lib.escape(
            f'{main_info.get("scenario_id", "")}:{main_info.get("trainee_name", "")}',
            quote=True,
        )
        turn_value = html_lib.escape(str(main_info.get("turn", 0)), quote=True)

        return (
            f'<div id="modern-training-helper" style="--mth-column-count:{facility_count};'
            f'--mth-facility-column:{modern_column_width}px;'
            f'--mth-grid-width:{modern_grid_width}px" data-stat-context="{stat_context}" '
            f'data-turn="{turn_value}">'
            '<header class="modern-header">'
            '<div class="modern-header-main">'
            '<div class="modern-run-meta">'
            f'<strong class="modern-run-scenario" title="{html_lib.escape(raw_scenario_name, quote=True)}">'
            f'{scenario_name}</strong>'
            f'<span class="modern-run-turn">&middot; Turn {main_info.get("turn", 0)}</span>'
            '</div>'
            f'{vitals_html}'
            '</div>'
            f'{energy_row_html}'
            '</header>'
            f'{context_panel_html}'
            '<main class="modern-facilities" role="table" aria-label="Training comparison">'
            '<div class="modern-grid-header" role="row">'
            '<div class="modern-grid-corner" role="columnheader" aria-label="Metric"></div>'
            f'{"".join(facility_headers)}</div>'
            '<div class="modern-table-shell" role="rowgroup">'
            f'{"".join(metric_rows)}</div>'
            '</main>'
            f'{lower_stage_html}'
            f'{mant_panel_html}'
            '</div>'
        )

    def generate_gm_table(self, main_info):
        if main_info['scenario_id'] != 5:
            return ""
        
        header = "<tr><th colspan=\"8\">Fragments</th></tr>"
    
        frag_tds = []
        for index, fragment_id in enumerate(main_info['gm_fragments']):
            frag_tds.append(f"<td style=\"{'outline: 1px solid red; outline-offset: -1px;' if index in (0, 4) else ''}\"><img src=\"{self.gm_fragment_dict[fragment_id]}\" height=\"32\" width=\"30\" style=\"display:block; margin: auto; width: auto; height: 32px;\" /></td>")
        
        frag_tr = f"<tr>{''.join(frag_tds)}</tr>"

        return f"<table id=\"gm-fragments\"><thead>{header}</thead><tbody>{frag_tr}</tbody></table>"

    def generate_gl_table(self, main_info):
        if main_info['scenario_id'] != 3:
            return ""
        
        top_row = []
        bottom_row = []

        for token_type in constants.GL_TOKEN_LIST:
            top_row.append(f"<th><img src=\"{self.gl_token_dict[token_type]}\" height=\"32\" width=\"31\" style=\"display:block; margin: auto; width: auto; height: 32px;\" /></th>")
            bottom_row.append(f"<td>{main_info['gl_stats'][token_type]}</td>")
        
        top_row = f"<tr>{''.join(top_row)}</tr>"
        bottom_row = f"<tr>{''.join(bottom_row)}</tr>"

        return f"<table id=\"gl-tokens\"><thead>{top_row}</thead><tbody>{bottom_row}</tbody></table>"

    def generate_modern_gl_panel(self, main_info):
        if main_info.get("scenario_id") != 3:
            return ""

        gl_stats = main_info.get("gl_stats") or {}
        token_rows = []
        for token_type in constants.GL_TOKEN_LIST:
            label = token_type.title()
            raw_value = gl_stats.get(token_type, 0)
            try:
                numeric_value = int(raw_value)
                value = f"{numeric_value:,}"
                stat_attributes = (
                    f' data-stat-key="gl-{html_lib.escape(token_type, quote=True)}"'
                    f' data-stat-value="{numeric_value}"'
                )
            except (TypeError, ValueError):
                value = str(raw_value)
                stat_attributes = ""
            escaped_label = html_lib.escape(label, quote=True)
            escaped_value = html_lib.escape(value)
            icon_source = html_lib.escape(
                str(self.gl_token_dict.get(token_type) or ""),
                quote=True,
            )
            icon_html = (
                f'<img src="{icon_source}" alt="" width="22" height="22">'
                if icon_source else (
                    '<span class="modern-gl-token-fallback" aria-hidden="true">'
                    f'{html_lib.escape(label[:1])}</span>'
                )
            )
            token_rows.append(
                '<span class="modern-gl-token" '
                f'aria-label="{escaped_label} {escaped_value}">'
                f'{icon_html}<strong class="modern-stat-value"'
                f'{stat_attributes}>{escaped_value}</strong></span>'
            )

        return (
            '<section class="modern-gl-panel" '
            'aria-label="Grand Live performance">'
            '<div class="modern-gl-tokens">'
            f'{"".join(token_rows)}</div></section>'
        )
    
    def generate_schedule(self, main_info):
        cur_turn = main_info['turn']
        next_race = None
        for race in main_info['scheduled_races']:
            if race['turn'] >= cur_turn:
                next_race = race
                break
        
        if not next_race:
            return ""
        
        turns_left = next_race['turn'] - cur_turn
        text = f"<p><b>{turns_left} turn{'' if turns_left == 1 else 's'}</b> until</p>"
        img = f"<img width=100 height=50 src=\"{next_race['thumb_url']}\"/>"

        fan_warning = ""

        if main_info['fans'] < next_race['fans']:
            fans_needed = next_race['fans'] - main_info['fans']
            fan_warning = f"""<p style="color: orange; margin: 0;"><b>{fans_needed} more</b> fans needed!</p>"""

        return f"""<div id="schedule" style="display: flex; flex-direction: column; justify-content: center; align-items: center;"><div id="schedule-race-container" style="display: flex; align-items: center; gap: 0.5rem;">{text}{img}</div>{fan_warning}</div>"""

    def generate_arc(self, main_info):
        if main_info['scenario_id'] != 6 or main_info['turn'] < 3:
            return ""

        gauge_str = str(main_info['arc_expectation_gauge'] // 10)
        gauge_str2 = str(main_info['arc_expectation_gauge'] % 10)
        return f"<div id=\"arc\"><b>Aptitude Points:</b> {main_info['arc_aptitude_points']:,} - <b>Supporter Points:</b> {main_info['arc_supporter_points']:,} - <b>Expectation Gauge:</b> {gauge_str}.{gauge_str2}%</div>"

    def generate_uaf(self, main_info):
        if main_info['scenario_id'] != 7:
            return ""
        
        required_rank_to_effect = {
            -1: 17, # Janky hack for UAF end
            0: 0,
            10: 0,
            20: 3,
            30: 7,
            40: 12,
            50: 17,
        }
        
        uaf_sport_rank = main_info['uaf_sport_ranks']
        uaf_sport_rank_total = main_info['uaf_sport_rank_total']
        uaf_current_required_rank = main_info['uaf_current_required_rank']
        uaf_current_active_effects = main_info['uaf_current_active_effects']
        uaf_current_active_bonus = main_info['uaf_current_active_bonus']
        uaf_sport_competition = main_info['uaf_sport_competition']
        uaf_consultations_left = main_info['uaf_consultations_left']

        html_output = "<div id='uaf'><div style='display:flex; flex-flow: row; justify-content:center; gap: 0.5rem;'>"

        flex_divs = []
        
        if uaf_current_required_rank >= 0:
            flex_divs.append(f"""<b>Training Target:</b> {uaf_current_required_rank}""")
        flex_divs.append(f"""<b>Total Bonus:</b> {uaf_current_active_bonus}%""")
        flex_divs.append(f"""<b>Wins:</b> {uaf_sport_competition}""")
        flex_divs.append(f"""<b>Calls left:</b> {uaf_consultations_left}""")
        
        flex_divs = [f"""<p style="margin: 0 0 0.1rem 0">{div}</p>""" for div in flex_divs]
        html_output += ''.join(flex_divs)
        html_output += "</div>"
            
        html_output += "<table style='margin-left: 52px;'><thead><tr><th style='position: relative; text-overflow: clip;white-space: nowrap;overflow: hidden; z-index: 0; font-size: 0.8rem; min-width:101px'>Genres</th>"
        
        for command_id in list(main_info['all_commands'].keys())[:5]:
            text_part = f"{TABLE_HEADERS[constants.COMMAND_ID_TO_KEY[command_id]]}"
            header = f"""<th style="position: relative; text-overflow: clip;white-space: nowrap;overflow: hidden; z-index: 0; font-size: 0.8rem; min-width:50px">{text_part}</th>"""
            html_output += header
            
        html_output += "<th style='position: relative; text-overflow: clip;white-space: nowrap;overflow: hidden; z-index: 0; font-size: 0.8rem;'>Bonus</th></tr></thead><tbody>"

        # Loop through the IDs
        for base in [2100, 2200, 2300]:
            total_row = 0
            row = f"""<tr><td><div style="display: flex; align-items: center; justify-content: center; flex-direction: row; gap: 5px"><img src=\"{util.get_uaf_genre_image_dict()[str(base)]}\" width=\"32\" height=\"32\" style=\"display:inline-block; width: auto; height: 1.5rem; margin-top: 1px;\"/><div>{uaf_sport_rank_total[base]}</div></div></td>"""
            for i in range(1, 6):
                id = base + i
                if id in uaf_sport_rank:
                    rank = uaf_sport_rank.get(id, 0)
                    total_row += rank
                    
                    # Determine the color based on the rank
                    if rank >= uaf_current_required_rank+10:
                        style = f"color:{Colors.GREAT.value}; font-weight:600;"
                    elif rank >= uaf_current_required_rank:
                        style = f"color:{Colors.GOOD.value}; font-weight:600;"
                    elif abs(uaf_current_required_rank - rank) <= 2:
                        style = f"color:{Colors.WARNING.value}; font-weight:600;"
                    else:
                        style = f"color:{Colors.ALERT.value}; font-weight:600;"
                    
                    # Color bg if the sport is available
                    if id in main_info['all_commands']:
                        # 🤮
                        bg_color = constants.UAF_COLOR_DICT[str(id)[1]]
                        bg_color = bg_color.split(",")
                        bg_color[-1] = "0.2)"
                        bg_color = ",".join(bg_color)
                        style += f"background-color: {bg_color};"
                    
                    row += f"""<td style='{style}'>{rank}</td>"""

            current_effect_value = uaf_current_active_effects.get(str(base)[1], 0)
            expected_effect_value = required_rank_to_effect.get(uaf_current_required_rank, 0)

            # Determine the color for the effect value
            if current_effect_value == expected_effect_value:
                effect_style = f"color:{Colors.GOOD.value}; font-weight:600;" 
            else:
                effect_style = f"color:{Colors.WARNING.value};"

            row += f"<td style='{effect_style}'>{current_effect_value}%</td>"
            html_output += row

        html_output += "</tbody></table></div>"

        return html_output

    def generate_gff(self, main_info):
        # Great Food Festival
        if main_info['scenario_id'] != 8:
            return ""
    
        internal = ""

        # Great success chance.
        if main_info['gff_great_success'] is not None:
            style_text = " style=\"color:orange\""
            if main_info['gff_great_success'] == 100:
                style_text = " style=\"color:lightgreen\""
            internal += f"<div><b>Great Success chance: <span{style_text}>{main_info['gff_great_success']}%</span></b> ({main_info['gff_success_point']}/1500)</div>"

        cooking_point = main_info['gff_cooking_point']
        tasting_thres = main_info['gff_tasting_thres']
        tasting_great_thres = main_info['gff_tasting_great_thres']

        def pts_left_text(pts, thres):
            txt = ""
            pts_left = thres - pts
            if pts_left <= 0:
                txt = "<span style=\"color:lightgreen\">Reached!</span>"
            else:
                txt = f"<span style=\"color:orange\">{pts_left} left</span>"
            return txt

        tasting_text = "Great Satisfaction: " + pts_left_text(cooking_point, tasting_thres)
        if tasting_great_thres:
            tasting_text += " — Super: " + pts_left_text(cooking_point, tasting_great_thres)
        internal += f"<div><b>{tasting_text}</b></div>"

        
        # Vegetable table
        header_lines = []
        data_lines = []
        for veg_dict in main_info['gff_vegetables'].values():
            veg_img = f"""<img src="{util.get_gff_veg_image_dict()[veg_dict['img']]}" width="32" height="32" style="display:inline-block; width: auto; height: 1.25rem; margin: 0;"/>"""
            veg_cur_count = veg_dict['count']
            veg_max_count = veg_dict['max']
            veg_harvest_count = veg_dict['harvest']
            veg_level = veg_dict['level']

            header_lines.append(f"<th style=\"position:relative;\">{veg_img}<div style=\"font-size:0.75rem;font-weight:normal;position:absolute;top:-2px;right:2px;\">Lv{veg_level}</div></th>")
            data_lines.append(f"<td><p style=\"margin:0;\"><b>{veg_cur_count}</b>/{veg_max_count}</p><p style=\"margin:0;color:lightgreen;\">+{veg_harvest_count}</p></td>")
        
        # Field points
        fp_cur = main_info['gff_field_point'][0]
        fp_gain = main_info['gff_field_point'][1]
        header_lines.append(f"""<th><img src="{util.get_gff_veg_image_dict()['pt']}" width="32" height="32" style="display:inline-block; width: auto; height: 1.25rem; margin: 0;"/></th>""")
        data_lines.append(f"<td><p style=\"margin:0;\"><b>{fp_cur}</b></p><p style=\"margin:0;color:lightgreen;\">+{fp_gain}</p></td>")
        
        header = f"<tr>{''.join(header_lines)}</tr>"
        data = f"<tr>{''.join(data_lines)}</tr>"
        internal += f"<table id='gff-veg'><thead>{header}</thead><tbody>{data}</tbody></table>"

        
        return f"<div id='gff' style='display:flex; flex-flow: column; justify-content:center; align-items:center; gap: 0.5rem;'>{internal}</div>"

    def generate_mant(self, main_info):
        if main_info['scenario_id'] != 4:
            return ""

        shop_div = self.generate_mant_shop_div(main_info)
        races_div = self.generate_mant_races_div(main_info)

        html_text = "<div style=\"display:flex;flex-direction:column;align-items:center;gap:0.5rem;width:100%;\">"
        html_text += shop_div
        html_text += races_div
        html_text += "</div>"
        return html_text

    def generate_mant_shop_div(self, main_info):
        if main_info['turn'] <= 12:
            # Ignore anything before the debut
            return ""
        if len(main_info['pick_up_item_info_array']) == 0:
            return ""

        mant_imgs = util.get_mant_image_dict()
        items_html = ""
        inv_html = ""

        # Build inventory lookup: item_id -> count owned
        inventory = {inv['item_id']: inv['num'] for inv in main_info.get('user_item_info_array', [])}
        num_hammers = inventory.get(11001, 0) + inventory.get(11002, 0)
        for item in main_info['pick_up_item_info_array']:
            if item['item_buy_num'] == item['limit_buy_count']:
                continue
            if item['item_id'] in (11001, 11002):
                num_hammers += 1

        races_left = 999
        if main_info['turn'] >= 77:
            races_left = 1
        elif main_info['turn'] >= 75:
            races_left = 2
        elif main_info['turn'] >= 73:
            races_left = 3

        # Inventory
        for item in main_info.get('user_item_info_array', []):
            item_id = item['item_id']
            icon_src = mant_imgs.get(f'scenario_free_item_icon_{item_id:05}', '')
            description = constants.MANT_ITEM_ID_TO_DESCRIPTION.get(item_id, '')
            modifier = constants.MANT_ITEM_ID_TO_MODIFIER.get(item_id, '')

            # Modifier label for shared-icon items (megaphones, cleat hammers)
            modifier_label = ""
            if modifier:
                modifier_label = (
                    f'<div style="position:absolute;bottom:10px;left:50%;transform:translateX(-50%);'
                    f'background:rgba(0,0,0,0.75);color:#ffd700;font-size:0.5rem;font-weight:700;'
                    f'padding:0 2px;border-radius:3px;white-space:nowrap;z-index:2;line-height:1.2;">'
                    f'{modifier}</div>'
                )

            # Owned count badge
            owned_count = inventory.get(item_id, 0)
            hammer_highlight_style = ""
            if num_hammers >= races_left and item_id in (11001, 11002):
                hammer_highlight_style = "box-shadow: 0px 0px 10px 7px rgba(255,0,0,0.65); border-radius:4px;"
            owned_badge = (
                f'<div style="position:absolute;bottom:-2px;left:50%;transform:translateX(-50%);'
                f'background:rgba(0,0,0,0.7);color:#7bed9f;font-size:0.55rem;font-weight:700;'
                f'padding:0 3px;border-radius:4px;white-space:nowrap;z-index:2;line-height:1.2;">'
                f'x{owned_count}</div>'
            )

            inv_html += (
                f'<div title="{description}" style="position:relative;flex:0 0 auto;'
                f'width:36px;height:36px;cursor:default;">'
                f'<img src="{icon_src}" width="32" height="32" '
                f'style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);{hammer_highlight_style}"/>'
                f'{modifier_label}{owned_badge}'
                f'</div>'
            )

        # Shop
        for item in reversed(main_info['pick_up_item_info_array']):
            if item['item_buy_num'] == item['limit_buy_count']:
                # Sold out
                continue
            item_id = item['item_id']
            icon_src = mant_imgs.get(f'scenario_free_item_icon_{item_id:05}', '')
            description = constants.MANT_ITEM_ID_TO_DESCRIPTION.get(item_id, '')
            modifier = constants.MANT_ITEM_ID_TO_MODIFIER.get(item_id, '')

            # Turns left badge
            turns_left = self._get_mant_turns_left(item, main_info['turn'])
            turn_color = '#e74c3c' if turns_left == 1 else '#e67e22' if turns_left <= 2 else '#888'
            turns_badge = (
                f'<div style="position:absolute;top:-2px;right:-2px;'
                f'background:{turn_color};color:#fff;font-size:0.6rem;font-weight:700;'
                f'min-width:14px;height:14px;line-height:14px;text-align:center;'
                f'border-radius:7px;padding:0 2px;z-index:2;">'
                f'{turns_left}</div>'
            )

            # Coin cost badge
            can_afford = main_info['coin_num'] >= item['coin_num']
            cost_color = '#ccc' if can_afford else '#e74c3c'
            coin_badge = (
                f'<div style="position:absolute;bottom:-2px;left:50%;transform:translateX(-50%);'
                f'background:rgba(0,0,0,0.7);color:{cost_color};font-size:0.55rem;font-weight:700;'
                f'padding:0 3px;border-radius:4px;white-space:nowrap;z-index:2;line-height:1.2;">'
                f'{item["coin_num"]}</div>'
            )

            # Modifier label for shared-icon items (megaphones, cleat hammers)
            modifier_label = ""
            if modifier:
                modifier_label = (
                    f'<div style="position:absolute;bottom:10px;left:50%;transform:translateX(-50%);'
                    f'background:rgba(0,0,0,0.75);color:#ffd700;font-size:0.5rem;font-weight:700;'
                    f'padding:0 2px;border-radius:3px;white-space:nowrap;z-index:2;line-height:1.2;">'
                    f'{modifier}</div>'
                )

            # Owned count badge
            owned_badge = ""
            owned_count = inventory.get(item_id, 0)
            if owned_count > 0:
                owned_badge = (
                    f'<div style="position:absolute;top:-2px;left:-2px;'
                    f'background:rgba(0,0,0,0.7);color:#7bed9f;font-size:0.55rem;font-weight:700;'
                    f'padding:0 3px;border-radius:4px;white-space:nowrap;z-index:2;line-height:1.2;">'
                    f'+{owned_count}</div>'
                )

            hammer_highlight_style = ""
            if num_hammers >= races_left and item_id in (11001, 11002):
                hammer_highlight_style = "box-shadow: 0px 0px 10px 7px rgba(255,0,0,0.65); border-radius:4px;"

            items_html += (
                f'<div title="{description}" style="position:relative;flex:0 0 auto;'
                f'width:36px;height:36px;cursor:default;">'
                f'<img src="{icon_src}" width="32" height="32" '
                f'style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);{hammer_highlight_style}"/>'
                f'{turns_badge}{coin_badge}{modifier_label}{owned_badge}'
                f'</div>'
            )

        # Coin badge at the end
        coin_src = mant_imgs.get('coin', '')
        coin_count = main_info['coin_num']
        sale_indicator = ""
        sale_val = main_info.get('sale_value', 0)
        if sale_val > 0:
            sale_indicator = (
                f'<div style="position:absolute;top:-4px;right:-6px;'
                'background:linear-gradient(135deg,#ff6b8f,#ff3b6f);'
                'color:#fff;font-weight:700;font-size:0.5rem;line-height:1;'
                'padding:2px 3px;border-radius:4px;'
                'box-shadow:0 1px 3px rgba(0,0,0,.3);z-index:10;'
                f'pointer-events:none;white-space:nowrap;">{sale_val}%</div>'
            )
        coin_badge_html = (
            f'<div style="position:relative;flex:0 0 auto;'
            f'width:36px;height:36px;cursor:default;">'
            f'<img src="{coin_src}" width="32" height="32" '
            f'style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);"/>'
            f'<div style="position:absolute;bottom:-2px;left:50%;transform:translateX(-50%);'
            f'background:rgba(0,0,0,0.7);color:#ffd700;font-size:0.6rem;font-weight:700;'
            f'padding:0 3px;border-radius:4px;white-space:nowrap;z-index:2;line-height:1.2;">'
            f'{coin_count}</div>'
            f'{sale_indicator}'
            f'</div>'
        )

        return (
            f'<div style="display:flex;flex-wrap:wrap;justify-content:center;'
            f'gap:0.4rem;width:100%;padding:0.2rem 0;">'
            f'{inv_html}</div>'
            f'<div style="display:flex;flex-wrap:wrap;justify-content:center;'
            f'gap:0.4rem;width:100%;padding:0.2rem 0;">'
            f'{items_html}{coin_badge_html}</div>'
        )

    def generate_mant_races_div(self, main_info):
        if main_info['turn'] <= 12 or main_info['turn'] >= 73:
            # Ignore anything before the debut and during Twinkle Start Climax
            return ""
        html_text = "<div style=\"width:100%;\">"
        races_div = ""
        mant_imgs = util.get_mant_image_dict()

        races_div += "<div><table style=\"width:100%;white-space:nowrap;\"><thead><tr><th>Grade</th><th>Name</th><th>Surface/Dist</th><th>Rival</th></tr></thead><tbody>"
        rival_program_ids = [race['program_id'] for race in main_info['rival_race_info_array']]
        for race in main_info['races']:
            program_id = race['program_id']
            race_grade = mdb.get_program_id_grade(program_id)

            if not race_grade:
                logger.error(f"Race grade not found for program id {program_id}")
            if race_grade == 800 or race_grade == 700 or race_grade == 400: # Debut/OP/Pre-OP, ignore
                continue
            race_img_url = "https://gametora.com/images/umamusume/race_ribbons/utx_txt_grade_ribbon_"
            if race_grade == 700:
                race_img_url += "06.png" # Pre-OP
            elif race_grade == 400:
                race_img_url += "02.png" # OP
            elif race_grade == 300:
                race_img_url += "03.png" # G3
            elif race_grade == 200:
                race_img_url += "04.png" # G2
            elif race_grade == 100:
                race_img_url += "05.png" # G1
            else:
                race_img_url += "07.png" # EX
            # race_img_url = self.get_thumb_url(program_id)
            races_div += "<tr>"
            races_div += f"<td><img src=\"{race_img_url}\" width=\"51\" height=\"18.5\" style=\"vertical-align:middle;\"/></td>"
            race_aptitudes = self._get_race_aptitudes(mdb.get_race_surface_dict()[program_id], mdb.get_race_distance_dict()[program_id], main_info['uma_aptitudes'])
            races_div += f"<td { 'style=\"font-weight:bold;\"' if race_aptitudes[0] and race_aptitudes[1] else ''}>{mdb.get_race_name_dict()[program_id]}</td>"
            races_div += f"<td>{self.get_race_details_text(mdb.get_race_surface_dict()[program_id], mdb.get_race_distance_dict()[program_id], main_info['uma_aptitudes'])}</td>"
            if program_id in rival_program_ids:
                races_div += f"<td><img src=\"{mant_imgs['rival']}\" width=\"24\" height=\"24\" style=\"vertical-align:middle;\"/></td>"
            else:
                races_div += f"<td></td>"
            races_div += "</tr>"
        races_div += "</tbody></table></div>"

        html_text += races_div
        html_text += "</div>"
        return html_text

    def _get_mant_turns_left(self, item, turn):
        turns_left = 0
        if item['limit_turn'] == 0:
            # Normal shop item, refreshes every six turns
            turns_left = 6 - ((turn - 13) % 6)
        else:
            turns_left = item['limit_turn'] - turn + 1 # limit_turn is the last turn it can be bought
        return turns_left

    def get_turns_left_text(self, item, turn):
        turns_left = self._get_mant_turns_left(item, turn)
        return f"<div style=\"{'color:red;' if turns_left == 1 else 'color:orange;' if turns_left == 2 else ''}\">{turns_left}</div>"

    def _get_race_aptitudes(self, surface, distance, uma_aptitudes):
        # C or higher is "good"
        #
        # 1=G, 2=F, etc.
        good_surface = False
        good_distance = False
        if surface == 1:
            if uma_aptitudes["proper_ground_turf"] >= 5:
                good_surface = True
        else:
            if uma_aptitudes["proper_ground_dirt"] >= 5:
                good_surface = True

        if distance <= 1400:
            if uma_aptitudes["proper_distance_short"] >= 5:
                good_distance = True
        elif distance <= 1800:
            if uma_aptitudes["proper_distance_mile"] >= 5:
                good_distance = True
        elif distance <= 2400:
            if uma_aptitudes["proper_distance_middle"] >= 5:
                good_distance = True
        else:
            if uma_aptitudes["proper_distance_long"] >= 5:
                good_distance = True

        return good_surface, good_distance

    def get_race_details_text(self, surface, distance, uma_aptitudes):
        surface_text = ""
        good_surface, good_distance = self._get_race_aptitudes(surface, distance, uma_aptitudes)
        distance_text = ""
        if surface == 1:
            surface_text = "Turf"
        else:
            surface_text = "Dirt"
        if good_surface:
            surface_text = f"<div style=\"color:lightgreen;\">{surface_text}</div>"
        else:
            surface_text = f"<div style=\"color:orange;\">{surface_text}</div>"

        if good_distance:
            distance_text = f"<div style=\"color:lightgreen;\">{distance}m</div>"
        else:
            distance_text = f"<div style=\"color:orange;\">{distance}m</div>"

        return f"<div style=\"display:flex; justify-content:space-around;{'font-weight:bold;' if good_distance and good_surface else ''}\">" + surface_text + "<div>-</div>" + distance_text + "</div>"

    def grade_to_pts(self, grade):
        if grade == 700:
            return "20"  # Pre-OP
        elif grade == 400:
            return "40"  # OP
        elif grade == 300:
            return "60"  # G3
        elif grade == 200:
            return "80"  # G2
        elif grade == 100:
            return "100"  # G1
        else:
            return "-"

    def get_thumb_url(self, program_id):
        program_data = mdb.get_program_id_data(program_id)
        if not program_data:
            util.show_warning_box(f"Could not get program data for program_id {program_id}")
            return None

        if program_data['base_program_id'] != 0:
            program_data = mdb.get_program_id_data(program_data['base_program_id'])

        if not program_data:
            util.show_warning_box(f"Could not get program data for program_id {program_id}")
            return None

        thumb_url = f"https://gametora.com/images/umamusume/en/race_banners/thum_race_rt_000_{str(program_data['race_instance_id'])[:4]}_00.png"
        return thumb_url

    def to_dict(self):
        return {
            "name": self.name,
            "settings": self.settings.to_dict(),
            "rows": [row.to_dict(self.row_types) for row in self.initialized_rows] if self.initialized_rows else []
        }
    
    def from_dict(self, preset_dict):
        if "name" in preset_dict:
            self.name = preset_dict["name"]
        if "settings" in preset_dict:
            self.settings.from_dict(preset_dict["settings"])
        if "rows" in preset_dict:
            self.initialized_rows = []
            for row_dict in preset_dict["rows"]:
                try:
                    # TODO: Make this proper.
                    row_object = self.row_types[row_dict["type"]].value()
                except KeyError:
                    logger.error(f"Unknown row type: {row_dict['type']}")
                    continue
                if row_object.settings:
                    row_object.settings.from_dict(row_dict["settings"])
                self.initialized_rows.append(row_object)
