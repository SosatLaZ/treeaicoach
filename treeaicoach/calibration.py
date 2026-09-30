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
Tk widgets are only used from the Tk thread; the capture happens on it too (fast), the
automatic detection on a worker thread polled with ``after``. Nothing here raises: on any
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

# palette (same as ui.py)
BG = "#010A13"
PANEL = "#0A1428"
PANEL_HI = "#0F1D36"
BORDER = "#1E2328"
GOLD = "#C8AA6E"
GOLD_HOVER = "#DCC28E"
GOLD_DARK = "#785A28"
TEXT = "#F0E6D2"
MUTED = "#A09B8C"
DIM = "#5B5A56"
TEAL = "#0AC8B9"
DANGER = "#E84057"
ON_GOLD = "#1A1408"

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

    Returns ``(bgr image or None, origin Rect)``. Never raises.
    """
    from treeaicoach.capture import Rect, ScreenCapture, find_game_window  # noqa: PLC0415

    origin = None
    img = None
    hidden = False
    try:
        origin = find_game_window() or _primary_monitor()
        if parent is not None:
            try:
                parent.withdraw()
                parent.update()
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
                parent.deiconify()
                parent.update_idletasks()
            except Exception:
                pass
    return img, origin if origin is not None else Rect(0, 0, 1920, 1080)


class CalibrationDialog:
    """The modal calibration window. ``result`` holds the rect dict after "Valider"."""

    def __init__(self, parent: Any, cfg: Any, image: np.ndarray | None, origin: Any) -> None:
        import tkinter as tk  # noqa: PLC0415

        import customtkinter as ctk  # noqa: PLC0415

        self.ctk = ctk
        self.cfg = cfg
        self.parent = parent
        self.origin = origin
        self.image = image if isinstance(image, np.ndarray) and image.ndim == 3 and image.size else None
        self.result: dict[str, int] | None = None
        self.sel: Selection | None = None
        self.auto_sel: Selection | None = None
        self._drag: tuple[str, float, float, Selection | None] | None = None
        self._scale = 1.0
        self._off = (0, 0)
        self._photo: Any = None
        self._zoom_photo: Any = None
        self._q: queue.SimpleQueue[Any] = queue.SimpleQueue()
        self._detecting = False
        self._closed = False

        fam = "Segoe UI"
        try:
            import tkinter.font as tkfont  # noqa: PLC0415

            fams = set(tkfont.families(parent))
            fam = next((f for f in ("Segoe UI", "Inter", "Noto Sans", "DejaVu Sans") if f in fams), "TkDefaultFont")
        except Exception:
            pass
        f_title = ctk.CTkFont(family=fam, size=20, weight="bold")
        f_body = ctk.CTkFont(family=fam, size=13)
        f_small = ctk.CTkFont(family=fam, size=12)
        f_btn = ctk.CTkFont(family=fam, size=13, weight="bold")

        top = ctk.CTkToplevel(parent)
        self.top = top
        top.title("Calibrer la minimap")
        top.configure(fg_color=BG)
        try:
            top.transient(parent)
        except Exception:
            pass
        self._size_window()
        top.minsize(760, 520)
        top.grid_columnconfigure(0, weight=1)
        top.grid_rowconfigure(1, weight=1)
        top.protocol("WM_DELETE_WINDOW", self.cancel)

        head = ctk.CTkFrame(top, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 10))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="Calibrer la minimap", font=f_title, text_color=GOLD,
                     fg_color="transparent", anchor="w").grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(head, text="Trace un carré autour de la minimap puis valide. Tu peux aussi essayer "
                                "« Détection auto ». Flèches : ajuster · +/- : taille · Entrée : valider.",
                     font=f_small, text_color=MUTED, fg_color="transparent", anchor="w",
                     justify="left").grid(row=1, column=0, sticky="w")

        box = ctk.CTkFrame(top, fg_color=PANEL, corner_radius=12, border_width=1, border_color=BORDER)
        box.grid(row=1, column=0, sticky="nsew", padx=24)
        box.grid_columnconfigure(0, weight=1)
        box.grid_rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(box, bg=PANEL, highlightthickness=0, bd=0, cursor="crosshair")
        self.canvas.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self.canvas.bind("<Configure>", lambda _e: self._on_resize())
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        foot = ctk.CTkFrame(top, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=24, pady=16)
        foot.grid_columnconfigure(0, weight=1)
        self.status = ctk.CTkLabel(foot, text="", font=f_body, text_color=TEXT, fg_color="transparent",
                                   anchor="w", justify="left")
        self.status.grid(row=0, column=0, sticky="w")
        common = dict(height=38, corner_radius=8, font=f_btn, text_color_disabled=DIM)
        self.btn_auto = ctk.CTkButton(foot, text="Détection auto", width=150, fg_color=PANEL_HI,
                                      hover_color="#16284A", text_color=TEXT, border_width=1,
                                      border_color=GOLD_DARK, command=self._safe(self.auto_detect), **common)
        self.btn_auto.grid(row=0, column=1, padx=(12, 8))
        ctk.CTkButton(foot, text="Annuler", width=120, fg_color="transparent", hover_color=PANEL_HI,
                      text_color=MUTED, border_width=1, border_color=BORDER, command=self._safe(self.cancel),
                      **common).grid(row=0, column=2, padx=(0, 8))
        self.btn_ok = ctk.CTkButton(foot, text="Valider", width=140, fg_color=GOLD, hover_color=GOLD_HOVER,
                                    text_color=ON_GOLD, command=self._safe(self.validate), **common)
        self.btn_ok.grid(row=0, column=3)

        for seq, fn in (("<Escape>", lambda _e: self.cancel()), ("<Return>", lambda _e: self.validate()),
                        ("<KP_Enter>", lambda _e: self.validate()),
                        ("<Left>", lambda e: self._nudge(-1, 0, e)), ("<Right>", lambda e: self._nudge(1, 0, e)),
                        ("<Up>", lambda e: self._nudge(0, -1, e)), ("<Down>", lambda e: self._nudge(0, 1, e)),
                        ("<plus>", lambda _e: self._grow(2)), ("<KP_Add>", lambda _e: self._grow(2)),
                        ("<equal>", lambda _e: self._grow(2)),
                        ("<minus>", lambda _e: self._grow(-2)), ("<KP_Subtract>", lambda _e: self._grow(-2))):
            top.bind(seq, self._safe(fn))

        if self.image is None:
            self.btn_auto.configure(state="disabled")
            self._set_status("Capture de l'écran impossible. Vérifie que le jeu est en mode « Sans bordure » "
                             "puis réessaie.", DANGER)
        else:
            saved = getattr(cfg, "manual_minimap_rect", None)
            self.sel = rect_to_selection(saved, origin) if saved else None
            self._set_status(self._sel_text() if self.sel else
                             "Clique-glisse sur la minimap (en bas à droite, en général).", TEXT if self.sel else MUTED)
        self._update_ok()
        self._set_icon()
        top.after(120, self._grab_focus)
        top.after(100, self._poll)

    # ---------------------------------------------------------------- window
    def _size_window(self) -> None:
        top = self.top
        try:
            scaling = float(self.ctk.ScalingTracker.get_window_scaling(top))
        except Exception:
            scaling = 1.0
        sw, sh = int(top.winfo_screenwidth()), int(top.winfo_screenheight())
        w = int(sw * DIALOG_FRACTION / scaling)
        h = int(sh * DIALOG_FRACTION / scaling)
        x = int((sw - w * scaling) / 2)
        y = int((sh - h * scaling) / 3)
        top.geometry(f"{max(760, w)}x{max(520, h)}+{max(0, x)}+{max(0, y)}")

    def _set_icon(self) -> None:
        try:
            import sys  # noqa: PLC0415

            from treeaicoach.ui import app_icon_path  # noqa: PLC0415

            ico = app_icon_path("ico")
            if sys.platform == "win32" and ico is not None:
                self.top.after(260, lambda: self.top.iconbitmap(str(ico)))
        except Exception:
            pass

    def _grab_focus(self) -> None:
        try:
            self.top.lift()
            self.top.focus_force()
            self.top.grab_set()
        except Exception:
            log.debug("Calibration grab failed", exc_info=True)

    def _safe(self, fn: Any) -> Any:
        def wrapper(*args: Any) -> Any:
            try:
                return fn(*args)
            except Exception as exc:
                log.exception("Calibration action failed")
                try:
                    self._set_status(f"Erreur : {exc}", DANGER)
                except Exception:
                    pass
                return None
        return wrapper

    def _set_status(self, text: str, color: str = TEXT) -> None:
        self.status.configure(text=text, text_color=color)

    def _sel_text(self) -> str:
        if self.sel is None:
            return ""
        r = selection_to_rect(self.sel, self.origin)
        return f"Carré : {r['w']} × {r['h']} px à la position ({r['x']}, {r['y']})"

    def _update_ok(self) -> None:
        ok = self.sel is not None and self.sel.side >= MIN_SIDE_PX
        self.btn_ok.configure(state="normal" if ok else "disabled")

    # ---------------------------------------------------------------- drawing
    def _on_resize(self) -> None:
        if self.image is None:
            self._draw_empty()
            return
        cw = max(10, int(self.canvas.winfo_width()))
        ch = max(10, int(self.canvas.winfo_height()))
        h, w = self.image.shape[:2]
        scale = fit_scale(w, h, cw, ch)
        dw, dh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
        if self._photo is None or (dw, dh) != (self._photo.width(), self._photo.height()):
            import cv2  # noqa: PLC0415
            from PIL import Image, ImageTk  # noqa: PLC0415

            small = cv2.resize(self.image, (dw, dh), interpolation=cv2.INTER_AREA)
            self._photo = ImageTk.PhotoImage(Image.fromarray(np.ascontiguousarray(small[..., ::-1])),
                                             master=self.canvas)
        self._scale = scale
        self._off = ((cw - dw) // 2, (ch - dh) // 2)
        self._redraw()

    def _draw_empty(self) -> None:
        c = self.canvas
        c.delete("all")
        c.create_text(max(10, c.winfo_width()) / 2, max(10, c.winfo_height()) / 2,
                      text="Aucune capture disponible", fill=DIM, font=("TkDefaultFont", 14))

    def _to_canvas(self, x: float, y: float) -> tuple[float, float]:
        return self._off[0] + x * self._scale, self._off[1] + y * self._scale

    def _to_image(self, cx: float, cy: float) -> tuple[float, float]:
        return (cx - self._off[0]) / self._scale, (cy - self._off[1]) / self._scale

    def _redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        if self._photo is None:
            return
        ox, oy = self._off
        dw, dh = self._photo.width(), self._photo.height()
        c.create_image(ox, oy, image=self._photo, anchor="nw")
        if self.auto_sel is not None:
            x0, y0 = self._to_canvas(self.auto_sel.x, self.auto_sel.y)
            x1, y1 = self._to_canvas(self.auto_sel.x + self.auto_sel.side, self.auto_sel.y + self.auto_sel.side)
            c.create_rectangle(x0 - 3, y0 - 3, x1 + 3, y1 + 3, outline=TEAL, width=2, dash=(6, 4))
        if self.sel is None:
            return
        x0, y0 = self._to_canvas(self.sel.x, self.sel.y)
        x1, y1 = self._to_canvas(self.sel.x + self.sel.side, self.sel.y + self.sel.side)
        # dim everything outside the square
        for rx0, ry0, rx1, ry1 in ((ox, oy, ox + dw, y0), (ox, y1, ox + dw, oy + dh),
                                   (ox, y0, x0, y1), (x1, y0, ox + dw, y1)):
            if rx1 - rx0 >= 1 and ry1 - ry0 >= 1:
                c.create_rectangle(rx0, ry0, rx1, ry1, fill="#000000", outline="", stipple="gray50")
        c.create_rectangle(x0, y0, x1, y1, outline=GOLD, width=2)
        k = max(6.0, min(14.0, (x1 - x0) / 5))
        for cx, cy, sx, sy in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
            c.create_line(cx, cy + sy * k, cx, cy, cx + sx * k, cy, fill=GOLD_HOVER, width=4)
        self._draw_zoom(ox, oy, dw, dh)
        r = selection_to_rect(self.sel, self.origin)
        label = f"{r['w']} × {r['h']}"
        ty = y0 - 14 if y0 - 26 > oy else y1 + 14
        tid = c.create_text((x0 + x1) / 2, ty, text=label, fill=ON_GOLD, font=("TkDefaultFont", 10, "bold"))
        bb = c.bbox(tid)
        if bb:
            bg = c.create_rectangle(bb[0] - 6, bb[1] - 2, bb[2] + 6, bb[3] + 2, fill=GOLD, outline="")
            c.tag_lower(bg, tid)

    def _draw_zoom(self, ox: int, oy: int, dw: int, dh: int) -> None:
        """Magnified view of the selection (with a margin) in the top-left corner of the capture."""
        if self.sel is None or self.image is None or self.sel.side < 8:
            return
        import cv2  # noqa: PLC0415
        from PIL import Image, ImageTk  # noqa: PLC0415

        zs = int(min(ZOOM_PX, dh * 0.45, dw * 0.3))
        if zs < 80:
            return
        h, w = self.image.shape[:2]
        m = self.sel.side * 0.12
        x0, y0 = int(max(0, self.sel.x - m)), int(max(0, self.sel.y - m))
        x1, y1 = int(min(w, self.sel.x + self.sel.side + m)), int(min(h, self.sel.y + self.sel.side + m))
        if x1 - x0 < 4 or y1 - y0 < 4:
            return
        crop = self.image[y0:y1, x0:x1]
        k = zs / max(x1 - x0, y1 - y0)
        zw, zh = max(1, int((x1 - x0) * k)), max(1, int((y1 - y0) * k))
        big = cv2.resize(crop, (zw, zh), interpolation=cv2.INTER_LINEAR if k > 1 else cv2.INTER_AREA)
        self._zoom_photo = ImageTk.PhotoImage(Image.fromarray(np.ascontiguousarray(big[..., ::-1])),
                                              master=self.canvas)
        c = self.canvas
        px, py = ox + 12, oy + 12
        c.create_rectangle(px - 3, py - 3, px + zw + 3, py + zh + 25, fill=PANEL, outline=GOLD_DARK)
        c.create_image(px, py, image=self._zoom_photo, anchor="nw")
        sx0, sy0 = px + (self.sel.x - x0) * k, py + (self.sel.y - y0) * k
        c.create_rectangle(sx0, sy0, sx0 + self.sel.side * k, sy0 + self.sel.side * k, outline=GOLD, width=2)
        c.create_text(px + 8, py + zh + 12, text="Zoom sur la sélection", anchor="w", fill=MUTED,
                      font=("TkDefaultFont", 9))

    # ---------------------------------------------------------------- mouse / keys
    def _on_press(self, e: Any) -> None:
        if self.image is None:
            return
        ix, iy = self._to_image(e.x, e.y)
        h, w = self.image.shape[:2]
        if not (0 <= ix <= w and 0 <= iy <= h):
            self._drag = None
            return
        if self.sel is not None and self.sel.contains(ix, iy):
            self._drag = ("move", ix, iy, Selection(self.sel.x, self.sel.y, self.sel.side))
            self.canvas.configure(cursor="fleur")
        else:
            self._drag = ("new", ix, iy, None)

    def _on_motion(self, e: Any) -> None:
        if self._drag is None or self.image is None:
            return
        mode, ax, ay, start = self._drag
        h, w = self.image.shape[:2]
        ix, iy = self._to_image(e.x, e.y)
        if mode == "move" and start is not None:
            self.sel = clamp_selection(Selection(start.x + ix - ax, start.y + iy - ay, start.side), w, h)
        else:
            x, y, side = constrain_square(ax, ay, ix, iy, w, h)
            self.sel = Selection(x, y, side) if side >= 1 else None
        self._redraw()
        self._set_status(self._sel_text(), TEXT)
        self._update_ok()

    def _on_release(self, e: Any) -> None:
        self._on_motion(e)
        self._drag = None
        self.canvas.configure(cursor="crosshair")
        if self.sel is not None and self.sel.side < MIN_SIDE_PX:
            self._set_status("Carré trop petit : trace-le autour de toute la minimap.", DANGER)
        self._update_ok()

    def _nudge(self, dx: int, dy: int, e: Any = None) -> None:
        if self.sel is None or self.image is None:
            return
        step = 10 if e is not None and (int(getattr(e, "state", 0)) & 0x1) else 1
        h, w = self.image.shape[:2]
        self.sel = clamp_selection(Selection(self.sel.x + dx * step, self.sel.y + dy * step, self.sel.side), w, h)
        self._redraw()
        self._set_status(self._sel_text(), TEXT)

    def _grow(self, d: int) -> None:
        if self.sel is None or self.image is None:
            return
        h, w = self.image.shape[:2]
        s = self.sel
        self.sel = clamp_selection(Selection(s.x - d / 2, s.y - d / 2, max(MIN_SIDE_PX, s.side + d)), w, h)
        self._redraw()
        self._set_status(self._sel_text(), TEXT)
        self._update_ok()

    # ---------------------------------------------------------------- actions
    def auto_detect(self) -> None:
        """Run the automatic minimap localisation on the capture (worker thread)."""
        if self.image is None or self._detecting:
            return
        self._detecting = True
        self.btn_auto.configure(state="disabled", text="Recherche…")
        self._set_status("Recherche automatique de la minimap…", MUTED)
        img, origin = self.image, self.origin
        side = str(getattr(self.cfg, "minimap_side", "auto") or "auto")

        def job() -> None:
            try:
                from treeaicoach.minimap_locator import MinimapLocator  # noqa: PLC0415

                loc = MinimapLocator().locate(img, origin, side)
                self._q.put(("auto", loc))
            except Exception as exc:
                log.exception("Automatic minimap detection failed")
                self._q.put(("auto_error", exc))

        threading.Thread(target=job, name="TreeAI-calib-locate", daemon=True).start()

    def _poll(self) -> None:
        if self._closed:
            return
        try:
            while True:
                kind, payload = self._q.get_nowait()
                self._detecting = False
                self.btn_auto.configure(state="normal", text="Détection auto")
                if kind == "auto" and payload is not None:
                    sel = rect_to_selection(payload.rect, self.origin)
                    if sel is None:
                        self._set_status("Minimap trouvée hors de la capture : trace le carré à la main.", DANGER)
                        continue
                    self.auto_sel = sel
                    self.sel = Selection(sel.x, sel.y, sel.side)
                    self._redraw()
                    self._update_ok()
                    self._set_status(f"Minimap trouvée (confiance {payload.score:.0%}). "
                                     f"{self._sel_text()} — valide ou ajuste.".replace("%", " %"), TEAL)
                elif kind == "auto":
                    self._set_status("Minimap introuvable automatiquement : trace le carré à la main.", DANGER)
                else:
                    self._set_status(f"Détection automatique impossible : {payload}", DANGER)
        except queue.Empty:
            pass
        except Exception:
            log.exception("Calibration poll failed")
        try:
            self.top.after(100, self._poll)
        except Exception:
            pass

    def validate(self) -> None:
        if self.sel is None or self.sel.side < MIN_SIDE_PX:
            self._set_status("Trace d'abord un carré autour de la minimap.", DANGER)
            return
        self.result = selection_to_rect(self.sel, self.origin)
        log.info("Manual minimap calibration: %s", self.result)
        self._close()

    def cancel(self) -> None:
        self.result = None
        self._close()

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.top.grab_release()
        except Exception:
            pass
        try:
            self.top.destroy()
        except Exception:
            pass

    def wait(self) -> dict[str, int] | None:
        """Block (nested event loop) until the dialog is closed; returns :attr:`result`."""
        try:
            self.parent.wait_window(self.top)
        except Exception:
            log.debug("wait_window failed", exc_info=True)
        return self.result


def run_calibration(parent: Any, cfg: Any, *, screenshot: tuple[np.ndarray, Any] | None = None) -> dict | None:
    """Modal calibration; returns the ``manual_minimap_rect`` dict, or None if cancelled.

    ``screenshot`` = ``(bgr image, origin Rect)`` skips the screen capture (tests). Never raises.
    """
    try:
        if screenshot is not None:
            img, origin = screenshot
        else:
            img, origin = grab_screen(parent)
        dlg = CalibrationDialog(parent, cfg, img, origin)
        return dlg.wait()
    except Exception:
        log.exception("Minimap calibration failed")
        return None


__all__ = ["run_calibration", "CalibrationDialog", "Selection", "constrain_square", "fit_scale",
           "clamp_selection", "selection_to_rect", "rect_to_selection", "grab_screen"]
