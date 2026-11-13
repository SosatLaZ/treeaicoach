"""5.0 LOOK: the V5 look (tests/wow/look5_scenario.lua) — one set of design tokens (Theme.lua), the « actions rapides »
strip impossible to miss on a real screen, the redone window (≤ 11 guarded categories with icons, ≤ 3 action tiles per
page, search across every action / setting, Réglages façon Leatrix with « Affichage » and « Simple / Complet »), the
3-step first-run setup, toasts one at a time, the minimap menu; forever + modern.
Layout dumps at 3 resolutions × 3 UI scales × 2 default layouts: nothing over the party / player / target frames, the
quest tracker, the minimap or the buffs, nothing off screen, nothing clipped, still there in combat, zero chat line,
zero blocked action. Static rules: bindings, nav, no per-frame work."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
MODS = ADDON / "Modules"
LUA = shutil.which("lua5.1")
MINE = ["Window.lua", "WindowPages.lua", "JournalPage.lua", "Shortcuts.lua", "Hub.lua", "Binds.lua", "Tour.lua", "Addons.lua",
        "Theme.lua", "Meter.lua"]   # (5.1 CLEAN) Quick / QuickFight removed


def code(path: Path) -> str:
    return "\n".join(line.split("--", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())


def run(flavor: str, geometry: str = "ready=1", savedvars: Path | None = None, **env: str) -> str:
    cmd = [LUA, str(ROOT / "tests/wow/harness.lua"), "-q", "--no-storm", "--geometry", geometry, "--profile", "sur",
           "--flavor", flavor, "--class", "HUNTER", "--level", "30"]
    if savedvars:
        cmd += ["--savedvars", str(savedvars)]
    cmd += ["--scenario", str(ROOT / "tests/wow/look5_scenario.lua"), str(ADDON)]
    e = dict(os.environ, **env)
    e.pop("TREEAI_WOW_FORCE_FLAVOR", None)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=ROOT, env=e)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-7000:] + r.stderr[-2000:]
    assert "LOOK5 chat:" not in r.stdout and "LOOK5 violation:" not in r.stdout
    return r.stdout


@pytest.mark.skipif(not LUA, reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["forever", "modern"])
def test_look5_full(flavor: str) -> None:
    out = run(flavor)
    # what the player sees: groups of the strip, the pill, the nav, the settings sub-list, the profiles, the setup
    assert "bande : Quêtes [Guidage quêtes ON] : Rendre (2) · Prendre · Suivant · Zone" in out
    assert "|  Perso : Sacs · Équipement" in out and "|  Groupe  |  TreeAI" in out
    assert "LOOK5 navigation : Quêtes · Trajet · Groupe · Donjons · Équipement" in out
    assert re.search(r"LOOK5 Réglages, sous-liste : Affichage · Profil : Simple / Complet · Touches", out)
    assert "LOOK5 PROFIL Simple : " in out and "LOOK5 PROFIL Complet : " in out
    assert "étape 1 — Bienvenue dans TreeAI  1/3 · Ton personnage" in out
    assert "étape 2 — Bienvenue dans TreeAI  2/3 · Ce que TreeAI affiche" in out
    assert "étape 3 — Bienvenue dans TreeAI  3/3 · Tes touches" in out
    assert "LOOK5 groupes : rapide · jeu" in out
    assert "RECHERCHE « rendre » : Action : Rendre mes quêtes" in out


SCREENS = [(1920, 1080), (2560, 1440), (1366, 768)]
SCALES = ["0.64", "0.9", "1.15"]


@pytest.mark.skipif(not LUA, reason="lua5.1 absent")
@pytest.mark.parametrize("layout", ["classic", "modern"])
@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize("res", SCREENS)
def test_look5_screen(res: tuple[int, int], scale: str, layout: str) -> None:
    out = run("forever", geometry=f"ready=1,w={res[0]},h={res[1]}", LOOK5_MODE="screen", LOOK5_SCALE=scale, LOOK5_LAYOUT=layout)
    m = re.search(r"TreeAIStrip : gauche (-?\d+), haut (-?\d+), (\d+)×(\d+) unités = (\d+)×(\d+) px réels", out)
    assert m, out[-3000:]
    # the 4.3.6 strip was 521×27 real px at 1080p / 0.64 with 9 px text : never again that small
    assert int(m.group(6)) >= 30, m.group(0)
    assert "fenêtre : " in out and "pages dessinées sans chevauchement" in out


@pytest.mark.skipif(not LUA, reason="lua5.1 absent")
def test_look5_login_race() -> None:
    """Real login: UIParent unsized, the UI scale lands late, Edit Mode re-anchors the frames — same guarantees."""
    out = run("forever", geometry="w=1920,h=1080", LOOK5_MODE="screen", LOOK5_LAYOUT="classic")
    assert "TreeAIStrip : gauche" in out


@pytest.mark.skipif(not LUA, reason="lua5.1 absent")
def test_look5_existing_player_gets_the_setup_once(tmp_path: Path) -> None:
    sv = tmp_path / "sv.lua"
    sv.write_text('TreeAIDB = { profileV = 2, safeV = 4, moduleProfile = "sur", tour = { done = 1 }, onboarded = "4.3.6", '
                  'v5hBar = true }\n', encoding="utf-8")
    out = run("forever", savedvars=sv, LOOK5_MODE="upgrade")
    assert "LOOK5 MISE À JOUR étape 1 : Bienvenue dans TreeAI  1/3 · Ton personnage" in out


def test_tokens_one_system() -> None:
    src = (MODS / "Theme.lua").read_text(encoding="utf-8")
    assert "Th.V = 5" in src and "Th.TOKENS = {" in src and "Th.ICONS = {" in src and "Th.LEGEND = {" in src
    for fn in ("ActionButton", "Pill", "CountBadge", "Card", "Legend", "LegendLines", "PaintAction", "ToastSeconds", "StopAnim"):
        assert re.search(r"function Th:%s\(" % fn, src), fn
    # menus opaque, small on-screen frames half transparent
    for m in re.finditer(r"\n    bg = \{ [\d.]+, [\d.]+, [\d.]+, ([\d.]+) \}", src):
        assert float(m.group(1)) >= 0.95, m.group(0)
    smalls = re.findall(r"small = \{ [\d.]+, [\d.]+, [\d.]+, ([\d.]+) \}", src)
    assert len(smalls) == 3 and all(0.35 <= float(a) <= 0.65 for a in smalls), smalls
    # game icons only: no texture shipped with the addon
    for f in MINE:
        c = code(MODS / f)
        assert "Interface\\\\AddOns\\\\TreeAI" not in c, f


def test_strip_groups_and_direct_actions() -> None:
    src = (MODS / "Shortcuts.lua").read_text(encoding="utf-8")
    order = re.findall(r'\{ id = "(\w+)", group = "(\w+)"', src)
    assert order == [("rendre", "quetes"), ("prendre", "quetes"), ("suivant", "quetes"), ("zone", "quetes"), ("vite", "quetes"),
                     ("sacs", "perso"), ("equip", "perso"), ("metiers", "perso"), ("groupe", "groupe"), ("fenetre", "treeai")]
    assert 'T.SetQuestGuide' in src and 'T.QuestGuideOn' in src and '"QUEST_GUIDE_CHANGED"' in src
    assert "function S.SetAction(id, spec)" in src and "function S.NextAction(" in src and "function S.Audit()" in src
    assert 'T:RegisterSlash("interface"' in src
    c = code(MODS / "Shortcuts.lua")
    # nothing per frame, nothing protected, nothing in the chat
    for bad in ("OnUpdate", "C_Timer", "SetBinding", "SaveBindings", "CastSpell", "UseAction", "RunMacroText", "SendChatMessage",
                ":Print(", "print(", "EquipItemByName", "UseContainerItem"):
        assert bad not in c, bad


def test_one_binding_per_action_no_default_key() -> None:
    root = ET.parse(ADDON / "Bindings.xml").getroot()
    by = {b.get("name"): b for b in root}
    src = (MODS / "Shortcuts.lua").read_text(encoding="utf-8")
    order = re.search(r"S\.KEY_ORDER = \{(.*?)\}", src, re.S).group(1)
    cmds = re.findall(r'"(TREEAI_\w+)"', order)
    assert len(cmds) == 14
    for cmd in cmds:
        b = by.get(cmd)
        assert b is not None, cmd
        assert b.get("default") is None and b.get("header") == "TREEAI", cmd
        assert "G.BINDING_NAME_" + cmd in src or "BINDING_NAME_" + cmd in (MODS / "Window.lua").read_text(encoding="utf-8"), cmd
    for cmd, arg in (("TREEAI_QB_FASTLVL", "vite"), ("TREEAI_STRIP_SACS", "sacs"), ("TREEAI_STRIP_EQUIP", "equip"),
                     ("TREEAI_GUIDE_TOGGLE", "guide"), ("TREEAI_ACTION_NEXT", "next")):
        assert 'TreeAI.StripAction("%s")' % arg in by[cmd].text, cmd
    keys = re.findall(r'"((?:CTRL|ALT|SHIFT)[A-Z-]*-[\dF]\d?)"', code(MODS / "Shortcuts.lua"))
    assert keys
    for k in keys:
        assert not re.fullmatch(r"CTRL-\d", k) and not re.fullmatch(r"SHIFT-[1-6]", k), k


def test_window_shell_rules() -> None:
    src = (MODS / "Window.lua").read_text(encoding="utf-8")
    nav = re.search(r"\nW\.NAV = \{(.*?)\}", src).group(1)
    ids = re.findall(r'"(\w+)"', nav)
    assert len(ids) == 11 and ids[0] == "quests" and ids[-1] == "settings" and "bags" in ids
    assert "W.FALLBACK = { bags = \"gear\"" in src and "W.SIDE = { settings = true }" in src
    for fn in ("function Ly.actions(", "function Ly.toggle(", "function Ly.choice(", "function Ly.card(", "function Ly.note(",
               "function Ly.kv(", "function Ly.legend(",
               # the 4.x layout API other agents' pages use: kept, never broken
               "function Ly.gap(", "function Ly.text(", "function Ly.header(", "function Ly.row(", "function Ly.hero(",
               "function Ly.step(", "function Ly.quest(", "function Ly.buttons(", "function Ly.empty(", "function Ly.tabs(",
               "function Ly.tiles(", "function Ly.finish("):
        assert fn in src, fn
    assert "function W.SearchHits(cur)" in src and "function W.BuildNav()" in src and "function W.ResetPlace()" in src
    # first opening on the right half, never the centre ; no per-frame script in the shell
    assert 'anchorTo(f, "RIGHT", -W.FIRST.right, W.FIRST.y)' in src
    assert "OnUpdate" not in code(MODS / "Window.lua")


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
def test_syntax_no_globals() -> None:
    for p in [MODS / f for f in MINE] + [ADDON / "Options.lua"]:
        assert subprocess.run(["luac5.1", "-p", str(p)], capture_output=True).returncode == 0, p
        listing = subprocess.run(["luac5.1", "-l", "-p", str(p)], capture_output=True, text=True).stdout
        assert "SETGLOBAL" not in listing, p
