"""Bot Discord (discord_bot.py) with a fake Discord layer: no network, no discord.py needed."""

from __future__ import annotations

import asyncio
import io
import logging
import threading
import time
import wave
from types import SimpleNamespace

import pytest

from treeaicoach import discord_bot as db
from treeaicoach.config import Config

# fake, shaped like a real one (assembled at runtime so secret scanners do not flag the source)
TOKEN = ".".join(["MTIzNDU2Nzg5MDEyMzQ1Njc4OQ", "GabcDE", "abcdefghijklmnopqrstuvwxyz" + "0123456789AB"])


def _wav(seconds: float = 0.1, rate: int = 24000) -> bytes:
    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x10\x00" * int(seconds * rate))
    return bio.getvalue()


class FakeBackend:
    instances: list[FakeBackend] = []

    def __init__(self, *, bad: bool = False, channel: str | None = "général", hang: float = 0.0) -> None:
        self.bad, self.channel, self.hang = bad, channel, hang
        self.played: list[int] = []
        self.joined: list[str] = []
        self.left = 0
        self.closed = 0
        self.tokens: list[str] = []
        self.current = None                 # where the bot is
        self.join_error: BaseException | None = None
        FakeBackend.instances.append(self)

    def current_channel(self):  # noqa: ANN201
        return self.current

    async def start(self, token: str) -> tuple[str, str]:
        self.tokens.append(token)
        if self.hang:
            await asyncio.sleep(self.hang)
        if self.bad:
            raise db.BadToken()
        return "TreeAI", "123456789012345678"

    def alive(self) -> bool:
        return True

    def guild_count(self) -> int:
        return 1

    def find_channel(self, user_id: str):  # noqa: ANN201
        return self.channel

    def channel_name(self, ch) -> str:  # noqa: ANN001
        return str(ch)

    def can_speak(self, ch) -> bool:  # noqa: ANN001
        return True

    async def join(self, ch) -> None:  # noqa: ANN001
        if self.join_error is not None:
            raise self.join_error
        self.joined.append(ch)
        self.current = ch

    async def play(self, pcm: bytes) -> None:
        self.played.append(len(pcm))

    async def leave(self) -> None:
        self.left += 1
        self.current = None

    async def close(self) -> None:
        self.closed += 1


def _cfg(**kw) -> SimpleNamespace:  # noqa: ANN003
    base = dict(discord_enabled=True, discord_token=TOKEN, discord_user_id="123456789012345678",
                neural_voice="", neural_rate="")
    base.update(kw)
    return SimpleNamespace(**base)


def _wait(cond, timeout: float = 5.0) -> bool:  # noqa: ANN001
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def bot():  # noqa: ANN201
    FakeBackend.instances = []
    spoken: list[str] = []

    def speech(text: str, _v: str, _r: str) -> bytes:
        spoken.append(text)
        return _wav()
    c = db.DiscordCaller(backend_factory=FakeBackend, speech=speech, beep=lambda: _wav(0.05, 32000))
    c.tts_texts = spoken  # type: ignore[attr-defined]
    yield c
    c.shutdown(2.0)


# ------------------------------------------------------------------------------ pure parts
def test_gank_text_is_only_lane_and_gank() -> None:
    assert db.gank_text("top") == "Top gank"
    assert db.gank_text("MID") == "Mid gank"
    assert db.gank_text("bot") == "Bot gank"
    assert db.gank_text(None) == "" and db.gank_text("jungle") == ""


def test_filter_kinds_dedup_and_rate_limit() -> None:
    now = [0.0]
    f = db.CallFilter(lambda: now[0])
    assert not f.admit("tip") and not f.admit("death")
    assert f.admit("matchup") and not f.admit("matchup")          # once per game
    assert f.admit("gank", "bot")
    now[0] = 3.0
    assert not f.admit("gank", "top")                              # min gap
    now[0] = 10.0
    assert not f.admit("gank", "bot")                              # same lane within 30 s
    assert f.admit("gank", "top")
    now[0] = 20.0
    assert f.admit("gank", "mid")
    now[0] = 41.0
    assert f.admit("gank", "bot")
    now[0] = 50.0
    assert not f.admit("gank", "top")                              # 4 a minute at most (0, 10, 20, 41)
    f.reset()
    assert f.admit("matchup")


