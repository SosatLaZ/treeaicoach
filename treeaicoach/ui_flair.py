"""League flavour and launch sequence of the launcher (docs/DESIGN.md, "Hextech discret").

* :class:`HeroCard`: the Accueil hero. The current (or last played) champion's loading art, darkened
  and faded into the card on its right side, one hairline frame. Painted once per size (cached
  pixmap), no timer, no animation.
* :func:`ensure_art`: Data Dragon loading art of a champion (308 x 560 JPEG, about 40 Ko), cached in
  ``paths.cache_dir()/splash``; downloaded on a worker thread (never on the window thread); without
  network the bundled 64 px portrait is used. Nothing from the game client's own UI or logos.
* :class:`BootSplash`: a quiet launch screen (under 0.8 s): the name, one thin progress line and ONE
  status line (the first REAL check that is not ready, else « Prêt »), then a short fade. Any click
  or key skips it. One animation, stopped and deleted at the end: nothing runs when idle.
"""

from __future__ import annotations

import importlib.util
import logging
import re
import sys
import threading
from pathlib import Path
from typing import Any, Callable

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

from treeaicoach import ui_widgets as W

log = logging.getLogger(__name__)

ART_URL = "https://ddragon.leagueoflegends.com/cdn/img/champion/loading/{key}_0.jpg"
ART_MAX_BYTES = 160 * 1024
ROLE_FR = {"top": "Top", "jungle": "Jungle", "mid": "Mid", "middle": "Mid", "adc": "ADC", "bottom": "ADC",
           "support": "Support", "utility": "Support"}

_art_lock = threading.Lock()
_art_failed: set[str] = set()
_art_inflight: set[str] = set()
_fetch_hook: Callable[[str], bytes] | None = None       # tests: replaces the HTTP GET


# ======================================================================================
# Champion art
# ======================================================================================
def champion_key(alias: Any) -> str:
    """A Data Dragon champion id ("MonkeyKing", "Garen") if it is safe to use in a path / URL, else ""."""
    a = str(alias or "").strip()
    return a if re.fullmatch(r"[A-Za-z0-9]{2,24}", a) else ""


def _art_dir() -> Path | None:
    try:
        from treeaicoach import paths  # noqa: PLC0415

        d = Path(paths.cache_dir()) / "splash"
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception:
        return None


def art_path(alias: Any) -> Path | None:
    """Cached loading art on disk, else the bundled portrait, else None. Never raises."""
    key = champion_key(alias)
    if not key:
        return None
    try:
        d = _art_dir()
        if d is not None and (d / f"{key}.jpg").is_file():
            return d / f"{key}.jpg"
        from treeaicoach.paths import asset_path  # noqa: PLC0415

        p = Path(asset_path("icons", "champions", f"{key}.png"))
        return p if p.is_file() else None
    except Exception:
        return None


def has_full_art(alias: Any) -> bool:
    p = art_path(alias)
    return p is not None and p.suffix == ".jpg"


def fetch_art(alias: Any) -> bool:
    """Download the loading art (blocking: call from a worker). True when it is in the cache."""
    key = champion_key(alias)
    d = _art_dir()
    if not key or d is None:
        return False
    if (d / f"{key}.jpg").is_file():
        return True
    if key in _art_failed:
        return False
    url = ART_URL.format(key=key)
    try:
        if _fetch_hook is not None:
            data = _fetch_hook(url)
        else:
            from treeaicoach.meta_builds import get_bytes  # noqa: PLC0415

            data = get_bytes(url, "image/jpeg,*/*", timeout=6.0, limit=ART_MAX_BYTES)
        if not data or len(data) > ART_MAX_BYTES or not data.startswith(b"\xff\xd8"):
            raise ValueError("not a small JPEG")
        tmp = d / f".{key}.tmp"
        tmp.write_bytes(data)
        tmp.replace(d / f"{key}.jpg")
        return True
    except Exception as exc:
        log.debug("champion art %s not downloaded: %s", key, exc)
        _art_failed.add(key)
        return False


