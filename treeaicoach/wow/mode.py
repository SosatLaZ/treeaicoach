"""« Mode WoW »: TreeAI coaches World of Warcraft: Forever while WoW runs and no League game does.

One daemon thread (started by the engine, :func:`attach`), cheap when idle: the process NAME list every
``POLL_IDLE_S`` (10 s) while WoW is not running — nothing else, no file read —, every ``POLL_S`` while it runs.
When the mode turns on:

* the TreeAI addon is installed / updated once per session (``cfg.wow_addon_auto_update``),
* the combat session (:mod:`treeaicoach.wow.coach`, optional) reads the game's ``Logs/WoWCombatLog.txt``
  (``cfg.wow_combatlog``). User rule: the voice NEVER speaks on its own in WoW (``cfg.wow_voice`` is off
  by default); the facts feed the AI context (« Hey coach ») and the WoW page's report.

Coach: ``wow.coach.WowCombatSession(log_path, say)`` (its own tail thread, ``.facts()``) wrapped in
:class:`SessionCoach`. Fallback contract (duck-typed, every method optional): ``create(say=, cfg=, install=)``
or a ``WowCoach`` class, whose object may have ``start(combat_log_path)``, ``tick(now)`` (every ``TICK_S``),
``apply_config(cfg)``, ``stop()``. ``say(text, level)`` speaks through TreeAI's voice (0 info / 1 warning /
2 danger, mute respected).

For the AI context (wow/ai_context.py) the engine gets ``_wow_mode`` (bool), ``_wow_db`` (TreeAIDB or None,
re-read every ``DB_REFRESH_S``) and ``_wow_coach`` (``recent_facts(limit)``).

Never touches the game process: names only. Never raises.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from treeaicoach.wow import optional

log = logging.getLogger(__name__)

POLL_S = 3.0          # WoW running: process list (WoW closed? League started?)
POLL_IDLE_S = 10.0    # (4.0.1) WoW not running: one look at the process names every 10 s, nothing else
TICK_S = 0.5
DB_REFRESH_S = 30.0
MSG_ACTIVE = "Mode WoW Forever : coach actif"
MSG_NO_LOG = "Mode WoW Forever : tape /combatlog en jeu"


class SessionCoach:
    """Adapter of ``wow.coach.WowCombatSession`` to the mode's coach contract (+ ``recent_facts``)."""

    def __init__(self, session_cls: Any, say: Callable[..., Any]) -> None:
        self._cls = session_cls
        self._say = say
        self.session: Any = None

    def start(self, log_path: Any, voice: bool = False) -> None:
        """``voice`` False (default, user rule): the session never speaks on its own; its facts only feed
        the AI context (« Hey coach ») and the report of the WoW page."""
        try:
            self.session = self._cls(log_path, self._say if voice else None, voice=bool(voice))
        except TypeError:
            self.session = self._cls(log_path, self._say if voice else None)
        self.session.start()

    def stop(self) -> None:
        if self.session is not None:
            self.session.stop()

    def recent_facts(self, limit: int = 8) -> list[str]:
        try:
            text = self.session.facts() if self.session is not None else ""
        except Exception:
            return []
        lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
        return lines[-max(0, int(limit)):] if limit else lines

    def report_text(self) -> str:
        try:
            return str(self.session.report_text()) if self.session is not None else ""
        except Exception:
            return ""


