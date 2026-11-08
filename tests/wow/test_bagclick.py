"""4.1 BUGHUNT — « je peux plus bouger les trucs de l'inventaire » (real game, 4.0.4 → 4.0.6).
tests/wow/bagclick_scenario.lua on the real-client geometry: no TreeAI frame that takes the mouse (alpha 0 included)
sits on the open bags (bottom-right container frames) — the bag guard puts our frames under the bags while they are
open and gives their strata back when they close; no TreeAI code path touches the cursor (ClearCursor / Pickup* /
PlaceAction…) without a click while items are dragged (bags, bank, merchant, mailbox); Theme fades never leave a
frame shown at alpha 0."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = os.environ.get("TREEAI_WOW_ADDON") or str(ROOT / "wow_addon" / "TreeAI")
HARNESS = ROOT / "tests" / "wow" / "harness.lua"
SCENARIO = ROOT / "tests" / "wow" / "bagclick_scenario.lua"

needs_lua = pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")


def run(*extra: str) -> str:
    args = ["lua5.1", str(HARNESS), "-q", "--geometry", "ready=1", "--flavor", "forever", "--class", "HUNTER",
            "--level", "29", *extra, "--scenario", str(SCENARIO), ADDON]
    env = dict(os.environ, TREEAI_BAGCLICK_DUMP="1")
    env.pop("TREEAI_WOW_FORCE_FLAVOR", None)
    r = subprocess.run(args, capture_output=True, text=True, timeout=240, cwd=ROOT, env=env)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-6000:] + r.stderr[-2000:]
    return r.stdout


@needs_lua
@pytest.mark.parametrize("profile", ["tout", "sur"])
def test_nothing_of_ours_on_the_open_bags(profile):
    out = run("--profile", profile)
    assert "BAGCLICK" in out and "cursorCalls=0" in out and "covering=0" in out


@needs_lua
def test_without_storm():
    out = run("--no-storm", "--profile", "tout")
    assert "covering=0" in out
