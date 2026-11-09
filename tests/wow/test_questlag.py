"""V5H QUESTLAG: accepting a quest, (un)tracking, a mob kill (objective +1) and a filled objective never cost a frame
more than 8 ms of TreeAI work (harness, real CPU clock fed to the core scheduler); the « reste à tuer » nameplate count
updates right after each kill (< 1 ms), disappears when the objective is done; quest events coalesced."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("profile", ["sur", "tout"])
def test_questlag(profile):
    env = dict(os.environ, TREEAI_REALCLOCK="1")
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--no-storm", "--flavor", "forever",
                        "--class", "HUNTER", "--level", "30", "--profile", profile,
                        "--scenario", str(ROOT / "tests/wow/questlag_scenario.lua"), str(ROOT / "wow_addon/TreeAI")],
                       capture_output=True, text=True, timeout=300, cwd=ROOT, env=env)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-6000:] + r.stderr[-2000:]
    out = r.stdout
    for tag in ("accepter une quête", "suivre / ne plus suivre", "objectif +1", "objectif rempli",
                "monstre tué (reste 1)", "objectif des loups rempli"):
        assert f"QUESTLAG {tag} :" in out, tag
    assert "QUESTLAG barre de nom : mise à jour" in out


def test_core_coalesces_quest_bursts():
    core = (ROOT / "wow_addon/TreeAI/Core.lua").read_text(encoding="utf-8")
    assert "TreeAI.QUEST_SETTLE = 1.5" in core and "function TreeAI:ExpectQuestWatch(q, added)" in core
    zw = (ROOT / "wow_addon/TreeAI/Modules/ZoneWatch.lua").read_text(encoding="utf-8")
    assert zw.count("T:ExpectQuestWatch(q,") == 2
