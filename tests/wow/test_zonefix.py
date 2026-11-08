"""ZONEFIX (4.0.1): regression tests for the first real-client screenshot of 4.0 (level-28 Horde hunter at Splintertree
Post, « mode sûr », zone « Orneval » chosen).

* tests/wow/zonefix_scenario.lua replays it on the faithful Forever mock and on the modern API, with and without
  QuestieDB, in « mode sûr » (what the player runs): the route stays in the chosen zone (no Barrens / Thousand Needles
  quest, no flight away, no dungeon quest), « Autres zones : … » is said instead, the compass aims at the card's stop 1
  (a quick win a few steps away IS stop 1), never hides for an unrelated Blizzard waypoint, keeps its target during a
  replan, stays in combat (arrow only), updates ≥ 5 Hz (text) / ≥ 20 Hz (arrow) while moving and does nothing at rest,
  one subtitle format (no « ≈ ≈ »), route modes, « Zones à faire », themes and the auto watch list (manual choices
  kept, combat deferred, one pass / 2 s);
* a player who got « mode sûr » in 3.9.9 / 4.0.0 (ZonePlan saved as off) gets the zone route back once; a player who
  turns it off afterwards keeps it off.
Fake character / quest ids, real map ids and bounds of the client."""
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
ALLOW = ["LibQuestieDB", "QuestDB", "NpcDB", "ObjectDB", "ItemDB", "TreeAIPlan", "IsQuestFlaggedCompleted"]


def _harness(flavor: str, scenario: Path, *extra: str, env: dict | None = None) -> subprocess.CompletedProcess:
    cmd = [LUA, str(HERE / "harness.lua"), "-q", "--flavor", flavor, "--class", "HUNTER"]
    for a in ALLOW:
        cmd += ["--allow", a]
    cmd += [*extra, "--scenario", str(scenario), str(ADDON)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=ROOT, check=False,
                          env={**os.environ, **(env or {})})


@pytest.mark.skipif(not LUA, reason="lua5.1 not installed")
@pytest.mark.parametrize("variant", ["full", "noquestie"])
@pytest.mark.parametrize("flavor", ["forever", "modern"])
def test_zonefix_replay(flavor: str, variant: str) -> None:
    r = _harness(flavor, HERE / "zonefix_scenario.lua", "--profile", "sur", env={"ZF_VARIANT": variant})
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-6000:] + r.stderr[-2000:]


@pytest.mark.skipif(not LUA, reason="lua5.1 not installed")
def test_zonefix_replay_legacy_client() -> None:
    """Classic-style client: watch list by log index (AddQuestWatch(index)), no UnitPosition."""
    r = _harness("legacy", HERE / "zonefix_scenario.lua", "--profile", "sur", env={"ZF_VARIANT": "full"})
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-6000:] + r.stderr[-2000:]


MIGRATION = """
return function(M, ctx)
  local T = TreeAI
  local Z = T.modules.ZonePlan
  ctx.check(T:ModuleProfile() == "sur", "the player's mode sûr is kept")
  M.Advance(2, 0.5)
  if os.getenv("ZF_EXPECT") == "on" then
    ctx.check(Z._active and Z.db.enabled == true and Z.db.safeV == 1, "ZonePlan (route by zone) turned back on, once")
    ctx.check(T.modules.Boost._active ~= true, "…and nothing else: the bêta modules stay off")
  else
    ctx.check(not Z._active and Z.db.enabled == false, "turned off by the player after 4.0.1: stays off")
  end
end
"""


def _saved(zoneplan: str, top: str = "") -> str:
    return ("TreeAIDB = { profileV = 2, moduleProfile = \"sur\", tour = { done = 999 }, onboarded = true," + top + "\n"
            "  modules = { ZonePlan = " + zoneplan + ", Boost = { enabled = false } } }\n")


# « off »: what a 4.0.1 player who switched the route off himself has saved (the core's own choice note — db.modChoice
# from 4.0.1 on — and its safe-set version are there too, so the case holds whichever 4.0.1 core reads it)
@pytest.mark.skipif(not LUA, reason="lua5.1 not installed")
@pytest.mark.parametrize("flavor", ["forever", "modern"])
@pytest.mark.parametrize("saved,top,expect", [
    ("{ enabled = false }", "", "on"),
    ("{ enabled = false, safeV = 1 }", " safeV = 1, modChoice = { ZonePlan = false },", "off"),
    ("{ enabled = false }", " modChoice = { ZonePlan = false },", "off"),
])
def test_zoneplan_back_on_for_mode_sur_players(flavor: str, saved: str, top: str, expect: str, tmp_path: Path) -> None:
    sv = tmp_path / "TreeAI.lua"
    sv.write_text(_saved(saved, top), encoding="utf-8")
    sc = tmp_path / "migration_scenario.lua"
    sc.write_text(MIGRATION, encoding="utf-8")
    r = _harness(flavor, sc, "--no-storm", "--savedvars", str(sv), env={"ZF_EXPECT": expect})
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-4000:] + r.stderr[-2000:]


def test_safe_set_has_the_zone_route() -> None:
    """The route by zone is part of the base guidance (mode sûr), declared next to its module."""
    src = (ADDON / "Modules" / "ZonePlan.lua").read_text(encoding="utf-8")
    assert "T.SAFE_MODULES.ZonePlan = true" in src
    toc = (ADDON / "TreeAI.toc").read_text(encoding="utf-8")
    assert toc.index("Modules\\ZonePlan.lua") < toc.index("Modules\\ZoneWatch.lua")


def test_errlog_writes_the_compass_journal() -> None:
    """« Trajet / boussole » in TreeAI-erreurs.txt: the ring the addon keeps by itself (TreeAIDB.diag.nav), newest
    first — even before any /tai erreurs."""
    import sys
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from treeaicoach.wow import errlog
    db = {"diag": {"nav": {"v": 1, "bytes": 120, "lines": {
        1: "08/11 15:00:01 cible · Ours → Prendre « Le brasier » (arrêt 1 du trajet)",
        2: "08/11 15:00:09 trajet · refait (zone) : 9 arrêts · 1er : Prendre « Le brasier » · zone Orneval (choisie)",
        3: "08/11 15:01:40 temps · trajet de 274 m : prévu 50 s, réel 1 min 00 (+20 %) · Orneval · à pied"}}}}
    txt = errlog.build_text(db, now=0)
    assert "== Trajet / boussole (3 évènements, les plus récents d'abord) ==" in txt
    assert txt.index("temps · trajet de 274 m") < txt.index("trajet · refait (zone)") < txt.index("cible · Ours")
    assert "Trajet / boussole" not in errlog.build_text({"diag": {}}, now=0)

