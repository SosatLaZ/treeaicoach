"""5.0 STABLE — the player's three real-game complaints as a simulation (tests/wow/session_sim.lua --crowd, phases in
tests/wow/crowd_phases.lua): « ça bugue quand il y a beaucoup de joueurs » (capital with 40 nameplates streaming, quest
hub, battleground-like fight in combat), « quand les quêtes s'actualisent » (quest-log bursts and noise), « quand je
loot » (auto-loot, AoE loot, vendor, craft, item-info storms), plus the login storm and a rest phase.

4 TreeAI clients in a group, the main one a level-30 hunter on the faithful WoW: Forever mock; one image = the events the
game sends in it + timers + OnUpdate; the cost of the game's own work behind widget calls and QuestieDB reads is added
(estimated µs, see session_sim.lua / crowd_phases.lua). Gates: no image over 4 ms of TreeAI work (200 kinstr at 50
kinstr / ms), nothing at rest, zero Lua error (swallowed ones too), zero blocked action, zero spike recorded by T.Spikes."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
ADDON = ROOT / "wow_addon" / "TreeAI"
PHASES = ("login", "ville", "hub", "bg", "butin", "quetes", "repos")
# whole-phase budgets, kinstr of TreeAI work per second (50 = 1 ms / s): what the 4.3.6 code measured on the same
# simulation was ville 513, quetes 300, butin 196, login 530, hub 178, bg 145
PER_SECOND = {"sur": {"login": 420, "ville": 150, "hub": 170, "bg": 110, "butin": 110, "quetes": 215, "repos": 22},
              "tout": {"login": 620, "ville": 260, "hub": 300, "bg": 230, "butin": 200, "quetes": 330, "repos": 40}}

pytestmark = pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 not installed")


@pytest.mark.parametrize("profile", ["sur", "tout"])
def test_crowd(profile):
    r = subprocess.run(["lua5.1", str(HERE / "session_sim.lua"), "--crowd", "--class", "HUNTER", "--flavor", "forever",
                        "--profile", profile, "--addon", str(ADDON)], cwd=ROOT, capture_output=True, text=True, timeout=900)
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "RESULT ok" in out, out[-7000:]
    seen = {}
    for m in re.finditer(r"^phase (\w+)\s+\d+ s\s+images\s+\d+\s+worst\s+(\d+) kinstr .*?over 4 ms: (\d+)\s+total\s+\d+ kinstr \((\d+) / s\)",
                         out, re.M):
        seen[m.group(1)] = (int(m.group(2)), int(m.group(3)), int(m.group(4)))
    assert set(seen) == set(PHASES), sorted(seen)
    for name, (worst, over, per_s) in seen.items():
        assert worst <= 200 and over == 0, f"{name}: worst image {worst} kinstr, {over} over 4 ms\n{out[:4000]}"
        assert per_s <= PER_SECOND[profile][name], f"{name}: {per_s} kinstr / s > {PER_SECOND[profile][name]}\n{out[:4000]}"
    assert re.search(r"spikes recorded by the addon: 0\b", out), out[:4000]
