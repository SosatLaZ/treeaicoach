"""MACRO3 (4.0.1): macro status page, personalisation (Lua = Python), presets Simple / Complet / Pro, smart aspect,
« Pro » macros (one-button pull, burst, panic, melee, pet, kite, assist, focus, rare / quest « clic » buttons),
aspect guard and « once per target » sequences in the linter, off-GCD flags from the Forever client tables."""
import csv
import pathlib
import re
import shutil
import subprocess

import pytest

from treeaicoach.wow.macros import lint, lint_all, lint_rules, tokenize
from treeaicoach.wow.macros.generator import (GUARD_LINE, applies, option_combos, options_of, personalize,
                                              simulate_macro)
from treeaicoach.wow.macros.spells import COMMON, SPELLS
from treeaicoach.wow.macros.templates import macros_for

ROOT = pathlib.Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
SCRATCH = list(pathlib.Path("/tmp").glob("claude-*/**/macro3dl"))


def T(key, cls="HUNTER"):
    return "{%d}" % SPELLS[cls][key][0]


def _m(name, cls="HUNTER", spec="bete"):
    return next(m for m in macros_for(cls) if m.name == name and applies(m, spec))


def _body(name, level=28, spec="bete", **opts):
    return simulate_macro("HUNTER", _m(name, spec=spec), spec, level, opts=opts or None)


def test_base_clean_with_every_personalisation():
    assert lint_all() == []


# the same cases are asserted on the Lua side (tests/wow/macro3_scenario.lua): personalize() Lua == Python
PARITY = [
    ("#showtooltip\n/cast ?[mod:shift] {2643}; [@mouseover,harm,nodead][] {3044}\n?/cast !{13165}\n/cast !{75}\n"
     "/petattack [harm,nodead,nomod:alt]\n/stopmacro [mod:shift]", dict(mods=False, mo=False),
     "#showtooltip\n/cast {3044}\n?/cast !{13165}\n/cast !{75}\n/petattack [harm,nodead]"),
    ("#showtooltip\n/cast ?[mod:shift] {2643}; [@mouseover,harm,nodead][] {3044}\n?/cast !{13165}\n/cast !{75}\n"
     "/petattack [harm,nodead,nomod:alt]\n/stopmacro [mod:shift]", dict(pet=False, guard=False),
     "#showtooltip\n/cast ?[mod:shift] {2643}; [@mouseover,harm,nodead][] {3044}\n/cast !{75}\n/stopmacro [mod:shift]"),
    ("/assist [@mouseover,help,nodead][help,nodead][@party1,exists,nodead]", dict(tank=3, mo=False),
     "/assist [help,nodead][@party3,exists,nodead]"),
    ("#showtooltip {136}\n/petattack [nomod,@mouseover,harm,nodead][nomod,harm,nodead]\n/cast [mod:alt,@pet,exists,nodead] {136}",
     dict(mods=False), "/petattack [@mouseover,harm,nodead][harm,nodead]"),
    ("/cast [mod:shift][@pet,dead] {982}; [nopet] {883}; {136}", dict(mods=False),
     "/cast [@pet,dead] {982}; [nopet] {883}; {136}"),
    ("#showtooltip\n/use [mod:alt,@player][@mouseover,help,nodead][@target,help,nodead][@player] {item:bandage}",
     dict(mods=False, mo=False), "#showtooltip\n/use [@target,help,nodead][@player] {item:bandage}"),
    ("/castsequence [nomod:shift] reset=target {1130}, null\n/cast [mod:shift] {2643}", dict(mods=False),
     "/castsequence reset=target {1130}, null"),
]


@pytest.mark.parametrize("body,opts,want", PARITY)
def test_personalize_cases(body, opts, want):
    assert personalize(body, **opts) == want
    assert personalize(body) == body                     # everything on: untouched


def test_lua_personalize_has_the_same_cases():
    src = (ROOT / "tests" / "wow" / "macro3_scenario.lua").read_text(encoding="utf-8")
    for _body0, _opts, want in PARITY:
        lua = want.replace("\n", "\\n")
        assert lua in src, want


def test_smart_aspect_macro():
    """Hawk in combat or on a hostile living target, else Cheetah; « ! » never toggles off (Simple = the bare rule)."""
    assert _body("TAI Aspects", mods=False, mo=False) == \
        "#showtooltip\n/cast [combat][harm,nodead] !Aspect du faucon; !Aspect du guépard"
    full = _body("TAI Aspects")
    assert full.endswith("[combat][harm,nodead] !Aspect du faucon; !Aspect du guépard")
    assert "[mod:shift] !Aspect du guépard" in full and "[mod:alt] !Aspect du singe" in full
    assert _body("TAI Aspects", level=40).count("!Aspect") == 5          # + Meute (Ctrl) at 40
    assert _body("TAI Aspects", level=12) == "#showtooltip\n/cast [mod:alt] !Aspect du singe; !Aspect du faucon"
    assert _body("TAI Aspects", level=5) == "#showtooltip\n/cast !Aspect du singe"
    m = _m("TAI Aspects")
    assert "toi" in m.why and "bot" in m.why                             # why it cannot be fully automatic


