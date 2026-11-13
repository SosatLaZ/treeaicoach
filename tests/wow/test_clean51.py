"""5.1 CLEAN — what the independent audit (AUDIT5) called botched, dangerous or dead, each with its reproduction.

B15 / B13 commands (tests/wow/slash51_scenario.lua): a bare switch command shows the state and never toggles, a command
whose module is off says what to enable (never « ACTIVÉ »), no documented command flips a switch when run bare, one name
per switch (« Profil : Simple / Complet », « Conseils »), duplicates out of the help, one shell.
Every scenario here runs in the DEFAULT module profile (« sur ») on the faithful Forever mock."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
CORE = (ADDON / "Core.lua").read_text(encoding="utf-8")
HARNESS = ROOT / "tests" / "wow" / "harness.lua"

needs_lua = pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")


def run_scenario(name, *extra, flavor="forever", cls="HUNTER", level="40", timeout=300):
    r = subprocess.run(["lua5.1", str(HARNESS), "-q", "--no-storm", "--profile", "sur", "--flavor", flavor,
                        "--class", cls, "--level", level, *extra,
                        "--scenario", str(ROOT / "tests" / "wow" / name), str(ADDON)],
                       capture_output=True, text=True, timeout=timeout, cwd=ROOT)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-5000:] + r.stderr[-2000:]
    return r.stdout


# ------------------------------------------------------------------------------------------------ B15 / B13 commands
def test_bare_toggle_pattern_is_gone_from_the_owned_files():
    """« rest == "" and not X » (a bare command switching something on) no longer exists in Core / QoL / PvP."""
    for name in ("Core.lua", "Modules/QoL.lua", "Modules/PvP.lua"):
        src = re.sub(r"--[^\n]*", "", (ADDON / name).read_text(encoding="utf-8"))
        assert not re.search(r'rest == ""\s+and not ', src), name
    qol = re.sub(r"--[^\n]*", "", (ADDON / "Modules" / "QoL.lua").read_text(encoding="utf-8"))
    assert "T:SlashSwitch(" in qol and "ACTIVÉ" not in qol
    # the switches of the core go through the one contract
    for cmd in ("pvp", "verrou", "debug", "guide"):
        assert re.search(r'RegisterSlash\("%s", TreeAI:SlashSwitch\(' % cmd, CORE), cmd
    # the old « test » command of the core (overwritten at load since 3.9) is gone, « profil » is registered once
    assert CORE.count('RegisterSlash("profil"') == 1 and 'RegisterSlash("test"' not in CORE
    assert '"Mode sûr", essentiel' not in CORE


@needs_lua
@pytest.mark.parametrize("flavor", ["forever", "modern", "legacy"])
def test_slash51_scenario(flavor):
    run_scenario("slash51_scenario.lua", flavor=flavor)


# ------------------------------------------------------------------------------------------------ B5 default profile
@needs_lua
@pytest.mark.parametrize("flavor", ["forever", "modern"])
def test_default51_scenario(flavor):
    """Default profile: Hunter / Talents / PathLearn on, Stats left out, removed modules gone, Hunter cheap at rest
    (events only for switched-on helpers), dead pet → « Ressuscite » even once the corpse is gone; B9 / B14 below."""
    run_scenario("default51_scenario.lua", flavor=flavor)


@needs_lua
def test_migrate51_scenario():
    """A 5.0 player: the new default modules switched on once, never what he switched off; removed modules' settings
    wiped once (tests/wow/fixtures/sv50_default_sv.lua)."""
    run_scenario("migrate51_scenario.lua", "--savedvars", str(ROOT / "tests" / "wow" / "fixtures" / "sv50_default_sv.lua"))


def test_default_profile_lists():
    m = re.search(r"TreeAI\.SAFE_MODULES = TreeAI\.SAFE_MODULES or \{(.*?)\n\}", CORE, re.S)
    body = re.sub(r"--[^\n]*", "", m.group(1))
    for name in ("Hunter", "Talents", "PathLearn"):
        assert re.search(r"\b%s = true" % name, body), name
    assert not re.search(r"\bStats = true", body), "Stats measured over the budgets: not in the default"
    assert re.search(r"Hunter = 5, Talents = 5, PathLearn = 5", CORE) and "TreeAI.SAFE_V = 5" in CORE
    toc = (ADDON / "TreeAI.toc").read_text(encoding="utf-8")
    for gone in ("Watch", "Horizon", "Radar", "QuickFight", "Quick", "MinimapPins", "Region"):
        assert "Modules\\%s.lua" % gone not in toc and not (ADDON / "Modules" / (gone + ".lua")).exists(), gone
        assert '"%s"' % gone in CORE.split("TreeAI.GONE_MODULES = ")[1].split("\n")[0]


# ------------------------------------------------------------------------------------------------ B9 swallowed errors
# (5.1 INTEG) the merged branches' files (route6 / look6 / bags6 / ah5) now report too; left: files no branch of this
# round owned, and the two that probe the game's API on purpose and report it themselves
OWNED_BY_OTHERS = {"Inventory", "Hub", "Quests", "Highlight",
                   "Bugs", "SelfTest"}


def test_wrappers_report_through_the_central_counter():
    """Every `try` / `pass` / `tryPass` wrapper of the files this round may edit reports a failure to T.Swallow."""
    missing = []
    for p in sorted((ADDON / "Modules").glob("*.lua")):
        if p.stem in OWNED_BY_OTHERS:
            continue
        src = p.read_text(encoding="utf-8")
        if not re.search(r"^local function (try|pass|tryPass)\(", src, re.M):
            continue
        if "TreeAI.Swallow" not in src:
            missing.append(p.stem)
        assert "if ok then return ... end return nil end" not in src, p.stem
    assert not missing, missing
    assert 'return TreeAI.Swallow(a, "Core")' in CORE


def test_error_report_sections():
    """TreeAI-erreurs.txt shows the swallowed errors (place, count, first message) and the real time per module."""
    import sys
    sys.path.insert(0, str(ROOT))
    from treeaicoach.wow import errlog
    db = {"diag": {
        "swallowed": {"total": 7, "dropped": 1, "sites": {
            "Bags.lua:120": {"n": 6, "msg": "Modules\\Bags.lua:120: attempt to index a nil value", "t": 1700000000,
                             "ctx": "Bags:sacs", "combat": True},
            "Core · GetItemInfo": {"n": 1, "msg": "bad argument #1 to 'GetItemInfo'", "t": 1700000000}}},
        "perf": {"t": 1700000000, "ver": "5.1.0", "up": 3600, "mods": [
            {"m": "Core", "ms": 1200.0, "n": 50000}, {"m": "Hunter", "ms": 30.0, "n": 900}]},
    }}
    text = errlog.build_text(db, now=1700000000)
    assert "Erreurs avalées (aucun message à l'écran) : 7 en tout, 2 endroit(s)" in text
    assert "== Erreurs avalées (sans message à l'écran) : 7 en tout, 2 endroit(s) ==" in text
    assert "Bags.lua:120 ×6 (en combat) pendant Bags:sacs" in text
    assert "(+ 1 échec(s) à d'autres endroits" in text
    assert "== Temps réel par module (mesuré en jeu" in text
    assert "Core : 20.00 ms/min (1200 ms, 50000 appels, 0.024 ms par appel)" in text
    # an old save without the new tables: no empty sections
    plain = errlog.build_text({"diag": {"errors": []}}, now=1700000000)
    assert "avalées" not in plain and "Temps réel" not in plain


# ------------------------------------------------------------------------------------------------ TUTO_FR.md (5.x)
def test_tuto_cites_only_what_exists():
    """Every button / page the tutorial names exists in the code, nothing of the 4.0 tutorial is left (the commands:
    test_tuto_commands_registered)."""
    tuto = (ROOT / "wow_addon" / "TUTO_FR.md").read_text(encoding="utf-8")
    cmds = sorted(set(re.findall(r"`/tai ([a-z]+)", tuto)))
    assert len(cmds) >= 15, cmds
    src = "\n".join(p.read_text(encoding="utf-8") for p in sorted((ADDON / "Modules").glob("*.lua"))) + CORE
    for label in ("Guidage quêtes", "Rendre", "Prendre", "Suivant", "Zone", "Vite", "Sacs", "Équipement", "Métiers",
                  "Groupe", "Tutoriel : quoi poser, sur quelle touche", 'label = "Fonctions"', 'label = "Macros"',
                  "Mes 5 macros essentielles"):
        assert label in src, label
    for stale in ("Radar", "Fonctions bêta", "mode sûr", "Fenêtre « À faire »", "Maintenant, Quêtes, Trajet"):
        assert stale not in tuto, stale
    assert "mesurés en\nsimulation" in tuto or "mesurés en simulation" in tuto


@needs_lua
def test_tuto_commands_registered():
    """Every « /tai x » the tutorial cites is a registered command in the default profile (tuto51_scenario.lua)."""
    tuto = (ROOT / "wow_addon" / "TUTO_FR.md").read_text(encoding="utf-8")
    cmds = sorted(set(re.findall(r"`/tai ([a-z]+)", tuto)))
    import os
    env = dict(os.environ, TREEAI_TUTO_CMDS=" ".join(cmds))
    r = subprocess.run(["lua5.1", str(HARNESS), "-q", "--no-storm", "--profile", "sur", "--flavor", "forever",
                        "--class", "HUNTER", "--level", "40", "--scenario", str(ROOT / "tests" / "wow" / "tuto51_scenario.lua"),
                        str(ADDON)], capture_output=True, text=True, timeout=300, cwd=ROOT, env=env)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-4000:]


# ------------------------------------------------------------------------------------------------ B14 honesty
def test_changelog_ms_claims_say_simulation():
    """Every millisecond figure the changelog gives for the addon (4.x / 5.x entries) says it was measured in
    simulation; the real figures come from the player's own report (diag.perf, diag.spikes)."""
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    addon = log[:log.index("## 4.3.0")] if "## 4.3.0" in log else log[:4000]
    for para in re.split(r"\n(?=- |## )", addon):
        if re.search(r"\d ms\b", para):
            assert "simulation" in para, para
    core = re.sub(r"--[^\n]*", "", CORE)
    assert "function TreeAI:_SavePerf()" in core and "safecall(self._SavePerf, self)" in core


