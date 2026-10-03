"""Qt building blocks of the launcher (docs/LAUNCHER.md, docs/DESIGN.md "Lanceur").

Apple System Settings feel: a quiet sidebar, large legible type, grouped inset rounded lists with
hairline separators, ONE accent (TreeAI sap green), light and dark palettes, tiny transitions
(switch knob, hover). Everything is a plain ``QWidget`` styled by one application style sheet
(:func:`style_sheet`) plus a few custom-painted controls (:class:`Switch`, :class:`Dot`, the sidebar
glyphs). Nothing here touches the engine; every class is safe to build before / without it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from PySide6 import QtCore, QtGui, QtWidgets

Qt = QtCore.Qt

# ======================================================================================
# Theme
# ======================================================================================
FONT_FAMILIES = ("Segoe UI Variable Text", "Segoe UI", "Inter", "Noto Sans", "DejaVu Sans", "Liberation Sans")
DISPLAY_FAMILIES = ("Segoe UI Variable Display", "Segoe UI Semibold", "Segoe UI", "Inter", "Noto Sans",
                    "DejaVu Sans")


@dataclass(frozen=True)
class Theme:
    name: str
    window: str          # page background (grouped background)
    sidebar: str
    group: str           # inset grouped list cell
    group_hover: str
    separator: str
    text: str
    secondary: str
    tertiary: str
    accent: str
    accent_hover: str
    on_accent: str
    accent_soft: str     # selected sidebar row / soft accent fill
    control: str         # secondary button / segmented track
    control_hover: str
    control_border: str
    track_off: str
    knob: str
    danger: str
    warning: str
    ok: str


LIGHT = Theme(
    name="light", window="#F2F2F4", sidebar="#E8E8EA", group="#FFFFFF", group_hover="#F7F7F8",
    separator="#E3E3E6", text="#1D1D1F", secondary="#5E5E63", tertiary="#6B6B70",
    accent="#2A721B", accent_hover="#236316", on_accent="#FFFFFF", accent_soft="#DCEBD3",
    control="#FFFFFF", control_hover="#F1F1F3", control_border="#D2D2D7", track_off="#E1E1E5", knob="#FFFFFF",
    danger="#C8001A", warning="#A34A00", ok="#2A721B")
DARK = Theme(
    name="dark", window="#1C1D1C", sidebar="#242524", group="#2B2C2B", group_hover="#323332",
    separator="#3A3B3A", text="#F2F3F2", secondary="#B4B7B4", tertiary="#9A9E9A",
    accent="#9BD84A", accent_hover="#AEE36A", on_accent="#0C0E0D", accent_soft="#34452A",
    control="#3A3B3A", control_hover="#444544", control_border="#4A4B4A", track_off="#48494A", knob="#FFFFFF",
    danger="#FF6961", warning="#FFB340", ok="#9BD84A")

# sizes (px at 100 %, Qt applies the monitor's DPI factor on top)
SIDEBAR_W = 248
CONTENT_MAX = 760        # centred column of a page (System Settings uses ~680-760)
PAGE_PAD_X = 40
PAGE_PAD_TOP = 28
GROUP_RADIUS = 10
ROW_MIN_H = 48
SECTION_GAP = 28
TITLE_PX = 26
BODY_PX = 14
SMALL_PX = 13
CAPTION_PX = 12
HEADLINE_PX = 15

_theme: list[Theme] = [DARK]


def theme() -> Theme:
    return _theme[0]


def system_is_dark(app: QtWidgets.QApplication | None = None) -> bool:
    """Light / dark from the OS (Qt >= 6.5 colour scheme; Windows registry fallback)."""
    try:
        hints = (app or QtWidgets.QApplication.instance()).styleHints()
        scheme = hints.colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return True
        if scheme == Qt.ColorScheme.Light:
            return False
    except Exception:
        pass
    try:
        import sys

        if sys.platform == "win32":
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
                return int(winreg.QueryValueEx(k, "AppsUseLightTheme")[0]) == 0
    except Exception:
        pass
    return True


def pick_family(candidates: Sequence[str] = FONT_FAMILIES) -> str:
    try:
        have = set(QtGui.QFontDatabase.families())
    except Exception:
        have = set()
    for f in candidates:
        if f in have:
            return f
    return QtWidgets.QApplication.font().family()


def style_sheet(t: Theme, body: str, display: str) -> str:
    """The ONE application style sheet (selectors by object name / dynamic property only)."""
    return f"""
