"""Private helper-table rows backed by Python training simulation output."""

import helper_table_elements as hte


def _get_nested(data, path):
    current = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _get_first_nested(data, paths):
    for path in paths:
        value = _get_nested(data, path)
        if value is not None:
            return value
    return None


def _format_number(value):
    if value is None:
        return ""
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return ""


def _format_signed_number(value):
    if value is None:
        return ""
    try:
        return f"{float(value):+.1f}"
    except (TypeError, ValueError):
        return ""


def _format_percent(value):
    if value is None:
        return ""
    try:
        return f"{float(value) * 100:.0f}%"
    except (TypeError, ValueError):
        return ""


def _tooltip(training_sim):
    parts = []

    raw_total = training_sim.get("rawTotal")
    if raw_total is not None:
        parts.append(f"Raw total: {raw_total}")

    expected_raw = _get_first_nested(
        training_sim,
        [
            ("baseline", "expected", "total"),
            ("baseline", "expectedTotal"),
        ],
    )
    if expected_raw is not None:
        parts.append(f"Avg raw: {_format_number(expected_raw)}")

    expected_score = _get_nested(training_sim, ("baseline", "expectedScore"))
    if expected_score is not None:
        parts.append(f"Avg score: {_format_number(expected_score)}")

    support_ids = training_sim.get("supportIds", [])
    if support_ids:
        parts.append(f"Supports: {', '.join(str(support_id) for support_id in support_ids)}")

    visible_stats = training_sim.get("visibleStats")
    final_stats = training_sim.get("finalStats")
    if isinstance(visible_stats, dict) and isinstance(final_stats, dict):
        mismatches = []
        for stat_key in ("speed", "stamina", "power", "guts", "wiz", "skillPt"):
            visible_value = visible_stats.get(stat_key)
            final_value = final_stats.get(stat_key)
            if (
                visible_value is not None
                and final_value is not None
                and visible_value != final_value
            ):
                mismatches.append(
                    f"{stat_key}: packet {visible_value} vs sim {final_value}"
                )

        if mismatches:
            parts.append(f"Visible stat mismatch: {'; '.join(mismatches)}")

    return " | ".join(parts)


class TrainingSimValueRow(hte.Row):
    paths = []
    formatter = staticmethod(_format_number)

    def to_tr(self, command_info):
        if not any(command.get("training_sim") for command in command_info.values()):
            return ""
        return super().to_tr(command_info)

    def get_value(self, training_sim):
        return _get_first_nested(training_sim, self.paths)

    def _generate_cells(self, game_state) -> list[hte.Cell]:
        cells = [hte.Cell(self.short_name, title=self.description)]

        for command in game_state.values():
            training_sim = command.get("training_sim")
            if not training_sim:
                cells.append(hte.Cell())
                continue

            value = self.get_value(training_sim)
            cells.append(hte.Cell(self.formatter(value), title=_tooltip(training_sim)))

        return cells


class TrainingSimScoreRow(TrainingSimValueRow):
    long_name = "Training sim score"
    short_name = "Score"
    description = "Shows the simulator score for this displayed training."
    paths = [("score",)]


class TrainingSimRiskAdjustedScoreRow(TrainingSimValueRow):
    long_name = "Training sim risk-adjusted score"
    short_name = "Risk Score"
    description = "Shows the simulator score after failure risk adjustment."
    paths = [("riskAdjustedScore",)]


class TrainingSimExpectedRawRow(TrainingSimValueRow):
    long_name = "Training sim average raw total"
    short_name = "Avg Raw"
    description = (
        "Shows the average raw stat total for this facility from the current "
        "deck/state baseline."
    )
    paths = [
        ("baseline", "expected", "total"),
        ("baseline", "expectedTotal"),
    ]


class TrainingSimExpectedScoreRow(TrainingSimValueRow):
    long_name = "Training sim average score"
    short_name = "Avg Score"
    description = (
        "Shows the average simulator score for this facility from the current "
        "deck/state baseline."
    )
    paths = [("baseline", "expectedScore")]


class TrainingSimScoreDeltaRow(TrainingSimValueRow):
    long_name = "Training sim score delta"
    short_name = "Score Delta"
    description = "Shows displayed score minus the current deck/state average score."
    formatter = staticmethod(_format_signed_number)

    def get_value(self, training_sim):
        score = training_sim.get("score")
        expected_score = _get_nested(training_sim, ("baseline", "expectedScore"))
        if score is None or expected_score is None:
            return None
        return score - expected_score


class TrainingSimScorePercentileRow(TrainingSimValueRow):
    long_name = "Training sim score percentile"
    short_name = "Score %ile"
    description = (
        "Shows where this training's score lands in the current deck/state baseline."
    )
    paths = [("baseline", "scorePercentile")]
    formatter = staticmethod(_format_percent)


class TrainingSimWhistleDowngradeRow(TrainingSimValueRow):
    long_name = "Training sim whistle downgrade probability"
    short_name = "Whistle Down"
    description = (
        "Shows the chance that a whistle reroll gives a lower raw stat total than "
        "this training."
    )
    paths = [("whistle", "downgradeProbability")]
    formatter = staticmethod(_format_percent)


TRAINING_ROW_CLASSES = (
    TrainingSimScoreRow,
    TrainingSimRiskAdjustedScoreRow,
    TrainingSimExpectedRawRow,
    TrainingSimExpectedScoreRow,
    TrainingSimScoreDeltaRow,
    TrainingSimScorePercentileRow,
    TrainingSimWhistleDowngradeRow,
)

__all__ = [row.__name__ for row in TRAINING_ROW_CLASSES] + ["TRAINING_ROW_CLASSES"]
