"""Bot Discord (discord_bot.py) with a fake Discord layer: no network, no discord.py needed."""

from __future__ import annotations

import asyncio
import io
import logging
import struct
import threading
import time
import wave
from types import SimpleNamespace

import numpy as np
import pytest

from treeaicoach import discord_bot as db
from treeaicoach.config import Config

# fake, shaped like a real one (assembled at runtime so secret scanners do not flag the source)
TOKEN = ".".join(["MTIzNDU2Nzg5MDEyMzQ1Njc4OQ", "GabcDE", "abcdefghijklmnopqrstuvwxyz" + "0123456789AB"])


def _wav(seconds: float = 0.4, rate: int = 24000, amp: float = 0.2) -> bytes:
    """A 440 Hz tone (audible: passes discord_audio.is_audible when >= 0.15 s and amp > 0.005)."""
    import numpy as np

    n = int(seconds * rate)
    x = (amp * 32767 * np.sin(2 * np.pi * 440.0 * np.arange(n) / rate)).astype("<i2")
    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(x.tobytes())
    return bio.getvalue()


class FakeBackend:
    instances: list[FakeBackend] = []

    def __init__(self, *, bad: bool = False, channel: str | None = "général", hang: float = 0.0) -> None:
        self.bad, self.channel, self.hang = bad, channel, hang
        self.played: list[int] = []
        self.pcms: list[bytes] = []
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
        self.pcms.append(pcm)

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
    assert db.gank_text("jungle") == "Jungle gank"
    assert db.gank_text(None) == "" and db.gank_text("river") == ""


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
    # the 4 gank clips are pre-rendered in the background at start; under load a call can come
    # before "Bot gank" is rendered (the bundled recorded clip is used then): wait for them
    assert _wait(lambda: len(bot._clips) == 4)
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
    assert _wait(lambda: len(bot._clips) == 4)
    assert {"Top gank", "Mid gank", "Bot gank", "Jungle gank"} <= set(bot.tts_texts)
    assert _wait(lambda: bot.snapshot()["voice_source"] == "custom" and bot.snapshot()["voice_checked"])


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
    friends = [c for c in e.calls if c[0] == "matchups"]       # the friends' lanes: once too
    assert len(friends) == 1 and friends[0][2]["lines"][0]["champion"] == "Lux"
    assert friends[0][2]["lines"][0]["text"].startswith("Lux contre Ahri : ") and callable(friends[0][2]["ai"])
    e.calls = [c for c in e.calls if c[0] != "matchups"]
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
    # the snapshot's servers / channel come from the bot's cached refresh (its own loop, a moment
    # after the join): wait for it instead of reading it at once (a race under load)
    assert _wait(lambda: (bot.snapshot().get("channel") or {}).get("name") == "général"
                 and bot.snapshot().get("user"))
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
    # newest first; "A quitté le salon vocal" may already follow the test (outside a game the bot
    # leaves after it): under load the snapshot comes after it, so look at the newest "A dit"
    said = [e["text"] for e in ev if e["text"].startswith("A dit")]
    assert said and said[0] == f"A dit « {db.EXAMPLE_MATCHUP} »" and len(ev) <= db.EVENTS_MAX
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


# ------------------------------------------------------------------------------ 2.9.1: voice, volume, options
from treeaicoach import discord_audio as da  # noqa: E402


def _silent_wav(seconds: float = 0.5) -> bytes:
    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * int(seconds * 24000))
    return bio.getvalue()


def test_recorded_clips_are_bundled_and_audible() -> None:
    for text in da.RECORDED:
        wav = da.recorded_wav(text)
        assert wav, text
        pcm = da.speech_pcm(wav)
        st = da.pcm_stats(pcm)
        assert da.is_audible(pcm) and 0.5 < st["seconds"] < 2.0, (text, st)
        assert abs(st["rms_db"] - da.TARGET_RMS_DB) < 1.0, (text, st)       # loudness-normalised
    assert da.recorded_wav("Garen contre Darius") is None
    assert db.gank_text("jungle") in da.RECORDED and db.objective_text("grubs") in da.RECORDED
    assert db.seen_text("mid") in da.RECORDED


