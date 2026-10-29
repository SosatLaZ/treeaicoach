"""« Écran de chargement » card: what is worth knowing about the ten players while the game loads.

Shown in a small top-most overlay window ONLY during the loading screen (rule below), on the Accueil
page (loading screen and game), and in the Discord rank announcement
(:func:`treeaicoach.player_intel.ranks_tick`). By champion, never a name or an id.

Overlay window (:class:`LoadingController`, :func:`overlay_verdict`):

* shown from the game's detection (gameflow ``GameStart`` / ``InProgress``) while the game window is in
  front (never over the League client, our own window or the desktop; hidden while the game is minimised
  or not focused, back if it comes back in front during the loading screen);
* hidden for good for this game (never re-appears) as soon as the game starts: Live Client game time
  above :data:`GAME_STARTED_GT`, or the minimap seen (engine ``RUNNING``) and at the latest
  :data:`STARTED_GRACE_S` after it; at the latest :data:`SHOW_MAX_S` after detection; as soon as the
  overlay hotkey hides the visual indicators; and when the game ends (no game: nothing shown).

Layout (:func:`make_window`, DESIGN.md graphite, one accent): two columns Alliés | Ennemis, one row per
player (round champion icon, champion, lane, rank badge, one short tag: OTP / 1re partie / série /
main banni), then the 5 matchups as 5 short lines. 420 x 300 px at 1080p, scaled with the screen height.

Privacy (same rules as :mod:`treeaicoach.player_intel`, whose data this only formats): the data exists only
after champion select, in memory, shown by champion; nothing of it is logged or written. Pure functions +
the Qt window; nothing here does I/O except reading bundled champion icons. Never raises.
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)

GAME_STARTED_GT = 4.0           # Live Client game time above this: the game started, the window goes
#   (the engine extrapolates a frozen 0:00 by up to 3 s during the loading screen: 4 s is past that)
STARTED_GRACE_S = 15.0          # minimap seen without a game time: gone at the latest this long after
SHOW_MAX_S = 300.0              # never longer than this after the game was detected (slow loading screen)
BANS_MAX_AGE_S = 240.0          # bans recorded more than this before the game's detection: another game
ROLE_ORDER = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
LANE_FR = {"TOP": "Top", "JUNGLE": "Jungle", "MIDDLE": "Mid", "BOTTOM": "Tireur", "UTILITY": "Support"}
ROLE_FR = {"TOP": "top", "JUNGLE": "jungle", "MIDDLE": "mid", "BOTTOM": "tireur", "UTILITY": "support"}
NOT_PLAYED_MIN = 8              # "pas joué sur ses N dernières" from this many recent games
TEAM_SIZE = 5
NO_INFO = "pas d'info du client"
LOADING = "chargement…"

#: base size of the overlay at 1080p (logical px) and the scale bounds (screen height / 1080)
BASE_W, BASE_H = 420, 300
SCALE_MIN, SCALE_MAX = 0.8, 1.6


@dataclass(frozen=True)
class EnemyBlock:
    head: str                   # "Darius · top · Platine 2 · 54 % (37 parties)"
    lines: tuple[str, ...] = ()  # facts, mains, last 5 games
    alert: str = ""             # "Son main Sett a été banni"


@dataclass(frozen=True)
class Row:
    """One player of the overlay: champion, lane, rank badge, one short tag."""
    alias: str
    name: str
    lane: str = ""              # "Top"
    rank: str = ""              # "Platine 2" / "Non classé" / "" (unknown)
    tag: str = ""               # "OTP" / "1re partie" / "3 défaites" / "Main banni" / "Toi"
    tone: str = ""              # "good" (good for us) / "warn" (careful) / "" (neutral)
    state: str = "ok"           # "ok" / "loading" / "none" (the client never answered)


@dataclass(frozen=True)
class LoadingCard:
    enemies: tuple[EnemyBlock, ...] = ()
    allies: tuple[str, ...] = ()
    matchups: tuple[str, ...] = ()
    win_condition: str = ""
    complete: bool = False      # every request answered
    ally_rows: tuple[Row, ...] = ()
    enemy_rows: tuple[Row, ...] = ()
    duels: tuple[tuple[str, str], ...] = ()   # ("Top", "Garen vs Darius · Darius gagne niv 1-3"), 5 at most
    teams_known: bool = True    # False: my team is unknown (the card says so, the overlay stays hidden)

    def lines(self) -> list[str]:
        """Plain text lines (Accueil card / tests)."""
        out = ["Ennemis"]
        for b in self.enemies:
            out.append(b.head)
            out.extend("  " + x for x in b.lines)
            if b.alert:
                out.append("  " + b.alert)
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
    lanes (:func:`treeaicoach.champ_select.assign_roles`). A position held twice on one side keeps the
    first player only (the other is guessed again or left without lane)."""
    from dataclasses import replace

    out = list(players)
    for ally in (False, True):
        taken: set[str] = set()
        for i, p in enumerate(out):
            if p.ally is ally and p.role in ROLE_ORDER:
                if p.role in taken:
                    out[i] = replace(p, role="")
                else:
                    taken.add(p.role)
        side = [p for p in out if p.ally is ally]
        if not side or all(p.role in ROLE_ORDER for p in side) or ally:
            continue
        try:
            from treeaicoach.champ_select import Pick, assign_roles

            guess = {pick.alias: role for role, (pick, _s) in
                     assign_roles(tuple(Pick(p.alias, p.name) for p in side)).items()}
        except Exception:
            guess = {}
        for i, p in enumerate(out):
            if p.ally is ally and p.role not in ROLE_ORDER and guess.get(p.alias) and guess[p.alias] not in taken:
                taken.add(guess[p.alias])
                out[i] = replace(p, role=guess[p.alias])
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