def test_matchup_sentences() -> None:
    s = db.matchup_verdict("Garen", "Darius", "them", 480, "us")
    assert s == "Garen contre Darius : défavorable, joue safe jusqu'à 8 minutes ; ton équipe est plus forte."
    assert db.matchup_verdict("Garen", "Teemo", "us").startswith("Garen contre Teemo : favorable")
    assert "équilibré" in db.matchup_verdict("Swain", "Lux", "even")
    assert db.matchup_verdict("", "Lux", "us") == ""
    assert db.clean_ai_line('« Garen contre Darius : défavorable, joue safe jusqu\'au niveau 6. » Bonus', opp="Darius") \
        == "Garen contre Darius : défavorable, joue safe jusqu'au niveau 6."
    assert db.clean_ai_line("ok", opp="Darius") == ""
    assert db.clean_ai_line("Tu es favorisé dans cette voie, joue agressif.", opp="Darius") == ""   # no opponent
    assert db.clean_ai_line("Darius " + "très " * 60 + "fort.", opp="Darius") == ""


def test_wav_resampled_to_discord_pcm() -> None:
    pcm = db.wav_to_pcm48(_wav(0.1, 24000))
    assert len(pcm) == 0.1 * 48000 * 4 and len(pcm) % 3840 == 0
    assert len(db.wav_to_pcm48(_wav(0.05, 32000))) % 3840 == 0
    assert db.wav_to_pcm48(b"junk") == b""


def test_invite_url_from_token() -> None:
    app_id = db.application_id_from_token(TOKEN)
    assert app_id == "1234567890123456789"
    url = db.invite_url(app_id)
    assert url and "client_id=1234567890123456789" in url and "permissions=3146752" in url and "scope=bot" in url
    assert db.invite_url(db.application_id_from_token("nope")) is None
    assert db.clean_token(f' "Bot {TOKEN}" ') == TOKEN
    assert db.clean_user_id("<@123456789012345678>") == "123456789012345678" and db.clean_user_id("12") == ""


# ------------------------------------------------------------------------------ the caller
def test_disabled_by_default_and_hook_never_raises(bot) -> None:  # noqa: ANN001
    assert Config().discord_enabled is False
    assert not bot.on_call("gank", "x", {"lane": "bot"})              # not configured
    bot.configure(_cfg(discord_enabled=False))
    assert not bot.on_call("gank", "x", {"lane": "bot"})
    assert bot._thread is None                                      # nothing started
    bot.configure(_cfg())
    assert not bot.on_call("death_recap", "Tu es mort", None)       # other kinds dropped
    assert not bot.on_call(None, None, "junk")  # type: ignore[arg-type]
    assert not bot.on_call("gank", "Gank !", {"lane": None})        # no lane: nothing said


def test_lifecycle_join_say_leave_stop(bot) -> None:  # noqa: ANN001
    bot.configure(_cfg())
    assert _wait(lambda: bot.status()[1])
    fb = FakeBackend.instances[-1]
    assert fb.tokens == [TOKEN]
    assert bot.on_call("game_start")
    assert _wait(lambda: fb.joined == ["général"])
    assert bot.status() == ("Connecté à #général.", True)
    assert bot.on_call("gank", "Gank ! Lee Sin, recule !", {"lane": "bot"})
    assert not bot.on_call("gank", "Lee Sin !", {"lane": "bot"})    # dedup
    assert _wait(lambda: bot.spoken == ["Bot gank"])
    assert "Bot gank" in bot.tts_texts and "Lee Sin" not in " ".join(bot.tts_texts)
    # chime + gap + words, 48 kHz stereo frames
    assert fb.played and fb.played[-1] % 3840 == 0 and fb.played[-1] > len(db.wav_to_pcm48(_wav()))
    assert bot.on_call("game_end")
    time.sleep(0.2)
    assert fb.left == 0                                             # game over: stays with the player in voice
    fb.channel = None                                               # the player leaves voice: the bot too
    bot._poke()
    assert _wait(lambda: fb.left >= 1)
    th = bot._thread
    bot.shutdown(3.0)
    assert fb.closed == 1 and not th.is_alive()
    assert not bot.on_call("gank", "x", {"lane": "top"})            # after shutdown: nothing


def test_gank_clips_prerendered_at_start(bot) -> None:  # noqa: ANN001
    bot.configure(_cfg())
    assert _wait(lambda: len(bot._clips) == 3)
    assert {"Top gank", "Mid gank", "Bot gank"} <= set(bot.tts_texts)


