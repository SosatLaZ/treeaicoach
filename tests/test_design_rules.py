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
UI_FILES = ("ui.py", "ui_common.py", "ui_dialogs.py", "ui_page_alerts.py", "ui_page_analysis.py",
            "ui_page_dashboard.py", "ui_page_overlay.py", "ui_page_settings.py", "ui_kit.py", "report.py",
            "replay.py", "progress.py")
#: the CustomTkinter window, split over several modules (ui.py + ui_common.py + one module per page)
APP_FILES = tuple(f for f in UI_FILES if f.startswith("ui") and f != "ui_kit.py")


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
    from treeaicoach import ui

    for fam in ui.BODY_FONTS + ui.DISPLAY_FONTS:
        assert not any(fam == b or fam.startswith(b + " ") for b in BANNED_FONTS), fam
    css = (PKG / "report.py").read_text(encoding="utf-8")
    for fam in BANNED_FONTS:
        assert not re.search(rf"font-family:[^;}}]*\b{fam}\b", css), fam


def test_ui_radius_is_small() -> None:
    src = _app_source()
    radii = [int(x) for x in re.findall(r"corner_radius=(\d+)", src)]
    assert all(r <= 6 for r in radii), radii
    from treeaicoach import ui

    assert ui.RADIUS <= 6 and ui.RADIUS_DIALOG <= 6
    # the one pill shape allowed: toggles (docs/DESIGN.md), and only them
    assert ui.TOGGLE_RADIUS == ui.TOGGLE_H // 2
    assert "corner_radius=TOGGLE_RADIUS" not in src          # drawn by ui.toggle_image, not a CTk shape


def _contrast(a: str, b: str) -> float:
    def lum(h: str) -> float:
        c = [int(h.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_text_is_legible() -> None:
    """Readable sizes (nothing under 12 px) and WCAG AA contrast for every text colour."""
    from treeaicoach import ui

    for bg in (ui.BG, ui.SURFACE, ui.RAISED):
        for fg in (ui.TEXT, ui.MUTED, ui.DIM, ui.ACCENT):
            assert _contrast(fg, bg) >= 4.5, (fg, bg, round(_contrast(fg, bg), 2))
    src = _app_source()
    m = re.search(r"class _Fonts:.*?(?=\n\n\n)", src, re.S)
    assert m
    sizes = [int(x) for x in re.findall(r"size=(\d+)", m.group(0))]
    assert sizes and min(sizes) >= 12, sizes


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