def test_empty_or_silent_speech_falls_back_to_the_recorded_clip() -> None:
    for bad in (lambda t, v, r: b"", lambda t, v, r: None, lambda t, v, r: _silent_wav(),
                lambda t, v, r: b"RIFF junk", lambda t, v, r: (_ for _ in ()).throw(RuntimeError("403"))):
        voice = da.BotVoice([("custom", bad)])
        wav, src = voice.synth("Bot gank")
        assert src == "clip" and da.wav_audible(wav) and "custom" in voice.failures
    # the bot: the gank clip is chime + RECORDED words, never the chime alone
    c = db.DiscordCaller(backend_factory=FakeBackend, speech=lambda t, v, r: _silent_wav(),
                         beep=lambda: _wav(0.05, 32000))
    clip = c._gank_clip("bot", "", "")
    beep = da.chime_pcm(_wav(0.05, 32000))
    assert len(clip) > len(beep) + da.FRAME_BYTES * 4 and c._voice_source == "clip"
    # no voice at all (no recorded clip): nothing, not the chime alone
    c2 = db.DiscordCaller(backend_factory=FakeBackend, speech=lambda t, v, r: b"", recorded=False,
                          beep=lambda: _wav(0.05, 32000))
    assert c2._gank_clip("bot", "", "") == b""
    assert c2.prerender() == 0 and c2.snapshot()["voice_checked"] and c2.snapshot()["voice_source"] == ""
    assert any("Aucune voix" in e["text"] and not e["ok"] for e in c2.snapshot()["events"])


def test_parse_wav_extensible_float_and_24bit() -> None:
    import numpy as np

    x = (0.3 * np.sin(np.arange(4800) / 5.0)).astype("<f4")
    fmt = struct.pack("<HHIIHH", 0xFFFE, 1, 24000, 24000 * 4, 4, 32) + struct.pack("<HHI", 22, 32, 4) \
        + struct.pack("<H", 3) + b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", x.nbytes) + x.tobytes()
    wav = b"RIFF" + struct.pack("<I", len(body)) + body
    got = da.parse_wav(wav)
    assert got is not None and got[1] == 24000 and abs(float(got[0].max()) - 0.3) < 0.01
    i24 = (np.round(x * (1 << 23)).astype(np.int32))
    raw = b"".join(int(v).to_bytes(4, "little", signed=True)[:3] for v in i24[:100])
    fmt = struct.pack("<HHIIHH", 1, 1, 24000, 72000, 3, 24)
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(raw)) + raw
    got = da.parse_wav(b"RIFF" + struct.pack("<I", len(body)) + body)
    assert got is not None and np.allclose(got[0][:, 0], x[:100], atol=1e-4)
    assert da.parse_wav(b"") is None and da.parse_wav(b"RIFF\x00\x00\x00\x00WAVEjunk" * 4) is None


def test_volume_scaling_and_limiter() -> None:
    speech = da.speech_pcm(da.recorded_wav("Bot gank"))
    base = da.pcm_stats(speech)["rms_db"]
    at = {v: da.pcm_stats(da.apply_volume(speech, v)) for v in (0, 10, 40, 70, 100)}
    assert at[0]["peak_db"] == float("-inf")                                   # 0 = silent
    assert abs(at[40]["rms_db"] - (base + 20 * np.log10(0.35))) < 0.5           # default = 35 %
    assert at[10]["rms_db"] < at[40]["rms_db"] < at[70]["rms_db"] < at[100]["rms_db"]
    loud = da.apply_volume(b"".join([(30000).to_bytes(2, "little", signed=True)] * 9600), 100)
    assert da.pcm_stats(loud)["peak_db"] < -0.05                              # soft limiter: never clips
    assert da.apply_volume(b"", 40) == b""
    # the chime sits 6 dB under the words (2.9.0: 7 dB OVER them)
    chime = da.chime_pcm(db._danger_chime())
    assert abs(da.pcm_stats(chime)["rms_db"] - (da.TARGET_RMS_DB - da.CHIME_UNDER_DB)) < 1.0
    assert Config().discord_volume == da.DEFAULT_VOLUME == 40


