"""Smart macros (agent C): knowledge base, generator, 255-byte linter, Lua data + in-game manager."""
import pathlib
import shutil
import subprocess

import pytest

from treeaicoach.wow.macros import export_lua, lint, lint_all, render, required, tokenize
from treeaicoach.wow.macros.spells import CLASSES
from treeaicoach.wow.macros.templates import macros_for

ROOT = pathlib.Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"


def test_all_macros_lint_clean():
    assert lint_all() == []


@pytest.mark.parametrize("cls", CLASSES)
def test_every_class_has_macros_and_fits_slots(cls):
    ms = macros_for(cls)
    assert len(ms) >= 6
    assert sum(m.default for m in ms) <= 18
    assert len({m.name for m in ms}) == len(ms)


def test_linter_catches_problems():
    assert lint("TAI X", "#showtooltip\n/cast " + "a" * 260)
    assert lint("Un nom beaucoup trop long", "/cast Charge")
    assert any("crochets" in e for e in lint("X", "/cast [mod:shift Charge"))
    assert any("condition inconnue" in e for e in lint("X", "/cast [bidule] Charge"))
    assert any("focus" in e for e in lint("X", "/cast [@focus] Métamorphose"))
    assert any("commande inconnue" in e for e in lint("X", "/autoattackbot"))
    assert any("1re ligne" in e for e in lint("X", "/cast Charge\n#showtooltip"))
    assert any("octets" in e for e in lint("X", "/cast " + "é" * 130))  # accents = 2 bytes
    assert lint("TAI Ok", "#showtooltip\n/cast [@mouseover,harm,nodead][harm,nodead] Peur") == []


def test_hunter_tokens_required_and_render():
    body = tokenize("HUNTER", "#showtooltip\n/cast ?[mod:shift] {multi_shot}; {arcane_shot}\n"
                    "?/cast {bestial_wrath}\n/use {item:hpot}")
    spells, items = required("HUNTER", body)
    assert spells == [3044] and items == ["hpot"]
    full = render("HUNTER", body)
    assert "Flèches multiples" in full and "Courroux bestial" in full and "?" not in full
    minimal = render("HUNTER", body, minimal=True)
    assert "Flèches multiples" not in minimal and "Courroux" not in minimal
    assert "/cast Tir des arcanes" in minimal


def test_unknown_key_raises():
    with pytest.raises(ValueError):
        tokenize("MAGE", "/cast {not_a_spell}")


def test_data_file_in_sync():
    assert (ADDON / "Data" / "Macros.lua").read_text(encoding="utf-8") == export_lua()


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
def test_lua_syntax():
    for f in ("Data/Macros.lua", "Modules/Macros.lua"):
        subprocess.run(["luac5.1", "-p", str(ADDON / f)], check=True)


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
def test_lua_manager_spec():
    r = subprocess.run(["lua5.1", str(ROOT / "tests" / "wow" / "macros_spec.lua"), str(ADDON)],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0 and r.stdout.strip().endswith("OK"), r.stderr + r.stdout