* {{ font-family: "{body}"; font-size: {BODY_PX}px; color: {t.text}; }}
QMainWindow, QDialog, #Page, #PageBody, QStackedWidget {{ background: {t.window}; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
#Sidebar {{ background: {t.sidebar}; border: none; border-right: 1px solid {t.separator}; }}
QLabel {{ background: transparent; }}
QLabel[role="title"] {{ font-family: "{display}"; font-size: {TITLE_PX}px; font-weight: 600; }}
QLabel[role="headline"] {{ font-family: "{display}"; font-size: 20px; font-weight: 600; }}
QLabel[role="section"] {{ font-size: {SMALL_PX}px; font-weight: 600; color: {t.secondary}; }}
QLabel[role="secondary"] {{ font-size: {SMALL_PX}px; color: {t.secondary}; }}
QLabel[role="footnote"] {{ font-size: {CAPTION_PX}px; color: {t.tertiary}; }}
QLabel[role="value"] {{ color: {t.secondary}; }}
QLabel[role="big"] {{ font-family: "{display}"; font-size: 28px; font-weight: 600; }}
QLabel[tone="danger"] {{ color: {t.danger}; }}
QLabel[tone="warning"] {{ color: {t.warning}; }}
QLabel[tone="ok"] {{ color: {t.ok}; }}
QLabel[tone="accent"] {{ color: {t.accent}; }}
QLabel a {{ color: {t.accent}; }}
#Group {{ background: {t.group}; border-radius: {GROUP_RADIUS}px; }}
#Separator {{ background: {t.separator}; border: none; }}
QPushButton {{ background: {t.control}; border: 1px solid {t.control_border}; border-radius: 7px;
  padding: 6px 14px; min-height: 20px; font-weight: 500; }}
QPushButton:hover {{ background: {t.control_hover}; }}
QPushButton:pressed {{ background: {t.separator}; }}
QPushButton:disabled {{ color: {t.tertiary}; }}
QPushButton[kind="primary"] {{ background: {t.accent}; color: {t.on_accent}; border: 1px solid {t.accent};
  font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: {t.accent_hover}; }}
QPushButton[kind="primary"]:disabled {{ background: {t.track_off}; border-color: {t.track_off};
  color: {t.tertiary}; }}
QPushButton[kind="danger"] {{ background: {t.control}; color: {t.danger}; }}
QPushButton[kind="link"] {{ background: transparent; border: none; color: {t.accent}; padding: 2px 0;
  font-weight: 500; }}
QPushButton[kind="link"]:hover {{ text-decoration: underline; }}
QPushButton[kind="large"] {{ padding: 9px 22px; font-size: 15px; }}
QPushButton#NavItem {{ background: transparent; border: none; border-radius: 7px; text-align: left;
  padding: 7px 10px; font-size: {BODY_PX}px; font-weight: 500; }}
QPushButton#NavItem:hover {{ background: {t.control_hover if t.name == "dark" else "#DCDCDF"}; }}
QPushButton#NavItem:checked {{ background: {t.accent}; color: {t.on_accent}; }}
QComboBox {{ background: {t.control}; border: 1px solid {t.control_border}; border-radius: 7px;
  padding: 5px 30px 5px 10px; min-height: 20px; min-width: 140px; }}