def _banned_main(p: Any, bans: Any, resolve: Callable[[int], tuple[str, str] | None]) -> str:
    for cid in _mains(p):
        if cid in bans and cid != getattr(p, "champion_id", 0):
            return _cname(cid, resolve) or "?"
    return ""


def _state(p: Any, complete: bool) -> str:
    if p.ranked_loaded or p.history_loaded or p.mastery_loaded:
        return "ok"
    return "none" if complete else "loading"


def enemy_block(p: Any, bans: frozenset[int] | set[int] = frozenset(),
                resolve: Callable[[int], tuple[str, str] | None] | None = None,
                complete: bool = False) -> EnemyBlock:
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
        lines.append(NO_INFO if _state(p, complete) == "none" else LOADING)
    banned = _banned_main(p, bans, resolve)
    return EnemyBlock(head, tuple(lines), f"Son main {banned} a été banni" if banned else "")


def ally_line(p: Any, complete: bool = False) -> str:
    from treeaicoach.player_intel import _short_rank, streak_text

    role = ROLE_FR.get(p.role, "")
    bits = [x for x in (_short_rank(p), streak_text(p.streak) if p.history_loaded else "", _champ_fact(p)) if x]
    empty = NO_INFO if _state(p, complete) == "none" else LOADING
    return p.name + (f" ({role})" if role else "") + (" : " + " · ".join(bits) if bits else f" : {empty}")


def _short_tag(p: Any, ally: bool, banned: str) -> tuple[str, str]:
    """(tag, tone) of one player: the single most useful fact, two words at most."""
    good, warn = ("", "") if ally else ("good", "warn")
    if banned:
        return "Main banni", good
    if p.mastery_loaded and p.champ_points == 0:
        return "1re partie", good
    if p.history_loaded and p.recent_all >= 5 and p.champ_all / p.recent_all >= 0.6:
        return "OTP", warn
    if p.history_loaded and p.streak <= -2:
        return f"{-p.streak} défaites", good
    if p.history_loaded and p.streak >= 3:
        return f"{p.streak} victoires", warn
    if p.history_loaded and p.recent_all >= NOT_PLAYED_MIN and p.champ_all == 0:
        return "Pas joué", good
    return "", ""


def player_row(p: Any, bans: Any = frozenset(), resolve: Callable[[int], tuple[str, str] | None] | None = None,
               complete: bool = False) -> Row:
    from treeaicoach.player_intel import _short_rank

    rank = _short_rank(p)
    rank = rank[:1].upper() + rank[1:]
    banned = "" if p.ally else _banned_main(p, bans, resolve or _resolver())
    tag, tone = _short_tag(p, p.ally, banned)
    return Row(p.alias, p.name, LANE_FR.get(p.role, ""), rank, tag, tone, _state(p, complete))


