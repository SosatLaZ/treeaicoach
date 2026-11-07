"""Contract tests for the Python WoW modules (savedvars / combatlog / macro linter).

Written against the brief's contracts before those modules are merged: each test skips when its
module (or a recognisable entry point) is absent, and checks only behaviour every sane
implementation must have — parse real-looking data, never execute or raise on hostile input,
enforce Blizzard's 255-char macro limit. Fake names only.
"""
from __future__ import annotations

import importlib
import pkgutil

import pytest


def _module(name: str):
    try:
        return importlib.import_module(name)
    except ImportError:
        pytest.skip(f"{name} not merged yet")


def _entry(mod, names):
    for n in names:
        fn = getattr(mod, n, None)
        if callable(fn):
            return fn
    pytest.skip(f"{mod.__name__}: none of {names}")


# --- SavedVariables parser (agent A) ------------------------------------------------------------
SV_TEXT = """
TreeAIDB = {
\t["version"] = 3,
\t["chars"] = {
\t\t["Testeur-Royaume-Test"] = {
\t\t\t["class"] = "HUNTER",
\t\t\t["level"] = 26,
\t\t\t["zone"] = "Contreforts de Hautebrande",
\t\t\t["gold"] = 12.5,
\t\t\t["quests"] = {
\t\t\t\t"Le fermier Ray", -- [1]
\t\t\t\t"Le courrier \\"de\\" Southshore", -- [2]
\t\t\t},
\t\t\t["dead"] = false,
\t\t\t["note"] = nil,
\t\t},
\t},
}
"""
SV_PARSERS = ("parse", "loads", "parse_savedvariables", "parse_text", "parse_lua")


def _sv_parse(text):
    fn = _entry(_module("treeaicoach.wow.savedvars"), SV_PARSERS)
    return fn(text)


def test_savedvars_parses_wow_output():
    out = _sv_parse(SV_TEXT)
    r = repr(out)
    assert "HUNTER" in r and "26" in r and "Le fermier Ray" in r
    assert "Contreforts de Hautebrande" in r


@pytest.mark.parametrize("hostile", [
    'TreeAIDB = os.execute("echo pwned")',
    'TreeAIDB = { ["x"] = loadstring("return 1")() }',
    "TreeAIDB = {{{{{{{{",
    "\x00\xff garbage",
    "TreeAIDB = {" + "{" * 5000 + "}" * 5000 + "}",
    "",
])
def test_savedvars_hostile_input_never_raises(hostile):
    # never executes anything; a clean SavedVarsError (strict parser) is fine, nothing else
    mod = _module("treeaicoach.wow.savedvars")
    err = getattr(mod, "SavedVarsError", None)
    try:
        _sv_parse(hostile)
    except Exception as exc:
        assert err is not None and isinstance(exc, err), repr(exc)


# --- combat log parser (agent E) ----------------------------------------------------------------
CL_LINES = [
    '11/7 21:14:03.123  SWING_DAMAGE,Player-0000-00000001,"Testeur",0x511,0x0,'
    'Creature-0-0-0-0-2351-0001,"Ours enragé",0xa48,0x0,85,-1,1,0,0,0,nil,nil,nil',
    '11/7 21:14:04.500  SPELL_DAMAGE,Player-0000-00000001,"Testeur",0x511,0x0,'
    'Creature-0-0-0-0-2351-0001,"Ours enragé",0xa48,0x0,3044,"Tir des arcanes",0x40,120,-1,64,0,0,0,nil,nil,nil',
    '11/7 21:14:09.001  UNIT_DIED,0000000000000000,nil,0x80000000,0x80000000,'
    'Pet-0-0-0-0-1-0001,"Crocdur",0x1111,0x0',
]
CL_PARSERS = ("parse_line", "parse", "parse_event", "parse_combatlog_line")


def _cl_parse(line):
    fn = _entry(_module("treeaicoach.wow.combatlog"), CL_PARSERS)
    return fn(line)


@pytest.mark.parametrize("line", CL_LINES)
def test_combatlog_parses_classic_lines(line):
    ev = _cl_parse(line)
    assert ev is not None
    sub = line.split("  ", 1)[1].split(",", 1)[0]
    assert sub in repr(ev)


@pytest.mark.parametrize("bad", ["", "garbage", "11/7 21:14:03.123  ", "11/7 SWING_DAMAGE,,,,", "\x00" * 50])
def test_combatlog_bad_lines_never_raise(bad):
    _cl_parse(bad)


# --- macro linter (agent C) ---------------------------------------------------------------------
LINT_NAMES = ("lint", "lint_macro", "lint_body", "validate", "validate_macro", "check_macro")


def _linter():
    pkg = _module("treeaicoach.wow.macros")
    mods = [pkg]
    for info in pkgutil.iter_modules(getattr(pkg, "__path__", [])):
        try:
            mods.append(importlib.import_module(f"{pkg.__name__}.{info.name}"))
        except ImportError:
            continue
    for mod in mods:
        for n in LINT_NAMES:
            fn = getattr(mod, n, None)
            if callable(fn):
                return fn
    pytest.skip("no macro linter entry point found")


def _is_ok(result) -> bool:
    if isinstance(result, bool):
        return result
    if isinstance(result, (list, tuple, set, dict)):
        if isinstance(result, tuple) and result and isinstance(result[0], bool):
            return result[0]
        return len(result) == 0
    if result is None:
        return True
    return bool(getattr(result, "ok", not getattr(result, "errors", [])))


def test_macro_linter_enforces_255_chars():
    lint = _linter()
    try:
        good = lint("#showtooltip\n/cast [@pettarget,exists] Guérison du familier")
        bad = lint("#showtooltip\n/cast " + "Tir automatique; " * 20)
    except TypeError:
        pytest.skip("linter signature differs (needs more than a body)")
    assert _is_ok(good)
    assert not _is_ok(bad)