class WowMode:
    def __init__(self, cfg_fn: Callable[[], Any], say: Callable[[str, int], None] | None = None,
                 lol_in_game: Callable[[], bool] | None = None,
                 names_fn: Callable[[], Any] | None = None, clock: Callable[[], float] = time.monotonic,
                 on_change: Callable[["WowMode"], None] | None = None) -> None:
        self._cfg_fn = cfg_fn
        self._say = say
        self._lol_in_game = lol_in_game or (lambda: False)
        self._names_fn = names_fn
        self._clock = clock
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.active = False
        self._log_seen = False           # the combat log was active once this session
        self.wow_exe: str | None = None
        self.install: Any = None
        self.install_result: Any = None
        self.coach: Any = None
        self._coach_started = False
        self._installed_once = False
        self._last_poll = -1e9
        self.last_error = ""
        self.db: dict | None = None
        self._db_t = -1e9
        self._on_change = on_change

    # -------------------------------------------------------------- lifecycle
    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="TreeAICoach-wow", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 1.5) -> None:
        self._stop.set()
        th = self._thread
        if th is not None and th is not threading.current_thread():
            th.join(timeout)
        self._thread = None
        self._deactivate()

    def _early_install(self) -> None:
        """(3.8) Install / update the addon as soon as TreeAI starts, WoW running or not: a NEW addon only
        appears after a WoW restart, so waiting for WoW to run was too late. Once per session. Never raises."""
        if self._installed_once:
            return
        try:
            cfg = self._cfg_fn()
            if cfg is None or not bool(getattr(cfg, "wow_enabled", True)) \
                    or not bool(getattr(cfg, "wow_addon_auto_update", True)):
                return
            install = optional("install")
            if install is None:
                return
            inst = install.find_wow(str(getattr(cfg, "wow_path", "") or ""))
            if inst is not None:
                self._installed_once = True
                self.install = self.install or inst
                self.install_result = install.install_addon(inst)
                log.info("WoW addon at start: %s", getattr(self.install_result, "message", self.install_result))
                self._changed()
        except Exception:
            log.debug("wow early install failed", exc_info=True)

    def _run(self) -> None:
        self._early_install()
        while not self._stop.is_set():
            try:
                self.step()
            except Exception:
                log.debug("wow mode step failed", exc_info=True)
            self._stop.wait(TICK_S if self.active else POLL_IDLE_S)

    # -------------------------------------------------------------- one step (tests call it directly)
    def cfg(self) -> Any:
        try:
            return self._cfg_fn()
        except Exception:
            return None

    def step(self) -> None:
        now = self._clock()
        cfg = self.cfg()
        enabled = bool(getattr(cfg, "wow_enabled", True)) if cfg is not None else False
        if now - self._last_poll >= POLL_S or not self.active:
            self._last_poll = now
            process = optional("process")
            names = self._names_fn() if self._names_fn is not None else None
            exe = process.wow_process(names) if process is not None else None
            lol = False
            try:
                lol = bool(self._lol_in_game()) or bool(process is not None and process.is_lol_running(names))
            except Exception:
                pass
            self.wow_exe = exe
            want = enabled and exe is not None and not lol
            if want and not self.active:
                self._activate(cfg)
            elif not want and self.active:
                self._deactivate()
        if self.active:
            self._tick_coach(cfg, now)
            if now - self._db_t >= DB_REFRESH_S:
                self._db_t = now
                self.db = self.read_db()
                if self.db:
                    errlog = optional("errlog")
                    if errlog is not None:
                        errlog.write_report(self.db)  # « TreeAI-erreurs.txt », only when it changed
                self._changed()

    def read_db(self) -> dict | None:
        """TreeAIDB from the newest SavedVariables file of the found install (None when absent).

        (4.0) Parsed once per save, shared with the WoW page and the Accueil card (``live.read_db_cached``):
        a big save is no longer re-parsed every 30 s while WoW runs (CPU that slowed the window down)."""
        install, sv = optional("install"), optional("savedvars")
        if install is None or sv is None or self.install is None:
            return None
        try:
            files = install.savedvars_files(self.install)
            if not files:
                return None
            live = optional("live")
            db = live.read_db_cached(files[0]) if live is not None else sv.read_db(files[0])
            return db or None
        except Exception:
            log.debug("wow db read failed", exc_info=True)
            return None

    def _changed(self) -> None:
        if self._on_change is not None:
            try:
                self._on_change(self)
            except Exception:
                log.debug("wow on_change failed", exc_info=True)

    def _activate(self, cfg: Any) -> None:
        log.info("WoW mode on (%s)", self.wow_exe)
        self.active = True
        install = optional("install")
        if install is not None:
            try:
                self.install = install.find_wow(str(getattr(cfg, "wow_path", "") or ""))
                if self.install is not None and not self._installed_once \
                        and bool(getattr(cfg, "wow_addon_auto_update", True)):
                    self._installed_once = True
                    self.install_result = install.install_addon(self.install)
            except Exception:
                log.debug("wow install step failed", exc_info=True)
        self._make_coach(cfg)
        self._db_t = -1e9
        self._changed()

    def _deactivate(self) -> None:
        was = self.active
        if self.active:
            log.info("WoW mode off")
        self.active = False
        coach, self.coach = self.coach, None
        self._coach_started = False
        self.db = None
        if was:
            self._changed()
        stop = getattr(coach, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception:
                log.debug("wow coach stop failed", exc_info=True)

    # -------------------------------------------------------------- coach
    def say(self, text: str, level: int = 0) -> None:
        cfg = self.cfg()
        # user rule: in WoW the voice never speaks on its own unless « Annonces vocales automatiques » is on
        if not text or self._say is None or cfg is None or not bool(getattr(cfg, "wow_voice", False)):
            return
        try:
            self._say(str(text), int(level))
        except Exception:
            log.debug("wow say failed", exc_info=True)

    def _make_coach(self, cfg: Any) -> None:
        mod = optional("coach")
        if mod is None:
            return
        session_cls = getattr(mod, "WowCombatSession", None)
        if callable(session_cls):
            self.coach = SessionCoach(session_cls, self.say)
            return
        factory = getattr(mod, "create", None) or getattr(mod, "WowCoach", None)
        if not callable(factory):
            return
        for kwargs in ({"say": self.say, "cfg": cfg, "install": self.install}, {"say": self.say, "cfg": cfg},
                       {"say": self.say}):
            try:
                self.coach = factory(**kwargs)
                return
            except TypeError:
                continue
            except Exception as exc:
                self.last_error = f"coach: {type(exc).__name__}"
                log.debug("wow coach creation failed", exc_info=True)
                return

    def _tick_coach(self, cfg: Any, now: float) -> None:
        coach = self.coach
        if coach is None:
            return
        try:
            if not self._coach_started:
                self._coach_started = True
                start = getattr(coach, "start", None)
                if callable(start) and bool(getattr(cfg, "wow_combatlog", True)) and self.install is not None:
                    if isinstance(coach, SessionCoach):
                        start(self.install.combat_log, voice=bool(getattr(cfg, "wow_voice", False)))
                    else:
                        start(self.install.combat_log)
            tick = getattr(coach, "tick", None)
            if callable(tick):
                tick(now)
        except Exception as exc:
            self.last_error = f"coach: {type(exc).__name__}"
            log.debug("wow coach tick failed", exc_info=True)

    def apply_config(self, cfg: Any) -> None:
        fn = getattr(self.coach, "apply_config", None)
        if callable(fn):
            try:
                fn(cfg)
            except Exception:
                log.debug("wow coach apply_config failed", exc_info=True)

    # -------------------------------------------------------------- status
    def message(self) -> str | None:
        """The engine's status line while the mode is on (None when off). (4.0 QA) The combat log is optional
        (the addon and the chat log carry what the page needs, and Forever may not write it): « tape /combatlog »
        only when it was active earlier this session and stopped, and the option is on."""
        if not self.active:
            return None
        install = optional("install")
        if install is not None and self.install is not None:
            tone, _t = install.combat_log_status(self.install)
            if tone == "ok":
                self._log_seen = True
            elif self._log_seen and bool(getattr(self._cfg_fn(), "wow_combatlog", True)):
                return MSG_NO_LOG
        return MSG_ACTIVE

    def snapshot(self) -> dict:
        res = self.install_result
        report = getattr(self.coach, "report_text", None)
        try:
            report_text = str(report() or "") if callable(report) else ""
        except Exception:
            report_text = ""
        return {"active": self.active, "wow_exe": self.wow_exe, "install": self.install, "report": report_text,
                "install_message": getattr(res, "message", "") if res is not None else "",
                "coach": self.coach is not None, "error": self.last_error}


_current: WowMode | None = None


def current() -> WowMode | None:
    """The engine's WoW mode controller (None before the engine starts)."""
    return _current


def attach(engine: Any) -> WowMode | None:
    """Start the WoW mode for a live engine (idempotent). Never raises."""
    global _current
    try:
        if _current is not None:
            _current.start()
            return _current

        def lol_in_game() -> bool:
            return bool(getattr(engine, "_in_game", False))

        def on_change(m: WowMode) -> None:
            engine._wow_mode = m.active
            engine._wow_db = m.db
            engine._wow_coach = m.coach

        _current = WowMode(lambda: engine.cfg, say=getattr(engine, "_say", None), lol_in_game=lol_in_game,
                           on_change=on_change)
        _current.start()
        return _current
    except Exception:
        log.debug("wow attach failed", exc_info=True)
        return None


def detach() -> None:
    global _current
    mode, _current = _current, None
    if mode is not None:
        try:
            mode.stop()
        except Exception:
            log.debug("wow detach failed", exc_info=True)
