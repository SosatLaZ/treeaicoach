"""5.1 ROUTE6 — « le trajet dit vrai » : the findings of the independent audit of 5.0.0 (AUDIT5, B1–B12), each reproduced as
a scenario that FAILS on 5.0.0 and passes now. Every scenario runs in the DEFAULT profile (« sur », what the player has)
and in « tout », on the faithful Forever mock, real-client geometry, and never forces a route rebuild: it moves the player,
waits, and reads what the addon shows by itself (audit C1: 13 older scenarios forced the rebuild 115 times).

  route6_walk_scenario     B1 + B2  the route is re-solved from where he is (walk, chosen zone, zone arrival, death) and
                                    new quest data reaches it without « Recalculer » ; no frame over 4 ms while walking
  route6_phantom_scenario  B4       absent giver → one question [Pas là] [Je reviens plus tard] ; never removed by a
                                    walk-past ; « Quêtes écartées » list with undo, remembered per character
  route6_buy_scenario      B8       « Achète X chez Y » in each Questie flavour (library alone / old addon / both)
  route6_now_scenario      B6 B7 B11 B12 + D  one answer to « que faire maintenant », no invented numbers, the game's
                                    watch list untouched by default, city help on screen, hearth / flight advice,
                                    « mauvais conseil »
  route6_cost_scenario     B3       travel cost: Shortest Path estimate (read-only), learned paths, zone factor — source
                                    labelled ; death-aware legs
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
ADDON = ROOT / "wow_addon" / "TreeAI"
LUA = shutil.which("lua5.1")

PROFILES = ["sur", "tout"]


def run(scenario: str, profile: str, flavor: str = "forever", env: dict | None = None, level: int = 29) -> str:
    cmd = [LUA, str(HERE / "harness.lua"), "-q", "--no-storm", "--geometry", "ready=1,w=1920,h=1080",
           "--profile", profile, "--flavor", flavor, "--class", "HUNTER", "--level", str(level),
           "--scenario", str(HERE / scenario), str(ADDON)]
    e = dict(os.environ)
    e.update(env or {})
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=ROOT, check=False, env=e)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-8000:] + r.stderr[-2000:]
    return r.stdout


@pytest.mark.skipif(not LUA, reason="lua5.1 not installed")
@pytest.mark.parametrize("profile", PROFILES)
def test_route_follows_the_player(profile):
    """B1 + B2: d_walk / a2_zone of the audit, without a forced rebuild."""
    run("route6_walk_scenario.lua", profile)


@pytest.mark.skipif(not LUA, reason="lua5.1 not installed")
@pytest.mark.parametrize("profile", PROFILES)
def test_phantom_quests_absent_giver(profile):
    """B4: b_pick / b2_pick of the audit."""
    run("route6_phantom_scenario.lua", profile)


@pytest.mark.skipif(not LUA, reason="lua5.1 not installed")
@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("questie", ["lib", "loader", "both"])
def test_buy_item_in_each_questie_flavour(profile, questie):
    """B8: e3_buy / e4_buy of the audit — real item / vendor rows, the real Route.VendorFor."""
    run("route6_buy_scenario.lua", profile, env={"R6_QUESTIE": questie})