def test_matchup_ai_sentence_then_fallback(bot, monkeypatch) -> None:  # noqa: ANN001
    bot.configure(_cfg())
    assert _wait(lambda: bot.status()[1])
    bot.on_call("game_start")
    fallback = db.matchup_verdict("Garen", "Darius", "them", 480)
    assert bot.on_call("matchup", fallback, {"opp": "Darius",
                                             "ai": lambda: "Garen contre Darius : défavorable, joue safe."})
    assert not bot.on_call("matchup", fallback, {"opp": "Darius"})  # once per game
    assert _wait(lambda: bot.spoken == ["Garen contre Darius : défavorable, joue safe."])
    # AI failing, then AI too slow: the coach's own sentence
    monkeypatch.setattr(db, "AI_TIMEOUT_S", 0.2)
    for ai in (lambda: (_ for _ in ()).throw(RuntimeError("down")), lambda: time.sleep(2.0) or "late"):
        bot.on_call("game_start")
        n = len(bot.spoken)
        assert bot.on_call("matchup", fallback, {"opp": "Darius", "ai": ai})
        assert _wait(lambda n=n: len(bot.spoken) == n + 1)
        assert bot.spoken[-1] == fallback


def test_stale_gank_is_dropped(bot) -> None:  # noqa: ANN001
    now = [1000.0]
    bot._clock = lambda: now[0]
    bot._filter = db.CallFilter(bot._clock)
    bot.configure(_cfg())
    bot._settings  # configured
    with bot._lock:
        bot._jobs.append(db._Job("say", "Top gank", 1000.0 - db.GANK_STALE_S - 1, db.GANK_STALE_S, "gank", lane="top"))
    bot._poke()
    assert _wait(lambda: bot.dropped >= 1)
    assert bot.spoken == []


def test_bad_token_no_retry_and_clear_status(bot) -> None:  # noqa: ANN001
    calls = []

    def factory() -> FakeBackend:
        calls.append(1)
        return FakeBackend(bad=True)
    bot._factory = factory
    bot.configure(_cfg())
    assert _wait(lambda: bot.status()[0].startswith("Token invalide"))
    res = bot.test_blocking(3.0)
    assert res[1] is False
    time.sleep(0.3)
    assert len(calls) == 1


def test_no_voice_channel_status(bot) -> None:  # noqa: ANN001
    bot._factory = lambda: FakeBackend(channel=None)
    bot.configure(_cfg())
    msg, ok = bot.test_blocking(5.0)
    assert not ok and "salon vocal" in msg


def test_test_button_speaks_and_leaves_outside_game(bot) -> None:  # noqa: ANN001
    bot.configure(_cfg())
    msg, ok = bot.test_blocking(5.0)
    fb = FakeBackend.instances[-1]
    assert ok and "général" in msg and fb.played and fb.left == 0   # it follows the player: stays


def test_hook_never_blocks_while_discord_hangs(bot) -> None:  # noqa: ANN001
    bot._factory = lambda: FakeBackend(hang=3.0)
    bot.configure(_cfg())
    t0 = time.perf_counter()
    for lane in ("top", "mid", "bot") * 20:
        bot.on_call("gank", "x", {"lane": lane})
    bot.on_call("game_start")
    bot.on_call("game_end")
    assert time.perf_counter() - t0 < 0.05
    assert threading.current_thread() is threading.main_thread()


def test_token_never_logged_nor_in_diag(bot, caplog) -> None:  # noqa: ANN001
    caplog.set_level(logging.DEBUG)
    bot.configure(_cfg())
    assert _wait(lambda: bot.status()[1])
    bot.on_call("game_start")
    bot.on_call("gank", "x", {"lane": "top"})
    bot.test_blocking(5.0)
    bot.shutdown(2.0)
    assert TOKEN not in caplog.text
    assert TOKEN not in repr(Config(discord_token=TOKEN))
    assert db.mask_secrets(f"token={TOKEN}") == "token=[token masqué]"
    from treeaicoach import diag, fingerprint

    assert TOKEN not in diag._mask_paths(f"Authorization: Bot {TOKEN}")
    assert "discord_token" in fingerprint.PRIVATE_SETTINGS and "discord_user_id" in fingerprint.PRIVATE_SETTINGS


def test_config_validation() -> None:
    c = Config.from_dict({"discord_enabled": "yes", "discord_token": 5, "discord_user_id": "  42  "})
    assert c.discord_token == "" and c.discord_user_id == "42"


