"""Progress over the last games (Analyses page, "Progrès" tab).

Pure module (no Tk): :func:`game_metrics` turns one game record (+ optional League Client
ground truth) into a few numbers, :func:`collect` reads the last N recorded games (with a
small cache next to the records so that opening the page stays instant), :func:`trends`
summarises each metric and :func:`focus_points` writes "tes 3 points à travailler" in plain
French. :func:`sparkline` draws a compact PIL chart (docs/DESIGN.md colours).

Metrics (None when unknown): ``cs_per_min``, ``deaths``, ``precision`` (rated plays, plays.py),
``gold_diff10`` / ``gold_diff15``
(vs the lane opponent, League Client only), ``vision_per_min``, ``ganks`` /
``ganks_survived``, ``deaths_after_alert``, ``reliability`` (TreeAI's own score 0-100,
League Client only). Never raises.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Iterable, Sequence

from PIL import Image, ImageDraw

log = logging.getLogger(__name__)

CACHE_NAME = "progress_cache.json"
CACHE_VERSION = 3

# docs/DESIGN.md (RGB)
BG = (12, 14, 13)
LINE = (34, 39, 37)
MUTED = (139, 148, 143)
DIM = (89, 97, 92)
ACCENT = (155, 216, 74)
DANGER = (229, 72, 77)
WARNING = (232, 162, 58)

#: key -> (label, unit, higher is better, decimals)
METRICS: dict[str, tuple[str, str, bool, int]] = {
    "cs_per_min": ("CS / min", "", True, 1),
    "deaths": ("Morts", "", False, 0),
    "gold_diff10": ("Or à 10 min", "PO", True, 0),
    "gold_diff15": ("Or à 15 min", "PO", True, 0),
    "vision_per_min": ("Vision / min", "", True, 2),
    "precision": ("Précision des coups", "", True, 0),
    "reliability": ("Fiabilité TreeAI", "%", True, 0),
}
CS_TARGET = {"TOP": 7.0, "MIDDLE": 7.5, "BOTTOM": 8.0, "JUNGLE": 5.5}
VISION_TARGET = {"UTILITY": 1.6}
VISION_TARGET_DEFAULT = 0.7


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def fmt_num(v: Any, decimals: int = 1, signed: bool = False) -> str:
    x = _f(v)
    if x is None:
        return "-"
    s = f"{x:+.{decimals}f}" if signed else f"{x:.{decimals}f}"
    return s.replace(".", ",").replace("-", "−")


# ======================================================================================
# one game
# ======================================================================================
def game_metrics(record: Any, truth: Any = None) -> dict[str, Any] | None:
    """Numbers of one recorded game (None if the record is unusable)."""
    try:
        if not isinstance(record, dict):
            return None
        from treeaicoach import analysis  # noqa: PLC0415

        a = analysis.analyze_game(record)
        s = a.get("summary") or {}
        dur = _f(s.get("duration")) or 0.0
        if dur < 300:           # remakes / very short records: not meaningful
            return None
        out: dict[str, Any] = {
            "start": s.get("start"), "champion": s.get("champion"), "champion_name": s.get("champion_name"),
            "position": str(s.get("position") or "").upper(), "result": s.get("result"),
            "duration": dur,
            "cs_per_min": _f(s.get("cs_per_min")),
            "deaths": _f(s.get("deaths")),
            "vision_per_min": _f(s.get("vision_per_min")),
            "ganks": int(a.get("ganks_faced") or 0),
            "ganks_survived": int(a.get("ganks_survived") or 0),
            "deaths_after_alert": len(a.get("deaths_warned") or []) if isinstance(a.get("deaths_warned"), list)
            else int(a.get("deaths_warned") or 0),
            "deaths_unwarned": len(a.get("deaths_unwarned") or []) if isinstance(a.get("deaths_unwarned"), list)
            else int(a.get("deaths_unwarned") or 0),
            "gold_diff10": None, "gold_diff15": None, "reliability": None, "precision": None,
        }
        try:
            from treeaicoach import plays  # noqa: PLC0415

            ps = plays.summary_from_record(record)
            if ps and ps.get("total"):
                out["precision"] = _f(ps.get("precision"))
        except Exception:
            log.debug("no rated plays in this record", exc_info=True)
        if isinstance(truth, dict) and truth:
            from treeaicoach import ground_truth  # noqa: PLC0415

            t = ground_truth.analyze_truth(record, truth)
            if t.get("available"):
                lane = t.get("lane") or {}
                out["gold_diff10"] = _f(lane.get("gold_diff10"))
                out["gold_diff15"] = _f(lane.get("gold_diff15"))
                rel = t.get("reliability") or {}
                out["reliability"] = _f(rel.get("grade"))
        return out
    except Exception:
        log.exception("progress.game_metrics failed")
        return None


# ======================================================================================
# many games (with a cache keyed by file name + mtime)
# ======================================================================================
def _record_files(games_dir: Path) -> list[Path]:
    try:
        files = [p for p in games_dir.glob("*.json")
                 if not p.name.endswith(".partial.json") and not p.name.endswith(".truth.json")
                 and p.name != CACHE_NAME]
    except OSError:
        return []
    return sorted(files, key=lambda p: p.name)


def collect(games_dir: Path | None = None, last: int = 20) -> list[dict[str, Any]]:
    """Metrics of the last ``last`` games, oldest first. Uses / refreshes ``progress_cache.json``."""
    try:
        if games_dir is None:
            from treeaicoach import paths  # noqa: PLC0415

            games_dir = paths.user_data_dir() / "games"
        games_dir = Path(games_dir)
        files = _record_files(games_dir)[-max(1, int(last)) * 2:]
        cache_p = games_dir / CACHE_NAME
        try:
            cache = json.loads(cache_p.read_text(encoding="utf-8"))
            if cache.get("version") != CACHE_VERSION:
                cache = {}
        except (OSError, ValueError, AttributeError):
            cache = {}
        entries: dict[str, Any] = dict(cache.get("games") or {}) if isinstance(cache, dict) else {}
        changed = False
        rows: list[dict[str, Any]] = []
        for p in files:
            try:
                st = p.stat()
                sig = f"{int(st.st_mtime)}-{st.st_size}"
            except OSError:
                continue
            truth_p = games_dir / "truth" / (p.name[:-len(".json")] + ".truth.json")
            if truth_p.is_file():
                try:
                    sig += f"-t{int(truth_p.stat().st_mtime)}"
                except OSError:
                    pass
            hit = entries.get(p.name)
            if isinstance(hit, dict) and hit.get("sig") == sig:
                m = hit.get("m")
            else:
                try:
                    rec = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    rec = None
                truth = None
                if truth_p.is_file():
                    try:
                        truth = json.loads(truth_p.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        truth = None
                m = game_metrics(rec, truth)
                entries[p.name] = {"sig": sig, "m": m}
                changed = True
            if isinstance(m, dict):
                rows.append(dict(m, file=p.name))
        if changed:
            keep = {p.name for p in files}
            entries = {k: v for k, v in entries.items() if k in keep}
            try:
                tmp = cache_p.with_suffix(".tmp")
                tmp.write_text(json.dumps({"version": CACHE_VERSION, "games": entries}), encoding="utf-8")
                tmp.replace(cache_p)
            except OSError:
                log.debug("progress cache not written", exc_info=True)
        return rows[-max(1, int(last)):]
    except Exception:
        log.exception("progress.collect failed")
        return []


# ======================================================================================
# trends and focus points
# ======================================================================================
def _mean(xs: Iterable[float]) -> float | None:
    v = [x for x in xs if x is not None]
    return sum(v) / len(v) if v else None


def trends(rows: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per metric: values (oldest first), avg, last, recent (3 last) vs before, direction."""
    out: dict[str, dict[str, Any]] = {}
    for key, (label, unit, higher, dec) in METRICS.items():
        vals = [_f(r.get(key)) for r in rows]
        known = [v for v in vals if v is not None]
        if not known:
            out[key] = {"label": label, "unit": unit, "values": vals, "avg": None, "last": None,
                        "delta": None, "direction": "flat", "better": None, "decimals": dec}
            continue
        recent = _mean(known[-3:])
        before = _mean(known[:-3]) if len(known) > 3 else None
        delta = (recent - before) if (recent is not None and before is not None) else None
        tol = {"cs_per_min": 0.3, "deaths": 0.7, "gold_diff10": 150, "gold_diff15": 200,
               "vision_per_min": 0.08, "reliability": 4, "precision": 4}.get(key, 0.0)
        direction = "flat" if delta is None or abs(delta) < tol else ("up" if delta > 0 else "down")
        better = None if direction == "flat" else ((direction == "up") == higher)
        out[key] = {"label": label, "unit": unit, "values": vals, "avg": _mean(known), "last": known[-1],
                    "delta": delta, "direction": direction, "better": better, "decimals": dec}
    return out


