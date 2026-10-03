"""Analyse: session summary + the list of recorded games (read on a worker thread), one click to the
HTML report. The list is rebuilt only when the history changed (signature)."""

from __future__ import annotations

import logging
from typing import Any

from treeaicoach import paths
from treeaicoach.ui_common import (
    GAMES_PAGE,
    _game_json_path,
    _int_or_none,
    champion_name,
    fmt_clock,
    fmt_decimal_fr,
    fmt_game_date,
    game_datetime,
    game_field,
    game_result,
    open_path,
    session_stats,
    ui_text,
)

log = logging.getLogger(__name__)


def game_line(g: dict) -> tuple[str, str, str, str]:
    """(title, detail, result text, result tone) of one recorded game."""
    champ = champion_name(game_field(g, "champion", "alias", default="")) or "Champion inconnu"
    role = str(game_field(g, "role", default="") or "").lower()
    res = game_result(g)
    k, d, a = (_int_or_none(game_field(g, x)) for x in ("kills", "deaths", "assists"))
    parts = [fmt_game_date(game_datetime(g))]
    dur = game_field(g, "duration", "game_duration")
    if isinstance(dur, (int, float)) and dur > 0:
        parts.append(fmt_clock(dur))
    if k is not None or d is not None:
        parts.append(f"{k if k is not None else '?'} / {d if d is not None else '?'} / {a if a is not None else '?'}")
    title = champ + (f" · {role}" if role and role not in ("none", "?") else "")
    return (title, " · ".join(parts), {"win": "Victoire", "lose": "Défaite"}.get(res or "", ""),
            {"win": "ok", "lose": "danger"}.get(res or "", ""))


class AnalysisPage:
    def __init__(self, app: Any) -> None:
        self.app = app
        W, QtWidgets = app.W, app.QtWidgets
        self.page = W.Page("Analyse", "Après chaque partie : rapport (morts, ganks, jungler adverse, conseils) "
                                      "et tes vraies stats lues dans le client LoL.")
        self._sig: Any = None
        self._limit = GAMES_PAGE
        self._games: list[dict] = []

        self.summary = self.page.section("Résumé")
        box = QtWidgets.QWidget()
        bl = QtWidgets.QHBoxLayout(box)
        bl.setContentsMargins(20, 16, 20, 16)
        bl.setSpacing(32)
        self.stats: dict[str, Any] = {}
        for key, cap in (("games", "Parties"), ("winrate", "Victoires"), ("deaths", "Morts par partie"),
                         ("ganks", "Ganks évités")):
            col = QtWidgets.QVBoxLayout()
            col.setSpacing(2)
            v = W.label("–", "big")
            col.addWidget(v)
            col.addWidget(W.label(cap, "secondary"))
            bl.addLayout(col)
            self.stats[key] = v
        bl.addStretch(1)
        self.summary.add(box)

        actions = QtWidgets.QWidget()
        al = QtWidgets.QHBoxLayout(actions)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(8)
        al.addWidget(W.button("Actualiser", app.refresh_games))
        al.addWidget(W.button("Dossier", self.open_games_dir))
        self.list = self.page.section("Parties", "", actions)
        self.more = W.button("Afficher plus", self._show_more, "link")
        self.more.hide()
        self.page.add(self.more)
        self.lcu = W.label("", "footnote")
        self.page.add(self.lcu)
        self._rows: list[Any] = []
        self._empty = self.list.add(W.Row("Lecture de l'historique…", None))

    def on_show(self) -> None:
        self.app.refresh_lcu(self._set_lcu)
        if not self._games and self.app._games:
            self.on_games(self.app._games)

    def _set_lcu(self, text: str) -> None:
        self.lcu.setText(ui_text(text))

    def _show_more(self) -> None:
        self._limit += GAMES_PAGE
        self._sig = None
        self.on_games(self._games)

    def on_games(self, games: list[dict]) -> None:
        sig = (tuple(str(_game_json_path(g) or id(g)) for g in games[: self._limit]), self._limit)
        self._games = games
        if sig == self._sig:
            return
        self._sig = sig
        W = self.app.W
        s = session_stats(games)
        self.summary.header.setText(f"Résumé · {s['scope']}")
        self.stats["games"].setText(str(s["games"]) if s["games"] else "–")
        self.stats["winrate"].setText(f"{round(100 * s['winrate'])} %" if s["winrate"] is not None else "–")
        self.stats["deaths"].setText(fmt_decimal_fr(s["deaths_per_game"], 1) if s["deaths_per_game"] is not None
                                     else "–")
        self.stats["ganks"].setText(f"{s['ganks_avoided']} / {s['ganks']}" if s["ganks"] else "–")
        grp = self.list.group
        for r in self._rows:
            r.setParent(None)
            r.deleteLater()
        self._rows = []
        # rebuild the group (rows + separators) from scratch: a fresh Group, same section
        new = W.Group()
        lay = self.list.layout()
        lay.replaceWidget(grp, new)
        grp.setParent(None)
        grp.deleteLater()
        self.list.group = new
        if not games:
            new.add(W.Row("Aucune partie enregistrée", "Joue une partie avec l'analyse active : le rapport "
                                                      "apparaît ici."))
            self.more.hide()
            return
        for g in games[: self._limit]:
            title, detail, res, tone = game_line(g)
            right = self.app.QtWidgets.QWidget()
            rl = self.app.QtWidgets.QHBoxLayout(right)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(16)
            if res:
                rl.addWidget(W.label(res, None, tone=tone))
            rl.addWidget(W.button("Rapport", lambda gg=g: self.app.open_report(gg)))
            row = W.Row(title, detail, right)
            new.add(row)
            self._rows.append(row)
        self.more.setVisible(len(games) > self._limit)

    def open_games_dir(self) -> None:
        d = paths.user_data_dir() / "games"
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        if not open_path(d):
            self.app.show_error(f"Impossible d'ouvrir le dossier {d}.")


def build(app: Any) -> AnalysisPage:
    return AnalysisPage(app)