# ------------------------------------------------------------------------------ engine hook
class _Eng:
    """The CoachingMixin hook methods on a minimal object."""

    def __init__(self, cfg) -> None:  # noqa: ANN001
        from treeaicoach.engine_coaching import CoachingMixin

        self.__class__ = type("E", (CoachingMixin,), {})
        self._cfg = cfg
        self._demo = False
        self._frame_source = None
        self._tracker = None
        self._game = None
        self._discord_matchup_done = False
        self.calls: list = []
        self.on_call = lambda k, t, i: self.calls.append((k, t, i))


def _player(name: str, pos: str) -> SimpleNamespace:
    return SimpleNamespace(champion_name=name, champion_alias=name, position=pos)


def test_engine_matchup_hook_once_with_fallback_and_ai() -> None:
    cfg = Config(discord_enabled=True, ai_provider="groq", ai_api_key="k")
    e = _Eng(cfg)
    game = SimpleNamespace(game_time=5.0, me=_player("Garen", "TOP"), allies=[_player("Lux", "MIDDLE")],
                           enemies=[_player("Darius", "TOP"), _player("Ahri", "MIDDLE")])
    e._discord_matchup(game)
    e._discord_matchup(game)
    assert len(e.calls) == 1
    kind, text, info = e.calls[0]
    assert kind == "matchup" and text.startswith("Garen contre Darius : ") and info["opp"] == "Darius"
    assert callable(info["ai"])
    e2 = _Eng(Config(discord_enabled=True))
    e2._discord_matchup(game)
    assert e2.calls[0][2]["ai"] is None                        # AI not configured: the rules only
    e3 = _Eng(Config(discord_enabled=True))
    e3._discord_matchup(SimpleNamespace(game_time=5.0, me=_player("Kayn", "JUNGLE"), allies=[], enemies=[]))
    assert e3.calls == []                                      # no lane opponent: nothing


def test_engine_gank_lane_from_role() -> None:
    e = _Eng(Config(discord_enabled=True))
    e._game = SimpleNamespace(me=_player("Swain", "UTILITY"))
    assert e._discord_lane() == "bot"
    e._game = SimpleNamespace(me=_player("Kayn", "JUNGLE"))
    assert e._discord_lane() is None


# ------------------------------------------------------------------------------ launcher view + commands
UID = 123456789012345678


def _perm(view=True, connect=True, speak=True) -> SimpleNamespace:  # noqa: ANN001
    return SimpleNamespace(view_channel=view, connect=connect, speak=speak)


def _member(i: int, name: str) -> SimpleNamespace:
    return SimpleNamespace(id=i, display_name=name, name=name)


def _vchan(i: int, name: str, members: list, perm: SimpleNamespace, pos: int = 0) -> SimpleNamespace:
    return SimpleNamespace(id=i, name=name, position=pos, members=members,
                           voice_states={m.id: object() for m in members}, permissions_for=lambda _me: perm)


def _guilds() -> list:
    me_bot = _member(1, "TreeAI")
    g1 = SimpleNamespace(id=10, name="Les potes", member_count=12, me=me_bot, stage_channels=[],
                         voice_channels=[_vchan(101, "Général", [_member(UID, "Dexter"), _member(43, "Ami"), me_bot],
                                                _perm(), 1),
                                         _vchan(102, "Muet", [], _perm(speak=False), 2)])
    g1.get_member = lambda i: None
    g2 = SimpleNamespace(id=20, name="Clan", member_count=5, me=me_bot, stage_channels=[],
                         voice_channels=[_vchan(201, "Raid", [], _perm())], get_member=lambda i: None)
    return [g1, g2]


def test_describe_guilds_members_and_permissions() -> None:
    guilds, user = db.describe_guilds(_guilds(), str(UID), bot_id=1, bot_channel_id=101)
    assert [g["name"] for g in guilds] == ["Les potes", "Clan"] and guilds[0]["members"] == 12
    gen, muet = guilds[0]["voice_channels"]
    assert gen["members"] == 2 and gen["people"] == ["Ami", "Dexter"]          # the bot not counted
    assert gen["me_here"] and gen["bot_here"] and gen["can_speak"]
    assert not muet["can_speak"] and muet["missing"] == ["parler"] and muet["members"] == 0
    assert user == {"id": str(UID), "found": True, "name": "Dexter", "channel_id": "101", "channel": "Général",
                    "guild": "Les potes", "guild_id": "10"}
    _g, nobody = db.describe_guilds(_guilds(), "99", bot_id=1)
    assert not nobody["found"] and nobody["channel_id"] is None
    assert db.missing_permissions(SimpleNamespace(permissions_for=lambda me: 1 / 0), object()) is None


