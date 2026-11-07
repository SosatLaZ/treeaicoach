"""Core comms vs WoW Forever addon-message limits (token bucket, result codes, dungeon pause) and Squad's
quest-log diffs. Runs the Lua harness scenarios; fake names only."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
HERE = Path(__file__).parent

pytestmark = pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 not installed")


@pytest.mark.parametrize("flavor", ["modern", "legacy"])
def test_comms_rate_limit_scenario(flavor):
    r = subprocess.run(["lua5.1", str(HERE / "harness.lua"), "-q", "--flavor", flavor, "--scenario",
                        str(HERE / "comms_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-3000:] + r.stderr[-2000:]


def test_core_checks_send_result():
    src = (ADDON / "Core.lua").read_text(encoding="utf-8")
    assert "BURST, REFILL = 8, 1" in src
    assert "classify(sendRaw(" in src
