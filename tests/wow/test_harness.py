"""Lua 5.1 harness: loads an addon TOC on the WoW API mock and drives it with events.

Skipped when no ``lua5.1`` interpreter is installed (``apt-get install -y lua5.1``).
The real addon (``wow_addon/TreeAI``, or ``$TREEAI_WOW_ADDON``) is tested once it exists; missing
TOC files (modules not merged yet) are skipped by the harness with a note, not failed.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
HARNESS = HERE / "harness.lua"
SAMPLE = HERE / "fixtures" / "SampleAddon"
LEAKY = HERE / "fixtures" / "bad" / "LeakyAddon"
LUA = shutil.which("lua5.1")

pytestmark = pytest.mark.skipif(LUA is None, reason="lua5.1 not installed")


def run(target: Path, *args: str) -> tuple[int, str]:
    res = subprocess.run([LUA, str(HARNESS), *args, str(target)], capture_output=True, text=True,
                         timeout=120, cwd=REPO)
    return res.returncode, res.stdout + res.stderr


def result_line(out: str) -> dict[str, str]:
    m = re.search(r"^RESULT (\w+)(.*)$", out, re.M)
    assert m, out
    fields = dict(re.findall(r"(\w+)=(\S+)", m.group(2)))
    fields["status"] = m.group(1)
    return fields


@pytest.mark.parametrize("flavor", ["modern", "legacy"])
def test_sample_addon_passes_with_scenario(flavor):
    rc, out = run(SAMPLE, "--flavor", flavor, "--scenario", str(SAMPLE / "scenario.lua"))
    res = result_line(out)
    assert rc == 0 and res["status"] == "ok", out
    assert res["loaded"] == "3" and res["skipped"] == "1", out
    assert "SKIP missing file Modules/NotMergedYet.lua" in out


def test_bad_addon_is_caught():
    rc, out = run(LEAKY, "-q")
    res = result_line(out)
    assert rc == 1 and res["status"] == "fail"
    assert "LEAK global leakedCounter" in out and "LEAK global helperWithoutLocal" in out
    assert "VIOLATION [combat] CreateMacro" in out and "Leaky.lua:9" in out
    assert "attempt to index local 't'" in out


def test_missing_toc_is_a_skip(tmp_path):
    rc, out = run(tmp_path)
    assert rc == 2 and "RESULT skip" in out


def test_savedvars_dump_roundtrip(tmp_path):
    dump = tmp_path / "SampleAddon.lua"
    rc, out = run(SAMPLE, "-q", "--dump", str(dump))
    assert rc == 0, out
    text = dump.read_text(encoding="utf-8")
    assert text.lstrip().startswith("SampleAddonDB = {") and '["class"] = "HUNTER"' in text
    # restoring it bumps the login counter, as a /reload would
    dump2 = tmp_path / "again.lua"
    rc, out = run(SAMPLE, "-q", "--no-storm", "--savedvars", str(dump), "--dump", str(dump2))
    assert rc == 0, out
    assert '["logins"] = 2' in dump2.read_text(encoding="utf-8")
    # the TreeAI-side parser (agent A) reads it, when present
    try:
        from treeaicoach.wow import savedvars
    except ImportError:
        return
    parsed = None
    for name in ("parse", "loads", "parse_savedvariables", "parse_text", "parse_lua"):
        fn = getattr(savedvars, name, None)
        if callable(fn):
            try:
                parsed = fn(text)
            except TypeError:
                continue
            break
    if parsed is not None:
        assert "HUNTER" in repr(parsed)


def _real_addon_dirs() -> list[Path]:
    dirs = [REPO / "wow_addon" / "TreeAI"]
    if os.environ.get("TREEAI_WOW_ADDON"):
        dirs.insert(0, Path(os.environ["TREEAI_WOW_ADDON"]))
    return [d for d in dirs if d.is_dir() and any(d.glob("*.toc"))]


@pytest.mark.parametrize("flavor", ["modern", "legacy"])
@pytest.mark.parametrize("cls", ["HUNTER", "MAGE"])
def test_real_treeai_addon(flavor, cls):
    dirs = _real_addon_dirs()
    if not dirs:
        pytest.skip("wow_addon/TreeAI not merged yet")
    rc, out = run(dirs[0], "--flavor", flavor, "--class", cls, "--allow", "TreeAI_Locale")
    res = result_line(out)
    assert rc == 0 and res["status"] == "ok", out
    assert int(res["loaded"]) >= 1
    assert "/treeai" in out  # slash registered