QComboBox:hover {{ background: {t.control_hover}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
QComboBox QAbstractItemView {{ background: {t.group}; border: 1px solid {t.control_border};
  selection-background-color: {t.accent}; selection-color: {t.on_accent}; padding: 4px; outline: none; }}
QLineEdit {{ background: {t.control}; border: 1px solid {t.control_border}; border-radius: 7px;
  padding: 6px 10px; selection-background-color: {t.accent}; selection-color: {t.on_accent}; }}
QLineEdit:focus {{ border: 2px solid {t.accent}; padding: 5px 9px; }}
QSlider::groove:horizontal {{ height: 4px; background: {t.track_off}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ height: 4px; background: {t.accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {t.knob}; border: 1px solid {t.control_border}; width: 20px;
  height: 20px; margin: -9px 0; border-radius: 10px; }}
QProgressBar {{ background: {t.track_off}; border: none; border-radius: 3px; max-height: 6px; }}
QProgressBar::chunk {{ background: {t.accent}; border-radius: 3px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t.control_border}; border-radius: 3px; min-height: 40px; }}
QScrollBar::handle:vertical:hover {{ background: {t.tertiary}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
  background: none; height: 0; }}
QToolTip {{ background: {t.group}; color: {t.text}; border: 1px solid {t.control_border}; padding: 4px 8px; }}
#Segmented {{ background: {t.track_off}; border-radius: 8px; }}
QPushButton#Segment {{ background: transparent; border: none; border-radius: 6px; padding: 4px 12px;
  font-size: {SMALL_PX}px; font-weight: 500; min-height: 18px; }}
