"""« Écran de chargement » card: what is worth knowing about the ten players while the game loads.

Shown in a small top-most overlay window during the loading screen and the first
:data:`SHOW_UNTIL_GT` seconds of the game, on the Accueil page, and in the Discord rank announcement
(:func:`treeaicoach.player_intel.ranks_tick`). Per enemy, BY CHAMPION (never a name or an id):

* rank of the game's queue "Platine 2 · 54 % (37 parties)" and his current streak;
* his mains (top 3 by games in his last 20 games, champion masteries when the client gives them),
  "OTP (80 % de ses parties)", "1re partie sur ce champion" (0 mastery point) / "pas joué récemment";
* his last 5 games (champion + V / D);
* "Son main Sett a été banni" when one of his mains is in this game's bans (champion ids kept in memory
  from the champion select screen by :mod:`treeaicoach.champ_select`).

Then one line per lane (5 lanes) from :mod:`treeaicoach.draft_edge` (who wins levels 1-3 / 6 / items + one
tip from :mod:`treeaicoach.game_plan`) and our win condition; allies in one short line each.

Privacy (same rules as :mod:`treeaicoach.player_intel`, whose data this only formats): the data exists only
after champion select (gameflow ``GameStart`` / ``InProgress``), in memory, shown by champion; nothing of
it is logged or written. Pure functions + the Qt window; nothing here does I/O. Never raises.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)

SHOW_UNTIL_GT = 90.0            # the overlay stays up until this game time ...
SHOW_MAX_S = 420.0              # ... and never longer than this after the game was detected (no Live Client)
ROLE_ORDER = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
LANE_FR = {"TOP": "Top", "JUNGLE": "Jungle", "MIDDLE": "Mid", "BOTTOM": "Tireur", "UTILITY": "Support"}
ROLE_FR = {"TOP": "top", "JUNGLE": "jungle", "MIDDLE": "mid", "BOTTOM": "tireur", "UTILITY": "support"}
NOT_PLAYED_MIN = 8              # "pas joué sur ses N dernières" from this many recent games


@dataclass(frozen=True)
class EnemyBlock:
    head: str                   # "Darius · top · Platine 2 · 54 % (37 parties)"
    lines: tuple[str, ...] = ()  # facts, mains, last 5 games
    alert: str = ""             # "Son main Sett a été banni"


@dataclass(frozen=True)
class LoadingCard:
    enemies: tuple[EnemyBlock, ...] = ()
    allies: tuple[str, ...] = ()
    matchups: tuple[str, ...] = ()
    win_condition: str = ""
    complete: bool = False      # every request answered

    def lines(self) -> list[str]:
        """Plain text lines (Accueil card / tests)."""
        out = ["Ennemis"]
        for b in self.enemies:
            out.append(b.head)
            out.extend("  " + x for x in b.lines)
            if b.alert:
                out.append("  ⚠ " + b.alert)
        if self.matchups:
            out.append("Voies")
            out.extend(self.matchups)
        if self.win_condition:
            out.append("Plan : " + self.win_condition)
        if self.allies:
            out.append("Alliés")
            out.extend(self.allies)
        return out


def _resolver() -> Callable[[int], tuple[str, str] | None]:
    from treeaicoach import player_intel

    return player_intel._default_resolver()


def _cname(cid: int, resolve: Callable[[int], tuple[str, str] | None]) -> str:
    try:
        r = resolve(int(cid))
        return (r[1] or r[0]) if r else ""
    except Exception:
        return ""


def _fill_roles(players: list[Any]) -> list[Any]:
    """Enemy positions are hidden at loading: the missing ones are guessed from the champions' usual
    lanes (:func:`treeaicoach.champ_select.assign_roles`)."""
    from dataclasses import replace

    enemies = [p for p in players if not p.ally]
    if not enemies or all(p.role in ROLE_ORDER for p in enemies):
        return players
    try:
        from treeaicoach.champ_select import Pick, assign_roles

        guess = {pick.alias: role for role, (pick, _s) in
                 assign_roles(tuple(Pick(p.alias, p.name) for p in enemies)).items()}
    except Exception:
        guess = {}
    taken = {p.role for p in enemies if p.role in ROLE_ORDER}
    out = []
    for p in players:
        if not p.ally and p.role not in ROLE_ORDER and guess.get(p.alias) and guess[p.alias] not in taken:
            taken.add(guess[p.alias])
            p = replace(p, role=guess[p.alias])
        out.append(p)
    return out


def _order(p: Any) -> int:
    return ROLE_ORDER.index(p.role) if p.role in ROLE_ORDER else len(ROLE_ORDER)


def _mains(p: Any) -> list[int]:
    """His mains' champion ids: history first (games), then masteries."""
    ids = [int(c) for c, _g in (p.mains or ())]
    ids += [int(c) for c, _pts in (p.mastery_mains or ()) if int(c) not in ids]
    return ids[:3]


