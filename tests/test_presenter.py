"""Presentation router: scripted timelines (laning, gank, fight, dead, siege) -> what goes where."""
from __future__ import annotations

from treeaicoach import presenter as P

M, C = P.Message, P.Context


def test_routing_table_is_complete_and_readable():
    for kind, r in P.ROUTES.items():
        assert r.normal in P.CHANNELS and r.fight in P.CHANNELS and r.dead in P.CHANNELS, kind
        assert 0.0 <= r.value <= 1.0
    assert P.message_kind("insight", "tip:12") == "tip"
    assert P.message_kind("insight", "genie:baron") == "macro"
    assert P.message_kind("danger", "x") == "danger" and P.message_kind("praise", "k") == "praise"


def test_laning_one_panel_line_every_5_s_and_topic_dedupe():
    p = P.Presenter()
    ctx = lambda t: C(t=t, skill="debutant")
    assert p.offer(M("tip", "Pose une balise", topic="ward"), ctx(0.0)).channel == P.PANEL
    assert p.offer(M("tip", "Farme sous ta tour", topic="farm"), ctx(2.0)).channel == P.DROP    # 5 s cap
    assert p.offer(M("warning", "Darius a 2 niveaux de plus", urgency=2, topic="fed"), ctx(3.0)).channel == P.PANEL
    assert p.offer(M("tip", "Farme sous ta tour", topic="farm"), ctx(9.0)).channel == P.PANEL
    assert p.offer(M("tip", "Pose une balise", topic="ward"), ctx(20.0)).channel == P.DROP      # topic 40 s
    assert p.panel(9.5) == "Farme sous ta tour" and p.panel(100.0) is None


def test_levels_bar():
    p = P.Presenter()
    assert p.offer(M("tip", "Achète une balise"), C(t=0.0, skill="expert")).channel == P.DROP
    assert p.offer(M("danger", "Recule !", urgency=3), C(t=0.0, skill="expert")).channel == P.BANNER


def test_gank_and_fight_only_danger():
    p = P.Presenter()
    g = C(t=100.0, gank=True)
    assert p.offer(M("danger", "GANK", urgency=3), g).channel == P.BANNER
    assert p.offer(M("tip", "Pose une balise"), g).channel == P.DROP
    assert p.offer(M("macro", "Va au Baron"), g).channel == P.DROP
    assert p.offer(M("play", "Bon coup"), g).channel == P.DROP
    f = C(t=101.0, fight=True)
    assert p.offer(M("retreat", "RECULE", urgency=3), f).channel == P.BANNER
    assert p.offer(M("engage", "ATTAQUE", urgency=2), f).channel == P.BANNER
    assert p.offer(M("praise", "SOLO KILL"), f).channel == P.DROP
    assert p.filter_panel_line("Pose une balise", "info", f) is None
    assert p.filter_panel_line("Recule vers ta tour", "danger", f) == "Recule vers ta tour"


def test_banner_gap_20_s_except_danger():
    p = P.Presenter()
    assert p.offer(M("macro", "Prends le dragon", value=0.9, topic="a"), C(t=0.0)).channel == P.BANNER
    assert p.offer(M("macro", "Pousse bot", value=0.9, topic="b"), C(t=10.0)).channel == P.PANEL   # gap -> panel
    assert p.offer(M("engage", "ATTAQUE", topic="c"), C(t=12.0)).channel == P.DROP
    assert p.offer(M("danger", "GANK", urgency=3, topic="d"), C(t=13.0)).channel == P.BANNER
    assert p.offer(M("macro", "Baron", value=0.9, topic="e"), C(t=25.0)).channel == P.BANNER
    ident = ("call", "BARON", 30.0)
    assert not p.banner_ok("call", ident, C(t=30.0))           # 5 s after the last banner
    assert not p.banner_ok("call", ident, C(t=60.0))           # decision kept for the banner's life
    assert p.banner_ok("retreat", ("retreat", "RECULE", 31.0), C(t=31.0))


def test_dead_and_siege():
    p = P.Presenter()
    d = C(t=500.0, dead=True)
    assert p.offer(M("tip", "Farme"), d).channel == P.DROP
    assert p.offer(M("death_cause", "Tu es mort sans vision du jungler"), d).channel == P.PANEL
    assert p.offer(M("danger", "GANK", urgency=3), d).channel == P.DROP              # not my fight
    assert p.offer(M("danger", "BASE ATTAQUÉE", urgency=3, siege=True), d).channel == P.BANNER
    s = C(t=600.0, siege=True, gank=True)
    assert p.offer(M("tip", "Pose une balise"), s).channel == P.DROP
    assert p.counts()[P.BANNER] >= 1
