"""« Hey coach » made observable and less strict (2.16 field report: « ça détecte pas ma voix ni celle de
mes amis »): wake rules, mic fallbacks, the « Écoute » panels (PC + per Discord member), tests outside a
game, SSRC guessing, who plays / who does not (both teams). Fakes only."""
from __future__ import annotations

import sys
import threading
import time
import types
from types import SimpleNamespace as NS

import numpy as np

from treeaicoach import discord_friends as df
from treeaicoach import discord_listen as dl
from treeaicoach import voice_assistant as va

LOUD = np.full(va.BLOCK_FRAMES, 3000, dtype="<i2").tobytes()
QUIET = bytes(va.BLOCK_FRAMES * 2)


# ------------------------------------------------------------------------------ wake rules
def test_wake_kind_rules() -> None:
    assert va.wake_kind("hé coach quel item") == "strong"
    assert va.wake_kind("bon [unk] hey coach") == "strong"
    assert va.wake_kind("coach il est où") == "bare"                       # « Coach, … » at the start
    assert va.wake_kind("[unk] coach") == ""                               # « Mais coach » / « ce coach »
    assert va.wake_kind("le coach") == "" and va.wake_kind("mon coach de") == ""
    assert va.wake_kind("hé coq le [unk]", [0.9, 0.9, 0.9, 1.0], final=True) == ""   # « Hé, quelqu'un… »
    assert va.wake_kind("hé coche", final=False) == ""                     # weak: never from a partial
    assert va.wake_kind("hé coche", [0.9, 0.8], final=True) == "weak"      # said alone
    assert va.wake_kind("hé coche", [0.4, 0.5], final=True) == ""          # unsure
    assert va.wake_kind("et coche et là [unk]", [0.4, 0.9, 0.9, 1, 1], final=True) == ""  # « il faut cocher »
    assert va.wake_kind("coach", [0.5], final=True) == ""                  # bare needs a sure word
    assert va.wake_kind("coach", [0.9], final=True) == "bare"
    assert va.wake_kind(None) == ""


class _Rec:
    """Scripted recognizer: one (kind, text, confs) per block."""

    def __init__(self, items: list[tuple[str, str, list[float] | None]]) -> None:
        self.items, self.i = list(items), -1
        self.last_raw, self.last_confs = "", None

    def accept(self, _b: bytes) -> bool:
        self.i += 1
        k, t, c = self.items[self.i]
        self.last_raw, self.last_confs = t, c
        return k == "F"

    def result(self) -> str:
        return self.last_raw.replace("[unk]", "").strip()

    def partial(self) -> str:
        return self.result()

    def reset(self) -> None:
        pass


def _run(det: va.WakeDetector, rec: _Rec, blocks: list[bytes]) -> list[bool]:
    return [det.feed(rec, b) for b in blocks]


def test_bare_coach_needs_a_silence_before() -> None:
    # quiet, then « Coach, il est où » : fires on the 2nd stable partial
    rec = _Rec([("P", "", None)] * 3 + [("P", "coach", None), ("P", "coach il", None)])
    assert _run(va.WakeDetector(), rec, [QUIET] * 3 + [LOUD, LOUD]) == [False] * 4 + [True]
    # talking all along (« ce coach est pratique » split by the decoder): never
    rec = _Rec([("P", "ce", None), ("F", "ce coach [unk]", [1, 1, 1]), ("P", "coach", None), ("P", "coach", None)])
    assert not any(_run(va.WakeDetector(), rec, [LOUD] * 4))
    # « coach » "heard" in the silence itself: never
    rec = _Rec([("P", "", None)] * 3 + [("P", "coach", None)] * 3)
    assert not any(_run(va.WakeDetector(), rec, [QUIET] * 6))


def test_strong_wake_partials_and_finals() -> None:
    det = va.WakeDetector()
    rec = _Rec([("P", "eh coach", None), ("P", "et toi", None)])           # one flipping partial: no
    assert not any(_run(det, rec, [LOUD, LOUD]))
    rec = _Rec([("P", "hé coach", None), ("P", "hé coach quel", None)])
    assert _run(va.WakeDetector(), rec, [LOUD, LOUD]) == [False, True]
    rec = _Rec([("F", "hey coach", [0.9, 0.9])])
    assert _run(va.WakeDetector(), rec, [LOUD]) == [True]
    det = va.WakeDetector()
    _run(det, _Rec([("P", "[unk] hé coach", None)]), [LOUD])
    assert det.last_text == "… hé coach" and det.rms > 2000