def ensure_art(app: Any, alias: Any, done: Callable[[], None]) -> None:
    """Start the download of ``alias``'s art if it is missing (one attempt per session); ``done()``
    runs on the window thread when a new file landed."""
    key = champion_key(alias)
    if not key or has_full_art(key) or key in _art_failed:
        return
    with _art_lock:
        if key in _art_inflight:
            return
        _art_inflight.add(key)

    def job() -> bool:
        try:
            return fetch_art(key)
        finally:
            with _art_lock:
                _art_inflight.discard(key)

    app.run_job(job, lambda ok: done() if ok else None, None, name="TreeAI-ui-art")


# ======================================================================================
# Hero card
# ======================================================================================
class HeroCard(QtWidgets.QFrame):
    """Glass card with the champion art on its right third. Children (labels, buttons) are laid out by
    the caller on the left; ``set_champion`` swaps the art. Paint cost: one cached pixmap blit."""

    RADIUS = W.GROUP_RADIUS
    ART_FRAC = 0.46                    # share of the width the art may cover (it fades into the glass)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Hero")
        self.setAttribute(Qt.WA_StyledBackground, False)
        self._alias = ""
        self._src: QtGui.QPixmap | None = None
        self._scaled: tuple[tuple[int, int, float], QtGui.QPixmap] | None = None

    def set_champion(self, alias: Any) -> None:
        alias = champion_key(alias)
        p = art_path(alias) if alias else None
        sig = (alias, str(p))
        if sig == getattr(self, "_sig", None):
            return
        self._sig = sig
        self._alias = alias
        pm = QtGui.QPixmap(str(p)) if p is not None else QtGui.QPixmap()
        self._src = pm if not pm.isNull() else None
        self._scaled = None
        self.update()

    @property
    def has_art(self) -> bool:
        return self._src is not None

    def _art_pixmap(self) -> QtGui.QPixmap | None:
        """The art cropped to the card's art zone, darkened, as ONE pixmap for the current size."""
        if self._src is None:
            return None
        dpr = self.devicePixelRatioF()
        w, h = self.width(), self.height()
        key = (w, h, dpr)
        if self._scaled is not None and self._scaled[0] == key:
            return self._scaled[1]
        zw = int(w * self.ART_FRAC)
        out = QtGui.QPixmap(int(zw * dpr), int(h * dpr))
        out.setDevicePixelRatio(dpr)
        out.fill(Qt.transparent)
        qp = QtGui.QPainter(out)
        qp.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        sw, sh = self._src.width(), self._src.height()
        if sw <= 96:                                    # bundled portrait: a centred, soft tile
            side = min(zw, h) - 24
            qp.setOpacity(0.55)
            qp.drawPixmap(QtCore.QRectF(zw - side - 16, (h - side) / 2, side, side), self._src,
                          QtCore.QRectF(0, 0, sw, sh))
        else:                                           # loading art: cover the zone around the head
            scale = max(zw / sw, h / sh)
            dw, dh = sw * scale, sh * scale
            dy = min(0.0, max(h - dh, h * 0.5 - dh * 0.27))   # centre the head (about a quarter down)
            qp.drawPixmap(QtCore.QRectF((zw - dw) / 2, dy, dw, dh), self._src, QtCore.QRectF(0, 0, sw, sh))
        # darken, then fade the left edge into the glass (painter gradient, not a style sheet one)
        qp.setCompositionMode(QtGui.QPainter.CompositionMode_SourceAtop)
        qp.fillRect(QtCore.QRectF(0, 0, zw, h), QtGui.QColor(16, 17, 19, 140))
        grad = QtGui.QLinearGradient(0, 0, zw, 0)
        grad.setColorAt(0.0, QtGui.QColor(0, 0, 0, 0))
        grad.setColorAt(0.55, QtGui.QColor(0, 0, 0, 255))
        qp.setCompositionMode(QtGui.QPainter.CompositionMode_DestinationIn)
        qp.fillRect(QtCore.QRectF(0, 0, zw, h), QtGui.QBrush(grad))
        qp.end()
        self._scaled = (key, out)
        return out

    def paintEvent(self, e: Any) -> None:  # noqa: N802
        qp = QtGui.QPainter(self)
        qp.setRenderHint(QtGui.QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        rect = QtCore.QRectF(0.5, 0.5, w - 1, h - 1)
        path = QtGui.QPainterPath()
        path.addRoundedRect(rect, self.RADIUS, self.RADIUS)
        qp.fillPath(path, QtGui.QColor(W.theme().group))
        art = self._art_pixmap()
        if art is not None:
            qp.save()
            qp.setClipPath(path)
            qp.setOpacity(0.55)
            qp.drawPixmap(w - int(w * self.ART_FRAC), 0, art)
            qp.restore()
        qp.setBrush(Qt.NoBrush)
        qp.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 16), 1))
        qp.drawPath(path)
        qp.end()


