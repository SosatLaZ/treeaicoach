"""Command line / entry point of TreeAI Coach (``TreeAICoach.exe``, ``python -m treeaicoach``).

Options::

    --selftest [--selftest-out FILE]   self-test (exit code 0 = OK), used by the CI on the exe
    --demo                             simulated game (UI, or console with --nogui)
    --nogui                            console mode: the engine runs until Ctrl+C
    --debug                            DEBUG logs
    --config FILE                      configuration file (default: %APPDATA%/TreeAICoach/config.json)
    --ui-smoke                         open the interface, close it after 4 s, exit 0
    --version                          print the version

The windowed exe has ``sys.stdout = None``: every print is guarded. A single instance runs at a
time (named mutex ``Local\\TreeAICoachSingleInstance``, Windows only; skipped for the self-test
and the UI smoke test).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from treeaicoach import APP_NAME, __version__

log = logging.getLogger(__name__)

MUTEX_NAME = "Local\\TreeAICoachSingleInstance"
ERROR_ALREADY_EXISTS = 183
UI_SMOKE_SECONDS = 4.0
ALREADY_RUNNING_TEXT = ("TreeAI Coach est déjà ouvert.\n\n"
                        "Regarde dans la barre des tâches ou dans la zone de notification.")

_mutex_handle: Any = None


# ------------------------------------------------------------------------------ output
def _out(text: str, err: bool = False) -> None:
    """Print if a console exists (never raises: the windowed exe has no stdout/stderr)."""
    stream = sys.stderr if err else sys.stdout
    try:
        if stream is not None:
            stream.write(text + "\n")
            stream.flush()
    except Exception:
        pass


def message_box(text: str, title: str = APP_NAME, error: bool = False) -> None:
    """Small Windows message box (no-op elsewhere / on failure)."""
    if sys.platform != "win32":
        _out(text, err=error)
        return
    try:
        import ctypes

        flags = (0x10 if error else 0x40) | 0x40000   # MB_ICONERROR / MB_ICONINFORMATION | MB_TOPMOST
        ctypes.windll.user32.MessageBoxW(None, str(text), str(title), flags)
    except Exception:
        log.debug("MessageBoxW failed", exc_info=True)


# ------------------------------------------------------------------------------ single instance
def acquire_single_instance(name: str = MUTEX_NAME) -> bool:
    """True if this is the only running instance (always True off Windows / on error)."""
    global _mutex_handle
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        handle = kernel32.CreateMutexW(None, False, name)
        last = ctypes.get_last_error()
        if not handle:
            log.warning("CreateMutexW failed (error %s): single instance not enforced", last)
            return True
        if last == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        _mutex_handle = handle          # kept open for the whole process lifetime
        return True
    except Exception:
        log.debug("Single-instance check failed", exc_info=True)
        return True


def release_single_instance() -> None:
    global _mutex_handle
    h, _mutex_handle = _mutex_handle, None
    if h and sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.CloseHandle(h)
        except Exception:
            pass


# ------------------------------------------------------------------------------ resources
def apply_process_policy(cfg: Any) -> dict[str, Any]:
    """The game always wins: below-normal process priority (+ EcoQoS hint) when
    ``cfg.low_priority`` (default), and at most 2 OpenCV worker threads. Never raises."""
    out: dict[str, Any] = {}
    try:
        import cv2

        if cv2.getNumThreads() > 2:
            cv2.setNumThreads(2)
        out["cv_threads"] = cv2.getNumThreads()
    except Exception:
        pass
    try:
        if bool(getattr(cfg, "low_priority", True)):
            from treeaicoach.sysperf import lower_process_priority

            out.update(lower_process_priority(eco_qos=bool(getattr(cfg, "eco_qos", True))))
    except Exception:
        log.debug("Process policy failed", exc_info=True)
    return out


# ------------------------------------------------------------------------------ arguments
class _Parser(argparse.ArgumentParser):
    """argparse without printing to a missing console and without sys.exit()."""

    def _print_message(self, message: str, file: Any = None) -> None:
        if message:
            _out(message.rstrip("\n"), err=file is sys.stderr)

    def exit(self, status: int = 0, message: str | None = None) -> None:  # type: ignore[override]
        if message:
            _out(message.rstrip("\n"), err=True)
        raise _ParserExit(status)


class _ParserExit(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="TreeAICoach", description=f"{APP_NAME} — coach vocal anti-gank pour League of Legends")
    p.add_argument("--selftest", action="store_true", help="autotest (code de sortie 0 = OK)")
    p.add_argument("--selftest-out", metavar="FICHIER", default=None, help="rapport de l'autotest")
    p.add_argument("--selftest-voice", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--demo", action="store_true", help="partie simulée (démo)")
    p.add_argument("--nogui", action="store_true", help="mode console, sans interface (Ctrl+C pour quitter)")
    p.add_argument("--debug", action="store_true", help="journaux détaillés")
    p.add_argument("--config", metavar="FICHIER", default=None, help="fichier de configuration")
    p.add_argument("--ui-smoke", action="store_true", help="ouvre l'interface puis la ferme après 4 s")
    p.add_argument("--duration", type=float, default=None, help=argparse.SUPPRESS)  # --nogui auto-stop (tests)
    p.add_argument("--version", action="store_true", help="affiche la version")
    return p


# ------------------------------------------------------------------------------ modes
def run_console(cfg: Any, demo: bool = False, duration: float | None = None) -> int:
    """Console mode: engine + voice until Ctrl+C (or ``duration`` seconds)."""
    from treeaicoach.engine import CoachEngine
    from treeaicoach.voice import VoiceEngine

    voice = VoiceEngine(cfg.voice_name, cfg.voice_rate, cfg.voice_volume, cfg.beep_on_danger,
                        engine=getattr(cfg, "voice_engine", "auto"), neural_voice=getattr(cfg, "neural_voice", ""),
                        neural_rate=getattr(cfg, "neural_rate", "+15%"))
    source = None
    if demo:
        from treeaicoach.demo import DemoSource

        source = DemoSource()
    engine = CoachEngine(cfg, voice, frame_source=source)
    voice.start()
    engine.start()
    _out(f"{APP_NAME} {__version__} — mode console{' (démo)' if demo else ''}. Ctrl+C pour quitter.")
    t_end = None if duration is None else time.monotonic() + max(0.0, duration)
    last_line = ""
    try:
        while t_end is None or time.monotonic() < t_end:
            time.sleep(0.25 if t_end is not None else 1.0)
            st = engine.get_status()
            gt = f"{int(st.game_time // 60)}:{int(st.game_time % 60):02d}" if st.game_time is not None else "--:--"
            line = (f"[{st.state.value}] {st.message} | jeu {gt} | {st.fps:.1f} img/s | "
                    f"ennemis visibles {st.enemies_visible} | dernière alerte : {st.last_alert or '-'}")
            if line != last_line:
                _out(line)
                last_line = line
    except KeyboardInterrupt:
        _out("Arrêt…")
    finally:
        engine.stop()
        voice.stop()
    return 0


def run_gui(cfg: Any, demo: bool = False, smoke_seconds: float | None = None) -> int:
    """Launch the CustomTkinter interface (``ui.run_app``)."""
    try:
        from treeaicoach import ui
    except Exception as exc:
        log.exception("User interface unavailable")
        message_box(f"L'interface de {APP_NAME} n'a pas pu démarrer :\n{exc}\n\n"
                    "Consulte le journal dans %APPDATA%\\TreeAICoach\\logs.", error=True)
        return 1
    rc = ui.run_app(cfg, demo=demo, smoke_seconds=smoke_seconds)
    return int(rc) if isinstance(rc, int) else 0


def _default_selftest_out() -> Path | None:
    """Where the windowed exe writes its self-test report when no console and no --selftest-out."""
    if sys.stdout is not None:
        return None
    try:
        from treeaicoach.paths import user_data_dir

        return user_data_dir() / "selftest.txt"
    except Exception:
        return None


def _start_game_data_refresh(cfg: Any) -> None:
    """Background Data Dragon refresh (items / champions of the live patch, at most once a day).
    Uses the same switch as the other public-asset downloads (``download_skin_icons``)."""
    try:
        from treeaicoach import game_data

        game_data.refresh_async(allow_network=bool(getattr(cfg, "download_skin_icons", True)))
    except Exception:
        log.debug("Data Dragon refresh not started", exc_info=True)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point; returns the process exit code. ``argv`` defaults to ``sys.argv[1:]``."""
    try:
        args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    except _ParserExit as exc:
        return int(exc.status)
    if args.version:
        _out(f"{APP_NAME} {__version__}")
        return 0
    try:
        from treeaicoach.capture import set_dpi_awareness

        set_dpi_awareness()
    except Exception:
        pass
    from treeaicoach.logging_setup import install_excepthooks, setup_logging

    log_file = setup_logging(debug=args.debug)
    install_excepthooks()
    log.info("%s %s starting (args: %s)", APP_NAME, __version__, " ".join(sys.argv[1:] if argv is None else argv))
    try:
        if args.selftest:
            from treeaicoach.selftest import run_selftest

            out = Path(args.selftest_out) if args.selftest_out else _default_selftest_out()
            return run_selftest(out, voice=bool(args.selftest_voice))

        from treeaicoach.config import load_config

        cfg = load_config(Path(args.config) if args.config else None)
        if not args.ui_smoke and not acquire_single_instance():
            log.info("Another instance is already running")
            message_box(ALREADY_RUNNING_TEXT)
            return 0
        apply_process_policy(cfg)
        if not args.ui_smoke and not args.demo:
            _start_game_data_refresh(cfg)
        if args.nogui:
            return run_console(cfg, demo=args.demo, duration=args.duration)
        return run_gui(cfg, demo=args.demo, smoke_seconds=UI_SMOKE_SECONDS if args.ui_smoke else None)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        log.exception("Fatal error")
        message_box(f"{APP_NAME} a rencontré une erreur inattendue :\n{exc}\n\nJournal : {log_file}", error=True)
        return 1
    finally:
        release_single_instance()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