# ------------------------------------------------------------------------------ audio
def test_resample_48k_stereo_and_44k() -> None:
    t = np.arange(48000) / 48000.0
    s = (8000 * np.sin(2 * np.pi * 440 * t)).astype("<i2")
    stereo = np.stack([s, s], axis=1).reshape(-1).tobytes()
    out = np.frombuffer(va.resample_to_16k(stereo, 48000, 2), dtype="<i2")
    assert len(out) == 16000
    f = np.abs(np.fft.rfft(out.astype(float)))
    assert abs(int(np.argmax(f)) - 440) <= 2                               # the pitch survives
    a44 = np.zeros(44100, dtype="<i2").tobytes()
    assert len(va.resample_to_16k(a44, 44100, 1)) == 16000 * 2
    assert va.resample_to_16k(b"", 48000, 2) == b""
    assert 0.0 == va.level_of(0) and va.level_of(16000) > 0.8 and va.level_bar(0.5) == "▮▮▮▮▯▯▯▯"


def test_mic_falls_back_to_the_device_rate(monkeypatch) -> None:  # noqa: ANN001
    opened = []

    class Stream:
        def __init__(self, samplerate, blocksize, device, channels, dtype):  # noqa: ANN001
            if samplerate == 16000:
                raise RuntimeError("Invalid sample rate")
            opened.append((samplerate, channels, blocksize))

        def start(self) -> None:
            pass

        def read(self, n):  # noqa: ANN001, ANN201
            return np.full(n * opened[-1][1], 500, dtype="<i2").tobytes(), False

        def stop(self) -> None:
            pass

        def close(self) -> None:
            pass

    fake = types.ModuleType("sounddevice")
    fake.RawInputStream = Stream  # type: ignore[attr-defined]
    fake.query_devices = lambda dev, kind=None: {"default_samplerate": 48000.0, "max_input_channels": 2}  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    mic = va.SoundDeviceMic(None)
    assert opened == [(48000, 1, 12000)] and mic.rate == 48000
    assert len(mic.read() or b"") == va.BLOCK_FRAMES * 2                    # 0.25 s at 16 kHz mono
    mic.close()


def test_missing_vosk_deps_are_stubbed(monkeypatch) -> None:  # noqa: ANN001
    import importlib.util

    for n in ("srt", "tqdm"):
        monkeypatch.delitem(sys.modules, n, raising=False)
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda n, *a: None if n in ("srt", "tqdm") else real(n, *a))
    va._stub_vosk_deps()
    from tqdm import tqdm  # noqa: F401  (vosk does this at import time)

    assert "srt" in sys.modules
    assert "<chemin>" in va.mask_error(OSError("cannot load 'C:\\Users\\Bob\\x\\libvosk.dll'"))


# ------------------------------------------------------------------------------ the PC assistant
class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class _Mic:
    def __init__(self, clock: _Clock, script: list[bytes]) -> None:
        self.clock, self.script, self.closed = clock, list(script), False

    def read(self) -> bytes:
        self.clock.t += va.BLOCK_S
        time.sleep(0.001)
        return self.script.pop(0) if self.script else QUIET

    def close(self) -> None:
        self.closed = True


class _TextRec:
    """b"F:..." final / b"P:..." partial blocks (padded to a block), else nothing."""

    def __init__(self, _g) -> None:  # noqa: ANN001
        self._f = self._p = ""

    def accept(self, pcm: bytes) -> bool:
        txt = pcm.rstrip(b"\0").decode(errors="ignore")
        if txt.startswith("F:"):
            self._f, self._p = txt[2:], ""
            return True
        if txt.startswith("P:"):
            self._p = txt[2:]
        return False

    def result(self) -> str:
        r, self._f = self._f, ""
        return r

    def partial(self) -> str:
        return self._p

    def final(self) -> str:
        r, self._p = self._p, ""
        return r

    def reset(self) -> None:
        self._f = self._p = ""


def _blk(t: str) -> bytes:
    return t.encode().ljust(va.BLOCK_FRAMES * 2, b"\0")


def _wait(cond, timeout: float = 3.0) -> bool:  # noqa: ANN001
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_panel_shows_stages_and_a_silent_mic() -> None:
    clock = _Clock()
    a = va.VoiceAssistant(mic_factory=lambda _n: _Mic(clock, [_blk("P:bonjour")] + [QUIET] * 60),
                          recognizer_factory=_TextRec, clock=clock)
    assert a.snapshot()["stage"] == "off"
    a.set_hooks(should_listen=lambda: True)
    a.configure(NS(voice_assistant=True))
    try:
        assert _wait(lambda: a.snapshot()["heard"] == "bonjour")
        assert _wait(lambda: a.snapshot()["silent_mic"])                    # 10 s of zeros: reported
        snap = a.snapshot()
        assert snap["stage"] == "listening" and snap["stage_label"] == "écoute" and "aucun son" in snap["status"]
    finally:
        a.stop()


