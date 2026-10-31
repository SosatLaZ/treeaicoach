"""threat_gate.py: one threat = one alert (episode merge, same tick, held early warnings)."""

from treeaicoach.threat_gate import ThreatGate


def test_repeat_same_enemy_merged_escalation_passes():
    g = ThreatGate()
    assert g.offer(0.0, "a", "roam_approach", 2, ("Trundle",), 0.08)
    assert not g.offer(3.0, "b", "roam_approach", 2, ("Trundle",), 0.08)      # same threat
    assert not g.offer(4.0, "c", "personal_danger", 1, ("Trundle",), 0.07)    # other kind, same enemy
    assert g.offer(5.0, "d", "roam_approach", 2, ("Trundle",), 0.03)          # much closer: said
    assert g.offer(30.0, "e", "roam_approach", 2, ("Trundle",), 0.08)         # new episode


def test_same_tick_highest_level_first_and_companion():
    class A:
        def __init__(self, kind, level, key=""):
            self.kind, self.level, self.key, self.members, self.alias, self.t = kind, level, key, (), "Jhin", 0.0
    g = ThreatGate()
    warn = A("personal_danger", 1)
    recule = A("personal_danger", 2, "personal_danger:recule")
    why = A("personal_danger", 1, "personal_danger:recule:why")
    out = g.filter_tick(0.0, [warn, recule, why], lambda a: (("Jhin",), 0.05), lambda m: 0.05)
    assert recule in out and why in out and warn not in out


def test_early_warning_held_until_enemy_closes_in():
    g = ThreatGate()
    assert not g.offer(0.0, "x", "roam_approach", 1, ("Ahri",), 0.24)         # held
    assert g.due(0.5, lambda m: 0.15) == []                                    # too early even if close
    assert g.due(1.0, lambda m: 0.22) == []                                    # not yet due
    assert g.due(2.6, lambda m: 0.22) == ["x"]                                 # kept closing: said
    g3 = ThreatGate()
    g3.offer(0.0, "f", "roam_approach", 1, ("Ahri",), 0.24)
    assert g3.due(1.0, lambda m: 0.20) == ["f"]                                # clearly closing: said at once
    g2 = ThreatGate()
    g2.offer(0.0, "y", "roam_approach", 1, ("Ahri",), 0.24)
    for t in (2.6, 3.6, 4.6, 5.6):
        assert g2.due(t, lambda m: 0.25) == []                                 # walking parallel: never


def test_laner_mia_cooldown_and_never_raises():
    g = ThreatGate()
    assert g.offer(0.0, "m", "laner_mia", 0, ("Jhin",), None)
    assert not g.offer(60.0, "m", "laner_mia", 0, ("Jhin",), None)
    assert g.offer(1.0, "o", "macro_tip", 1, (), None)                         # not gated
    assert g.offer("bad", "z", "roam_approach", 2, ("X",), None)               # error -> pass


def test_pre_alert_never_held():
    class A:
        def __init__(self, key):
            self.kind, self.level, self.key, self.members, self.alias, self.t = \
                "jungler_approach", 1, key, (), "LeeSin", 0.0
    g = ThreatGate()
    pre = A("jungler_approach:pre-LeeSin")
    assert g.filter_tick(0.0, [pre], lambda a: (("LeeSin",), 0.18), lambda m: 0.18) == [pre]
    full = A("jungler_approach:LeeSin")
    assert g.filter_tick(0.1, [full], lambda a: (("LeeSin",), 0.18), lambda m: 0.18) == []   # held
    assert g.filter_tick(1.0, [], lambda a: ((), None), lambda m: 0.14) == [full]            # closing: said


def test_held_jungler_warning_says_short_name_at_once_with_cooldown():
    class A:
        def __init__(self, key, text="Lee Sin arrive par la rivière !"):
            self.kind, self.level, self.key, self.members, self.alias, self.t, self.text = \
                "jungler_approach", 1, key, (), "LeeSin", 0.0, text
    g = ThreatGate()
    pre = A("jungler_approach:pre-LeeSin", "Lee Sin !")
    full = A("jungler_approach:LeeSin")
    out = g.filter_tick(0.0, [full], lambda a: (("LeeSin",), 0.18), lambda m: 0.18, lambda a: pre)
    assert out == [pre]                                                          # name now, sentence held
    g._held.clear()
    assert g.filter_tick(5.0, [full], lambda a: (("LeeSin",), 0.18), lambda m: 0.18, lambda a: pre) == []
