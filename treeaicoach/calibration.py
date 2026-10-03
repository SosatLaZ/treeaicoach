"""Manual minimap calibration (ARCHITECTURE.md §4.17 / §8.2).

:func:`run_calibration` grabs the game window (or the primary monitor) with
:class:`treeaicoach.capture.ScreenCapture`, shows it scaled in a modal window and lets the
user draw a square around the minimap (aspect ratio locked). "Détection auto" runs
:class:`treeaicoach.minimap_locator.MinimapLocator` on the same capture and proposes its result.
The returned dict is ``cfg.manual_minimap_rect``: ``{"screen_w", "screen_h", "x", "y", "w", "h"}``
in **physical screen pixels** (``x``, ``y`` absolute screen coordinates of the minimap's top-left
corner, ``screen_w`` x ``screen_h`` = size of the captured game window / monitor).

Controls: drag = new square (or move the current one when the drag starts inside it),
arrow keys = move by 1 px (Maj: 10 px), +/- = resize, Entrée = valider, Échap = annuler.
Qt widgets are only used from the main thread; the capture happens on it too (fast), the
automatic detection on a worker thread polled by a ``QTimer``. Nothing here raises: on any
failure the dialog shows a French message and the function returns None.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any

import numpy as np


log = logging.getLogger(__name__)

MIN_SIDE_PX = 32            # smallest accepted minimap side (screen pixels), = config.RECT_SIZE_MIN
HIDE_DELAY_S = 0.35         # time for the main window to disappear before the capture
DIALOG_FRACTION = 0.9       # dialog size relative to the screen
ZOOM_PX = 240               # side of the magnified view of the selection


@dataclass
class Selection:
    """Square in capture-image pixels (floats)."""

    x: float
    y: float
    side: float

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px <= self.x + self.side and self.y <= py <= self.y + self.side


def fit_scale(img_w: int, img_h: int, max_w: int, max_h: int) -> float:
    """Scale that fits an ``img_w x img_h`` image in ``max_w x max_h`` (never enlarges above 1)."""
    if img_w <= 0 or img_h <= 0 or max_w <= 0 or max_h <= 0:
        return 1.0
    return min(1.0, max_w / img_w, max_h / img_h)


def constrain_square(x0: float, y0: float, x1: float, y1: float, max_w: float, max_h: float,
                     min_side: float = 0.0) -> tuple[float, float, float]:
    """Square anchored at ``(x0, y0)`` towards ``(x1, y1)`` (side = max(|dx|, |dy|)),
    clamped to ``[0, max_w] x [0, max_h]``. Returns ``(x, y, side)``."""
    x0 = min(max(x0, 0.0), max_w)
    y0 = min(max(y0, 0.0), max_h)
    dx, dy = x1 - x0, y1 - y0
    side = max(abs(dx), abs(dy), min_side)
    room_x = (max_w - x0) if dx >= 0 else x0
    room_y = (max_h - y0) if dy >= 0 else y0
    side = max(0.0, min(side, room_x, room_y))
    x = x0 if dx >= 0 else x0 - side
    y = y0 if dy >= 0 else y0 - side
    return x, y, side


def clamp_selection(sel: Selection, img_w: int, img_h: int) -> Selection:
    """Keep a square inside the image (side limited to the smaller dimension)."""
    side = max(1.0, min(sel.side, float(img_w), float(img_h)))
    x = min(max(sel.x, 0.0), img_w - side)
    y = min(max(sel.y, 0.0), img_h - side)
    return Selection(x, y, side)


def selection_to_rect(sel: Selection, origin: Any) -> dict[str, int]:
    """``manual_minimap_rect`` dict (screen pixels) of a selection in a capture of ``origin``."""
    side = int(round(sel.side))
    return {
        "screen_w": int(origin.w),
        "screen_h": int(origin.h),
        "x": int(origin.x) + int(round(sel.x)),
        "y": int(origin.y) + int(round(sel.y)),
        "w": side,
        "h": side,
    }


def rect_to_selection(rect: Any, origin: Any) -> Selection | None:
    """Selection (image pixels) of a saved rect / located Rect, if it lies in the capture."""
    try:
        if isinstance(rect, dict):
            x, y, w, h = (float(rect[k]) for k in ("x", "y", "w", "h"))
            if int(rect.get("screen_w", origin.w)) != int(origin.w) or \
                    int(rect.get("screen_h", origin.h)) != int(origin.h):
                return None
        else:
            x, y, w, h = float(rect.x), float(rect.y), float(rect.w), float(rect.h)
        sx, sy = x - origin.x, y - origin.y
        side = min(w, h)
        if side < 8 or sx < -1 or sy < -1 or sx + side > origin.w + 1 or sy + side > origin.h + 1:
            return None
        return clamp_selection(Selection(sx, sy, side), int(origin.w), int(origin.h))
    except Exception:
        return None


def _primary_monitor() -> Any:
    from treeaicoach.capture import Rect, monitor_rects  # noqa: PLC0415

    mons = monitor_rects()
    for m in mons:
        if m.x == 0 and m.y == 0:
            return m
    return mons[0] if mons else Rect(0, 0, 1920, 1080)


def grab_screen(parent: Any = None) -> tuple[np.ndarray | None, Any]:
    """Capture the game window (or the primary monitor) with the app window hidden.

    ``parent`` is the launcher's Qt window (hidden during the capture). Returns
    ``(bgr image or None, origin Rect)``. Never raises.
    """
    from treeaicoach.capture import Rect, ScreenCapture, find_game_window  # noqa: PLC0415

    origin = None
    img = None
    hidden = False
    try:
        origin = find_game_window() or _primary_monitor()
        if parent is not None:
            try:
                from PySide6 import QtWidgets  # noqa: PLC0415

                parent.hide()
                QtWidgets.QApplication.processEvents()
                hidden = True
            except Exception:
                hidden = False
            time.sleep(HIDE_DELAY_S)
        cap = ScreenCapture()
        try:
            img = cap.grab(origin)
        finally:
            cap.close()
    except Exception:
        log.exception("Calibration capture failed")
        img = None
    finally:
        if hidden:
            try:
                parent.show()
            except Exception:
                pass
    return img, origin if origin is not None else Rect(0, 0, 1920, 1080)


def _dialog_class() -> Any:
    from PySide6 import QtCore, QtGui, QtWidgets  # noqa: PLC0415

    from treeaicoach import ui_widgets as W  # noqa: PLC0415

    Qt = QtCore.Qt

    class Canvas(QtWidgets.QWidget):
        """Scaled capture + the square selection (drag to draw, drag inside to move) + a zoom."""

        def __init__(self, dlg: Any) -> None:
            super().__init__()
            self.dlg = dlg
            self.setCursor(Qt.CrossCursor)
            self.setMinimumSize(600, 360)
            self._pix: Any = None
            if dlg.image is not None:
                rgb = np.ascontiguousarray(dlg.image[..., 2::-1])
                h, w = rgb.shape[:2]
                self._pix = QtGui.QPixmap.fromImage(QtGui.QImage(rgb.data, w, h, 3 * w,
                                                                 QtGui.QImage.Format_RGB888).copy())

        def geometry_of_image(self) -> tuple[float, float, float]:
            if self._pix is None:
                return 1.0, 0.0, 0.0
            w, h = self._pix.width(), self._pix.height()
            k = fit_scale(w, h, self.width() - 8, self.height() - 8)
            return k, (self.width() - w * k) / 2, (self.height() - h * k) / 2

        def to_image(self, p: Any) -> tuple[float, float]:
            k, ox, oy = self.geometry_of_image()
            return (p.x() - ox) / k, (p.y() - oy) / k

        def paintEvent(self, _e: Any) -> None:  # noqa: N802
            t = W.theme()
            qp = QtGui.QPainter(self)
            qp.setRenderHint(QtGui.QPainter.Antialiasing)
            qp.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
            if self._pix is None:
                qp.setPen(QtGui.QColor(t.secondary))
                qp.drawText(self.rect(), Qt.AlignCenter, "Aucune capture")
                qp.end()
                return
            k, ox, oy = self.geometry_of_image()
            dst = QtCore.QRectF(ox, oy, self._pix.width() * k, self._pix.height() * k)
            qp.drawPixmap(dst, self._pix, QtCore.QRectF(self._pix.rect()))
            sel = self.dlg.sel
            if sel is not None:
                r = QtCore.QRectF(ox + sel.x * k, oy + sel.y * k, sel.side * k, sel.side * k)
                shade = QtGui.QPainterPath()
                shade.addRect(dst)
                hole = QtGui.QPainterPath()
                hole.addRect(r)
                qp.fillPath(shade.subtracted(hole), QtGui.QColor(0, 0, 0, 120))
                qp.setPen(QtGui.QPen(QtGui.QColor(t.accent), 2))
                qp.setBrush(Qt.NoBrush)
                qp.drawRect(r)
                zs = int(min(ZOOM_PX, self.height() * 0.45, self.width() * 0.3))
                src = QtCore.QRectF(sel.x, sel.y, sel.side, sel.side)
                zr = QtCore.QRectF(self.width() - zs - 12, 12, zs, zs)
                qp.drawPixmap(zr, self._pix, src)
                qp.drawRoundedRect(zr, 4, 4)
            qp.end()

        def mousePressEvent(self, e: Any) -> None:  # noqa: N802
            self.dlg.press(*self.to_image(e.position()))

        def mouseMoveEvent(self, e: Any) -> None:  # noqa: N802
            if e.buttons() & Qt.LeftButton:
                self.dlg.motion(*self.to_image(e.position()))

        def mouseReleaseEvent(self, _e: Any) -> None:  # noqa: N802
            self.dlg.release()

    class Dialog(QtWidgets.QDialog):
        def __init__(self, parent: Any, cfg: Any, image: np.ndarray | None, origin: Any) -> None:
            super().__init__(parent)
            self.cfg = cfg
            self.origin = origin
            self.image = image if isinstance(image, np.ndarray) and image.ndim == 3 and image.size else None
            self.result_rect: dict[str, int] | None = None
            self.sel: Selection | None = None
            self._drag: tuple[str, float, float, Selection | None] | None = None
            self._q: queue.SimpleQueue[Any] = queue.SimpleQueue()
            self._detecting = False
            self.setWindowTitle("Calibrer la minimap")
            self.setModal(True)
            lay = QtWidgets.QVBoxLayout(self)
            lay.setContentsMargins(24, 20, 24, 18)
            lay.setSpacing(12)
            lay.addWidget(W.label("Calibrer la minimap", "headline"))
            lay.addWidget(W.label("Trace un carré autour de la minimap puis valide. Tu peux aussi essayer « Détection "
                                  "auto ». Flèches : ajuster · +/- : taille · Entrée : valider.", "secondary", wrap=True))
            self.canvas = Canvas(self)
            lay.addWidget(self.canvas, 1)
            foot = QtWidgets.QHBoxLayout()
            self.status = W.label("", "secondary", wrap=True)
            foot.addWidget(self.status, 1)
            self.btn_auto = W.button("Détection auto", self.auto_detect)
            foot.addWidget(self.btn_auto)
            foot.addWidget(W.button("Annuler", self.reject))
            self.btn_ok = W.button("Valider", self.validate, "primary")
            self.btn_ok.setDefault(True)
            foot.addWidget(self.btn_ok)
            lay.addLayout(foot)
            scr = QtGui.QGuiApplication.primaryScreen()
            if scr is not None:
                ag = scr.availableGeometry()
                self.resize(int(ag.width() * DIALOG_FRACTION * 0.8), int(ag.height() * DIALOG_FRACTION * 0.8))
            self.setMinimumSize(760, 520)
            if self.image is None:
                self.btn_auto.setEnabled(False)
                self.set_status("Capture de l'écran impossible. Vérifie que le jeu est en mode « Sans bordure » "
                                "puis réessaie.", "danger")
            else:
                saved = getattr(cfg, "manual_minimap_rect", None)
                self.sel = rect_to_selection(saved, origin) if saved else None
                self.set_status(self.sel_text() if self.sel else
                                "Clique-glisse sur la minimap (en bas à droite, en général).")
            self.update_ok()
            self._timer = QtCore.QTimer(self)
            self._timer.timeout.connect(self.poll)
            self._timer.start(100)

        def set_status(self, text: str, tone: str = "") -> None:
            self.status.setText(text)
            W.set_prop(self.status, "tone", tone)

        def sel_text(self) -> str:
            if self.sel is None:
                return ""
            r = selection_to_rect(self.sel, self.origin)
            return f"Sélection : {r['w']} × {r['h']} px à ({r['x']}, {r['y']})"

        def update_ok(self) -> None:
            self.btn_ok.setEnabled(self.sel is not None and self.sel.side >= MIN_SIDE_PX)

        def press(self, x: float, y: float) -> None:
            if self.image is None:
                return
            if self.sel is not None and self.sel.contains(x, y):
                self._drag = ("move", x, y, Selection(self.sel.x, self.sel.y, self.sel.side))
            else:
                self._drag = ("new", x, y, None)

        def motion(self, x: float, y: float) -> None:
            if self._drag is None or self.image is None:
                return
            h, w = self.image.shape[:2]
            kind, x0, y0, start = self._drag
            if kind == "move" and start is not None:
                self.sel = clamp_selection(Selection(start.x + x - x0, start.y + y - y0, start.side), w, h)
            else:
                sx, sy, side = constrain_square(x0, y0, x, y, w, h)
                self.sel = Selection(sx, sy, side)
            self.canvas.update()
            self.set_status(self.sel_text())
            self.update_ok()

        def release(self) -> None:
            self._drag = None
            self.update_ok()

        def keyPressEvent(self, e: Any) -> None:  # noqa: N802
            k = e.key()
            step = 10 if e.modifiers() & Qt.ShiftModifier else 1
            moves = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1)}
            if k in moves and self.sel is not None and self.image is not None:
                dx, dy = moves[k]
                h, w = self.image.shape[:2]
                self.sel = clamp_selection(Selection(self.sel.x + dx * step, self.sel.y + dy * step, self.sel.side),
                                           w, h)
            elif k in (Qt.Key_Plus, Qt.Key_Equal, Qt.Key_Minus) and self.sel is not None and self.image is not None:
                d = -2 if k == Qt.Key_Minus else 2
                h, w = self.image.shape[:2]
                s = self.sel
                self.sel = clamp_selection(Selection(s.x - d / 2, s.y - d / 2, max(MIN_SIDE_PX, s.side + d)), w, h)
            elif k in (Qt.Key_Return, Qt.Key_Enter):
                self.validate()
                return
            else:
                super().keyPressEvent(e)
                return
            self.canvas.update()
            self.set_status(self.sel_text())
            self.update_ok()

        def auto_detect(self) -> None:
            if self.image is None or self._detecting:
                return
            self._detecting = True
            self.btn_auto.setEnabled(False)
            self.btn_auto.setText("Recherche…")
            self.set_status("Recherche automatique de la minimap…")
            img, origin = self.image, self.origin
            side = str(getattr(self.cfg, "minimap_side", "auto") or "auto")

            def job() -> None:
                try:
                    from treeaicoach.minimap_locator import MinimapLocator  # noqa: PLC0415

                    self._q.put(("auto", MinimapLocator().locate(img, origin, side)))
                except Exception as exc:
                    log.exception("Automatic minimap detection failed")
                    self._q.put(("auto_error", exc))

            threading.Thread(target=job, name="TreeAI-calib-locate", daemon=True).start()

        def poll(self) -> None:
            try:
                while True:
                    kind, payload = self._q.get_nowait()
                    self._detecting = False
                    self.btn_auto.setEnabled(True)
                    self.btn_auto.setText("Détection auto")
                    if kind == "auto" and payload is not None:
                        sel = rect_to_selection(payload.rect, self.origin)
                        if sel is None:
                            self.set_status("Minimap trouvée hors de la capture : trace le carré à la main.", "danger")
                            continue
                        self.sel = sel
                        self.canvas.update()
                        self.update_ok()
                        self.set_status(f"Minimap trouvée (confiance {round(100 * payload.score)} %). "
                                        f"{self.sel_text()} : valide ou ajuste.", "ok")
                    elif kind == "auto":
                        self.set_status("Minimap introuvable automatiquement : trace le carré à la main.", "danger")
                    else:
                        self.set_status(f"Détection automatique impossible : {payload}", "danger")
            except queue.Empty:
                pass
            except Exception:
                log.exception("Calibration poll failed")

        def validate(self) -> None:
            if self.sel is None or self.sel.side < MIN_SIDE_PX:
                self.set_status("Trace d'abord un carré autour de la minimap.", "danger")
                return
            self.result_rect = selection_to_rect(self.sel, self.origin)
            log.info("Manual minimap calibration: %s", self.result_rect)
            self.accept()

    return Dialog


class CalibrationDialog:
    """Wrapper of the Qt dialog (built lazily: importing this module needs no GUI toolkit)."""

    def __init__(self, parent: Any, cfg: Any, image: np.ndarray | None, origin: Any) -> None:
        self.dialog = _dialog_class()(parent, cfg, image, origin)

    @property
    def sel(self) -> Selection | None:
        return self.dialog.sel

    def validate(self) -> None:
        self.dialog.validate()

    def cancel(self) -> None:
        self.dialog.reject()

    def wait(self) -> dict[str, int] | None:
        """Modal (nested event loop) until the dialog is closed; the rect after "Valider"."""
        try:
            self.dialog.exec()
        except Exception:
            log.debug("calibration dialog failed", exc_info=True)
        res = self.dialog.result_rect
        self.dialog.deleteLater()
        return res


def run_calibration(parent: Any, cfg: Any, *, screenshot: tuple[np.ndarray, Any] | None = None) -> dict | None:
    """Modal calibration; returns the ``manual_minimap_rect`` dict, or None if cancelled.

    ``screenshot`` = ``(bgr image, origin Rect)`` skips the screen capture (tests). Never raises.
    """
    try:
        if screenshot is not None:
            img, origin = screenshot
        else:
            img, origin = grab_screen(parent)
        return CalibrationDialog(parent, cfg, img, origin).wait()
    except Exception:
        log.exception("Minimap calibration failed")
        return None


__all__ = ["run_calibration", "CalibrationDialog", "Selection", "constrain_square", "fit_scale",
           "clamp_selection", "selection_to_rect", "rect_to_selection", "grab_screen"]
