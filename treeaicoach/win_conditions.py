"""Personal win conditions: which measurable conditions separate MY wins from MY losses.

From my own recorded games (``progress.collect`` rows, cached next to the records) each game gets a
few numbers (:func:`game_features`): deaths before 10:00, creep score at 10:00, first purchase back
in base, ward score at 15:00, lane gold gap at 10 / 15 min (``postgame_edge.lane_diffs``: client
timeline, else the Tab), share of the epic monsters I was near (``analysis`` objective presence).

:func:`analyze` turns each condition into a yes / no test on a small fixed threshold grid
(no free fitting: ``CONDITIONS``) and reports the win rate when the condition is met vs not met
with the counts ("0 mort avant 10:00 : 6 victoires sur 8 quand tenu, 2 sur 12 sinon"). The effect size is the
win-rate gap; a condition needs ``MIN_SIDE`` games on each side.

:func:`pick_focus` picks AT MOST ONE "focus du soir": among the conditions I can act on live
(``live`` = deaths / CS / vision / first back), the one with the biggest win-rate gap x the share of
games where I failed it. Fewer than ``MIN_GAMES`` usable games (classic Rift, >= 10 min, known
result) -> nothing. Same role first when it has enough games.

:class:`FocusTracker` follows the chosen focus live from the Live Client numbers only (my scores)
and gives ONE quiet line for the essentials strip while it is at risk ("Focus : 0 mort avant 10:00
- 1 mort"), and the post-game verdict (``result``). Pure Python, never raises.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from treeaicoach.fmtutil import finite_loose as _f

log = logging.getLogger(__name__)

MIN_GAMES = 8
MIN_SIDE = 3                     # games on each side of a threshold
MIN_GAP = 0.12                   # smoothed win-rate gap below this is noise on a dozen games
MIN_GAME_S = 600.0
BACK_MIN_S = 90.0                # purchases before this are the starting items
BACK_SPEND = 250                 # gold drop between two snapshots that means "bought something"
HISTORY = 30
RISK_LINE_S = 60.0               # the live strip line stays this long after the event that put the focus at risk

#: key -> (metric, op, thresholds, live, roles excluded). "ge": met when value >= thr, "le": <= thr.
CONDITIONS: dict[str, tuple[str, str, tuple[float, ...], bool, frozenset[str]]] = {
    "deaths10": ("wc_deaths10", "le", (0, 1), True, frozenset()),
    "cs10": ("wc_cs10", "ge", (40, 50, 60, 70), True, frozenset({"UTILITY"})),
    "first_back": ("wc_first_back", "ge", (240, 300, 360, 420), True, frozenset()),
    "vision15": ("wc_ward15", "ge", (8, 12, 16, 20), True, frozenset()),
    "gold10": ("wc_gold10", "ge", (-300, 0, 300), False, frozenset()),
    "gold15": ("wc_gold15", "ge", (-300, 0, 300), False, frozenset()),
    "objectives": ("wc_obj", "ge", (0.25, 0.4, 0.5), False, frozenset()),
}


def _fmt_t(s: float) -> str:
    s = int(round(s))
    return f"{s // 60}:{s % 60:02d}"


def label(key: str, thr: float) -> str:
    """French label of a condition at a threshold ("0 mort avant 10:00")."""
    t = int(thr) if float(thr).is_integer() else thr
    if key == "deaths10":
        return "0 mort avant 10:00" if t == 0 else f"{t} mort max avant 10:00"
    if key == "cs10":
        return f"{t} sbires à 10:00"
    if key == "first_back":
        return f"pas de retour avant {_fmt_t(float(t))}"
    if key == "vision15":
        return f"score de vision {t} à 15:00"
    if key in ("gold10", "gold15"):
        m = 10 if key == "gold10" else 15
        return f"or à {m} min ≥ {t:+d} PO".replace("-", "−") if isinstance(t, int) else f"or à {m} min ≥ {t}"
    if key == "objectives":
        return f"présent sur {int(round(float(t) * 100))} % des objectifs"
    return key


# ======================================================================================
# one game
# ======================================================================================
def _snaps(record: dict) -> list[dict]:
    out = [s for s in record.get("snapshots") or [] if isinstance(s, dict) and _f(s.get("game_time")) is not None]
    out.sort(key=lambda s: float(s["game_time"]))
    return out


def _at(snaps: list[dict], t: float, key: str, tol: float = 45.0) -> float | None:
    best, bd = None, tol + 1
    for s in snaps:
        d = abs(float(s["game_time"]) - t)
        if d < bd and _f(s.get(key)) is not None:
            best, bd = _f(s.get(key)), d
    return best if bd <= tol else None


def first_back(snaps: list[dict]) -> float | None:
    """Game time of my first purchase after the starting items (gold drop >= BACK_SPEND), or None."""
    prev = None
    for s in snaps:
        gt, g = float(s["game_time"]), _f(s.get("gold"))
        if g is None:
            continue
        if prev is not None and gt >= BACK_MIN_S and prev[1] - g >= BACK_SPEND and gt - prev[0] <= 60.0:
            return round(prev[0], 1)
        prev = (gt, g)
    return None


def game_features(record: Any, analysis: Any = None, truth: Any = None) -> dict[str, Any]:
    """``wc_*`` numbers of one game (None when unknown)."""
    out: dict[str, Any] = {"wc_deaths10": None, "wc_cs10": None, "wc_first_back": None, "wc_ward15": None,
                           "wc_gold10": None, "wc_gold15": None, "wc_obj": None}
    try:
        if not isinstance(record, dict):
            return out
        snaps = _snaps(record)
        if snaps and float(snaps[0]["game_time"]) <= 150.0:      # recorded from the start
            last = max((s for s in snaps if float(s["game_time"]) <= 600.0), key=lambda s: float(s["game_time"]),
                       default=None)
            if last is not None and float(last["game_time"]) >= 540.0:
                out["wc_deaths10"] = _f(last.get("deaths"))
            out["wc_cs10"] = _at(snaps, 600.0, "cs")
            out["wc_ward15"] = _at(snaps, 900.0, "ward_score")
            fb = first_back(snaps)
            if fb is not None or float(snaps[-1]["game_time"]) >= 600.0:
                out["wc_first_back"] = fb if fb is not None else float(snaps[-1]["game_time"])
        from treeaicoach import postgame_edge  # noqa: PLC0415

        for r in postgame_edge.lane_diffs(record, truth).get("rows") or []:
            if r.get("minute") in (10, 15):
                out[f"wc_gold{r['minute']}"] = _f(r.get("gold_diff"))
        a = analysis if isinstance(analysis, dict) else None
        if a is None:
            from treeaicoach import analysis as an  # noqa: PLC0415
            a = an.analyze_game(record)
        items = [i for i in (a.get("objective_presence") or {}).get("items") or [] if isinstance(i, dict)]
        if len(items) >= 2:
            out["wc_obj"] = round(sum(1 for i in items if i.get("near")) / len(items), 2)
    except Exception:
        log.debug("win condition features failed", exc_info=True)
    return out


# ======================================================================================
# many games
# ======================================================================================
@dataclass(frozen=True)
class Effect:
    key: str
    threshold: float
    label: str
    live: bool
    n_met: int
    w_met: int
    n_not: int
    w_not: int

    @property
    def gap(self) -> float:
        """Win-rate gap, Laplace-smoothed ((w + 1) / (n + 2)) so that 3 games weigh less than 10."""
        return (self.w_met + 1) / (self.n_met + 2) - (self.w_not + 1) / (self.n_not + 2)

    @property
    def raw_gap(self) -> float:
        return self.w_met / self.n_met - self.w_not / self.n_not

    @property
    def fail_rate(self) -> float:
        return self.n_not / (self.n_met + self.n_not)

    @property
    def score(self) -> float:
        return self.gap * self.fail_rate

    def line(self) -> str:
        return (f"{self.label} : {self.w_met} victoires sur {self.n_met} quand tenu, "
                f"{self.w_not} sur {self.n_not} sinon")


def usable(rows: Sequence[dict]) -> list[dict]:
    """Classic Rift games >= 10 min with a known result."""
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        res = str(r.get("result") or "").lower()
        if res not in ("win", "lose"):
            continue
        if (_f(r.get("duration")) or 0.0) < MIN_GAME_S:
            continue
        if str(r.get("game_mode") or "CLASSIC").upper() not in ("CLASSIC", "SWIFTPLAY", ""):
            continue
        out.append(r)
    return out


def _met(op: str, v: float, thr: float) -> bool:
    return v >= thr if op == "ge" else v <= thr


def effects(rows: Sequence[dict]) -> list[Effect]:
    """Best threshold of each condition (biggest gap with MIN_SIDE games per side), sorted by gap."""
    games = usable(rows)
    out: list[Effect] = []
    for key, (metric, op, thrs, live, excl) in CONDITIONS.items():
        pts = [(float(_f(r.get(metric))), str(r.get("result")).lower() == "win") for r in games
               if _f(r.get(metric)) is not None and str(r.get("position") or "").upper() not in excl]
        best: Effect | None = None
        for thr in thrs:
            met = [w for v, w in pts if _met(op, v, thr)]
            nm = [w for v, w in pts if not _met(op, v, thr)]
            if len(met) < MIN_SIDE or len(nm) < MIN_SIDE:
                continue
            e = Effect(key, thr, label(key, thr), live, len(met), sum(met), len(nm), sum(nm))
            if best is None or e.gap > best.gap:
                best = e
        if best is not None:
            out.append(best)
    out.sort(key=lambda e: -e.gap)
    return out


def champion_rates(rows: Sequence[dict]) -> list[tuple[str, int, int]]:
    """``[(champion / role, wins, games)]`` most played first."""
    agg: dict[str, list[int]] = {}
    for r in usable(rows):
        k = f"{r.get('champion_name') or r.get('champion') or '?'} {str(r.get('position') or '').lower()}".strip()
        a = agg.setdefault(k, [0, 0])
        a[0] += int(str(r.get("result")).lower() == "win")
        a[1] += 1
    return sorted(((k, w, n) for k, (w, n) in agg.items()), key=lambda x: (-x[2], x[0]))


@dataclass(frozen=True)
class Focus:
    key: str
    threshold: float
    label: str
    why: str                      # "6 victoires sur 8 quand tenu, 2 sur 12 sinon"
    effect: Effect = field(compare=False)

    @property
    def text(self) -> str:
        return f"Focus : {self.label}"


def pick_focus(rows: Sequence[dict], role: str | None = None) -> Focus | None:
    """At most one live-trackable condition (biggest gap x fail rate), None under MIN_GAMES games."""
    try:
        games = usable(rows)
        if len(games) < MIN_GAMES:
            return None
        role = str(role or "").upper()
        same = [r for r in games if str(r.get("position") or "").upper() == role] if role else []
        pool = same if len(same) >= MIN_GAMES else games
        cands = [e for e in effects(pool) if e.live and e.gap >= MIN_GAP
                 and not (role and role in CONDITIONS[e.key][4])]
        if not cands:
            return None
        e = max(cands, key=lambda e: (e.score, e.gap))
        why = f"{e.w_met} victoires sur {e.n_met} quand tenu, {e.w_not} sur {e.n_not} sinon"
        return Focus(e.key, e.threshold, e.label, why, e)
    except Exception:
        log.debug("focus pick failed", exc_info=True)
        return None


def load_rows(last: int = HISTORY) -> list[dict]:
    try:
        from treeaicoach import progress  # noqa: PLC0415
        return list(progress.collect(last=last) or [])
    except Exception:
        log.debug("win condition history unavailable", exc_info=True)
        return []


_FOCUS_CACHE: dict[str, Any] = {}


def focus_for(role: str | None, loader: Callable[[], list[dict]] | None = None) -> Focus | None:
    """The focus of the next game for this role (cached per role for the session)."""
    k = str(role or "").upper()
    if k not in _FOCUS_CACHE:
        _FOCUS_CACHE[k] = pick_focus((loader or load_rows)(), k)
    return _FOCUS_CACHE[k]


def clear_cache() -> None:
    _FOCUS_CACHE.clear()


def focus_block(f: Focus | None) -> dict[str, Any] | None:
    """JSON block of a focus (stored in the game record)."""
    if f is None:
        return None
    return {"key": f.key, "threshold": f.threshold, "label": f.label, "why": f.why}


def focus_from_block(b: Any) -> Focus | None:
    try:
        if not isinstance(b, dict) or b.get("key") not in CONDITIONS or _f(b.get("threshold")) is None:
            return None
        thr = float(b["threshold"])
        e = Effect(b["key"], thr, label(b["key"], thr), True, 0, 0, 0, 0)
        return Focus(b["key"], thr, str(b.get("label") or e.label), str(b.get("why") or ""), e)
    except Exception:
        return None


# ======================================================================================
# live + post-game
# ======================================================================================
def evaluate(focus: Focus | None, feats: dict[str, Any]) -> tuple[bool, str] | None:
    """(held, French line) for a finished game, None if unknown."""
    if focus is None:
        return None
    metric, op = CONDITIONS[focus.key][0], CONDITIONS[focus.key][1]
    v = _f((feats or {}).get(metric))
    if v is None:
        return None
    ok = _met(op, v, focus.threshold)
    if focus.key == "deaths10":
        val = f"{int(v)} mort{'s' if v > 1 else ''} avant 10:00"
    elif focus.key == "cs10":
        val = f"{int(v)} sbires à 10:00"
    elif focus.key == "first_back":
        val = f"premier retour à {_fmt_t(v)}"
    elif focus.key == "vision15":
        val = f"vision {v:.0f} à 15:00"
    else:
        val = str(v)
    return ok, f"Focus du soir {'tenu' if ok else 'raté'} : {focus.label} ({val})"


class FocusTracker:
    """Follows the focus live (my Live Client scores). ``line`` is the quiet strip text or None."""

    def __init__(self, focus: Focus | None = None) -> None:
        self.focus = focus
        self.line: str | None = None
        self.status = "en cours"
        self._first_back: float | None = None
        self._prev_gold: tuple[float, float] | None = None
        self._deaths = 0
        self._deaths_t = -1e9

    def update(self, gt: float, scores: dict | None, gold: float | None = None) -> str | None:
        self.line = None
        f = self.focus
        if f is None:
            return None
        try:
            sc = scores or {}
            thr = f.threshold
            if f.key == "deaths10":
                d = int(_f(sc.get("deaths")) or 0)
                if d != self._deaths:
                    self._deaths, self._deaths_t = d, gt
                if gt < 600.0:
                    if d > thr:
                        self.status = "raté"
                    # a death before 10:00: the count for one minute (the last allowed one / missed)
                    if d >= 1 and gt - self._deaths_t <= RISK_LINE_S:
                        self.line = f"{f.text} — {d} mort{'s' if d > 1 else ''}"
                elif self.status == "en cours":
                    self.status = "tenu"
            elif f.key == "cs10":
                cs = _f(sc.get("creepScore")) or 0.0
                if gt < 600.0:
                    pace = thr * max(0.0, gt - 90.0) / 510.0        # minions arrive at 1:30
                    if gt >= 240.0 and gt % 120.0 < 40.0 and cs < pace - 6:     # every 2 min, 40 s
                        self.line = f"{f.text} — {int(cs)} (il en faut {int(pace)})"
                elif self.status == "en cours":
                    self.status = "tenu" if cs >= thr else "raté"
            elif f.key == "vision15":
                ws = _f(sc.get("wardScore")) or 0.0
                if gt < 900.0:
                    pace = thr * gt / 900.0
                    if gt >= 360.0 and gt % 180.0 < 40.0 and ws < pace - 2:
                        self.line = f"{f.text} — {ws:.0f} (il en faut {pace:.0f})"
                elif self.status == "en cours":
                    self.status = "tenu" if ws >= thr else "raté"
            elif f.key == "first_back":
                g = _f(gold)
                if g is not None:
                    if (self._prev_gold is not None and self._first_back is None and gt >= BACK_MIN_S
                            and self._prev_gold[1] - g >= BACK_SPEND):
                        self._first_back = gt
                        self.status = "raté" if gt < thr else "tenu"
                    self._prev_gold = (gt, g)
                if self._first_back is None and thr - 90.0 <= gt < thr and (g or 0.0) >= 900.0:
                    self.line = f"{f.text} — attends {_fmt_t(thr)}"       # gold in the pocket: the temptation
                elif self._first_back is None and gt >= thr and self.status == "en cours":
                    self.status = "tenu"
        except Exception:
            log.debug("focus tracker failed", exc_info=True)
            self.line = None
        return self.line


__all__ = ["CONDITIONS", "Effect", "Focus", "FocusTracker", "MIN_GAMES", "champion_rates", "effects",
           "evaluate", "focus_for", "game_features", "label", "pick_focus", "usable"]