QPushButton#Segment:checked {{ background: {t.group if t.name == "light" else "#5A5B5A"}; }}
#Toast {{ background: {"#2C2C2E" if t.name == "light" else "#F2F3F2"}; border-radius: 10px; }}
#Toast QLabel {{ color: {"#FFFFFF" if t.name == "light" else "#1D1D1F"}; font-weight: 500; }}
#Toast[level="error"] QLabel, #Toast[level="warning"] QLabel {{ font-weight: 600; }}
"""


def apply_theme(app: QtWidgets.QApplication, dark: bool) -> Theme:
    t = DARK if dark else LIGHT
    _theme[0] = t
    body, display = pick_family(FONT_FAMILIES), pick_family(DISPLAY_FAMILIES)
    f = QtGui.QFont(body)
    f.setPixelSize(BODY_PX)
    f.setHintingPreference(QtGui.QFont.PreferNoHinting)
    app.setFont(f)
    pal = app.palette()
    for role, col in ((QtGui.QPalette.Window, t.window), (QtGui.QPalette.Base, t.group),
                      (QtGui.QPalette.Text, t.text), (QtGui.QPalette.WindowText, t.text),
                      (QtGui.QPalette.Button, t.control), (QtGui.QPalette.ButtonText, t.text),
                      (QtGui.QPalette.Highlight, t.accent), (QtGui.QPalette.HighlightedText, t.on_accent),
                      (QtGui.QPalette.Link, t.accent), (QtGui.QPalette.ToolTipBase, t.group),
                      (QtGui.QPalette.ToolTipText, t.text), (QtGui.QPalette.PlaceholderText, t.tertiary)):
        pal.setColor(role, QtGui.QColor(col))
    app.setPalette(pal)
    app.setStyleSheet(style_sheet(t, body, display))
    return t


def repolish(w: QtWidgets.QWidget) -> None:
    """Re-apply the style sheet after a dynamic property change."""
    st = w.style()
    st.unpolish(w)
    st.polish(w)
    w.update()


def set_prop(w: QtWidgets.QWidget, name: str, value: Any) -> None:
    if w.property(name) != value:
        w.setProperty(name, value)
        repolish(w)


# ======================================================================================
# Glyphs (SF-like line icons, painted: crisp at any DPI, follow the text colour)
# ======================================================================================
def _glyph_path(kind: str, s: float) -> QtGui.QPainterPath:
    p = QtGui.QPainterPath()
    u = s / 20.0

    def rr(x: float, y: float, w: float, h: float, r: float) -> None:
        p.addRoundedRect(QtCore.QRectF(x * u, y * u, w * u, h * u), r * u, r * u)

    if kind == "home":
        p.moveTo(3 * u, 9.5 * u); p.lineTo(10 * u, 3.5 * u); p.lineTo(17 * u, 9.5 * u)
        p.moveTo(5 * u, 8 * u); p.lineTo(5 * u, 16.5 * u); p.lineTo(15 * u, 16.5 * u); p.lineTo(15 * u, 8 * u)
        p.moveTo(8.5 * u, 16.5 * u); p.lineTo(8.5 * u, 12 * u); p.lineTo(11.5 * u, 12 * u); p.lineTo(11.5 * u, 16.5 * u)
    elif kind == "overlay":
        rr(2.5, 4, 15, 12, 2.5)
        rr(10, 9, 5.5, 5, 1.2)
    elif kind == "alerts":
        p.moveTo(4 * u, 8 * u); p.lineTo(7 * u, 8 * u); p.lineTo(11 * u, 4.5 * u); p.lineTo(11 * u, 15.5 * u)
        p.lineTo(7 * u, 12 * u); p.lineTo(4 * u, 12 * u); p.closeSubpath()
        p.moveTo(13.5 * u, 7.5 * u); p.quadTo(15.5 * u, 10 * u, 13.5 * u, 12.5 * u)
        p.moveTo(15.5 * u, 5.5 * u); p.quadTo(18.8 * u, 10 * u, 15.5 * u, 14.5 * u)
    elif kind == "analysis":
        rr(3, 10, 3, 6.5, 1)
        rr(8.5, 6, 3, 10.5, 1)
        rr(14, 3, 3, 13.5, 1)
    elif kind == "settings":
        c = QtCore.QPointF(10 * u, 10 * u)
        for i in range(8):
            a = i * math.pi / 4
            p.moveTo(c + QtCore.QPointF(math.cos(a) * 5.6 * u, math.sin(a) * 5.6 * u))
            p.lineTo(c + QtCore.QPointF(math.cos(a) * 7.6 * u, math.sin(a) * 7.6 * u))
        p.addEllipse(c, 5.3 * u, 5.3 * u)
        p.addEllipse(c, 2.2 * u, 2.2 * u)
    elif kind == "about":
        p.addEllipse(QtCore.QPointF(10 * u, 10 * u), 7.5 * u, 7.5 * u)
        p.moveTo(10 * u, 9 * u); p.lineTo(10 * u, 14 * u)
        p.addEllipse(QtCore.QPointF(10 * u, 6.4 * u), 0.5 * u, 0.5 * u)
    elif kind == "update":
        p.moveTo(10 * u, 3.5 * u); p.lineTo(10 * u, 12.5 * u)
        p.moveTo(6.5 * u, 9 * u); p.lineTo(10 * u, 12.5 * u); p.lineTo(13.5 * u, 9 * u)
        p.moveTo(4 * u, 14 * u); p.lineTo(4 * u, 16.5 * u); p.lineTo(16 * u, 16.5 * u); p.lineTo(16 * u, 14 * u)
    else:
        p.addEllipse(QtCore.QPointF(10 * u, 10 * u), 6 * u, 6 * u)
    return p


def glyph_icon(kind: str, color: str, size: int = 20, checked_color: str | None = None) -> QtGui.QIcon:
    icon = QtGui.QIcon()
    for col, state in ((color, QtGui.QIcon.Off), (checked_color or color, QtGui.QIcon.On)):
        dpr = 2.0
        pm = QtGui.QPixmap(int(size * dpr), int(size * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        qp = QtGui.QPainter(pm)
        qp.setRenderHint(QtGui.QPainter.Antialiasing)
        pen = QtGui.QPen(QtGui.QColor(col), 1.6 * size / 20.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        qp.setPen(pen)
        qp.drawPath(_glyph_path(kind, size))
        qp.end()
        icon.addPixmap(pm, QtGui.QIcon.Normal, state)
    return icon


# ======================================================================================
# Controls
# ======================================================================================
class Switch(QtWidgets.QAbstractButton):
    """Apple-style switch (pill 42 x 24, knob slides in 120 ms)."""

    def __init__(self, checked: bool = False, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(bool(checked))
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(42, 24)
        self.setFocusPolicy(Qt.TabFocus)
        self._pos = 1.0 if checked else 0.0
        self._anim = QtCore.QVariantAnimation(self, duration=120, easingCurve=QtCore.QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self.toggled.connect(self._animate)

    def set_quiet(self, on: bool) -> None:
        """Set without emitting (sync from the configuration)."""
        self.blockSignals(True)
        self.setChecked(bool(on))
        self.blockSignals(False)
        self._anim.stop()
        self._pos = 1.0 if on else 0.0
        self.update()

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _on_anim(self, v: Any) -> None:
        self._pos = float(v)
        self.update()

    def sizeHint(self) -> QtCore.QSize:  # noqa: N802
        return QtCore.QSize(42, 24)

    def paintEvent(self, _e: Any) -> None:  # noqa: N802
        t = theme()
        qp = QtGui.QPainter(self)
        qp.setRenderHint(QtGui.QPainter.Antialiasing)
        r = QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        off, on = QtGui.QColor(t.track_off), QtGui.QColor(t.accent)
        k = self._pos
        col = QtGui.QColor(int(off.red() + (on.red() - off.red()) * k), int(off.green() + (on.green() - off.green()) * k),
                           int(off.blue() + (on.blue() - off.blue()) * k))
        if not self.isEnabled():
            col.setAlpha(110)
        qp.setPen(Qt.NoPen)
        qp.setBrush(col)
        qp.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 4
        x = r.left() + 2 + (r.width() - d - 4) * k
        qp.setBrush(QtGui.QColor(0, 0, 0, 40))
        qp.drawEllipse(QtCore.QRectF(x, r.top() + 2.6, d, d))
        qp.setBrush(QtGui.QColor(t.knob))
        qp.drawEllipse(QtCore.QRectF(x, r.top() + 2, d, d))
        if self.hasFocus():
            qp.setPen(QtGui.QPen(QtGui.QColor(t.accent), 2))
            qp.setBrush(Qt.NoBrush)
            qp.drawRoundedRect(r.adjusted(-1, -1, 1, 1), r.height() / 2, r.height() / 2)
        qp.end()


class Dot(QtWidgets.QWidget):
    """Small status dot (ok / warning / danger / off)."""

    def __init__(self, level: int = -1, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(10, 10)
        self._level = level

    def set_level(self, level: int) -> None:
        if level != self._level:
            self._level = level
            self.update()

    def paintEvent(self, _e: Any) -> None:  # noqa: N802
        t = theme()
        col = {0: t.ok, 1: t.warning, 2: t.danger}.get(self._level, t.tertiary)
        qp = QtGui.QPainter(self)
        qp.setRenderHint(QtGui.QPainter.Antialiasing)
        qp.setPen(Qt.NoPen)
        qp.setBrush(QtGui.QColor(col))
        qp.drawEllipse(QtCore.QRectF(1, 1, 8, 8))
        qp.end()


class Segmented(QtWidgets.QFrame):
    """Segmented control (macOS style): one checked segment; ``changed(value)``."""

    changed = QtCore.Signal(str)

    def __init__(self, choices: Sequence[tuple[str, str]], value: str = "",
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Segmented")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QtWidgets.QPushButton] = {}
        for v, label in choices:
            b = QtWidgets.QPushButton(label)
            b.setObjectName("Segment")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            self._group.addButton(b)
            self._buttons[v] = b
            lay.addWidget(b)
            b.clicked.connect(lambda _c=False, val=v: self.changed.emit(val))
        self.set_value(value)

    def set_value(self, value: str) -> None:
        b = self._buttons.get(str(value))
        if b is not None and not b.isChecked():
            b.setChecked(True)

    def value(self) -> str:
        for v, b in self._buttons.items():
            if b.isChecked():
                return v
        return ""


class Combo(QtWidgets.QComboBox):
    """QComboBox with (value, label) items, a painted chevron and no wheel hijack while scrolling."""

    def __init__(self, choices: Sequence[tuple[str, str]], value: Any = None,
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.set_choices(choices, value)

    def set_choices(self, choices: Sequence[tuple[Any, str]], value: Any = None) -> None:
        self.blockSignals(True)
        self.clear()
        for v, label in choices:
            self.addItem(str(label), v)
        self.blockSignals(False)
        self.set_value(value)

    def set_value(self, value: Any) -> None:
        for i in range(self.count()):
            if self.itemData(i) == value or str(self.itemData(i)) == str(value):
                if self.currentIndex() != i:
                    self.blockSignals(True)
                    self.setCurrentIndex(i)
                    self.blockSignals(False)
                return

    def value(self) -> Any:
        return self.currentData()

    def wheelEvent(self, e: QtGui.QWheelEvent) -> None:  # noqa: N802
        if self.hasFocus():
            super().wheelEvent(e)
        else:
            e.ignore()

    def paintEvent(self, e: Any) -> None:  # noqa: N802
        super().paintEvent(e)
        qp = QtGui.QPainter(self)
        qp.setRenderHint(QtGui.QPainter.Antialiasing)
        qp.setPen(QtGui.QPen(QtGui.QColor(theme().secondary), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        x, y = self.width() - 18, self.height() / 2
        qp.drawPolyline([QtCore.QPointF(x - 4, y - 4), QtCore.QPointF(x, y - 7.5), QtCore.QPointF(x + 4, y - 4)])
        qp.drawPolyline([QtCore.QPointF(x - 4, y + 4), QtCore.QPointF(x, y + 7.5), QtCore.QPointF(x + 4, y + 4)])
        qp.end()


class Slider(QtWidgets.QWidget):
    """Horizontal slider with its value written on the right; ``changed(float)`` on release / key."""

    changed = QtCore.Signal(float)

    def __init__(self, lo: float, hi: float, step: float, value: float, fmt: Callable[[float], str],
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._lo, self._step, self._fmt = float(lo), float(step), fmt
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        self.slider = QtWidgets.QSlider(Qt.Horizontal)
        self.slider.setRange(0, max(1, int(round((hi - lo) / step))))
        self.slider.setFixedWidth(200)
        self.slider.setFocusPolicy(Qt.StrongFocus)
        self.label = QtWidgets.QLabel()
        self.label.setProperty("role", "value")
        self.label.setMinimumWidth(64)
        self.label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.slider)
        lay.addWidget(self.label)
        self.set_value(value)
        self.slider.valueChanged.connect(lambda _v: self.label.setText(self._fmt(self.value())))
        self.slider.sliderReleased.connect(lambda: self.changed.emit(self.value()))
        self.slider.actionTriggered.connect(self._on_action)

    def _on_action(self, action: int) -> None:
        if not self.slider.isSliderDown():
            QtCore.QTimer.singleShot(0, lambda: self.changed.emit(self.value()))

    def value(self) -> float:
        return round(self._lo + self.slider.value() * self._step, 6)

    def set_value(self, v: Any) -> None:
        try:
            pos = int(round((float(v) - self._lo) / self._step))
        except (TypeError, ValueError):
            pos = 0
        self.slider.blockSignals(True)
        self.slider.setValue(pos)
        self.slider.blockSignals(False)
        self.label.setText(self._fmt(self.value()))


def label(text: str = "", role: str | None = None, wrap: bool = False, tone: str | None = None,
          selectable: bool = False) -> QtWidgets.QLabel:
    lbl = QtWidgets.QLabel(text)
    if role:
        lbl.setProperty("role", role)
    if tone:
        lbl.setProperty("tone", tone)
    if wrap:
        lbl.setWordWrap(True)
    if selectable:
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lbl


def button(text: str, on_click: Callable[[], Any] | None = None, kind: str | None = None,
           tip: str = "") -> QtWidgets.QPushButton:
    b = QtWidgets.QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    if kind:
        b.setProperty("kind", kind)
    if tip:
        b.setToolTip(tip)
    if on_click is not None:
        b.clicked.connect(lambda _c=False: on_click())
    return b


# ======================================================================================
# Grouped inset list
# ======================================================================================
class Row(QtWidgets.QWidget):
    """One row of a group: title (+ description below) on the left, a control on the right.
    Narrow content: the control drops below the text (``set_narrow``)."""

    def __init__(self, title: str, desc: str | None = None, control: QtWidgets.QWidget | None = None,
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(ROW_MIN_H)
        self._grid = QtWidgets.QGridLayout(self)
        self._grid.setContentsMargins(16, 10, 16, 10)
        self._grid.setHorizontalSpacing(24)
        self._grid.setVerticalSpacing(2)
        self.title = label(title)
        self._grid.addWidget(self.title, 0, 0)
        self.desc = None
        if desc:
            self.desc = label(desc, "secondary", wrap=True)
            self._grid.addWidget(self.desc, 1, 0)
        self._grid.setColumnStretch(0, 1)
        self.control = control
        if control is not None:
            self._grid.addWidget(control, 0, 1, 2 if desc else 1, 1, Qt.AlignRight | Qt.AlignVCenter)

    def set_desc(self, text: str) -> None:
        if self.desc is None:
            self.desc = label("", "secondary", wrap=True)
            self._grid.addWidget(self.desc, 1, 0)
        self.desc.setText(text)
        self.desc.setVisible(bool(text))


def card() -> QtWidgets.QFrame:
    """A rounded group cell without rows (free layout inside)."""
    f = QtWidgets.QFrame()
    f.setObjectName("Group")
    return f


class Group(QtWidgets.QFrame):
    """Inset grouped list: rounded cell, rows separated by hairlines inset 16 px."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Group")
        self._lay = QtWidgets.QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        self._rows: list[QtWidgets.QWidget] = []
        self._seps: list[QtWidgets.QWidget] = []

    def add(self, row: QtWidgets.QWidget) -> QtWidgets.QWidget:
        if self._rows:
            holder = QtWidgets.QWidget()
            hl = QtWidgets.QHBoxLayout(holder)
            hl.setContentsMargins(16, 0, 0, 0)
            sep = QtWidgets.QFrame()
            sep.setObjectName("Separator")
            sep.setFixedHeight(1)
            hl.addWidget(sep)
            self._lay.addWidget(holder)
            self._seps.append(holder)
        self._lay.addWidget(row)
        self._rows.append(row)
        return row

    def sync_separators(self) -> None:
        """Hide the separator above a hidden row (and above the first visible row)."""
        seen = False
        for i, row in enumerate(self._rows):
            vis = not row.isHidden()
            if i > 0:
                self._seps[i - 1].setVisible(vis and seen)
            seen = seen or vis