def _duels(allies: Iterable[Any], enemies: Iterable[Any], my_role: str = "",
           with_tip: bool = True) -> list[tuple[str, str, str]]:
    """(role, lane label, text) per lane with both sides known, in lane order."""
    from treeaicoach import draft_edge, game_plan

    a: dict[str, Any] = {}
    e: dict[str, Any] = {}
    for p in allies:
        a.setdefault(p[2], p)
    for p in enemies:
        e.setdefault(p[2], p)
    out = []
    for role in ROLE_ORDER:
        if role not in a or role not in e:
            continue
        (aa, an, _r), (ea, en, _r2) = a[role], e[role]
        duel = draft_edge.duel_line(aa, an, ea, en)
        if not with_tip:
            duel = duel.split(", ")[0]                   # the overlay: one short clause per lane
        tip = ""
        if with_tip and role != "JUNGLE":
            try:
                tips = game_plan.lane_lines(aa, ea, en, 1)
                tip = tips[0] if tips else ""
            except Exception:
                pass
        lane = LANE_FR[role] + (" (ta voie)" if role == my_role and with_tip else "")
        out.append((role, lane, f"{an} vs {en}" + (f" : {duel}" if duel else "") + (f" · {tip}" if tip else "")))
    return out


def matchup_lines(allies: Iterable[Any], enemies: Iterable[Any], my_role: str = "") -> list[str]:
    """One line per lane: "Top (ta voie) · Garen vs Darius : Darius gagne jusqu'au 1er objet · <conseil>"."""
    return [f"{lane} · {text}" for _r, lane, text in _duels(allies, enemies, my_role)]


def _dedupe(players: Iterable[Any], skip_alias: str = "") -> list[Any]:
    seen: set[tuple[str, bool]] = set()
    out = []
    for p in players:
        k = (str(p.alias).lower(), bool(p.ally))
        if k in seen or (p.ally and skip_alias and k[0] == skip_alias.lower()):
            continue
        seen.add(k)
        out.append(p)
    return out


def build(players: Iterable[Any], bans: frozenset[int] | set[int] = frozenset(),
          me: tuple[str, str, str] | None = None, complete: bool = False,
          resolve: Callable[[int], tuple[str, str] | None] | None = None) -> LoadingCard | None:
    """The card from :meth:`PlayerIntelWatcher.snapshot` (+ the bans, + my (alias, name, role)). None
    without players. Pure, never raises."""
    try:
        ps = _fill_roles(_dedupe(players or (), me[0] if me else ""))
        if not ps:
            return None
        resolve = resolve or _resolver()
        enemies = sorted((p for p in ps if not p.ally), key=_order)
        allies = sorted((p for p in ps if p.ally), key=_order)
        if not allies and len(enemies) > TEAM_SIZE:
            # my team is unknown (my puuid never came): never show nine "enemies"
            return LoadingCard(complete=complete, teams_known=False,
                               allies=tuple(f"{p.name} : équipe inconnue" for p in enemies))
        enemies, allies = enemies[:TEAM_SIZE], allies[:TEAM_SIZE - (1 if me and me[0] else 0)]
        blocks = tuple(enemy_block(p, bans, resolve, complete) for p in enemies)
        ally_picks = [(p.alias, p.name, p.role) for p in allies]
        my_role = ""
        ally_rows = [player_row(p, bans, resolve, complete) for p in allies]
        if me and me[0]:
            ally_picks.append(me)
            my_role = me[2]
            ally_rows.append(Row(me[0], me[1] or me[0], LANE_FR.get(me[2], ""), "", "Toi", "me", "ok"))
            ally_rows.sort(key=lambda r: next((i for i, k in enumerate(ROLE_ORDER) if LANE_FR[k] == r.lane), 9))
        enemy_picks = [(p.alias, p.name, p.role) for p in enemies]
        matchups = tuple(matchup_lines(ally_picks, enemy_picks, my_role))
        duels = tuple((lane, text) for _r, lane, text in _duels(ally_picks, enemy_picks, "", with_tip=False))
        wc = ""
        try:
            from treeaicoach import draft_edge

            cr = draft_edge.comp_read(ally_picks, enemy_picks, me[0] if me else "")
            wc = cr.sentence if cr is not None else ""
        except Exception:
            log.debug("win condition failed", exc_info=True)
        return LoadingCard(blocks, tuple(ally_line(p, complete) for p in allies), matchups, wc, complete,
                           tuple(ally_rows), tuple(player_row(p, bans, resolve, complete) for p in enemies),
                           duels)
    except Exception:
        log.debug("loading card failed", exc_info=True)
        return None


