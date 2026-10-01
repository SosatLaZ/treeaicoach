"""champ_select.py: read-only champion select session -> pre-game card."""

from __future__ import annotations

from treeaicoach import champ_select as cs
from treeaicoach.champions import get_default_db

DB = get_default_db()


def cid(alias: str) -> int:
    return DB.get(alias).key


def session(me="Ahri", my_pos="middle", hover=False, enemies=("Zed", "LeeSin", "Darius", "Jinx", "Thresh"),
            enemy_pos=None):
    my_team = [{"cellId": 0, "championId": cid("Garen"), "assignedPosition": "top", "puuid": "secret"},
               {"cellId": 1, "championId": 0 if hover else cid(me), "championPickIntent": cid(me),
                "assignedPosition": my_pos, "summonerId": 42}]
    their = [{"cellId": 5 + i, "championId": cid(a), "assignedPosition": (enemy_pos or {}).get(a, "")}
             for i, a in enumerate(enemies)]
    return {"localPlayerCellId": 1, "myTeam": my_team, "theirTeam": their, "timer": {"phase": "BAN_PICK"}}


def test_parse_session_my_pick_and_enemies():
    st = cs.parse_session(session(hover=True))
    assert st is not None and st.me.alias == "Ahri" and st.me.position == "MIDDLE" and not st.me.locked
    assert [a.alias for a in st.allies] == ["Garen"] and st.phase == "BAN_PICK"
    assert {e.alias for e in st.enemies} == {"Zed", "LeeSin", "Darius", "Jinx", "Thresh"}
    assert "secret" not in repr(st) and "42" not in repr(st)          # no player identity kept
    assert cs.parse_session(None) is None and cs.parse_session({"isSpectating": True}) is None
    classic = session()
    classic["theirTeam"][0]["championId"] = 60081                     # League Classic ids
    assert cs.parse_session(classic) is None


def test_card_mid_matchup_tips_and_start_items():
    card = cs.build_card(cs.parse_session(session()))
    assert card is not None and card.title == "AHRI · MID"
    assert card.opponent == "Zed" and not card.opponent_sure
    assert len(card.tips) == 3 and all(" : " in t and len(t.split()) <= 12 for t in card.tips)
    assert any("Lee Sin" in t for t in card.tips)                     # enemy jungler line
    ids = [i for i, _n, _g in card.start_items]
    assert ids[0] == 1056 and card.start_items[0][1] == "Anneau de Doran" and card.start_gold >= 450
    assert card.lines[0] == "Face à Zed (probable)" and card.lines[-1].startswith("Départ : Anneau de Doran")


def test_card_roles_and_assigned_enemy_position():
    sup = cs.build_card(cs.parse_session(session("Thresh", "utility", enemies=("Leona", "Jinx"),
                                                 enemy_pos={"Leona": "utility"})))
    assert sup.opponent == "Leona" and sup.opponent_sure and sup.start_items[0][0] == 3865
    assert any("quête de support" in t for t in sup.tips)
    jg = cs.build_card(cs.parse_session(session("Amumu", "jungle")))
    assert jg.opponent is None and jg.start_items[0][0] == 1103       # tank pet
    adc = cs.build_card(cs.parse_session(session("Jinx", "bottom")))
    assert adc.start_items[0][0] == 1055 and adc.opponent == "Jinx" or adc.opponent is not None
    unknown_role = cs.build_card(cs.parse_session(session("Ahri", "")))
    assert unknown_role is not None and unknown_role.title == "AHRI" and unknown_role.opponent is None
    assert cs.build_card(None) is None


def test_watcher_polls_read_only_and_rate_limits():
    calls = []

    class FakeClient:
        def get(self, path):
            calls.append(path)
            return session() if len(calls) < 3 else None

    t = [0.0]
    w = cs.ChampSelectWatcher(client=FakeClient(), clock=lambda: t[0])
    assert w.poll() is not None and w.card().my_alias == "Ahri"
    assert w.poll() is w.card() and len(calls) == 1                    # rate limited
    t[0] += cs.POLL_S
    w.poll()
    t[0] += cs.POLL_S
    assert w.poll() is None and w.card() is None                       # champ select over
    assert calls == [cs.SESSION_PATH] * 3