def _champ_fact(p: Any) -> str:
    if p.mastery_loaded and p.champ_points == 0:
        return "1re partie sur ce champion"
    if p.history_loaded and p.recent_all >= 5 and p.champ_all / p.recent_all >= 0.6:
        return f"OTP ({round(100 * p.champ_all / p.recent_all)} % de ses parties)"
    if p.history_loaded and p.recent_all >= NOT_PLAYED_MIN and p.champ_all == 0:
        return f"pas joué sur ses {p.recent_all} dernières"
    return ""


def enemy_block(p: Any, bans: frozenset[int] | set[int] = frozenset(),
                resolve: Callable[[int], tuple[str, str] | None] | None = None) -> EnemyBlock:
    from treeaicoach.player_intel import rank_text, streak_text

    resolve = resolve or _resolver()
    role = ROLE_FR.get(p.role, "")
    rank = rank_text(p)
    head = p.name + (f" · {role}" if role else "") + (f" · {rank}" if rank else "")
    facts = [x for x in (streak_text(p.streak) if p.history_loaded else "", _champ_fact(p)) if x]
    lines = []
    if facts:
        lines.append(" · ".join(facts))
    if p.mains:
        names = [f"{_cname(c, resolve)} {g}" for c, g in p.mains if _cname(c, resolve)]
        if names:
            lines.append("Mains : " + ", ".join(names) + " (20 dernières)")
    elif p.mastery_mains:
        names = [_cname(c, resolve) for c, _pts in p.mastery_mains if _cname(c, resolve)]
        if names:
            lines.append("Mains : " + ", ".join(names) + " (maîtrise)")
    if p.last5:
        games = [f"{_cname(c, resolve) or '?'} {'V' if w else 'D'}" for c, w in p.last5]
        lines.append("5 dernières : " + " · ".join(games))
    if not rank and not lines:
        lines.append("chargement…")
    alert = ""
    for cid in _mains(p):
        if cid in bans and cid != getattr(p, "champion_id", 0):
            alert = f"Son main {_cname(cid, resolve) or '?'} a été banni"
            break
    return EnemyBlock(head, tuple(lines), alert)


def ally_line(p: Any) -> str:
    from treeaicoach.player_intel import _short_rank, streak_text

    role = ROLE_FR.get(p.role, "")
    bits = [x for x in (_short_rank(p), streak_text(p.streak) if p.history_loaded else "", _champ_fact(p)) if x]
    return p.name + (f" ({role})" if role else "") + (" : " + " · ".join(bits) if bits else " : chargement…")


def matchup_lines(allies: Iterable[Any], enemies: Iterable[Any], my_role: str = "") -> list[str]:
    """One line per lane: "Top (ta voie) · Garen vs Darius : Darius gagne jusqu'au 1er objet · <conseil>"."""
    from treeaicoach import draft_edge, game_plan

    a = {p[2]: p for p in allies if p[2] in ROLE_ORDER}
    e = {p[2]: p for p in enemies if p[2] in ROLE_ORDER}
    out = []
    for role in ROLE_ORDER:
        if role not in a or role not in e:
            continue
        (aa, an, _r), (ea, en, _r2) = a[role], e[role]
        lane = LANE_FR[role] + (" (ta voie)" if role == my_role else "")
        duel = draft_edge.duel_line(aa, an, ea, en)
        tip = ""
        try:
            tips = game_plan.lane_lines(aa, ea, en, 1) if role != "JUNGLE" else []
            tip = tips[0] if tips else ""
        except Exception:
            pass
        out.append(f"{lane} · {an} vs {en}" + (f" : {duel}" if duel else "") + (f" · {tip}" if tip else ""))
    return out


