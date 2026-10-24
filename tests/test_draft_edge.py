"""draft_edge.py: comp reading, power spikes, counter tweak, lanes, enemy jungler forecast, tilt guard."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treeaicoach import champ_select as cs  # noqa: E402
from treeaicoach import draft_edge as de  # noqa: E402
from treeaicoach import presenter  # noqa: E402
from treeaicoach.champions import get_default_db  # noqa: E402

ROLES = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
SCALING = ["Nasus", "Karthus", "Kassadin", "Kayle", "Sona"]
EARLY = ["Renekton", "LeeSin", "Pantheon", "Draven", "Leona"]


def team(aliases):
    return [(a, a, r) for a, r in zip(aliases, ROLES)]


# ----------------------------------------------------------------------------- 8. comp reading
def test_scaling_comp_win_condition_is_one_card_sentence():
    cr = de.comp_read(team(SCALING), team(EARLY), "Nasus")
    assert cr is not None and cr.sentence.startswith("On scale mieux : joue le temps jusqu'à ")
    assert len(cr.sentence) <= de.CARD_MAX and presenter.card_line(cr.sentence).startswith("Joue le temps")
    assert "% AP" in cr.detail and "% AD" in cr.detail and "on scale" in cr.detail
    rev = de.comp_read(team(EARLY), team(SCALING), "Renekton")
    assert rev is not None and rev.sentence.startswith("On est plus forts tôt : force")
    assert de.comp_read(team(SCALING[:2]), team(EARLY), "Nasus") is None          # < 3 picks
    assert de.win_condition(None, None) == ""


def test_team_card_starts_with_the_win_condition():
    from tests.test_mastermind import game  # noqa: PLC0415
    from treeaicoach import game_plan, mastermind  # noqa: PLC0415

    card = game_plan.team_card(mastermind.analyze(game(SCALING, EARLY, 100.0)))
    assert card is not None and card.subtitle.startswith("Joue le temps jusqu'à") and len(card.subtitle) <= 60
    assert presenter.card_line(card.subtitle) == card.subtitle
    assert not any(x.startswith("Joue le temps :") for x in card.lines)             # tempo said once


# ----------------------------------------------------------------------------- 9. power spikes
def test_spike_timeline_merges_phases():
    t = de.spike_timeline("Nasus", "Renekton")
    assert t == "Il gagne jusqu'au 1er objet, toi après 2 objets"
    assert de.spike_timeline("Kayle", "Irelia").startswith("Elle gagne")
    assert de.spike_timeline("Garen", "Teemo") == "Il gagne niv 1-3, toi au 6, égal dès le 1er objet"
    assert de.spike_timeline("Garen", "NotAChampion") == ""


# ----------------------------------------------------------------------------- 10. counter tweak
def test_counter_tweak_by_enemy_comp():
    ap = de.counter_tweak("Garen", "TOP", team(["Mordekaiser", "Elise", "Syndra", "Ezreal", "Lux"]))
    assert ap is not None and ap.need == "magic" and ap.item_id == de.MERCURY and ap.rune == "Second souffle"
    assert ap.text.startswith("Contre eux : Sandales de Mercure tôt + rune Second souffle (")
    heal = de.counter_tweak("Garen", "TOP", team(["Vladimir", "Kindred", "Ekko", "Caitlyn", "Soraka"]))
    assert heal is not None and heal.need == "antiheal" and heal.item == "Marque du bourreau"
    ranged = de.counter_tweak("Jinx", "BOTTOM", team(["Mordekaiser", "Elise", "Syndra", "Ezreal", "Lux"]))
    assert ranged is not None and ranged.rune == ""                                 # Second Wind: melee only
    assert de.counter_tweak("Garen", "TOP", team(["Darius"])) is None


# ----------------------------------------------------------------------------- 11. lanes
def test_lanes_and_play_around():
    edges = de.lane_edges(team(["Swain", "Elise", "Syndra", "MissFortune", "Leona"]),
                          team(["Malphite", "Amumu", "Orianna", "Jinx", "Lulu"]))
    assert set(edges) == {"top", "mid", "bot"} and all(v > 0 for v in edges.values())
    assert de.lanes_line(edges) == "Voies : top favorable, mid favorable, bot favorable"
    assert de.play_around({"top": 0.01, "mid": -0.05, "bot": 0.09}) == "Jouez bot : 2v2 gagnant"
    assert de.play_around({"top": 0.08, "bot": 0.0}, team(["Garen"])) == "Jouez top : Garen gagne sa voie"
    assert de.play_around({"top": -0.1, "mid": -0.1}) == "Jouez les objectifs à 5 : aucune voie gagnante"
    assert de.friend_suffix("BOTTOM", {"top": 0.0, "bot": 0.09}).startswith(" L'équipe joue autour")
    assert de.friend_suffix("TOP", {"top": 0.0, "bot": 0.09}) == ""


# ----------------------------------------------------------------------------- 12. enemy jungler
def test_jungle_forecast_uses_the_red_side_rule():
    us, them = team(["Garen", "Vi", "Lux", "Jinx", "Thresh"]), team(["Darius", "LeeSin", "Ahri", "Caitlyn", "Nautilus"])
    f = de.jungle_forecast(us, them, "ORDER")              # they are CHAOS: red buff top, blue start bot
    assert f is not None and f.start == "bot" and f.gank == "top" and f.early and f.when == "2:45"
    assert f.text("TOP") == "Balise ta rivière avant 2:45 : LeeSin vient top"
    assert f.text("JUNGLE") == "Contre-gank top vers 2:45 : LeeSin commence en bas"
    assert f.text("MIDDLE").startswith("LeeSin : départ en bas, 1er gank top")
    g = de.jungle_forecast(us, them, "CHAOS")
    assert g is not None and g.start == "top" and g.gank == "bot"
    assert de.jungle_forecast(us, team(["Darius"]), "ORDER") is None


# ----------------------------------------------------------------------------- 13. tilt guard
H = 3600.0
NOW = 1_800_000_000.0


def hist(*games):
    """(win, k, d, a, ended hours ago, duration s) -> LCU match-history entries (newest first)."""
    out = []
    for win, k, d, a, ago, dur in games:
        start = NOW - ago * H - dur
        out.append({"gameId": int(start), "gameCreation": int(start * 1000), "gameDuration": dur,
                    "participants": [{"stats": {"win": win, "kills": k, "deaths": d, "assists": a}}],
                    "participantIdentities": [{"player": {"gameName": "SECRET", "tagLine": "EUW"}}]})
    return out


def test_tilt_guard_losing_streak_and_kda():
    two = de.parse_history(hist((False, 2, 7, 3, 0.2, 1800), (False, 1, 6, 2, 0.8, 1700), (True, 8, 2, 9, 1.4, 2000)))
    assert de.tilt_line(two, NOW) == "2 défaites d'affilée : pause 10 min ?"
    assert "SECRET" not in repr(two)                                    # numbers only
    three = de.parse_history(hist(*[(False, 1, 5, 1, 0.2 + 0.6 * i, 1800) for i in range(3)]))
    assert de.tilt_line(three, NOW) == "3 défaites d'affilée : vraie pause de 15 min ?"
    assert de.tilt_line(three, NOW + 5 * H) == ""                       # session over: nothing to say
    won = de.parse_history(hist((True, 5, 2, 5, 0.2, 1800), (False, 1, 5, 1, 0.8, 1800)))
    assert de.tilt_line(won, NOW) == ""
    remake = de.parse_history(hist((False, 0, 0, 0, 0.1, 200), (True, 5, 2, 5, 0.3, 1800)))
    assert len(remake) == 1 and de.tilt_line(remake, NOW) == ""
    kda = de.parse_history(hist((False, 1, 8, 1, 0.2, 1800), (True, 2, 7, 2, 0.8, 1800), (True, 1, 6, 2, 1.4, 1800),
                                *[(True, 8, 2, 8, 30 + i, 1800) for i in range(4)]))
    assert de.tilt_line(kda, NOW) == "Ton KDA baisse sur 3 parties : petite pause ?"
    assert de.parse_history(None) == [] and de.tilt_line([], NOW) == ""


def test_lcu_recent_games_reads_only_my_history():
    from treeaicoach import lcu  # noqa: PLC0415

    paths = []

    class C(lcu.LcuClient):
        def get(self, path, timeout=None):
            paths.append(path)
            return {"games": {"games": hist((True, 1, 1, 1, 2.0, 1800), (False, 1, 1, 1, 0.1, 1800))}}

    games = C().recent_games(5)
    assert paths == [lcu.RECENT_PATH.format(n=5)] and "current-summoner" in paths[0]
    assert games[0]["gameCreation"] > games[1]["gameCreation"]


# ----------------------------------------------------------------------------- champion select card
def _session(team_id=1):
    db = get_default_db()
    mine = [("Nasus", "top"), ("Karthus", "jungle"), ("Kassadin", "middle"), ("Kayle", "bottom"), ("Sona", "utility")]
    return {"localPlayerCellId": 0,
            "myTeam": [{"cellId": i, "championId": db.get(a).key, "assignedPosition": p, "team": team_id}
                       for i, (a, p) in enumerate(mine)],
            "theirTeam": [{"cellId": 5 + i, "championId": db.get(a).key} for i, a in enumerate(EARLY)],
            "timer": {"phase": "BAN_PICK"}}


def test_card_carries_the_draft_edge():
    st = cs.parse_session(_session(2))
    assert st is not None and st.my_team == "CHAOS"
    card = cs.build_card(st, "2 défaites d'affilée : pause 10 min ?")
    assert card is not None and card.lines[0] == "2 défaites d'affilée : pause 10 min ?"
    assert card.win_condition.startswith("On scale mieux") and card.win_condition in card.lines
    assert card.spikes and card.spikes in card.lines and card.counter.startswith("Contre eux : ")
    assert card.play_around and card.jungle and card.lanes.startswith("Voies : ")
    assert len(card.lines) <= cs.MAX_LINES and any(x.startswith("Départ : ") for x in card.lines)


def test_watcher_reads_the_tilt_once_per_champ_select():
    reads = []

    class Client:
        session = _session()

        def get(self, path):
            return self.session

        def recent_games(self, n):
            reads.append(n)
            return hist((False, 1, 5, 1, 0.0, 1800), (False, 1, 5, 1, 0.5, 1800))

    t = [0.0]
    c = Client()
    w = cs.ChampSelectWatcher(client=c, clock=lambda: t[0])
    import time as _time

    real = _time.time
    _time.time = lambda: NOW                                              # type: ignore[assignment]
    try:
        card = w.poll()
        t[0] += cs.POLL_S
        w.poll()
    finally:
        _time.time = real                                                 # type: ignore[assignment]
    assert card is not None and card.tilt == "2 défaites d'affilée : pause 10 min ?" and reads == [10]
    c.session = None
    t[0] += cs.POLL_S
    assert w.poll() is None
    c.session = _session()
    t[0] += cs.POLL_S
    w.poll()
    assert reads == [10, 10]                                              # read again next champ select


def test_tilt_guard_ignores_custom_and_practice_games():
    games = hist((False, 0, 9, 0, 0.2, 1800), (False, 0, 9, 0, 0.8, 1800), (True, 5, 2, 5, 1.4, 1800))
    games[0]["gameType"] = "CUSTOM_GAME"
    games[1]["gameMode"] = "PRACTICETOOL"
    res = de.parse_history(games)
    assert len(res) == 1 and res[0].win and de.tilt_line(res, NOW) == ""


def test_lane_prior_phase_rank_and_streak():
    from types import SimpleNamespace as N

    assert de.phase_index(3, 0) == 0 and de.phase_index(6, 1000) == 1 and de.phase_index(9, 3000) == 2
    assert de.phase_index(14, 7000) == 3
    lp = de.lane_prior("Garen", "Darius", 3, 0)                    # Darius wins levels 1-5
    assert lp.hard_losing and lp.kill_extra > 0.5 and lp.reasons
    assert de.lane_prior("Garen", "Vladimir", 3, 0).kill_extra == 0.0   # my phase: never looser
    assert de.lane_prior("Garen", "NoSuchChamp", 3, 0) == de.LanePrior(phase=0)
    gold = N(ranked_loaded=True, tier="GOLD", history_loaded=True, streak=0)
    assert de.lane_prior("Garen", "Malphite", 3, 0, opp_intel=gold, my_tier="IRON").kill_extra == de.PRIOR_RANK_EXTRA
    assert de.lane_prior("Garen", "Malphite", 3, 0, opp_intel=gold, my_tier="SILVER").kill_extra == 0.0
    tilt = N(ranked_loaded=False, history_loaded=True, streak=-5)
    assert de.lane_prior("Garen", "Malphite", 3, 0, opp_intel=tilt).kill_extra == -de.PRIOR_STREAK_EXTRA
    hot = N(ranked_loaded=True, tier="DIAMOND", history_loaded=True, streak=8)
    assert de.lane_prior("Garen", "Darius", 3, 0, opp_intel=hot, my_tier="IRON").kill_extra == de.PRIOR_MAX


def test_live_prior_reads_the_running_intel_watcher(monkeypatch):
    from types import SimpleNamespace as N

    from treeaicoach import meta_builds, player_intel

    me, opp = N(champion_alias="Garen", level=3, items=[]), N(champion_alias="Malphite", level=3, items=[])
    assert de.live_prior(me, opp).kill_extra == 0.0
    w = N(snapshot=lambda: (player_intel.PlayerIntel("Malphite", "Malphite", False, "TOP", ranked_loaded=True,
                                                     tier="PLATINUM"),))
    monkeypatch.setattr(player_intel, "_default", w)
    monkeypatch.setattr(meta_builds, "_user_tier", "BRONZE")
    assert de.live_prior(me, opp).kill_extra == de.PRIOR_RANK_EXTRA
    assert player_intel.enemy_intel("malphite").tier == "PLATINUM" and player_intel.enemy_intel("Garen") is None
    assert de.live_prior(None, opp) == de.LanePrior()
