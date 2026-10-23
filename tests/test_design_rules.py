"""Design rules of docs/DESIGN.md, checked automatically so that the "generated template" look
cannot creep back: no em dash in user-facing strings, no banned colours / fonts / emoji, flat
report CSS (no glow, no radial gradient, no pill radius), 4-6 px radius in the desktop UI."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "treeaicoach"
UI_FILES = ("ui.py", "ui_common.py", "ui_dialogs.py", "ui_widgets.py", "ui_page_home.py", "ui_page_alerts.py",
            "ui_page_discord.py",
            "ui_page_analysis.py", "ui_page_overlay.py", "ui_page_settings.py", "ui_page_about.py", "ui_kit.py",
            "calibration.py", "report.py", "replay.py", "progress.py")
#: the Qt launcher, split over several modules (ui.py + ui_widgets.py + one module per page)
APP_FILES = tuple(f for f in UI_FILES if f.startswith("ui") and f != "ui_kit.py") + ("calibration.py",)


def _app_source() -> str:
    return "\n".join((PKG / f).read_text(encoding="utf-8") for f in APP_FILES)

EM_DASH = chr(0x2014)
BANNED_COLORS = (
    "#C8AA6E", "#0A1428", "#010A13", "#F0E6D2", "#785A28",        # Riot client gold / navy
    "#3B82F6", "#6366F1", "#8B5CF6", "#A855F7", "#7C3AED",        # framework blue / indigo / violet
    "#0F172A", "#1E293B",                                          # default slate "dark mode"
)
BANNED_FONTS = ("Inter", "Poppins", "Space Grotesk", "Geist")
_EMOJI = re.compile("[\U0001F300-\U0001FAFF✨⭐\U0001F000-\U0001F2FF]")


def _strings(path: Path) -> list[tuple[int, str]]:
    """Non-docstring string constants of a module (line, value)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docs.add(id(first.value))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            out.append((node.lineno, node.value))
    return out


@pytest.mark.parametrize("name", UI_FILES)
def test_no_em_dash_or_emoji_in_ui_strings(name: str) -> None:
    bad = [(ln, s[:60]) for ln, s in _strings(PKG / name) if EM_DASH in s or _EMOJI.search(s)]
    assert not bad, f"{name}: em dash / emoji in user-facing strings (docs/DESIGN.md): {bad[:5]}"


@pytest.mark.parametrize("name", UI_FILES)
def test_no_banned_colours(name: str) -> None:
    src = (PKG / name).read_text(encoding="utf-8").upper()
    found = [c for c in BANNED_COLORS if c in src]
    assert not found, f"{name}: banned colours {found} (docs/DESIGN.md)"


def test_no_template_fonts() -> None:
    """The launcher's identity font is Segoe UI Variable (Windows); Inter / Noto / DejaVu are only
    fallbacks for other systems (docs/DESIGN.md, "Lanceur")."""
    pytest.importorskip("PySide6.QtWidgets")
    from treeaicoach import ui_widgets as W

    # v2.17: Inter (OFL) is bundled and is the identity; Segoe UI Variable is the fallback
    assert W.FONT_FAMILIES[0] == "Inter" and W.DISPLAY_FAMILIES[0] == "Inter Display"
    for fams in (W.FONT_FAMILIES, W.DISPLAY_FAMILIES):
        assert any(f.startswith("Segoe UI") for f in fams), fams
        assert not any(f.startswith(("Poppins", "Space Grotesk", "Geist")) for f in fams), fams
    css = (PKG / "report.py").read_text(encoding="utf-8")
    for fam in BANNED_FONTS:
        assert not re.search(rf"font-family:[^;}}]*\b{fam}\b", css), fam


