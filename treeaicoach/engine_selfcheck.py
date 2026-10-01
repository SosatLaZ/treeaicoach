"""Self-check stage of the engine: the watchdog of :mod:`treeaicoach.selfcheck` wired to the
pipeline (measures, automatic actions, in-game notices, "Santé TreeAI" for the app).

Mixin of :class:`treeaicoach.engine.CoachEngine`: the methods use the engine's state (``self._lock``,
``self._cfg`` ...), created in ``CoachEngine.__init__``. Not meant to be used on its own.

Hooks (all cheap, never raising): ``step()`` -> :meth:`_selfcheck_tick` (rules ~1 Hz, analysis
thread), ``_after_tick`` -> :meth:`_selfcheck_tracks` (identity anomalies), the Live Client poller ->
:meth:`_selfcheck_api` (API silent while the game window exists), ``_start_game`` ->
:meth:`_selfcheck_new_game`. Read side: :meth:`selfcheck_summary` (also ``health()["selfcheck"]``)
and :meth:`selfcheck_report` (diagnostic bundle).
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any

from treeaicoach.selfcheck import API_OUTAGE_MAX_S, RULES, SelfCheck, apply_actions, snapshot_from_engine

log = logging.getLogger("treeaicoach.engine")   # same logger as the other engine stages

#: Rules judged on real-time costs (perf_counter): only with the real capture (tests and
#: harnesses drive the engine with a fake clock).
REALTIME_RULES = frozenset({"perf"})
#: Outside a game, the game window is probed at most this often (rule 9).
WINDOW_PROBE_S = 2.0
#: Roster matcher module knobs saved while a load level other than "normal" is applied.
_RM_SAVED: dict[str, int] = {}


def push_roster_knobs(prof: Any) -> None:
    """Load level of ``prof`` (sysperf.degraded) -> roster matcher module constants read at each
    frame: ring proposals / stack proposals every Nth frame. Untouched at "normal" (the module's
    own values are restored after a degraded period). Never raises."""
    try:
        load = str(getattr(prof, "load", "normal") or "normal")
        if load == "normal" and not _RM_SAVED:
            return
        from treeaicoach import roster_matcher as rm

        if not _RM_SAVED:
            _RM_SAVED.update(RING_PROP_EVERY=int(rm.RING_PROP_EVERY), STACKV_EVERY=int(rm.STACKV_EVERY))
        if load == "normal":
            rm.RING_PROP_EVERY = _RM_SAVED["RING_PROP_EVERY"]
            rm.STACKV_EVERY = _RM_SAVED["STACKV_EVERY"]
            _RM_SAVED.clear()
            return
        rm.RING_PROP_EVERY = max(_RM_SAVED["RING_PROP_EVERY"], int(getattr(prof, "ring_every", 0) or 0))
        stack = _RM_SAVED["STACKV_EVERY"]
        rm.STACKV_EVERY = max(stack, int(getattr(prof, "stack_every", 0) or 0)) if stack > 0 else 0
    except Exception:
        log.debug("roster matcher knobs not applied", exc_info=True)


class SelfCheckMixin:
    """Self-check stage: watchdog measures -> automatic actions -> status (see the module doc)."""

    def _init_selfcheck(self) -> None:
        live = self._frame_source is None
        real = live and self._window_finder is None and self._capture is None
        rules = set(RULES) if real else set(RULES) - REALTIME_RULES
        self._selfcheck = SelfCheck(rules=rules)
        # demo / frame sources (tests, simulations): off unless a test turns it on
        self._selfcheck.enabled = live and bool(getattr(self._cfg, "selfcheck_enabled", True))
        self._sc_ms = 0.0                          # self-check cost accumulated during this tick
        self._voice_override: str | None = None    # "bip": dangers beep-only (rule 7)
        self._ai_block_prev: float | None = None   # AI back-off before the per-game block (rule 8)
        self._loc_attempts = 0                     # minimap locations this game (rule 2)
        self._loc_fails = 0                        # ... consecutive failures
        self._last_good: tuple[Any, Any, float] | None = None   # (rect, window, score) last located
        self._locate_score: float | None = None
        self._api_period: float | None = None      # Live Client poll period while it is silent
        self._win_probe: tuple[float, bool | None] = (-math.inf, None)

    # ------------------------------------------------------------------ hooks
    def _selfcheck_tick(self, t: float) -> None:
        """End of an analysis tick (in game): rules about once per second, then their actions."""
        sc = self._selfcheck
        acc, self._sc_ms = self._sc_ms, 0.0
        if not sc.enabled or not self._in_game:
            return
        t0 = time.perf_counter()
        actions: list[Any] = []
        try:
            if sc.due(t):
                actions = sc.evaluate(snapshot_from_engine(self, t))
        except Exception:
            self._err.exception("Self-check failed")
        sc.cost.add(acc + (time.perf_counter() - t0) * 1000.0)     # (actions excluded: one-off work)
        if actions:
            apply_actions(self, actions, t)

    def _selfcheck_tracks(self, t: float, tracks: Any) -> None:
        sc = self._selfcheck
        if not sc.enabled:
            return
        t0 = time.perf_counter()
        sc.on_tracks(t, tracks)
        self._sc_ms += (time.perf_counter() - t0) * 1000.0

    def _selfcheck_api(self, t: float, ok: bool) -> None:
        """Live Client poll result (poller thread, or step() when synchronous). Never raises."""
        sc = self._selfcheck
        if not sc.enabled or "api" not in sc.rules:
            return
        try:
            window = True if ok else self._window_exists(t)
            err = str(getattr(self._live_client, "last_error", "") or "")
            for a in sc.api_update(t, ok, window, in_game=self._in_game, answering=err.startswith("HTTP")):
                if a.kind == "api_backoff":
                    self._api_period = a.arg
                elif a.kind == "api_recreate":
                    self._recreate_live_client()
        except Exception:
            self._err.exception("Self-check (Live Client) failed")

    def _selfcheck_new_game(self, t: float, game_time: float | None = None) -> None:
        self._loc_attempts = self._loc_fails = 0
        self._last_good = None
        self._locate_score = None
        self._ai_game_block(False)          # a new game: the AI provider may be asked again
        self._selfcheck.new_game(t, game_time)

    # ------------------------------------------------------------------ helpers / actions
    def _window_exists(self, t: float) -> bool | None:
        """Is the game window there? In game: the analysis thread's cached window; outside a game a
        cheap probe at most every :data:`WINDOW_PROBE_S`. None when unknown (demo)."""
        if self._frame_source is not None:
            return None
        if self._in_game and self._window_t > -math.inf and t - self._window_t <= 3.0:
            return self._window is not None
        last, val = self._win_probe
        if 0.0 <= t - last < WINDOW_PROBE_S:
            return val
        try:
            if self._window_finder is not None:
                win = self._window_finder()
            else:
                from treeaicoach.capture import find_game_window

                win = find_game_window()
            val = win is not None
        except Exception:
            val = None
        self._win_probe = (t, val)
        return val

    def _api_outage_hold(self, t: float) -> bool:
        """The Live Client went silent but the game window is still there: a mid-game API outage,
        not a game over - keep the game for up to :data:`API_OUTAGE_MAX_S` (rule 9)."""
        sc = self._selfcheck
        if not sc.enabled or "api" not in sc.rules or self._last_game_seen is None:
            return False
        if t - self._last_game_seen > API_OUTAGE_MAX_S:
            return False
        return bool(self._window_exists(t))

    def _recreate_live_client(self) -> None:
        try:
            from treeaicoach.live_client import LiveClient

            old = self._live_client
            if type(old) is LiveClient:                 # never replace an injected client (tests)
                self._live_client = LiveClient(old.url, old.timeout)
                log.info("Self-check: Live Client HTTP client recreated")
        except Exception:
            log.debug("Live Client recreation failed", exc_info=True)

    def _poll_backoff(self, period: float) -> float:
        p = self._api_period
        return max(float(period), float(p)) if p else float(period)

    def _selfcheck_notify(self, rule: str, text: str, t: float) -> bool:
        """The ONE in-game notice of a problem the player must fix, through the engine's toast /
        presenter path (no new window). False = not now (fight, gank, siege, dead: retried later);
        True = shown, or nothing can be shown in game (the app status says it)."""
        if not text:
            return True
        if not getattr(self._cfg, "toasts_enabled", True) or self._toasts is None or not self._overlay_visible:
            return True
        try:
            ctx = self._presenter_ctx(t)
            if ctx.fight or ctx.gank or ctx.siege or ctx.dead:
                return False
        except Exception:
            pass
        pr = getattr(self, "_presenter", None)
        plog = getattr(pr, "log", None)
        before = plog[-1] if plog else None
        title = {"capture": "CAPTURE", "minimap": "MINIMAP", "champions": "DÉTECTION"}.get(rule, "RÉGLAGE")
        self._toast("warning", title, text, None, f"selfcheck:{rule}", t)
        if pr is None or plog is None:
            return True
        last = plog[-1] if plog else None
        return last is not None and last is not before and last[2] != "drop"

    def _set_voice_override(self, mode: str | None) -> None:
        """``"bip"``: dangers beep-only while the voice fails / is slow (rule 7); None: settings."""
        self._voice_override = mode
        sdv = getattr(self._voice, "set_danger_voice", None)
        if callable(sdv):
            try:
                sdv(mode or getattr(self._cfg, "danger_voice", "bip_voix"))
            except Exception:
                log.debug("voice.set_danger_voice failed", exc_info=True)

    def _danger_voice_mode(self) -> str:
        return self._voice_override or str(getattr(self._cfg, "danger_voice", "bip_voix") or "bip_voix")

    def _ai_game_block(self, on: bool) -> None:
        """No AI request for the rest of this game (rule 8: key refused, quota...); the offline
        rule-based plans continue (ai_advisor publishes them while blocked). Never raises."""
        ai = getattr(self, "_ai", None)
        if ai is None or not hasattr(ai, "_blocked_until"):
            if not on:
                self._ai_block_prev = None
            return
        try:
            with getattr(ai, "_lock", None) or threading.Lock():
                if on:
                    if self._ai_block_prev is None and ai._blocked_until != math.inf:
                        self._ai_block_prev = float(ai._blocked_until)
                        ai._blocked_until = math.inf
                elif self._ai_block_prev is not None:
                    if ai._blocked_until == math.inf:
                        ai._blocked_until = self._ai_block_prev
                    self._ai_block_prev = None
        except Exception:
            log.debug("AI block failed", exc_info=True)

    # ------------------------------------------------------------------ user actions (app buttons)
    def reset_detection(self) -> None:
        """"Réinitialiser la détection": forget everything learned on this PC that changes the
        analysis - icon scale per minimap size, learned custom-skin icons, located minimap
        rectangles (``minimap_cache.json``), this game's calibration / learned colours / tracks,
        the capture backend choice and the load level. The engine objects are reset by the
        analysis thread at its next tick (at once outside a game). The caller (UI) also saves
        ``icon_scale_by_res = {}``. Never raises."""
        try:
            store = getattr(self._cfg, "icon_scale_by_res", None)
            if isinstance(store, dict):
                store.clear()
            from treeaicoach.paths import cache_dir, user_data_dir

            from pathlib import Path

            learned = Path(cache_dir()) / "learned_icons"
            n = 0
            if learned.is_dir():
                for f in learned.glob("*.png"):
                    try:
                        f.unlink()
                        n += 1
                    except OSError:
                        pass
            mc = Path(user_data_dir()) / "minimap_cache.json"
            if mc.is_file():
                try:
                    mc.unlink()
                except OSError:
                    pass
            if self._rect_cache is not None:
                from treeaicoach.game_settings import RectCache

                self._rect_cache = RectCache()
            log.info("Detection reset by the user (%d learned icon(s) deleted)", n)
            self._selfcheck.note(self._clock(), "adapt", "Détection réinitialisée (caches et icônes apprises)")
            with self._lock:
                self._diag_req["reset_detection"] = True
            if not self._in_game:
                self._reset_detection_now(self._clock())
        except Exception:
            log.exception("reset_detection failed")

    def _reset_detection_now(self, t: float) -> None:
        """Analysis thread part of :meth:`reset_detection`."""
        with self._lock:
            self._diag_req.pop("reset_detection", None)
        try:
            m = getattr(self._detector, "matcher", None) if self._detector is not None else None
            if m is not None:
                if isinstance(getattr(m, "scale_store", None), dict):
                    m.scale_store.clear()
                if callable(getattr(m, "set_entries", None)):
                    m.set_entries(())                  # calibration, colours, learned icons, tracks
            db = self._champion_db() if self._in_game else self._db
            if db is not None and callable(getattr(db, "clear_icon_cache", None)):
                db.clear_icon_cache()
            with self._lock:
                self._roster_sig = None                # templates rebuilt at the next poll
                self._prefetched = False
                self._relocate = True
            self._last_good = None
            self._loc_fails = 0
            if self._tracker is not None:
                self._tracker.reset()
            if self._window_finder is None and self._frame_source is None and self._capture is not None:
                self._diag_req["recreate_capture"] = True
            if self._budget.set_load_level(0):
                self._applied_profile = None
            self._selfcheck.load_level = 0
        except Exception:
            log.exception("Detection reset failed")

    def force_normal_profile(self) -> None:
        """"Forcer profil normal": the normal performance budget and no automatic load level for
        this session (the caller saves ``perf_mode = "normal"``). Never raises."""
        try:
            from treeaicoach.sysperf import PerfBudget

            with self._lock:
                self._budget = PerfBudget("normal", target_fps=float(self._cfg.target_fps))
                self._applied_profile = None
            self._selfcheck.force_normal(self._clock())
        except Exception:
            log.exception("force_normal_profile failed")

    def config_fingerprint(self) -> dict[str, Any]:
        """"Empreinte de config" (fingerprint.py): what makes this PC analyse differently."""
        try:
            from treeaicoach import fingerprint

            return fingerprint.collect(self)
        except Exception:
            log.debug("fingerprint failed", exc_info=True)
            return {}

    def config_fingerprint_text(self) -> str:
        from treeaicoach import fingerprint

        return fingerprint.to_text(self.config_fingerprint())

    # ------------------------------------------------------------------ read side
    def selfcheck_summary(self) -> dict[str, Any]:
        """"Santé TreeAI" (selfcheck.SelfCheck.summary): ``state``, ``title``, ``reasons``,
        ``fixed``, ``profile``... Thread-safe, never raises."""
        sc = getattr(self, "_selfcheck", None)
        return sc.summary() if sc is not None else {}

    def selfcheck_report(self) -> dict[str, Any]:
        """Everything the self-check knows (summary, this game, event log): diagnostic bundle."""
        sc = getattr(self, "_selfcheck", None)
        try:
            return sc.export() if sc is not None else {}
        except Exception:
            return {}

    def selfcheck_log(self) -> list[str]:
        sc = getattr(self, "_selfcheck", None)
        try:
            return sc.log_lines() if sc is not None else []
        except Exception:
            return []
