"""Ganks of the friends in the voice channel: voice members <-> allies (discord_friends.py), the ally gank
watcher (ally_gank.py) on a real Tracker, the bot's filter (duo dedup, rate limits) and the engine hook."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from treeaicoach import discord_bot as db
from treeaicoach import discord_friends as df
from treeaicoach.alerts import Level
from treeaicoach.ally_gank import ALLY_REPEAT_S, AllyGankWatcher, lane_at
from treeaicoach.config import Config
from treeaicoach.live_client import GameInfo, PlayerInfo
from treeaicoach.tracker import Tracker

from tests.test_discord_bot import TOKEN, FakeBackend, _cfg, _wait, _wav  # noqa: F401

ME_ID = "123456789012345678"
PAUL_ID = "223456789012345678"
LEO_ID = "323456789012345678"
BOB_ID = "423456789012345678"


def _members() -> list[dict]:
    return [{"id": ME_ID, "display": "Dex", "global": "Dexter", "name": "dexter"},
            {"id": PAUL_ID, "display": "xX_P4ul_Xx", "global": "Paul", "name": "paul_59"},
            {"id": LEO_ID, "display": "Léo", "global": "", "name": "leo.k"},
            {"id": BOB_ID, "display": "Bobby", "global": "", "name": "bobby"}]


def _players() -> list[dict]:
    return [{"riot": "DexterMain#EUW", "champion": "Garen", "alias": "Garen", "position": "TOP", "me": True},
            {"riot": "PaulDu59#EUW", "champion": "Jinx", "alias": "Jinx", "position": "BOTTOM", "me": False},
            {"riot": "leok#1234", "champion": "Lux", "alias": "Lux", "position": "UTILITY", "me": False},
            {"riot": "Shadowz#EUW", "champion": "Ahri", "alias": "Ahri", "position": "MIDDLE", "me": False},
            {"riot": "Kaynou#EUW", "champion": "Kayn", "alias": "Kayn", "position": "JUNGLE", "me": False}]


# ------------------------------------------------------------------------------ names
def test_name_normalisation_and_fuzzy_scores() -> None:
    assert df.norm_name("Pâul_59#EUW") == "paul59"
    assert df.norm_name("P4ul") == "paul" and df.norm_name("xX_Paul_Xx") == "paul"
    assert df.norm_name("Alexx") == "alexx" and df.norm_name("🎮") == ""
    assert df.name_score("P4ul", "Paul#EUW") == 1.0
    assert df.name_score("Paul", "PaulDu59") >= df.MATCH_MIN          # contained
    assert df.name_score("Dexter", "Dexterr") >= df.MATCH_MIN         # one typo
    assert df.name_score("Kevin", "Marcel") < 0.5
    assert df.name_score("", "Paul") == 0.0
    assert df.same_riot_id("paul#euw", "Paul#EUW") and df.same_riot_id("Paul", "Paul#EUW")
    assert not df.same_riot_id("Paul#EUW", "Paul#KR1") and not df.same_riot_id("", "Paul")
    assert df.spoken_name("xX_Paul_Xx 🎮") == "Paul" and df.spoken_name("Paul59") == "Paul"
    assert df.spoken_name("🎮🎮") == "" and df.spoken_name(None) == ""
    assert df.best_spoken(["xX_P4ul_Xx", "Paul", "paul_59"]) == "Paul" and df.best_spoken(["P4ul"]) == "P4ul"
    assert df.best_spoken([]) == ""


def test_match_auto_one_to_one_me_and_absent() -> None:
    m = {e["id"]: e for e in df.match(_members(), _players(), {}, ME_ID)}
    assert m[ME_ID]["source"] == "toi" and m[ME_ID]["player"]["alias"] == "Garen"
    assert m[PAUL_ID]["source"] == "auto" and m[PAUL_ID]["player"]["alias"] == "Jinx"
    assert m[LEO_ID]["source"] == "auto" and m[LEO_ID]["player"]["alias"] == "Lux"
    assert m[BOB_ID]["player"] is None and m[BOB_ID]["source"] == "inconnu"   # not in the game
    assert df.describe(m[BOB_ID], True) == "ne joue pas"
    assert df.describe(m[BOB_ID], False) == "en attente de la partie"
    # two members who look like the same player: only the best one gets him
    twins = [{"id": PAUL_ID, "display": "Paul"}, {"id": LEO_ID, "display": "Paul2"}]
    got = df.match(twins, _players(), {}, "")
    assert [e["player"]["alias"] if e["player"] else None for e in got] == ["Jinx", None]
    assert df.match(None, None) == [] and df.match([{"id": ""}], _players()) == []


def test_manual_override_wins_and_absent_link() -> None:
    links = {BOB_ID: "Shadowz#EUW", PAUL_ID: df.NOT_PLAYING, "garbage": "x"}
    m = {e["id"]: e for e in df.match(_members(), _players(), links, ME_ID)}
    assert m[BOB_ID]["source"] == "manuel" and m[BOB_ID]["player"]["alias"] == "Ahri"
    assert m[PAUL_ID]["source"] == "absent" and m[PAUL_ID]["player"] is None
    # a manual link takes the player away from the automatic match
    m2 = {e["id"]: e for e in df.match(_members(), _players(), {BOB_ID: "PaulDu59#EUW"}, ME_ID)}
    assert m2[BOB_ID]["player"]["alias"] == "Jinx" and m2[PAUL_ID]["player"] is None
    # a link to someone of another game: the automatic match still works
    m3 = {e["id"]: e for e in df.match(_members(), _players(), {PAUL_ID: "Nobody#EUW"}, ME_ID)}
    assert m3[PAUL_ID]["player"]["alias"] == "Jinx" and m3[PAUL_ID]["source"] == "auto"
    assert df.clean_links({"x1": "a", PAUL_ID: "  Paul  #EUW "}) == {PAUL_ID: "Paul #EUW"}
    assert df.clean_links("nope") == {}


def test_config_fields_defaults_and_validation() -> None:
    c = Config()
    assert c.discord_ally_ganks is True and c.discord_say_name is False and c.discord_links == {}
    c = Config.from_dict({"discord_links": {PAUL_ID: "Paul#EUW", "bad": "x"}, "discord_say_name": True})
    assert c.discord_links == {PAUL_ID: "Paul#EUW"} and c.discord_say_name is True
    assert Config.from_dict({"discord_links": "x"}).discord_links == {}
    from treeaicoach import fingerprint

    assert "discord_links" in fingerprint.PRIVATE_SETTINGS


# ------------------------------------------------------------------------------ the bot filter
def test_filter_duo_dedup_ally_rate_and_gap() -> None:
    now = [0.0]
    f = db.CallFilter(lambda: now[0])
    assert f.admit("gank", "bot", target="Jinx")
    now[0] = 1.0
    assert not f.admit("gank", "bot", target="Lux")          # the bot duo: ONE "Bot gank"
    assert not f.admit("gank", "top", target="Sett")         # never within 3 s of another gank call
    now[0] = 3.5
    assert f.admit("gank", "top", target="Sett")             # another friend, another lane
    now[0] = 7.0
    assert f.admit("gank", "mid")                            # mine (target "")
    now[0] = 12.0
    assert not f.admit("gank", "jungle")                     # me again within 8 s
    now[0] = 16.0
    assert f.admit("gank", "jungle")
    now[0] = 25.0
    assert not f.admit("gank", "mid", target="Ahri")         # 4 gank calls a minute at most
    now[0] = 31.0
    assert not f.admit("gank", "bot", target="Jinx")         # 0 s + 30: Jinx again, still capped (4/min)
    now[0] = 61.0
    assert f.admit("gank", "bot", target="Jinx")
    # an objective call never right after a gank call; every call together <= 6 a minute
    assert not f.admit("objective", "dragon:300")
    now[0] = 65.0
    assert f.admit("objective", "dragon:300")


def test_total_calls_per_minute_gank_first() -> None:
    now = [0.0]
    f = db.CallFilter(lambda: now[0])
    said = 0
    for i, lane in enumerate(["top", "mid", "bot", "jungle"] * 4):
        now[0] = i * 4.0
        said += f.admit("gank", lane, target=f"ally{i}")
    assert said <= db.GANK_MAX_PER_MIN + 1               # 64 s: at most 4 in any minute
    times = list(f._all_times)
    assert all(sum(1 for t in times if t0 <= t < t0 + 60) <= db.CALLS_MAX_PER_MIN for t0 in times)


# ------------------------------------------------------------------------------ the bot
class FriendsBackend(FakeBackend):
    """A fake Discord with one voice channel: me, Paul, Léo, Bobby."""

    def describe(self, user_id: str) -> dict:
        occ = _members()
        ch = {"id": "101", "name": "général", "members": len(occ), "people": [o["display"] for o in occ],
              "can_speak": True, "missing": [], "me_here": True, "bot_here": True, "occupants": occ}
        return {"guilds": [{"id": "10", "name": "Les potes", "members": 5, "voice_channels": [ch]}],
                "user": {"id": user_id, "found": True, "name": "Dex", "channel_id": "101", "channel": "général",
                         "guild": "Les potes", "guild_id": "10"},
                "channel": {"id": "101", "name": "général", "guild": "Les potes", "guild_id": "10"},
                "latency_ms": 30}


@pytest.fixture
def fbot():  # noqa: ANN201
    FakeBackend.instances = []
    c = db.DiscordCaller(backend_factory=FriendsBackend, speech=lambda t, v, r: _wav(),
                         beep=lambda: _wav(0.05, 32000))
    yield c
    c.shutdown(2.0)


def test_bot_maps_voice_members_and_gives_ally_targets(fbot) -> None:  # noqa: ANN001
    assert fbot.on_call("roster", "", {"players": _players()})      # kept even while the bot is off
    assert fbot.ally_targets() == {}                                # bot off: nothing to watch
    fbot.configure(_cfg(discord_user_id=ME_ID))
    assert _wait(lambda: fbot.snapshot()["state"] == "connected" and fbot.snapshot()["guilds"])
    assert fbot.ally_targets() == {"Jinx": "Paul", "Lux": "Léo"}         # the name the voice reads well
    snap = fbot.snapshot()
    by = {e["id"]: e for e in snap["voice_map"]}
    assert by[ME_ID]["source"] == "toi" and by[BOB_ID]["player"] is None and len(snap["roster"]) == 5
    # the player fixes Bobby by hand (he plays Ahri) and says Léo is not in the game
    fbot.configure(_cfg(discord_user_id=ME_ID, discord_links={BOB_ID: "Shadowz#EUW", LEO_ID: "-"}))
    assert fbot.ally_targets() == {"Jinx": "Paul", "Ahri": "Bobby"}
    fbot.configure(_cfg(discord_user_id=ME_ID, discord_ally_ganks=False))
    assert fbot.ally_targets() == {}
    assert not fbot.on_call("ally_gank", "", {"lane": "bot", "ally": "Jinx"})   # switched off
    fbot.on_call("game_end")
    assert fbot.snapshot()["roster"] == []


def test_ally_gank_spoken_with_duo_dedup_and_name_option(fbot) -> None:  # noqa: ANN001
    fbot.configure(_cfg(discord_user_id=ME_ID))
    assert _wait(lambda: fbot.snapshot()["state"] == "connected")
    fbot.on_call("game_start")
    fbot.on_call("roster", "", {"players": _players()})
    assert fbot.on_call("ally_gank", "", {"lane": "bot", "ally": "Jinx", "name": "xX_P4ul_Xx"})
    assert not fbot.on_call("ally_gank", "", {"lane": "bot", "ally": "Lux", "name": "Léo"})   # duo: one call
    assert not fbot.on_call("ally_gank", "", {"lane": "bot"})                                 # nobody: dropped
    assert _wait(lambda: "Bot gank" in fbot.spoken)
    # "dire le pseudo": "Paul, bot gank" (rendered beforehand; the plain call when not ready yet)
    fbot.configure(_cfg(discord_user_id=ME_ID, discord_say_name=True))
    fbot._filter.reset()
    assert fbot.render_names() >= 1
    assert fbot.on_call("ally_gank", "", {"lane": "top", "ally": "Jinx", "name": "Paul"})
    assert _wait(lambda: "Paul, top gank" in fbot.spoken)
    assert fbot.speaks_for("ally_gank") is False                # the PC voice never had these calls


# ------------------------------------------------------------------------------ the watcher (real Tracker)
def _pi(alias: str, team: str, pos: str, smite: bool = False) -> PlayerInfo:
    return PlayerInfo(riot_id=f"{alias}#T", champion_alias=alias, champion_name=alias, team=team, position=pos,
                      has_smite=smite)


ME = _pi("Garen", "ORDER", "TOP")
ALLIES = [_pi("Jinx", "ORDER", "BOTTOM"), _pi("Lux", "ORDER", "UTILITY"), _pi("Ahri", "ORDER", "MIDDLE"),
          _pi("Kayn", "ORDER", "JUNGLE", True)]
ENEMIES = [_pi("LeeSin", "CHAOS", "JUNGLE", True), _pi("Caitlyn", "CHAOS", "BOTTOM"), _pi("Zed", "CHAOS", "MIDDLE"),
           _pi("Darius", "CHAOS", "TOP"), _pi("Nami", "CHAOS", "UTILITY")]


def _ident(u: float, v: float, rel: str, alias: str, team: str | None) -> SimpleNamespace:
    return SimpleNamespace(det=SimpleNamespace(u=u, v=v, r=0.025, score=0.9), relation=rel, alias=alias, team=team,
                           id_score=0.9)


def _walk(a: tuple[float, float], b: tuple[float, float], t: float, speed: float = 0.026) -> tuple[float, float]:
    d = math.dist(a, b)
    k = min(1.0, speed * t / d) if d > 0 else 1.0
    return a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k


def _replay(scene, friends, seconds: float = 16.0, step: float = 0.1, watcher=None):  # noqa: ANN001, ANN202
    """``scene(t) -> [(u, v, relation, alias)]``; returns the watcher's calls."""
    tracker = Tracker()
    tracker.set_roster({"Garen": "self", **{p.champion_alias: "ally" for p in ALLIES},
                        **{p.champion_alias: "enemy" for p in ENEMIES}})
    w = watcher or AllyGankWatcher(Config())
    calls = []
    t = 0.0
    while t <= seconds:
        game = GameInfo(game_time=300.0 + t, map_number=11, game_mode="CLASSIC", me=ME, allies=ALLIES,
                        enemies=ENEMIES, fetched_at=t)
        tracker.update(t, [_ident(u, v, rel, al, "ORDER" if rel != "enemy" else None) for u, v, rel, al in scene(t)])
        calls += w.update(t, tracker, game, friends)
        t = round(t + step, 3)
    return calls