def test_aspect_guard():
    """The guard comes first in the pull key (explicit #showtooltip), after the main shot in TAI Tir."""
    mq = _body("TAI Marque")
    assert mq.startswith("#showtooltip Marque du chasseur\n/cast !Aspect du faucon\n")
    tir = _body("TAI Tir").split("\n")
    assert tir[0] == "#showtooltip" and tir[1].startswith("/cast ") and tir[2] == "/cast !Aspect du faucon"
    assert "Aspect" not in _body("TAI Marque", guard=False)
    assert "!Aspect du faucon" not in (_body("TAI Marque", level=8) or "")   # no Hawk before level 10
    # linter: a guard is exempt from « 1 GCD per press » only when it is the bare guard line
    mark, hawk, arc = T("hunters_mark"), T("aspect_hawk"), T("arcane_shot")
    assert lint_rules("HUNTER", "X", f"#showtooltip {mark}\n{GUARD_LINE}\n/cast {mark}") == []
    assert any("temps de recharge global" in e
               for e in lint_rules("HUNTER", "X", f"#showtooltip {mark}\n/cast [combat] !{hawk}\n/cast {mark}"))
    assert any("#showtooltip <sort principal>" in e for e in lint_rules("HUNTER", "X", f"#showtooltip\n{GUARD_LINE}\n/cast {arc}"))


def test_once_per_target_sequences_and_exclusive_modifiers():
    mark, serp, arc, multi = T("hunters_mark"), T("serpent_sting"), T("arcane_shot"), T("multi_shot")
    ok = f"#showtooltip {arc}\n/castsequence reset=target {mark}, {serp}, null\n/cast {arc}"
    assert lint_rules("HUNTER", "X", ok) == []
    assert any("null" in e for e in lint_rules("HUNTER", "X", f"/castsequence reset=target null, {mark}"))
    assert any("reset=" in e for e in lint_rules("HUNTER", "X", f"/castsequence {mark}, null"))
    # [mod:shift] vs [nomod:shift] lines never act on the same press; two plain GCD lines do
    assert lint_rules("HUNTER", "X", f"#showtooltip\n/castsequence [nomod:shift] reset=target {mark}, {arc}\n"
                                     f"/cast [mod:shift] {multi}") == []
    assert any("temps de recharge global" in e for e in lint_rules("HUNTER", "X", f"#showtooltip\n/cast {mark}\n/cast {arc}"))


def test_focus_supported_and_qualified():
    ref = (ROOT / "tests" / "wow" / "forever_ref.lua").read_text(encoding="utf-8")
    assert re.search(r"^FocusUnit\b", ref, re.M)                        # Forever has the focus target
    assert lint("X", "/cast [@focus,harm,nodead][] Effrayer une bête") == []
    assert any("focus" in e for e in lint("X", "/cast [@focus] Effrayer une bête"))
    assert lint("X", "/focus [mod:shift,@target,harm,nodead]") == []


def test_pro_macros():
    names = {m.name for m in macros_for("HUNTER") if m.pro}
    assert {"TAI Pro Tir", "TAI Pro Burst", "TAI Pro Panique", "TAI Pro Melee", "TAI Pro Pet", "TAI Pro Kite",
            "TAI Pro Assist", "TAI Pro Focus", "TAI Pro Rare", "TAI Pro Quete"} <= names
    for m in macros_for("HUNTER"):
        if m.pro:
            assert m.trick and not m.default and m.in_pro, m.name
    pt = _body("TAI Pro Tir")
    assert pt.split("\n")[:4] == ["#showtooltip Tir des arcanes", "/targetenemy [noharm][dead]", "/cast !Aspect du faucon",
                                  "/castsequence reset=target Marque du chasseur, Morsure de serpent, null"]
    assert "/cast !Tir automatique" in pt and "/petattack [harm,nodead,nomod:alt]" in pt
    burst = _body("TAI Pro Burst")
    assert burst.startswith("#showtooltip Tir rapide\n/cast Tir rapide") and burst.endswith("/cast Tir des arcanes")
    assert "/cast Fureur sanguinaire" in burst and "/use 13" in burst
    pan = _body("TAI Pro Panique", level=30)
    assert "/stopattack" in pan and "/petfollow" in pan and pan.endswith("/cast [nocombat] Piège givrant; Feindre la mort")
    assert _body("TAI Pro Panique", level=28) is None                   # Feign Death at 30
    mel = _body("TAI Pro Melee")
    assert "/castsequence reset=target Coupure d'ailes, null" in mel and "/cast Attaque du raptor" in mel
    assert "[mod:alt,@pet,exists,nodead] Guérison du familier" in _body("TAI Pro Pet")
    assert "@party1" in _body("TAI Pro Assist") and "@party4" in personalize(
        tokenize("HUNTER", _m("TAI Pro Assist").variants[0]), tank=4)
    assert "[@focus,harm,nodead]" in _body("TAI Pro Focus")
    assert _body("TAI Pro Rare") == "/click TreeAIMacro_rare" and _body("TAI Pro Quete") == "/click TreeAIMacro_quest"
    # replaced by a Pro macro in the « Pro » preset; still in Complet / Simple
    for name in ("TAI Tir", "TAI Marque", "TAI Melee", "TAI Pet", "TAI Burst", "TAI Feindre"):
        m = _m(name)
        assert m.default and not m.in_pro, name


