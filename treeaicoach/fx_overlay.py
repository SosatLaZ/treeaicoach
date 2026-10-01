"""Play-rating animations on screen: a dedicated click-through layered window + optional sounds.

:class:`PlayFx` owns one thread. While nothing is queued it blocks on an event (no window,
no timer: idle cost ~0). When :meth:`PlayFx.push` queues a :class:`treeaicoach.plays.Play`, the
thread creates a layered window (same Win32 styles as :mod:`treeaicoach.overlay`: popup,
``WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST``,
never activated, mouse clicks go through), plays the frames of :mod:`treeaicoach.fx_render`
at <= 30 fps, then destroys the window once the queue is empty. Only pixels drawn by TreeAI
in its own window: no injection, no hook, no input.

Sounds (:func:`play_wav_path`): short generated tones (stdlib :mod:`wave`) per class, in
``cache/sounds/play_<class>.wav`` (a bundled ``assets/sounds/play_<class>.wav`` wins), played
with ``winsound`` (async). Off for negative classes unless ``cfg.plays_sound_negative``.

Off Windows everything is a no-op (``ok`` False), but :meth:`PlayFx.push` still records the
queue so tests can check what would be shown. Never raises.
"""

from __future__ import annotations

import array
import copy
import logging
import math
import os
import sys
import tempfile
import threading
import time
import wave
from collections import deque
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

MAX_QUEUE = 3
STALE_S = 20.0                  # a queued badge not started within this delay is dropped
SAMPLE_RATE = 22050
#: per class: (frequencies of the notes in Hz, note length ms, volume 0..1)
TONES: dict[str, tuple[tuple[float, ...], int, float]] = {
    "brilliant": ((784.0, 1046.5, 1318.5), 85, 0.32),
    "great": ((880.0, 1174.7), 80, 0.28),
    "best": ((987.8,), 110, 0.24),
    "good": ((880.0,), 90, 0.20),
    "inaccuracy": ((523.3, 466.2), 90, 0.22),
    "mistake": ((440.0, 370.0), 100, 0.24),
    "blunder": ((392.0, 311.1, 261.6), 110, 0.26),
    "miss": ((493.9, 415.3), 100, 0.22),
}


