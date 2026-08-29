import re

import constants


SECTION_END_TURNS = (24, 36, 48, 60, 72)
LESSON_BUDGET = (10, 16, 16, 22, 22)
HIGHEST_SONGS = {40000, 40004}
HIGH_SONGS = set()
IGNORED_SONGS = set()

SONG_PHASES = (
    {40001, 40002, 40003, 40006, 40007, 40009, 40010, 40020},
    {40000, 40011, 40013},
    {40004, 40005, 40008, 40017},
    {40012, 40014, 40015, 40016, 40018, 40019},
)


def section_for_turn(turn):
    if not 5 <= turn <= 72:
        return None
    return next(index for index, end_turn in enumerate(SECTION_END_TURNS) if turn <= end_turn)


def can_afford(balance, cost):
    return all(balance[token] >= cost.get(token, 0) for token in constants.GL_TOKEN_LIST)


def subtract(balance, cost):
    return {
        token: balance[token] - cost.get(token, 0)
        for token in constants.GL_TOKEN_LIST
    }


class GrandLiveSuggester:
    """Track Grand Live purchases and rank the three visible squares."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.training_id = None
        self.section = None
        self.lesson_count = 0
        self.last_page = ()
        self.carried_song_page = False
        self.pending = None

    def record_request(self, data, catalog):
        try:
            square_id = int(data.get("square_id"))
            turn = int(data.get("current_turn"))
        except (AttributeError, TypeError, ValueError):
            return
        section = section_for_turn(turn)
        if section is None or square_id not in catalog:
            return
        self.pending = {
            "square_id": square_id,
            "section": section,
            "carried_song": self.carried_song_page,
        }

    def record_response(self, data, catalog):
        live_data = data.get("live_data_set") if isinstance(data, dict) else None
        if not isinstance(live_data, dict):
            return

        chara_info = data.get("chara_info") or {}
        training_id = chara_info.get("start_time")
        if training_id and training_id != self.training_id:
            pending = self.pending
            self.reset()
            self.training_id = training_id
            self.pending = pending

        turn = chara_info.get("turn")
        if turn is None and self.pending:
            section = self.pending["section"]
        else:
            try:
                section = section_for_turn(int(turn))
            except (TypeError, ValueError):
                return
        if section is None:
            return

        page = tuple(
            item.get("square_id")
            for item in (live_data.get("next_square_info_array") or [])
            if isinstance(item, dict)
        )
        if self.section != section:
            self.carried_song_page = bool(
                self.last_page
                and page == self.last_page
                and any(catalog.get(square_id, {}).get("type") == 4 for square_id in page)
            )
            self.section = section
            self.lesson_count = 0

        if self.pending and self.pending["section"] == section:
            selected = catalog.get(self.pending["square_id"], {})
            if selected.get("type") == 4:
                self.lesson_count = 1 if self.pending["carried_song"] else 0
                self.carried_song_page = False
            elif selected:
                self.lesson_count += 1
            self.pending = None

        self.last_page = page

    def rank(self, data, catalog):
        chara_info = data.get("chara_info") or {}
        live_data = data.get("live_data_set") or {}
        try:
            turn = int(chara_info.get("turn"))
        except (TypeError, ValueError):
            return None
        section = section_for_turn(turn)
        if chara_info.get("scenario_id") != 3 or section is None:
            return None

        page = live_data.get("next_square_info_array") or []
        if not page:
            return None
        performance = live_data.get("live_performance_info") or {}
        balance = {
            token: int(performance.get(token, 0) or 0)
            for token in constants.GL_TOKEN_LIST
        }

        pool = set().union(*SONG_PHASES[:min(section, 3) + 1])
        live_to_square = {
            square["song_id"]: square_id
            for square_id, square in catalog.items()
            if square.get("song_id")
        }
        current_song_ids = live_data.get("next_live_id_array") or []
        owned = {
            live_to_square[live_id]
            for live_id in (live_data.get("master_live_id_array") or [])
            if live_id in live_to_square
        }
        remaining_pool = pool - owned
        manual_songs = sum(live_id in live_to_square for live_id in current_song_ids)

        def pattern_gap(cycle):
            if section == 0:
                pattern = (1, 2, 3, 4, 4, 2, 3)
                return pattern[cycle] if cycle < len(pattern) else None
            prefix = (2, 2, 2)
            loop = (4, 3, 2, 2) if section == 4 else (4, 5, 2, 2)
            return prefix[cycle] if cycle < 3 else loop[(cycle - 3) % len(loop)]

        current_gap = pattern_gap(manual_songs)
        song_page = any(
            catalog.get(item.get("square_id"), {}).get("type") == 4
            for item in page
        )
        lesson_step = (
            current_gap
            if song_page and current_gap is not None
            else min(self.lesson_count + 1, current_gap)
            if current_gap is not None
            else None
        )
        terminal = turn == 72
        choices = []

        for item in page:
            square_id = item.get("square_id")
            square = catalog.get(square_id)
            if not square:
                continue
            cost = square["cost"]
            affordable = can_afford(balance, cost)
            after = subtract(balance, cost) if affordable else dict(balance)
            future_pool = remaining_pool - ({square_id} if square["type"] == 4 else set())

            if square["type"] == 4:
                next_gap = pattern_gap(manual_songs + 1)
                lesson_steps = next_gap
            elif current_gap is None:
                lesson_steps = None
            else:
                lesson_steps = max(0, current_gap - self.lesson_count - 1)

            lesson_cost = lesson_steps * LESSON_BUDGET[section] if lesson_steps is not None else None
            can_expose = bool(
                affordable
                and lesson_cost is not None
                and sum(after.values()) >= lesson_cost
            )
            reachable = {"highest": 0, "high": 0, "normal": 0}
            for future_id in future_pool:
                future_cost = catalog[future_id]["cost"]
                if not can_expose or not can_afford(after, future_cost):
                    continue
                if sum(after.values()) - sum(future_cost.values()) < lesson_cost:
                    continue
                if terminal:
                    reachable["normal"] += 1
                elif future_id in HIGHEST_SONGS:
                    reachable["highest"] += 1
                elif future_id in HIGH_SONGS:
                    reachable["high"] += 1
                elif future_id not in IGNORED_SONGS:
                    reachable["normal"] += 1

            demand_pool = future_pool if terminal else future_pool - IGNORED_SONGS
            demand = {
                token: sum(catalog[future_id]["cost"].get(token, 0) for future_id in demand_pool)
                for token in constants.GL_TOKEN_LIST
            }
            pressure = sum(
                cost.get(token, 0) * demand[token] / max(balance[token], 1)
                for token in constants.GL_TOKEN_LIST
            )
            margins = [
                after[token] / demand[token]
                for token in constants.GL_TOKEN_LIST
                if demand[token]
            ]
            choices.append({
                "slot": int(item.get("square_num", 0) or 0),
                "square_id": square_id,
                "name": square["name"],
                "kind": "Song" if square["type"] == 4 else "Lesson",
                "cost": {token: value for token, value in cost.items() if value},
                "effect": re.sub(r"<[^>]+>", "", square.get("effect") or "").replace("\\n", " · "),
                "affordable": affordable,
                "can_expose": can_expose,
                "reachable": reachable,
                "reachable_songs": sum(reachable.values()),
                "highest": bool(not terminal and square_id in HIGHEST_SONGS),
                "pressure": pressure,
                "margin": min(margins, default=0),
                "total_cost": sum(cost.values()),
            })

        if not choices:
            return None

        expose_only = bool(
            not song_page
            and not terminal
            and not any(choice["can_expose"] and choice["reachable_songs"] for choice in choices)
            and any(choice["can_expose"] for choice in choices)
        )
        for choice in choices:
            if song_page or terminal:
                choice["score"] = (
                    choice["affordable"],
                    choice["can_expose"],
                    choice["reachable_songs"],
                    -choice["pressure"],
                    choice["margin"],
                    -choice["total_cost"],
                    -choice["slot"],
                )
            elif expose_only:
                choice["score"] = (
                    choice["affordable"],
                    choice["can_expose"],
                    -choice["total_cost"],
                    -choice["pressure"],
                    choice["margin"],
                    -choice["slot"],
                )
            else:
                choice["score"] = (
                    choice["affordable"],
                    choice["can_expose"],
                    choice["reachable"]["highest"],
                    choice["reachable"]["high"],
                    choice["reachable"]["normal"],
                    -choice["pressure"],
                    choice["margin"],
                    -choice["total_cost"],
                    -choice["slot"],
                )

        order = sorted(range(len(choices)), key=lambda index: choices[index]["score"], reverse=True)
        for rank, index in enumerate(order, 1):
            choices[index]["rank"] = rank
            for key in ("score", "pressure", "margin", "total_cost"):
                choices[index].pop(key, None)
        choices.sort(key=lambda choice: choice["slot"])
        best = next(choice for choice in choices if choice["rank"] == 1)

        if not best["affordable"]:
            status = "stop"
            message = "No affordable choices"
        elif terminal or song_page or not expose_only:
            status = "pick"
            message = None
        elif best["can_expose"]:
            status = "pick"
            message = "Expose, then carry"
        else:
            status = "stop"
            message = "Stop after the last song"

        return {
            "turn": turn,
            "section": section,
            "auto_open": chara_info.get("playing_state") == 10,
            "status": status,
            "mode": "terminal" if terminal else "song" if song_page else "expose" if expose_only else "coverage",
            "message": message,
            "points": balance,
            "lesson_step": lesson_step,
            "lesson_total": current_gap,
            "songs_acquired": len(current_song_ids),
            "recommended_slot": best["slot"] if status == "pick" else None,
            "choices": choices,
        }
