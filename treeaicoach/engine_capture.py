"""Capture stage of a tick: game window, minimap location / verification, grab, occlusion,
black / frozen / fullscreen checks.

Mixin of :class:`treeaicoach.engine.CoachEngine` (split out of ``engine.py`` without any
behaviour change): the methods use the engine's state (``self._lock``, ``self._cfg`` ...),
created in ``CoachEngine.__init__``. Not meant to be used on its own.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np

from treeaicoach.capture import Rect, is_black_frame
from treeaicoach.engine_base import (
    LOCATE_RETRY_S,
    MSG_BLACK,
    MSG_FALLBACK,
    MSG_FROZEN,
    MSG_FULLSCREEN,
    MSG_LOCATING,
    MSG_MINIMAP_COVERED,
    MSG_MINIMIZED,
    MSG_NO_FRAME,
    MSG_NO_WINDOW,
    MSG_OCCLUDED,
    MSG_RUNNING,
    STALE_MIN_GAME_S,
    UNFOCUSED_HIDE_S,
    VERIFY_BAD_S,
    VERIFY_PERIOD_S,
    WINDOW_REFRESH_S,
    EngineState,
    _as_bgr,
)

log = logging.getLogger("treeaicoach.engine")   # same logger as before the split


class CaptureMixin:
    """Capture stage of a tick: game window, minimap location / verification, grab, occlusion,"""

    # ================================================================== capture / location
    def _find_window(self, t: float) -> Rect | None:
        if t - self._window_t < WINDOW_REFRESH_S and self._window_t > -math.inf:
            return self._window
        self._window_t = t
        info = None
        try:
            if self._window_finder is not None:
                win = self._window_finder()
            else:
                from treeaicoach.capture import game_window_info

                info = game_window_info()
                win = info.rect if info is not None else None
        except Exception:
            self._err.exception("find_game_window failed")
            win = None
        self._win_info = info
        # focus: the overlay hides and the detection slows down while the game is not in the
        # foreground (alt-tab); our own windows (settings, preview) do not count as "away"
        focused = info is None or info.foreground or info.own_foreground
        if focused or not getattr(self._cfg, "pause_when_unfocused", True):
            self._unfocused_since = None
        elif self._unfocused_since is None:
            self._unfocused_since = t
        self._paused = "minimized" if (info is not None and info.minimized) else None
        if win != self._window:
            old = self._window
            if win is not None and old is not None and (win.w, win.h) != (old.w, old.h):
                self._relocate = True
            elif win is not None and old is not None and self._minimap_rect is not None \
                    and self._rect_window == old and (win.x, win.y) != (old.x, old.y):
                # window moved (same size): the minimap moved with it, no new search
                self._minimap_rect = self._minimap_rect.offset(win.x - old.x, win.y - old.y)
                self._rect_window = win
                log.info("Game window moved: minimap rect now %s", self._minimap_rect)
            self._window = win
        return win

    def _grabber(self) -> Any:
        if self._diag_req.pop("recreate_capture", False) and self._capture is not None:
            try:
                self._capture.close()
            except Exception:
                pass
            self._capture = None
        if self._capture is None:
            from treeaicoach.capture import SmartCapture

            self._capture = SmartCapture(str(getattr(self._cfg, "capture_backend", "auto") or "auto"))
        return self._capture

    def _manual_rect(self, win: Rect) -> Rect | None:
        r = self._cfg.manual_minimap_rect
        if self._cfg.minimap_mode != "manual" or not isinstance(r, dict):
            return None
        try:
            sw, sh = float(r["screen_w"]), float(r["screen_h"])
            x, y, w, h = float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"])
            if (int(sw), int(sh)) == (win.w, win.h) and win.x <= x and win.y <= y \
                    and x + w <= win.x + win.w and y + h <= win.y + win.h:
                return Rect(int(x), int(y), int(w), int(h))
            sx, sy = win.w / sw, win.h / sh
            return Rect(win.x + int(round(x * sx)), win.y + int(round(y * sy)),
                        max(1, int(round(w * sx))), max(1, int(round(h * sy))))
        except Exception:
            log.warning("Invalid manual minimap rectangle %r", r)
            return None

    def _locate(self, t: float, win: Rect) -> None:
        """(Re)compute the minimap rectangle for window ``win``."""
        self._relocate = False
        self._bad_since = None
        self._next_verify = t + VERIFY_PERIOD_S
        manual = self._manual_rect(win)
        if manual is not None:
            self._minimap_rect, self._locate_method = manual, "manual"
            self._rect_window = win
            return
        side = self._cfg.minimap_side
        gs, hint, hint_key = None, None, None
        try:
            gs = self._settings_watcher.get() if self._settings_watcher is not None else None
            if side == "auto" and gs is not None and gs.minimap_side():
                side = gs.minimap_side()          # FlipMiniMap from the game's own settings
            if self._rect_cache is not None:
                hint_key = self._rect_cache.key(win.w, win.h, gs)
                hint = self._rect_cache.get(hint_key)
        except Exception:
            log.debug("Game settings prior failed", exc_info=True)
        self._set_state(EngineState.LOCATING, MSG_LOCATING)
        loc = None
        try:
            screen = self._grabber().grab(win)
            if screen is not None and not is_black_frame(screen):
                locator = self._ensure_locator()
                try:
                    loc = locator.locate(screen, win, side=side, hint=hint)
                except TypeError:                 # a locator without the hint parameter
                    loc = locator.locate(screen, win, side=side)
            elif screen is not None:
                self._set_state(EngineState.CAPTURE_BLACK, MSG_BLACK)
        except Exception:
            self._err.exception("Minimap location failed")
        self._rect_window = win
        if loc is not None:
            self._minimap_rect, self._locate_method = loc.rect, "auto"
            log.info("Minimap located at %s (score %.2f)", loc.rect, loc.score)
            if hint_key is not None:
                self._rect_cache.put(hint_key, loc.rect.x - win.x, loc.rect.y - win.y,
                                     loc.rect.w, loc.rect.h, loc.score)
            return
        from treeaicoach.minimap_locator import fallback_rect

        fb_side = "left" if side == "left" else "right"
        self._minimap_rect, self._locate_method = fallback_rect(win, fb_side), "fallback"
        self._next_locate = t + LOCATE_RETRY_S
        log.info("Minimap not found: fallback rectangle %s", self._minimap_rect)

    def _grab_minimap(self, t: float, gt: float | None = None) -> np.ndarray | None:
        win = self._find_window(t)
        if win is None:
            self._set_state(EngineState.LOCATING, MSG_MINIMIZED if self._paused else MSG_NO_WINDOW)
            return None
        self._settings_changed_check()
        if self._relocate or self._minimap_rect is None or self._rect_window != win or (
                self._locate_method == "fallback" and t >= self._next_locate):
            self._locate(t, win)
        rect = self._minimap_rect
        if rect is None:
            return None
        self._occluded = bool(self._occlusion(rect))
        if self._occluded:
            # another window (League client, browser...) covers the minimap: its pixels must never
            # become detections; the tick is frozen (tracks keep their state) until it is visible
            self._set_state(EngineState.RUNNING, MSG_OCCLUDED)
            return None
        cap = self._grabber()
        frame = _as_bgr(cap.grab(rect))
        if frame is None:
            self._set_state(EngineState.RUNNING, MSG_NO_FRAME)
            return None
        check = getattr(cap, "check", None)
        if callable(check):      # black / frozen frames -> other capture backend (capture.SmartCapture)
            try:
                st = check(frame, t, rect, allow_stale=gt is not None and gt >= STALE_MIN_GAME_S)
            except Exception:
                st = "ok"
            self._capture_status = st
            if st == "switched":
                frame = _as_bgr(cap.grab(rect))
                if frame is None:
                    return None
            elif st == "black":
                self._set_state(EngineState.CAPTURE_BLACK, MSG_BLACK)
                return None
            elif st == "stale":
                self._capture_note = MSG_FROZEN
        verify_due = t >= self._next_verify
        if verify_due and self._bad_since is None and self._heavy_now and not self._verify_due_deferred:
            # keep the verification off the tick running a coaching slot (no spike); next tick
            self._verify_due_deferred = True
            verify_due = False
        if self._locate_method == "auto" and verify_due:
            self._verify_due_deferred = False
            self._next_verify = t + self._budget.profile.verify_s
            try:
                from treeaicoach.minimap_locator import VERIFY_MIN_SCORE

                score = float(self._ensure_locator().verify(frame))
            except Exception:
                self._err.exception("Minimap verify failed")
                score = 1.0
            self._minimap_score = score
            if score < VERIFY_MIN_SCORE:
                if self._bad_since is None:
                    self._bad_since = t
                elif t - self._bad_since >= VERIFY_BAD_S:
                    log.info("Minimap verification low (%.2f) for %.0f s: relocating", score, VERIFY_BAD_S)
                    self._relocate = True
            else:
                self._bad_since = None
            if self._bad_since is not None:
                # the crop does not look like the minimap (shop / scoreboard over it, scale
                # being changed...): no detection on it (phantoms), checked again next tick
                self._next_verify = t
                self._set_state(EngineState.RUNNING, MSG_MINIMAP_COVERED)
                return None
        self._set_state(EngineState.RUNNING,
                        MSG_FALLBACK if self._locate_method == "fallback" else MSG_RUNNING)
        return frame

    def _occlusion(self, rect: Rect) -> bool | None:
        """Is the minimap covered by another window? (``occlusion_probe`` for tests; live
        capture only). Never raises."""
        probe = self.occlusion_probe
        try:
            if probe is not None:
                return probe(rect)
            if self._window_finder is not None or self._frame_source is not None:
                return False
            from treeaicoach.capture import rect_occluded

            info = self._win_info
            return rect_occluded(rect, game_hwnd=info.hwnd if info is not None else None)
        except Exception:
            return False

    def _settings_changed_check(self) -> None:
        """The game's own settings changed (minimap scale, flip, resolution, HUD scale): the
        minimap moved / was resized -> locate it again (cheap: SettingsWatcher re-reads the
        files only when their mtime changed, at most every 10 s)."""
        w = self._settings_watcher
        if w is None:
            return
        try:
            gs = w.get()
            fp = gs.fingerprint() if gs is not None else None
        except Exception:
            return
        old = getattr(self, "_settings_fp", None)
        self._settings_fp = fp
        if old is not None and fp is not None and fp != old:
            log.info("Game display settings changed (%s -> %s): relocating the minimap", old, fp)
            self._relocate = True

    def _fullscreen_check(self, game: Any = None) -> None:
        """Exclusive fullscreen (WindowMode 0 in game.cfg): layered overlay windows cannot show
        over it and screen capture may be black -> clear French warning (status + log)."""
        w = self._settings_watcher
        if w is None:
            return
        try:
            gs = w.get()
            if gs is not None and gs.exclusive_fullscreen:
                self._capture_note = MSG_FULLSCREEN
                log.warning("Game in exclusive fullscreen (WindowMode=0): overlay invisible, capture may be black")
        except Exception:
            log.debug("fullscreen check failed", exc_info=True)

    # ================================================================== health / diagnostics (v2)
    def overlay_paused(self, now: float | None = None) -> bool:
        """True while the overlay must hide: game minimized, or not in the foreground for
        :data:`UNFOCUSED_HIDE_S` (our own windows excepted). Never raises."""
        try:
            if self._paused:
                return True
            since = self._unfocused_since
            now = self._clock() if now is None else float(now)
            return since is not None and now - since >= UNFOCUSED_HIDE_S
        except Exception:
            return False
