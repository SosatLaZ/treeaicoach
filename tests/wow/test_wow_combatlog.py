"""WoW combat log parser + live tail (synthetic logs, fake names only)."""
from pathlib import Path

from treeaicoach.wow import combatlog as cl

FIX = Path(__file__).parent / "fixtures" / "combatlog_hunter_fr.txt"

ME = 'Player-9999-0A1B2C3D,"Sylvebrume-Brumeval",0x511,0x0'
MOB = 'Creature-0-9999-0-321-2420-00002B1,"Défenseur de Hautebrande",0xa48,0x0'


def _lines():
    return FIX.read_text(encoding="utf-8").splitlines()


def test_header_line():
    h = cl.parse_line(_lines()[0])
    assert isinstance(h, cl.LogHeader)
    assert h.version == 9 and h.advanced is True and h.build == "1.15.8" and h.project_id == 2


def test_fixture_parses_every_event_line():
    evs = cl.parse_lines(_lines())
    assert len(evs) == len(_lines()) - 1
    kinds = {e.event for e in evs}
    assert {"RANGE_DAMAGE", "SWING_DAMAGE", "SPELL_DAMAGE", "UNIT_DIED", "SPELL_CAST_START",
            "SPELL_AURA_BROKEN_SPELL", "ENVIRONMENTAL_DAMAGE", "SPELL_HEAL"} <= kinds


def test_classic_advanced_damage_fields():
    ev = cl.parse_lines(_lines())[1]   # auto shot on the bear
    assert ev.event == "RANGE_DAMAGE" and ev.spell_id == 75 and ev.spell_name == "Tir automatique"
    assert ev.amount == 188 and ev.overkill == 0
    assert ev.hp == 1112 and ev.max_hp == 1300 and ev.info_guid == ev.dst_guid
    assert cl.is_me(ev.src_flags, ev.src_guid) and cl.is_hostile(ev.dst_flags)
    assert cl.short_name(ev.src_name) == "Sylvebrume"


def test_crit_overkill_pet_and_env():
    evs = cl.parse_lines(_lines())
    claw = next(e for e in evs if e.spell_name == "Griffe")
    assert claw.critical and claw.amount == 92 and cl.is_my_pet(claw.src_flags, claw.src_guid)
    kill = next(e for e in evs if e.overkill > 0)
    assert kill.amount == 380 and kill.overkill == 200 and kill.critical
    env = next(e for e in evs if e.event == "ENVIRONMENTAL_DAMAGE")
    assert env.env_type == "DROWNING" and env.amount == 82 and env.hp_pct is not None


def test_aura_broken_and_cast_start():
    evs = cl.parse_lines(_lines())
    br = next(e for e in evs if e.event == "SPELL_AURA_BROKEN_SPELL")
    assert br.spell_id == 3355 and br.spell_name == "Piège givrant"
    assert br.extra_spell_id == 116 and br.extra_spell_name == "Éclair de givre" and br.aura_type == "DEBUFF"
    cs = next(e for e in evs if e.event == "SPELL_CAST_START")
    assert cs.spell_name == "Soins inférieurs" and cl.is_hostile(cs.src_flags) and cs.dst_name == ""


def test_basic_logging_without_advanced_block():
    line = f'11/7 21:14:03.123  SWING_DAMAGE,{MOB},{ME},250,-1,1,0,0,0,nil,nil,nil,nil'
    ev = cl.parse_line(line)
    assert ev.amount == 250 and ev.overkill == 0 and ev.hp == -1 and ev.hp_pct is None
    spell = f'11/7 21:14:04.000  SPELL_DAMAGE,{ME},{MOB},14281,"Tir des Arcanes",0x40,156,-1,64,0,0,0,1,nil,nil'
    ev = cl.parse_line(spell)
    assert ev.amount == 156 and ev.critical and ev.school == 0x40


def test_retail_style_timestamp_and_17_field_advanced_block():
    adv = "Creature-0-1-2-3-4-5,0000000000000000,9000,9000,100,0,500,0,1,0,0,0,1.0,2.0,1424,0.5,30"
    line = (f'11/7/2026 21:14:03.1234-5  SWING_DAMAGE,Creature-0-1-2-3-4-5,"Ogre, le Brutal",0xa48,0x0,{ME},'
            f'{adv},412,400,-1,1,0,0,0,nil,nil,nil,nil')
    ev = cl.parse_line(line)
    assert ev.src_name == "Ogre, le Brutal"           # quoted comma kept
    assert ev.amount == 412 and ev.max_hp == 9000 and ev.info_guid == ev.src_guid
    assert ev.t == cl.parse_timestamp("11/7/2026 21:14:03.1234")


def test_garbage_never_raises():
    for bad in ["", "hello", "11/7 99:99:99.000  SPELL_DAMAGE,x", "11/7 21:00:00.000  ",
                '11/7 21:00:00.000  SPELL_DAMAGE,"unterminated', "\x00\xff" * 50,
                "11/7 21:00:00.000  ZONE_CHANGE,1424,\"Contreforts de Hautebrande\",0"]:
        cl.parse_line(bad)
    z = cl.parse_line('11/7 21:00:00.000  ZONE_CHANGE,1424,"Contreforts de Hautebrande",0')
    assert z.event == "ZONE_CHANGE"


def test_tail_starts_at_end_and_handles_partial_and_truncation(tmp_path):
    p = tmp_path / "Logs" / "WoWCombatLog.txt"
    tail = cl.CombatLogTail(p)
    assert tail.poll() == []                          # file missing: fine
    p.parent.mkdir()
    lines = _lines()
    p.write_text("\n".join(lines[:5]) + "\n", encoding="utf-8")
    first = tail.poll()                               # created after start: read from its start
    assert len(first) == 4 and tail.header is not None
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(lines[5] + "\n" + lines[6][:30])     # partial line
    assert len(tail.poll()) == 1
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(lines[6][30:] + "\n")
    assert len(tail.poll()) == 1
    p.write_text(lines[0] + "\n" + lines[1] + "\n", encoding="utf-8")   # new /combatlog file (smaller)
    assert len(tail.poll()) == 1


def test_tail_skips_existing_content(tmp_path):
    p = tmp_path / "WoWCombatLog.txt"
    p.write_text("\n".join(_lines()) + "\n", encoding="utf-8")
    tail = cl.CombatLogTail(p)
    assert tail.poll() == []
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(_lines()[1] + "\n")
    assert len(tail.poll()) == 1
    assert len(cl.CombatLogTail(p, from_start=True).poll()) == len(_lines())


def test_watcher_thread(tmp_path):
    import threading
    p = tmp_path / "WoWCombatLog.txt"
    p.write_text("", encoding="utf-8")
    got, done = [], threading.Event()

    def on(evs):
        got.extend(evs)
        if len(got) >= 3:
            done.set()

    w = cl.CombatLogWatcher(p, on, interval=0.05)
    w.start()
    try:
        with open(p, "a", encoding="utf-8") as fh:
            fh.write("\n".join(_lines()[1:4]) + "\n")
        assert done.wait(3.0)
    finally:
        w.stop()
    assert not w.running and len(got) == 3


def test_default_log_path():
    assert cl.default_log_path("C:/Jeux/World of Warcraft/_classic_era_").name == "WoWCombatLog.txt"
    assert cl.default_log_path(None) is None
