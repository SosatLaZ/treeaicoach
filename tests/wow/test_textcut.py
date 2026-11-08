"""4.0: ONE UTF-8-safe text cut for the whole addon (Core.lua TreeAI.Cut). The byte cuts of 17 modules
(`s:sub(1, n - 1) .. "…"`) split accented letters: « Les loups-garous de la for\\xc3… » on screen and in the app.
- textcut_spec.lua: accented strings cut at every length (« … », « ... », dry cut) -> valid UTF-8, <= n characters;
- textcut_scenario.lua: the whole addon with accents everywhere: every text set on a frame stays valid UTF-8;
- static: no byte cut left in the addon, every module's `cut` is Core's."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
LUA = shutil.which("lua5.1")
ALLOW = ["--allow", "LibQuestieDB", "--allow", "QuestDB", "--allow", "NpcDB", "--allow", "ObjectDB",
         "--allow", "ItemDB", "--allow", "TreeAIPlan", "--allow", "IsQuestFlaggedCompleted"]


@pytest.mark.skipif(not LUA, reason="lua5.1 absent")
def test_cut_spec_every_length_is_valid_utf8():
    r = subprocess.run([LUA, str(ROOT / "tests/wow/textcut_spec.lua"), str(ADDON / "Core.lua")],
                       capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
    assert " 0 failures" in r.stdout


@pytest.mark.skipif(not LUA, reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["forever", "modern"])
def test_every_text_on_screen_is_valid_utf8(flavor):
    r = subprocess.run([LUA, str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", "HUNTER",
                        "--level", "28", *ALLOW, "--scenario", str(ROOT / "tests/wow/textcut_scenario.lua"),
                        str(ADDON)], capture_output=True, text=True, timeout=180, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-5000:] + r.stderr[-2000:]
    assert "RESULT ok" in r.stdout


# a byte cut: `s:sub(1, n - 1) .. "…"`, `if #s > n then s = s:sub(1, n)`, `if TextLen(s) > n then s = s:sub(1, n)`,
# `name = name:sub(1, 40)`
BYTE_CUT = (
    re.compile(r':sub\(1,[^()]*(\([^()]*\))?[^()]*\)\s*(:gsub\([^)]*\)\s*)?\.\.\s*"(…|\.\.\.)"'),
    re.compile(r'#\s*(\w+)\s*>\s*[\w.]+\s+then\s+(return\s+)?(\w+\s*=\s*)?\1:sub\(1,(?!\s*-)'),
    re.compile(r'TextLen\(\w+\)\s*>\s*\w+\s+then\s+\w+\s*=\s*\w+:sub\(1,'),
    re.compile(r':sub\(1,\s*\d{2,}\)(?!\s*[=~]=)'),        # name = name:sub(1, 40) (a compare is fine)
)
# Group.lua utf8cut = a BYTE budget for addon messages (the wire is bytes), already never splitting a letter
ALLOWED = {("Modules/Group.lua", "utf8cut"), ("Core.lua", "cutText")}


def _lua_files():
    return sorted(p for p in ADDON.rglob("*.lua") if "Data" not in p.parts)


def test_no_byte_cut_left_in_the_addon():
    bad = []
    for p in _lua_files():
        rel = p.relative_to(ADDON).as_posix()
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            code = re.sub(r"--.*$", "", line)
            if any(rx.search(code) for rx in BYTE_CUT):
                bad.append(f"{rel}:{n}: {line.strip()}")
    assert not bad, "byte cuts left (use T.Cut):\n" + "\n".join(bad)


def test_every_module_cut_is_cores():
    defs = []
    for p in _lua_files():
        rel = p.relative_to(ADDON).as_posix()
        src = p.read_text(encoding="utf-8")
        for m in re.finditer(r"^local (?:function )?(cut\w*|utf8cut)\b(.*)$", src, re.M):
            name, rest = m.group(1), m.group(2)
            if (rel, name) in ALLOWED:
                continue
            if rest.strip().startswith("= T.Cut") or "T.Cut(" in rest:
                continue
            defs.append(f"{rel}: local {name}{rest[:80]}")
    assert not defs, "module cuts that are not Core's T.Cut:\n" + "\n".join(defs)
    core = (ADDON / "Core.lua").read_text(encoding="utf-8")
    assert "TreeAI.Cut = cutText" in core
    assert core.index("TreeAI.Cut = cutText") < core.index("TreeAI.ShortText = shortText")