def role_fr(role: Any) -> str:
    return ROLE_FR.get(str(role or "").strip().lower(), str(role or "").strip().capitalize())


# ======================================================================================
# Real start-up checks (cheap, no network, no process listing)
# ======================================================================================
def boot_checks(cfg: Any = None) -> list[tuple[str, bool | None, str]]:
    """``(label, ok, detail)``: ok True = ready, False = missing, None = not applicable / waiting.
    Every line reads real state; none of them blocks for more than a few milliseconds."""
    out: list[tuple[str, bool | None, str]] = []

    def have(mod: str) -> bool:
        try:
            return importlib.util.find_spec(mod) is not None
        except Exception:
            return False

    try:
        from treeaicoach.paths import asset_path  # noqa: PLC0415

        models = list(Path(asset_path("model")).glob("*.onnx"))
    except Exception:
        models = []
    out.append(("détecteur", bool(models) and have("onnxruntime"), "modèle chargé" if models else "modèle absent"))
    out.append(("capture", have("mss"), "écran" if have("mss") else "indisponible"))
    lcu_ok: bool | None = None
    try:     # only if lcu is already loaded: importing it (http.client, ssl) costs ~50 ms before the first
        lcu = sys.modules.get("treeaicoach.lcu")     # paint, and a waiting client is never shown anyway
        if lcu is None:
            raise LookupError
        for d in lcu.candidate_dirs():
            if lcu.read_lockfile(d) is not None:
                lcu_ok = True
                break
    except Exception:
        lcu_ok = None
    out.append(("client LoL", True if lcu_ok else None, "connecté" if lcu_ok else "en attente"))
    voice_ok = sys.platform == "win32" or have("edge_tts") or have("pyttsx3")
    out.append(("voix", voice_ok, "prête" if voice_ok else "aucune voix"))
    ov = sys.platform == "win32"
    on = bool(getattr(cfg, "overlay_enabled", True)) if cfg is not None else True
    out.append(("overlay", ov and on if ov else None, "prêt" if ov and on else ("coupé" if ov else "Windows seulement")))
    return out


