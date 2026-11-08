"""« Sans autres addons » (agent REPLACE, 4.0.1): TreeAI's own quest icons on the world map and the minimap (QuestPins,
Questie's database read at run time, Questie silent), the « Suivi TreeAI » tracker that replaces the game's (Watch),
the « Mes addons » page (Questie / Shortest Path through their own options), the Leatrix-style comfort toggles (QoL) and
the « Dégâts » window on the game's own damage meter (Meter).
Static rules (taint, no copied Questie data, only documented option keys written in other addons, TOC, session caps) +
the harness scenario with a fake QuestieDB on modern and forever, including its perf measures (idle = 0)."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = Path(os.environ.get("TREEAI_WOW_ADDON") or ROOT / "wow_addon" / "TreeAI")
MODS = ("QuestPins", "Watch", "Addons", "QoL", "Meter")
LUA = shutil.which("lua5.1")
needs_lua = pytest.mark.skipif(not LUA, reason="lua5.1 not installed")
ALLOW = ["LibQuestieDB", "QuestDB", "NpcDB", "ObjectDB", "ItemDB", "TreeAIPlan", "IsQuestFlaggedCompleted", "Questie",
         "WorldMapFrame", "MapCanvasDataProviderMixin", "CreateFromMixins", "LibStub", "ObjectiveTrackerFrame",
         "EditModeManagerFrame", "ShortestPathForever", "ShortestPathForeverDB", "LootSlot", "C_DamageMeter"]


def src(name):
    return (ADDON / "Modules" / (name + ".lua")).read_text(encoding="utf-8")


def code(name):
    return "\n".join(line.split("--", 1)[0] for line in src(name).splitlines())


def test_toc_order():
    toc = [ln.strip() for ln in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert toc.index("Modules\\QuestPins.lua") == toc.index("Modules\\MinimapPins.lua") + 1
    assert toc.index("Modules\\Watch.lua") == toc.index("Modules\\QuestPins.lua") + 1
    for m in ("QoL", "Meter", "Addons"):   # the « Mes addons » page needs the TreeAI window (Window.lua) loaded first
        assert toc.index("Modules\\JournalPage.lua") < toc.index("Modules\\%s.lua" % m) < toc.index("Modules\\Tour.lua")
    assert toc[-1] == "Modules\\Tour.lua"


def test_taint_rules():
    for m in MODS:
        c = code(m)
        for bad in ("SetParent(", "hooksecurefunc", "ShowUIPanel", "SetSuperTracked", "AcquirePin",
                    "CreateFrame(\"Frame\", nil, child)", "CreateFrame(\"Button\", nil, child)", "UseAction", "CastSpell"):
            assert bad not in c, (m, bad)
    qp = code("QuestPins")
    assert "AddDataProvider" in qp and "MapCanvasDataProviderMixin" in qp          # Blizzard's supported map pattern
    assert 'CF("Frame", nil, up)' in qp                                            # our overlay = child of UIParent
    w = code("Watch")
    # the game's tracker: never Hide / Show in combat (alpha only), secure item buttons only out of combat
    assert "SetAlpha\", 0)" in w and "RunOutOfCombat(\"Watch:hideTracker\"" in w and "RunOutOfCombat(\"Watch:items\"" in w
    assert "SecureActionButtonTemplate" in w and "g(\"UIParent\"), \"SecureActionButtonTemplate\"" in w


def test_no_questie_data_copied():
    # Questie / SPF are GPL: their data is READ at run time, never copied into TreeAI
    for m in MODS:
        c = code(m)
        assert "spawns = {" not in c and "objectives = {" not in c, m
    for p in (ADDON / "Data").glob("*.lua"):
        body = "\n".join(line.split("--", 1)[0] for line in p.read_text(encoding="utf-8", errors="ignore").splitlines())
        assert "QuestieDB" not in body and "Questie" not in body, p.name


def test_other_addons_only_their_option_keys():
    a = code("Addons")
    writes = set(re.findall(r"\bp\.([A-Za-z_.]+)\s*=(?!=)", a))
    assert writes <= {"enabled", "trackerEnabled", "minimap.hide"}, writes
    assert set(re.findall(r"\bd\.([A-Za-z_]+)\s*,?\s*(?:d\.[A-Za-z_]+\s*)?=(?!=)", a)) <= {"tracker", "journeyTracker"}
    for m in ("QuestPins", "Watch", "QoL", "Meter"):
        assert not re.search(r"Questie[^\n]*\.\w+\s*=(?!=)", code(m)), m      # read only everywhere else


def test_labels_and_safe_set():
    qp, w, a, q, d = (src(m) for m in MODS)
    for label in ("« ! » quêtes à prendre", "« ? » quêtes à rendre", "Zones d'objectifs", "quêtes grises"):
        assert label in qp, label
    assert "Remplacer le suivi du jeu" in w and "Par distance" in w and "Ordre du trajet" in w
    assert "Rendre Questie silencieux" in a and "Réafficher Questie" in a and "Activer les icônes" in a
    assert "Vendre les objets gris et réparer" in q and "Butin instantané" in q
    assert "C_DamageMeter" in d and "En combat" in d
    core = (ADDON / "Core.lua").read_text(encoding="utf-8")
    safe = core[core.index("TreeAI.SAFE_MODULES = "):core.index("TreeAI.SAFE_ADDED")]
    safe = "\n".join(line.split("--", 1)[0] for line in safe.splitlines())
    for m in ("QuestPins", "Watch", "Addons"):       # on in « mode sûr » (nothing automatic, Questie keeps the duplicates off)
        assert re.search(r"\b%s = true" % m, safe), m
    for m in ("QoL", "Meter"):                        # automations / a window: one click on the « Mes addons » page
        assert not re.search(r"\b%s = true" % m, safe), m
        assert "SAFE_MODULES" not in code(m)
    for m in MODS:                                    # « Plus → Fonctions » lists them with a description
        assert re.search(r"\b%s = \{ lite = (true|false)[^\n]*desc = \"" % m, core), m


def test_session_sim_caps():
    sim = (ROOT / "tests" / "wow" / "session_sim.lua").read_text(encoding="utf-8")
    assert "QuestPins = 40, Watch = 40" in sim
    assert "QuestPins = 1, Watch = 1, Addons = 1" in sim     # idle (map closed, standing still): nothing


@needs_lua
def test_syntax():
    luac = LUA.replace("lua5.1", "luac5.1")
    if shutil.which(luac):
        for m in MODS:
            assert subprocess.run([luac, "-p", str(ADDON / "Modules" / (m + ".lua"))]).returncode == 0, m


@needs_lua
@pytest.mark.parametrize("flavor", ["modern", "forever"])
def test_scenario(flavor):
    cmd = [LUA, "tests/wow/harness.lua", "-q", "--flavor", flavor, "--class", "HUNTER"]
    for a in ALLOW:
        cmd += ["--allow", a]
    cmd += ["--scenario", "tests/wow/replace_scenario.lua", str(ADDON)]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "RESULT ok" in out, out[-4000:]
    m = re.search(r"PERF QuestPins idle ([\d.]+) · moving ([\d.]+) · map open ([\d.]+) · dragged ([\d.]+)", out)
    assert m, out[-2000:]
    idle, moving, opened, dragged = (float(x) for x in m.groups())
    assert idle == 0 and moving <= 40 and opened <= 40 and dragged <= 40, m.group(0)
