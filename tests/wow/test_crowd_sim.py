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
# whole-phase budgets, kinstr of TreeAI work per second (50 = 1 ms / s) = measured in 5.0 + ~15 %. The 4.3.6 code on
# the same simulation (profile « sur »): login 530, ville 513, hub 178, bg 145, butin 196, quetes 300, and images over
# 4 ms in every phase but bg (worst 7,7 ms).
# 5.0 INTEG (all five branches merged): « butin » re-measured — the phase opens a merchant and loots equipment, where
# the merged build now does real work that STABLE alone did not have: Gear's first plan (≈ 470 QuestieDB reads charged
# at 12-30 µs each, sliced by T:Spread) and the inventory verdicts for the merchant card (6 items per image). Measured:
# sur 101, tout 116 kinstr / s (STABLE alone: 84 / 104), worst image 76 / 85 kinstr (1.5 / 1.7 ms). Budget = measure
# + ~12 %. « login » sur measures 417 (budget unchanged). Every other phase is at or under its STABLE budget.
PER_SECOND = {"sur": {"login": 430, "ville": 150, "hub": 165, "bg": 100, "butin": 113, "quetes": 213, "repos": 13},
              "tout": {"login": 515, "ville": 220, "hub": 210, "bg": 325, "butin": 130, "quetes": 237, "repos": 16}}

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
