"""Identification of the detected champion icons (ARCHITECTURE.md §4.10).

The Live Client Data API gives the 10 champions of the match (and their skins). For each
of them :meth:`ChampionIdentifier.set_roster` prepares templates of the round portrait:
the inner disc (``INNER_RATIO`` of the icon radius, i.e. without the coloured ring) is
resampled on a canonical ``CANON x CANON`` grid and turned into

* a grey-level vector (zero mean, unit norm) -> normalized cross-correlation (NCC),
* a chroma vector (Lab a/b channels, zero mean, unit norm) -> colour-layout NCC,
* an HSV hue x saturation histogram (zero mean, unit norm) -> histogram correlation.

Templates are also built for a few small shifts of the centre, so the detector's position
error (a few pixels) costs almost nothing at run time: :meth:`ChampionIdentifier.identify`
crops every detection once from the ORIGINAL minimap, computes one matrix product against
all template variants and keeps, per champion, the best variant. A small prior from the
detector's class probabilities (ring colour) versus the champion's side is added, then a
greedy unique assignment (by descending score, minimum score ``MIN_SCORE``) gives each
roster player at most one icon.

The local player's ring is the same light blue as the allies' (docs/MINIMAP_FACTS.md):
``"self"`` is therefore decided by IDENTITY (``game.me`` champion + skin). Fallback when
the local player's portrait is not recognised: the allied icon nearest to the centre of
the white camera rectangle, if that rectangle is found.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Any, Sequence

import cv2
import numpy as np

from treeaicoach.detector import CLASSES, Detection

log = logging.getLogger(__name__)

#: Canonical side (px) of the resampled inner disc.
CANON = 24
#: Inner disc radius / icon radius (excludes the ring and the dark line under it).
INNER_RATIO = 0.72
#: Portrait radius (fraction of the half-side of the square icon image) visible at
#: ``INNER_RATIO``: the portrait fills ~0.81 R (ring 13 % + dark line 6 %) and the visible
#: disc shows ~94 % of the icon image (see render.PORTRAIT_FILL).
ICON_FRAC_AT_INNER = INNER_RATIO / 0.81 * 0.94
#: Gaussian blur (canonical pixels) applied to both templates and crops.
BLUR_SIGMA = 0.9
#: Template centre shifts, in units of the icon radius (absorbs detector position noise).
SHIFTS: tuple[tuple[float, float], ...] = (
    (0.0, 0.0),
    (0.1, 0.0), (-0.1, 0.0), (0.0, 0.1), (0.0, -0.1),
    (0.1, 0.1), (0.1, -0.1), (-0.1, 0.1), (-0.1, -0.1),
)
#: Template scale factors (absorbs radius estimation errors).
SCALES: tuple[float, ...] = (1.0, 0.9)
#: Feature weights (tuned on rendered minimaps, see tests/test_identifier.py).
W_GRAY = 0.45
W_CHROMA = 0.30
W_HIST = 0.25
#: Weight of the detector class prior (ring colour vs champion side).
W_PRIOR = 0.12
#: Minimum combined score to accept an identity.
MIN_SCORE = 0.55
#: Hue x saturation histogram bins.
HUE_BINS = 12
SAT_BINS = 3
#: Radii (px, original image) below this are too small to identify.
MIN_RADIUS_PX = 3.0
#: Part of the inner disc that must lie inside the image.
MIN_VISIBLE = 0.55


@dataclass
class Identified:
    """A detection with its identity (champion) and relation to the local player."""

    det: Detection
    alias: str | None          # recognised champion (None if uncertain)
    relation: str              # "self" | "ally" | "enemy"
    team: str | None           # "ORDER" | "CHAOS" if known
    id_score: float


@dataclass(frozen=True)
class _Member:
    """One roster player."""

    alias: str
    skin_id: int
    relation: str              # "self" | "ally" | "enemy"
    team: str | None


@dataclass(frozen=True)
class _Roster:
    """Immutable snapshot shared between threads (swapped atomically)."""

    members: tuple[_Member, ...]
    feats: np.ndarray          # [n_members * n_variants, D] float32
    n_variants: int
    member_side: np.ndarray    # [n_members] 0 = enemy side, 1 = ally side
    my_team: str | None
    has_me: bool


# ======================================================================================
# Feature extraction
# ======================================================================================

_g = (np.arange(CANON, dtype=np.float32) + 0.5) / CANON * 2.0 - 1.0   # [-1, 1] pixel centres
_GX, _GY = np.meshgrid(_g, _g)
_MASK = (_GX ** 2 + _GY ** 2) <= 1.0
_MASK_IDX = np.nonzero(_MASK.ravel())[0]
_GRAY_W = np.asarray([0.114, 0.587, 0.299], np.float32)   # BGR


def _unit(x: np.ndarray) -> np.ndarray:
    """Zero-mean, unit-norm rows (float32); constant rows -> zeros."""
    x = x - x.mean(axis=-1, keepdims=True)
    n = np.linalg.norm(x, axis=-1, keepdims=True)
    return (x / np.maximum(n, 1e-6)).astype(np.float32)


def _features(crops: Sequence[np.ndarray]) -> np.ndarray:
    """Feature vectors ``[n, D]`` of canonical ``CANON x CANON`` BGR uint8 crops.

    ``dot(f1, f2) = W_GRAY * ncc_gray + W_CHROMA * ncc_chroma + W_HIST * hist_corr``.
    The colour conversions run once on the whole batch (a cv2 call has a fixed cost).
    """
    n = len(crops)
    if BLUR_SIGMA > 0:
        crops = [cv2.GaussianBlur(c, (0, 0), BLUR_SIGMA) for c in crops]
    px = np.stack([c.reshape(-1, 3)[_MASK_IDX] for c in crops])          # [n, P, 3] uint8
    gray = px.astype(np.float32) @ _GRAY_W                               # [n, P]
    lab = cv2.cvtColor(px, cv2.COLOR_BGR2LAB)
    hsv = cv2.cvtColor(px, cv2.COLOR_BGR2HSV)
    chroma = lab[:, :, 1:].astype(np.float32)
    chroma -= chroma.mean(axis=1, keepdims=True)                         # per channel
    chroma = chroma.transpose(0, 2, 1).reshape(n, -1)
    h = hsv[:, :, 0].astype(np.int32) * HUE_BINS // 180
    sat = np.minimum(hsv[:, :, 1].astype(np.int32) * SAT_BINS // 256, SAT_BINS - 1)
    wgt = 0.25 + hsv[:, :, 2].astype(np.float32) / 255.0                 # dark pixels count less
    nb = HUE_BINS * SAT_BINS
    bins = (h * SAT_BINS + sat) + (np.arange(n, dtype=np.int32) * nb)[:, None]
    hist = np.bincount(bins.ravel(), weights=wgt.ravel(), minlength=n * nb).reshape(n, nb)
    hist = np.sqrt(hist.astype(np.float32))                              # less peaky
    return np.concatenate([_unit(gray) * math.sqrt(W_GRAY), _unit(chroma) * math.sqrt(W_CHROMA),
                           _unit(hist) * math.sqrt(W_HIST)], axis=1).astype(np.float32)


def _crop_canon(img: np.ndarray, cx: float, cy: float, radius_px: float,
                dx: float = 0.0, dy: float = 0.0) -> np.ndarray:
    """Inner disc of radius ``radius_px`` around (cx + dx, cy + dy) -> CANON x CANON BGR.

    ``cx``, ``cy`` are continuous coordinates (pixel edges at integers). Outside pixels are
    replicated from the border.
    """
    mx = (cx + dx - 0.5 + _GX * radius_px).astype(np.float32)
    my = (cy + dy - 0.5 + _GY * radius_px).astype(np.float32)
    return cv2.remap(img, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def _icon_bgr(icon_rgba: np.ndarray) -> np.ndarray | None:
    """RGBA icon -> BGR on a dark background (transparent corners), None if invalid."""
    if not isinstance(icon_rgba, np.ndarray) or icon_rgba.ndim != 3 or icon_rgba.shape[2] < 3:
        return None
    if min(icon_rgba.shape[:2]) < 8:
        return None
    rgb = icon_rgba[:, :, :3].astype(np.float32)
    if icon_rgba.shape[2] >= 4:
        a = icon_rgba[:, :, 3:4].astype(np.float32) / 255.0
        rgb = rgb * a + 40.0 * (1.0 - a)
    return np.ascontiguousarray(rgb[:, :, ::-1]).clip(0, 255).astype(np.uint8)


def _template_features(icon_rgba: np.ndarray) -> np.ndarray | None:
    """Feature vectors ``[n_variants, D]`` of one portrait (shifts x scales)."""
    bgr = _icon_bgr(icon_rgba)
    if bgr is None:
        return None
    h, w = bgr.shape[:2]
    half = 0.5 * min(h, w)
    # radius (icon px) of the inner disc, and of the whole icon ring radius R
    r_inner = ICON_FRAC_AT_INNER * half
    R = r_inner / INNER_RATIO
    # pre-shrink so that the canonical sampling is ~1:1 (area filter = realistic blur)
    k = r_inner / (CANON / 2.0)
    if k > 1.3:
        nw, nh = max(8, int(round(w / k))), max(8, int(round(h / k)))
        bgr = cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_AREA)
        sx, sy = nw / w, nh / h
    else:
        sx = sy = 1.0
    cx, cy = 0.5 * w * sx, 0.5 * h * sy
    crops = [_crop_canon(bgr, cx, cy, r_inner * sc * sx, ox * R * sx, oy * R * sy)
             for sc in SCALES for ox, oy in SHIFTS]
    return _features(crops)


# ======================================================================================
# Camera rectangle (fallback for "self")
# ======================================================================================


def _line_runs(profile: np.ndarray, min_count: float) -> list[tuple[float, float]]:
    """Centres and strengths of runs of consecutive indices where ``profile >= min_count``."""
    on = profile >= min_count
    runs: list[tuple[float, float]] = []
    i, n = 0, profile.size
    while i < n:
        if on[i]:
            j = i
            while j + 1 < n and on[j + 1]:
                j += 1
            if j - i <= 4:           # a line is thin; wider runs are white areas
                runs.append((0.5 * (i + j), float(profile[i:j + 1].max())))
            i = j + 1
        else:
            i += 1
    return runs[:24]


def _best_pair(runs: list[tuple[float, float]], lo: float, hi: float
               ) -> tuple[float, float] | None:
    """Pair of runs whose distance lies in [lo, hi], strongest first."""
    best, best_s = None, -1.0
    for a in range(len(runs)):
        for b in range(a + 1, len(runs)):
            gap = runs[b][0] - runs[a][0]
            if lo <= gap <= hi and runs[a][1] + runs[b][1] > best_s:
                best, best_s = (runs[a][0], runs[b][0]), runs[a][1] + runs[b][1]
    return best


def find_camera_center(minimap_bgr: np.ndarray) -> tuple[float, float] | None:
    """Centre (u, v) of the white camera rectangle, or None if not found. Never raises.

    White, unsaturated pixels -> long horizontal / vertical segments (morphological
    opening) -> a pair of rows ~0.155 W apart and a pair of columns ~0.275 W apart whose
    segments actually bound the same rectangle. Icons drawn over the lines are tolerated.
    """
    try:
        if not isinstance(minimap_bgr, np.ndarray) or minimap_bgr.ndim != 3:
            return None
        H, W = minimap_bgr.shape[:2]
        if min(H, W) < 48 or minimap_bgr.shape[2] < 3:
            return None
        img = minimap_bgr[:, :, :3]
        mn = img.min(axis=2)
        mx = img.max(axis=2)
        mask = ((mn >= 185) & ((mx - mn) <= 45)).astype(np.uint8)
        if W > 420:   # keep thin lines: downscale the mask, not the image
            sh = max(1, int(round(H * 320.0 / W)))
            mask = (cv2.resize(mask, (320, sh), interpolation=cv2.INTER_AREA) > 0).astype(np.uint8)
        h_, w_ = mask.shape
        if int(mask.sum()) < 0.25 * w_:
            return None
        L = max(4, int(0.07 * w_))
        horiz = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((1, L), np.uint8))
        vert = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((max(3, int(0.05 * w_)), 1),
                                                              np.uint8))
        rows = _line_runs(horiz.sum(axis=1).astype(np.float32), L)
        cols = _line_runs(vert.sum(axis=0).astype(np.float32), max(3, int(0.05 * w_)))
        pr = _best_pair(rows, 0.11 * w_, 0.20 * w_)
        pc = _best_pair(cols, 0.22 * w_, 0.33 * w_)
        if pr is None or pc is None:
            return None
        (r1, r2), (c1, c2) = pr, pc
        ya, yb = int(round(r1)), int(round(r2)) + 1
        xa, xb = int(round(c1)), int(round(c2)) + 1
        # the segments must bound this rectangle (>= 35 % of each side visible)
        top = horiz[int(round(r1)), xa:xb].mean() if xb > xa else 0.0
        bot = horiz[int(round(r2)), xa:xb].mean() if xb > xa else 0.0
        left = vert[ya:yb, int(round(c1))].mean() if yb > ya else 0.0
        right = vert[ya:yb, int(round(c2))].mean() if yb > ya else 0.0
        if min(top, bot) < 0.35 or min(left, right) < 0.35:
            return None
        return (0.5 * (c1 + c2) + 0.5) / w_, (0.5 * (r1 + r2) + 0.5) / h_
    except Exception:
        log.debug("Camera rectangle search failed", exc_info=True)
        return None


# ======================================================================================
# Identifier
# ======================================================================================


def _side_of(relation: str) -> int:
    return 0 if relation == "enemy" else 1


def _other_team(team: str | None) -> str | None:
    return {"ORDER": "CHAOS", "CHAOS": "ORDER"}.get(team or "")


class ChampionIdentifier:
    """Recognises the roster champions among the detections. Thread-safe; never raises."""

    def __init__(self, db: Any) -> None:
        self.db = db
        self._lock = threading.Lock()
        self._roster: _Roster | None = None
        self._roster_key: tuple | None = None
        self._last_error = -math.inf

    # ------------------------------------------------------------------ roster
    def _roster_signature(self, game: Any) -> tuple:
        """What the templates depend on (players, skins, availability of skin icons)."""
        sig = []
        me = getattr(game, "me", None)
        players = ([(me, "self")] if me is not None else []) + \
            [(p, "ally") for p in (getattr(game, "allies", None) or [])] + \
            [(p, "enemy") for p in (getattr(game, "enemies", None) or [])]
        for p, rel in players:
            alias = str(getattr(p, "champion_alias", "") or "")
            skin = int(getattr(p, "skin_id", 0) or 0)
            cached = False
            if skin > 0:
                try:
                    path = self.db.cached_icon_path(alias, skin)
                    cached = bool(path is not None and path.is_file())
                except Exception:
                    cached = False
            sig.append((alias, skin, rel, str(getattr(p, "team", "") or ""), cached))
        return tuple(sig)

    def set_roster(self, game: Any) -> None:
        """Prepare the templates of the match's champions (idempotent). Never raises.

        ``game`` None clears the roster (identify then only uses the detector classes).
        """
        try:
            if game is None:
                with self._lock:
                    self._roster, self._roster_key = None, None
                return
            key = self._roster_signature(game)
            with self._lock:
                if key == self._roster_key:
                    return
            roster = self._build_roster(game, key)
            with self._lock:
                self._roster, self._roster_key = roster, key
        except Exception:
            log.exception("set_roster failed")

    def _build_roster(self, game: Any, key: tuple) -> _Roster | None:
        me = getattr(game, "me", None)
        my_team = str(getattr(me, "team", "") or "") or None if me is not None else None
        members: list[_Member] = []
        feats: list[np.ndarray] = []
        t0 = time.perf_counter()
        for alias, skin, rel, team, _ in key:
            if not alias:
                continue
            try:
                icon = self.db.load_icon(alias, skin)
                if icon is None and skin:
                    icon = self.db.load_icon(alias, 0)
            except Exception:
                log.debug("load_icon(%s, %s) failed", alias, skin, exc_info=True)
                icon = None
            f = _template_features(icon) if icon is not None else None
            if f is None:
                log.warning("No icon for champion %r: it cannot be identified", alias)
                continue
            members.append(_Member(alias=alias, skin_id=skin, relation=rel, team=team or None))
            feats.append(f)
        if not members:
            return _Roster(members=(), feats=np.zeros((0, 1), np.float32), n_variants=1,
                           member_side=np.zeros(0, np.int8), my_team=my_team,
                           has_me=me is not None)
        nv = feats[0].shape[0]
        log.info("Identifier roster: %d champions (%.0f ms)", len(members),
                 1000 * (time.perf_counter() - t0))
        return _Roster(members=tuple(members), feats=np.concatenate(feats).astype(np.float32),
                       n_variants=nv,
                       member_side=np.asarray([_side_of(m.relation) for m in members], np.int8),
                       my_team=my_team, has_me=me is not None)

    @property
    def roster_aliases(self) -> list[str]:
        """Aliases of the champions that can currently be identified."""
        r = self._roster
        return [m.alias for m in r.members] if r is not None else []

    # ------------------------------------------------------------------ identify
    def identify(self, minimap_bgr: np.ndarray, detections: Sequence[Detection]
                 ) -> list[Identified]:
        """Identify each detection (same order as ``detections``). Never raises."""
        dets = [d for d in (detections or []) if d is not None]
        try:
            return self._identify(minimap_bgr, dets)
        except Exception:
            now = time.monotonic()
            if now - self._last_error > 30.0:
                self._last_error = now
                log.exception("identify failed")
            return [self._fallback(d, None) for d in dets]

    @staticmethod
    def _fallback(d: Detection, roster: _Roster | None) -> Identified:
        """Identity-less result: relation from the detector class."""
        cls = d.cls if d.cls in CLASSES else "enemy"
        if roster is not None and roster.has_me:
            relation = "enemy" if cls == "enemy" else "ally"   # "self" only by identity
            team = roster.my_team if relation == "ally" else _other_team(roster.my_team)
        else:
            relation, team = cls, None
        return Identified(det=d, alias=None, relation=relation, team=team, id_score=0.0)

    def _identify(self, img: Any, dets: list[Detection]) -> list[Identified]:
        roster = self._roster
        out = [self._fallback(d, roster) for d in dets]
        if not dets:
            return out
        if not isinstance(img, np.ndarray) or img.ndim not in (2, 3) or min(img.shape[:2]) < 8:
            return out
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        elif img.shape[2] == 4:
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        elif img.shape[2] != 3:
            return out
        if img.dtype != np.uint8:
            img = np.clip(img, 0, 255).astype(np.uint8)
        if roster is not None and roster.members:
            self._match(img, dets, roster, out)
        if roster is not None and roster.has_me and not any(o.relation == "self" for o in out):
            self._camera_fallback(img, out, roster)
        return out

    def _match(self, img: np.ndarray, dets: list[Detection], roster: _Roster,
               out: list[Identified]) -> None:
        H, W = img.shape[:2]
        rows: list[int] = []
        qf: list[np.ndarray] = []
        for i, d in enumerate(dets):
            u, v, r = float(d.u), float(d.v), float(d.r)
            if not (math.isfinite(u) and math.isfinite(v) and math.isfinite(r)):
                continue
            if not (-0.05 <= u <= 1.05 and -0.05 <= v <= 1.05):
                continue
            R = r * W
            if R < MIN_RADIUS_PX or R > 0.5 * W:
                continue
            cx, cy, ri = u * W, v * H, INNER_RATIO * R
            # fraction of the inner disc inside the image (partially out-of-frame icons)
            vis_x = min(1.0, max(0.0, (min(cx + ri, W) - max(cx - ri, 0.0)) / (2 * ri)))
            vis_y = min(1.0, max(0.0, (min(cy + ri, H) - max(cy - ri, 0.0)) / (2 * ri)))
            if vis_x * vis_y < MIN_VISIBLE:
                continue
            qf.append(_crop_canon(img, cx, cy, ri))
            rows.append(i)
        if not rows:
            return
        nm, nv = len(roster.members), roster.n_variants
        # one matrix-vector product per crop: BLAS gemm spawns threads that are very slow
        # on a busy CPU, gemv stays single-threaded and fast
        Q = _features(qf)
        S = np.stack([(roster.feats @ q).reshape(nm, nv).max(axis=1) for q in Q])  # [n, m]
        # class prior: probability that the ring colour matches the champion's side
        probs = np.asarray([dets[i].cls_probs for i in rows], np.float32).reshape(len(rows), -1)
        if probs.shape[1] >= 3:
            p_enemy = probs[:, 0]
            p_ally = probs[:, 1] + probs[:, 2]
            p_side = np.where(roster.member_side[None, :] == 0, p_enemy[:, None], p_ally[:, None])
            S = S + W_PRIOR * (p_side - 0.5) * 2.0
        # greedy unique assignment
        flat = np.argsort(-S, axis=None)
        used_d: set[int] = set()
        used_m: set[int] = set()
        for idx in flat:
            a, m = divmod(int(idx), nm)
            s = float(S[a, m])
            if s < MIN_SCORE:
                break
            if a in used_d or m in used_m:
                continue
            used_d.add(a)
            used_m.add(m)
            mem = roster.members[m]
            team = mem.team or (roster.my_team if mem.relation != "enemy"
                                else _other_team(roster.my_team))
            out[rows[a]] = Identified(det=dets[rows[a]], alias=mem.alias, relation=mem.relation,
                                      team=team, id_score=min(1.0, max(0.0, s)))
            if len(used_d) == len(rows) or len(used_m) == nm:
                break

    @staticmethod
    def _camera_fallback(img: np.ndarray, out: list[Identified], roster: _Roster) -> None:
        """No identity match for me: the unnamed ally nearest to the camera centre is "self"."""
        cand = [k for k, o in enumerate(out) if o.alias is None and o.relation == "ally"]
        if not cand:
            return
        centre = find_camera_center(img)
        if centre is None:
            return
        cu, cv_ = centre
        k = min(cand, key=lambda j: (out[j].det.u - cu) ** 2 + (out[j].det.v - cv_) ** 2)
        d = out[k].det
        if math.hypot(d.u - cu, d.v - cv_) > 0.12:
            return
        out[k] = Identified(det=d, alias=None, relation="self", team=roster.my_team,
                            id_score=0.0)


__all__ = ["Identified", "ChampionIdentifier", "find_camera_center", "INNER_RATIO", "CANON"]
