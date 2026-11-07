"""Smart macros (agent C): knowledge base, generator, 255-byte linter, Lua data + in-game manager."""
import pathlib
import shutil
import subprocess

import pytest

from treeaicoach.wow.macros import band_report, export_lua, lint, lint_all, lint_rules, render, required, tokenize
from treeaicoach.wow.macros.generator import applies
from treeaicoach.wow.macros.spells import CLASSES, LEVEL_OVERRIDE, SPELLS, is_talent, spec_ids
from treeaicoach.wow.macros.templates import macros_for

ROOT = pathlib.Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"


def test_all_macros_lint_clean():
    assert lint_all() == []


@pytest.mark.parametrize("cls", CLASSES)
def test_every_class_has_macros_per_spec(cls):
    for spec in spec_ids(cls):
        ms = [m for m in macros_for(cls) if applies(m, spec)]
        assert len(ms) >= 10, (cls, spec)
        assert all(m.why for m in ms)


@pytest.mark.parametrize("cls", CLASSES)
def test_every_level_band_has_working_macros(cls):
    """Each spec × level band gets real, linted macros built only from spells known at that level."""
    for spec in spec_ids(cls):
        report = band_report(cls, spec)
        for (lo, hi), names in report.items():
            assert len(names) >= 5, (cls, spec, lo, hi, names)
        # macros only grow with the level
        bands = list(report.values())
        for prev, nxt in zip(bands, bands[1:]):
            assert set(prev) <= set(nxt), (cls, spec)


@pytest.mark.parametrize("cls", CLASSES)
def test_band_renderings_use_only_known_spells(cls):
    from treeaicoach.wow.macros.generator import _names, known_ids, simulate_macro
    names = _names(cls)
    for spec in spec_ids(cls):
        for level in (9, 19, 29, 39, 49, 60):
            known_names = {names[i] for i in known_ids(cls, spec, level)}
            unknown = set(names.values()) - known_names
            for m in macros_for(cls):
                body = simulate_macro(cls, m, spec, level) if applies(m, spec) else None
                if not body or m.name in ("TAI Grondement", "TAI Demon sort"):
                    continue
                for line in body.split("\n"):
                    for clause in line.split(";"):
                        spell = clause.split("]")[-1].strip().lstrip("!")
                        if line.startswith("/cast ") and "]" not in clause:
                            spell = clause.replace("/cast ", "").strip().lstrip("!")
                        assert spell not in unknown, (cls, spec, level, m.name, spell)


@pytest.mark.parametrize("cls", CLASSES)
def test_spells_match_forever_client_tables(cls):
    """IDs + French names come from the Forever client (gamedata), except pet/talent extras."""
    from treeaicoach.wow import gamedata
    if not gamedata.available():
        pytest.skip("gamedata absent")
    client = {s["id"]: s["name"] for s in gamedata.class_spells(cls)}
    extras = []
    for key, (sid, name, flags) in SPELLS[cls].items():
        if sid in client:
            assert client[sid] == name, (cls, key, client[sid], name)
        else:
            extras.append(key)
            assert "p" in flags or is_talent(cls, key) or key in LEVEL_OVERRIDE.get(cls, {}), (cls, key)
    assert len(extras) <= 10, extras


def test_default_install_fits_character_slots():
    from treeaicoach.wow.macros.generator import LEVELS, simulate_macro
    for cls in CLASSES:
        for spec in spec_ids(cls):
            for ctx in ("solo", "groupe"):
                n = sum(1 for m in macros_for(cls) if applies(m, spec) and m.default
                        and m.ctx in ("", ctx) and simulate_macro(cls, m, spec, LEVELS[-1]))
                assert n <= 18, (cls, spec, ctx, n)


def test_gcd_and_showtooltip_rules():
    hs, sb = "{%d}" % SPELLS["WARRIOR"]["heroic_strike"][0], "{%d}" % SPELLS["WARRIOR"]["slam"][0]
    ms = "{%d}" % SPELLS["WARRIOR"]["mortal_strike"][0]
    assert lint_rules("WARRIOR", "X", f"#showtooltip\n/cast {sb}\n/cast {hs}") == []   # next-swing is off GCD
    assert any("temps de recharge global" in e
               for e in lint_rules("WARRIOR", "X", f"#showtooltip\n/cast {sb}\n/cast {ms}"))
    assert any("action principale" in e
               for e in lint_rules("WARRIOR", "X", f"#showtooltip\n/cast {hs}\n/cast {ms}"))
    assert any("bijou" in e for e in lint_rules("WARRIOR", "X", "/use {slot:13}"))
    assert any("sans /cast" in e for e in lint_rules("WARRIOR", "X", "#showtooltip\n/startattack"))


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
