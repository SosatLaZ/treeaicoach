"""MACRO2 (round 7): expert review of every macro — byte length per level band, unknown spells rejected,
conditional syntax validator, Classic-engine rules (Auto Shot / aspects never toggled off, spam-safe
sequences, Feign Death + trap), French client names, consumable requirements, Lua scenarios."""
import csv
import pathlib
import shutil
import subprocess

import pytest

from treeaicoach.wow.macros import lint, lint_all, lint_rules, tokenize
from treeaicoach.wow.macros.consumables import BANDAGE, HEALTHSTONE
from treeaicoach.wow.macros.generator import (LEVELS, TemplateError, applies, lint_spells, simulate_macro)
from treeaicoach.wow.macros.spells import CLASSES, COMMON, SPELLS, spec_ids
from treeaicoach.wow.macros.templates import macros_for, where_for

ROOT = pathlib.Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"


def T(cls, key):
    return "{%d}" % SPELLS[cls][key][0]


def test_whole_base_is_clean():
    assert lint_all() == []


@pytest.mark.parametrize("cls", CLASSES)
def test_every_rendering_fits_255_bytes(cls):
    """Rendered as the game would (every spec × level, every racial / trinket line): ≤ 255 UTF-8 bytes."""
    worst = 0
    for spec in spec_ids(cls):
        for level in LEVELS:
            for m in macros_for(cls):
                if applies(m, spec):
                    body = simulate_macro(cls, m, spec, level)
                    if body:
                        worst = max(worst, len(body.encode("utf-8")))
                        assert len(body.encode("utf-8")) <= 255, (cls, spec, level, m.name)
    assert worst > 100


def test_unknown_spells_rejected():
    with pytest.raises(TemplateError):
        tokenize("HUNTER", "/cast {not_a_spell}")
    # a spell typed in clear (not from the client tables) is refused
    errs = lint_rules("HUNTER", "X", "#showtooltip\n/cast [mod:shift] Tir magique; " + T("HUNTER", "arcane_shot"))
    assert any("pas un sort" in e for e in errs), errs
    errs = lint_rules("HUNTER", "X", "/castsequence reset=5 " + T("HUNTER", "serpent_sting") + ", Morsure")
    assert any("pas un sort" in e for e in errs), errs


def test_conditional_syntax_validator():
    assert lint("X", "/cast [@mouseover] Peur")                            # mouseover with no qualifier
    assert any("contradictoires" in e for e in lint("X", "/cast [harm,help] Peur"))
    assert any("toujours vraie" in e for e in lint("X", "/cast [@mouseover,harm,nodead][] Peur; Charge"))
    assert any("toujours vraie" in e for e in lint("X", "/cast Peur; [mod:shift] Charge"))
    assert any("reset" in e for e in lint("X", "/castsequence reset=combat/soon Peur, Charge"))
    assert any("précéder" in e for e in lint("X", "/cast [mod:shift] Aspect! du faucon"))
    assert any("modificateur" in e for e in lint("X", "/cast [mod:cmd] Peur"))
    assert any("crochets" in e for e in lint("X", "/cast [mod:shift Peur"))
    assert lint("TAI Ok", "#showtooltip\n/cast [mod:shift,@mouseover,harm,nodead][mod:shift] A; "
                          "[@mouseover,harm,nodead][] B") == []
    assert lint("TAI Ok", "/castsequence reset=combat/8/target A, B") == []