def game_bans(since: float | None) -> frozenset[int]:
    """The bans of THIS game's champion select: recorded at most :data:`BANS_MAX_AGE_S` before the game's
    detection (a game without bans, ARAM or a remake, never shows the previous game's bans)."""
    try:
        from treeaicoach import champ_select

        with champ_select._bans_lock:
            t, b = champ_select._bans
        if since is None or not b or not (-5.0 <= since - t <= BANS_MAX_AGE_S):
            return frozenset()
        return frozenset(b)
    except Exception:
        return frozenset()


def from_watcher(w: Any, bans: frozenset[int] | None = None) -> LoadingCard | None:
    """The card of the running :class:`treeaicoach.player_intel.PlayerIntelWatcher` (lock-only reads)."""
    try:
        players = w.snapshot()
        if not players:
            return None
        if bans is None:
            bans = game_bans(w.game_since())
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


def _gt(game_time: Any) -> float | None:
    try:
        return None if game_time is None else float(game_time)
    except (TypeError, ValueError):
        return None


def should_show(since: float | None, game_time: Any, now: float, started_at: float | None = None) -> bool:
    """The time rule of the overlay alone: from the game's detection (``since``) until the game starts
    (game time above :data:`GAME_STARTED_GT`, or :data:`STARTED_GRACE_S` after the minimap was first
    seen at ``started_at``), never longer than :data:`SHOW_MAX_S`."""
    if since is None or now - since > SHOW_MAX_S:
        return False
    gt = _gt(game_time)
    if gt is not None and gt > GAME_STARTED_GT:
        return False
    return started_at is None or now - started_at < STARTED_GRACE_S


def overlay_verdict(*, since: float | None, now: float, game_time: Any = None, started_at: float | None = None,
                    overlay_on: bool = True, focus: bool | None = None, has_card: bool = True,
                    win32: bool = sys.platform == "win32") -> str:
    """What the overlay does now: "show", "hide" (for now: not in front / no card yet) or "done" (hidden
    for the rest of this game). ``focus``: the game window is in front (None: unknown; on Windows that is
    "no game window", so hidden)."""
    if since is None:
        return "hide"
    if not overlay_on or not should_show(since, game_time, now, started_at):
        return "done"
    if focus is False or (focus is None and win32) or not has_card:
        return "hide"
    return "show"


def game_in_front() -> bool | None:
    """The League game window is the foreground window (None: no game window / not Windows)."""
    if sys.platform != "win32":
        return None
    try:
        from treeaicoach.capture import foreground_state

        return foreground_state()[0]
    except Exception:
        return None


# ----------------------------------------------------------------------------- Qt window
#: DESIGN.md tokens (graphite, one accent)
C_BG = (12, 14, 13, 224)       # BG, semi-opaque
C_LINE = "#2F3532"
C_TEXT = "#E4E8E5"
C_MUTED = "#A4ADA8"
C_DIM = "#7E8782"
C_ACCENT = "#9BD84A"
C_WARN = "#E8A23A"
C_RAISED = "#191D1B"
C_ALLY = "#4A90D9"
C_ENEMY = "#E5484D"


def scale_for(height: float) -> float:
    """Overlay scale from the screen's logical height (1080 -> 1.0), clamped so text stays readable."""
    try:
        return max(SCALE_MIN, min(SCALE_MAX, float(height) / 1080.0))
    except (TypeError, ValueError):
        return 1.0