JINX, LUX, AHRI = (0.80, 0.94), (0.83, 0.93), (0.50, 0.50)
LEE_FROM = (0.62, 0.70)                    # enemy jungle / river side of the bot lane


def _bot_gank(t: float) -> list:
    lee = _walk(LEE_FROM, JINX, max(0.0, t - 2.0))
    return [(0.08, 0.30, "self", "Garen"), (*JINX, "ally", "Jinx"), (*LUX, "ally", "Lux"), (*AHRI, "ally", "Ahri"),
            (0.92, 0.86, "enemy", "Caitlyn"), (0.93, 0.83, "enemy", "Nami"), (*lee, "enemy", "LeeSin")]


def test_ally_gank_fires_for_the_mapped_ally_only_with_his_lane() -> None:
    calls = _replay(_bot_gank, ["Jinx", "Garen"])
    assert calls, "the enemy jungler walking at Jinx must be called"
    c = calls[0]
    assert c.alias == "Jinx" and c.lane == "bot" and c.threats[0] == "LeeSin" and c.level >= Level.WARNING
    assert len([x for x in calls if x.alias == "Jinx"]) == 1               # once per gank (30 s)
    assert all(x.alias != "Garen" for x in calls)                           # my own ganks: gank.py
    # Ahri (not in voice) is not watched; Caitlyn / Nami (Jinx's lane opponents) never raise a call
    assert not _replay(_bot_gank, ["Ahri"])
    assert not [x for x in calls if "Caitlyn" in x.threats or "Nami" in x.threats]


