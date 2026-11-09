"""V5H « tout ce qui n'apparaît pas »: every default on-screen TreeAI element (strip, compass, minimap button, alert)
shown, on screen, alpha > 0, size > 0 after login, /reload and combat, mode sûr profile; « /tai barre » repairs."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"


def run(mode, sv_dump=None, sv_load=None):
    args = ["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--no-storm", "--geometry", "ready=1",
            "--flavor", "forever", "--class", "HUNTER", "--level", "30", "--profile", "sur",
            "--scenario", str(ROOT / "tests/wow/visible_scenario.lua")]
    if sv_dump:
        args += ["--dump", str(sv_dump)]
    if sv_load:
        args += ["--savedvars", str(sv_load)]
    args.append(str(ADDON))
    env = dict(os.environ, TREEAI_VISIBLE_MODE=mode)
    r = subprocess.run(args, capture_output=True, text=True, timeout=300, cwd=ROOT, env=env)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-6000:] + r.stderr[-2000:]
    return r.stdout


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
def test_visible_login_combat_reload(tmp_path):
    sv = tmp_path / "sv.lua"
    out = run("login", sv_dump=sv)
    for tag in ("connexion", "combat", "après combat"):
        for n in ("TreeAIStrip", "TreeAICompassFrame", "TreeAIMinimapButton"):
            assert f"VISIBLE {tag} ok {n}" in out, (tag, n)
    assert "VISIBLE alerte ok TreeAIAlert" in out
    assert "VISIBLE /tai barre (module coupé) ok TreeAIStrip" in out
    out = run("reload", sv_load=sv)
    assert "VISIBLE combat ok TreeAIStrip" in out