class Section(QtWidgets.QWidget):
    """Header (small title, optional action on the right) + one group + optional footnote."""

    def __init__(self, title: str = "", footnote: str = "", action: QtWidgets.QWidget | None = None,
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        if title or action is not None:
            head = QtWidgets.QHBoxLayout()
            head.setContentsMargins(16, 0, 4, 0)
            self.header = label(title, "section")
            head.addWidget(self.header, 1)
            if action is not None:
                head.addWidget(action, 0, Qt.AlignRight)
            lay.addLayout(head)
        self.group = Group()
        lay.addWidget(self.group)
        self.footnote = label(footnote, "footnote", wrap=True)
        self.footnote.setContentsMargins(16, 0, 16, 0)
        self.footnote.setVisible(bool(footnote))
        lay.addWidget(self.footnote)

    def add(self, row: QtWidgets.QWidget) -> QtWidgets.QWidget:
        return self.group.add(row)


class Page(QtWidgets.QScrollArea):
    """Scrolling page: large title, subtitle, then sections in a centred column."""

    def __init__(self, title: str, subtitle: str = "", max_width: int = CONTENT_MAX,
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Page")
        self.setWidgetResizable(True)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QtWidgets.QWidget()
        body.setObjectName("PageBody")
        outer = QtWidgets.QHBoxLayout(body)
        outer.setContentsMargins(PAGE_PAD_X, PAGE_PAD_TOP, PAGE_PAD_X, 40)
        self.column = QtWidgets.QWidget()
        self.column.setMaximumWidth(max_width)
        outer.addStretch(1)
        outer.addWidget(self.column, 100)
        outer.addStretch(1)
        self.lay = QtWidgets.QVBoxLayout(self.column)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(SECTION_GAP)
        head = QtWidgets.QVBoxLayout()
        head.setSpacing(4)
        self.title = label(title, "title")
        head.addWidget(self.title)
        self.subtitle = label(subtitle, "secondary", wrap=True)
        self.subtitle.setVisible(bool(subtitle))
        head.addWidget(self.subtitle)
        self.lay.addLayout(head)
        self.lay.addStretch(1)
        self.setWidget(body)

    def add(self, w: QtWidgets.QWidget) -> QtWidgets.QWidget:
        self.lay.insertWidget(self.lay.count() - 1, w)
        return w

    def section(self, title: str = "", footnote: str = "", action: QtWidgets.QWidget | None = None) -> Section:
        s = Section(title, footnote, action)
        self.add(s)
        return s


class Sidebar(QtWidgets.QFrame):
    """Navigation (icon + label rows, accent fill on the current page) + a status line at the bottom."""

    selected = QtCore.Signal(str)

    def __init__(self, pages: Sequence[tuple[str, str, str]], parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(SIDEBAR_W)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(12, 18, 12, 14)
        lay.setSpacing(2)
        self.brand = QtWidgets.QLabel()
        self.brand.setProperty("role", "headline")
        self.brand.setContentsMargins(10, 0, 0, 12)
        lay.addWidget(self.brand)
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: dict[str, QtWidgets.QPushButton] = {}
        self._kinds: dict[str, str] = {}
        for key, text, kind in pages:
            b = QtWidgets.QPushButton(text)
            b.setObjectName("NavItem")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setIconSize(QtCore.QSize(20, 20))
            b.setMinimumHeight(36)
            self._group.addButton(b)
            self.buttons[key] = b
            self._kinds[key] = kind
            b.clicked.connect(lambda _c=False, k=key: self.selected.emit(k))
            lay.addWidget(b)
        lay.addStretch(1)
        self.update_btn = button("", None, "primary")
        self.update_btn.hide()
        lay.addWidget(self.update_btn)
        foot = QtWidgets.QHBoxLayout()
        foot.setContentsMargins(10, 10, 4, 0)
        foot.setSpacing(8)
        self.dot = Dot()
        foot.addWidget(self.dot, 0, Qt.AlignVCenter)
        self.status = label("", "secondary")
        foot.addWidget(self.status, 1)
        lay.addLayout(foot)
        self.recolor()

    def recolor(self) -> None:
        t = theme()
        for key, b in self.buttons.items():
            b.setIcon(glyph_icon(self._kinds[key], t.accent, 20, t.on_accent))

    def set_current(self, key: str) -> None:
        b = self.buttons.get(key)
        if b is not None and not b.isChecked():
            b.setChecked(True)


class Toast(QtWidgets.QFrame):
    """Transient message at the bottom of the window (fades out)."""

    def __init__(self, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(18, 11, 18, 11)
        self.text = QtWidgets.QLabel()
        self.text.setWordWrap(True)
        lay.addWidget(self.text)
        self._timer = QtCore.QTimer(self, singleShot=True)
        self._timer.timeout.connect(self.hide)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()

    def show_text(self, text: str, level: str = "info", ms: int = 4500) -> None:
        set_prop(self, "level", level)
        t = theme()
        dot = {"error": t.danger, "warning": t.warning}.get(level)
        self.text.setText(text if dot is None else text)
        self._place()
        self.show()
        self.raise_()
        self._timer.start(ms)

    def _place(self) -> None:
        par = self.parentWidget()
        if par is None:
            return
        w = min(560, par.width() - 80)
        self.text.setMaximumWidth(w - 36)
        self.setFixedWidth(w)
        self.adjustSize()
        self.move((par.width() - w) // 2 + SIDEBAR_W // 2, par.height() - self.height() - 24)


__all__ = ["Theme", "LIGHT", "DARK", "theme", "apply_theme", "system_is_dark", "style_sheet", "Switch", "Dot",
           "Segmented", "Combo", "Slider", "Row", "Group", "Section", "Page", "Sidebar", "Toast", "label",
           "button", "glyph_icon", "set_prop", "repolish"]