def test_bot_plays_at_the_configured_volume(bot) -> None:  # noqa: ANN001
    bot.configure(_cfg(discord_volume=40))
    assert _wait(lambda: bot.status()[1])
    bot.on_call("game_start")
    assert bot.on_call("gank", "x", {"lane": "top"})
    fb = FakeBackend.instances[-1]
    assert _wait(lambda: fb.pcms)
    quiet = da.pcm_stats(fb.pcms[-1])
    bot.configure(_cfg(discord_volume=100))
    bot._filter.reset()
    assert bot.on_call("gank", "x", {"lane": "mid"})
    assert _wait(lambda: len(fb.pcms) == 2)
    loud = da.pcm_stats(fb.pcms[-1])
    assert loud["rms_db"] - quiet["rms_db"] > 10.0 and loud["peak_db"] < 0.0


def test_new_switches_default_off_and_gate_the_calls(bot) -> None:  # noqa: ANN001
    cfg = Config()
    assert not (cfg.discord_only or cfg.discord_objectives or cfg.discord_jungler_seen or cfg.discord_summary)
    assert cfg.hotkey_discord_mute == ""
    bot.configure(_cfg())
    assert not bot.on_call("objective", "", {"key": "dragon", "spawn": 300})
    assert not bot.on_call("jungler_seen", "", {"lane": "top"})
    assert not bot.on_call("summary", "Victoire en 30 minutes.")
    bot.configure(_cfg(discord_objectives=True, discord_jungler_seen=True, discord_summary=True))
    assert bot.on_call("objective", "", {"key": "dragon", "spawn": 300})
    assert not bot.on_call("objective", "", {"key": "dragon", "spawn": 300.2})   # once per spawn
    assert not bot.on_call("objective", "", {"key": "herald", "spawn": 900})     # not a bot call
    assert bot.on_call("summary", "Victoire en 30 minutes.")
    assert not bot.on_call("summary", "Victoire en 30 minutes.")                  # once per game


def test_jungler_seen_is_lowest_priority() -> None:
    now = [1000.0]
    f = db.CallFilter(lambda: now[0])
    assert f.admit("gank", "bot")
    now[0] += 10
    assert not f.admit("jungler_seen", "top")             # 10 s after a gank call: no
    now[0] += 15
    assert f.admit("jungler_seen", "top")
    now[0] += 5
    assert not f.admit("jungler_seen", "top")             # same lane within 45 s
    assert f.admit("jungler_seen", "mid")
    now[0] += 1
    assert not f.admit("jungler_seen", "bot")             # 2 a minute at most
    c = db.DiscordCaller(backend_factory=FakeBackend, speech=lambda t, v, r: _wav())
    c.configure(_cfg(discord_jungler_seen=True, discord_objectives=True))
    with c._lock:                                          # a call is waiting: "seen" is not queued behind it
        c._jobs.append(db._Job("say", "Dragon dans 30 secondes", time.monotonic(), 6.0, "objective"))
    assert not c.on_call("jungler_seen", "", {"lane": "top"})
    with c._lock:                                          # the gank goes before a waiting objective
        c._jobs.append(db._Job("say", "Bot gank", time.monotonic(), 5.0, "gank", lane="bot"))
    assert c._pop().call_kind == "gank"
    c.shutdown(1.0)


def test_push_to_mute_drops_calls(bot) -> None:  # noqa: ANN001
    bot.configure(_cfg())
    assert bot.set_muted(None) is True and bot.snapshot()["muted"]
    assert not bot.on_call("gank", "x", {"lane": "top"})
    assert bot.set_muted(None) is False
    assert bot.on_call("gank", "x", {"lane": "top"})


