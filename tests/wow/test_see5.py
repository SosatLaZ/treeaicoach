"""5.0 INTEG — « ce que le joueur voit » (tests/wow/see5_scenario.lua).

Level-30 hunter in a group of 4 on the WoW: Forever mock, default profile, real-client geometry. The texts on screen
are dumped moment by moment — login in town, opening bags, merchant, trainer, taking a quest, killing a quest mob,
loot, a crowd of 40 players, quest guidance off / on, the chosen zone kept — and what must never happen is checked:
nothing in the chat, no « nil » or raw item number in a line, one sell / repair card at the merchant, one verdict line
per item tooltip, no quest advice left when guidance is off, zero blocked action."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
LUA = shutil.which("lua5.1")


@pytest.mark.skipif(not LUA, reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["forever", "modern"])
def test_what_the_player_sees(flavor: str) -> None:
    r = subprocess.run([LUA, str(ROOT / "tests/wow/harness.lua"), "-q", "--no-storm", "--geometry", "ready=1,w=1920,h=1080",
                        "--profile", "sur", "--flavor", flavor, "--class", "HUNTER", "--level", "30",
                        "--scenario", str(ROOT / "tests/wow/see5_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=300, cwd=ROOT)
    out = r.stdout
    assert r.returncode == 0 and "RESULT ok" in out, out[-6000:] + r.stderr[-2000:]
    for title in ("1. Connexion en ville", "3. Chez un marchand", "4. Chez le maître", "5. Une quête prise",
                  "6. Un monstre de quête tué", "7. Butin ramassé", "8. Foule : 40 joueurs", "9. Guidage des quêtes coupé"):
        assert "=== " + title in out, title
    assert "[CHAT !]" not in out and "SEE5 chat:" not in out
    assert "0 arrêt" not in out                                    # « 5 courses, 0 arrêt · < 1 min »
    assert "[carte sous le marchand — Sacs] Vendre 1 objet" in out
    assert "Guidage quêtes OFF" in out and "zone choisie, après guidage coupé" in out