def test_stuck_voice_gate_does_not_deafen_forever() -> None:
    clock = _Clock()
    asked: list[str] = []
    script = [QUIET] * int(va.SPEAKING_MAX_S / va.BLOCK_S + 4) + [_blk("F:hé coach"), _blk("F:on fait quoi")]
    a = va.VoiceAssistant(mic_factory=lambda _n: _Mic(clock, script), recognizer_factory=_TextRec, clock=clock)
    a.set_hooks(should_listen=lambda: True, is_speaking=lambda: True, on_question=asked.append)
    a.configure(NS(voice_assistant=True))
    try:
        assert _wait(lambda: asked == ["on fait quoi"])
        assert a.dropped_self > 0
    finally:
        a.stop()


# ------------------------------------------------------------------------------ Discord
WAKE, WORDS = 1111, 2222


class _DRec:
    def __init__(self, grammar) -> None:  # noqa: ANN001
        self.wake, self._r = grammar is not None, ""

    def accept(self, pcm: bytes) -> bool:
        v = int(np.frombuffer(pcm, dtype="<i2")[0])
        if self.wake:
            self._r = "hé coach" if v == WAKE else ("bonjour" if v == 500 else "")
            return v in (WAKE, 500)
        if v == WORDS:
            self._r = "tu m'entends"
            return True
        if v == WAKE:                      # the free recognizer confirms the wake
            self._r = "hé coach"
            return True
        return False

    def result(self) -> str:
        r, self._r = self._r, ""
        return r

    def partial(self) -> str:
        return ""

    def final(self) -> str:
        return ""

    def reset(self) -> None:
        pass


def _b(v: int) -> bytes:
    return np.full(va.BLOCK_FRAMES, v, dtype="<i2").tobytes()


def test_discord_test_mode_outside_a_game_and_member_views() -> None:
    clock = [10.0]
    lst = dl.VoiceListener(make_rec=_DRec, clock=lambda: clock[0], threaded=False)
    lst.should_listen = lambda: False                                       # not in game
    got = NS(wakes=[], questions=[], tests=[])
    lst.on_wake, lst.on_question = got.wakes.append, lambda u, q: got.questions.append(q)
    lst.on_test = lambda u, t: got.tests.append((u, t))
    lst.step(("11", _b(500)))
    assert lst.member_views() == {}                                         # off: nothing heard
    lst.test(30)
    lst.step(("11", _b(500)))
    v = lst.member_views()["11"]
    assert v["heard"] == "bonjour" and v["level"] > 0.3 and "▮" in v["bar"] and v["stage"] == "écoute"
    lst.step(("11", _b(WAKE)))
    assert got.wakes == ["11"] and lst.member_views()["11"]["stage"] == "question"
    lst.step(("11", _b(WORDS)))
    assert got.tests == [("11", "tu m'entends")] and got.questions == []    # the test never asks the AI
    lst.step(("22", _b(WAKE)))                                              # no rate limit in the test
    assert got.wakes == ["11", "22"]
    clock[0] += 40
    lst.step(("11", _b(500)))
    assert lst.member_views() == {} and not lst.testing()


def _packet(opus: bytes, ssrc: int) -> bytes:
    import struct

    import nacl.secret

    key = bytes(range(32))
    header = struct.pack(">BBHII", 0x80, 0x78, 5, 1234, ssrc)
    n = struct.pack(">I", 9)
    return header + nacl.secret.Aead(key).encrypt(opus, header, n + b"\x00" * 20).ciphertext + n


def _conn(dave=None) -> NS:  # noqa: ANN001
    return NS(secret_key=list(range(32)), mode="aead_xchacha20_poly1305_rtpsize", ssrc=1, dave_session=dave,
              dave_protocol_version=1 if dave else 0, user=NS(id=999),
              add_socket_listener=lambda cb: None, remove_socket_listener=lambda cb: None)


def test_receiver_guesses_a_missed_ssrc_and_counts_every_stage(monkeypatch) -> None:  # noqa: ANN001
    import discord.opus

    class Dec:
        def decode(self, data, *, fec):  # noqa: ANN001, ANN201
            return np.full(1920, 900, dtype="<i2").tobytes()

    monkeypatch.setattr(discord.opus, "Decoder", Dec)
    got: list = []
    lst = NS(active=lambda: True, push=lambda u, p: got.append(u), bot_user_id="999")

    class Dave:                                                             # only Paul's key opens Paul's frames
        ready = True

        def get_user_ids(self) -> list[str]:
            return ["42", "43", "999"]

        def decrypt(self, uid, _m, frame):  # noqa: ANN001, ANN201
            if uid != 43:
                raise ValueError("wrong key")
            return frame[:-2]

    rx = dl.Receiver(lst)  # type: ignore[arg-type]
    rx.attach(NS(_connection=_conn(Dave())))
    rx._on_packet(_packet(b"abc" * 5 + dl.DAVE_MAGIC, ssrc=77))            # no SPEAKING seen for 77
    assert got == [43] and rx.stats()["guessed"] == 1 and rx.stats()["mapped"] == 1
    # without DAVE: the one channel member not mapped yet
    got.clear()
    rx2 = dl.Receiver(lst)  # type: ignore[arg-type]
    rx2.candidates = lambda: ["999", "42", "55"]
    rx2.on_ws({"op": 5, "d": {"user_id": "42", "ssrc": 70}})
    rx2.attach(NS(_connection=_conn()))
    rx2._on_packet(_packet(b"abc" * 5, ssrc=80))
    assert got == [55]
    rx2.candidates = lambda: ["42", "55", "66", "77"]                       # two unknown: no guess
    rx2._on_packet(_packet(b"abc" * 5, ssrc=81))
    rx2._on_packet(b"\x80" + bytes(40))
    st = rx2.stats()
    assert st["unmapped"] == 1 and st["bad_rtp"] == 1 and st["decoded"] == 1 and got == [55]


