"""Call evaluation loop: was each coach call followed, what followed it, and quiet the useless ones.

Live, every shown macro call (``engine_coaching._macro_show``) is logged in the game record as
``record["calls"]`` = ``[[game_time, kind, text, u, v], ...]`` (``u, v`` = the call's minimap target
or null); the danger / gank alerts (``personal_danger``, ``jungler_approach``, ``roam_approach``,
``collapse``) and the written coach tips (``macro_tip``) are already in ``record["alerts"]``.

Post-game (:func:`evaluate_record`, own record only: ``my_positions`` ~1 / s, snapshots, events,
Tab timeline), each call gets:

* ``followed``: by its intent (:func:`intent_of`) - *retreat* (danger, "Recule", "Reste sous ta
  tour"): I did not walk further from my fountain within :data:`RETREAT_S` (or I was already
  recalling / dead); *goto* ("Va en bas : dragon dans 40 s", a call with a target): I came within
  :data:`GOTO_R` of the target within :data:`GOTO_S`; *recall*: I was in my fountain within
  :data:`RECALL_S`; *stay* ("Ne rentre pas"): I was NOT; *ward*: my ward score rose within
  :data:`WARD_S`. Other calls (no checkable intent) are not counted.
* what followed within :data:`OUTCOME_S`: ``died`` (I died), ``objective`` (my team took an epic
  monster), ``gold`` (team gold difference swing, Tab timeline).
* ``useful`` = followed and I did not die within :data:`OUTCOME_S`.

Across games (:func:`update_stats`, ``games/truth/call_stats.json``, the last :data:`KEEP_GAMES`
games, idempotent per record) a kind is *quiet* when, over >= :data:`MIN_OCC` occurrences, the
player ignored it >= :data:`IGNORED_FRAC`, or when following it preceded clearly more deaths than
ignoring it (>= :data:`WORSE_DEATHS` more, >= :data:`MIN_SIDE` each). Quieting is SLOW and BOUNDED:
one step per game (cooldown x :data:`STEP`), at most x :data:`MAX_SCALE` (danger / gank kinds x
:data:`DANGER_MAX_SCALE`: never muted), and it steps back down the same way once the kind is
followed again. :func:`reset_stats` (Avancé -> Analyse) forgets everything.

Live, :func:`cooldown_scale` gives the factor read by ``macro.MacroPlanner`` (per call kind) and
``alerts.cooldown_for`` (per alert kind / tip intent). Pure Python except the small JSON file; never
raises.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
from pathlib import Path
from typing import Any, Sequence

from treeaicoach.fmtutil import finite_loose as _f

log = logging.getLogger(__name__)

STATS_FILE = "call_stats.json"
SCHEMA = 1
KEEP_GAMES = 40
MIN_OCC = 15
IGNORED_FRAC = 0.8
MIN_SIDE = 5
WORSE_DEATHS = 0.2
STEP = 1.5
MAX_SCALE = 3.0
DANGER_MAX_SCALE = 1.5

RETREAT_S = 12.0
RETREAT_SLACK = 0.02            # uv: standing still / a step forward still counts as "not advancing"
GOTO_S = 45.0
GOTO_R = 0.15
RECALL_S = 50.0
STAY_S = 40.0
WARD_S = 60.0
OUTCOME_S = 45.0
GOLD_S = 60.0

DANGER_KINDS = frozenset({"personal_danger", "jungler_approach", "roam_approach", "collapse"})
TIP_KIND = "macro_tip"
#: macro call kinds -> intent when the call has no target
KIND_INTENT = {"wave_recall": "recall", "back_off": "retreat", "fight_lost": "retreat", "gc_obj_safe": "retreat",
               "gc_obj_stay": "stay"}

_DRAGON = (0.67, 0.70)
_BARON = (0.33, 0.30)
_LANES = {"bas": (0.80, 0.85), "bot": (0.80, 0.85), "haut": (0.18, 0.15), "top": (0.18, 0.15),
          "mid": (0.5, 0.5), "milieu": (0.5, 0.5)}

_RE_RETREAT = re.compile(r"\b(recule|reste (sous|près de) ta tour|arrête de pousser|ne va pas|joue safe|"
                         r"ne contre-attaque)", re.I)
_RE_RECALL = re.compile(r"\brentre\b", re.I)
_RE_STAY = re.compile(r"\bne rentre pas\b", re.I)
_RE_WARD = re.compile(r"\b(balise|pose des balises)", re.I)
_RE_GOTO = re.compile(r"\b(va (en |au |à |vers )?(bas|bot|haut|top|mid|milieu|dragon|baron|héraut)|"
                      r"va au (dragon|baron)|regroupe|prends (le|un) (dragon|baron|objectif))", re.I)


KIND_LABEL = {"personal_danger": "danger (Recule)", "jungler_approach": "jungler qui arrive",
              "roam_approach": "ennemi qui arrive", "collapse": "plusieurs ennemis arrivent",
              "tip:retreat": "« Recule / reste sous ta tour »", "tip:goto": "« Va en bas / en haut »",
              "tip:ward": "balises", "tip:recall": "« Rentre »", "tip:stay": "« Ne rentre pas »",
              "wave_recall": "« Rentre acheter »", "side_wave": "« Change de voie »", "plates": "plaques",
              "rotate_mid": "« Va mid »", "fight_won": "« Combat gagné : objectif »"}


def kind_label(kind: str) -> str:
    """Short French name of a call kind (UI / report)."""
    k = str(kind or "")
    return KIND_LABEL.get(k, f"appel « {k.replace('gc_', '').replace('_', ' ')} »")


def _norm_kind(kind: Any) -> str:
    return str(getattr(kind, "value", kind) or "").lower()


def intent_of(kind: Any, text: str = "", target: Any = None) -> str | None:
    """"retreat" | "goto" | "recall" | "stay" | "ward" | None (not checkable)."""
    k = _norm_kind(kind)
    if k in DANGER_KINDS:
        return "retreat"
    t = str(text or "")
    if k in KIND_INTENT:
        return KIND_INTENT[k]
    if target is not None:
        return "goto"
    if _RE_STAY.search(t):
        return "stay"
    if _RE_RETREAT.search(t):
        return "retreat"
    if _RE_RECALL.search(t):
        return "recall"
    if _RE_WARD.search(t):
        return "ward"
    if _RE_GOTO.search(t) and _text_target(t) is not None:
        return "goto"
    return None


def stat_kind(kind: Any, text: str = "") -> str:
    """The key the stats / cooldowns are kept under: the call kind, or ``tip:<intent>`` for tips."""
    k = _norm_kind(kind)
    if k == TIP_KIND:
        it = intent_of(k, text)
        return f"tip:{it}" if it else TIP_KIND
    return k


def _text_target(text: str) -> tuple[float, float] | None:
    t = text.lower()
    if "dragon" in t:
        return _DRAGON
    if "baron" in t or "héraut" in t:
        return _BARON
    m = re.search(r"\bva (?:en |au |à |vers )?(bas|bot|haut|top|mid|milieu)\b", t)
    if m:
        return _LANES[m.group(1)]
    m = re.search(r"\bgroupée (?:en |au )?(bas|haut|milieu)\b", t)
    return _LANES[m.group(1)] if m else None


# ======================================================================================
# one record
# ======================================================================================
def calls_from_record(record: dict) -> list[dict]:
    """``[{t, kind, text, target, intent}]`` of the shown calls (logged calls + danger alerts + tips)."""
    out: list[dict] = []
    logged: list[tuple[float, str]] = []
    for c in record.get("calls") or []:
        if not isinstance(c, list) or len(c) < 3 or _f(c[0]) is None:
            continue
        u, v = (_f(c[3]), _f(c[4])) if len(c) >= 5 else (None, None)
        tgt = (u, v) if u is not None and v is not None else None
        kind, text = _norm_kind(c[1]), str(c[2] or "")
        logged.append((float(c[0]), text))
        out.append({"t": float(c[0]), "kind": kind, "text": text, "target": tgt,
                    "intent": intent_of(kind, text, tgt)})
    for a in record.get("alerts") or []:
        if not isinstance(a, list) or len(a) < 4 or _f(a[0]) is None:
            continue
        kind, t, text = _norm_kind(a[1]), float(a[0]), str(a[3] or "")
        if kind in DANGER_KINDS:
            out.append({"t": t, "kind": kind, "text": text, "target": None, "intent": "retreat"})
        elif kind == TIP_KIND:
            if any(abs(t - lt) <= 3.0 and lt_text == text for lt, lt_text in logged):
                continue                                   # the alert copy of a logged macro call
            it = intent_of(kind, text)
            if it is None:
                continue
            tgt = _text_target(text) if it == "goto" else None
            out.append({"t": t, "kind": stat_kind(kind, text), "text": text, "target": tgt, "intent": it})
    out.sort(key=lambda c: c["t"])
    return out


class _Track:
    def __init__(self, record: dict) -> None:
        self.pts = sorted((float(p[0]), float(p[1]), float(p[2])) for p in record.get("my_positions") or []
                          if isinstance(p, list) and len(p) >= 3 and None not in (_f(p[0]), _f(p[1]), _f(p[2])))
        self.ts = [p[0] for p in self.pts]
        team = str((record.get("meta") or {}).get("team") or (record.get("summary") or {}).get("team") or "").upper()
        self.team = team if team in ("ORDER", "CHAOS") else "ORDER"
        self.home = (0.04, 0.96) if self.team == "ORDER" else (0.96, 0.04)

    def window(self, t0: float, t1: float) -> list[tuple[float, float, float]]:
        import bisect
        i = bisect.bisect_left(self.ts, t0)
        j = bisect.bisect_right(self.ts, t1)
        return self.pts[i:j]

    def near(self, t: float, tol: float = 4.0) -> tuple[float, float, float] | None:
        w = self.window(t - tol, t + tol)
        return min(w, key=lambda p: abs(p[0] - t)) if w else None

    def home_d(self, p: tuple[float, float, float]) -> float:
        return math.hypot(p[1] - self.home[0], p[2] - self.home[1])


def _deaths(record: dict) -> tuple[list[float], dict]:
    try:
        from treeaicoach import analysis  # noqa: PLC0415

        a = analysis.analyze_game(record)
        return sorted(float(d["game_time"]) for d in a.get("deaths") or []
                      if isinstance(d, dict) and _f(d.get("game_time")) is not None), a
    except Exception:
        log.debug("deaths unavailable", exc_info=True)
        return [], {}


def evaluate_record(record: Any) -> list[dict]:
    """Every checkable call of the game with ``followed`` / ``died`` / ``objective`` / ``gold`` /
    ``useful`` (see the module docstring). [] on any problem."""
    try:
        if not isinstance(record, dict):
            return []
        calls = calls_from_record(record)
        if not calls:
            return []
        tr = _Track(record)
        if len(tr.pts) < 30:
            return []
        deaths, a = _deaths(record)
        objs = [float(i["game_time"]) for i in (a.get("objective_presence") or {}).get("items") or []
                if isinstance(i, dict) and i.get("mine") and _f(i.get("game_time")) is not None]
        snaps = sorted((float(s["game_time"]), _f(s.get("ward_score")), _f(s.get("gold")))
                       for s in record.get("snapshots") or [] if isinstance(s, dict) and _f(s.get("game_time")) is not None)
        tl = sorted((float(r[0]), _f(r[1])) for r in ((record.get("scoreboard") or {}).get("timeline") or [])
                    if isinstance(r, list) and len(r) >= 2 and _f(r[0]) is not None and _f(r[1]) is not None)
        out = []
        for c in calls:
            if c["intent"] is None:
                continue
            t = c["t"]
            if any(0.0 <= t - d <= 15.0 for d in deaths):
                continue                                    # shown while dead / respawning: not a choice
            f = _followed(c, tr, snaps, deaths)
            if f is None:
                continue
            died = any(0.0 < d - t <= OUTCOME_S for d in deaths)
            obj = any(0.0 <= o - t <= GOLD_S for o in objs)
            gold = None
            b = [g for gt, g in tl if gt <= t + 5.0]
            e = [g for gt, g in tl if t + GOLD_S <= gt <= t + GOLD_S + 70.0]
            if b and e:
                gold = round(e[0] - b[-1])
            out.append({"t": round(t, 1), "kind": c["kind"], "text": c["text"][:80], "intent": c["intent"],
                        "followed": f, "died": died, "objective": obj, "gold": gold, "useful": bool(f and not died)})
        return out
    except Exception:
        log.exception("call evaluation failed")
        return []


def _followed(c: dict, tr: _Track, snaps: list, deaths: list[float]) -> bool | None:
    t, it = c["t"], c["intent"]
    p0 = tr.near(t)
    if p0 is None:
        return None
    if it == "retreat":
        if tr.home_d(p0) < 0.12:
            return None                                     # already home: nothing to follow
        # judged before a death (a dead champion jumps to the fountain: that is not a retreat)
        t1 = min([t + RETREAT_S] + [d - 1.0 for d in deaths if t < d <= t + RETREAT_S])
        if t1 - t < 4.0:
            return None
        p1 = tr.near(t1)
        if p1 is None:
            return None
        return tr.home_d(p1) <= tr.home_d(p0) + RETREAT_SLACK
    if it == "goto":
        tgt = c.get("target")
        if tgt is None:
            return None
        d0 = math.hypot(p0[1] - tgt[0], p0[2] - tgt[1])
        if d0 <= GOTO_R:
            return None                                     # already there
        w = tr.window(t, t + GOTO_S)
        if not w:
            return None
        return min(math.hypot(p[1] - tgt[0], p[2] - tgt[1]) for p in w) <= GOTO_R
    if it in ("recall", "stay"):
        if tr.home_d(p0) < 0.12:
            return None
        lim = RECALL_S if it == "recall" else STAY_S
        died = any(0.0 < d - t <= lim for d in deaths)
        home = any(tr.home_d(p) < 0.11 for p in tr.window(t, t + lim))
        if it == "recall":
            return home and not died
        return None if died else not home
    if it == "ward":
        w0 = [ws for gt, ws, _g in snaps if gt <= t and ws is not None]
        w1 = [ws for gt, ws, _g in snaps if t + 10.0 <= gt <= t + WARD_S and ws is not None]
        if not w0 or not w1:
            return None
        return max(w1) > w0[-1] + 0.5
    return None


def summary_line(evals: Sequence[dict]) -> tuple[str, str] | None:
    """("Conseils utiles : 7/9", "Le moins suivi : « Recule ! » (ignoré 8 fois sur 10)") or None."""
    ev = [e for e in evals or [] if isinstance(e, dict)]
    if not ev:
        return None
    useful = sum(1 for e in ev if e.get("useful"))
    head = f"Conseils utiles : {useful}/{len(ev)}"
    by: dict[str, list[dict]] = {}
    for e in ev:
        by.setdefault(str(e.get("kind")), []).append(e)
    worst = None
    for k, xs in by.items():
        bad = sum(1 for e in xs if not e.get("useful"))
        if len(xs) >= 3 and bad / len(xs) >= 0.5 and (worst is None or bad > worst[1]):
            worst = (k, bad, len(xs), xs[0].get("text") or k)
    tail = ""
    if worst is not None:
        tail = f"Le moins utile : « {str(worst[3])[:48]} » (inutile {worst[1]} fois sur {worst[2]})"
    return head, tail


# ======================================================================================
# across games
# ======================================================================================
def _agg_game(evals: Sequence[dict]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for e in evals:
        s = out.setdefault(str(e.get("kind")), {"n": 0, "followed": 0, "useful": 0, "died_f": 0, "died_i": 0})
        s["n"] += 1
        if e.get("followed"):
            s["followed"] += 1
            s["died_f"] += int(bool(e.get("died")))
        else:
            s["died_i"] += int(bool(e.get("died")))
        s["useful"] += int(bool(e.get("useful")))
    return out


def totals(data: dict) -> dict[str, dict[str, int]]:
    tot: dict[str, dict[str, int]] = {}
    for g in (data.get("games") or {}).values():
        for k, s in (g.get("kinds") or {}).items():
            t = tot.setdefault(k, {"n": 0, "followed": 0, "useful": 0, "died_f": 0, "died_i": 0})
            for f in t:
                t[f] += int(s.get(f) or 0)
    return tot


def quiet_reason(s: dict[str, int]) -> str | None:
    """Why a kind should be quieter (None: it is fine)."""
    n, fo = int(s.get("n") or 0), int(s.get("followed") or 0)
    ig = n - fo
    if n >= MIN_OCC and ig / n >= IGNORED_FRAC:
        return f"ignoré {ig} fois sur {n}"
    if fo >= MIN_SIDE and ig >= MIN_SIDE and s["died_f"] / fo - s["died_i"] / ig >= WORSE_DEATHS:
        return f"suivi, il précède plus de morts ({s['died_f']}/{fo} contre {s['died_i']}/{ig})"
    return None


def _cap(kind: str) -> float:
    return DANGER_MAX_SCALE if kind in DANGER_KINDS or kind == "tip:retreat" else MAX_SCALE


def stats_path(truth_dir: Path | str | None = None) -> Path:
    if truth_dir is None:
        from treeaicoach import ground_truth  # noqa: PLC0415
        from treeaicoach.recorder import default_games_dir  # noqa: PLC0415

        truth_dir = default_games_dir() / ground_truth.TRUTH_DIR_NAME
    return Path(truth_dir) / STATS_FILE


def load_stats(truth_dir: Path | str | None = None) -> dict:
    try:
        d = json.loads(stats_path(truth_dir).read_text(encoding="utf-8"))
        if isinstance(d, dict) and d.get("schema") == SCHEMA:
            return d
    except Exception:
        pass
    return {"schema": SCHEMA, "games": {}, "scale": {}}


def update_stats(truth_dir: Path | str | None, game: str, evals: Sequence[dict], order: Any = None) -> dict:
    """Add one game (once per ``game`` name), step each kind's scale ONE notch, write the file."""
    data = load_stats(truth_dir)
    try:
        games = data.setdefault("games", {})
        if game in games:
            return data
        games[game] = {"order": order if order is not None else len(games), "kinds": _agg_game(evals)}
        if len(games) > KEEP_GAMES:
            for k in sorted(games, key=lambda k: (games[k].get("order") or 0, k))[:len(games) - KEEP_GAMES]:
                del games[k]
        scale = data.setdefault("scale", {})
        why = data.setdefault("why", {})
        for k, s in totals(data).items():
            old = float(scale.get(k) or 1.0)
            r = quiet_reason(s)
            new = min(_cap(k), old * STEP) if r else max(1.0, old / STEP)
            new = round(new, 3)
            if new <= 1.0:
                scale.pop(k, None)
                why.pop(k, None)
            else:
                scale[k] = new
                why[k] = r or why.get(k, "")
        from treeaicoach.recorder import atomic_write_text  # noqa: PLC0415

        p = stats_path(truth_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(p, json.dumps(data, ensure_ascii=False, separators=(",", ":")))
        reload()
    except Exception:
        log.exception("call stats update failed")
    return data


def reset_stats(truth_dir: Path | str | None = None) -> bool:
    """Forget all call stats and every quieted kind (Avancé -> Analyse). Never raises."""
    try:
        p = stats_path(truth_dir)
        if p.exists():
            p.unlink()
        reload()
        return True
    except Exception:
        log.exception("call stats reset failed")
        return False


# ----------------------------------------------------------------------------- live scales
_lock = threading.Lock()
_scales: dict[str, float] | None = None
ENABLED = True                   # tests switch it off (conftest)


def reload() -> None:
    global _scales
    with _lock:
        _scales = None


def cooldown_scale(kind: Any, text: str = "") -> float:
    """Cooldown multiplier of a call / alert kind (1.0 = normal), bounded. Never raises."""
    global _scales
    if not ENABLED:
        return 1.0
    try:
        with _lock:
            if _scales is None:
                _scales = {k: float(v) for k, v in (load_stats().get("scale") or {}).items()
                           if _f(v) is not None}
            sc = _scales
        if not sc:
            return 1.0
        k = stat_kind(kind, text)
        return max(1.0, min(_cap(k), float(sc.get(k, 1.0))))
    except Exception:
        return 1.0


def quieted() -> list[tuple[str, float, str]]:
    """[(kind, scale, why)] for the UI."""
    d = load_stats()
    return [(k, float(v), str((d.get("why") or {}).get(k, ""))) for k, v in sorted((d.get("scale") or {}).items())]


__all__ = ["DANGER_KINDS", "calls_from_record", "cooldown_scale", "evaluate_record", "intent_of", "load_stats",
           "quiet_reason", "quieted", "reload", "reset_stats", "stat_kind", "summary_line", "totals", "update_stats"]