def test_presets_and_new_macros():
    simple = {m.name for m in macros_for("HUNTER") if m.in_simple and applies(m, "bete")}
    assert {"TAI Tir", "TAI Marque", "TAI Aspects", "TAI Familier", "TAI Nourrir", "TAI Multi", "TAI Repas"} <= simple
    assert "TAI Pet" not in simple
    assert _body("TAI Nourrir").split("\n")[1:] == ["/cast [pet,@pet,nodead] Nourrir le familier",
                                                    "/use [pet,@pet,nodead] item:99999"]
    assert "Trait provocateur" in _body("TAI Provoc") and "Apprivoise une bête" in _body("TAI Dressage")
    assert "Connaissance des bêtes" in _body("TAI Dressage")
    morsure = _body("TAI Morsure")
    assert morsure.endswith("Morsure de serpent\n/cast !Tir automatique")   # « Morsure de serpent + auto »
    # every hunter macro × every setting it offers: ≤ 255 bytes at every level
    for m in macros_for("HUNTER"):
        for spec in ("bete", "precision", "survie"):
            if not applies(m, spec):
                continue
            for opts in option_combos(options_of("HUNTER", m)):
                for lvl in (10, 20, 28, 40, 60):
                    b = simulate_macro("HUNTER", m, spec, lvl, opts=opts)
                    if b:
                        assert len(b.encode("utf-8")) <= 255, (m.name, spec, lvl, opts)


def test_options_offered():
    assert options_of("HUNTER", _m("TAI Tir")) == "mpg"
    assert options_of("HUNTER", _m("TAI Marque")) == "mopg"
    assert "p" not in options_of("HUNTER", _m("TAI Pet"))               # pet-control macro: no pet switch
    assert "g" not in options_of("HUNTER", _m("TAI Aspects"))           # all aspects: no guard switch
    assert "t" in options_of("HUNTER", _m("TAI Pro Assist"))


@pytest.mark.skipif(not SCRATCH, reason="Forever SpellCategories / SpellCooldowns CSV absent")
def test_off_gcd_flags_match_forever_tables():
    """« o » (off the GCD) = StartRecoveryCategory 133 with StartRecoveryTime 0, or not on the GCD at all."""
    d = SCRATCH[0]
    cd, cat = {}, {}
    for r in csv.DictReader((d / "SpellCooldowns.csv").open(encoding="utf-8")):
        cd.setdefault(int(r["SpellID"]), int(r["StartRecoveryTime"]))
    for r in csv.DictReader((d / "SpellCategories.csv").open(encoding="utf-8")):
        cat.setdefault(int(r["SpellID"]), int(r["StartRecoveryCategory"]))
    for table in (SPELLS["HUNTER"], COMMON):
        for key, (sid, _n, flags) in table.items():
            if "o" in flags:
                assert not (cd.get(sid, 0) > 0 and cat.get(sid) == 133), key    # flagged off-GCD but triggers it
    for key in ("aspect_hawk", "aspect_cheetah", "aspect_monkey", "hunters_mark", "intimidation"):
        sid = SPELLS["HUNTER"][key][0]
        assert cd.get(sid) == 1500 and cat.get(sid) == 133, key               # aspects are ON the GCD in Forever


def test_data_and_window_contract():
    text = (ADDON / "Data" / "Macros.lua").read_text(encoding="utf-8")
    assert "D.guard = 13165" in text and 'f="mpg"' in text and "pro=true" in text and "sp=true" in text
    win = (ADDON / "Modules" / "Window.lua").read_text(encoding="utf-8")
    assert "p.before" in win
    src = (ADDON / "Modules" / "Macros.lua").read_text(encoding="utf-8")
    assert 'before = "modules"' in src and "useOnKeyDown" in src
    assert not (ADDON / "Modules" / "QuickFight.lua").exists()   # (5.1 CLEAN) the combat bar is removed


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor,cls", [("forever", "HUNTER"), ("modern", "HUNTER"), ("forever", "WARRIOR")])
def test_macro3_scenario(flavor, cls):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", cls,
                        "--scenario", str(ROOT / "tests/wow/macro3_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=180, cwd=ROOT)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-3000:] + r.stderr[-2000:]
