"""Detection decision fixes measured on det_gym botlane (replay of the real Swain game):

* camera lock: a locked camera released by the player ("free" camera, it stays still) no
  longer pins me to the old camera spot for CAMLOCK_HOLD_S while I walk on under my ADC: past
  CAMLOCK_COVER_S without a match of mine, the lock position needs an icon drawn over it or an
  unexplained ally-side icon there;
* covered jump: a champion whose last matched spot is covered by an icon accepted in this
  frame is under it - a far, weak candidate of his (a base glyph within the recall zone) is
  refused, a clean ring of his team colour (a real recall out of a stack) is not;
* anonymous phantoms: an anonymous track that no alive champion of its side can be (all of
  them accounted for, or too far away) is not reported by Tracker.enemies() / allies().
"""

from __future__ import annotations

import numpy as np

from treeaicoach import roster_matcher as RM
from treeaicoach import tracker as T
from treeaicoach.tracker import Tracker


def _matcher(*entries):
    m = RM.RosterMatcher()
    m._entries = tuple(RM.RosterEntry(a, rel, np.zeros((8, 8, 4), np.uint8), team=team)
                       for a, rel, team in entries)
    return m


# ======================================================================================
# camera lock
# ======================================================================================
def test_camlock_released_when_nothing_can_be_me_at_the_lock_point(monkeypatch):
    m = _matcher(("Swain", "self", "ORDER"), ("Tristana", "ally", "ORDER"))
    monkeypatch.setattr(m, "_verified_ally_at", lambda *a, **k: None)
    m.camlock.anchor = (0.9, 0.9, 10.0)
    lp, r = (0.93, 0.90), 0.045
    # just confirmed: kept
    assert not m._camlock_uncovered(lp, [], 10.0 + RM.CAMLOCK_COVER_S * 0.5, 1.0, 1.0, r, None)
    # unconfirmed for a while, an icon drawn over the spot (my ADC on me): kept
    adc = RM._Cand(1, 0.935, 0.89, 0.9, 0.9)
    assert not m._camlock_uncovered(lp, [adc], 12.0, 1.0, 1.0, r, None)
    # nothing there: released
    far = RM._Cand(1, 0.80, 0.70, 0.9, 0.9)
    assert m._camlock_uncovered(lp, [far], 12.0, 1.0, 1.0, r, None)
    # ... unless the verifier sees an unexplained ally icon there (my custom skin)
    monkeypatch.setattr(m, "_verified_ally_at", lambda *a, **k: lp)
    assert not m._camlock_uncovered(lp, [far], 12.0, 1.0, 1.0, r, None)


def test_camlock_cover_rule_can_be_disabled(monkeypatch):
    m = _matcher(("Swain", "self", "ORDER"))
    monkeypatch.setattr(m, "_verified_ally_at", lambda *a, **k: None)
    monkeypatch.setattr(RM, "CAMLOCK_COVER_S", -1.0)
    m.camlock.anchor = (0.9, 0.9, 10.0)
    assert not m._camlock_uncovered((0.93, 0.9), [], 15.0, 1.0, 1.0, 0.045, None)


# ======================================================================================
# covered jump
# ======================================================================================
def test_far_weak_candidate_of_a_covered_champion_is_refused():
    m = _matcher(("Shyvana", "ally", "ORDER"), ("Udyr", "enemy", "CHAOS"))
    m._tracks[0] = RM._Track(0.77, 0.907, 20.67)
    glyph = RM._Cand(0, 0.097, 0.906, 0.8, 0.8, f_en=0.16, f_al=0.61)      # base glyph
    udyr = RM._Cand(1, 0.785, 0.89, 1.0, 1.0)                               # drawn over her
    assert m._covered_jump(glyph, [udyr], 21.0, 1.0, 1.0, 0.045)
    # nothing over her last spot: not covered (she may have gone)
    assert not m._covered_jump(glyph, [], 21.0, 1.0, 1.0, 0.045)
    # a clean ring of her team's colour: a real recall out of the stack
    clean = RM._Cand(0, 0.097, 0.906, 0.8, 0.8, f_en=0.0, f_al=1.0)
    assert not m._covered_jump(clean, [udyr], 21.0, 1.0, 1.0, 0.045)
    # within walking reach: not a jump
    near = RM._Cand(0, 0.79, 0.90, 0.8, 0.8, f_en=0.16, f_al=0.61)
    assert not m._covered_jump(near, [udyr], 21.0, 1.0, 1.0, 0.045)
    # long after her last match: no memory
    assert not m._covered_jump(glyph, [udyr], 20.67 + RM.JUMP_COVERED_S + 0.5, 1.0, 1.0, 0.045)


