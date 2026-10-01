"""game_settings.py: League's config files (game.cfg / PersistedSettings.json) as detection priors."""

from __future__ import annotations

import shutil
from pathlib import Path

import cv2

from treeaicoach import game_settings as GS
from treeaicoach.capture import Rect
from treeaicoach.minimap_locator import MinimapLocator

FIX = Path(__file__).parent / "fixtures"
CFG_DIR = FIX / "lol_config"


def test_parse_game_cfg_only(tmp_path):
    shutil.copy(CFG_DIR / "game.cfg", tmp_path / "game.cfg")
    gs = GS.load_game_settings([tmp_path])
    assert gs is not None and gs.source == str(tmp_path)
    assert (gs.width, gs.height, gs.window_mode) == (1920, 1080, 2)
    assert gs.minimap_scale == 1.0 and gs.global_scale == 0.5
    assert gs.flip_minimap is False and gs.minimap_side() == "right"
    assert gs.colorblind is False and gs.relative_team_colors is True
    assert not gs.exclusive_fullscreen
    assert gs.fingerprint() == "1920x1080|w2|m1.000|g0.500|f0"


def test_persisted_settings_win_and_game_cfg_fills_gaps():
    gs = GS.load_game_settings([CFG_DIR, Path("/nonexistent")])
    assert (gs.width, gs.height) == (2560, 1440)
    assert gs.minimap_scale == 1.4 and gs.flip_minimap is True and gs.minimap_side() == "left"
    assert gs.colorblind is True
    assert gs.global_scale == 0.5                 # only in game.cfg


def test_garbage_never_raises(tmp_path):
    (tmp_path / "game.cfg").write_bytes(b"\xff\xfe[HUD\nMinimapScale=abc\nWidth=-5\n=\n")
    (tmp_path / "PersistedSettings.json").write_text("{not json", encoding="utf-8")
    gs = GS.load_game_settings([tmp_path])
    assert gs is not None and gs.minimap_scale is None and gs.width is None
    assert gs.fingerprint() == "" and gs.minimap_side() is None
    assert GS.load_game_settings([tmp_path / "missing"]) is None
    assert GS.parse_game_cfg(None) == {} and GS.parse_persisted(12) == {}
    assert GS.settings_from_sections({"hud": {"minimapscale": "inf"}}).minimap_scale is None


def test_watcher_reloads_on_change(tmp_path):
    shutil.copy(CFG_DIR / "game.cfg", tmp_path / "game.cfg")
    clock = [0.0]
    w = GS.SettingsWatcher([tmp_path], clock=lambda: clock[0])
    assert w.get().flip_minimap is False
    text = (tmp_path / "game.cfg").read_text().replace("FlipMiniMap=0", "FlipMiniMap=1")
    (tmp_path / "game.cfg").write_text(text + "\n")
    assert w.get().flip_minimap is False          # not re-checked yet
    clock[0] += GS.RELOAD_S + 1
    assert w.get().flip_minimap is True


def test_rect_cache_and_locator_hint(tmp_path):
    cache = GS.RectCache(tmp_path / "mm.json")
    gs = GS.settings_from_sections(GS.parse_game_cfg((CFG_DIR / "game.cfg").read_text()))
    key = cache.key(800, 600, gs)
    assert key.startswith("800x600|") and cache.get(key) is None
    cache.put(key, 1, 2, 300, 300, 0.8)
    cache.put(key, 1, 2, 300, 300, 0.7)           # same rectangle: best score kept
    assert GS.RectCache(tmp_path / "mm.json").get(key) == (1, 2, 300, 300, 0.8)
    assert cache.key(800, 600, None) == "800x600"

    # the locator reuses a still-valid remembered rectangle, rejects a wrong one
    mm = cv2.imread(str(FIX / "real_minimap_306.png"))
    screen = cv2.copyMakeBorder(mm, 500, 12, 900, 14, cv2.BORDER_CONSTANT, value=(20, 20, 20))
    win = Rect(100, 50, screen.shape[1], screen.shape[0])
    loc = MinimapLocator()
    hit = loc.locate(screen, win, hint=(900, 500, 306, 306, 0.8))
    assert hit is not None and hit.rect == Rect(1000, 550, 306, 306) and loc.last_timing.get("hint")
    assert loc.locate(screen, win, side="left", hint=(900, 500, 306, 306, 0.8)) is None or \
        not loc.last_timing.get("hint")
    miss = loc.locate(screen, win, hint=(10, 10, 306, 306, 0.8))     # stale: full search
    assert miss is not None and abs(miss.rect.x - 1000) <= 3 and abs(miss.rect.y - 550) <= 3
    assert not loc.last_timing.get("hint")
    assert loc.locate(screen, win, hint=("bad",)) is not None
