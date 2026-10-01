"""UI additions: build advice / caster style / AI section, AI test button, shareable summary."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import test_ui as tu

from treeaicoach import ui

home = tu.home


@tu.needs_display
def test_ai_section_and_share_summary(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = tmp_path / "g1.json"
    shutil.copy(Path(__file__).parent / "fixtures" / "game_record_sample.json", rec)
    games = [{"path": str(rec), "start": "2026-09-30T21:00:00", "champion": "Garen"}]
    real = ui._report_function
    monkeypatch.setattr(ui, "_report_function",
                        lambda name: {"list_games": lambda n=50: games}.get(name) or real(name))
    app, _voice, _ = tu._build(tmp_path)
    try:
        for key in ("alerts", "settings", "analysis"):
            app.show_page(key)
            tu._pump(app, 0.2)
        assert "item_advice_speak" in app._widgets_by_field and "caster_style" in app._widgets_by_field
        assert "ai_provider" in app._widgets_by_field and "ai_speak" in app._widgets_by_field
        assert "hotkey_ai" in app._widgets_by_field and app.cfg.hotkey_ai == "F8"
        app.ask_ai()                                   # fake engine without ask_ai: a toast, no crash
        tu._pump(app, 0.5)
        app._ai_key_entry.insert(0, "secret-key")
        app.test_ai()                                  # provider "off": explains what to do
        tu._pump(app, 3.0, lambda: "fournisseur" in app._ai_status.cget("text"))
        assert "fournisseur" in app._ai_status.cget("text")
        assert app.cfg.ai_api_key == "secret-key"
        app.set_option("caster_style", "caster")
        assert app.cfg.caster_style == "caster"
        app.root.clipboard_clear()
        app.copy_share_summary()
        tu._pump(app, 5.0, lambda: app.root.clipboard_get().startswith("TreeAI Coach"))
        assert app.root.clipboard_get().startswith("TreeAI Coach")
        assert "KDA" in app.root.clipboard_get()
        app.reset_settings()
        assert app.cfg.ai_api_key == "secret-key" and app.cfg.caster_style == "coach"
    finally:
        app.close()
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert saved.get("ai_provider") == "off"