def build(players: Iterable[Any], bans: frozenset[int] | set[int] = frozenset(),
          me: tuple[str, str, str] | None = None, complete: bool = False,
          resolve: Callable[[int], tuple[str, str] | None] | None = None) -> LoadingCard | None:
    """The card from :meth:`PlayerIntelWatcher.snapshot` (+ the bans, + my (alias, name, role)). None
    without players. Pure, never raises."""
    try:
        ps = _fill_roles(list(players or ()))
        if not ps:
            return None
        resolve = resolve or _resolver()
        enemies = sorted((p for p in ps if not p.ally), key=_order)
        allies = sorted((p for p in ps if p.ally), key=_order)
        blocks = tuple(enemy_block(p, bans, resolve) for p in enemies)
        ally_picks = [(p.alias, p.name, p.role) for p in allies]
        my_role = ""
        if me and me[0]:
            ally_picks.append(me)
            my_role = me[2]
        enemy_picks = [(p.alias, p.name, p.role) for p in enemies]
        matchups = tuple(matchup_lines(ally_picks, enemy_picks, my_role))
        wc = ""
        try:
            from treeaicoach import draft_edge

            cr = draft_edge.comp_read(ally_picks, enemy_picks, me[0] if me else "")
            wc = cr.sentence if cr is not None else ""
        except Exception:
            log.debug("win condition failed", exc_info=True)
        return LoadingCard(blocks, tuple(ally_line(p) for p in allies), matchups, wc, complete)
    except Exception:
        log.debug("loading card failed", exc_info=True)
        return None


def from_watcher(w: Any, bans: frozenset[int] | None = None) -> LoadingCard | None:
    """The card of the running :class:`treeaicoach.player_intel.PlayerIntelWatcher` (lock-only reads)."""
    try:
        players = w.snapshot()
        if not players:
            return None
        if bans is None:
            from treeaicoach import champ_select

            bans = champ_select.last_bans()
        me = None
        cid, role = w.me_pick()
        if cid:
            from treeaicoach.player_intel import _default_resolver

            r = _default_resolver()(cid)
            if r:
                me = (r[0], r[1] or r[0], role)
        return build(players, bans, me, complete=w.ready())
    except Exception:
        log.debug("loading card from watcher failed", exc_info=True)
        return None


def discord_extra(card: LoadingCard | None) -> str:
    """What the Discord rank announcement adds: banned mains, OTP / first games, the lanes, the plan."""
    if card is None:
        return ""
    out = []
    notes = []
    for b in card.enemies:
        name = b.head.split(" · ")[0]
        bits = [x for x in (b.alert,) if x]
        bits += [ln for ln in b.lines[:1] if ("OTP" in ln or "1re partie" in ln or "pas joué" in ln)]
        if bits:
            notes.append(f"{name} : " + ", ".join(bits))
    if notes:
        out.append("À savoir : " + " · ".join(notes))
    out.extend(card.matchups)
    if card.win_condition:
        out.append("Plan : " + card.win_condition)
    return "\n".join(out)


def should_show(since: float | None, game_time: Any, now: float) -> bool:
    """Overlay visible from the game's detection (loading screen) until game time :data:`SHOW_UNTIL_GT`
    (or :data:`SHOW_MAX_S` after detection when the game time is unknown)."""
    if since is None or now - since > SHOW_MAX_S:
        return False
    try:
        return game_time is None or float(game_time) < SHOW_UNTIL_GT
    except (TypeError, ValueError):
        return True


