"""Module Dungeon (agent PVE): TOC, syntax, no globals / automation, data sanity, harness scenario."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
MOD = ADDON / "Modules" / "Dungeon.lua"
DATA = ADDON / "Data" / "Dungeons.lua"


def test_toc_order():
    lines = [ln.strip() for ln in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert lines.index("Data\\Dungeons.lua") < lines.index("Modules\\Dungeon.lua")
    assert lines.index("Modules\\Fight.lua") < lines.index("Modules\\Dungeon.lua")


def test_no_automation():
    src = "\n".join(ln for ln in MOD.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("--"))
    for bad in ("CastSpellByName", "TargetUnit", "SendChatMessage", "SendAddonMessage", "COMBAT_LOG_EVENT",
                "SetBinding", "SelectQuestLogEntry", "SetSelectedQuest", "ShowUIPanel"):
        assert bad not in src, bad
    # SetRaidTarget only from the click handler, guarded by combat
    assert src.count("try(G.SetRaidTarget") == 1 and "Pas de marque en combat" in src


def test_data_sanity():
    src = DATA.read_text(encoding="utf-8")
    ids = [int(x) for blk in re.findall(r"ids = \{([^}]*)\}", src) for x in re.findall(r"\d+", blk)]
    assert len(ids) == len(set(ids)), "duplicate encounter id"
    for known in (592, 2747, 2755, 448, 450, 554, 2772):  # Forever DungeonEncounter ids
        assert known in ids
    keys = re.findall(r'key = "(\w+)"', src)
    assert len(keys) == len(set(keys)) and len(keys) >= 25
    assert "indicatif" in src.lower() or "INDICATIFS" in src
    # 15–40 classic dungeons: every boss has a tip
    for key in ("DM", "WC", "SFK", "BFD", "RFK", "GNOMER", "SM", "RFD", "ULDA"):
        block = src.split(f'key = "{key}"', 1)[1].split("key = ", 1)[0]
        for boss in block.split("{ n = ")[1:]:
            if "ids =" in boss:
                assert "tip =" in boss, (key, boss[:60])


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
@pytest.mark.parametrize("path", [MOD, DATA])
def test_syntax_no_globals(path):
    assert subprocess.run(["luac5.1", "-p", str(path)], capture_output=True).returncode == 0
    listing = subprocess.run(["luac5.1", "-l", "-p", str(path)], capture_output=True, text=True).stdout
    assert "SETGLOBAL" not in listing


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["modern", "legacy"])
def test_dungeon_scenario(flavor):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", "HUNTER",
                        "--scenario", str(ROOT / "tests/wow/dungeon_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout
