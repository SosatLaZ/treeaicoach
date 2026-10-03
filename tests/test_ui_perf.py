"""UI v3 (legibility + performance): scale factor, toggle drawing, lazy pages, light controls,
quiet timers. Pure parts run everywhere; Tk parts need a display."""
from __future__ import annotations

import time
from pathlib import Path

import test_ui as tu

from treeaicoach import ui_common as ui
from treeaicoach.config import Config

home = tu.home


def test_ui_scale_of() -> None:
    assert ui.ui_scale_of(Config()) >= 1.0                     # never the old compact 0.88
    assert ui.ui_scale_of(type("C", (), {"ui_scale": 0.88, "ui_scaling": "auto"})()) == 1.0
    assert ui.ui_scale_of(type("C", (), {"ui_scale": 1.2, "ui_scaling": "auto"})()) == 1.2
    assert ui.ui_scale_of(type("C", (), {"ui_scale": 1.0, "ui_scaling": "125"})()) == 1.25
    assert ui.ui_scale_of(type("C", (), {"ui_scale": "x", "ui_scaling": "?"})()) == 1.0
    assert ui.ui_scale_of(type("C", (), {"ui_scale": 9.0})()) == 1.4


def test_health_text_and_new_hooks() -> None:
    h = {"capture_backend": "dxcam", "capture_fps": 29.6, "detect_ms": {"p50": 11.2, "p95": 24.0},
         "overlay": {"fps": 60.0}, "champions_seen": 9, "champions_expected": 10, "cpu_percent": 7.5}
    text, level = ui.health_text(h)
    assert "dxcam" in text and "11/24 ms" in text and "9/10" in text and "CPU 8 %" in text and level == 0
    assert ui.health_text({**h, "cpu_percent": 90.0, "capture_note": "capture lente"})[1] == 1
    assert ui.health_text(None) == ("", 0) and ui.health_text({"detect_ms": "x"})[0] == ""
    assert ui.HUD_POSITIONS[0][0] == "left_of_minimap"          # the new default first


def test_default_detector_factory_passes_the_engine_options(monkeypatch) -> None:
    from treeaicoach import detector

    got: dict = {}

    def fake(backend, threshold, **kw):
        got.update(kw, backend=backend)
        return "det"

    monkeypatch.setattr(detector, "create_detector", fake)
    cfg = Config()
    from treeaicoach import ui as uimod

    assert uimod._default_detector_factory(cfg, learn=False) == "det"
    assert got["learn_cache"] is False and "db" in got and isinstance(got["scale_store"], dict)
    uimod._default_detector_factory(cfg)
    assert got["learn_cache"] is True