def test_minimap_geometry_kept_for_its_users():
    """MinimapPins.lua was removed, but its minimap geometry served the quest icons, the dotted path and the city
    services on the minimap: it lives on in MinimapGeo.lua under the same name (T.MinimapPins), no module, no event."""
    geo = (ADDON / "Modules" / "MinimapGeo.lua").read_text(encoding="utf-8")
    assert geo.splitlines()[0] == "local tonumber = function(v) return _G.tonumber(v) end   -- (4.0.8) tonumber(f()) : un 2e retour de f n'est jamais une base"
    for fn in ("function MP.HBD()", "function MP.YardsRadius(", "function MP.Offset(", "function MP.View()"):
        assert fn in geo, fn
    assert "T.MinimapPins = MP" in geo and "RegisterModule" not in geo and "RegisterEvent" not in geo
    toc = [ln.strip() for ln in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert toc.index("Modules\\MinimapGeo.lua") < toc.index("Modules\\QuestPins.lua")
    assert toc.index("Modules\\MinimapGeo.lua") < toc.index("Modules\\CityRun.lua")
    for user in ("QuestPins.lua", "PathDraw.lua", "CityRun.lua"):
        assert "T.MinimapPins" in (ADDON / "Modules" / user).read_text(encoding="utf-8"), user
    # the « TAI Pro Quete » macro keeps its target without Quick.lua
    mac = (ADDON / "Modules" / "Macros.lua").read_text(encoding="utf-8")
    assert "function M.NextQuestTarget()" in mac and "T.Quick" not in re.sub(r"--[^\n]*", "", mac)
