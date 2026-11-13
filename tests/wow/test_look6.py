"""5.1 LOOK — « une vraie maj graphique » : calm, simple, connected to the player's other addons
(tests/wow/look6_scenario.lua, on the player's real screen: 2000×1125, UI scale 0.64, the « Suivi Forever » column).

  look     V6 tokens (flat, thin, translucent, one accent, gold for titles only); the strip is ONE row of 24–28 real
           pixels, faded when idle (60 %) and in combat (30 %), full on hover; « Suivant » / « Vite » off the strip by
           default but still working; a 5.0 saved position is kept; /tai interface reset; one small short toast.
  login    a new player gets NO window by itself: one small dismissable line; nothing proposes itself during the first
           minute nor in combat, one at a time, « plus tard » remembered; /tai bienvenue and /tai visite still work.
  window   opens on « Maintenant » (≤ 5 lines, one button each); ≤ 9 plain categories; ≤ 1 primary action per page;
           old pages reachable; search works; never over the game's quest tracker.
  addons   with Shortest Path Forever: no TreeAI compass, no second route list, « Y aller » → its public API on the
           player's click (else the game's waypoint), the « Maintenant » block against its column; read-only (nothing
           written in another addon's tables, no frame touched); every link switches off; Leatrix Plus; the probe.
  connect  no dead end: advice lines get the obvious click.
Static rules: read-only integration, no per-frame work, the renderer tool stays out of the addon.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
MODS = ADDON / "Modules"
LUA = shutil.which("lua5.1")
needs_lua = pytest.mark.skipif(not LUA, reason="lua5.1 absent")


def code(path: Path) -> str:
    return "\n".join(line.split("--", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())


def run(part: str, flavor: str = "forever") -> str:
    env = dict(os.environ, LOOK6_PART=part)
    env.pop("TREEAI_WOW_FORCE_FLAVOR", None)
    r = subprocess.run([LUA, str(ROOT / "tests/wow/harness.lua"), "-q", "--no-storm", "--allow", "ShortestPathForever",
                        "--allow", "ShortestPathForeverDB", "--allow", "LeaPlusDB", "--geometry", "ready=1,w=2000,h=1125",
                        "--profile", "sur", "--flavor", flavor, "--class", "HUNTER", "--level", "30",
                        "--scenario", str(ROOT / "tests/wow/look6_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=300, cwd=ROOT, env=env)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-7000:] + r.stderr[-2000:]
    assert "LOOK6 chat:" not in r.stdout
    return r.stdout


@needs_lua
@pytest.mark.parametrize("flavor", ["forever", "modern"])
def test_look_strip_and_toast(flavor: str) -> None:
    out = run("look", flavor)
    m = re.search(r"LOOK6 bande : (\d+)×(\d+) px réels en x \d+, y \d+ ; alpha ([\d.]+)", out)
    assert m and 24 <= int(m.group(2)) <= 28 and float(m.group(3)) == 0.60, out[-3000:]
    assert "Rendre (1) · Prendre · Zone" in out and "Suivant" not in out.split("LOOK6 bande : Quêtes")[1].split("\n")[0]
    assert "JETON thème = Sombre (v6)" in out and "JETON couleur accent = 0.45 0.74 1.00" in out
    assert re.search(r"LOOK6 alerte : « Quête finie : Chasse de test » \d+×\d+ px", out)


@needs_lua
def test_first_login_is_quiet() -> None:
    out = run("login")
    assert re.search(r"LOOK6 1er lancement : ligne \d+×\d+ px", out)
    assert "TreeAI est prêt." in out


@needs_lua
def test_window_home_and_navigation() -> None:
    out = run("window")
    assert "LOOK6 navigation : Maintenant · Quêtes · Groupe · Équipement" in out
    assert "LOOK6 onglets de Quêtes : Quêtes | Trajet" in out
    home = out.split("LOOK6 MAINTENANT (page d'accueil) :")[1].split("LOOK6 navigation")[0].strip().splitlines()
    assert 1 <= len(home) <= 5 and all(re.search(r"\[[^\]]+\]$", line) for line in home), home


@needs_lua
def test_other_addons_are_connected_read_only() -> None:
    out = run("addons")
    assert "LOOK6 sans API Navigate : « Y aller » = blizzard (repère du jeu posé)" in out
    assert "ShortestPathForever.API.Navigate=function" in out and "Colonne Suivi Forever : cadre « ShortestPathForeverTrackerFrame »" in out
    assert "Leatrix Plus vend les objets gris et répare déjà tout seul : TreeAI ne le propose pas." in out
    m = re.search(r"LOOK6 bloc « Maintenant » : (\d+)×(\d+) px en x (\d+), y (\d+) ; colonne Suivi Forever x (\d+)–(\d+), y (\d+)–(\d+)", out)
    assert m, out[-3000:]
    w, h, x, y, cx0, cx1, cy0, cy1 = map(int, m.groups())
    assert abs(w - (cx1 - cx0)) <= 3 and x == cx0 and (y + h <= cy0 or y >= cy1)


@needs_lua
def test_no_dead_end() -> None:
    run("connect")


def test_integration_is_read_only_static() -> None:
    """Addons.lua / Now.lua: nothing is ever written in another addon's saved variables or options, none of its
    frames is anchored / moved / hidden; other addons' tables are read with rawget."""
    a = code(MODS / "Addons.lua")
    n = code(MODS / "Now.lua")
    for src in (a, n):
        for bad in ("hooksecurefunc", "SetParent(", ":Hide()", "ShowUIPanel", "ReloadUI", "SetBackdrop", "StartMoving", "RegisterForDrag"):
            assert bad not in src, bad
    # the old « rendre silencieux » writers are gone (they wrote in Questie's / SPF's options)
    for gone in ("SilenceQuestie", "RestoreQuestie", "SetSPFTracker", "trackerEnabled = false", "journeyTracker = false", "p.enabled = false"):
        assert gone not in a, gone
    assert not re.search(r"\b(ShortestPathForeverDB|LeaPlusDB|Questie|Auctionator)\b[^\n]*\]\s*=[^=]", a)
    assert 'rawget(d, "AutoSellJunk")' in a and 'rawget(S, "API")' in a
    # our block is anchored to UIParent at the READ position of the column, never to the other addon's frame
    assert 'meth(block, "SetPoint", "BOTTOMLEFT", G.UIParent, "BOTTOMLEFT"' in n
    assert "OnUpdate" not in n and "OnUpdate" not in a and "NewTicker" not in n and "NewTicker" not in a
    # the hand-off happens inside a player's click only
    assert "T:InUserAction()" in a


