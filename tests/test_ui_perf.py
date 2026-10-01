"""UI v3 (legibility + performance): scale factor, toggle drawing, lazy pages, light controls,
quiet timers. Pure parts run everywhere; Tk parts need a display."""
from __future__ import annotations

import time
from pathlib import Path

import test_ui as tu

from treeaicoach import ui
from treeaicoach.config import Config

home = tu.home


def test_ui_scale_of() -> None:
    assert ui.ui_scale_of(Config()) >= 1.0                     # never the old compact 0.88
    assert ui.ui_scale_of(type("C", (), {"ui_scale": 0.88, "ui_scaling": "auto"})()) == 1.0
    assert ui.ui_scale_of(type("C", (), {"ui_scale": 1.2, "ui_scaling": "auto"})()) == 1.2
    assert ui.ui_scale_of(type("C", (), {"ui_scale": 1.0, "ui_scaling": "125"})()) == 1.25
    assert ui.ui_scale_of(type("C", (), {"ui_scale": "x", "ui_scaling": "?"})()) == 1.0
    assert ui.ui_scale_of(type("C", (), {"ui_scale": 9.0})()) == 1.4


def test_toggle_image_reads_on_off() -> None:
    on = ui.toggle_image(46, 26, True, ui.ACCENT, ui.SURFACE)
    off = ui.toggle_image(46, 26, False, ui.ACCENT, ui.SURFACE)
    assert on.size == off.size == (46, 26)
    # on: green track on the left, dark knob on the right; off: no green anywhere
    r, g, b = on.getpixel((10, 13))[:3]
    assert g > 180 and r < 190
    assert sum(on.getpixel((33, 13))[:3]) < 120
    pixels = [off.getpixel((x, y))[:3] for x in range(46) for y in range(26)]
    assert all(px[1] - px[0] < 30 for px in pixels)          # nothing green when off
    assert ui.toggle_image(46, 26, True, ui.ACCENT, ui.SURFACE) is on        # cached
    dis = ui.toggle_image(46, 26, True, ui.ACCENT, ui.SURFACE, disabled=True)
    assert dis.getpixel((10, 13))[1] < on.getpixel((10, 13))[1]


def test_page_attribute_index() -> None:
    idx = ui._page_attr_index()
    assert idx["hero"] == "dashboard" and idx["journal"] == "dashboard"
    assert idx["_examples"] == "alerts" and idx["voice_menu"] == "alerts"
    assert idx["_update_btn"] == "settings" and idx["_ai_key_entry"] == "settings"
    assert idx["_replay"] == "analysis" and idx["overlay_preview"] == "overlay"
    assert "_closing" not in idx and "cfg" not in idx


@tu.needs_display
def test_lazy_pages_and_quiet_timers(home: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ui, "PREBUILD_DELAY_MS", 0)
    app, voice, (engines, _ov) = tu._build(tmp_path)
    try:
        assert app._built == {"dashboard"}                     # only the visible page at start-up
        assert "settings" in app.pages and dict.get(app.pages, "settings") is None
        tu._pump(app, 1.0)
        # a page widget read before its page exists builds that page only
        assert app._update_check_btn is not None
        assert app._built == {"dashboard", "settings"}
        t0 = time.perf_counter()
        app.show_page("help")
        assert "help" in app._built and app._current_page == "help"
        app.root.update()
        t1 = time.perf_counter()
        app.show_page("settings")
        app.root.update()
        assert time.perf_counter() - t1 < max(0.5, t1 - t0)   # a built page switches fast
        # dashboard hidden: the status loop slows down and does not touch its widgets
        tu._pump(app, 0.3)
        assert not app._dash_live()
        before = app.clock_lbl.cget("text")
        tu._pump(app, 1.2)
        assert app.clock_lbl.cget("text") == before
        app.show_page("dashboard")
        tu._pump(app, 1.5, lambda: app.clock_lbl.cget("text") != before)
        app.build_all_pages()
        assert app._built == set(app._page_builders)
    finally:
        app.close()


@tu.needs_display
def test_light_controls(home: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ui, "PREBUILD_DELAY_MS", 0)
    app, voice, _ = tu._build(tmp_path)
    try:
        calls: list = []
        var = app.ctk.BooleanVar(value=False)
        t = app._toggle(app.content, var, lambda: calls.append(var.get()))
        t.grid(row=5, column=0)
        app.root.update()
        t._click()
        assert var.get() is True and calls == [True]
        t.configure(state="disabled")
        t._click()
        assert var.get() is True and calls == [True]
        var.set(False)                                         # variable -> image follows
        assert t._sig[2] is False

        picked: list = []
        d = ui.Dropdown(app, app.content, ["A", "B"], picked.append)
        d.set("B")
        assert d.get() == "B"
        d._pick("A")
        assert picked == ["A"] and d.get() == "A"
        d.configure(values=["C"], state="disabled")
        assert d.cget("values") == ["C"] and d.cget("state") == "disabled"

        seg = ui.Segmented(app, app.content, ["  x  ", "  y  "], picked.append)
        seg._click("  y  ")
        assert seg.get() == "  y  " and picked[-1] == "  y  "
        seg.set("")
        assert seg.get() == ""
        # settings rows are built with the light controls and stay in sync with the config
        app.show_page("settings")
        app.set_option("minimap_side", "left")
        app._refresh_all_widgets()
    finally:
        app.close()