def test_ally_gank_lead_time_and_duo() -> None:
    calls = _replay(_bot_gank, ["Jinx", "Lux"])
    assert {c.alias for c in calls} == {"Jinx", "Lux"} and {c.lane for c in calls} == {"bot"}
    contact = 2.0 + (math.dist(LEE_FROM, JINX) - 0.03) / 0.026            # Lee Sin on Jinx
    assert contact - min(c.t for c in calls) >= 5.0                         # >= 5 s before contact
    # the bot says it once (duo dedup in the filter)
    now = [0.0]
    f = db.CallFilter(lambda: now[0])
    said = [f.admit("gank", c.lane, target=c.alias) for c in sorted(calls, key=lambda c: c.t)]
    assert said.count(True) == 1


def test_no_call_when_the_ally_is_dead_in_base_or_safe_mode() -> None:
    def in_base(t: float) -> list:
        return [(0.04, 0.96, "ally", "Jinx"), (*_walk((0.25, 0.75), (0.04, 0.96), t), "enemy", "LeeSin")]
    assert not _replay(in_base, ["Jinx"])
    assert not _replay(_bot_gank, ["Jinx"], watcher=AllyGankWatcher(Config(safe_mode=True)))
    assert not _replay(_bot_gank, ["Jinx"], watcher=AllyGankWatcher(Config(alert_jungler_approach=False)))