def test_launcher_shapes_are_quiet() -> None:
    """Grouped lists 10 px, controls 6-8 px, pills only for switches; no gradient, glow or shadow."""
    pytest.importorskip("PySide6.QtWidgets")
    from treeaicoach import ui_widgets as W

    assert W.GROUP_RADIUS <= 12
    qss = "".join(W.style_sheet(t, "Segoe UI", "Segoe UI") for t in W.THEMES)
    radii = [int(x) for x in re.findall(r"border-radius: (\d+)px", qss)]
    assert radii and max(radii) <= 12, radii
    for banned in ("gradient", "box-shadow", "qlineargradient", "qradialgradient"):
        assert banned not in qss.lower(), banned


def _contrast(a: str, b: str) -> float:
    def lum(h: str) -> float:
        c = [int(h.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_text_is_legible() -> None:
    """Readable sizes (nothing under 12 px) and WCAG AA contrast for every text colour, light and dark."""
    pytest.importorskip("PySide6.QtWidgets")
    from treeaicoach import ui_widgets as W

    for t in W.THEMES:
        for bg in (t.window, t.group, t.sidebar):
            for fg in (t.text, t.secondary, t.tertiary, t.accent, t.danger, t.warning):
                assert _contrast(fg, bg) >= 4.5, (t.name, fg, bg, round(_contrast(fg, bg), 2))
        assert _contrast(t.on_accent, t.accent) >= 4.5
    assert min(W.TITLE_PX, W.BODY_PX, W.SMALL_PX, W.CAPTION_PX) >= 12
    qss = W.style_sheet(W.DARK, "Segoe UI", "Segoe UI")
    sizes = [int(x) for x in re.findall(r"font-size: (\d+)px", qss)]
    assert sizes and min(sizes) >= 12, sizes


def test_flat_window_no_decoration() -> None:
    """v2.21 « graphite »: flat opaque panels, no backdrop image, no gold ornament, no monospace."""
    pytest.importorskip("PySide6.QtWidgets")
    from treeaicoach import ui_widgets as W

    assert not (PKG / "ui_backdrop.py").exists() and not (PKG / "assets" / "ui" / "backdrop.png").exists()
    qss = W.style_sheet(W.DARK, "Segoe UI", "Segoe UI", "Consolas")
    assert "Consolas" not in qss and "Scanline" not in qss
    flair = (PKG / "ui_flair.py").read_text(encoding="utf-8")
    assert "GOLD" not in flair and "mono_font(" not in flair.split("def mono_font")[0]


def test_bundled_font_loads() -> None:
    """The Inter files load and give the three weights the style sheet uses (400 / 500 / 600)."""
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6 import QtGui, QtWidgets

    from treeaicoach import ui_widgets as W

    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    assert W.load_fonts() == len(W.FONT_FILES)
    assert {"Regular", "Medium", "SemiBold"} <= set(QtGui.QFontDatabase.styles("Inter"))
    assert "Inter Display" in QtGui.QFontDatabase.families()
    folder = Path(W.__file__).parent / "assets" / "fonts"
    assert (folder / "Inter-LICENSE.txt").is_file()
    assert sum((folder / f).stat().st_size for f in W.FONT_FILES) < 400_000      # Latin subset


def test_report_html_follows_the_rules() -> None:
    from treeaicoach import report
    from treeaicoach.analysis import analyze_game

    rec = json.loads((ROOT / "tests" / "fixtures" / "game_record_sample.json").read_text(encoding="utf-8"))
    page = report.render_report_html(rec, analyze_game(rec))
    assert EM_DASH not in page
    up = page.upper()
    assert not [c for c in BANNED_COLORS if c in up]
    assert "radial-gradient" not in page and "box-shadow" not in page and "backdrop-filter" not in page
    radii = re.findall(r"border-radius:(\d+)px", page)
    assert radii and all(int(r) <= 6 for r in radii), radii
    assert "999px" not in page
    assert not _EMOJI.search(page)
    # the plain-French 3-line summary and the key moments are there
    assert page.count('class="tldr"') == 1 and "Moments clés" in page
