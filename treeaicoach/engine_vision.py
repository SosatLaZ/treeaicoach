"""Vision stage of a tick: my icon / HUD reading, identity stabilisation (jumps, swaps,
duplicates, camera "self" fallback), tracker update, gank threat, personal danger, sample collection.

Mixin of :class:`treeaicoach.engine.CoachEngine` (split out of ``engine.py`` without any
behaviour change): the methods use the engine's state (``self._lock``, ``self._cfg`` ...),
created in ``CoachEngine.__init__``. Not meant to be used on its own.
"""

from __future__ import annotations

import dataclasses
import logging
import math
import time
from typing import Any

import cv2
import numpy as np

from treeaicoach.alerts import Alert, AlertKind, Level, make_alert
from treeaicoach.capture import Rect, is_black_frame
from treeaicoach.engine_base import (
    CAMERA_SELF_MAX_DIST,
    COLLECT_MAX_FILES,
    DUP_DIST,
    DUP_KEEP_ID_SCORE,
    GANK_KINDS,
    IDENTITY_SWAP_DIST,
    IDENTITY_SWAP_HIDDEN_S,
    IDENTITY_SWAP_RECENT_S,
    JUMP_CHECK_S,
    JUMP_SLACK,
    JUMP_SPEED,
    RELABEL_DIST,
    RELABEL_RECENT_S,
    SELF_ICON_PERIOD_S,
    STICKY_SELF_DIST,
    STICKY_SELF_S,
    THREAT_HOLD_S,
    _PassThroughIdentifier,
    find_camera_center,
)
from treeaicoach.live_client import GameInfo

log = logging.getLogger("treeaicoach.engine")   # same logger as before the split

#: HUD portrait calibration (one full-window grab + search) retried after a failure this late (s),
#: doubled at each new failure up to HUD_CAL_RETRY_MAX_S.
HUD_CAL_RETRY_S = 15.0
HUD_CAL_RETRY_MAX_S = 120.0

#: Camera-centre fallback for "self": when my track was seen this recently, the chosen ally
#: icon must be within CAMERA_SELF_REACH + JUMP_SPEED x elapsed of my last position.
CAMERA_SELF_MEMORY_S = 20.0
CAMERA_SELF_REACH = 0.06


