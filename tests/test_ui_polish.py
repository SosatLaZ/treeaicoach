"""UI polish: font fallback chain, AI rows / key test, game display mode, précision column data,
overlay page preview composition, guided first run. Pure parts run everywhere; Tk parts need a display."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import test_ui as tu

from treeaicoach import report, ui_kit, ui_preview
from treeaicoach import ui_common as ui
from treeaicoach.config import Config

home = tu.home
FIXTURE = Path(__file__).parent / "fixtures" / "game_record_sample.json"

WINDOWS_FAMILIES = ["@Malgun Gothic", "Arial", "Bahnschrift", "Bahnschrift Condensed", "Bahnschrift Light",
                    "Bahnschrift SemiBold", "Bahnschrift SemiBold Condensed", "Bahnschrift SemiLight",
                    "Segoe UI", "Segoe UI Semibold", "Segoe UI Variable Display Semib", "Tahoma"]


def test_ai_rows_and_key_test() -> None:
    assert ui_kit.ai_row("off")[2] == -1 and ui_kit.ai_row("off")[5] == "settings_ai"
    row = ui_kit.ai_row("gemini", key_set=False)
    assert row[2] == 1 and "clé manquante" in row[3]
    row = ui_kit.ai_row("groq", key_set=True, budget="IA 3/5")
    assert row[2] == 0 and row[3] == "Groq · IA 3/5" and row[4] == "Tester la clé" and row[5] == "test_ai"
    assert ui_kit.ai_row("groq", True, test=(False, "clé refusée"))[2] == 2
    assert ui_kit.ai_row("ollama")[2] == 0          # local: no key needed

    from treeaicoach import ai_advisor

    seen = {}

    def ok_caller(prov, key, model, system, prompt, timeout=0, max_tokens=0, **_kw):
        seen.update(prov=prov, key=key, max_tokens=max_tokens)
        return "OK"

    cfg = Config(ai_provider="groq", ai_api_key="k")
    ok, short, msg = ui_kit.test_ai_key(cfg, caller=ok_caller)
    assert ok and short == "clé OK" and "Groq" in msg and seen["max_tokens"] <= 32

    def bad(*_a, **_k):
        raise ai_advisor.AIError("key")

    ok, short, msg = ui_kit.test_ai_key(cfg, caller=bad)
    assert not ok and short == "clé refusée" and "Groq" in msg

    def empty(*_a, **_k):
        raise ai_advisor.AIError("empty")

    assert ui_kit.test_ai_key(cfg, caller=empty)[0]          # empty answer = the key was accepted
    assert not ui_kit.test_ai_key(Config(ai_provider="gemini", ai_api_key=""), caller=ok_caller)[0]
    assert not ui_kit.test_ai_key(Config(), caller=ok_caller)[0]


def test_window_mode_status_and_detector_short() -> None:
    assert ui_kit.window_mode_status(2)[0] == 0 and ui_kit.window_mode_status(1)[0] == 0
    assert ui_kit.window_mode_status(0)[0] == 2 and ui_kit.window_mode_status(None)[0] == -1
    assert ui.detector_short("roster+onnx") == "ONNX" and ui.detector_short("classic") == "Classique"
    assert ui.detector_short("") == "-"
    assert [s[0] for s in ui_kit.onboarding_steps()] == ["Ton niveau", "Jeu en « Sans bordure »",
                                                          "Teste l'overlay et la voix"]


def test_list_games_precision_and_non_records(tmp_path: Path) -> None:
    from treeaicoach import plays

    rec = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rated = dict(rec, plays=plays.summarize([{"cls": "brilliant", "rule": "r", "reason": "x", "gt": 10.0},
                                             {"cls": "blunder", "rule": "r", "reason": "y", "gt": 20.0}]))
    (tmp_path / "2026-09-20_2100_Garen.json").write_text(json.dumps(rated, separators=(",", ":")), encoding="utf-8")
    shutil.copy(FIXTURE, tmp_path / "2026-09-19_2100_Garen.json")
    (tmp_path / "progress_cache.json").write_text(json.dumps({"version": 3, "games": {}}), encoding="utf-8")
    games = report.list_games(10, tmp_path)
    assert len(games) == 2                                  # the progress cache is not a game
    by = {Path(g["path"]).name: g for g in games}
    assert by["2026-09-20_2100_Garen.json"]["precision"] == rated["plays"]["precision"]
    assert by["2026-09-19_2100_Garen.json"]["precision"] is None
    assert report.read_precision(tmp_path / "missing.json") is None
    st = ui.session_stats(games)
    assert st["precision"] == rated["plays"]["precision"]
    assert ui.precision_color(90) == ui.SAFE and ui.precision_color(40) == ui.WARNING
    assert ui.precision_color(None) == ui.DIM


def test_overlay_preview_composition() -> None:
    cfg = Config()
    comp = ui_preview.compose(cfg)
    assert comp.screen.shape == (1080, 1920, 3) and not comp.live
    for key in ("minimap", "hud", "badge"):
        assert key in comp.rects, key
    x, y, w, h = comp.rects["badge"]
    assert y < 540                                          # top centre by default
    near = ui_preview.compose(Config(plays_position="minimap"))
    assert near.rects["badge"][1] > 540
    imgs = ui_preview.preview_images(comp, screen_w=400)
    assert imgs["screen"].size[0] == 400 and {"hud", "minimap", "badge"} <= set(imgs)
    off = ui_preview.compose(Config(overlay_enabled=False))
    assert set(off.rects) == {"screen", "minimap"}
    assert "badge" not in ui_preview.compose(Config(plays_enabled=False)).rects
    radar = ui_preview.compose(Config(overlay_mode="radar"))
    assert "radar" in radar.rects


