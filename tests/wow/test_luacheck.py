"""Static Lua checks: syntax (luac5.1 -p), global leaks, ToS rules, for every .lua of the addon."""
from __future__ import annotations

from pathlib import Path

import sys

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import luacheck  # noqa: E402  (tests/wow/luacheck.py)

LUAC = luacheck.find_luac()
pytestmark = pytest.mark.skipif(LUAC is None, reason="luac5.1 not installed")


def test_all_lua_files_clean():
    files, findings = luacheck.check_paths(luac=LUAC)
    assert files, "no .lua file found"
    assert findings == []


def test_bad_fixture_detected():
    _, findings = luacheck.check_paths([HERE / "fixtures" / "bad"], luac=LUAC)
    text = "\n".join(findings)
    assert "LEAK global 'leakedCounter'" in text
    assert "LEAK global 'helperWithoutLocal'" in text
    assert "InCombatLockdown" in text


def test_syntax_error_and_forbidden_api(tmp_path):
    (tmp_path / "Broken.lua").write_text("local x = = 1\n", encoding="utf-8")
    (tmp_path / "Bot.lua").write_text(
        "local function go() CastSpellByName('Tir automatique') end\n"
        "local f = function() _G.RunMacroText('/cast x') end\n"
        "_G.SomeLeak = 1\n", encoding="utf-8")
    _, findings = luacheck.check_paths([tmp_path], luac=LUAC)
    text = "\n".join(findings)
    assert "SYNTAX" in text
    assert "'CastSpellByName'" in text and "'RunMacroText'" in text
    assert "'_G.SomeLeak'" in text


def test_toc_savedvariables_and_slash_are_allowed(tmp_path):
    (tmp_path / "Foo.toc").write_text("## SavedVariables: FooDB, FooCharDB\nFoo.lua\n", encoding="utf-8")
    (tmp_path / "Foo.lua").write_text(
        "Foo = {}\nFooDB = FooDB or {}\nFooCharDB = {}\nSLASH_FOO1 = '/foo'\nTreeAIDB = TreeAIDB or {}\n",
        encoding="utf-8")
    _, findings = luacheck.check_paths([tmp_path], luac=LUAC)
    assert findings == []