# ----------------------------------------------------------------------------- Qt window
def _html(card: LoadingCard) -> str:
    from html import escape as e

    rows = ["<div style='font-size:15px;font-weight:700;color:#F0E6D2'>Écran de chargement"
            + ("" if card.complete else " <span style='font-weight:400;color:#A09B8C'>· lecture…</span>")
            + "</div>"]
    for b in card.enemies:
        rows.append(f"<div style='margin-top:5px;color:#FF8A80;font-weight:600'>{e(b.head)}</div>")
        rows.extend(f"<div style='color:#D8D2C2'>&nbsp;&nbsp;{e(x)}</div>" for x in b.lines)
        if b.alert:
            rows.append(f"<div style='color:#FFD54F;font-weight:600'>&nbsp;&nbsp;⚠ {e(b.alert)}</div>")
    if card.matchups:
        rows.append("<div style='margin-top:7px;color:#C8AA6E;font-weight:700'>Voies</div>")
        rows.extend(f"<div style='color:#E8E2D2'>{e(x)}</div>" for x in card.matchups)
    if card.win_condition:
        rows.append(f"<div style='margin-top:5px;color:#8BE38B'>Plan : {e(card.win_condition)}</div>")
    if card.allies:
        rows.append("<div style='margin-top:7px;color:#7FB2FF;font-weight:700'>Alliés</div>")
        rows.extend(f"<div style='color:#BFC8D6'>{e(x)}</div>" for x in card.allies)
    return "".join(rows)


def make_window(parent: Any = None) -> Any:
    """A top-most, click-through, non-activating translucent window (PySide6); None without Qt."""
    try:
        from PySide6 import QtCore, QtWidgets
    except Exception:
        return None

    class LoadingWindow(QtWidgets.QWidget):
        def __init__(self) -> None:
            super().__init__(parent, QtCore.Qt.WindowType.Tool | QtCore.Qt.WindowType.FramelessWindowHint
                             | QtCore.Qt.WindowType.WindowStaysOnTopHint
                             | QtCore.Qt.WindowType.WindowTransparentForInput)
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.setWindowTitle("TreeAI · Écran de chargement")
            lay = QtWidgets.QVBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            self.box = QtWidgets.QLabel()
            self.box.setTextFormat(QtCore.Qt.TextFormat.RichText)
            self.box.setWordWrap(True)
            self.box.setStyleSheet("QLabel{background:rgba(10,14,20,215);border:1px solid #785A28;"
                                   "border-radius:8px;padding:10px 14px;font-size:12px}")
            lay.addWidget(self.box)
            self.setFixedWidth(560)
            self._sig: Any = None

        def set_card(self, card: LoadingCard) -> None:
            html = _html(card)
            if html == self._sig:
                return
            self._sig = html
            self.box.setText(html)
            self.adjustSize()
            try:
                scr = self.screen() or QtWidgets.QApplication.primaryScreen()
                g = scr.availableGeometry()
                self.move(g.x() + 16, g.y() + 16)          # top-left: the loading screen's cards are centred
            except Exception:
                pass

    return LoadingWindow()


class LoadingController:
    """Drives the overlay window from the UI thread (lock-only reads, no I/O). :meth:`tick` returns the
    card for the Accueil page (None: nothing to show)."""

    def __init__(self, window_factory: Callable[[], Any] = make_window,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._factory = window_factory
        self._clock = clock
        self._win: Any = None

    def tick(self, watcher: Any, game_time: Any = None, overlay: bool = True) -> LoadingCard | None:
        try:
            card = from_watcher(watcher) if watcher is not None else None
            show = (overlay and card is not None
                    and should_show(watcher.game_since(), game_time, self._clock()))
            if show:
                if self._win is None:
                    self._win = self._factory()
                if self._win is not None:
                    self._win.set_card(card)
                    if not self._win.isVisible():
                        self._win.show()
            else:
                self.hide()
            return card
        except Exception:
            log.debug("loading overlay tick failed", exc_info=True)
            return None

    def hide(self) -> None:
        try:
            if self._win is not None and self._win.isVisible():
                self._win.hide()
        except Exception:
            pass

    @property
    def visible(self) -> bool:
        try:
            return self._win is not None and bool(self._win.isVisible())
        except Exception:
            return False


__all__ = ["LoadingCard", "EnemyBlock", "build", "from_watcher", "enemy_block", "ally_line", "matchup_lines",
           "discord_extra", "should_show", "make_window", "LoadingController"]
