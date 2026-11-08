"""COHERE: one guide on screen when Shortest Path Forever / Questie are loaded (one arrow, no duplicate quest
lines in « Maintenant », no stale turn-in line after QUEST_TURNED_IN), pet training points never the raw total,
panel placed outside the SPF column / Questie tracker / chat. Fake names only."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = Path(os.environ.get("TREEAI_WOW_ADDON") or ROOT / "wow_addon" / "TreeAI")
LUA = shutil.which("lua5.1")
needs_lua = pytest.mark.skipif(not LUA, reason="lua5.1 not installed")


@needs_lua
@pytest.mark.parametrize("flavor", ["modern", "forever"])
def test_scenario(flavor):
    r = subprocess.run([LUA, "tests/wow/harness.lua", "-q", "--flavor", flavor, "--class", "HUNTER",
                        "--allow", "ShortestPathForever", "--allow", "QuestieLoader",
                        "--scenario", "tests/wow/cohere_scenario.lua", str(ADDON)],
                       cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout


def test_guide_option_and_ids():
    core = (ADDON / "Core.lua").read_text(encoding="utf-8")
    opts = (ADDON / "Options.lua").read_text(encoding="utf-8")
    assert 'guide = "auto"' in core and "function TreeAI:ExternalGuide()" in core
    for gid in ("Quests:near", "Quests:chain", "Compass:target"):
        assert f'["{gid}"]' in core
    assert "SetGuideMode" in opts and "Guidage" in opts
    assert "Suivi Forever (recommandé si installé)" in core
