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
#: Inter (OFL, bundled in assets/fonts, Latin subset ~60 KB per weight) is the identity; Segoe UI
#: Variable is the fallback if the files cannot be loaded.
FONT_FILES = ("Inter-Regular.ttf", "Inter-Medium.ttf", "Inter-SemiBold.ttf", "InterDisplay-SemiBold.ttf")
FONT_FAMILIES = ("Inter", "Segoe UI Variable Text", "Segoe UI", "Noto Sans", "DejaVu Sans", "Liberation Sans")
DISPLAY_FAMILIES = ("Inter Display", "Inter", "Segoe UI Variable Display", "Segoe UI Semibold", "Segoe UI", "Noto Sans",
                    "DejaVu Sans")


@dataclass(frozen=True)
class Theme:
    name: str
    window: str          # page background (grouped background)
    sidebar: str
    group: str           # card / inset grouped list cell
    group_hover: str
    separator: str
    text: str
    secondary: str
    tertiary: str
    accent: str
    accent_hover: str
    on_accent: str
    accent_soft: str     # selected sidebar row / segmented thumb (neutral)
    control: str         # secondary button / segmented track
    control_hover: str
    control_border: str
    track_off: str
    knob: str
    danger: str
    warning: str
    ok: str
    # v4 design system: the card hairline and the soft tints behind badges (text stays >= 4.5:1 on them)
    edge: str = "rgba(255, 255, 255, 0.06)"
    accent_tint: str = "#1E2A44"
    danger_soft: str = "#3A1E1F"
    warning_soft: str = "#352C17"
    ok_soft: str = "#183326"
    hover: str = "rgba(255, 255, 255, 0.05)"
    seg_track: str = "#232529"   # segmented control: track and selected thumb
    thumb: str = "#3A3D45"


#: « graphite » (docs/DESIGN.md, v4): a flat neutral graphite window, opaque cards with a faint hairline,
#: ONE blue accent (switches, primary button, links, current page icon). No image, no glass, no glow.
GRAPHITE = Theme(
    name="dark", window="#0F1012", sidebar="#141518", group="#1A1B1F", group_hover="#222429",
    separator="#2A2C31", text="#ECEDEF", secondary="#A3A7AE", tertiary="#8A8E97",
    accent="#7AA7FF", accent_hover="#93B8FF", on_accent="#0A0F1C", accent_soft="#262930",
    control="#232529", control_hover="#2A2D32", control_border="#34373D", track_off="#3A3D44", knob="#FFFFFF",
    danger="#FF6B63", warning="#EBBE4D", ok="#5FD08F")
#: « porcelaine »: the same system on light grey (Windows light mode, ``ui_theme``)
PORCELAIN = Theme(
    name="light", window="#F4F5F7", sidebar="#EBEDF0", group="#FFFFFF", group_hover="#F3F4F6",
    separator="#E4E6EA", text="#15171B", secondary="#4F555E", tertiary="#5F6570",
    accent="#2459D6", accent_hover="#3368E3", on_accent="#FFFFFF", accent_soft="#DDE1E7",
    control="#FFFFFF", control_hover="#F0F2F5", control_border="#CDD1D8", track_off="#C9CDD4", knob="#FFFFFF",
    danger="#C22F2A", warning="#8A5800", ok="#18794A", edge="rgba(16, 24, 40, 0.08)", accent_tint="#E3EBFC",
    danger_soft="#FBE7E6", warning_soft="#F8EED9", ok_soft="#E2F3EA", hover="rgba(16, 24, 40, 0.05)",
    seg_track="#E6E8EC", thumb="#FFFFFF")
SAPPHIRE = GRAPHITE  # older names
SPACE = GRAPHITE
DARK = GRAPHITE
LIGHT = PORCELAIN
THEMES: tuple[Theme, ...] = (GRAPHITE, PORCELAIN)
#: panels are opaque (the names stay for the callers); hairlines are a faint white
GLASS_GROUP = GRAPHITE.group
GLASS_GROUP_HOVER = "rgba(255, 255, 255, 0.05)"
GLASS_SIDEBAR = GRAPHITE.sidebar
GLASS_EDGE = "rgba(255, 255, 255, 0.06)"
GLASS_SEPARATOR = "rgba(255, 255, 255, 0.07)"
GLASS_GROUP_ALPHA = 1.0
GLASS_GROUP_RGB = (26, 27, 31)

# ---- design tokens (px at 100 %, Qt applies the monitor's DPI factor on top) ----------------------
#: spacing scale: every gap / padding of the launcher is one of these (4-pt grid)
SP_1, SP_2, SP_3, SP_4, SP_5, SP_6, SP_7 = 4, 8, 12, 16, 24, 32, 40
SPACING = (SP_1, SP_2, SP_3, SP_4, SP_5, SP_6, SP_7)
SIDEBAR_W = 232
CONTENT_MAX = 760        # centred column of a page (a wide screen gets air, not stretched rows)
WIDE_MAX = 960           # dashboard pages (Accueil): two cards side by side
PAGE_PAD_X = SP_7
PAGE_PAD_TOP = SP_6
GROUP_RADIUS = 12
CONTROL_RADIUS = 8
BADGE_RADIUS = 6
ROW_MIN_H = 48
ROW_PAD_X = SP_4
ROW_PAD_Y = SP_3
CARD_PAD = SP_5          # inner padding of a free card (status, game cards, empty states)
SECTION_GAP = SP_6       # air between sections
#: type scale (Inter / Inter Display): page title, card headline, section title, body, small, caption
TITLE_PX = 26
HEADLINE_PX = 18
SECTION_PX = 15
BODY_PX = 14
SMALL_PX = 13
CAPTION_PX = 12
TYPE_SCALE = (TITLE_PX, HEADLINE_PX, SECTION_PX, BODY_PX, SMALL_PX, CAPTION_PX)
MONO_FAMILIES = ("Cascadia Mono", "Consolas", "SF Mono", "DejaVu Sans Mono", "Liberation Mono", "Courier New")

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