def _icon_image(alias: str) -> Any:
    """Round champion portrait as a QImage (bundled / cached icon), None when absent."""
    try:
        from PySide6 import QtGui

        from treeaicoach import champions

        img = champions.get_default_db().load_icon(alias)
        if img is None:
            return None
        h, w = img.shape[:2]
        buf = bytes(img.tobytes())
        return QtGui.QImage(buf, w, h, 4 * w, QtGui.QImage.Format.Format_RGBA8888).copy()
    except Exception:
        return None


def make_window(parent: Any = None) -> Any:
    """A top-most, click-through, never-activated translucent window (PySide6); None without Qt."""
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except Exception:
        return None
    Qt = QtCore.Qt

    class LoadingWindow(QtWidgets.QWidget):
        def __init__(self) -> None:
            super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                             | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowTransparentForInput
                             | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.NoDropShadowWindowHint)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.setWindowTitle("TreeAI · Écran de chargement")
            self.card: LoadingCard | None = None
            self.s = 1.0
            self._icons: dict[str, Any] = {}
            self._geo: Any = None
            self._pinned: Any = None
            self.place()

        # ---------------------------------------------------------- geometry
        def place(self, geo: Any = None) -> None:
            """Size from the screen height, top-left corner of the screen (the loading screen's cards
            are centred). Only moves when the screen changed (no jump on updates)."""
            try:
                if geo is not None:
                    self._pinned = geo                   # a given screen (tests, the game's monitor) stays
                geo = geo if geo is not None else self._pinned
                if geo is None:
                    scr = self.screen() or QtWidgets.QApplication.primaryScreen()
                    geo = scr.geometry()
                key = (geo.x(), geo.y(), geo.width(), geo.height())
                if key == self._geo:
                    return
                self._geo = key
                self.s = scale_for(geo.height())
                self.setFixedSize(round(BASE_W * self.s), round(BASE_H * self.s))
                m = round(16 * self.s)
                self.move(geo.x() + m, geo.y() + m)
            except Exception:
                log.debug("loading window place failed", exc_info=True)

        def set_card(self, card: LoadingCard) -> None:
            self.place()
            if card == self.card:
                return                                   # same data: no repaint, no flicker
            self.card = card
            self.update()

        def showEvent(self, ev: Any) -> None:  # noqa: N802
            super().showEvent(ev)
            _no_activate(self)

        # ---------------------------------------------------------- painting
        def _font(self, px: float, bold: bool = False) -> Any:
            f = QtGui.QFont(self.font())
            for fam in ("Inter", "Segoe UI Variable", "Segoe UI"):
                if fam in QtGui.QFontDatabase.families():
                    f.setFamily(fam)
                    break
            f.setPixelSize(max(9, round(px * self.s)))
            f.setWeight(QtGui.QFont.Weight.DemiBold if bold else QtGui.QFont.Weight.Normal)
            return f

        def _text(self, p: Any, x: float, y: float, w: float, h: float, text: str, color: str, font: Any,
                  align: Any = None) -> float:
            """Draws ``text`` elided to ``w``; returns the width used."""
            p.setFont(font)
            p.setPen(QtGui.QColor(color))
            fm = QtGui.QFontMetrics(font)
            t = text if fm.horizontalAdvance(text) <= w + 1 else \
                fm.elidedText(text, Qt.TextElideMode.ElideRight, max(0, int(w)))
            al = align if align is not None else (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            p.drawText(QtCore.QRectF(x, y, w, h), int(al), t)
            return float(fm.horizontalAdvance(t))

        def _icon(self, alias: str) -> Any:
            if alias not in self._icons:
                self._icons[alias] = _icon_image(alias)
            return self._icons[alias]

        def paintEvent(self, _ev: Any) -> None:  # noqa: N802
            p = QtGui.QPainter(self)
            try:
                p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
                p.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)
                p.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)
                self._paint(p)
            except Exception:
                log.debug("loading window paint failed", exc_info=True)
            finally:
                p.end()

        def _paint(self, p: Any) -> None:
            s, W, H = self.s, float(self.width()), float(self.height())
            path = QtGui.QPainterPath()
            path.addRoundedRect(QtCore.QRectF(0.5, 0.5, W - 1, H - 1), 12 * s, 12 * s)
            p.fillPath(path, QtGui.QColor(*C_BG))
            p.setPen(QtGui.QPen(QtGui.QColor(C_LINE), 1))
            p.drawPath(path)
            card = self.card
            if card is None:
                return
            pad, gap = 12 * s, 12 * s
            colw = (W - 2 * pad - gap) / 2
            y = pad
            head = self._font(12, True)
            for i, (title, dot) in enumerate((("Alliés", C_ALLY), ("Ennemis", C_ENEMY))):
                x = pad + i * (colw + gap)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QtGui.QColor(dot))
                p.drawEllipse(QtCore.QRectF(x, y + 7 * s, 6 * s, 6 * s))
                used = self._text(p, x + 11 * s, y, colw - 11 * s, 20 * s, title, C_TEXT, head)
                if i == 1 and not card.complete:
                    self._text(p, x + 11 * s + used, y, colw - used - 11 * s, 20 * s, "  · lecture",
                               C_DIM, self._font(12))
            y += 22 * s
            rh = 32 * s
            for i, rows in enumerate((card.ally_rows, card.enemy_rows)):
                x = pad + i * (colw + gap)
                for j, r in enumerate(rows[:TEAM_SIZE]):
                    self._row(p, x, y + j * rh, colw, rh, r)
            y += TEAM_SIZE * rh + 4 * s
            p.setPen(QtGui.QPen(QtGui.QColor(C_LINE), 1))
            p.drawLine(QtCore.QPointF(pad, y), QtCore.QPointF(W - pad, y))
            y += 6 * s
            lh = 15.5 * s
            small, lane_f = self._font(12), self._font(12, True)
            lfm = QtGui.QFontMetrics(lane_f)
            lane_w = max([lfm.horizontalAdvance(lane) for lane, _t in card.duels] + [0]) + 8 * s
            for lane, text in card.duels[:TEAM_SIZE]:
                self._text(p, pad, y, lane_w, lh, lane, C_DIM, lane_f)
                self._text(p, pad + lane_w, y, W - 2 * pad - lane_w, lh, text, C_MUTED, small)
                y += lh
            if not card.duels:
                self._text(p, pad, y, W - 2 * pad, lh, "Voies : en attente des postes", C_DIM, small)

        def _row(self, p: Any, x: float, y: float, w: float, h: float, r: Row) -> None:
            s = self.s
            d = 24 * s
            iy = y + (h - d) / 2
            img = self._icon(r.alias)
            clip = QtGui.QPainterPath()
            clip.addEllipse(QtCore.QRectF(x, iy, d, d))
            if img is not None:
                p.save()
                p.setClipPath(clip)
                p.drawImage(QtCore.QRectF(x, iy, d, d), img)
                p.restore()
            else:
                p.fillPath(clip, QtGui.QColor(C_RAISED))
            if r.tone == "me":
                p.setPen(QtGui.QPen(QtGui.QColor(C_ACCENT), max(1.0, 1.5 * s)))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QtCore.QRectF(x, iy, d, d))
            tx = x + d + 7 * s
            tw = w - d - 7 * s
            l1, l2 = y + 1 * s, y + h / 2
            lh = h / 2 - 1 * s
            tag_f = self._font(12, True)
            tag_c = {"good": C_ACCENT, "warn": C_WARN, "me": C_ACCENT}.get(r.tone, C_MUTED)
            tag_w = 0.0
            name_f = self._font(13, True)
            if r.tag:
                fm = QtGui.QFontMetrics(tag_f)
                name_w = min(tw * 0.45, float(QtGui.QFontMetrics(name_f).horizontalAdvance(r.name)))
                tag_w = min(tw - name_w - 6 * s, float(fm.horizontalAdvance(r.tag)) + 1)
                self._text(p, tx + tw - tag_w, l1, tag_w, lh, r.tag, tag_c, tag_f,
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._text(p, tx, l1, tw - tag_w - (6 * s if tag_w else 0), lh, r.name, C_TEXT, name_f)
            sub = self._font(12)
            used = 0.0
            if r.lane:
                used = self._text(p, tx, l2, tw, lh, r.lane, C_DIM, sub) + 6 * s
            if r.state == "loading":
                rank, rc = LOADING, C_DIM
            elif r.state == "none" and not r.rank:
                rank, rc = "inconnu", C_DIM
            else:
                rank, rc = r.rank, C_MUTED
            if rank and r.tone != "me":
                fm = QtGui.QFontMetrics(sub)
                bw = min(tw - used, fm.horizontalAdvance(rank) + 12 * s)
                if bw > 12 * s:
                    br = QtCore.QRectF(tx + used, l2 + 1 * s, bw, lh - 2 * s)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QtGui.QColor(C_RAISED))
                    p.drawRoundedRect(br, 4 * s, 4 * s)
                    self._text(p, br.x() + 5 * s, l2, bw - 10 * s, lh, rank, rc, sub)

    return LoadingWindow()