def test_texts_summary_and_lanes() -> None:
    assert db.objective_text("dragon") == "Dragon dans 30 secondes"
    assert db.objective_text("grubs") == "Larves dans 30 secondes" and db.objective_text("elder") == ""
    assert db.seen_text("bot") == "Jungler ennemi vu bot" and db.seen_text("jungle") == ""
    a = {"summary": {"result": "Win", "duration": 1920.0}, "ganks_faced": 10, "ganks_survived": 9}
    assert db.summary_line(a) == "Victoire en 32 minutes, 9 ganks évités sur 10."
    assert db.summary_line({"summary": {"result": "Lose", "duration": 1500}, "ganks_faced": 1,
                            "ganks_survived": 1}) == "Défaite en 25 minutes, 1 gank évité sur 1."
    assert db.summary_line({"summary": {"result": "Win", "duration": 600}}) == "Victoire en 10 minutes."
    assert db.summary_line({}) == "" and db.summary_line(None) == ""
    assert db.nearest_lane(0.07, 0.4) == "top" and db.nearest_lane(0.5, 0.5) == "mid"
    assert db.nearest_lane(0.6, 0.93) == "bot" and db.nearest_lane(2, 0) is None


def _speaker(cfg, takes: bool):  # noqa: ANN001, ANN202
    import collections as _c

    e = _Eng(cfg)
    e._gate = e._tactics = e._recorder = None
    e._muted = False
    e._lock = threading.Lock()
    e._recent = _c.deque(maxlen=20)
    e.said, e.beeps = [], []
    e._say = lambda text, level: e.said.append(text)
    e._danger_beep = lambda a: e.beeps.append(a.text)
    e.on_call = lambda k, t, i: (e.calls.append((k, t, i)), True)[1]
    e.speaks_for = lambda kind: takes
    return e


def test_discord_only_mutes_the_pc_voice_only_for_bot_calls() -> None:
    from treeaicoach.alerts import Alert, AlertKind, Level

    gank = Alert(kind=AlertKind.JUNGLER_APPROACH, level=Level.DANGER, text="Lee Sin arrive, recule !",
                 key="g", t=1.0)
    tip = Alert(kind=AlertKind.MACRO_TIP, level=Level.INFO, text="Pousse ta vague.", key="m", t=1.0)
    obj = Alert(kind=AlertKind.OBJECTIVE_SOON, level=Level.INFO, text="Dragon dans 60 secondes.", key="o", t=1.0)
    on = Config(discord_enabled=True, discord_only=True)
    e = _speaker(on, True)
    e._speak_alerts([gank, tip, obj], 1.0, 100.0)
    assert e.said == ["Pousse ta vague."]                       # gank + objective: said by the bot only
    assert e.beeps == ["Lee Sin arrive, recule !"]              # the danger beep stays on the PC
    assert [c[0] for c in e.calls] == ["gank"]
    e = _speaker(on, False)                                     # bot not in voice: the PC says everything
    e._speak_alerts([gank, tip], 1.0, 100.0)
    assert e.said == [gank.text, tip.text]
    e = _speaker(Config(discord_enabled=True), True)            # discord_only off (default)
    e._speak_alerts([gank], 1.0, 100.0)
    assert e.said == [gank.text]
    e = _speaker(on, True)                                      # the bot refused the call (rate limit): PC says it
    e.on_call = lambda k, t, i: False
    e._speak_alerts([gank], 1.0, 100.0)
    assert e.said == [gank.text]


