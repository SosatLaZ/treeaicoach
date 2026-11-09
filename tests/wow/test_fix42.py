"""4.2 FIX42: hunter auto-shot bar from PLAYER_SWING, purchases / crafts through one click-only path (T:ShopCall, safe
mode, /tai bloque achats), « Composants de métier » only at NPCs / in a session, the strip's « Métiers » button, HV
tiles never mixed, sell only the surplus (keep counts), NPC cards (merchant / bank / mailbox), Theme « small » frames,
the time-sliced scheduler (« micro-lag toutes les secondes ») and the session_sim --walk / --step / --ms-rate probes.
Static rules + the harness scenario (tests/wow/fix42_scenario.lua) on forever and modern."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
MODS = ADDON / "Modules"
LUA = shutil.which("lua5.1")


def code(path):
    return "\n".join(line.split("--", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())


def test_hunter_bar_reads_player_swing():
    src = code(MODS / "Hunter.lua")
    assert '"PLAYER_SWING"' in src and "function M:OnSwing(dur, kind)" in src
    assert "M.SWING_RANGED = 2" in src
    assert 'pcall(get, "showSwingTimer")' in src   # the game's own swing bars: ours stays silent
    assert re.search(r"M\.defaults = \{[^}]*trapBar = false, fdTimer = false, fleeAlert = false", src)


def test_shop_actions_only_through_shopcall():
    # every purchase / craft / container move of the click flows goes through T:ShopCall (Core) or the module's shop()
    for name in ("ProfLevel.lua", "CityRun.lua", "QuickFight.lua"):
        src = code(MODS / name)
        for bad in ("pcall(fn, e.index", "pcall(fn, pick.bag", "pcall(fn, s.bag", "pcall(C.CraftRecipe", "pcall(fn, idx, n)",
                    "try(AH.StartCommoditiesPurchase", "try(AH.ConfirmCommoditiesPurchase", "try(buy, o.index", "local ok = pcall(fn)"):
            assert bad not in src, (name, bad)
        assert "ShopCall" in src or "shop(" in src, name
    core = code(ADDON / "Core.lua")
    assert "function TreeAI:ShopCall(api, fn, ...)" in core and 'return false, "noclick"' in core
    assert "function TreeAI:_ShopBlocked(func)" in core and "TreeAI:_ShopBlocked(func)" in core
    st = code(MODS / "SelfTest.lua")
    assert 'a:find("^%s*achat")' in st and "T:ShopCheck()" in st


def test_strip_metiers_button_and_binding():
    src = code(MODS / "Shortcuts.lua")
    assert 'id = "metiers"' in src and 'bind = "TREEAI_STRIP_METIERS"' in src and "optional = true" in src
    xml = (ADDON / "Bindings.xml").read_text(encoding="utf-8")
    assert 'name="TREEAI_STRIP_METIERS"' in xml and 'StripAction("metiers")' in xml


def test_hv_tiles_never_mixed():
    src = code(MODS / "Auction.lua")
    assert "Vendre tes sacs à l'HV : +" in src and "Acheter-revendre : +" in src
    assert "bénéfice possible maintenant" not in src


def test_theme_small_tokens():
    src = code(MODS / "Theme.lua")
    assert 'small = { bg = "small", border = "smallBorder" }' in src and "Th.SMALL = { alpha = 0.42" in src
    for m in re.finditer(r"small = \{ ([\d.]+), ([\d.]+), ([\d.]+), ([\d.]+) \}", src):
        assert 0.35 <= float(m.group(4)) <= 0.5, m.group(0)
    assert 'th:Skin(f, "small")' in code(MODS / "Shortcuts.lua")


def test_scheduler_slices_deferred_jobs():
    core = code(ADDON / "Core.lua")
    assert "TreeAI._sliceAfter = function(sec, cb, owner)" in core
    assert "TreeAI.SLICE_MS, TreeAI.BIG_MS" in core and "p.once" in core
    sim = (ROOT / "tests" / "wow" / "session_sim.lua").read_text(encoding="utf-8")
    assert '"--walk"' in sim and '"--step"' in sim and '"--ms-rate"' in sim and "per-second worst frame" in sim


@pytest.mark.skipif(not LUA, reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["forever", "modern"])
def test_fix42_scenario(flavor):
    r = subprocess.run([LUA, str(ROOT / "tests/wow/harness.lua"), "-q", "--no-storm", "--flavor", flavor, "--class", "HUNTER",
                        "--scenario", str(ROOT / "tests/wow/fix42_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=300, cwd=ROOT)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-6000:] + r.stderr[-2000:]
    out = r.stdout
    assert "FIX42 garder / vendre : Cuir lourd — garde 12, vends 18" in out
    assert "Métiers (" in out and "chat:" not in out and "violation:" not in out


@pytest.mark.skipif(not LUA, reason="lua5.1 absent")
def test_walk_probe_no_flap_no_2ms_frame():
    r = subprocess.run([LUA, str(ROOT / "tests/wow/session_sim.lua"), "--class", "HUNTER", "--flavor", "forever", "--profile", "sur",
                        "--minutes", "3", "--walk", "--step", "0.02", "--ms-rate", "50", "--addon", str(ADDON)],
                       capture_output=True, text=True, timeout=600, cwd=ROOT)
    out = r.stdout
    assert "per-second worst frame" in out, out[-3000:]
    m = re.search(r"seconds with a frame > 2 ms: (\d+) /", out)
    assert m and int(m.group(1)) == 0, out[-3000:]
    assert "FLAP " not in out and "errors=0" in out and "violations=0" in out, out[-3000:]
