"""Bot Discord : le coach annonce les ganks et le matchup dans le salon vocal de tes amis.

The bot runs INSIDE the app (no server): the player creates his own Discord application, pastes
its token in the launcher ("Alertes et voix" > "Bot Discord") and invites it to his server. While
the app is open and the option is on, :class:`DiscordCaller` keeps a gateway connection on its own
asyncio loop in a daemon thread; at game start it joins the voice channel where the player is,
says only two kinds of things, then leaves at the end of the game:

* ``matchup``: once per game, one short sentence ("Garen contre Darius : défavorable, joue safe
  jusqu'à 8 minutes.") written by the AI (ai_advisor.call_llm, one request counted in the game's AI
  budget, 6 s max), else the coach's own verdict (mastermind lane / team windows);
* ``gank``: the gank calls the app speaks (enemy jungler / roamers coming to my lane) as the app's
  danger chime + ONLY "Top gank" / "Mid gank" / "Bot gank" (clips pre-rendered when the bot starts),
  deduped per lane and rate-limited (:class:`CallFilter`).

Everything else is dropped. The engine reaches the bot through ONE hook,
``CoachEngine._call(kind, text, info)`` -> :meth:`DiscordCaller.on_call`, which never blocks
(a filter + a deque append) and never raises.

Library (2026): discord.py 2.7 with the ``voice`` extra: PyNaCl (transport encryption
``aead_xchacha20_poly1305_rtpsize``) + ``davey`` (Discord's end-to-end voice encryption "DAVE",
mandatory for every voice connection since 1 March 2026; discord.py 2.7.0 added it) + the
libopus DLL that discord.py ships for Windows. Gateway intents: ``guilds`` + ``voice_states``
only (no privileged intent, no message content). Audio: the app's natural voice (tts_neural,
24 kHz mono WAV, OneCore voice as the fallback on Windows) resampled to 48 kHz stereo PCM.

The token is a secret: never logged (``mask_secrets`` filter on the ``discord`` loggers), never in
a diagnostic (diag.py / fingerprint.py), ``repr=False`` in Config.
"""

from __future__ import annotations

import asyncio
import atexit
import base64
import collections
import io
import logging
import re
import sys
import threading
import time
import wave
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

#: kinds that are spoken in the voice channel (everything else is dropped)
SPOKEN_KINDS = frozenset({"gank", "matchup"})
#: lifecycle kinds: join the voice channel / leave it (never spoken)
CONTROL_KINDS = frozenset({"game_start", "game_end"})

GANK_MIN_GAP_S = 8.0         # two gank calls at least this far apart
GANK_SAME_S = 30.0           # the same champions on the same lane: once per 30 s
GANK_MAX_PER_MIN = 4         # and never more than 4 gank calls a minute
GANK_STALE_S = 5.0           # a gank call that could not start within 5 s is dropped (too late)
MATCHUP_STALE_S = 90.0
TEST_STALE_S = 30.0
QUEUE_MAX = 3                # pending sentences (the oldest gank goes first when full)
LOGIN_TIMEOUT_S = 25.0
JOIN_TIMEOUT_S = 15.0
BACKOFF_S = (2.0, 5.0, 15.0, 30.0, 60.0)
SPEECH_TIMEOUT_S = 4.0       # natural voice synthesis of a sentence not cached yet
AI_TIMEOUT_S = 6.0           # the matchup AI sentence: slower -> the coach's own sentence

#: View Channel (1 << 10) + Connect (1 << 20) + Speak (1 << 21)
INVITE_PERMISSIONS = (1 << 10) | (1 << 20) | (1 << 21)
DEVELOPER_PORTAL = "https://discord.com/developers/applications"

TEST_TEXT = "TreeAI est prêt. Exemple d'annonce :"