# ------------------------------------------------------------------------------ sounds
def make_tone_wav(path: Path, cls: str) -> bool:
    """Write the short tone of ``cls`` as a 16-bit mono WAV (atomic). Never raises."""
    notes, ms, vol = TONES.get(cls, TONES["good"])
    n = int(SAMPLE_RATE * ms / 1000)
    fade = max(1, int(SAMPLE_RATE * 0.008))
    data = array.array("h")
    for f in notes:
        for i in range(n):
            env = min(1.0, i / fade, (n - 1 - i) / fade) * math.exp(-2.2 * i / n)
            s = math.sin(2 * math.pi * f * i / SAMPLE_RATE) + 0.25 * math.sin(4 * math.pi * f * i / SAMPLE_RATE)
            data.append(int(32767 * vol * env * s / 1.25))
    data.extend([0] * int(SAMPLE_RATE * 0.02))
    if sys.byteorder == "big":
        data.byteswap()
    tmp = Path(f"{path}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(data.tobytes())
        os.replace(tmp, path)
        return True
    except Exception as exc:
        log.debug("Cannot write tone %s: %s", path, exc)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def _sounds_dir() -> Path:
    try:
        from treeaicoach import paths

        d = Path(paths.cache_dir()) / "sounds"
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception:
        return Path(tempfile.gettempdir()) / "TreeAICoach" / "sounds"


def play_wav_path(cls: str) -> Path | None:
    """WAV of the class tone (bundled override, else generated once). None on failure."""
    try:
        from treeaicoach import paths

        bundled = Path(paths.asset_path("sounds", f"play_{cls}.wav"))
        if bundled.is_file():
            return bundled
    except Exception:
        pass
    p = _sounds_dir() / f"play_{cls}_v1.wav"
    try:
        if p.is_file() and p.stat().st_size > 44:
            return p
    except OSError:
        pass
    return p if make_tone_wav(p, cls) else None


def sound_wanted(cfg: Any, cls: str) -> bool:
    from treeaicoach.plays import NEGATIVE

    if not bool(getattr(cfg, "plays_sound", True)):
        return False
    return cls not in NEGATIVE or bool(getattr(cfg, "plays_sound_negative", False))


def play_sound(cls: str) -> bool:
    """Play the class tone asynchronously (Windows ``winsound``). Never raises."""
    if sys.platform != "win32":
        return False
    try:
        import winsound

        p = play_wav_path(cls)
        if p is None:
            return False
        winsound.PlaySound(str(p), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        return True
    except Exception:
        log.debug("play sound failed", exc_info=True)
        return False


# ------------------------------------------------------------------------------ geometry
def screen_and_minimap(a: Any, b: Any) -> tuple[tuple[int, int, int, int] | None, tuple[int, int, int, int] | None]:
    """``(screen, minimap)`` from a pair of rectangles given in EITHER order (the engine's
    ``_screen_rects`` returns ``(minimap, window)`` while this module used to read it as
    ``(screen, minimap)``: every badge was then laid out inside the minimap, at 60 %). The larger
    rectangle is the screen. Never raises."""
    from treeaicoach.layout import as_rect

    ra, rb = as_rect(a), as_rect(b)
    if ra is not None and rb is not None and ra[2] * ra[3] < rb[2] * rb[3]:
        return rb, ra
    if ra is not None and rb is None and ra[2] < 640 and ra[3] < 640:
        return None, ra                          # only a minimap-sized rect: it is the minimap
    return ra, rb


# ------------------------------------------------------------------------------ manager
class PlayFx:
    """Animation player (see module docstring).

    ``rects_provider()`` -> the screen and minimap rectangles in either order (any ``(x, y, w, h)``-like,
    or None; see :func:`screen_and_minimap`); ``visible()`` -> False hides the animations (overlay
    off / F11). Both are called from the fx thread. The badge goes to the layout's badge slot
    (:mod:`treeaicoach.layout`, the overlay's own layout when it runs): never over the minimap,
    the HUD card, the timers or the toasts.
    """

    def __init__(self, cfg: Any, rects_provider: Callable[[], tuple[Any, Any]] | None = None,
                 visible: Callable[[], bool] | None = None) -> None:
        self._lock = threading.Lock()
        self._cfg = copy.copy(cfg)
        self._rects = rects_provider
        self._visible = visible
        self._queue: deque[tuple[float, Any]] = deque()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.pushed: list[Any] = []          # every play pushed (diagnostics / tests)
        self.frames_drawn = 0
        self.ok = sys.platform == "win32"

    def apply_config(self, cfg: Any) -> None:
        with self._lock:
            self._cfg = copy.copy(cfg)

    def enabled(self) -> bool:
        with self._lock:
            cfg = self._cfg
        return bool(getattr(cfg, "plays_enabled", True)) and bool(getattr(cfg, "overlay_enabled", True))

    def push(self, play: Any) -> None:
        """Queue one badge (thread-safe, non-blocking). Never raises."""
        try:
            with self._lock:
                self.pushed.append(play)
                del self.pushed[:-100]
                if len(self._queue) >= MAX_QUEUE:
                    self._queue.popleft()
                self._queue.append((time.monotonic(), play))
            self._wake.set()
            if self.ok:
                self.start()
        except Exception:
            log.exception("PlayFx.push failed")

    def start(self) -> None:
        if not self.ok:
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="TreeAICoach-fx", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 1.5) -> None:
        self._stop.set()
        self._wake.set()
        th = self._thread
        if th is not None and th.is_alive() and th is not threading.current_thread():
            th.join(timeout)
        self._thread = None

    def _next(self) -> Any:
        with self._lock:
            now = time.monotonic()
            while self._queue:
                ts, play = self._queue.popleft()
                if now - ts <= STALE_S:
                    return play
            self._wake.clear()
            return None

    # ------------------------------------------------------------------ fx thread
    def _run(self) -> None:
        try:
            from treeaicoach.overlay import _get_api

            api = _get_api()
            if api.SetThreadDpiAwarenessContext is not None:   # physical pixels, like the overlay
                api.SetThreadDpiAwarenessContext(api.ctypes.c_void_p(-4))
        except Exception:
            log.debug("fx thread DPI awareness unavailable", exc_info=True)
        while not self._stop.is_set():
            self._wake.wait()
            if self._stop.is_set():
                break
            win = None
            try:
                while not self._stop.is_set():
                    play = self._next()
                    if play is None:
                        break
                    if not self.enabled() or (self._visible is not None and not self._visible()):
                        continue
                    if win is None:
                        from treeaicoach.overlay import LayeredWindow

                        win = LayeredWindow("fx", click_through=True)
                    self._animate(win, play)
            except Exception:
                log.exception("Play animation failed")
                self.ok = False
            finally:
                if win is not None:
                    try:
                        win.destroy()
                    except Exception:
                        pass
            if not self.ok:
                return

    def _animate(self, win: Any, play: Any) -> None:
        from treeaicoach import fx_render as fx
        from treeaicoach.overlay import pump_messages

        with self._lock:
            cfg = self._cfg
        try:
            pair = self._rects() if self._rects is not None else (None, None)
        except Exception:
            pair = (None, None)
        scr_t, mm_t = screen_and_minimap(*pair)
        size = str(getattr(play, "size", "big"))
        scale = fx.scale_for_screen(scr_t) if scr_t else 1.0
        x, y, _w, _h = fx.fx_layer_rect(scr_t, mm_t, str(getattr(cfg, "plays_position", "top_center")), size, scale,
                                        cfg=cfg)
        cls = str(getattr(play, "cls", "good"))
        if sound_wanted(cfg, cls):
            play_sound(cls)
        period = 1.0 / fx.FPS
        t0 = time.monotonic()
        while not self._stop.is_set():
            age = time.monotonic() - t0
            img = fx.render_play_frame(play, age, scale)
            if img is None:
                break
            win.update(img, x, y)
            if getattr(win, "failed", False):
                raise OSError("fx window update failed")
            self.frames_drawn += 1
            pump_messages()
            time.sleep(max(0.001, period - (time.monotonic() - t0 - age)))
        win.hide()


__all__ = ["PlayFx", "TONES", "make_tone_wav", "play_wav_path", "play_sound", "sound_wanted", "screen_and_minimap"]