class VisionMixin:
    """Vision stage of a tick: my icon / HUD reading, identity stabilisation (jumps, swaps,"""

    def _vision(self, frame: np.ndarray) -> list[Any]:
        """Detector + identifier (+ camera fallback for "self")."""
        try:
            self._ensure_detector()
            status = getattr(self._detector, "set_game_status", None)
            if callable(status):        # dead champions: never searched on the map
                status(self._game)
            dets = list(self._detector.detect(frame) or [])
        except Exception:
            self._errors += 1
            self._err.exception("Detector failed")
            return []
        try:
            identified = list(self._identifier.identify(frame, dets) or [])
        except Exception:
            self._errors += 1
            self._err.exception("Identifier failed")
            identified = _PassThroughIdentifier().identify(frame, dets)
        if identified and not any(getattr(x, "relation", None) == "self" for x in identified):
            self._camera_self_fallback(frame, identified)
        return identified

    # ------------------------------------------------------------------ my icon / HUD
    def _icon_learner(self) -> Any:
        """The roster matcher's icon learner (self_icon.IconLearner), or None."""
        return getattr(getattr(self._detector, "matcher", None), "learner", None)

    def my_observed_lane(self) -> str | None:
        """Lane ("top" / "mid" / "bot") where MY icon was seen laning (1:30-10:00), from the
        icon learner (works with custom skins), or None. Never raises."""
        try:
            lr = self._icon_learner()
            return lr.observed_lane() if lr is not None else None
        except Exception:
            return None

    def _self_icon_tick(self, t: float, gt: float, game: Any) -> None:
        """2 Hz: HUD portrait (dead flag, skin guess), dead players and my lane occupancy for
        the icon learner; hooks my observed lane into roles.RoleResolver. Never raises."""
        if t < self._selficon_next and t >= self._selficon_next - 1.0:
            return
        self._selficon_next = t + SELF_ICON_PERIOD_S
        try:
            matcher = getattr(self._detector, "matcher", None)
            lr = getattr(matcher, "learner", None)
            if lr is None:
                return
            hud = self._read_hud(t, game) if self._frame_source is None else None
            if hud is not None:
                lr.feed_hud(hud.portrait, hud.dead)
                if lr.skin_guesser is None and not self._demo:
                    from treeaicoach.self_icon import SkinGuesser

                    lr.skin_guesser = SkinGuesser(self._champion_db(), allow_network=bool(
                        getattr(self._cfg, "download_skin_icons", True)))
            matcher.set_status(game, me_dead=hud.dead if hud is not None else None, game_time=gt)
            lr.observe_lane(t, gt)
            res = self._role_resolver
            if res is not None and getattr(res, "my_lane_hook", False) is None:
                res.my_lane_hook = self.my_observed_lane
        except Exception:
            self._err.exception("Self icon tick failed")

    def _read_hud(self, t: float, game: Any) -> Any:
        """HUD portrait read (hud_reader.HudReader): one full-window grab to calibrate (per
        window size, retried every 15 s), then only the small portrait patch. None if unknown."""
        win = self._window
        if win is None:
            return None
        if self._hud_reader is None:
            from treeaicoach.hud_reader import HudReader

            self._hud_reader = HudReader()
        hr = self._hud_reader
        size = (win.w, win.h)
        cal_t, cal_size = self._hud_cal
        if cal_size != size:
            # a failed calibration (full-window grab + search) is retried 15 s later, then 30, 60,
            # 120 s (never a full-screen capture every 15 s for a whole game)
            fails = int(getattr(self, "_hud_cal_fails", 0) or 0)
            wait = min(HUD_CAL_RETRY_MAX_S, HUD_CAL_RETRY_S * 2 ** (fails - 1)) if fails > 0 else 0.0
            if cal_t <= t and t - cal_t < wait:
                return None
            self._hud_cal = (t, None)
            screen = self._grabber().grab(win)
            if screen is None or is_black_frame(screen) or not hr.calibrate(screen):
                self._hud_cal_fails = fails + 1
                return None
            self._hud_cal_fails = 0
            self._hud_cal = (t, size)
            log.info("HUD portrait found at %s", hr.location)
        roi = hr.roi()
        if roi is None:
            return None
        x, y, w, h = roi
        patch = self._grabber().grab(Rect(win.x + x, win.y + y, w, h))
        alive = None
        me = getattr(game, "me", None)
        if me is not None:
            alive = not bool(getattr(me, "is_dead", False))
        return hr.read_patch(patch, alive_hint=alive)

    @staticmethod
    def _with(item: Any, **changes: Any) -> Any:
        """Copy of an ``Identified`` with some fields changed (in place for non-dataclasses)."""
        try:
            if dataclasses.is_dataclass(item) and not isinstance(item, type):
                return dataclasses.replace(item, **changes)
            for k, v in changes.items():
                setattr(item, k, v)
        except Exception:
            log.debug("Cannot update %r", item, exc_info=True)
        return item

    def _stabilize(self, t: float, identified: list[Any]) -> list[Any]:
        """Temporal sanity checks between the identifier and the tracker.

        * an unidentified icon exactly where I was a moment ago, while no icon is "self" in
          this frame, is my own icon with a misread ring colour (it would otherwise look like
          an enemy standing on me);
        * an enemy identity that pops up exactly on the spot of another enemy that was visible
          a moment ago (and is missing from this frame), while its own track has been hidden
          for a while, is that other enemy misidentified.
        """
        tracker = self._tracker
        if tracker is None or not identified:
            return identified
        out = list(identified)
        tracks = {tr.alias: tr for tr in tracker.tracks() if tr.alias}
        for i, x in enumerate(out):
            alias = getattr(x, "alias", None)
            own = tracks.get(alias) if alias else None
            if own is None or getattr(x, "relation", None) == "self":
                continue
            dt = t - own.last_seen
            pos = own.raw_position() if hasattr(own, "raw_position") else own.position()
            if pos is None or not (0.0 <= dt <= JUMP_CHECK_S):
                continue
            det = getattr(x, "det", x)
            d = math.hypot(float(det.u) - pos[0], float(det.v) - pos[1])
            if d > JUMP_SPEED * dt + JUMP_SLACK:
                # physically impossible move: the identity (not the icon) is wrong
                out[i] = self._with(x, alias=None, id_score=0.0)
        rel = [getattr(x, "relation", None) for x in out]
        me = tracker.me() if "self" not in rel else None
        if self._me_dead():
            # dead: no icon of mine on the map. The icon next to where I died (my killer, an
            # ally) must not become "me" (real records: my position walked around while dead,
            # then "Swain vu à deux endroits" at the respawn)
            me = None
            out = [self._with(x, relation="ally") if getattr(x, "relation", None) == "self" else x
                   for x in out]
        if me is not None and t - me.last_seen <= STICKY_SELF_S:
            pos = me.position()
            if pos is not None:
                best, best_d = None, math.inf
                for i, x in enumerate(out):
                    det = getattr(x, "det", x)
                    if getattr(x, "alias", None) is None or getattr(x, "alias", None) == me.alias:
                        d = math.hypot(float(det.u) - pos[0], float(det.v) - pos[1])
                        if d < best_d:
                            best, best_d = i, d
                if best is not None and best_d <= STICKY_SELF_DIST:
                    out[best] = self._with(out[best], relation="self", alias=me.alias,
                                           team=getattr(out[best], "team", None) or me.team)
        present = {getattr(x, "alias", None) for x in out if getattr(x, "alias", None)}
        dead = self._dead_aliases()     # a dead champion has no icon: never relabel to him
        enemy_tracks = [tr for tr in tracker.enemies(visible_only=False)
                        if tr.alias and tr.alias not in dead]
        friends = [tr for tr in tracks.values() if tr.relation != "enemy" and t - tr.last_seen <= 1.0]
        for i, x in enumerate(out):
            # unidentified "ally" ring exactly where an enemy stood a moment ago: misread ring colour
            if getattr(x, "alias", None) or getattr(x, "relation", None) != "ally":
                continue
            det = getattr(x, "det", x)
            u, v = float(det.u), float(det.v)

            def near(tr: Any) -> float:
                p = tr.position()
                return math.hypot(u - p[0], v - p[1]) if p is not None else math.inf

            if any(near(tr) <= RELABEL_DIST for tr in friends):
                continue
            cands = [(near(tr), tr) for tr in enemy_tracks
                     if tr.alias not in present and t - tr.last_seen <= RELABEL_RECENT_S]
            cands = [c for c in cands if c[0] <= RELABEL_DIST]
            if cands:
                tr = min(cands, key=lambda c: c[0])[1]
                out[i] = self._with(x, alias=tr.alias, relation="enemy", team=tr.team)
                present.add(tr.alias)
        by_alias = {tr.alias: tr for tr in enemy_tracks}
        for i, x in enumerate(out):
            alias = getattr(x, "alias", None)
            if not alias or getattr(x, "relation", None) != "enemy":
                continue
            own = by_alias.get(alias)
            if own is not None and t - own.last_seen < IDENTITY_SWAP_HIDDEN_S:
                continue
            if float(getattr(x, "id_score", 0.0) or 0.0) >= DUP_KEEP_ID_SCORE:
                continue                  # a confident portrait match is trusted
            det = getattr(x, "det", x)
            own_pos = own.position() if own is not None and own.stacked_with is not None else None
            if own_pos is not None and math.hypot(float(det.u) - own_pos[0], float(det.v) -
                                                  own_pos[1]) <= IDENTITY_SWAP_DIST:
                # its own track is held under an icon right there (enemy duo stacked under my
                # ADC): it is coming out of the stack, not another enemy misidentified
                # (det_gym bl_swain_death: Veigar relabelled Nami, both held under Tristana)
                continue
            for tr in enemy_tracks:
                if tr.alias in present or t - tr.last_seen > IDENTITY_SWAP_RECENT_S:
                    continue
                pos = tr.position()
                if pos is not None and math.hypot(float(det.u) - pos[0], float(det.v) - pos[1]) \
                        <= IDENTITY_SWAP_DIST:
                    out[i] = self._with(x, alias=tr.alias)
                    present.discard(alias)
                    present.add(tr.alias)
                    break
        return self._drop_duplicates(t, out, tracks)

    def _track_update(self, t: float, identified: list[Any]) -> None:
        """Tracker update with the Live Client dead set (a dead champion is hidden at once)."""
        tracker = self._tracker
        set_dead = getattr(tracker, "set_dead", None)
        if callable(set_dead):
            try:
                set_dead(self._dead_aliases())
                game = self._game
                if game is not None and hasattr(tracker, "set_roster"):
                    me = game.me.champion_alias if game.me is not None else None
                    tracker.set_roster({p.champion_alias: ("self" if p.champion_alias == me else
                                                           "enemy" if p in game.enemies else "ally")
                                        for p in game.all_players() if p.champion_alias})
            except Exception:
                self._err.exception("Tracker dead set / roster failed")
        tracker.update(t, identified)

    def _dead_aliases(self) -> set[str]:
        """Champions dead right now (roster matcher's respawn-timed view, else the Live API)."""
        try:
            m = getattr(self._detector, "matcher", None)
            if m is not None and getattr(m, "has_roster", False):
                return set(getattr(m, "last_dead", None) or ())
            game = self._game
            return {p.champion_alias for p in game.all_players() if p.is_dead} \
                if game is not None else set()
        except Exception:
            return set()

    def _drop_duplicates(self, t: float, out: list[Any], tracks: dict[str, Any]) -> list[Any]:
        """Drop enemy detections that duplicate another enemy icon of the same frame.

        A second, overlapping detection of one icon (camera-rectangle edge, partial occlusion,
        imprecise detector) would otherwise become a phantom enemy right next to the real one
        (unidentified, or identified as the next-best portrait: often the hidden jungler).
        Non-maximum suppression among enemy detections within :data:`DUP_DIST`: identities seen
        a moment ago and confident identifications are always kept and win over the others.
        """
        def rank(x: Any) -> tuple[int, float]:
            alias = getattr(x, "alias", None)
            tr = tracks.get(alias) if alias else None
            if tr is not None and t - tr.last_seen <= STICKY_SELF_S:
                return 0, 0.0
            ids = float(getattr(x, "id_score", 0.0) or 0.0)
            if alias and ids >= DUP_KEEP_ID_SCORE:
                return 1, -ids
            det = getattr(x, "det", x)
            return (2 if alias else 3), -float(getattr(det, "score", 0.0) or 0.0)

        enemies = [(rank(x), i, x) for i, x in enumerate(out) if getattr(x, "relation", None) == "enemy"]
        if len(enemies) < 2:
            return out
        enemies.sort(key=lambda e: (e[0], e[1]))
        kept: list[Any] = []
        drop: set[int] = set()
        for (level, _s), i, x in enemies:
            det = getattr(x, "det", x)
            if level >= 2 and any(math.hypot(float(det.u) - float(getattr(k, "det", k).u),
                                             float(det.v) - float(getattr(k, "det", k).v)) <= DUP_DIST
                                  for k in kept):
                drop.add(i)
                continue
            kept.append(x)
        return [x for i, x in enumerate(out) if i not in drop] if drop else out

    def _me_dead(self) -> bool:
        """I am dead right now (Live Client, or the matcher's respawn-timed dead set)."""
        try:
            game = self._game
            me = getattr(game, "me", None) if game is not None else None
            if me is None:
                return False
            if bool(getattr(me, "is_dead", False)):
                return True
            return bool(me.champion_alias) and me.champion_alias in self._dead_aliases()
        except Exception:
            return False

    def _camera_self_fallback(self, frame: np.ndarray, identified: list[Any]) -> None:
        """No icon identified as me: the ally icon nearest to the camera centre is me.

        Not while I am dead (no icon of mine), not while my track is held under another icon
        (my support / ADC on me: I am there, wherever the camera looks), and never farther
        from my last known position than I can have walked (a dragged / free camera looking
        at a team-mate is not me: real records, me drawn on the far side of the map)."""
        if self._me_dead():
            return
        game = self._game
        my_alias = game.me.champion_alias if game is not None and game.me is not None else None
        allies = [x for x in identified if getattr(x, "relation", None) == "ally"
                  and (getattr(x, "alias", None) in (None, my_alias))]
        if not allies:
            return
        mine = None
        tracker = self._tracker
        if tracker is not None:
            try:
                mine = tracker.me()
            except Exception:
                mine = None
        if mine is not None and getattr(mine, "stacked_with", None) is not None and mine.visible:
            return
        center = find_camera_center(frame)
        if center is None:
            return
        reach = None
        t = float(getattr(tracker, "last_update", None) or 0.0) if tracker is not None else 0.0
        if mine is not None and mine.position() is not None and mine.last_seen is not None:
            dt = max(0.0, t - float(mine.last_seen))
            if dt <= CAMERA_SELF_MEMORY_S:
                reach = CAMERA_SELF_REACH + JUMP_SPEED * dt
        best, best_d = None, CAMERA_SELF_MAX_DIST
        for x in allies:
            det = getattr(x, "det", x)
            d = math.hypot(float(det.u) - center[0], float(det.v) - center[1])
            if reach is not None:
                p = mine.position()
                if math.hypot(float(det.u) - p[0], float(det.v) - p[1]) > reach:
                    continue
            if d < best_d:
                best, best_d = x, d
        if best is None:
            return
        idx = identified.index(best)
        try:
            if dataclasses.is_dataclass(best) and not isinstance(best, type):
                identified[idx] = dataclasses.replace(best, relation="self")
            else:
                best.relation = "self"
        except Exception:
            log.debug("Camera self fallback failed", exc_info=True)

    def _update_threat(self, t: float, gank_alerts: list[Alert]) -> int:
        for a in gank_alerts:
            lvl = int(a.level)
            if a.kind in GANK_KINDS:
                self._threat_hist.append((t, lvl, a))
                if lvl >= Level.DANGER:
                    self._last_danger_t = t
        while self._threat_hist and t - self._threat_hist[0][0] > THREAT_HOLD_S:
            self._threat_hist.popleft()
        return max((lvl for _t, lvl, _a in self._threat_hist), default=0)

    def _personal_danger(self, t: float, gt: float, game: GameInfo, tracker: Any, threat: int) -> list[Alert]:
        """Personal danger (danger.py): my HP / level / items vs the visible enemies on me, lane
        opponent and on-screen enemies included (written warning, spoken "Recule !" when low).
        Goes through the gank fast path (latency first). Never raises."""
        if getattr(self._cfg, "safe_mode", False):
            return []
        try:
            pd = getattr(self, "_danger", None)
            if pd is None:
                from treeaicoach.danger import PersonalDanger

                pd = self._danger = PersonalDanger()
            roles = self._role_resolver
            lane = list(roles.lane_opponents() or ()) if roles is not None else []
            jg = roles.enemy_jungler() if roles is not None else None
            if not jg and game.enemy_jungler() is not None:
                jg = game.enemy_jungler().champion_alias
            tac = self._tactics
            fog = self._fog.estimates() if self._fog is not None else []
            return pd.update(t, gt, game, tracker, lane_opponents=lane, jungler=jg, threat=threat,
                             gank_danger_t=self._last_danger_t,
                             in_fight=bool(tac is not None and tac.in_fight()), fog=fog,
                             recalling=self.recalling(t))
        except Exception:
            self._err.exception("Personal danger failed")
            return []

    RECALL_HOLD_S = 1.5             # a recall reading this old still counts (detector cadence)

    def note_recall(self, active: bool, t: float | None = None) -> None:
        """My recall channel was seen (True) / is over (False) - fed by a screen / minimap reader of
        the recall bar ("Rappel 6.1"). Coaching side: danger.PersonalDanger (cancel or nothing).
        Never raises."""
        try:
            now = self._clock() if t is None else float(t)
            self._recall_seen_t = now if active else None
        except Exception:
            self._recall_seen_t = None

    def recalling(self, t: float) -> bool:
        """My recall is channelling right now (see :meth:`note_recall`)."""
        seen = getattr(self, "_recall_seen_t", None)
        return seen is not None and 0.0 <= float(t) - seen <= self.RECALL_HOLD_S

    def _death_recap_alerts(self, t: float) -> list[Alert]:
        due = self._death_due
        if due is None or t < due[0]:
            return []
        self._death_due = None
        rec = self._recorder
        text = None
        if rec is not None:
            try:
                text = rec.death_recap(due[1])
            except Exception:
                self._err.exception("Death recap failed")
        if not text:
            return []
        return [make_alert(AlertKind.DEATH_RECAP, Level.INFO, t, text=text, key="death_recap")]

    def _collect(self, frame: np.ndarray, t: float) -> None:
        if t - self._last_collect < max(0.5, float(self._cfg.collect_interval_s)):
            return
        if self._collect_count >= COLLECT_MAX_FILES:
            return
        self._last_collect = t
        try:
            from treeaicoach.paths import collect_dir

            name = time.strftime("%Y%m%d_%H%M%S") + f"_{self._collect_count:04d}.png"
            if cv2.imwrite(str(collect_dir() / name), frame):
                self._collect_count += 1
        except Exception:
            self._err.exception("Sample collection failed")