def test_engine_objective_calls_once_per_spawn() -> None:
    e = _Eng(Config(discord_enabled=True, discord_objectives=True))
    states = [SimpleNamespace(key="dragon", alive=False, remaining=40.0, next_spawn=300.0),
              SimpleNamespace(key="herald", alive=False, remaining=30.0, next_spawn=900.0)]
    e._objectives = SimpleNamespace(states=lambda: states)
    e._discord_objectives()
    assert e.calls == []
    states[0].remaining = 30.0
    e._discord_objectives()
    e._discord_objectives()
    assert e.calls == [("objective", "", {"key": "dragon", "spawn": 300.0})]
    e2 = _Eng(Config(discord_enabled=True))                     # switch off: nothing
    e2._objectives = SimpleNamespace(states=lambda: states)
    e2._discord_objectives()
    assert e2.calls == []


def test_speaks_for_needs_voice_channel_and_switch(bot) -> None:  # noqa: ANN001
    class Described(FakeBackend):
        def describe(self, user_id: str) -> dict:
            return {"guilds": [], "user": {}, "channel": {"name": self.current} if self.current else None}

    bot._factory = lambda: Described(channel=None)
    bot.configure(_cfg(discord_only=True))
    assert _wait(lambda: bot.status()[0].startswith("Connecté"))
    assert not bot.speaks_for("gank")                          # not in a voice channel
    FakeBackend.instances[-1].channel = "général"              # the player joins voice: the bot follows
    bot._poke()
    assert _wait(lambda: bot.speaks_for("gank"), 6.0)
    assert not bot.speaks_for("objective")                     # that switch is off
    bot.set_muted(True)
    assert not bot.speaks_for("gank")                          # muted: the PC speaks again
    bot.set_muted(False)
    bot.configure(_cfg(discord_only=False))
    assert not bot.speaks_for("gank")


# ------------------------------------------------------------------------------ route, end of game, friends, listen
def test_route_of_and_switches() -> None:
    assert db.route_of(Config()) == "both"
    assert db.route_of(Config(discord_only=True)) == "discord"               # legacy switch
    assert db.route_of(Config(discord_route="pc", discord_only=True)) == "pc"
    assert db.route_of(SimpleNamespace(discord_route="nope")) == "both"
    s = db._Settings(route="pc")
    assert not s.wants("gank") and not s.wants("matchup") and s.wants("test")
    s = db._Settings(route="both")
    assert not s.wants("coach") and not s.wants("beep") and s.wants("gank")
    s = db._Settings(route="discord", listen=True)
    assert s.wants("coach") and s.wants("beep") and s.wants("chime") and s.wants("ai_answer")


def test_route_pc_bot_silent_route_discord_says_everything(bot) -> None:  # noqa: ANN001
    class Described(FakeBackend):
        def describe(self, user_id: str) -> dict:
            return {"guilds": [], "user": {}, "channel": {"name": self.current} if self.current else None}

    bot._factory = Described
    bot.configure(_cfg(discord_route="pc"))
    assert _wait(lambda: bot.status()[0].startswith("Connecté"))
    assert bot.on_call("game_start")
    assert not bot.on_call("gank", "x", {"lane": "top"})                  # « Tout sur mon PC »
    assert not bot.on_call("coach", "Pousse ta vague.")
    bot.configure(_cfg(discord_route="discord"))
    assert _wait(lambda: bot.speaks_for("coach"), 6.0) and bot.speaks_for("beep")
    fb = FakeBackend.instances[-1]
    n = len(fb.played)
    assert bot.on_call("beep", "", {"tone": "recule"})
    assert bot.on_call("coach", "Pousse ta vague.")
    assert _wait(lambda: "Pousse ta vague." in bot.spoken)
    assert len(fb.played) >= n + 2                                        # the tone, then the sentence
    bot.configure(_cfg(discord_route="both"))
    assert not bot.speaks_for("coach") and not bot.on_call("coach", "Autre phrase.")


