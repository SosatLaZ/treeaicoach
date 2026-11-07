"""WoW live voice coach + post-fight / session report (synthetic log, fake names)."""
from pathlib import Path

from treeaicoach.wow import combatlog as cl
from treeaicoach.wow.coach import DANGER, WARNING, WowCombatSession, WowVoiceCoach
from treeaicoach.wow.report import SessionReport

FIX = Path(__file__).parent / "fixtures" / "combatlog_hunter_fr.txt"
ME = 'Player-9999-0A1B2C3D,"Sylvebrume-Brumeval",0x511,0x0'


def _events():
    return cl.parse_lines(FIX.read_text(encoding="utf-8").splitlines())


def test_callouts_on_fixture_through_say():
    said = []
    coach = WowVoiceCoach(lambda text, level: said.append((text, level)), max_lag=None)
    out = coach.feed(_events())
    keys = [c.key for c in out]
    for k in ("adds", "friend_aggro", "cast", "cc_broke", "me_low", "pet_low", "pet_died", "friend_died", "env"):
        assert k in keys, k
    assert ("Ton familier est mort !", WARNING) in said
    assert ("Tu te noies, remonte !", DANGER) in said
    assert any(t.startswith("Vie basse") and lvl == DANGER for t, lvl in said)
    assert "Kazrok est mort." in [t for t, _ in said]
    assert all(len(t) <= 60 for t, _ in said)                       # short lines
    assert not any("Brumeval" in t for t, _ in said)                 # realm stripped


def test_noise_budget_and_cooldowns():
    coach = WowVoiceCoach(None, max_lag=None, budget=2, budget_window=20.0)
    evs = _events()
    coach.feed(evs)
    coach.feed(evs)  # replay with same timestamps: per-key cooldowns hold, nothing new
    low = [c for c in coach.spoken if c.key in ("adds", "friend_aggro", "mob_on_me", "pet_low", "elite")]
    assert len(low) <= 2 and coach.dropped > 0
    assert sum(c.key == "pet_died" for c in coach.spoken) == 1


def test_stale_events_are_silent_but_tracked():
    said = []
    coach = WowVoiceCoach(lambda *a: said.append(a), max_lag=15.0, wall=lambda: 0.0)
    coach.feed(_events())
    assert said == [] and coach.spoken          # remembered (cooldowns) but not spoken


def test_disable_a_callout_and_say_errors_are_swallowed():
    def boom(*_a):
        raise RuntimeError("voice down")
    coach = WowVoiceCoach(boom, max_lag=None, enabled={"env": False})
    out = coach.feed(_events())
    assert "env" not in [c.key for c in out] and out


def test_elite_heuristic():
    me_hit = (f'11/7 21:00:01.000  SWING_DAMAGE,Creature-0-1-2-3-99-1,"Gardien antique",0xa48,0x0,{ME},'
              f'Player-9999-0A1B2C3D,0000000000000000,1500,1650,0,0,0,0,0,0,0,1,2,1424,0,26,150,150,-1,1,0,0,0,nil,nil,nil,nil')
    mob_hit = (f'11/7 21:00:02.000  RANGE_DAMAGE,{ME},Creature-0-1-2-3-99-1,"Gardien antique",0xa48,0x0,75,'
               f'"Tir automatique",0x1,Creature-0-1-2-3-99-1,0000000000000000,9800,9900,0,0,0,0,0,0,0,1,2,1424,0,'
               f'32,100,100,-1,1,0,0,0,nil,nil,nil')
    coach = WowVoiceCoach(None, max_lag=None)
    out = coach.feed(cl.parse_lines([me_hit, mob_hit]))
    assert [c.key for c in out] == ["elite"]


def test_report_fights_deaths_and_mistakes():
    r = SessionReport()
    r.feed(_events())
    r.flush()
    f1, f2 = list(r.fights)
    assert f1.kills == 1 and f1.dmg_me == 1289 and f1.dmg_pet == 211 and f1.pet_uptime == 1.0
    assert round(f1.dps) == round(1500 / f1.duration) and f1.mistakes() == []
    assert f1.spells.most_common(1)[0][0] == "Tir automatique"
    assert f2.kills == 3 and f2.max_adds == 3 and f2.dmg_party["Bramchene"] == 1495
    assert 0.3 < f2.pet_uptime < 0.8
    pet = next(d for d in f2.deaths if d.who == "pet")
    assert pet.killer == "Guérisseur de Hautebrande" and pet.spell == "Éclair"
    assert "tué par Guérisseur de Hautebrande" in pet.cause_fr()
    m = " ".join(f2.mistakes())
    assert "Familier mort" in m and "29 %" in m and "3 monstres" in m and "Piège givrant cassé par Ondelune" in m
    d = r.to_dict()
    assert d["fights"] == 2 and d["deaths_pet"] == 1 and d["deaths_party"] == 1 and d["deaths_me"] == 0
    assert d["environment"] == {"noyade": 164}
    txt = r.text_fr()
    assert txt.startswith("Session : 2 combats") and "Dégâts de noyade : 164." in txt
    assert "Brumeval" not in txt and r.facts_fr() and len(r.facts_fr()) <= 400


def test_report_empty_and_garbage():
    r = SessionReport()
    assert "Pas encore de combat" in r.text_fr() and r.facts_fr() == ""
    r.feed([cl.CombatEvent(t=1.0, event="SPELL_DAMAGE", src_flags=0x511, dst_flags=0xa48, amount=5)])
    r.feed([None, 3])  # type: ignore[list-item]
    assert r.flush(now=2.0) is None and r.flush() is not None


def test_session_glue_end_to_end(tmp_path):
    import threading
    p = tmp_path / "WoWCombatLog.txt"
    p.write_text("", encoding="utf-8")
    said, ended = [], threading.Event()
    s = WowCombatSession(p, lambda t, lvl: said.append(t), on_fight_end=lambda f: ended.set(), voice=True,
                         interval=0.05, max_lag=None, wall=lambda: 1e12)
    s.start()
    try:
        p.write_text(FIX.read_text(encoding="utf-8"), encoding="utf-8")
        assert ended.wait(3.0)
    finally:
        s.stop()
    assert "Ton familier est mort !" in said
    assert s.report_dict()["fights"] == 2 and "Session" in s.report_text() and s.facts()