def _no_activate(win: Any) -> None:
    """Windows: WS_EX_NOACTIVATE | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW on top of Qt's flags (never takes
    the focus from the game, clicks go through, not in Alt+Tab). No-op elsewhere. Never raises."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = int(win.winId())
        user32 = ctypes.windll.user32
        ex = user32.GetWindowLongW(hwnd, -20)
        user32.SetWindowLongW(hwnd, -20, ex | 0x08000000 | 0x00000020 | 0x00000080 | 0x00080000)
    except Exception:
        log.debug("loading window styles failed", exc_info=True)


class LoadingController:
    """Drives the overlay window from the UI thread (lock-only reads, no I/O): the lifetime rule of the
    module docstring. :meth:`tick` returns the card for the Accueil page (None: nothing to show)."""

    def __init__(self, window_factory: Callable[[], Any] = make_window,
                 clock: Callable[[], float] = time.monotonic,
                 focus: Callable[[], bool | None] = game_in_front, win32: bool = sys.platform == "win32") -> None:
        self._factory = window_factory
        self._clock = clock
        self._focus = focus
        self._win32 = win32
        self._win: Any = None
        self._game: Any = None           # game_since() of the game the state below is about
        self._done = False               # hidden for the rest of this game
        self._started_at: float | None = None
        self._last: LoadingCard | None = None
        self.reason = ""                 # why it is hidden (tests / logs)

    def tick(self, watcher: Any, game_time: Any = None, overlay: bool = True,
             started: bool = False) -> LoadingCard | None:
        try:
            card = from_watcher(watcher) if watcher is not None else None
            since = watcher.game_since() if watcher is not None else None
            if since is None:                            # no game (ended, or not detected yet)
                self.reason = "hide"
                self.hide()
                return card
            if since != self._game:                      # a new game: fresh state (a gap in between keeps it)
                self._game, self._done, self._started_at, self._last = since, False, None, None
            if card is not None:
                self._last = card
            now = self._clock()
            if started and self._started_at is None:
                self._started_at = now
            if self._done:
                self.hide()
                return card
            shown = self._last
            has = shown is not None and shown.teams_known and bool(shown.enemy_rows)
            focus = None
            try:
                focus = self._focus()
            except Exception:
                pass
            v = overlay_verdict(since=since, now=now, game_time=game_time, started_at=self._started_at,
                                overlay_on=overlay, focus=focus, has_card=has, win32=self._win32)
            self.reason = v
            if v == "done":
                self._done = True
            if v == "show":
                if self._win is None:
                    self._win = self._factory()
                if self._win is not None:
                    self._win.set_card(shown)
                    if not self._win.isVisible():
                        self._win.show()
            else:
                self.hide()
            return card
        except Exception:
            log.debug("loading overlay tick failed", exc_info=True)
            self.hide()
            return None

    def hide(self) -> None:
        try:
            if self._win is not None and self._win.isVisible():
                self._win.hide()
        except Exception:
            pass

    def dismiss(self) -> None:
        """Hidden for the rest of this game (hotkey)."""
        self._done = True
        self.hide()

    @property
    def visible(self) -> bool:
        try:
            return self._win is not None and bool(self._win.isVisible())
        except Exception:
            return False


__all__ = ["LoadingCard", "EnemyBlock", "Row", "build", "from_watcher", "enemy_block", "ally_line",
           "player_row", "matchup_lines", "discord_extra", "should_show", "overlay_verdict", "game_bans",
           "scale_for", "make_window", "LoadingController"]