def test_game_end_without_user_id_stays_and_says_the_summary(bot) -> None:  # noqa: ANN001
    bot.configure(_cfg(discord_user_id="", discord_summary=True))
    assert _wait(lambda: bot.status()[1])
    fb = FakeBackend.instances[-1]
    assert bot.on_call("game_start")
    assert _wait(lambda: fb.joined == ["général"])
    assert bot.on_call("game_end") and bot.on_call("game_end")            # twice: one leave at most
    assert bot.on_call("summary", "Victoire en 30 minutes.")
    assert _wait(lambda: "Victoire en 30 minutes." in bot.spoken)
    assert fb.left == 0 and fb.joined == ["général"]                      # no leave / rejoin flapping
    with bot._lock:
        assert sum(j.kind == "leave" for j in bot._jobs) <= 1


def test_game_end_keeps_the_players_commands(bot) -> None:  # noqa: ANN001
    with bot._lock:
        bot._settings = db._Settings(enabled=True, token=TOKEN)
        bot._jobs.append(db._Job("leave_now"))
        bot._jobs.append(db._Job("say", "x", 0.0, 5.0, "coach"))
    bot._ensure_thread = lambda: None  # type: ignore[method-assign]
    assert bot.on_call("game_end")
    with bot._lock:
        assert [j.kind for j in bot._jobs] == ["leave_now", "leave"]


def test_refresh_errors_never_end_the_session(bot) -> None:  # noqa: ANN001
    class Broken(FakeBackend):
        def describe(self, user_id: str) -> dict:
            raise RuntimeError("cache moved")

        def find_channel(self, user_id: str):  # noqa: ANN201
            if getattr(self, "boom", False):
                raise RuntimeError("guild gone")
            return self.channel

    bot._factory = Broken
    bot.configure(_cfg())
    assert _wait(lambda: bot.status()[1])
    fb = FakeBackend.instances[-1]
    assert bot.on_call("game_start")
    assert _wait(lambda: fb.joined == ["général"])
    fb.boom = True
    assert bot.on_call("game_end")
    time.sleep(0.3)
    assert bot._state == "connected" and fb.closed == 0 and bot._thread.is_alive()


def test_friends_matchups_one_ai_request_and_names(bot) -> None:  # noqa: ANN001
    bot.configure(_cfg())
    assert _wait(lambda: bot.status()[1])
    me = {"riot": "Moi#EUW", "champion": "Garen", "alias": "Garen", "position": "TOP", "me": True}
    paul = {"riot": "Paul#EUW", "champion": "Lux", "alias": "Lux", "position": "MIDDLE", "me": False}
    bot._mapping_locked = lambda: [{"id": "1", "name": "Paul", "spoken": "Paul", "player": paul, "source": "auto"},
                                   {"id": "2", "name": "Moi", "spoken": "Moi", "player": me, "source": "toi"}]
    asked = []

    def ai(lines):  # noqa: ANN001, ANN202
        asked.append([ln["champion"] for ln in lines])
        return "Lux : favorable, pousse et poke Ahri jusqu'au niveau 6."
    lines = [{"alias": "Lux", "champion": "Lux", "opp": "Ahri", "role": "middle", "text": "Lux contre Ahri : équilibré."},
             {"alias": "Jinx", "champion": "Jinx", "opp": "Kaisa", "role": "bottom", "text": "Jinx contre Kaisa : x."}]
    assert bot.on_call("game_start")
    assert bot.on_call("matchups", "", {"lines": lines, "ai": ai})
    assert not bot.on_call("matchups", "", {"lines": lines, "ai": ai})     # once per game
    assert _wait(lambda: any(t.startswith("Paul, ") for t in bot.spoken))
    assert asked == [["Lux"]]                                              # one request, friends only
    assert "Paul, favorable, pousse et poke Ahri jusqu'au niveau 6." in bot.spoken
    assert db.parse_friends_answer("- Lux : bien.\nZed: phrase en trop ici.", ["Lux"]) == {}   # too short
    assert db.parse_friends_answer("Lux : tu gagnes ce duel jusqu'au 6.", ["Lux"]) == {
        "Lux": "tu gagnes ce duel jusqu'au 6."}


