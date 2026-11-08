"""NEXT10: modules Session (bilan, fin de session, pourquoi je meurs, pause) + Errands (courrier, vol, entraîneur,
appel prêt, limite d'instances) — scenario on the real addon, three API flavours, + static ToS checks."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
SRCS = [ADDON / "Modules" / "Session.lua", ADDON / "Modules" / "Errands.lua"]


def _code(p):
    return "\n".join(re.sub(r"--.*$", "", ln) for ln in p.read_text(encoding="utf-8").splitlines())


def test_toc_order():
    toc = [ln.strip() for ln in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert toc.index("Modules\\Fight.lua") < toc.index("Modules\\Session.lua") < toc.index("Modules\\Errands.lua")


def test_never_acts_for_the_player():
    for p in SRCS:
        code = _code(p)
        for bad in ("TakeTaxiNode", "BuyTrainerService", "TakeInboxMoney", "TakeInboxItem", "AutoLootMailItem",
                    "ConfirmReadyCheck", "ResetInstances", "SendChatMessage", "DeleteInboxItem", "SendMail("):   # never sends
            assert bad not in code, (p.name, bad)
        assert "OpenAllMail\")" not in code.replace('g("OpenAllMail") ~= nil', ""), p.name


def test_options_have_sections():
    for p in SRCS:
        for ln in p.read_text(encoding="utf-8").splitlines():
            if 'type = "toggle"' in ln or 'type = "range"' in ln:
                assert "section =" in ln, ln


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
def test_syntax_no_globals():
    for p in SRCS:
        assert subprocess.run(["luac5.1", "-p", str(p)], capture_output=True).returncode == 0
        assert "SETGLOBAL" not in subprocess.run(["luac5.1", "-l", "-p", str(p)], capture_output=True, text=True).stdout


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor,cls", [("modern", "HUNTER"), ("legacy", "MAGE"), ("forever", "HUNTER"), ("forever", "WARRIOR")])
def test_next10_scenario(flavor, cls):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", cls,
                        "--scenario", str(ROOT / "tests/wow/next10_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout
