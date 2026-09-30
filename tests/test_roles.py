"""Tests for treeaicoach.roles: one role per player and team, with and without Riot positions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from treeaicoach import roles as R
from treeaicoach.live_client import GameInfo, PlayerInfo, parse_allgamedata
from treeaicoach.roles import RoleResolver, assign_roles, champion_prior, spell_kinds
from treeaicoach.tracker import Tracker

try:
    from treeaicoach.detector import Detection
    from treeaicoach.identifier import Identified
except Exception:  # pragma: no cover
    from tests.test_gank import Detection, Identified  # type: ignore


def P(alias: str, team: str, pos: str = "", smite: bool = False, spells: tuple[str, ...] = (),
      name: str | None = None) -> PlayerInfo:
    return PlayerInfo(riot_id=f"{alias}#T", summoner_name=alias, champion_alias=alias,
                      champion_name=name or alias, team=team, position=pos, has_smite=smite,
                      spells=spells)


def game(me: PlayerInfo, allies: list[PlayerInfo], enemies: list[PlayerInfo], gt: float = 400.0) -> GameInfo:
    return GameInfo(game_time=gt, game_mode="CLASSIC", map_number=11, me=me, allies=allies,
                    enemies=enemies, fetched_at=0.0)


def roles_of(res: RoleResolver, side: str) -> dict[str, str | None]:
    return {i.alias: i.role for i in res.roles().values() if i.side == side}


def test_champion_table_covers_every_bundled_champion() -> None:
    index = Path(R.__file__).parent / "assets" / "icons" / "champions" / "index.json"
    aliases = {c["alias"] for c in json.loads(index.read_text(encoding="utf-8"))["champions"]}
    assert len(aliases) >= 170
    assert aliases <= set(R.CHAMPION_ROLES)
    for letters in R.CHAMPION_ROLES.values():
        assert letters and set(letters) <= set("TJMBU") and len(set(letters)) == len(letters)


def test_champion_prior_table_tags_and_unknown() -> None:
    assert max(champion_prior("Jinx"), key=champion_prior("Jinx").get) == "BOTTOM"
    assert max(champion_prior("thresh"), key=champion_prior("thresh").get) == "UTILITY"   # case-insensitive
    tagged = champion_prior("NewChamp", tags=["Marksman", "Mage"])
    assert tagged["BOTTOM"] > tagged["MIDDLE"] > 0
    assert set(champion_prior(None).values()) == {0.0}


def test_spell_kinds_localized_and_raw_ids() -> None:
    fr = P("Jinx", "ORDER", spells=("Saut éclair", "Soins"))
    assert spell_kinds(fr) == {"heal"}
    raw = PlayerInfo(champion_alias="Nami", spell_ids=("SummonerFlash", "SummonerExhaust"))
    assert spell_kinds(raw) == {"exhaust"}
    assert "smite" in spell_kinds(P("Vi", "ORDER", smite=True))
    assert spell_kinds(P("Garen", "ORDER", spells=("Téléportation", "Embrasement"))) == {"teleport", "ignite"}


def test_live_client_parses_spell_ids() -> None:
    def pl(name: str, team: str, s1: str, s2: str) -> dict:
        return {"championName": name, "rawChampionName": f"game_character_displayname_{name}",
                "team": team, "position": "", "summonerName": name, "riotId": f"{name}#EUW",
                "summonerSpells": {
                    "summonerSpellOne": {"displayName": "Saut éclair",
                                         "rawDisplayName": f"GeneratedTip_SummonerSpell_{s1}_DisplayName"},
                    "summonerSpellTwo": {"displayName": "x",
                                         "rawDisplayName": f"GeneratedTip_SummonerSpell_{s2}_DisplayName"}}}
    data = {"activePlayer": {"riotId": "Jinx#EUW", "summonerName": "Jinx"},
            "allPlayers": [pl("Jinx", "ORDER", "SummonerFlash", "SummonerHeal"),
                           pl("Ahri", "CHAOS", "SummonerFlash", "SummonerDot")],
            "gameData": {"gameTime": 100.0, "mapNumber": 11, "gameMode": "CLASSIC"}, "events": {"Events": []}}
    g = parse_allgamedata(data, now=0.0)
    assert g is not None and g.me is not None
    assert g.me.spell_ids == ("SummonerFlash", "SummonerHeal")
    assert spell_kinds(g.me) == {"heal"}


def test_assign_roles_is_a_permutation_maximising_the_total() -> None:
    scores = [{"TOP": 1.0, "MIDDLE": 0.9}, {"TOP": 5.0}, {"MIDDLE": 1.0}]
    out = assign_roles(scores)
    assert out[1] == "TOP" and out[2] == "MIDDLE" and out[0] not in ("TOP", "MIDDLE")
    assert len(set(assign_roles([{}] * 5))) == 5
    six = assign_roles([{"TOP": 1.0}] * 6)
    assert sum(r is None for r in six) == 1 and len({r for r in six if r}) == 5
    assert assign_roles([]) == []


def test_riot_positions_win() -> None:
    me = P("Garen", "ORDER", "TOP")
    allies = [P("Vi", "ORDER", "JUNGLE", smite=True), P("Lux", "ORDER", "MIDDLE"),
              P("Jinx", "ORDER", "BOTTOM"), P("Renata", "ORDER", "UTILITY")]
    # odd picks: Riot says Yasuo is BOTTOM, Seraphine MIDDLE
    enemies = [P("Darius", "CHAOS", "TOP"), P("LeeSin", "CHAOS", "JUNGLE", smite=True),
               P("Seraphine", "CHAOS", "MIDDLE"), P("Yasuo", "CHAOS", "BOTTOM"),
               P("Nautilus", "CHAOS", "UTILITY")]
    res = RoleResolver()
    res.update(0.0, None, game(me, allies, enemies))
    assert roles_of(res, "enemy") == {"Darius": "TOP", "LeeSin": "JUNGLE", "Seraphine": "MIDDLE",
                                      "Yasuo": "BOTTOM", "Nautilus": "UTILITY"}
    assert all(i.source == "riot" and i.confidence == 1.0 for i in res.roles().values())
    assert res.my_role() == "TOP" and res.me().is_me
    assert res.lane_opponents() == frozenset({"Darius"})
    assert res.enemy_jungler() == "LeeSin"
    assert res.bot_lane("enemy") == ("Yasuo", "Nautilus")
    assert res.bot_lane("ally") == ("Jinx", "Renata")
    assert res.info("leesin").label_fr == "Jungle" and res.info("Jinx").short == "ADC"


def test_inference_without_riot_positions_priors_smite_and_spells() -> None:
    me = P("Jinx", "ORDER", spells=("Flash", "Heal"))
    allies = [P("Garen", "ORDER", spells=("Flash", "Teleport")),
              P("Vi", "ORDER", smite=True, spells=("Flash", "Smite")),
              P("Lux", "ORDER", spells=("Flash", "Ignite")),
              P("Renata", "ORDER", spells=("Flash", "Exhaust"))]
    # Senna / Seraphine are both bot-lane-ish: the spells decide (Heal = ADC, Exhaust = support);
    # Graves (marksman tag, jungle main) has Smite.
    enemies = [P("Darius", "CHAOS", spells=("Flash", "Teleport")),
               P("Graves", "CHAOS", smite=True, spells=("Flash", "Smite")),
               P("Ahri", "CHAOS", spells=("Flash", "Ignite")),
               P("Senna", "CHAOS", spells=("Flash", "Heal")),
               P("Seraphine", "CHAOS", spells=("Flash", "Exhaust"))]
    res = RoleResolver()
    res.update(0.0, None, game(me, allies, enemies))
    assert roles_of(res, "ally") == {"Jinx": "BOTTOM", "Garen": "TOP", "Vi": "JUNGLE",
                                     "Lux": "MIDDLE", "Renata": "UTILITY"}
    assert roles_of(res, "enemy") == {"Darius": "TOP", "Graves": "JUNGLE", "Ahri": "MIDDLE",
                                      "Senna": "BOTTOM", "Seraphine": "UTILITY"}
    assert res.info("Graves").source == "smite" and res.info("Ahri").source == "inferred"
    # I am the ADC: my lane opponents are the enemy bot lane pair
    assert res.lane_opponents() == frozenset({"Senna", "Seraphine"})
    assert res.enemy_jungler() == "Graves"


def _ident(u: float, v: float, alias: str, relation: str) -> Identified:
    probs = (0.9, 0.05, 0.05) if relation == "enemy" else (0.05, 0.9, 0.05)
    return Identified(det=Detection(u=u, v=v, r=0.047, score=0.9,
                                    cls="enemy" if relation == "enemy" else "ally", cls_probs=probs),
                      alias=alias, relation=relation, team=None, id_score=0.8)


@pytest.mark.parametrize("with_minimap", [False, True])
def test_minimap_occupancy_between_1m30_and_5m_decides(with_minimap: bool) -> None:
    """Yasuo and Ahri both like mid; the minimap shows who actually lanes where."""
    me = P("Garen", "ORDER")
    allies = [P("Vi", "ORDER", smite=True), P("Lux", "ORDER"), P("Jinx", "ORDER"), P("Renata", "ORDER")]
    enemies = [P("Yasuo", "CHAOS"), P("Ahri", "CHAOS"), P("LeeSin", "CHAOS", smite=True),
               P("Caitlyn", "CHAOS"), P("Nautilus", "CHAOS")]
    res = RoleResolver()
    tracker = Tracker()
    fps = 4.0
    for i in range(int(200 * fps)):
        t = i / fps
        gt = 60.0 + t                                     # 1:00 .. 4:20 of game time
        items = [_ident(0.09, 0.30, "Garen", "self")]
        if with_minimap:
            items += [_ident(0.50 + 0.01 * (i % 3), 0.50, "Yasuo", "enemy"),     # mid lane
                      _ident(0.30 + 0.01 * (i % 3), 0.083, "Ahri", "enemy")]     # top lane
        tracker.update(t, items)
        res.update(t, tracker, game(me, allies, enemies, gt=gt))
    got = roles_of(res, "enemy")
    if with_minimap:
        assert got["Yasuo"] == "MIDDLE" and got["Ahri"] == "TOP"
        assert res.info("Yasuo").confidence > 0.5
    else:
        assert got["Ahri"] == "MIDDLE" and got["Yasuo"] == "TOP"
    assert got["LeeSin"] == "JUNGLE" and {got["Caitlyn"], got["Nautilus"]} == {"BOTTOM", "UTILITY"}
    assert got["Caitlyn"] == "BOTTOM"
    assert len(set(got.values())) == 5                   # one role per player
    # me (top) faces the enemy top laner
    assert res.lane_opponents() == frozenset({"Ahri"} if with_minimap else {"Yasuo"})


def test_occupancy_outside_the_early_window_is_ignored() -> None:
    me = P("Garen", "ORDER")
    enemies = [P("Yasuo", "CHAOS"), P("Ahri", "CHAOS")]
    res = RoleResolver()
    tracker = Tracker()
    for i in range(400):
        t = i / 4.0
        tracker.update(t, [_ident(0.50, 0.50, "Yasuo", "enemy"), _ident(0.30, 0.083, "Ahri", "enemy")])
        res.update(t, tracker, game(me, [], enemies, gt=600.0 + t))     # 10:00+: roams, not lanes
    assert roles_of(res, "enemy") == {"Ahri": "MIDDLE", "Yasuo": "TOP"}


def test_mirror_match_and_robustness() -> None:
    me = P("Ahri", "ORDER", "MIDDLE")
    enemies = [P("Ahri", "CHAOS", "MIDDLE"), P("LeeSin", "CHAOS", "JUNGLE", smite=True)]
    res = RoleResolver()
    res.update(0.0, None, game(me, [], enemies))
    all_roles = res.roles()
    assert all_roles["Ahri"].role == "MIDDLE" and all_roles["Ahri~enemy"].side == "enemy"
    assert res.lane_opponents() == frozenset({"Ahri"})
    res.update(float("nan"), None, None)
    res.update(1.0, object(), object())                   # garbage: logged, never raises
    res.update(2.0, None, None)
    assert res.role_of("Ahri", "ally") == "MIDDLE"
    res.reset()
    assert res.roles() == {} and res.my_role() is None and res.lane_opponents() == frozenset()


def test_lane_opponent_roles() -> None:
    assert R.lane_opponent_roles("BOTTOM") == R.lane_opponent_roles("UTILITY") == frozenset({"BOTTOM", "UTILITY"})
    assert R.lane_opponent_roles("TOP") == frozenset({"TOP"})
    assert R.lane_opponent_roles("JUNGLE") == frozenset() == R.lane_opponent_roles(None)
    assert R.normalize_role("support") == "UTILITY" and R.normalize_role("MID") == "MIDDLE"
    assert R.normalize_role("") is None and R.normalize_role(3) is None