def test_ally_rate_limit_and_lane_words() -> None:
    w = AllyGankWatcher(Config())
    calls = _replay(_bot_gank, ["Jinx"], seconds=16.0, watcher=w)
    assert len(calls) == 1 and ALLY_REPEAT_S >= 30.0
    assert lane_at((0.5, 0.5)) == "mid" and lane_at((0.1, 0.3)) == "top" and lane_at((0.3, 0.3)) in ("jungle", "mid")
    assert lane_at((0.03, 0.97)) is None and lane_at(None, "UTILITY") == "bot"
    assert w.update(float("nan"), None, None, ["Jinx"]) == []                # never raises


# ------------------------------------------------------------------------------ engine hook
class _Eng:
    def __init__(self, cfg, targets) -> None:  # noqa: ANN001
        from treeaicoach.engine_coaching import CoachingMixin

        self.__class__ = type("E", (CoachingMixin,), {})
        self._cfg = cfg
        self._demo = False
        self._frame_source = None
        self._gank = None
        self.calls: list = []
        self.said: list = []
        self.on_call = lambda k, t, i: self.calls.append((k, t, i)) or True
        self.ally_targets = lambda: targets
        self._say = lambda *a, **k: self.said.append(a)


def test_engine_sends_roster_then_ally_ganks_to_the_bot_only() -> None:
    e = _Eng(Config(discord_enabled=True), {"Jinx": "Paul"})
    tracker = Tracker()
    tracker.set_roster({"Garen": "self", **{p.champion_alias: "ally" for p in ALLIES},
                        **{p.champion_alias: "enemy" for p in ENEMIES}})
    t = 0.0
    while t <= 16.0:
        game = GameInfo(game_time=300.0 + t, map_number=11, game_mode="CLASSIC", me=ME, allies=ALLIES,
                        enemies=ENEMIES, fetched_at=t)
        tracker.update(t, [_ident(u, v, rel, al, "ORDER" if rel != "enemy" else None) for u, v, rel, al in _bot_gank(t)])
        e._discord_ally_ganks(t, game, tracker)
        t = round(t + 0.1, 3)
    kinds = [k for k, _t, _i in e.calls]
    assert kinds.count("roster") == 1 and kinds.count("ally_gank") == 1
    roster = next(i for k, _t, i in e.calls if k == "roster")["players"]
    assert roster[0]["me"] and roster[0]["alias"] == "Garen" and {p["alias"] for p in roster} >= {"Jinx", "Lux"}
    info = next(i for k, _t, i in e.calls if k == "ally_gank")
    assert info["lane"] == "bot" and info["ally"] == "Jinx" and info["name"] == "Paul"
    assert e.said == []                                                     # the PC voice stays silent
    # switched off: no watcher work at all
    e2 = _Eng(Config(discord_enabled=True, discord_ally_ganks=False), {})
    e2.ally_targets = None  # type: ignore[assignment]
    del e2.ally_targets
    e2._discord_ally_ganks(1.0, GameInfo(me=ME, allies=ALLIES, enemies=ENEMIES), tracker)
    assert e2.calls == []


# ------------------------------------------------------------------------------ the page (pure part)
def test_page_rows_show_mapping_and_dropdown() -> None:
    from treeaicoach.ui_page_discord import friend_rows

    snap = {"voice_map": df.match(_members(), _players(), {}, ME_ID), "roster": _players()}
    rows = {r["id"]: r for r in friend_rows(snap, {})}
    assert rows[PAUL_ID]["title"] == "xX_P4ul_Xx : joue Jinx (ADC)" and rows[PAUL_ID]["editable"]
    assert rows[LEO_ID]["title"] == "Léo : joue Lux (support)"
    assert rows[BOB_ID]["title"] == "Bobby : ne joue pas"
    assert not rows[ME_ID]["editable"]
    labels = [lbl for _v, lbl in rows[BOB_ID]["choices"]]
    assert labels[:2] == ["Auto", "Ne joue pas"] and "Ahri (mid)" in labels and "Garen (top)" not in labels
    idle = friend_rows({"voice_map": df.match(_members(), [], {}, ME_ID), "roster": []}, {})
    assert all(not r["editable"] for r in idle) and "en attente de la partie" in idle[1]["title"]
    assert all("—" not in r["title"] + r["desc"] for r in friend_rows(snap, {}) + idle)