# ======================================================================================
# anonymous phantoms
# ======================================================================================
def _ident(u, v, alias=None, relation="enemy", score=0.9):
    from treeaicoach.detector import Detection
    from treeaicoach.identifier import Identified

    det = Detection(u=u, v=v, r=0.045, score=score, cls="enemy" if relation == "enemy" else "ally",
                    cls_probs=(0.9, 0.1, 0.0) if relation == "enemy" else (0.1, 0.9, 0.0), alias=alias)
    return Identified(det=det, alias=alias, relation=relation, team=None, id_score=score if alias else 0.0)


ENEMIES = {"Veigar": (0.2, 0.2), "Nami": (0.4, 0.4), "Udyr": (0.6, 0.3), "Locke": (0.3, 0.7),
           "Singed": (0.5, 0.6)}
ROSTER = {"Swain": "self", "Tristana": "ally", "Galio": "ally", "Zed": "ally", "Shyvana": "ally",
          **{a: "enemy" for a in ENEMIES}}


def _run(tracker, frames, extra, t0=0.0, dead=()):
    tracker.set_roster(ROSTER)
    tracker.set_dead(dead)
    t = t0
    for _ in range(frames):
        ids = [_ident(u, v, a) for a, (u, v) in ENEMIES.items() if a not in dead] + \
            [_ident(u, v) for u, v in extra]
        tracker.update(t, ids)
        t += 0.2
    return t


def test_anonymous_enemy_is_a_phantom_when_all_alive_enemies_are_seen():
    tr = Tracker()
    _run(tr, 12, [(0.8, 0.8)])
    keys = [x.key for x in tr.enemies()]
    assert len(keys) == 5 and all("?" not in k for k in keys)


def test_anonymous_enemy_is_kept_when_an_alive_enemy_is_unaccounted_for():
    tr = Tracker()
    # Singed seen at (0.5, 0.6), then gone in the fog; an anonymous enemy appears near him
    tr.set_roster(ROSTER)
    t = 0.0
    for _ in range(6):
        tr.update(t, [_ident(u, v, a) for a, (u, v) in ENEMIES.items()])
        t += 0.2
    for _ in range(12):
        tr.update(t, [_ident(u, v, a) for a, (u, v) in ENEMIES.items() if a != "Singed"]
                  + [_ident(0.55, 0.62)])
        t += 0.2
    # reported: anonymous, or identified as Singed by elimination
    assert any(x.position() is not None and abs(x.position()[0] - 0.55) < 0.01
               for x in tr.enemies())


def test_anonymous_enemy_far_from_the_only_unaccounted_enemy_is_a_phantom():
    tr = Tracker()
    tr.set_roster(ROSTER)
    t = 0.0
    for _ in range(6):
        tr.update(t, [_ident(u, v, a) for a, (u, v) in ENEMIES.items()])
        t += 0.2
    # Singed hidden 2.4 s ago at (0.5, 0.6): he cannot be at (0.95, 0.1) - nor anyone else
    for _ in range(12):
        tr.update(t, [_ident(u, v, a) for a, (u, v) in ENEMIES.items() if a != "Singed"]
                  + [_ident(0.95, 0.1)])
        t += 0.2
    assert not any("?" in x.key for x in tr.enemies())
    assert any("?" in x.key for x in tr.tracks())          # (still tracked, just not reported)


def test_dead_enemy_does_not_explain_an_anonymous_icon():
    tr = Tracker()
    _run(tr, 12, [(0.8, 0.8)], dead=("Singed",))
    assert not any("?" in x.key for x in tr.enemies())


def test_surplus_rule_can_be_disabled(monkeypatch):
    monkeypatch.setattr(T, "ANON_SURPLUS", False)
    tr = Tracker()
    _run(tr, 12, [(0.8, 0.8)])
    assert any("?" in x.key for x in tr.enemies())


def test_anonymous_track_parked_on_one_spot_is_a_phantom():
    tr = Tracker()
    tr.set_roster(ROSTER)
    t = 0.0
    # Singed in the fog (never seen): a moving anonymous enemy may be him ...
    for k in range(20):
        tr.update(t, [_ident(u, v, a) for a, (u, v) in ENEMIES.items() if a != "Singed"]
                  + [_ident(0.7 + 0.004 * k, 0.8), _ident(0.06, 0.29)])
        t += 0.2
    anon = [x for x in tr.enemies() if "?" in x.key]
    # ... the one parked on a turret glyph for 4 s is not
    assert len(anon) == 1 and abs(anon[0].position()[1] - 0.8) < 0.01