def test_join_error_causes_in_french() -> None:
    closed = type("ConnectionClosed", (Exception,), {})("Shard closed")
    closed.code = 4017
    assert "DAVE" in db.join_error_text(closed) and "4017" in db.join_error_text(closed)
    closed.code = 4014
    assert "expulsé" in db.join_error_text(closed)
    assert "pare-feu" in db.join_error_text(asyncio.TimeoutError())
    assert db.join_error_text(RuntimeError("Already connected to a voice channel.")) == "déjà connecté à un salon vocal"
    assert "opus" in db.join_error_text(type("OpusNotLoaded", (Exception,), {})())


class ViewBackend(FakeBackend):
    """FakeBackend with servers, voice channels, the player and events (launcher view)."""

    def __init__(self, **kw) -> None:  # noqa: ANN003
        super().__init__(**kw)
        self.listener = None
        self.where = {"101": "général", "201": "raid"}

    def describe(self, user_id: str) -> dict:
        guilds, user = db.describe_guilds(_guilds(), user_id, bot_id=1)
        cur = self.current
        return {"guilds": guilds, "user": user, "latency_ms": 42,
                "channel": {"id": "x", "name": cur, "guild": "Les potes", "guild_id": "10"} if cur else None}

    def get_voice_channel(self, cid: str):  # noqa: ANN201
        return self.where.get(cid)


def test_snapshot_commands_and_auto_follow(bot) -> None:  # noqa: ANN001
    bot._factory = ViewBackend
    assert bot.snapshot()["state"] == "off"
    bot.configure(_cfg(discord_user_id=str(UID)))
    assert _wait(lambda: bot.snapshot()["state"] == "connected" and bot.snapshot()["guilds"])
    fb = FakeBackend.instances[-1]
    assert _wait(lambda: fb.current == "général")                  # auto-join: the player is in voice
    snap = bot.snapshot()
    assert snap["bot_name"] == "TreeAI" and snap["latency_ms"] == 42 and snap["user"]["name"] == "Dexter"
    assert len(snap["guilds"]) == 2 and snap["channel"]["name"] == "général"
    assert any(e["text"] == "Connecté en tant que TreeAI" for e in snap["events"])
    assert any(e["text"] == "A rejoint #général" for e in snap["events"])
    # manual join: the bot stays there; "Quitter": it does not come straight back
    assert bot.join("201")
    assert _wait(lambda: fb.current == "raid")
    bot._poke()
    time.sleep(0.2)
    assert fb.current == "raid"
    assert bot.leave()
    assert _wait(lambda: fb.current is None)
    bot._poke()
    time.sleep(0.2)
    assert fb.current is None
    # tests from the launcher: the bot joins the player's channel and says it
    assert bot.say_test("gank")
    assert _wait(lambda: "Bot gank" in bot.spoken)
    assert bot.say_test("matchup")
    assert _wait(lambda: db.EXAMPLE_MATCHUP in bot.spoken)
    ev = bot.snapshot()["events"]
    assert ev[0]["text"] == f"A dit « {db.EXAMPLE_MATCHUP} »" and len(ev) <= db.EVENTS_MAX
    # the player leaves voice: the bot follows him out
    fb.channel = None
    bot._poke()
    assert _wait(lambda: fb.current is None)


def test_join_failure_shows_the_cause(bot) -> None:  # noqa: ANN001
    exc = type("ConnectionClosed", (Exception,), {})("closed")
    exc.code = 4017

    def factory() -> ViewBackend:
        b = ViewBackend()
        b.join_error = exc
        return b
    bot._factory = factory
    bot.configure(_cfg(discord_user_id=str(UID)))
    assert _wait(lambda: "Impossible de rejoindre" in bot.status()[0])
    assert "DAVE" in bot.status()[0]
    assert any(not e["ok"] and "DAVE" in e["text"] for e in bot.snapshot()["events"])
    msg, ok = bot.test_blocking(3.0)                                # no retry loop for 30 s, clear message
    assert not ok


def test_connection_error_is_in_snapshot(bot) -> None:  # noqa: ANN001
    class Down(FakeBackend):
        async def start(self, token: str):  # noqa: ANN201
            raise OSError("Cannot connect to host discord.com:443 ssl:True")
    bot._factory = Down
    bot.configure(_cfg())
    assert _wait(lambda: bot.snapshot()["state"] == "error")
    snap = bot.snapshot()
    assert "Cannot connect" in snap["error"] and "Cannot connect" in snap["status"]