def test_toggles_and_sequences():
    auto, hawk = T("HUNTER", "auto_shot"), T("HUNTER", "aspect_hawk")
    assert any("auto_shot" in e for e in lint_spells("HUNTER", "X", f"/cast {auto}"))
    assert lint_spells("HUNTER", "X", f"/cast !{auto}") == []
    assert any("aspect_hawk" in e for e in lint_spells("HUNTER", "X", f"/cast [mod:shift] {hawk}"))
    stealth = "{%d}" % SPELLS["ROGUE"]["stealth"][0]
    assert lint_spells("ROGUE", "X", f"/cast [nostealth,nocombat] {stealth}") == []   # guarded
    fd, trap = T("HUNTER", "feign_death"), T("HUNTER", "freezing_trap")
    assert any("Feindre la mort" in e for e in lint_spells("HUNTER", "X", f"/castsequence reset=combat {fd}, {trap}"))
    assert any("sans reset" in e for e in lint_spells("HUNTER", "X", f"/castsequence {fd}, {trap}"))


def _hunter(name, spec="bete", level=60):
    m = next(m for m in macros_for("HUNTER") if m.name == name and applies(m, spec))
    return simulate_macro("HUNTER", m, spec, level)


def test_hunter_macros_expert_patterns():
    tir = _hunter("TAI Tir", level=28)
    assert "/cast !Tir automatique" in tir and "/petattack [harm,nodead,nomod:alt]" in tir
    assert tir.startswith("#showtooltip\n/cast ")
    fd = _hunter("TAI FD Piege", level=30)
    assert "/cast [combat] Feindre la mort; Piège givrant" in fd and "castsequence" not in fd
    assert "/petfollow [combat]" in fd and "/stopcasting [combat]" in fd
    assert _hunter("TAI FD Piege", level=28) is None            # Feign Death is learned at 30
    asp = _hunter("TAI Aspects", level=40)
    assert asp.count("!Aspect") == 4
    fam = _hunter("TAI Familier", level=12)
    assert "[@pet,dead] Ressusciter le familier; [nopet] Appel du familier; Guérison du familier" in fam
    assert "/use [mod:ctrl,pet] item:" in fam and "/stopmacro [mod:ctrl]" in fam
    assert "Morsure de la mangouste" in _hunter("TAI Melee", level=28)


def test_spell_names_match_french_client_table():
    """Names = frFR SpellName of the Forever build (wago CSV in the session scratchpad when present)."""
    candidates = list(pathlib.Path("/tmp").glob("claude-*/**/frsn/SpellName_fr.csv"))
    if not candidates:
        pytest.skip("frFR SpellName.csv absent")
    fr = {int(r["ID"]): r["Name_lang"] for r in csv.DictReader(candidates[0].open(encoding="utf-8"))}
    for table in [COMMON] + list(SPELLS.values()):
        for key, (sid, name, _f) in table.items():
            assert fr.get(sid) == name, (key, sid, name, fr.get(sid))


def test_consumable_requirements():
    assert dict((i, r) for i, r, _v, _t in BANDAGE)[14530] == 225       # skill to USE, not to craft
    assert dict((i, r) for i, r, _v, _t in BANDAGE)[3530] == 50
    assert dict((i, r) for i, r, _v, _t in HEALTHSTONE)[9421] == 48     # Major Healthstone: level 48


def test_where_and_why_for_every_macro():
    for cls in CLASSES:
        for m in macros_for(cls):
            assert m.why and m.place and len(m.place) < 120, (cls, m.name)
    assert where_for("1").startswith("Case 1")
    assert "Maj+E" in where_for("Maj+E")


def test_mount_and_hearth_macro():
    m = next(m for m in macros_for("HUNTER") if m.name == "TAI Monture")
    body = simulate_macro("HUNTER", m, "bete", 40)
    assert "/dismount [mounted]" in body and "/use [mod:shift] item:" in body


def test_data_exports_where():
    text = (ADDON / "Data" / "Macros.lua").read_text(encoding="utf-8")
    assert "o=\"Case 1 de la barre principale" in text and "D.forms = {DRUID" in text


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("cls", ["HUNTER", "WARRIOR", "PRIEST", "MAGE"])
def test_macro2_scenario(cls):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", "forever", "--class", cls,
                        "--scenario", str(ROOT / "tests/wow/macro2_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-3000:] + r.stderr[-2000:]