def pick_mono() -> str:
    """Monospace family for numerals and kickers (Cascadia / Consolas on Windows), else the system fixed font."""
    try:
        have = set(QtGui.QFontDatabase.families())
        for f in MONO_FAMILIES:
            if f in have:
                return f
        return QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont).family()
    except Exception:
        return "Consolas"


def style_sheet(t: Theme, body: str, display: str, mono: str = "Consolas") -> str:
    """The ONE application style sheet (selectors by object name / dynamic property only), built from the
    theme's tokens: the same rules give the dark and the light window."""
    return f"""
* {{ font-family: "{body}"; font-size: {BODY_PX}px; color: {t.text}; }}
QMainWindow, QDialog {{ background: {t.window}; }}
QDialog {{ background: {t.sidebar}; }}
#Backdrop {{ background: {t.window}; }}
#Page, #PageBody, QStackedWidget, #Page > QWidget {{ background: transparent; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
#Sidebar {{ background: {t.sidebar}; border: none; border-right: 1px solid {t.edge}; }}
#SideFoot {{ background: {t.group}; border: 1px solid {t.edge}; border-radius: {CONTROL_RADIUS}px; }}
QLabel[role="brand"] {{ font-family: "{display}"; font-size: 16px; font-weight: 600; }}
QLabel[role="brandsub"] {{ font-size: {CAPTION_PX}px; color: {t.tertiary}; }}
QLabel[role="navgroup"] {{ font-size: {CAPTION_PX}px; font-weight: 600; color: {t.tertiary}; }}
QLabel {{ background: transparent; }}
QLabel[role="title"] {{ font-family: "{display}"; font-size: {TITLE_PX}px; font-weight: 600; }}
QLabel[role="headline"] {{ font-family: "{display}"; font-size: {HEADLINE_PX}px; font-weight: 600; }}
QLabel[role="cardtitle"] {{ font-family: "{display}"; font-size: {SECTION_PX}px; font-weight: 600; }}
QLabel[role="section"] {{ font-family: "{display}"; font-size: {SECTION_PX}px; font-weight: 600; color: {t.text}; }}
QLabel[role="kicker"] {{ font-size: {CAPTION_PX}px; font-weight: 600; color: {t.tertiary}; }}
QLabel[role="mono"] {{ font-size: {SMALL_PX}px; color: {t.secondary}; }}
QLabel[role="monofoot"] {{ font-size: {CAPTION_PX}px; color: {t.tertiary}; }}
QLabel[role="secondary"] {{ font-size: {SMALL_PX}px; color: {t.secondary}; }}
QLabel[role="footnote"] {{ font-size: {CAPTION_PX}px; color: {t.tertiary}; }}
QLabel[role="value"] {{ color: {t.secondary}; }}
QLabel[role="big"] {{ font-family: "{display}"; font-size: 24px; font-weight: 600; }}
QLabel[tone="danger"] {{ color: {t.danger}; }}
QLabel[tone="warning"] {{ color: {t.warning}; }}
QLabel[tone="ok"] {{ color: {t.ok}; }}
QLabel[tone="accent"] {{ color: {t.accent}; }}
QLabel[role="badge"] {{ font-size: {CAPTION_PX}px; font-weight: 600; color: {t.secondary}; background: {t.control};
  border-radius: {BADGE_RADIUS}px; padding: 2px 8px; }}
QLabel[role="badge"][tone="ok"] {{ color: {t.ok}; background: {t.ok_soft}; }}
QLabel[role="badge"][tone="warning"] {{ color: {t.warning}; background: {t.warning_soft}; }}
QLabel[role="badge"][tone="danger"] {{ color: {t.danger}; background: {t.danger_soft}; }}
QLabel[role="badge"][tone="accent"] {{ color: {t.accent}; background: {t.accent_tint}; }}
QLabel a {{ color: {t.accent}; }}
#Group {{ background: {t.group}; border: 1px solid {t.edge}; border-radius: {GROUP_RADIUS}px; }}
#Group[tone="accent"] {{ border: 1px solid {t.accent}; }}
#Group[clickable="true"]:hover {{ background: {t.group_hover}; }}
#Empty {{ background: transparent; border: 1px dashed {t.control_border}; border-radius: {GROUP_RADIUS}px; }}
#Separator {{ background: {t.separator}; border: none; }}
QPushButton#FoldHead {{ background: transparent; border: none; border-top: 1px solid {t.separator};
  border-radius: 0; text-align: left; padding: 16px 4px 4px 4px; font-family: "{display}";
  font-size: {SECTION_PX}px; font-weight: 600; color: {t.secondary}; }}
QPushButton#FoldHead:hover, QPushButton#FoldHead:checked {{ color: {t.text}; }}
QPushButton {{ background: {t.control}; border: 1px solid {t.control_border}; border-radius: {CONTROL_RADIUS}px;
  padding: 7px 16px; min-height: 20px; font-weight: 500; }}
QPushButton:hover {{ background: {t.control_hover}; }}
QPushButton:pressed {{ background: {t.separator}; }}
QPushButton:disabled {{ color: {t.tertiary}; }}
QPushButton[kind="primary"] {{ background: {t.accent}; color: {t.on_accent}; border: 1px solid {t.accent};
  font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: {t.accent_hover}; border-color: {t.accent_hover}; }}
QPushButton[kind="primary"]:disabled {{ background: {t.track_off}; border-color: {t.track_off};
  color: {t.tertiary}; }}
QPushButton[kind="danger"] {{ background: {t.control}; color: {t.danger}; }}
QPushButton[kind="link"] {{ background: transparent; border: none; color: {t.accent}; padding: 2px 0;
  font-weight: 500; }}
QPushButton[kind="link"]:hover {{ text-decoration: underline; }}
QPushButton[kind="large"] {{ padding: 9px 22px; font-size: 15px; }}
QPushButton#NavItem {{ background: transparent; border: none; border-radius: {CONTROL_RADIUS}px; text-align: left;
  padding: 7px 10px; font-size: {BODY_PX}px; font-weight: 500; color: {t.secondary}; }}
QPushButton#NavItem:hover {{ background: {t.hover}; color: {t.text}; }}
QPushButton#NavItem:checked {{ background: {t.accent_soft}; color: {t.text}; font-weight: 600; }}
QPushButton#NavItem[live="true"] {{ color: {t.text}; font-weight: 600; }}
QComboBox {{ background: {t.control}; border: 1px solid {t.control_border}; border-radius: {CONTROL_RADIUS}px;
  padding: 5px 30px 5px 10px; min-height: 20px; min-width: 140px; }}
QComboBox:hover {{ background: {t.control_hover}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
QComboBox QAbstractItemView {{ background: {t.group}; border: 1px solid {t.control_border};
  selection-background-color: {t.accent}; selection-color: {t.on_accent}; padding: 4px; outline: none; }}
QLineEdit {{ background: {t.control}; border: 1px solid {t.control_border}; border-radius: {CONTROL_RADIUS}px;
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
#Segmented {{ background: {t.seg_track}; border-radius: 8px; }}
QPushButton#Segment {{ background: transparent; border: none; border-radius: 6px; padding: 4px 12px;
  font-size: {SMALL_PX}px; font-weight: 500; min-height: 18px; color: {t.secondary}; }}
QPushButton#Segment:hover {{ color: {t.text}; }}
QPushButton#Segment:checked {{ background: {t.thumb}; color: {t.text}; font-weight: 600; }}
QListView#VList {{ background: {t.group}; border: 1px solid {t.edge}; border-radius: {GROUP_RADIUS}px;
  padding: 4px 0; outline: none; }}
QListView#VList::item {{ padding: 7px {ROW_PAD_X}px; border: none; }}
QListView#VList::item:hover {{ background: {t.hover}; }}
QProgressBar[tone="ok"]::chunk {{ background: {t.ok}; }}
QProgressBar[tone="warning"]::chunk {{ background: {t.warning}; }}
QLabel[role="num"] {{ font-size: {SMALL_PX}px; font-weight: 600; color: {t.secondary}; background: {t.control};
  border-radius: {BADGE_RADIUS}px; padding: 2px 0; }}
QLabel[role="num"][tone="accent"] {{ color: {t.accent}; background: {t.accent_tint}; }}
QLabel[role="metric"] {{ font-family: "{display}"; font-size: {HEADLINE_PX}px; font-weight: 600; }}
#Bubble {{ background: {t.group}; border: 1px solid {t.edge}; border-radius: {GROUP_RADIUS}px; }}
#Bubble[who="me"] {{ background: {t.accent_tint}; border: 1px solid {t.accent_tint}; }}
#Bubble[who="error"] {{ background: {t.danger_soft}; border: 1px solid {t.danger_soft}; }}
QPlainTextEdit#ChatInput {{ background: {t.control}; border: 1px solid {t.control_border};
  border-radius: {CONTROL_RADIUS}px; padding: 4px 6px; selection-background-color: {t.accent};
  selection-color: {t.on_accent}; }}
QPlainTextEdit#ChatInput:focus {{ border: 2px solid {t.accent}; padding: 3px 5px; }}
QPlainTextEdit#ChatInput:disabled {{ color: {t.tertiary}; }}
QPushButton[kind="chip"] {{ background: {t.group}; border: 1px solid {t.control_border};
  border-radius: {CONTROL_RADIUS}px; padding: 5px 12px; min-height: 18px; font-size: {SMALL_PX}px;
  font-weight: 500; color: {t.secondary}; }}
QPushButton[kind="chip"]:hover {{ background: {t.group_hover}; color: {t.text}; }}
QPushButton[kind="chip"]:disabled {{ color: {t.tertiary}; }}
#Toast {{ background: {t.text}; border-radius: 10px; }}
#Toast QLabel {{ color: {t.window}; font-weight: 500; }}
#Toast[level="error"] QLabel, #Toast[level="warning"] QLabel {{ font-weight: 600; }}
"""