def _main_role(rows: Sequence[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for r in rows:
        p = str(r.get("position") or "")
        if p:
            counts[p] = counts.get(p, 0) + 1
    return max(counts, key=lambda k: counts[k]) if counts else ""


def focus_points(rows: Sequence[dict[str, Any]], n: int = 3) -> list[tuple[str, str]]:
    """Up to ``n`` (title, advice) pairs, the most important first, in simple French."""
    if not rows:
        return []
    tr = trends(rows)
    role = _main_role(rows)
    cand: list[tuple[float, str, str]] = []
    games = len(rows)

    deaths = tr["deaths"]["avg"]
    warned = sum(int(r.get("deaths_after_alert") or 0) for r in rows)
    if deaths is not None and deaths >= 4.5:
        extra = (f" {warned} de tes morts sont arrivées juste après une alerte : recule dès l'annonce."
                 if warned >= max(2, games // 2) else " Avant d'avancer, regarde où est le jungler adverse.")
        cand.append((deaths - 3.0, "Meurs moins", f"{fmt_num(deaths, 1)} morts par partie en moyenne.{extra}"))
    elif warned >= max(2, games):
        cand.append((1.5, "Écoute les alertes", f"{warned} morts juste après une alerte sur {games} parties : "
                                                "recule dès l'annonce de gank."))

    cs = tr["cs_per_min"]["avg"]
    target = CS_TARGET.get(role)
    if cs is not None and target is not None and cs < target - 0.3:
        down = " Et ça baisse sur tes dernières parties." if tr["cs_per_min"]["direction"] == "down" else ""
        cand.append(((target - cs) * 1.2, "Farme plus",
                     f"{fmt_num(cs, 1)} CS/min, vise {fmt_num(target, 1)}. Ne rate pas les sbires sous ta tour "
                     f"et rentre après une vague poussée.{down}"))

    g10 = tr["gold_diff10"]["avg"]
    if g10 is not None and g10 < -150:
        cand.append((min(3.0, -g10 / 300), "Phase de voie",
                     f"{fmt_num(g10, 0, signed=True)} PO à 10 min contre ton adversaire en moyenne. Joue plus "
                     "prudent avant le niveau 6 et prends les sbires sûrs."))
    g15 = tr["gold_diff15"]["avg"]
    if g15 is not None and g15 < -300 and (g10 is None or g10 >= -150):
        cand.append((min(3.0, -g15 / 450), "Milieu de partie",
                     f"{fmt_num(g15, 0, signed=True)} PO à 15 min : tu perds de l'avance après la voie. "
                     "Groupe-toi pour les objectifs au lieu de farmer seul."))

    vis = tr["vision_per_min"]["avg"]
    vt = VISION_TARGET.get(role, VISION_TARGET_DEFAULT)
    if vis is not None and vis < vt * 0.8:
        cand.append(((vt - vis) / vt * 2.0, "Plus de vision",
                     f"{fmt_num(vis, 2)} de vision par minute, vise {fmt_num(vt, 1)}. Achète une balise de "
                     "contrôle à chaque retour en base."))

    ganks = sum(int(r.get("ganks") or 0) for r in rows)
    surv = sum(int(r.get("ganks_survived") or 0) for r in rows)
    if ganks >= 3 and surv / ganks < 0.6:
        cand.append(((0.6 - surv / ganks) * 4, "Ganks",
                     f"Tu survis à {surv} ganks sur {ganks}. Quand le jungler adverse n'est pas visible "
                     "depuis 30 s, reste près de ta tour."))

    prec = tr.get("precision", {}).get("avg")
    if prec is not None and prec < 60:
        cand.append(((60 - prec) / 15, "Précision des coups",
                     f"{fmt_num(prec, 0)}/100 en moyenne : relis les « gaffes » et « erreurs » du rapport "
                     "pour voir ce qui revient."))

    cand.sort(key=lambda c: -c[0])
    out = [(t, a) for _s, t, a in cand[:n]]
    if len(out) < n:      # nothing big to fix: keep the good habits
        good = []
        if cs is not None and target is not None and cs >= target - 0.3:
            good.append(("Garde ton farm", f"{fmt_num(cs, 1)} CS/min : c'est bien, continue."))
        if deaths is not None and deaths < 4.5:
            good.append(("Bonne survie", f"{fmt_num(deaths, 1)} morts par partie : continue à respecter les "
                                         "alertes."))
        if vis is not None and vis >= vt * 0.8:
            good.append(("Bonne vision", f"{fmt_num(vis, 2)} de vision par minute."))
        for g in good:
            if len(out) >= n:
                break
            out.append(g)
    return out


# ======================================================================================
# sparkline
# ======================================================================================
def sparkline(values: Sequence[Any], width: int = 180, height: int = 44, color: tuple[int, int, int] = ACCENT,
              baseline: float | None = None, bg: tuple[int, int, int] = BG, ss: int = 3) -> Image.Image:
    """Compact line chart (RGB): missing values break the line, last point marked, optional baseline."""
    W, H = max(20, width) * ss, max(12, height) * ss
    im = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(im)
    pts = [(i, _f(v)) for i, v in enumerate(values)]
    known = [v for _i, v in pts if v is not None]
    pad = 5 * ss
    if not known:
        d.line((pad, H / 2, W - pad, H / 2), fill=LINE, width=ss)
        return im.resize((W // ss, H // ss), Image.LANCZOS)
    lo, hi = min(known), max(known)
    if baseline is not None:
        lo, hi = min(lo, baseline), max(hi, baseline)
    if hi - lo < 1e-9:
        lo, hi = lo - 1, hi + 1
    n = max(1, len(values) - 1)

    def xy(i: int, v: float) -> tuple[float, float]:
        return pad + (W - 2 * pad) * (i / n if len(values) > 1 else 0.5), \
            pad + (H - 2 * pad) * (1 - (v - lo) / (hi - lo))

    if baseline is not None:
        y = xy(0, baseline)[1]
        x = pad
        while x < W - pad:          # dotted baseline
            d.line((x, y, min(W - pad, x + 3 * ss), y), fill=DIM, width=ss)
            x += 7 * ss
    seg: list[tuple[float, float]] = []
    for i, v in pts:
        if v is None:
            if len(seg) > 1:
                d.line(seg, fill=color, width=2 * ss, joint="curve")
            seg = []
            continue
        seg.append(xy(i, v))
    if len(seg) > 1:
        d.line(seg, fill=color, width=2 * ss, joint="curve")
    for i, v in pts:
        if v is not None:
            x, y = xy(i, v)
            r = 1.6 * ss
            d.ellipse((x - r, y - r, x + r, y + r), fill=color)
    li, lv = next(((i, v) for i, v in reversed(pts) if v is not None))
    x, y = xy(li, lv)
    r = 3.2 * ss
    d.rectangle((x - r, y - r, x + r, y + r), fill=color)
    return im.resize((W // ss, H // ss), Image.LANCZOS)


def metric_color(tr: dict[str, Any]) -> tuple[int, int, int]:
    """Colour of a metric's sparkline: green when improving, red when worse, grey when flat."""
    b = tr.get("better")
    return ACCENT if b is True else DANGER if b is False else MUTED


__all__ = ["METRICS", "game_metrics", "collect", "trends", "focus_points", "sparkline", "metric_color", "fmt_num"]
