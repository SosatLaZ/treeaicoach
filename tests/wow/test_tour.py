"""ONBOARD: « Visite guidée » (Modules/Tour.lua), contextual one-time tips, /tai aide → the minimap button, TUTO_FR.md.

Static rules (TOC, safe set, hooks, taint, glyphs), the harness scenario on the three API flavours, storm hygiene
(the tour never stays open after the generic event storm; a new player gets the first run) and the player guide.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
TOUR = ADDON / "Modules" / "Tour.lua"
HARNESS = ROOT / "tests" / "wow" / "harness.lua"
SCENARIO = ROOT / "tests" / "wow" / "tour_scenario.lua"
LUA = shutil.which("lua5.1")


def _code(src: str) -> str:
    """Lua source without comments (string contents kept)."""
    src = re.sub(r"--\[(=*)\[.*?\]\1\]", "", src, flags=re.S)
    return "\n".join(re.sub(r"--[^\n]*$", "", ln) if "--" in ln and '"' not in ln.split("--")[0] else ln
                     for ln in src.splitlines())


def test_toc_and_core_registration():
    toc = [ln.strip() for ln in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert "Modules\\Tour.lua" in toc
    assert toc.index("Modules\\Tour.lua") > toc.index("Modules\\Hub.lua")
    assert toc[-1] == "Modules\\Tour.lua", "Tour last: every target module is loaded before it"
    core = (ADDON / "Core.lua").read_text(encoding="utf-8")
    assert re.search(r"\n  Tour = \{ lite = true, desc = \"Visite guidée", core)
    src = TOUR.read_text(encoding="utf-8")
    assert "T.SAFE_MODULES.Tour = true" in src


def test_hooks_are_tiny_and_guarded():
    hub = (ADDON / "Modules" / "Hub.lua").read_text(encoding="utf-8")
    opts = (ADDON / "Options.lua").read_text(encoding="utf-8")
    assert "if T.Tour and T.Tour.HubEntry then out[#out + 1] = T.Tour.HubEntry() end" in hub
    assert "if TreeAI.Tour and TreeAI.Tour.OptionsButton then TreeAI.Tour.OptionsButton(P) end" in opts
    assert sum("Tour" in ln for ln in hub.splitlines()) == 1, "one line in Hub.lua"
    assert sum("Tour" in ln for ln in opts.splitlines()) == 1, "one line in Options.lua"


def test_tour_rules():
    src = TOUR.read_text(encoding="utf-8")
    code = _code(src)
    # no protected / Blizzard-panel / taint-prone calls, no direct timers (SPIKE scheduler), no print
    for bad in ("CastSpell", "UseAction", "TargetUnit", "SetBinding", "CreateMacro", "EditMacro", "PickupMacro",
                "ShowUIPanel", "HideUIPanel", "SetParent(", "hooksecurefunc", "SetCVar", "SecureActionButton",
                "C_Timer", "print(", "SendChatMessage", "OpenToCategory"):
        assert bad not in code, bad
    # our three frames are put on UIParent at the target's READ position, never anchored on the target
    assert code.count('"BOTTOMLEFT", G.UIParent, "BOTTOMLEFT"') >= 2
    assert 'SetPoint", "CENTER", G.UIParent, "BOTTOMLEFT"' in code
    for name in ("TreeAITourCallout", "TreeAITourGlow", "TreeAITourArrow"):
        assert f'"{name}", G.UIParent' in code
    # WoW fonts have no arrows / triangles: never in a shown string
    strings = re.findall(r'"([^"\n]*)"', code)
    for s in strings:
        assert "→" not in s and "▾" not in s and "⇒" not in s, s


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
def test_no_globals():
    listing = subprocess.run(["luac5.1", "-l", "-p", str(TOUR)], capture_output=True, text=True).stdout
    assert "SETGLOBAL" not in listing


@pytest.mark.skipif(not LUA, reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["forever", "modern", "legacy"])
@pytest.mark.parametrize("cls", ["HUNTER", "WARRIOR"])
def test_tour_scenario(flavor, cls):
    r = subprocess.run([LUA, str(HARNESS), "-q", "--profile", "tout", "--no-storm", "--flavor", flavor, "--class", cls,
                        "--scenario", str(SCENARIO), str(ADDON)], capture_output=True, text=True, timeout=180, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout


STORM_CHECK = """
return function(M, ctx)
  local T = TreeAI
  local Tr = T.Tour
  ctx.check(Tr, "Tour loaded")
  if not Tr then return end
  local want = (...)
  if want == "seeded" then
    ctx.check(not Tr.active, "storm: the tour is not left open for the next scenarios")
    ctx.check(not (T.Hub and T.Hub.IsOpen and T.Hub.IsOpen()), "storm: the minimap menu is closed")
    ctx.check(Tr.mode ~= "pointer", "storm: no help bubble left")
  else
    ctx.check(Tr.active or Tr.Done(), "new player: the first-run tour started (or was already seen)")
  end
end
"""


@pytest.mark.skipif(not LUA, reason="lua5.1 absent")
@pytest.mark.parametrize("profile", ["tout", "sur"])
def test_storm_hygiene(tmp_path, profile):
    want = "seeded" if profile == "tout" else "first"
    scen = tmp_path / "storm_check.lua"
    scen.write_text(STORM_CHECK.replace("(...)", f'"{want}"'), encoding="utf-8")
    r = subprocess.run([LUA, str(HARNESS), "-q", "--profile", profile, "--flavor", "forever", "--class", "HUNTER",
                        "--scenario", str(scen), str(ADDON)], capture_output=True, text=True, timeout=300, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout


def test_tuto_quick_start_and_faq():
    # (5.1 CLEAN) rewritten from the 5.x screens (audit B5: the 4.0 tutorial spoke of pages, « bêta » and menus that
    # no longer exist); its commands are checked against the addon by tests/wow/test_clean51.py
    tuto = (ROOT / "wow_addon" / "TUTO_FR.md").read_text(encoding="utf-8")
    lines = tuto.splitlines()
    assert len(lines) <= 95, f"quick start + short FAQ, not a manual ({len(lines)} lines)"
    for head in ("## Installer", "## La bande d'actions rapides", "## Le guidage des quêtes", "## Sacs, équipement, métiers",
                 "## Les macros", "## Ce qui tourne d'office", "## Commandes utiles", "## Envoyer le rapport d'erreurs",
                 "## FAQ"):
        assert head in tuto, head
    for must in ("/tai erreurs", "/reload", r"%APPDATA%\TreeAICoach\TreeAI-erreurs.txt", "Erreurs WoW : ouvrir",
                 "Visite guidée", "Tutoriel : quoi poser, sur quelle touche", "/tai guide on"):
        assert must in tuto, must
    faq = tuto.split("## FAQ", 1)[1]
    for topic in ("lague", "bloqué", "n'apparaît pas"):
        assert topic in faq, topic


def test_readme_documents_the_contract():
    readme = (ROOT / "wow_addon" / "README.md").read_text(encoding="utf-8")
    assert "module `Tour`" in readme and "T.Tour.Target(id" in readme and "TUTO_FR.md" in readme
    assert "/tai visite" in readme or "`visite`" in readme