def test_listen_chime_question_answer_in_the_channel(bot) -> None:  # noqa: ANN001
    bot.configure(_cfg(discord_listen=True))
    assert _wait(lambda: bot.status()[1])
    assert bot.on_call("game_start")
    fb = FakeBackend.instances[-1]
    assert _wait(lambda: fb.joined == ["général"])
    assert bot._should_listen()
    paul = {"riot": "Paul#EUW", "champion": "Lux", "alias": "Lux", "position": "MIDDLE", "me": False}
    bot._mapping_locked = lambda: [{"id": "1", "name": "Paul", "spoken": "Paul", "player": paul, "source": "auto"}]
    lst, _rx = bot._listen_parts()
    asked = []
    bot.on_voice_question = lambda who, q: asked.append((who, q))
    n = len(fb.played)
    lst.on_wake("1")
    assert _wait(lambda: len(fb.played) > n)                              # the chime, in the channel
    lst._busy_until = time.monotonic() + 99
    lst.on_question("1", "quel item")
    assert asked == [("Paul (Lux)", "quel item")]
    assert bot.on_call("ai_answer", "Achète une Ceinture de Titan.")
    assert _wait(lambda: "Achète une Ceinture de Titan." in bot.spoken)
    assert _wait(lambda: lst._busy_until < 0)                             # answered: next question allowed
    bot.set_muted(True)
    assert not bot._should_listen()


def test_engine_routes_coach_voice_and_discord_questions() -> None:
    from treeaicoach.engine import CoachEngine as Engine

    e = _Eng(Config(discord_enabled=True, discord_route="discord"))
    e._muted = False
    e._voice = SimpleNamespace(said=[], say=lambda t, lv: e._voice.said.append(t))
    e.speaks_for = lambda kind: True
    e.on_call = lambda k, t, i: (e.calls.append((k, t, i)), True)[1]
    Engine._say(e, "Pousse ta vague.", 1)
    assert e.calls[-1][:2] == ("coach", "Pousse ta vague.") and e._voice.said == []
    e.speaks_for = lambda kind: False                                     # bot not in voice: the PC
    Engine._say(e, "Recule.", 1)
    assert e._voice.said == ["Recule."]
    e.ask_ai = lambda question=None, spoken=False: "Pas de partie en cours : l'IA a besoin d'une partie pour conseiller."
    e._discord_voice_question("Paul (Lux)", "quel item")
    assert e.calls[-1][0] == "ai_answer" and e.calls[-1][1].startswith("Pas de partie")
    seen = []
    e.ask_ai = lambda question=None, spoken=False: (seen.append(question), "Question envoyée à l'IA…")[1]
    e._discord_voice_question("Paul (Lux)", "quel item")
    assert seen == ["Question posée par Paul (Lux) : quel item"] and e._ai_discord_pending


def test_game_start_ranks_post_once_and_speak_highlights(bot) -> None:  # noqa: ANN001
    posted: list[str] = []

    async def post(ch, text: str) -> None:  # noqa: ANN001
        posted.append(text)
    bot.configure(_cfg())
    assert _wait(lambda: bot.status()[1])
    fb = FakeBackend.instances[-1]
    fb.post = post
    assert bot.on_call("game_start")
    assert _wait(lambda: fb.joined == ["général"])
    msg = "Rangs de la partie\nEnnemis : Lee Sin Platine 2, 4 défaites d'affilée"
    assert bot.on_call("ranks", msg, {"lines": ["Leur jungler Lee Sin : Platine 2, 4 défaites d'affilée"]})
    assert not bot.on_call("ranks", msg, {"lines": ["encore"]})            # once per game
    assert _wait(lambda: posted == [msg])
    assert _wait(lambda: "Leur jungler Lee Sin : Platine 2, 4 défaites d'affilée" in bot.spoken)
    bot.configure(_cfg(discord_ranks=False))                               # the switch is honoured
    bot.on_call("game_end")
    bot.on_call("game_start")
    assert not bot.on_call("ranks", msg, {"lines": []})