def test_strip_has_no_per_frame_work() -> None:
    s = code(MODS / "Shortcuts.lua")
    assert "OnUpdate" not in s.split("-- ===================================================================== barre d'actions")[0]
    assert "function S.Alpha()" in s and "function S.Hover(on)" in s


def test_nav_and_tokens_static() -> None:
    w = (MODS / "Window.lua").read_text(encoding="utf-8")
    assert 'W.NAV = { "home", "quests", "group", "gear", "bags", "prof", "hv", "hunter", "settings" }' in w
    assert 'W.TABS = { quests = { "quests", "route" }, group = { "group", "dungeon" } }' in w
    t = (MODS / "Theme.lua").read_text(encoding="utf-8")
    assert "Th.V = 6" in t and "border = { 1, 1, 1, 0.10 }" in t
    core = (ADDON / "Core.lua").read_text(encoding="utf-8")
    assert 'if type(self.Hello) == "function" then self:Hello() else self:ShowWizard(1) end' in core
    tour = (MODS / "Tour.lua").read_text(encoding="utf-8")
    assert "M.AUTO = false" in tour


def test_every_module_keeps_its_first_line() -> None:
    first = "local tonumber = function(v) return _G.tonumber(v) end"
    for f in sorted(MODS.glob("*.lua")):
        head = f.read_text(encoding="utf-8").splitlines()[:40]
        assert any(line.startswith(first) for line in head), f.name
    assert (MODS / "Now.lua").read_text(encoding="utf-8").splitlines()[0].startswith(first)


def test_renderer_is_a_tool_not_part_of_the_addon() -> None:
    assert (ROOT / "tools" / "wow_render.lua").exists() and (ROOT / "tools" / "wow_render.js").exists()
    toc = (ADDON / "TreeAI.toc").read_text(encoding="utf-8")
    assert "wow_render" not in toc and "Modules\\Now.lua" in toc
    # no capture of the player's screen in the repository (it shows other players' names)
    for ext in ("*.webp", "*.png"):
        assert not list((ROOT / "tools").glob(ext)) and not list((ROOT / "research").glob("look6*" + ext[1:]))