_fonts_loaded: list[bool] = []


def load_fonts() -> int:
    """Register the bundled Inter files once (a few ms); returns how many loaded."""
    if _fonts_loaded:
        return int(_fonts_loaded[0])
    n = 0
    try:
        from treeaicoach.paths import asset_path  # noqa: PLC0415

        for name in FONT_FILES:
            if QtGui.QFontDatabase.addApplicationFont(str(asset_path("fonts", name))) >= 0:
                n += 1
    except Exception:
        pass
    _fonts_loaded.append(bool(n))
    _fonts_loaded[0] = n
    return n


def apply_theme(app: QtWidgets.QApplication, dark: bool = True) -> Theme:
    """Graphite (dark, the default) or porcelain (light): fonts, palette and the one style sheet."""
    t = GRAPHITE if dark else PORCELAIN
    load_fonts()
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
    app.setStyleSheet(style_sheet(t, body, display, pick_mono()))
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
    elif kind == "discord":          # a headset (the voice channel)
        p.moveTo(4 * u, 12 * u); p.lineTo(4 * u, 10 * u)
        p.cubicTo(4 * u, 2.5 * u, 16 * u, 2.5 * u, 16 * u, 10 * u); p.lineTo(16 * u, 12 * u)
        rr(3, 11, 3.5, 5.5, 1.2)
        rr(13.5, 11, 3.5, 5.5, 1.2)
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
    elif kind == "advanced":         # three sliders
        for y, x in ((5.5, 12.5), (10, 7), (14.5, 11)):
            p.moveTo(3 * u, y * u); p.lineTo(17 * u, y * u)
            p.addEllipse(QtCore.QPointF(x * u, y * u), 1.9 * u, 1.9 * u)
    elif kind == "about":
        p.addEllipse(QtCore.QPointF(10 * u, 10 * u), 7.5 * u, 7.5 * u)
        p.moveTo(10 * u, 9 * u); p.lineTo(10 * u, 14 * u)
        p.addEllipse(QtCore.QPointF(10 * u, 6.4 * u), 0.5 * u, 0.5 * u)
    elif kind == "update":
        p.moveTo(10 * u, 3.5 * u); p.lineTo(10 * u, 12.5 * u)
        p.moveTo(6.5 * u, 9 * u); p.lineTo(10 * u, 12.5 * u); p.lineTo(13.5 * u, 9 * u)
        p.moveTo(4 * u, 14 * u); p.lineTo(4 * u, 16.5 * u); p.lineTo(16 * u, 16.5 * u); p.lineTo(16 * u, 14 * u)
    elif kind == "wow":              # a sword (World of Warcraft: Forever)
        p.moveTo(14.5 * u, 3 * u); p.lineTo(17 * u, 3 * u); p.lineTo(17 * u, 5.5 * u); p.lineTo(8.5 * u, 14 * u)
        p.lineTo(6 * u, 11.5 * u); p.closeSubpath()
        p.moveTo(4.5 * u, 10 * u); p.lineTo(10 * u, 15.5 * u)
        p.moveTo(6.5 * u, 13.5 * u); p.lineTo(3.5 * u, 16.5 * u)
    elif kind == "lol":              # a shield with a crest line (League of Legends)
        p.moveTo(10 * u, 2.5 * u); p.lineTo(16 * u, 5 * u); p.lineTo(16 * u, 10 * u)
        p.cubicTo(16 * u, 13.8 * u, 13.4 * u, 16.2 * u, 10 * u, 17.5 * u)
        p.cubicTo(6.6 * u, 16.2 * u, 4 * u, 13.8 * u, 4 * u, 10 * u); p.lineTo(4 * u, 5 * u); p.closeSubpath()
        p.moveTo(10 * u, 6 * u); p.lineTo(10 * u, 14 * u)
    elif kind == "clock":
        p.addEllipse(QtCore.QPointF(10 * u, 10 * u), 7 * u, 7 * u)
        p.moveTo(10 * u, 6 * u); p.lineTo(10 * u, 10 * u); p.lineTo(13 * u, 12 * u)
    elif kind == "play":
        p.moveTo(7 * u, 5 * u); p.lineTo(15 * u, 10 * u); p.lineTo(7 * u, 15 * u); p.closeSubpath()
    elif kind == "tray":             # empty states: an open box
        p.moveTo(3 * u, 11 * u); p.lineTo(5.5 * u, 5 * u); p.lineTo(14.5 * u, 5 * u); p.lineTo(17 * u, 11 * u)
        p.lineTo(17 * u, 16 * u); p.lineTo(3 * u, 16 * u); p.closeSubpath()
        p.moveTo(3 * u, 11 * u); p.lineTo(7.5 * u, 11 * u); p.lineTo(8.5 * u, 13 * u); p.lineTo(11.5 * u, 13 * u)
        p.lineTo(12.5 * u, 11 * u); p.lineTo(17 * u, 11 * u)
    elif kind == "chat":             # a speech bubble with a tail (« Parler à l'IA »)
        p.moveTo(5.5 * u, 3.5 * u); p.lineTo(14.5 * u, 3.5 * u)
        p.quadTo(17 * u, 3.5 * u, 17 * u, 6 * u); p.lineTo(17 * u, 11.5 * u)
        p.quadTo(17 * u, 14 * u, 14.5 * u, 14 * u); p.lineTo(9 * u, 14 * u); p.lineTo(5.5 * u, 17 * u)
        p.lineTo(5.5 * u, 14 * u); p.quadTo(3 * u, 14 * u, 3 * u, 11.5 * u); p.lineTo(3 * u, 6 * u)
        p.quadTo(3 * u, 3.5 * u, 5.5 * u, 3.5 * u); p.closeSubpath()
        p.moveTo(7 * u, 7.5 * u); p.lineTo(13 * u, 7.5 * u)
        p.moveTo(7 * u, 10.5 * u); p.lineTo(11 * u, 10.5 * u)
    elif kind == "chevron":
        p.moveTo(8 * u, 5 * u); p.lineTo(13 * u, 10 * u); p.lineTo(8 * u, 15 * u)
    else:
        p.addEllipse(QtCore.QPointF(10 * u, 10 * u), 6 * u, 6 * u)
    return p


