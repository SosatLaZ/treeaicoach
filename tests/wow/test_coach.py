"""ADVICE 4.0.1: Modules/Coach.lua — « Conseils pro » : the move an expert leveler / hunter coach would make, at the
moment it applies, built on data the client lets addons read out of combat (vitals read out of combat, auras, bags,
pet bar, XP / rested, quest log, movement, the player's own measured fights) and on rules that are TRUE (WoW: Forever
1.60.1 client tables: spells, items, ammo DPS, « Bien nourri » XP foods; Classic rules marked « à vérifier »).
One Coach line at a time in « Maintenant » (highest gain wins), a « pourquoi » tooltip with numbers, a cooldown and an
off switch per tip, never in combat, never chat / sound / automation; page « Conseils pro » = what is true now, every
tip (ON / OFF) and the expert techniques to apply, by theme. Safe mode ON, ≤ 30 kinstr/min in session_sim."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
SRC = (ADDON / "Modules" / "Coach.lua").read_text(encoding="utf-8")


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["forever", "modern", "legacy"])
@pytest.mark.parametrize("cls", ["HUNTER", "WARRIOR", "MAGE"])
def test_coach_scenario(flavor, cls):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", cls,
                        "--scenario", str(ROOT / "tests/wow/coach_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=180, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "RESULT ok" in r.stdout


def test_registered_safe_and_in_toc():
    toc = (ADDON / "TreeAI.toc").read_text(encoding="utf-8").split()
    assert "Modules\\Coach.lua" in toc
    assert toc.index("Modules\\Coach.lua") > toc.index("Modules\\WindowPages.lua")   # the page needs T.Window
    assert "T.SAFE_MODULES.Coach = true" in SRC
    assert re.search(r"T\.MODULE_INFO\.Coach = \{ lite = true, desc = ", SRC)
    assert "combat = true" not in SRC.split("T.MODULE_INFO.Coach")[1].split("\n")[0]


def test_every_tip_has_a_check_an_option_and_a_theme():
    block = SRC.split("local TIPS = {", 1)[1].split("local TIP = {}", 1)[0]
    tips = re.findall(r'^\s*\{ "(\w+)", "(\w+)", "[^"]+",', block, re.M)
    keys = [k for k, _ in tips]
    assert len(keys) >= 18 and len(set(keys)) == len(keys)
    themes = set(re.findall(r'\{ id = "(\w+)", label = "', SRC.split("local THEME = {}", 1)[0]))
    assert themes == {"pull", "rest", "pet", "quest", "money", "group", "dungeon", "safety"}
    for key, theme in tips:
        assert theme in themes, key
        assert f"CHECK.{key} = function" in SRC, f"{key}: no check"
    assert "defaults[tp.key] = true" in SRC and 'type = "toggle", label = tp.label' in SRC   # off switch per tip


def test_tip_lines_are_actions_without_unknowns():
    texts = []
    for m in re.finditer(r'return \{ text = (.*?),\s*why = ', SRC, re.S):   # the line of each tip, up to its « pourquoi »
        texts += re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))
    texts = [t for t in texts if len(t) > 12]
    assert len(texts) >= 20
    verbs = ("Mange", "Bois", "Bande-toi", "Enchaîne", "Laisse", "vise", "Nouveau", "Mets", "Coupe", "Passe", "fais-le",
             "tue ", "Rends", "Équipe", "attends", "allez")
    for t in texts:
        assert any(v in t for v in verbs), t
        assert "?" not in t and "nil" not in t, t


def test_no_automation_no_chat_no_sound():
    for bad in ("CastSpell", "UseContainerItem", "PickupContainerItem", "BuyMerchantItem", "TargetUnit", "SetBinding",
                "SendChatMessage", "TogglePetAutocast", "PetPassiveMode", "PetDefensiveMode", "CreateMacro", "EditMacro",
                ":Print(", "PlaySound", "UIErrorsFrame"):
        assert bad not in SRC, bad
    assert "sound = false, chat = false" in SRC
    assert re.search(r"M:Suggest\(key, r\.text, 2,", SRC)                 # prio 2: never the corner alert (prio 3)


def test_one_line_at_a_time_and_nothing_in_combat():
    assert "M.MIN_SHOW = 20" in SRC and "M.GAP = 15" in SRC and "M.MAX_10MIN = 6" in SRC
    assert "if shown then hide(shown, false) end          -- en combat" in SRC
    assert "if fighting then return end" in SRC


def test_client_data_matches_the_generated_tables():
    """Bandage heals and XP-food levels come from the WoW: Forever client tables (Data/Consumables.lua is generated)."""
    cons = (ADDON / "Data" / "Consumables.lua").read_text(encoding="utf-8")
    band = cons.split("bandage = {", 1)[1].split("},\n  ammo", 1)[0]
    heal = {int(i): int(v) for i, v in re.findall(r"\{(\d+), \d+, (\d+), \"\"\}", band)}
    for item, value in re.findall(r"\[(\d+)\] = \{ (\d+), \d+ \}", SRC.split("local BANDAGE = ", 1)[1].split("\n\n", 1)[0]):
        assert heal[int(item)] == int(value), item
    ammo = cons.split("ammo = {", 1)[1]
    ilvl = {int(i): int(lv) for i, _, lv in re.findall(r"\{(\d+), (\d+), (\d+), \"(?:arrow|bullet)\"\}", ammo)}
    for item, lv in re.findall(r"\{ (\d+), (\d+), \d+, \d+, \"", SRC.split("local AMMO = {", 1)[1].split("local AMMO_DPS", 1)[0]):
        assert ilvl[int(item)] == int(lv), item
    xpfood = SRC.split("local XPFOOD = {", 1)[1].split("}", 1)[0]
    assert len(re.findall(r"\[\d+\] = \d+", xpfood)) >= 100 and "[2685] = 15" in xpfood
    assert "local WELLFED_XP = 1243969" in SRC


def test_card_notice_never_steals_another_notice():
    assert "W.CARD_NOTICE[id] = W.CARD_NOTICE[id] or {}" in SRC
    assert 'if k ~= id and not k:find("^Coach:") and S[k] then return end' in SRC
    assert 'key = "card", type = "toggle"' in SRC


def test_page_and_options_family():
    assert 'W.AddPage({ id = "coach", label = "Conseils pro"' in SRC
    assert 'if p.id == "tools" then at = i + 1 break end' in SRC          # after « Outils » in the navigation
    assert "T.OPTIONS_FAMILY[2]" in SRC and '"Coach"' in SRC


def test_session_sim_caps_coach():
    sim = (ROOT / "tests" / "wow" / "session_sim.lua").read_text(encoding="utf-8")
    assert re.search(r"Coach = 30[ ,]", sim)
    assert "coachNowPct" in sim and "coachShowsPer10Min" in sim and "coachTwoAtOnce = 0" in sim


def test_edge2_turnin_moved_here():
    edge = (ADDON / "Modules" / "Edge2.lua").read_text(encoding="utf-8")
    assert '"turninLevel"' not in edge and "CHECK.turninLevel = function" in SRC