#: a Discord bot token: base64(user id) . timestamp . hmac
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{18,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{20,}")
_LANES = ("top", "mid", "bot")


# ------------------------------------------------------------------------------ pure helpers
def mask_secrets(text: Any) -> str:
    """``text`` with every Discord-token-shaped string replaced by ``[token masqué]``. Never raises."""
    try:
        return TOKEN_RE.sub("[token masqué]", str(text))
    except Exception:
        return ""


class _RedactFilter(logging.Filter):
    """Masks tokens in the records of a logger (discord.py, aiohttp) before they are written."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            clean = mask_secrets(msg)
            if clean != msg:
                record.msg, record.args = clean, None
        except Exception:
            pass
        return True


def clean_token(value: Any) -> str:
    """The token as pasted (spaces, quotes, a "Bot " prefix removed)."""
    s = "".join(ch for ch in str(value or "") if ch.isprintable()).strip().strip("\"'").strip()
    if s.lower().startswith("bot "):
        s = s[4:].strip()
    return s


def clean_user_id(value: Any) -> str:
    """A Discord user id (17-20 digits) or ``""``."""
    s = re.sub(r"\D", "", str(value or ""))
    return s if 15 <= len(s) <= 21 else ""


def application_id_from_token(token: Any) -> str | None:
    """The bot's user id (= its application id) encoded in the first part of the token, or None."""
    try:
        head = clean_token(token).split(".", 1)[0]
        if len(head) < 16:
            return None
        raw = base64.urlsafe_b64decode(head + "=" * (-len(head) % 4)).decode("ascii")
        return raw if raw.isdigit() and 15 <= len(raw) <= 21 else None
    except Exception:
        return None


def invite_url(app_id: Any) -> str | None:
    """The OAuth2 link that adds the bot to a server with View Channel + Connect + Speak."""
    s = str(app_id or "")
    if not s.isdigit():
        return None
    return (f"https://discord.com/oauth2/authorize?client_id={s}&scope=bot"
            f"&permissions={INVITE_PERMISSIONS}")


def gank_text(lane: Any) -> str:
    """The gank call heard by the friends: ONLY the lane word + "gank" ("Top gank", "Mid gank",
    "Bot gank"), after the danger chime. ``""`` when the lane is unknown (nothing is said)."""
    ln = str(lane or "").strip().lower()
    return f"{ln.capitalize()} gank" if ln in _LANES else ""


def _until_words(until_s: Any) -> str:
    try:
        m = int(round(float(until_s) / 60.0))
        return f" jusqu'à {m} minutes" if 2 <= m <= 40 else ""
    except (TypeError, ValueError):
        return ""


def matchup_verdict(me: Any, opp: Any, lane_leader: Any, until_s: Any = None, team_leader: Any = None) -> str:
    """The heuristic matchup sentence (mastermind lane / team windows), the AI's fallback:
    "Garen contre Darius : défavorable, joue safe jusqu'à 8 minutes ; ton équipe est plus forte."
    ``lane_leader`` / ``team_leader``: "us" / "them" / "even" (None: unknown). Pure, never raises."""
    try:
        me_s, opp_s = str(me or "").strip(), str(opp or "").strip()
        if not (me_s and opp_s):
            return ""
        until = _until_words(until_s)
        if lane_leader == "us":
            body = f"favorable, joue agressif{until}"
        elif lane_leader == "them":
            body = f"défavorable, joue safe{until}"
        else:
            body = "équilibré, gagne la lane sur les erreurs"
        team = {"us": " ; ton équipe est plus forte", "them": " ; leur équipe est plus forte"}.get(str(team_leader))
        return f"{me_s} contre {opp_s} : {body}{team or ''}."
    except Exception:
        return ""


AI_SYSTEM = ("Tu es un coach de League of Legends. Réponds en UNE seule phrase française de 22 mots au plus, "
             "sans emoji, sans guillemets, au tutoiement.")
AI_MAX_CHARS = 170


def matchup_prompt(me: str, opp: str, role: str = "", allies: Iterable[Any] = (), enemies: Iterable[Any] = ()) -> str:
    """The one AI request of the game for the matchup sentence."""
    al = ", ".join(str(a) for a in allies if a)
    en = ", ".join(str(e) for e in enemies if e)
    return (f"Début de partie. Ma voie ({role or 'voie'}) : {me} contre {opp}. "
            + (f"Mon équipe : {al}. " if al else "") + (f"Équipe adverse : {en}. " if en else "")
            + "Dis si je suis favorisé ou non dans la voie (et l'équipe en général si ça tient dans la phrase), "
            f"avec un conseil très court. Forme exacte : « {me} contre {opp} : favorable / défavorable / "
            "équilibré, conseil. »")


def clean_ai_line(text: Any, me: Any = "", opp: Any = "") -> str:
    """The AI's sentence made safe to speak: one line, no quotes / markdown / emoji, bounded, names
    present. ``""`` when unusable (the heuristic sentence is said instead)."""
    try:
        s = " ".join(str(text or "").replace("\n", " ").split())
        s = re.sub(r"[*_`#«»\"“”]", "", s).strip(" -")
        s = "".join(ch for ch in s if ch.isprintable() and (ord(ch) < 0x2000 or ch == "’"))
        m = re.search(r"[.!?](\s|$)", s)
        if m:
            s = s[: m.start() + 1]
        if not s or len(s) > AI_MAX_CHARS or len(s.split()) < 4:
            return ""
        if opp and str(opp).split()[0].casefold() not in s.casefold():
            return ""
        return s if s[-1] in ".!?" else s + "."
    except Exception:
        return ""


def wav_to_pcm48(data: bytes) -> bytes:
    """A 16-bit PCM WAV (any rate, mono or stereo) -> 48 kHz stereo signed 16-bit little-endian,
    padded to whole 20 ms frames (what discord.PCMAudio sends). ``b""`` if unreadable."""
    try:
        import numpy as np  # noqa: PLC0415

        with wave.open(io.BytesIO(data), "rb") as w:
            ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(n)
        if width != 2 or ch not in (1, 2) or rate < 4000 or not raw:
            return b""
        x = np.frombuffer(raw[: len(raw) // (2 * ch) * 2 * ch], dtype="<i2").reshape(-1, ch).astype(np.float32)
        if x.shape[0] == 0:
            return b""
        if rate != 48000:
            m = max(1, int(round(x.shape[0] * 48000 / rate)))
            src = np.arange(x.shape[0], dtype=np.float64)
            dst = np.linspace(0.0, x.shape[0] - 1, m)
            x = np.stack([np.interp(dst, src, x[:, c]) for c in range(ch)], axis=1)
        if x.shape[1] == 1:
            x = np.repeat(x, 2, axis=1)
        pcm = np.clip(np.round(x), -32768, 32767).astype("<i2").tobytes()
        frame = 3840                      # 20 ms of 48 kHz stereo s16
        pad = (-len(pcm)) % frame
        return pcm + b"\x00" * pad
    except Exception:
        log.debug("wav_to_pcm48 failed", exc_info=True)
        return b""


# ------------------------------------------------------------------------------ the filter
class CallFilter:
    """What may be said: only ``gank`` / ``matchup``, the matchup once per game, gank calls deduped
    (same champions + lane: once per :data:`GANK_SAME_S`), spaced (:data:`GANK_MIN_GAP_S`) and capped
    (:data:`GANK_MAX_PER_MIN`). Pure (clock injected), not thread-safe (DiscordCaller locks it)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.reset()

    def reset(self) -> None:
        self._matchup_done = False
        self._last_gank = -1e9
        self._gank_times: collections.deque[float] = collections.deque()
        self._seen: dict[str, float] = {}

    def admit(self, kind: str, key: str = "") -> bool:
        now = float(self._clock())
        if kind == "matchup":
            if self._matchup_done:
                return False
            self._matchup_done = True
            return True
        if kind != "gank":
            return False
        k = key or "?"
        if now - self._seen.get(k, -1e9) < GANK_SAME_S:
            return False
        if now - self._last_gank < GANK_MIN_GAP_S:
            return False
        while self._gank_times and now - self._gank_times[0] > 60.0:
            self._gank_times.popleft()
        if len(self._gank_times) >= GANK_MAX_PER_MIN:
            return False
        self._seen[k] = now
        self._last_gank = now
        self._gank_times.append(now)
        if len(self._seen) > 64:
            self._seen = {a: b for a, b in self._seen.items() if now - b < GANK_SAME_S}
        return True


@dataclass
class _Job:
    kind: str                       # "say" | "join" | "leave" | "test"
    text: str = ""
    t: float = 0.0
    stale_s: float = 0.0
    call_kind: str = ""
    done: Callable[[str, bool], None] | None = None
    lane: str = ""
    ai: Callable[[], Any] | None = None     # matchup: the one AI request (blocking, run off the loop)


@dataclass
class _Settings:
    enabled: bool = False
    token: str = field(default="", repr=False)
    user_id: str = ""
    neural_voice: str = ""
    neural_rate: str = ""

    def key(self) -> tuple:
        return (self.enabled, self.token, self.user_id)


class BadToken(Exception):
    """Discord refused the token."""


# ------------------------------------------------------------------------------ discord.py layer
class DiscordPyBackend:
    """The real Discord layer (discord.py 2.7 voice + DAVE). Every method runs on the bot loop."""

    def __init__(self) -> None:
        import discord  # noqa: PLC0415  (slow import: on the bot thread only)

        self._d = discord
        for name in ("discord", "discord.client", "discord.gateway", "discord.voice_state", "discord.http"):
            lg = logging.getLogger(name)
            lg.setLevel(logging.WARNING)
            if not any(isinstance(f, _RedactFilter) for f in lg.filters):
                lg.addFilter(_RedactFilter())
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        self.client = discord.Client(intents=intents, max_messages=None, chunk_guilds_at_startup=False)
        self._task: asyncio.Task | None = None

    async def start(self, token: str) -> tuple[str, str | None]:
        """Log in + open the gateway; ``(bot name, application id)``. BadToken if refused."""
        d = self._d
        try:
            await self.client.login(token)
        except d.LoginFailure as exc:
            raise BadToken() from exc
        self._task = asyncio.get_running_loop().create_task(self.client.connect(reconnect=True))
        waiter = asyncio.ensure_future(self.client.wait_until_ready())
        done, _ = await asyncio.wait({waiter, self._task}, timeout=LOGIN_TIMEOUT_S,
                                     return_when=asyncio.FIRST_COMPLETED)
        if waiter not in done:
            waiter.cancel()
            if self._task in done and self._task.exception() is not None:
                raise self._task.exception()  # type: ignore[misc]
            raise TimeoutError("gateway")
        user = self.client.user
        return (str(getattr(user, "name", "") or "bot"), str(self.client.application_id or getattr(user, "id", "")))

    def alive(self) -> bool:
        return self._task is not None and not self._task.done() and not self.client.is_closed()

    def guild_count(self) -> int:
        return len(self.client.guilds)

    def find_channel(self, user_id: str) -> Any:
        """The voice channel where ``user_id`` is (cache only, no network); without an id, the only
        voice channel with people in it. None if not found."""
        me = getattr(self.client.user, "id", None)
        uid = int(user_id) if user_id.isdigit() else None
        occupied = []
        for g in self.client.guilds:
            for ch in list(g.voice_channels) + list(getattr(g, "stage_channels", []) or []):
                ids = set(getattr(ch, "voice_states", {}) or {})
                if uid is not None and uid in ids:
                    return ch
                if ids - {me}:
                    occupied.append(ch)
        if uid is None and len(occupied) == 1:
            return occupied[0]
        return None

    @staticmethod
    def channel_name(ch: Any) -> str:
        return str(getattr(ch, "name", "") or "salon vocal")

    def can_speak(self, ch: Any) -> bool:
        try:
            p = ch.permissions_for(ch.guild.me)
            return bool(p.view_channel and p.connect and p.speak)
        except Exception:
            return True

    def _vc(self) -> Any:
        for vc in list(self.client.voice_clients):
            return vc
        return None

    def current_channel(self) -> Any:
        vc = self._vc()
        return vc.channel if vc is not None and vc.is_connected() else None

    async def join(self, ch: Any) -> None:
        vc = ch.guild.voice_client
        if vc is not None and getattr(vc.channel, "id", None) == ch.id and vc.is_connected():
            return
        others = [v for v in self.client.voice_clients if v is not vc]
        for v in others:
            await v.disconnect(force=True)
        if vc is not None:
            await vc.move_to(ch, timeout=JOIN_TIMEOUT_S)
        else:
            await ch.connect(timeout=JOIN_TIMEOUT_S, reconnect=True, self_deaf=True)

    async def play(self, pcm: bytes) -> None:
        vc = self._vc()
        if vc is None or not vc.is_connected():
            raise ConnectionError("not in voice")
        loop = asyncio.get_running_loop()
        done: asyncio.Future = loop.create_future()

        def after(err: Exception | None) -> None:
            loop.call_soon_threadsafe(lambda: done.done() or done.set_result(err))

        if vc.is_playing():
            vc.stop()
        vc.play(self._d.PCMAudio(io.BytesIO(pcm)), after=after)
        err = await asyncio.wait_for(done, len(pcm) / 192000.0 + 5.0)
        if err is not None:
            raise err

    async def leave(self) -> None:
        for vc in list(self.client.voice_clients):
            try:
                await vc.disconnect(force=True)
            except Exception:
                log.debug("voice disconnect failed", exc_info=True)

    async def close(self) -> None:
        await self.leave()
        try:
            await self.client.close()
        finally:
            if self._task is not None:
                self._task.cancel()


def discord_available() -> bool:
    """discord.py, PyNaCl and davey can be imported (cheap check: no import)."""
    try:
        import importlib.util  # noqa: PLC0415

        return all(importlib.util.find_spec(m) is not None for m in ("discord", "nacl", "davey"))
    except Exception:
        return False


# ------------------------------------------------------------------------------ the speech
class _Speech:
    """Text -> WAV bytes with the app's natural voice (cached, tts_neural), the Windows OneCore
    voice as the fallback. Blocking: called in the loop's executor."""

    def __init__(self) -> None:
        self._tts: Any = None
        self._params: tuple[str, str] | None = None
        self._onecore: Any = None
        self._lock = threading.Lock()

    def _neural(self, voice: str, rate: str) -> Any:
        with self._lock:
            if self._params != (voice, rate):
                self._params = (voice, rate)
                try:
                    from treeaicoach import tts_neural  # noqa: PLC0415

                    if tts_neural.neural_available():
                        self._tts = tts_neural.NeuralTTS(tts_neural.clean_voice_id(voice or tts_neural.DEFAULT_NEURAL_VOICE),
                                                         rate or tts_neural.DEFAULT_NEURAL_RATE)
                    else:
                        self._tts = None
                except Exception:
                    log.debug("Discord: natural voice unavailable", exc_info=True)
                    self._tts = None
            return self._tts

    def wav(self, text: str, voice: str = "", rate: str = "") -> bytes | None:
        tts = self._neural(voice, rate)
        if tts is not None:
            try:
                p = tts.get(text, timeout=SPEECH_TIMEOUT_S)
                if p is not None:
                    return p.read_bytes()
            except Exception:
                log.debug("Discord: natural voice failed", exc_info=True)
        if sys.platform == "win32":
            try:
                if self._onecore is None:
                    from treeaicoach import tts_neural  # noqa: PLC0415

                    self._onecore = tts_neural.OneCoreSynth()
                return bytes(self._onecore.wav(text))
            except Exception:
                log.debug("Discord: OneCore voice failed", exc_info=True)
        return None


# ------------------------------------------------------------------------------ the caller
class DiscordCaller:
    """The bot of the app (one per process: :func:`caller`). Thread-safe, never raises.

    ``backend_factory()`` builds the Discord layer (``DiscordPyBackend`` by default; tests pass a
    fake), ``speech(text, voice, rate) -> wav bytes`` the audio (the app's voice by default)."""

    def __init__(self, *, backend_factory: Callable[[], Any] | None = None,
                 speech: Callable[[str, str, str], bytes | None] | None = None,
                 beep: Callable[[], bytes] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._factory = backend_factory or DiscordPyBackend
        sp = _Speech() if speech is None else None
        self._speech = speech or (lambda text, v, r: sp.wav(text, v, r))  # type: ignore[union-attr]
        self._beep = beep or _danger_chime
        self._clips: dict[str, bytes] = {}          # lane -> chime + "Top gank" (48 kHz stereo PCM)
        self._clips_key: tuple[str, str] | None = None
        self._clock = clock
        self._lock = threading.RLock()
        self._filter = CallFilter(clock)
        self._jobs: collections.deque[_Job] = collections.deque()
        self._settings = _Settings()
        self._gen = 0                       # bumped on every (re)configuration: the loop restarts its session
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._status = ("Bot Discord désactivé.", False)
        self._in_game = False
        self.spoken: list[str] = []         # what was said (tests / diagnostics), last 50
        self.dropped = 0

    # -- public, any thread ---------------------------------------------------------------
    def configure(self, cfg: Any) -> None:
        """Apply the Config fields ``discord_enabled`` / ``discord_token`` / ``discord_user_id``
        (+ the natural voice). Starts / restarts / stops the bot. Never raises."""
        try:
            s = _Settings(enabled=bool(getattr(cfg, "discord_enabled", False)),
                          token=clean_token(getattr(cfg, "discord_token", "")),
                          user_id=clean_user_id(getattr(cfg, "discord_user_id", "")),
                          neural_voice=str(getattr(cfg, "neural_voice", "") or ""),
                          neural_rate=str(getattr(cfg, "neural_rate", "") or ""))
            with self._lock:
                changed = s.key() != self._settings.key()
                self._settings = s
                if not changed:
                    return
                self._gen += 1
                if not s.enabled:
                    self._status = ("Bot Discord désactivé.", False)
                elif not s.token:
                    self._status = ("Colle le token du bot.", False)
                else:
                    self._status = ("Connexion à Discord...", False)
            if s.enabled and s.token:
                self._ensure_thread()
            self._poke()
        except Exception:
            log.exception("Discord: configure failed")

    def on_call(self, kind: str, text: str = "", info: dict | None = None) -> bool:
        """THE engine hook. ``kind``: ``gank`` (``info["lane"]``: "top" / "mid" / "bot") and
        ``matchup`` (``text``: the heuristic sentence, ``info["ai"]``: optional blocking callable
        returning the AI sentence) are spoken, filtered; ``game_start`` / ``game_end`` join / leave
        the voice channel; anything else is ignored. Returns True when something was queued.
        Non-blocking, never raises."""
        try:
            info = info if isinstance(info, dict) else {}
            with self._lock:
                s = self._settings
                if not (s.enabled and s.token) or self._stopping:
                    return False
                now = float(self._clock())
                if kind in CONTROL_KINDS:
                    self._in_game = kind == "game_start"
                    if self._in_game:
                        self._filter.reset()
                    self._jobs = collections.deque(j for j in self._jobs if j.kind == "test")
                    self._jobs.append(_Job("join" if self._in_game else "leave", t=now))
                elif kind == "gank":
                    lane = str(info.get("lane") or "").strip().lower()
                    say = gank_text(lane)
                    if not say or not self._filter.admit("gank", lane):
                        self.dropped += 1
                        return False
                    self._queue_say(_Job("say", say, now, GANK_STALE_S, "gank", lane=lane))
                elif kind == "matchup":
                    say = " ".join(str(text or "").split())
                    ai = info.get("ai") if callable(info.get("ai")) else None
                    if not (say or ai) or not self._filter.admit("matchup"):
                        self.dropped += 1
                        return False
                    self._queue_say(_Job("say", say, now, MATCHUP_STALE_S, "matchup", ai=ai,
                                         lane=str(info.get("opp") or "")))
                else:
                    return False
            self._ensure_thread()
            self._poke()
            return True
        except Exception:
            log.debug("Discord: on_call failed", exc_info=True)
            return False

    def _queue_say(self, job: _Job) -> None:
        says = [j for j in self._jobs if j.kind == "say"]
        if len(says) >= QUEUE_MAX:
            self._jobs.remove(says[0])
            self.dropped += 1
        self._jobs.append(job)

    def test(self, done: Callable[[str, bool], None] | None = None) -> bool:
        """"Faire parler le bot": connect if needed, join the player's channel, say a sentence
        (leave again if no game is on). ``done(status, ok)`` is called from the bot thread."""
        with self._lock:
            s = self._settings
            if not s.enabled or not s.token:
                msg = "Active le bot et colle son token d'abord." if not s.token else "Active le bot d'abord."
                if done is not None:
                    try:
                        done(msg, False)
                    except Exception:
                        log.debug("Discord: test callback failed", exc_info=True)
                return False
            self._jobs.append(_Job("test", TEST_TEXT, float(self._clock()), TEST_STALE_S, "test", done))
        self._ensure_thread()
        self._poke()
        return True

    def test_blocking(self, timeout: float = 45.0) -> tuple[str, bool]:
        """:meth:`test` waiting for its result (for a UI worker thread)."""
        evt = threading.Event()
        box: list[tuple[str, bool]] = []

        def done(msg: str, ok: bool) -> None:
            box.append((msg, ok))
            evt.set()

        if not self.test(done):
            return box[0] if box else self.status()
        if not evt.wait(timeout):
            return ("Discord ne répond pas.", False)
        return box[0]

    def status(self) -> tuple[str, bool]:
        """``(message in French, ok)`` for the launcher."""
        with self._lock:
            return self._status

    def shutdown(self, timeout: float = 2.0) -> None:
        """Leave the voice channel and close the connection (app closing). Never raises."""
        try:
            with self._lock:
                self._stopping = True
                th = self._thread
            self._poke()
            if th is not None and th.is_alive() and th is not threading.current_thread():
                th.join(max(0.05, float(timeout)))
        except Exception:
            log.debug("Discord: shutdown failed", exc_info=True)

    # -- internals ------------------------------------------------------------------------
    def _gank_clip(self, lane: str, voice: str, rate: str) -> bytes:
        """The danger chime then "Top gank" (48 kHz stereo PCM); cached per voice. Blocking."""
        with self._lock:
            if self._clips_key != (voice, rate):
                self._clips_key, self._clips = (voice, rate), {}
            clip = self._clips.get(lane)
        if clip:
            return clip
        beep = wav_to_pcm48(self._beep() or b"")
        words = wav_to_pcm48(self._speech(gank_text(lane), voice, rate) or b"")
        if not words:
            return beep                      # no voice right now: the chime alone, not cached
        clip = beep + b"\x00" * (3840 * 4) + words if beep else words
        with self._lock:
            if self._clips_key == (voice, rate):
                self._clips[lane] = clip
        return clip

    def prerender(self) -> int:
        """Render the three gank clips now (bot start), so a call plays instantly. Blocking (run on
        a daemon thread); returns how many clips are ready. Never raises."""
        with self._lock:
            s = self._settings
        n = 0
        for lane in _LANES:
            try:
                if len(self._gank_clip(lane, s.neural_voice, s.neural_rate)) > 0:
                    n += 1
            except Exception:
                log.debug("Discord: clip %s failed", lane, exc_info=True)
        return n

    async def _blocking(self, fn: Callable[..., Any], *args: Any, timeout: float) -> Any:
        """``fn(*args)`` on a daemon thread (never the loop, never a non-daemon executor that
        would hold the app at exit); TimeoutError after ``timeout``."""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()

        def run() -> None:
            try:
                res, err = fn(*args), None
            except BaseException as exc:  # noqa: BLE001
                res, err = None, exc

            def put() -> None:
                if not fut.done():
                    fut.set_exception(err) if err is not None else fut.set_result(res)
            try:
                loop.call_soon_threadsafe(put)
            except RuntimeError:
                pass
        threading.Thread(target=run, name="TreeAI-discord-job", daemon=True).start()
        return await asyncio.wait_for(fut, timeout)

    def _set_status(self, msg: str, ok: bool) -> None:
        with self._lock:
            if self._status != (msg, ok):
                log.info("Discord : %s", mask_secrets(msg))
            self._status = (msg, ok)

    def _poke(self) -> None:
        loop, wake = self._loop, self._wake
        if loop is not None and wake is not None:
            try:
                loop.call_soon_threadsafe(wake.set)
            except RuntimeError:
                pass

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._stopping or (self._thread is not None and self._thread.is_alive()):
                return
            self._thread = threading.Thread(target=self._run, name="TreeAI-discord", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            self._wake = asyncio.Event()
            self._loop = loop
            loop.run_until_complete(self._main())
        except Exception:
            log.exception("Discord: bot loop crashed")
        finally:
            self._loop = None
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()

    async def _wait(self, timeout: float) -> None:
        wake = self._wake
        assert wake is not None
        try:
            await asyncio.wait_for(wake.wait(), timeout)
        except asyncio.TimeoutError:
            pass
        wake.clear()

    def _pop(self) -> _Job | None:
        with self._lock:
            return self._jobs.popleft() if self._jobs else None

    def _fail_tests(self, msg: str) -> None:
        with self._lock:
            tests = [j for j in self._jobs if j.kind == "test"]
            self._jobs = collections.deque(j for j in self._jobs if j.kind != "test")
        for j in tests:
            self._done(j, msg, False)

    @staticmethod
    def _done(job: _Job, msg: str, ok: bool) -> None:
        if job.done is not None:
            try:
                job.done(msg, ok)
            except Exception:
                log.debug("Discord: test callback failed", exc_info=True)

    async def _main(self) -> None:
        attempt = 0
        while True:
            with self._lock:
                s, gen, stopping = self._settings, self._gen, self._stopping
            if stopping:
                return
            if not (s.enabled and s.token):
                self._fail_tests(self.status()[0])
                with self._lock:
                    self._jobs.clear()
                await self._wait(3600.0)
                continue
            try:
                backend = self._factory()
            except Exception as exc:
                log.warning("Discord : module indisponible (%s)", type(exc).__name__)
                self._set_status("Voix Discord indisponible dans cette version (module manquant).", False)
                self._fail_tests(self.status()[0])
                await self._wait(3600.0)
                continue
            result = await self._session(backend, s, gen)
            try:
                await asyncio.wait_for(backend.close(), 5.0)
            except Exception:
                log.debug("Discord: close failed", exc_info=True)
            if result == "ok":
                attempt = 0
                continue
            if result == "bad_token":
                self._fail_tests(self.status()[0])
                while True:                       # no retry until the token changes
                    with self._lock:
                        if self._stopping or self._gen != gen:
                            break
                    await self._wait(3600.0)
                continue
            delay = BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)]
            attempt += 1
            self._set_status(f"Discord ne répond pas, nouvel essai dans {int(delay)} s.", False)
            self._fail_tests(self.status()[0])
            t_end = time.monotonic() + delay
            while time.monotonic() < t_end:
                with self._lock:
                    if self._stopping or self._gen != gen:
                        break
                await self._wait(max(0.05, t_end - time.monotonic()))

    async def _session(self, backend: Any, s: _Settings, gen: int) -> str:
        """One gateway session: "ok" (reconfigured / stopping), "bad_token" or "error"."""
        self._set_status("Connexion à Discord...", False)
        try:
            name, _app_id = await backend.start(s.token)
        except BadToken:
            self._set_status("Token invalide : recopie-le depuis le portail Discord (onglet Bot).", False)
            return "bad_token"
        except Exception as exc:
            log.warning("Discord : connexion impossible (%s)", mask_secrets(type(exc).__name__))
            return "error"
        self._idle_status(backend, name)
        threading.Thread(target=self.prerender, name="TreeAI-discord-clips", daemon=True).start()
        while True:
            with self._lock:
                if self._stopping or self._gen != gen:
                    return "ok"
            if not backend.alive():
                return "error"
            job = self._pop()
            if job is None:
                await self._wait(5.0)
                continue
            try:
                await self._handle(backend, job, s, name)
            except Exception as exc:
                log.warning("Discord : %s a échoué (%s)", job.kind, mask_secrets(type(exc).__name__))
                if job.kind == "test":
                    self._done(job, "Le bot n'a pas pu parler (voir le journal).", False)

    def _idle_status(self, backend: Any, name: str) -> None:
        if backend.guild_count() == 0:
            self._set_status(f"Connecté ({name}) mais sur aucun serveur : invite-le avec le lien.", False)
        else:
            self._set_status(f"Connecté ({name}). Il rejoindra ton salon vocal au début de la partie.", True)

    async def _ensure_voice(self, backend: Any, s: _Settings) -> bool:
        ch = backend.find_channel(s.user_id)
        if ch is None:
            if backend.guild_count() == 0:
                self._set_status("Le bot n'est sur aucun serveur : invite-le avec le lien.", False)
            elif s.user_id:
                self._set_status("Rejoins un salon vocal sur un serveur où le bot est invité.", False)
            else:
                self._set_status("Rejoins un salon vocal, ou indique ton identifiant Discord.", False)
            return False
        cname = backend.channel_name(ch)
        if not backend.can_speak(ch):
            self._set_status(f"Le bot n'a pas le droit de parler dans #{cname}.", False)
            return False
        await asyncio.wait_for(backend.join(ch), JOIN_TIMEOUT_S + 5.0)
        self._set_status(f"Connecté à #{cname}.", True)
        return True

    async def _handle(self, backend: Any, job: _Job, s: _Settings, name: str) -> None:
        if job.kind == "leave":
            await backend.leave()
            self._idle_status(backend, name)
            return
        if job.kind == "join":
            await self._ensure_voice(backend, s)
            return
        # "say" / "test"
        if float(self._clock()) - job.t > job.stale_s:
            self.dropped += 1
            return
        if not await self._ensure_voice(backend, s):
            if job.kind == "test":
                self._done(job, self.status()[0], False)
            return
        v, r = s.neural_voice, s.neural_rate
        text = job.text
        if job.call_kind == "gank":
            pcm = await self._blocking(self._gank_clip, job.lane, v, r, timeout=SPEECH_TIMEOUT_S + 2.0)
        else:
            if job.ai is not None:                 # matchup: the one AI sentence of the game, else the rules
                try:
                    said = clean_ai_line(await self._blocking(job.ai, timeout=AI_TIMEOUT_S), opp=job.lane)
                    if said:
                        text = said
                except Exception as exc:
                    log.info("Discord : phrase IA du matchup indisponible (%s), phrase du coach utilisée",
                             type(exc).__name__)
            if not text:
                return
            wav = await self._blocking(self._speech, text, v, r, timeout=SPEECH_TIMEOUT_S + 2.0)
            pcm = wav_to_pcm48(wav) if wav else b""
            if pcm and job.kind == "test":           # the test ends with a real gank call
                pcm += b"\x00" * (3840 * 15) + await self._blocking(self._gank_clip, "bot", v, r,
                                                                    timeout=SPEECH_TIMEOUT_S + 2.0)
        if not pcm:
            if job.kind == "test":
                self._done(job, "Pas de voix disponible pour le bot (Internet ?).", False)
            return
        if job.kind != "test" and float(self._clock()) - job.t > job.stale_s:
            self.dropped += 1
            return
        await backend.play(pcm)
        with self._lock:
            self.spoken.append(text)
            del self.spoken[:-50]
        if job.kind == "test":
            msg = self.status()[0]
            self._done(job, f"{msg} Le bot vient de parler.", True)
            with self._lock:
                in_game = self._in_game
            if not in_game:
                await backend.leave()
                self._set_status(msg.replace("Connecté à", "Testé dans"), True)


def _danger_chime() -> bytes:
    """The app's soft danger chime (assets/sounds/gank.wav, chimes.py) as WAV bytes."""
    try:
        from treeaicoach import chimes  # noqa: PLC0415

        return chimes.wav_bytes("gank", 100)
    except Exception:
        return b""


# ------------------------------------------------------------------------------ the singleton
_CALLER: DiscordCaller | None = None
_CALLER_LOCK = threading.Lock()


def caller() -> DiscordCaller:
    """The bot of this process (created on first use)."""
    global _CALLER
    with _CALLER_LOCK:
        if _CALLER is None:
            _CALLER = DiscordCaller()
            atexit.register(_CALLER.shutdown, 1.5)
        return _CALLER


def active() -> DiscordCaller | None:
    """The bot if it was ever created (no creation)."""
    return _CALLER


def shutdown(timeout: float = 2.0) -> None:
    """Close the bot if it exists (app closing). Never raises."""
    c = _CALLER
    if c is not None:
        c.shutdown(timeout)


__all__ = ["DiscordCaller", "CallFilter", "DiscordPyBackend", "BadToken", "caller", "active", "shutdown",
           "gank_text", "matchup_verdict", "matchup_prompt", "clean_ai_line", "wav_to_pcm48", "mask_secrets", "clean_token", "clean_user_id",
           "application_id_from_token", "invite_url", "discord_available", "SPOKEN_KINDS", "CONTROL_KINDS"]
