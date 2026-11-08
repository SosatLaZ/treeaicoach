"""Route planner, addon side (Modules/Export.lua + Modules/Planner.lua): harness scenarios on the real addon,
a 2-client plan sync check, TOC wiring and the TreeAI_Plan data-addon stub (fake names only)."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).parent
ADDON = ROOT / "wow_addon" / "TreeAI"
PLAN_ADDON = ROOT / "wow_addon" / "TreeAI_Plan"
ALLOW = ["LibQuestieDB", "QuestDB", "NpcDB", "ObjectDB", "ItemDB", "TreeAIPlan", "TomTom", "ShortestPathForever",
         "UiMapPoint", "IsQuestFlaggedCompleted", "C_SuperTrack"]
LUA = shutil.which("lua5.1")


def run(flavor: str = "modern", env: dict | None = None) -> subprocess.CompletedProcess:
    cmd = [LUA, str(HERE / "harness.lua"), "-q", "--flavor", flavor]
    for a in ALLOW:
        cmd += ["--allow", a]
    cmd += ["--scenario", str(HERE / "planner_scenario.lua"), str(ADDON)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=ROOT,
                          env={**os.environ, **(env or {})})


@pytest.mark.skipif(not LUA, reason="lua5.1 not installed")
@pytest.mark.parametrize("flavor", ["modern", "legacy"])
def test_planner_scenario(flavor):
    r = run(flavor)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-4000:] + r.stderr[-2000:]


@pytest.mark.skipif(not LUA, reason="lua5.1 not installed")
def test_planner_two_clients_sync(tmp_path):
    f = tmp_path / "pln.txt"
    made = str(int(time.time()))                     # both clients load the same (fresh) plan
    a = run(env={"PLANNER_MODE": "syncA", "PLANNER_SYNC_FILE": str(f), "PLANNER_MADE": made})
    assert a.returncode == 0 and "RESULT ok" in a.stdout, a.stdout[-3000:]
    msgs = f.read_text(encoding="utf-8").split()
    assert msgs and all(len(m.encode()) <= 255 and "|" not in m for m in msgs)
    b = run(env={"PLANNER_MODE": "syncB", "PLANNER_SYNC_FILE": str(f), "PLANNER_MADE": made})
    assert b.returncode == 0 and "RESULT ok" in b.stdout, b.stdout[-3000:]


def test_toc_wiring():
    lines = [x.strip() for x in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert any(l.startswith("## OptionalDeps:") and "TreeAI_Plan" in l for l in lines)
    q, e, p = (lines.index(f"Modules\\{n}.lua") for n in ("Quests", "Export", "Planner"))
    assert q < e < p, "Export then Planner right after Quests"


def test_plan_addon_stub_is_pure_data():
    toc = (PLAN_ADDON / "TreeAI_Plan.toc").read_text(encoding="utf-8")
    assert "Plan.lua" in toc.split() and "## Interface:" in toc and "SavedVariables" not in toc
    code = [ln for ln in (PLAN_ADDON / "Plan.lua").read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("--")]
    assert code == [], "the stub defines nothing (the app writes TreeAIPlan = {...})"


def test_planner_never_touches_blizzard_quest_ui():
    for name in ("Planner.lua", "Export.lua"):
        src = (ADDON / "Modules" / name).read_text(encoding="utf-8")
        code = "\n".join(ln.split("--")[0] for ln in src.splitlines())
        for bad in ("SelectQuestLogEntry", "SetSelectedQuest", "QuestMapFrame", "ShowUIPanel", "QuestLogPushQuest",
                    "hooksecurefunc", "SetParent(", "COMBAT_LOG_EVENT"):
            assert bad not in code, f"{name}: {bad}"