# ------------------------------------------------------------------------------ who plays
ME, PAUL, LEO, BOB = "123456789012345678", "223456789012345678", "323456789012345678", "423456789012345678"


def _players() -> list[dict]:
    return [{"riot": "Dexter#EUW", "champion": "Garen", "alias": "Garen", "position": "TOP", "me": True},
            {"riot": "Paul59#EUW", "champion": "Jinx", "alias": "Jinx", "position": "BOTTOM"},
            {"riot": "LeoK#EUW", "champion": "Darius", "alias": "Darius", "position": "TOP", "enemy": True},
            {"riot": "Zed Main#EUW", "champion": "Zed", "alias": "Zed", "position": "MIDDLE", "enemy": True}]


def _members() -> list[dict]:
    return [{"id": ME, "display": "Dex"}, {"id": PAUL, "display": "Paul"}, {"id": LEO, "display": "Leo_K"},
            {"id": BOB, "display": "Bobby"}]


def test_both_teams_are_matched_and_only_teammates_get_calls() -> None:
    m = {e["id"]: e for e in df.match(_members(), _players(), {}, ME)}
    assert df.describe(m[PAUL], True) == "joue Jinx (ADC)" and df.is_teammate(m[PAUL])
    assert df.describe(m[LEO], True) == "joue en face : Darius (top)"
    assert df.plays(m[LEO]) and not df.is_teammate(m[LEO])
    assert df.describe(m[BOB], True) == "ne joue pas" and not df.plays(m[BOB])

    from treeaicoach.ui_page_alerts import listen_line
    from treeaicoach.ui_page_discord import friend_rows

    snap = {"voice_map": list(m.values()), "roster": _players(),
            "listen": {"members": {PAUL: {"bar": "▮▮▮▯▯▯▯▯", "heard": "hé coach", "stage": "écoute"}}}}
    rows = {r["id"]: r for r in friend_rows(snap, {})}
    assert rows[LEO]["title"] == "Leo_K : joue en face : Darius (top)" and "en face" in rows[LEO]["desc"]
    assert rows[LEO]["tone"] == "" and rows[PAUL]["tone"] == "accent"
    assert "Hey coach" in rows[BOB]["desc"]
    assert rows[PAUL]["listen"] == "Écoute : niveau ▮▮▮▯▯▯▯▯ · dernier mot entendu « hé coach »"
    assert rows[BOB]["listen"] == ""
    assert "Darius (top), en face" in [lbl for _v, lbl in rows[BOB]["choices"]]
    assert all("—" not in r["title"] + r["desc"] + r["listen"] for r in rows.values())
    line = listen_line({"stage": "listening", "stage_label": "écoute", "listening": True, "bar": "▮▯",
                        "heard": "hé coach"})
    assert line == "écoute · niveau ▮▯ · dernier mot entendu « hé coach »"
    assert "Tester" in listen_line(None)
    err = listen_line({"stage": "error", "stage_label": "erreur", "status": "Moteur vocal non téléchargé",
                       "engine_error": "ModuleNotFoundError: x"})
    assert "Moteur vocal" in err and "ModuleNotFoundError" in err


def test_bot_keeps_enemy_friends_out_of_gank_calls() -> None:
    from treeaicoach import discord_bot as db

    bot = db.DiscordCaller.__new__(db.DiscordCaller)
    bot._lock = threading.RLock()
    bot._settings = NS(enabled=True, token="t", ally_ganks=True, links={}, user_id=ME, say_name=True)
    bot._muted = bot._stopping = False
    bot._roster = _players()
    bot._view = {"channel": {"id": "1"}, "guilds": [{"voice_channels": [{"id": "1", "occupants": _members()}]}]}
    bot._map_sig, bot._map = None, []
    assert bot.ally_targets() == {"Jinx": "Paul"}
    assert all("Leo" not in t for t in bot.named_texts())