def glyph_pixmap(kind: str, color: str, size: int = 20, dpr: float = 2.0) -> QtGui.QPixmap:
    pm = QtGui.QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    qp = QtGui.QPainter(pm)
    qp.setRenderHint(QtGui.QPainter.Antialiasing)
    qp.setPen(QtGui.QPen(QtGui.QColor(color), 1.6 * size / 20.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    qp.drawPath(_glyph_path(kind, size))
    qp.end()
    return pm


class Glyph(QtWidgets.QLabel):
    """A line icon as a label, in a theme colour (``tone``: secondary / accent / ok / warning / danger);
    repainted from the current theme on :meth:`recolor` (live theme switch)."""

    def __init__(self, kind: str, size: int = 20, tone: str = "secondary",
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind, self._size, self._tone = kind, size, tone
        self.setFixedSize(size, size)
        self.recolor()

    def set_tone(self, tone: str) -> None:
        if tone != self._tone:
            self._tone = tone
            self.recolor()

    def set_kind(self, kind: str) -> None:
        if kind != self._kind:
            self._kind = kind
            self.recolor()

    def recolor(self) -> None:
        t = theme()
        col = {"accent": t.accent, "ok": t.ok, "warning": t.warning, "danger": t.danger, "text": t.text,
               "tertiary": t.tertiary}.get(self._tone, t.secondary)
        self.setPixmap(glyph_pixmap(self._kind, col, self._size))


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
        self._anim_obj: Any = None            # (cost) created on the first toggle, not per row built
        self.toggled.connect(self._animate)

    @property
    def _anim(self) -> Any:
        if self._anim_obj is None:
            self._anim_obj = QtCore.QVariantAnimation(self, duration=120, easingCurve=QtCore.QEasingCurve.OutCubic)
            self._anim_obj.valueChanged.connect(self._on_anim)
        return self._anim_obj

    def set_quiet(self, on: bool) -> None:
        """Set without emitting (sync from the configuration)."""
        self.blockSignals(True)
        self.setChecked(bool(on))
        self.blockSignals(False)
        if self._anim_obj is not None:
            self._anim_obj.stop()
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

    def set_choice_visible(self, value: str, on: bool) -> None:
        """Show / hide one segment (a tab that only exists sometimes, e.g. « Donjon » in an instance)."""
        b = self._buttons.get(str(value))
        if b is not None and b.isHidden() == bool(on):
            b.setVisible(bool(on))

    def choice_visible(self, value: str) -> bool:
        b = self._buttons.get(str(value))
        return b is not None and not b.isHidden()


class VirtualList(QtWidgets.QListView):
    """A long list (errors, events, loot) that costs nothing to keep on screen: a C++ model and the default
    delegate, so only the visible lines are painted and no Python runs while it scrolls or repaints.
    ``set_rows([(text, tooltip, tone)])`` touches only the lines that changed; up to ``max_visible`` lines are
    shown, the rest scroll inside. ``tone`` (ok / warning / danger / accent / "") colours a line."""

    TONE_ROLE = int(Qt.UserRole) + 1

    def __init__(self, max_visible: int = 8, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VList")
        self._model = QtGui.QStandardItemModel(self)
        self.setModel(self._model)
        self.setUniformItemSizes(True)
        self.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.setFocusPolicy(Qt.NoFocus)
        self.setTextElideMode(Qt.ElideRight)
        self.setWordWrap(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self._max = max(1, int(max_visible))
        self._fit()

    def rows(self) -> list[str]:
        return [self._model.item(i).text() for i in range(self._model.rowCount())]

    def set_rows(self, rows: Sequence[Any]) -> None:
        m = self._model
        old = m.rowCount()
        for i, row in enumerate(rows):
            text, tip, tone = (tuple(row) + ("", ""))[:3] if isinstance(row, (tuple, list)) else (str(row), "", "")
            text, tip, tone = str(text), str(tip or ""), str(tone or "")
            if i < old:
                it = m.item(i)
                if it.text() != text:
                    it.setText(text)
                if it.toolTip() != tip:
                    it.setToolTip(tip)
                if it.data(self.TONE_ROLE) != tone:
                    it.setData(tone, self.TONE_ROLE)
                    self._paint_tone(it)
            else:
                it = QtGui.QStandardItem(text)
                it.setEditable(False)
                it.setToolTip(tip)
                it.setData(tone, self.TONE_ROLE)
                self._paint_tone(it)
                m.appendRow(it)
        if old > len(rows):
            m.removeRows(len(rows), old - len(rows))
        self._fit()

    def _paint_tone(self, it: QtGui.QStandardItem) -> None:
        tone = str(it.data(self.TONE_ROLE) or "")
        col = getattr(theme(), tone, None) if tone in ("ok", "warning", "danger", "accent", "secondary") else None
        if col:
            it.setForeground(QtGui.QBrush(QtGui.QColor(col)))
        else:                                  # no tone: the palette's text colour (an empty brush paints black)
            it.setData(None, Qt.ForegroundRole)

    def recolor(self) -> None:
        for i in range(self._model.rowCount()):
            self._paint_tone(self._model.item(i))

    def _fit(self) -> None:
        n = self._model.rowCount()
        line = self.fontMetrics().height() + 14
        h = line * max(1, min(n, self._max)) + 10
        if self.height() != h or self.minimumHeight() != h:
            self.setFixedHeight(h)


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
        self._grid.setContentsMargins(ROW_PAD_X, ROW_PAD_Y, ROW_PAD_X, ROW_PAD_Y)
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


def badge(text: str = "", tone: str = "") -> QtWidgets.QLabel:
    """A small status pill (« En partie », « Prêt », « À installer »): tinted background, tone colour."""
    b = label(text, "badge")
    if tone:
        b.setProperty("tone", tone)
    b.setAlignment(Qt.AlignCenter)
    return b


def set_badge(b: QtWidgets.QLabel, text: str, tone: str = "") -> None:
    if b.text() != text:
        b.setText(text)
    set_prop(b, "tone", tone)
    b.setVisible(bool(text))


class Card(QtWidgets.QFrame):
    """A free card (same surface as a group, inner padding ``CARD_PAD``). ``clickable``: the whole card is
    a target (hover tint, hand cursor, ``clicked``), for the dashboard tiles that lead to a page."""

    clicked = QtCore.Signal()

    def __init__(self, clickable: bool = False, pad: int = CARD_PAD, spacing: int = SP_2,
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Group")
        self.lay = QtWidgets.QVBoxLayout(self)
        self.lay.setContentsMargins(pad, pad - 4, pad, pad - 4)
        self.lay.setSpacing(spacing)
        if clickable:
            self.setProperty("clickable", True)
            self.setCursor(Qt.PointingHandCursor)
            self.setAttribute(Qt.WA_Hover, True)

    def mouseReleaseEvent(self, e: Any) -> None:  # noqa: N802
        if self.property("clickable") and e.button() == Qt.LeftButton and self.rect().contains(e.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def add(self, w: Any, stretch: int = 0) -> Any:
        if isinstance(w, QtWidgets.QLayout):
            self.lay.addLayout(w, stretch)
        else:
            self.lay.addWidget(w, stretch)
        return w


class EmptyState(QtWidgets.QFrame):
    """What a list shows while it has nothing: a quiet icon, one short title, one line saying how to fill
    it, an optional button. Dashed outline, no fill (it is a placeholder, not content)."""

    def __init__(self, glyph: str, title: str, text: str = "", action: str = "",
                 on_action: Callable[[], Any] | None = None, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Empty")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(SP_5, SP_4, SP_5, SP_4)
        lay.setSpacing(SP_4)
        self.icon = Glyph(glyph, 24, "tertiary")
        lay.addWidget(self.icon, 0, Qt.AlignVCenter)
        col = QtWidgets.QVBoxLayout()
        col.setSpacing(2)
        self.title = label(title, "cardtitle")
        col.addWidget(self.title)
        self.text = label(text, "secondary", wrap=True)
        col.addWidget(self.text)
        if not text:
            self.text.hide()
        lay.addLayout(col, 1)
        self.button = None
        if action:
            self.button = button(action, on_action)
            lay.addWidget(self.button, 0, Qt.AlignVCenter)

    def set_text(self, title: str, text: str = "") -> None:
        self.title.setText(title)
        self.text.setText(text)
        self.text.setVisible(bool(text))


class Columns(QtWidgets.QWidget):
    """Two (or more) cards side by side when the column is wide enough (``min_each`` px per card), else
    stacked: the dashboard reads at 980 x 640 as well as full screen. Re-laid only when it crosses."""

    def __init__(self, min_each: int = 320, gap: int = SP_4, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._min_each = min_each
        self._lay = QtWidgets.QBoxLayout(QtWidgets.QBoxLayout.LeftToRight, self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(gap)
        self._items: list[QtWidgets.QWidget] = []

    def add(self, w: QtWidgets.QWidget) -> QtWidgets.QWidget:
        self._items.append(w)
        w.setSizePolicy(QtWidgets.QSizePolicy.Ignored, w.sizePolicy().verticalPolicy())
        self._lay.addWidget(w, 1)
        return w

    def set_order(self, widgets: Sequence[QtWidgets.QWidget]) -> None:
        """Put these widgets first (e.g. the game that runs now), keeping the others after them."""
        order = [w for w in widgets if w in self._items] + [w for w in self._items if w not in widgets]
        if order == [self._lay.itemAt(i).widget() for i in range(self._lay.count())]:
            return
        for w in order:
            self._lay.removeWidget(w)
        for w in order:
            self._lay.addWidget(w, 1)

    def is_wide(self) -> bool:
        return self._lay.direction() == QtWidgets.QBoxLayout.LeftToRight

    def resizeEvent(self, e: Any) -> None:  # noqa: N802
        n = max(1, len([w for w in self._items if not w.isHidden()]))
        wide = self.width() >= n * self._min_each + (n - 1) * self._lay.spacing()
        want = QtWidgets.QBoxLayout.LeftToRight if wide else QtWidgets.QBoxLayout.TopToBottom
        if self._lay.direction() != want:
            self._lay.setDirection(want)
        super().resizeEvent(e)


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
            hl.setContentsMargins(ROW_PAD_X, 0, 0, 0)
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
            head.setContentsMargins(4, 0, 4, 4)
            head.setSpacing(16)
            self.header = label(title, "section")
            head.addWidget(self.header, 0)
            head.addStretch(1)
            if action is not None:
                head.addWidget(action, 0, Qt.AlignRight)
            lay.addLayout(head)
        self.group = Group()
        lay.addWidget(self.group)
        self.footnote = label(footnote, "footnote", wrap=True)
        self.footnote.setContentsMargins(ROW_PAD_X, 0, ROW_PAD_X, 0)
        lay.addWidget(self.footnote)          # parented first: setVisible(True) on an orphan makes a window
        if not footnote:
            self.footnote.hide()

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
        outer.setContentsMargins(PAGE_PAD_X, PAGE_PAD_TOP, PAGE_PAD_X, SP_7)
        self.column = QtWidgets.QWidget()
        self.column.setMaximumWidth(max_width)
        outer.addStretch(1)
        outer.addWidget(self.column, 100)
        outer.addStretch(1)
        self.lay = QtWidgets.QVBoxLayout(self.column)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(SECTION_GAP)
        # header: title + one short sentence on the left, the page's own action (if any) on the right
        head = QtWidgets.QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(SP_4)
        texts = QtWidgets.QVBoxLayout()
        texts.setSpacing(SP_1 + 2)
        self.title = label(title, "title")
        texts.addWidget(self.title)
        self.subtitle = label(subtitle, "secondary", wrap=True)
        texts.addWidget(self.subtitle)
        if not subtitle:
            self.subtitle.hide()
        head.addLayout(texts, 1)
        self._head = head
        self.lay.addLayout(head)
        self.lay.addStretch(1)
        self.setWidget(body)

    def set_subtitle(self, text: str) -> None:
        if self.subtitle.text() != text:
            self.subtitle.setText(text)
        self.subtitle.setVisible(bool(text))

    def set_action(self, w: QtWidgets.QWidget) -> QtWidgets.QWidget:
        """A button (or a badge) at the right of the page title."""
        self._head.addWidget(w, 0, Qt.AlignRight | Qt.AlignVCenter)
        return w

    def add(self, w: QtWidgets.QWidget) -> QtWidgets.QWidget:
        self.lay.insertWidget(self.lay.count() - 1, w)
        return w

    def section(self, title: str = "", footnote: str = "", action: QtWidgets.QWidget | None = None) -> Section:
        s = Section(title, footnote, action)
        self.add(s)
        return s

    def fold(self, title: str = "Avancé", hint: str = "") -> "Fold":
        """The page's collapsible block of rarely used settings (closed)."""
        return self.add(Fold(title, hint))

    def reveal(self, w: QtWidgets.QWidget) -> None:
        """Open the fold holding ``w`` (if any) and scroll it into view."""
        p = w.parentWidget()
        while p is not None and p is not self:
            if isinstance(p, Fold):
                p.set_open(True)
            p = p.parentWidget()
        self.widget().layout().activate()
        self.column.layout().activate()

        def scroll() -> None:
            try:
                self.verticalScrollBar().setValue(max(0, w.mapTo(self.widget(), QtCore.QPoint(0, 0)).y() - 24))
            except RuntimeError:          # deleted meanwhile
                pass
        scroll()
        QtCore.QTimer.singleShot(0, self, scroll)


class Fold(QtWidgets.QWidget):
    """One collapsible block at the bottom of a page (« Avancé »): the rarely touched sections stay
    built (every setting reachable, tests) but out of sight until the player opens it."""

    def __init__(self, title: str = "Avancé", hint: str = "", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Fold")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SECTION_GAP)
        self.head = QtWidgets.QPushButton(title)
        self.head.setObjectName("FoldHead")
        self.head.setCheckable(True)
        self.head.setCursor(Qt.PointingHandCursor)
        hl = QtWidgets.QHBoxLayout(self.head)
        hl.setContentsMargins(0, 16, 4, 4)
        hl.addStretch(1)
        self.state = label("", "secondary")
        hl.addWidget(self.state, 0, Qt.AlignRight | Qt.AlignVCenter)
        self.hint = hint
        lay.addWidget(self.head)
        self.body = QtWidgets.QWidget()
        self.body_lay = QtWidgets.QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 0, 0, 0)
        self.body_lay.setSpacing(SECTION_GAP)
        lay.addWidget(self.body)
        self.body.hide()
        self.head.toggled.connect(self.set_open)
        self._builders: list[Callable[["Fold"], Any]] = []
        self._sync()

    def defer(self, build: Callable[["Fold"], Any]) -> None:
        """Build the content with ``build(self)`` only on the first opening (or :meth:`materialize`):
        the rows of a closed fold cost nothing at start-up."""
        self._builders.append(build)

    def materialize(self) -> bool:
        """Run the deferred builders now (idle turn, tests, a jump to a setting). True if it built something."""
        todo, self._builders = self._builders, []
        for build in todo:
            try:
                build(self)
            except Exception:       # a broken block never takes the page down
                import logging  # noqa: PLC0415

                logging.getLogger(__name__).exception("Cannot build the « %s » block", self.head.text())
        return bool(todo)

    def _sync(self) -> None:
        self.state.setText("Masquer" if self.is_open() else (f"{self.hint} · Afficher" if self.hint else "Afficher"))

    def is_open(self) -> bool:
        return not self.body.isHidden()

    def set_open(self, on: bool) -> None:
        on = bool(on)
        if on:
            self.materialize()
        if self.head.isChecked() != on:
            self.head.setChecked(on)          # re-enters once through toggled
            return
        self.body.setVisible(on)
        self._sync()

    def add(self, w: QtWidgets.QWidget) -> QtWidgets.QWidget:
        self.body_lay.addWidget(w)
        return w

    def section(self, title: str = "", footnote: str = "", action: QtWidgets.QWidget | None = None) -> Section:
        s = Section(title, footnote, action)
        self.add(s)
        return s


class NavButton(QtWidgets.QPushButton):
    """One sidebar row: glyph + label; while its game runs (``live``) a small accent dot on the right."""

    def paintEvent(self, e: Any) -> None:  # noqa: N802
        super().paintEvent(e)
        if not self.property("live"):
            return
        qp = QtGui.QPainter(self)
        qp.setRenderHint(QtGui.QPainter.Antialiasing)
        qp.setPen(Qt.NoPen)
        qp.setBrush(QtGui.QColor(theme().ok))
        r = 3.5
        qp.drawEllipse(QtCore.QPointF(self.width() - 14, self.height() / 2), r, r)
        qp.end()


class Sidebar(QtWidgets.QFrame):
    """Navigation (icon + label rows, soft fill and accent icon on the current page) in groups (a small
    caption above each group; the last group sits at the bottom) + a status card at the foot."""

    selected = QtCore.Signal(str)

    def __init__(self, pages: Sequence[tuple[str, str, str]],
                 groups: Sequence[tuple[str, Sequence[str]]] | None = None,
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(SIDEBAR_W)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(SP_3, SP_5, SP_3, SP_3)
        lay.setSpacing(2)
        top = QtWidgets.QHBoxLayout()
        top.setContentsMargins(SP_2, 0, 0, SP_5)
        top.setSpacing(SP_3 - 2)
        self.mark = QtWidgets.QLabel()
        self.mark.setFixedSize(28, 28)
        top.addWidget(self.mark, 0, Qt.AlignVCenter)
        names = QtWidgets.QVBoxLayout()
        names.setSpacing(0)
        self.brand = QtWidgets.QLabel()
        self.brand.setProperty("role", "brand")
        names.addWidget(self.brand)
        self.brand_sub = label("League of Legends · WoW", "brandsub")
        names.addWidget(self.brand_sub)
        top.addLayout(names, 1)
        lay.addLayout(top)
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: dict[str, QtWidgets.QPushButton] = {}
        self._kinds: dict[str, str] = {}
        by_key = {k: (k, text, kind) for k, text, kind in pages}
        if not groups:
            groups = (("", [k for k, _t, _k in pages]),)
        #: one widget per group (caption + its items): :meth:`promote` moves a whole group to the top
        self.group_boxes: list[QtWidgets.QWidget] = []
        self._group_keys: list[tuple[str, ...]] = []
        self._nav_lay = lay
        for gi, (caption, keys) in enumerate(groups):
            if gi == len(groups) - 1 and gi > 0:
                lay.addStretch(1)
            box = QtWidgets.QWidget()
            box.setObjectName("NavGroup")
            bl = QtWidgets.QVBoxLayout(box)
            bl.setContentsMargins(0, SP_4 if gi > 0 and gi != len(groups) - 1 else 0, 0, 0)
            bl.setSpacing(2)
            if caption:
                cap = label(caption, "navgroup")
                cap.setContentsMargins(SP_3 - 2, 0, 0, SP_1)
                bl.addWidget(cap)
            for key in keys:
                if key not in by_key:
                    continue
                _k, text, kind = by_key[key]
                b = NavButton(text)
                b.setObjectName("NavItem")
                b.setCheckable(True)
                b.setCursor(Qt.PointingHandCursor)
                b.setIconSize(QtCore.QSize(18, 18))
                b.setMinimumHeight(36)
                self._group.addButton(b)
                self.buttons[key] = b
                self._kinds[key] = kind
                b.clicked.connect(lambda _c=False, k=key: self.selected.emit(k))
                bl.addWidget(b)
            lay.addWidget(box)
            self.group_boxes.append(box)
            self._group_keys.append(tuple(keys))
        self._order = list(range(len(self.group_boxes)))
        if len(groups) < 2:
            lay.addStretch(1)
        lay.addSpacing(SP_3)
        self.update_btn = button("", None, "primary")
        self.update_btn.hide()
        lay.addWidget(self.update_btn)
        lay.addSpacing(SP_2)
        foot = QtWidgets.QFrame()
        foot.setObjectName("SideFoot")
        fl = QtWidgets.QHBoxLayout(foot)
        fl.setContentsMargins(SP_3, SP_2 + 2, SP_3, SP_2 + 2)
        fl.setSpacing(SP_2)
        self.dot = Dot()
        fl.addWidget(self.dot, 0, Qt.AlignVCenter)
        self.status = label("", "secondary")
        self.status.setMinimumWidth(0)
        fl.addWidget(self.status, 1)
        self.foot = foot
        lay.addWidget(foot)
        self.recolor()

    def recolor(self) -> None:
        t = theme()
        for key, b in self.buttons.items():
            b.setIcon(glyph_icon(self._kinds[key], t.secondary, 18, t.accent))

    def set_live(self, key: str, on: bool) -> None:
        """Mark a page as « active now » (a dot on its row: the game it coaches is running)."""
        b = self.buttons.get(key)
        if b is None or bool(b.property("live")) == bool(on):
            return
        set_prop(b, "live", bool(on))
        b.setToolTip("En cours" if on else "")

    def promote(self, key: str, on: bool = True) -> None:
        """Put the group holding ``key`` first (``on``) or back at its place; the bottom group stays put."""
        idx = next((i for i, keys in enumerate(self._group_keys) if key in keys), None)
        last = len(self.group_boxes) - 1
        if idx is None or idx == last:
            return
        order = [i for i in range(last) if i != idx]
        order = ([idx] + order) if on else sorted(order + [idx])
        if order == self._order[:last]:
            return
        self._order = order + [last]
        for pos, gi in enumerate(order):
            box = self.group_boxes[gi]
            self._nav_lay.removeWidget(box)
            self._nav_lay.insertWidget(1 + pos, box)          # after the brand row
            box.layout().setContentsMargins(0, SP_4 if pos > 0 else 0, 0, 0)

    def group_order(self) -> list[str]:
        """The page keys of the groups, top to bottom (tests)."""
        return [k for gi in self._order for k in self._group_keys[gi]]

    def set_current(self, key: str) -> None:
        b = self.buttons.get(key)
        if b is not None and not b.isChecked():
            b.setChecked(True)


class Backdrop(QtWidgets.QWidget):
    """Central widget: a flat window colour (v2.21: no backdrop image, nothing to decode or scale)."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Backdrop")
        self.setAttribute(Qt.WA_StyledBackground, True)


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


__all__ = ["Theme", "GRAPHITE", "PORCELAIN", "LIGHT", "Card", "EmptyState", "Columns", "Glyph", "badge",
           "set_badge", "glyph_pixmap", "NavButton", "SAPPHIRE", "SPACE", "DARK", "THEMES", "Backdrop", "theme", "apply_theme", "system_is_dark", "style_sheet", "Switch", "Dot",
           "Segmented", "Combo", "Slider", "Row", "Group", "Section", "Page", "Sidebar", "Toast", "label",
           "button", "glyph_icon", "set_prop", "repolish"]