# ======================================================================================
# Boot splash
# ======================================================================================
class BootSplash(QtWidgets.QWidget):
    """Covers its parent for under 0.5 s: a thin line fills (300 ms), then a 120 ms fade.
    ``finished`` is emitted once, whatever ends it (end, click, key, parent closing)."""

    finished = QtCore.Signal()
    FILL_MS = 300
    FADE_MS = 120

    def __init__(self, parent: QtWidgets.QWidget, version: str, checks: list[tuple[str, bool | None, str]]) -> None:
        super().__init__(parent)
        self.setObjectName("BootSplash")
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.ArrowCursor)
        self._version = version
        self._checks = list(checks)
        self._t = 0.0                       # 0..1 progress of the fill
        self._fade = 0.0
        self._done = False
        title_f = QtGui.QFont(W.pick_family(W.DISPLAY_FAMILIES))
        title_f.setPixelSize(17)
        title_f.setWeight(QtGui.QFont.DemiBold)
        small_f = QtGui.QFont(W.pick_family())
        small_f.setPixelSize(W.SMALL_PX)
        self._fonts = (title_f, small_f)
        pending = [f"{label.capitalize()} : {detail}" for label, ok, detail in self._checks if ok is False]
        self._line = pending[0] if pending else "Prêt"
        self._effect = QtWidgets.QGraphicsOpacityEffect(self)
        self._effect.setOpacity(1.0)
        self.setGraphicsEffect(self._effect)
        self._anim = QtCore.QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(self.FILL_MS + 40)
        self._anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._tick)
        self._anim.finished.connect(self._start_fade)
        self._fader = QtCore.QVariantAnimation(self)
        self._fader.setStartValue(1.0)
        self._fader.setEndValue(0.0)
        self._fader.setDuration(self.FADE_MS)
        self._fader.valueChanged.connect(lambda v: self._effect.setOpacity(float(v)))
        self._fader.finished.connect(self._end)
        parent.installEventFilter(self)
        self.setGeometry(parent.rect())

    # ------------------------------------------------------------------ life cycle
    def start(self) -> None:
        self.raise_()
        self.show()
        self.setFocus()
        self._anim.start()

    def eventFilter(self, obj: Any, ev: Any) -> bool:  # noqa: N802
        if obj is self.parent() and ev.type() == QtCore.QEvent.Resize:
            self.setGeometry(self.parent().rect())
        return False

    def _tick(self, v: Any) -> None:
        self._t = float(v)
        self.update()

    def _start_fade(self) -> None:
        if not self._done and self._fader.state() != QtCore.QAbstractAnimation.Running:
            self._t = 1.0
            self._fader.start()

    def skip(self) -> None:
        if self._done:
            return
        self._anim.stop()
        self._t = 1.0
        self._fader.setDuration(100)
        self._start_fade()

    def _end(self) -> None:
        if self._done:
            return
        self._done = True
        self._anim.stop()
        self._fader.stop()
        try:
            self.parent().removeEventFilter(self)
        except Exception:
            pass
        self.hide()
        self.finished.emit()
        self.deleteLater()

    def abort(self) -> None:
        """Window closing: no fade, nothing left running."""
        self._end()

    def mousePressEvent(self, e: Any) -> None:  # noqa: N802
        self.skip()

    def keyPressEvent(self, e: Any) -> None:  # noqa: N802
        self.skip()

    # ------------------------------------------------------------------ paint
    def paintEvent(self, e: Any) -> None:  # noqa: N802
        t = W.theme()
        qp = QtGui.QPainter(self)
        qp.setRenderHint(QtGui.QPainter.Antialiasing, True)
        qp.fillRect(self.rect(), QtGui.QColor(t.window))
        title_f, small_f = self._fonts
        bw = 220
        x0 = (self.width() - bw) / 2
        y = self.height() / 2 - 28
        qp.setFont(title_f)
        qp.setPen(QtGui.QColor(t.text))
        qp.drawText(QtCore.QRectF(x0 - 100, y, bw + 200, 24), Qt.AlignCenter, "TreeAI Coach")
        py = y + 40
        qp.fillRect(QtCore.QRectF(x0, py, bw, 2), QtGui.QColor(t.separator))
        qp.fillRect(QtCore.QRectF(x0, py, bw * self._t, 2), QtGui.QColor(t.accent))
        qp.setFont(small_f)
        qp.setPen(QtGui.QColor(t.tertiary))
        qp.drawText(QtCore.QRectF(x0 - 100, py + 14, bw + 200, 20), Qt.AlignCenter,
                    self._line if self._t >= 0.5 else self._version)
        qp.end()
