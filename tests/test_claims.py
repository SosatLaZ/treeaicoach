"""Correctness of what the coach SAYS (levels from fogged Tab data, names, pronouns) + tools/claims_audit.py."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

from treeaicoach import freshness as F  # noqa: E402
from treeaicoach import game_changers as gc  # noqa: E402
from treeaicoach.coach import MapCoach as Coach  # noqa: E402
from treeaicoach.live_client import GameInfo, PlayerInfo  # noqa: E402
from treeaicoach.scoreboard import PlayerLine, ScoreboardAnalyzer  # noqa: E402
from test_game_changers import ctx_for, game  # noqa: E402


def _unseen(g: GameInfo, alias: str, age: float = 60.0) -> GameInfo:
    """``alias`` unseen for ``age`` s with his level unchanged (what LevelWatch writes)."""
    for p in g.enemies:
        if p.champion_alias == alias:
            p.level_age_s = age
    return g


# ------------------------------------------------------------------ levels: a fogged level is a floor
def test_level_exact_reads_the_level_watch_annotations():
    lw = F.LevelWatch()
    g = game(400.0, levels={"Garen": 6, "Darius": 5})
    lw.update(g, 1000.0, lambda a: 1000.0)                    # all seen now
    assert all(F.level_exact(p) for p in g.enemies)
    g2 = game(440.0, levels={"Garen": 6, "Darius": 5})
    lw.update(g2, 1040.0, lambda a: 1000.0 if a == "Darius" else 1040.0)   # Darius unseen 40 s
    darius = next(p for p in g2.enemies if p.champion_alias == "Darius")
    assert darius.level_age_s >= 40.0 and not F.level_exact(darius)
    assert not F.enemy_level_exact(g2, "Darius") and F.enemy_level_exact(g2, "Ahri")
    assert F.level_exact(PlayerInfo(champion_alias="Garen"))  # never annotated (me, demo): exact


def test_attack_window_not_claimed_on_a_fogged_level():
    """'Frappe Darius maintenant : niveau 6 contre 5' needs HIS level now: unseen > 30 s, he may be 6."""
    fresh = gc.rule_level_race(ctx_for(game(130.0, levels={"Garen": 2, "Darius": 1})))
    assert fresh is not None and fresh.text.startswith("Frappe Darius")
    stale = gc.rule_level_race(ctx_for(_unseen(game(130.0, levels={"Garen": 2, "Darius": 1}), "Darius")))
    assert stale is None
    # "Darius est niveau 6, pas toi": the API level is a floor, still true -> kept
    back = gc.rule_level_race(ctx_for(_unseen(game(320.0, levels={"Garen": 5, "Darius": 6}), "Darius")))
    assert back is not None and back.text == "Recule vers ta tour : Darius est niveau 6, pas toi"
    # never "X est niveau 6, pas toi" when I am 6 too
    assert gc.rule_level_race(ctx_for(game(320.0, levels={"Garen": 6, "Darius": 6}))) is None


def test_my_level_lead_not_claimed_on_a_fogged_level():
    c = Coach()
    g = _unseen(game(600.0, levels={"Garen": 9, "Darius": 6}), "Darius")
    ctx = type("C", (), {})()
    ctx.me_player = g.me
    ctx.my_role = "TOP"
    darius = next(p for p in g.enemies if p.champion_alias == "Darius")
    ctx.opponents = [("Darius", "Darius", darius, None)]
    assert c._level_diff(ctx) == (0, None)                   # "Tu as 3 niveaux d'avance" unsure
    darius.level_age_s = 5.0
    assert c._level_diff(ctx) == (3, "Darius")
    darius.level, darius.level_age_s = 12, 60.0               # he is ahead: a floor, still true
    assert c._level_diff(ctx) == (-3, "Darius")


def test_stance_reasons_drop_my_lead_on_a_fogged_tab_line():
    from treeaicoach.coach import stance_factors

    sb = type("SB", (), {})()
    sb.my_matchup = type("M", (), {"enemy": "Darius", "enemy_alias": "Darius", "level_diff": 3,
                                   "gold_diff": 1500})()
    seen = [t for _w, t in stance_factors({"gt": 600.0}, game(600.0), sb)]
    assert any("sur Darius" in t for t in seen)
    fog = [t for _w, t in stance_factors({"gt": 600.0}, _unseen(game(600.0), "Darius"), sb)]
    assert not any("sur Darius" in t for t in fog)
    sb.my_matchup.level_diff, sb.my_matchup.gold_diff = -2, -1500       # he is ahead: a floor, kept
    assert any("d'avance" in t for _w, t in stance_factors({"gt": 600.0}, _unseen(game(600.0), "Darius"), sb))


# ------------------------------------------------------------------ names / pronouns
def test_antiheal_names_once_and_singular():
    """Real 3.1 line: "Achète Orbe de l'oubli : Trundle et Trundle se soignent" (healer AND life-steal items)."""
    from treeaicoach.itemization import enemy_profile, load_items

    trundle = PlayerInfo(champion_alias="Trundle", champion_name="Trundle", team="CHAOS", position="TOP",
                         items=[3072, 3074])
    assert enemy_profile([trundle], load_items()).names.get("antiheal") == ["Trundle"]


def test_feminine_champion_pronouns_in_scoreboard_lines():
    ln = PlayerLine(alias="Ahri", name="Ahri", team="CHAOS", side="enemy", role="MIDDLE", kills=11, deaths=4,
                    assists=2, cs=200, cs_per_min=7.0, level=14, item_gold=12000, est_gold=12000,
                    major_items=3, ward_score=5.0, is_dead=False)
    ins = ScoreboardAnalyzer._fed_insight(ln, 2000.0, 2, 0.0)
    assert "avancée" in ins.text and "Évite-la seul" in ins.text and "Évite-le" not in ins.text
    from dataclasses import replace

    ln2 = replace(ln, alias="Darius", name="Darius")
    ins2 = ScoreboardAnalyzer._fed_insight(ln2, 2000.0, 2, 0.0)
    assert "avancé :" in ins2.text and "Évite-le seul" in ins2.text


# ------------------------------------------------------------------ the audit tool itself
def _record():
    roster = [{"alias": "Seraphine", "name": "Séraphine", "team": "ORDER", "position": "UTILITY", "riot_id": "me#1",
               "summoner_name": "me#1", "is_me": True},
              {"alias": "Jhin", "name": "Jhin", "team": "CHAOS", "position": "BOTTOM", "riot_id": "j#1",
               "summoner_name": "j#1"},
              {"alias": "Ahri", "name": "Ahri", "team": "CHAOS", "position": "MIDDLE", "riot_id": "a#1",
               "summoner_name": "a#1"},
              {"alias": "Ashe", "name": "Ashe", "team": "ORDER", "position": "BOTTOM", "riot_id": "s#1",
               "summoner_name": "s#1"}]
    snaps = [{"game_time": float(t), "level": lv, "is_dead": False} for t, lv in ((0, 1), (90, 2), (500, 5), (530, 6))]
    events = [{"EventName": "ChampionKill", "EventTime": 400.0, "VictimName": "j#1", "KillerName": "s#1"}]
    alerts = [[405.0, "macro_tip", 0, "Jhin est mort : aide ton tireur à pousser.", None],
              [460.0, "macro_tip", 0, "Jhin est mort : aide ton tireur à pousser.", None],      # respawned
              [531.0, "macro_tip", 0, "Recule vers ta tour : Jhin est niveau 6, pas toi.", None],  # I am 6
              [600.0, "scoreboard", 0, "Ahri a terminé Onde orageuse : il devient plus fort.", "Ahri"],
              [610.0, "macro_tip", 0, "Achète Orbe de l'oubli : Ahri et Ahri se soignent.", None],
              [620.0, "macro_tip", 0, "Reste avec ton équipe et farme : 1 kills à 0", None],
              [630.0, "macro_tip", 0, "Va en bas : Baron dans 55 s", None]]
    return {"meta": {"app_version": "test", "game_mode": "CLASSIC", "start_game_time": 0.0}, "roster": roster,
            "snapshots": snaps, "events": events, "alerts": alerts, "calls": [], "sightings": {},
            "duration": 1500.0}


def test_claims_audit_flags_each_kind_of_wrong_fact():
    import claims_audit as CA

    rows = CA.audit(_record())
    wrong = {(r["rule"], int(r["t"])) for r in rows if r["verdict"] == "wrong"}
    ok = {(r["rule"], int(r["t"])) for r in rows if r["verdict"] == "ok"}
    assert ("dead", 405) in ok and ("dead", 460) in wrong
    assert ("level_named", 531) in wrong
    assert ("pronoun", 600) in wrong
    assert ("name", 610) in wrong
    assert ("score", 620) in ok
    assert ("side", 630) in wrong


def test_claims_audit_main_runs_on_a_file(tmp_path, capsys):
    import json

    import claims_audit as CA

    p = tmp_path / "g.json"
    p.write_text(json.dumps(_record()), encoding="utf-8")
    assert CA.main([str(p), "--show", "wrong"]) == 0
    out = capsys.readouterr().out
    assert "TOTAL wrong" in out and "pronoun" in out


def test_fight_gold_number_only_on_fresh_enemy_inventories():
    """'Force le dragon : combat 5v4, +12,3k or': their items are the ones seen last (Tab is fogged)."""
    from test_fog_intel import _items
    from test_macro import ctx_for as mctx
    from test_macro import game as mgame
    from test_macro import obj

    from treeaicoach import fog_intel as FI
    from treeaicoach import macro

    near = (macro.DRAGON_UV[0] - 0.05, macro.DRAGON_UV[1] + 0.05)
    g = _items(mgame(1000.0, me="Jinx", dead={"Ahri": 30.0}), "ORDER")
    call = FI.rule_objective_fight(mctx(g, me_uv=near, objectives=[obj("dragon", True)], t=100.0))
    assert call is not None and call.text.endswith(" or")
    g2 = _items(mgame(1000.0, me="Jinx", dead={"Ahri": 30.0}), "ORDER")
    for p in g2.enemies:
        p.level_age_s = 90.0
    call2 = FI.rule_objective_fight(mctx(g2, me_uv=near, objectives=[obj("dragon", True)], t=100.0))
    assert call2 is not None and call2.text == "Force le dragon : combat 5v4"
