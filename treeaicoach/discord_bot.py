"""Bot Discord : le coach annonce les ganks et le matchup dans le salon vocal de tes amis.

The bot runs INSIDE the app (no server): the player creates his own Discord application, pastes
its token in the launcher (page "Discord") and invites it to his server. While
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

The launcher page "Discord" (ui_page_discord.py) polls :meth:`DiscordCaller.snapshot` (a copy of the
bot's state refreshed on the bot loop every :data:`SNAPSHOT_EVERY_S` and on Discord's voice / guild
events: servers, voice channels with their occupants and the bot's permissions, the player found in
voice, latency, the last things the bot did) and sends :meth:`join` / :meth:`leave` /
:meth:`say_test`, queued to the bot loop like every other job (never blocking the window).

The token is a secret: never logged (``mask_secrets`` filter on the ``discord`` loggers), never in
a diagnostic (diag.py / fingerprint.py), ``repr=False`` in Config.

2.9.1 (user: "le bot n'arrive pas à parler et le volume est trop haut"): the voice goes through
:class:`discord_audio.BotVoice` (Edge -> OneCore -> SAPI -> clips recorded at build time, every answer
verified audible, the source logged and shown: "Voix : naturelle / Windows / enregistrée"); levels
are normalised and scaled by ``discord_volume`` (default 40 = 35 % of 2.9.0, chime 6 dB under the
words). Optional calls, all off by default: objectives 30 s before spawn (``discord_objectives``),
the enemy jungler seen on a lane (``discord_jungler_seen``, lowest priority), the end-of-game line
(``discord_summary``); ``discord_only`` lets the bot say the calls instead of the PC voice;
``hotkey_discord_mute`` silences the bot. No summoner spell / cooldown call (Riot policy).
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
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from treeaicoach import discord_audio as da
from treeaicoach import discord_friends as df

log = logging.getLogger(__name__)

#: kinds that are spoken in the voice channel (everything else is dropped); ``ally_gank`` = a gank on a
#: friend of the voice channel (ally_gank.py), said like mine ("Bot gank")
SPOKEN_KINDS = frozenset({"gank", "ally_gank", "matchup", "objective", "jungler_seen", "summary", "team_call",
                          "ranks", "team_brain"})
#: route "discord" (Tout par Discord): every sentence / beep of the app goes through the bot too
ROUTED_KINDS = frozenset({"coach", "beep"})
ROUTES = ("both", "discord", "pc")
#: every kind that makes the bot speak (dropped while muted / switched off / route "pc")
SPEAKING_KINDS = SPOKEN_KINDS | ROUTED_KINDS | frozenset({"matchups", "ai_answer"})
#: jobs asked from the launcher: a game start / end never drops them
USER_JOBS = frozenset({"test", "join_ch", "leave_now", "hush"})
#: priority when the queue is full / which job goes first (higher = more urgent)
PRIORITY = {"gank": 4, "beep": 4, "chime": 5, "team_call": 3, "test": 3, "ai_answer": 3, "coach": 2,
            "objective": 2, "team_brain": 1, "matchup": 1, "matchups": 1, "ranks": 1, "jungler_seen": 0, "summary": 0}
#: lifecycle kinds: join the voice channel / leave it (never spoken); ``roster``: my team's players
#: (Live Client) to match the voice members against (discord_friends.py)
CONTROL_KINDS = frozenset({"game_start", "game_end", "roster"})
TEXT_MAX_CHARS = 1900          # a Discord message is 2000 characters at most
TEXT_STALE_S = 600.0           # the post-game recap waits for the LCU / report at most this long

GANK_MIN_GAP_S = 8.0         # two gank calls about the same person (me / one friend) at least this far apart
GANK_ANY_GAP_S = 3.0         # any two gank calls (two friends), and a gank call and an objective call
GANK_SAME_S = 30.0           # the same lane: once per 30 s (bot duo ganked: ONE "Bot gank")
ALLY_SAME_S = 30.0           # the same friend: once per 30 s
GANK_MAX_PER_MIN = 4         # and never more than 4 gank calls a minute
GANK_STALE_S = 5.0           # a gank call that could not start within 5 s is dropped (too late)
CALLS_MAX_PER_MIN = 6        # every spoken call together (gank first: the others yield)
OBJECTIVE_STALE_S = 6.0      # "Dragon dans 30 secondes" said later than 6 s is wrong: dropped
SEEN_STALE_S = 4.0
SEEN_SAME_LANE_S = 45.0      # the jungler seen on the same lane: once per 45 s
SEEN_AFTER_GANK_S = 20.0     # and never within 20 s of a gank call (a gank says more)
SEEN_MAX_PER_MIN = 2
SUMMARY_STALE_S = 180.0
RANKS_STALE_S = 90.0          # the game-start ranks: still useful in the first minutes (loading screen, lanes)
TEAM_STALE_S = 5.0           # "Ils sont 4 bot : Baron maintenant" later than 5 s is a different map: dropped
TEAM_GAP_S = 20.0            # two team calls at least this far apart (a team acts on one at a time)
TEAM_MAX_CHARS = 90
BRAIN_FRIEND_S = 30.0        # team_brain.py: at most ONE voice line per friend per 30 s (his gank calls count)
BRAIN_STALE_S = 5.0
BRAIN_AFTER_GANK_S = 20.0    # "jungler probablement chez toi" never within 20 s of a gank call (a gank says more)
OBJECTIVE_KEYS = {"dragon": "Dragon", "baron": "Baron", "grubs": "Larves"}
OBJECTIVE_LEAD_S = 30
MATCHUP_STALE_S = 90.0
TEST_STALE_S = 30.0
COACH_STALE_S = 6.0          # a coach sentence (route "discord") later than 6 s is about another moment
BEEP_STALE_S = 1.5           # a danger beep later than 1.5 s means nothing
CHIME_STALE_S = 1.0          # the "je t'écoute" chime of « Hey coach »
AI_ANSWER_STALE_S = 40.0     # the AI answer to a question asked in the voice channel
FRIENDS_STALE_S = 150.0      # the friends' matchups (game start)
FRIENDS_GAP_FRAMES = 40      # 0.8 s of silence between two friends' matchups
FRIENDS_MAX = 4
QUEUE_MAX = 3                # pending sentences (the oldest gank goes first when full)
LOGIN_TIMEOUT_S = 25.0
JOIN_TIMEOUT_S = 15.0
BACKOFF_S = (2.0, 5.0, 15.0, 30.0, 60.0)
SPEECH_TIMEOUT_S = 4.0       # natural voice synthesis of a sentence not cached yet
CHAIN_TIMEOUT_S = 17.0       # the whole voice chain (Edge 4 s + OneCore 5 s + SAPI 6 s + clip)
AI_TIMEOUT_S = 6.0           # the matchup AI sentence: slower -> the coach's own sentence
SNAPSHOT_EVERY_S = 2.0       # the launcher's view of the bot is refreshed this often (and on events)
EVENTS_MAX = 10              # what the bot did / said, shown in the launcher
USER_LOOKUP_TIMEOUT_S = 5.0

#: View Channel (1 << 10) + Connect (1 << 20) + Speak (1 << 21)
INVITE_PERMISSIONS = (1 << 10) | (1 << 20) | (1 << 21)
DEVELOPER_PORTAL = "https://discord.com/developers/applications"

TEST_TEXT = "TreeAI est prêt. Exemple d'annonce :"
#: "Dire le matchup" in the launcher: an example of the game-start sentence
EXAMPLE_MATCHUP = "Garen contre Darius : défavorable, joue safe jusqu'à 8 minutes."
#: the permissions the bot needs in a voice channel (discord.Permissions attribute, French label)
VOICE_PERMS = (("view_channel", "voir le salon"), ("connect", "se connecter"), ("speak", "parler"))

#: a Discord bot token: base64(user id) . timestamp . hmac
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{18,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{20,}")
_LANES = ("top", "mid", "bot")
_GANK_LANES = _LANES + ("jungle",)


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


def error_text(exc: BaseException) -> str:
    """Short, secret-free reason shown to the player: exception name + message (120 chars)."""
    try:
        msg = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    except Exception:
        msg = ""
    text = type(exc).__name__ + (f" : {msg}" if msg else "")
    return mask_secrets(text)[:120]


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


def route_of(cfg: Any) -> str:
    """Where the app's sound goes: ``discord_route`` (both / discord / pc); the legacy switch
    ``discord_only`` (« Couper la voix du coach sur le PC ») means "discord"."""
    r = str(getattr(cfg, "discord_route", "both") or "both").lower()
    if r not in ROUTES:
        r = "both"
    if r == "both" and getattr(cfg, "discord_only", False) is True:
        r = "discord"
    return r


def friends_prompt(pairs: Iterable[dict]) -> str:
    """ONE AI request for every friend's matchup: one line per lane, « champion: phrase »."""
    rows = [f"- {p.get('champion')} ({p.get('role') or '?'}) contre {p.get('opp')}" for p in pairs]
    return ("Pour chaque duel de lane ci-dessous, donne UNE phrase française de 14 mots au plus pour le "
            "joueur débutant qui joue le premier champion : favorable / défavorable et jusqu'à quand. "
            "Réponds exactement une ligne par duel, au format « Champion : phrase ».\n" + "\n".join(rows))


def parse_friends_answer(text: Any, champions: Iterable[str]) -> dict[str, str]:
    """``{champion: sentence}`` from the AI answer of :func:`friends_prompt` (unknown lines dropped)."""
    out: dict[str, str] = {}
    names = {df.norm_name(c): str(c) for c in champions if c}
    for line in str(text or "").splitlines():
        head, sep, tail = line.strip().lstrip("-*• ").partition(":")
        key = df.norm_name(head)
        if sep and key in names and names[key] not in out:
            said = clean_ai_line(tail)
            if said:
                out[names[key]] = said
    return out


def gank_text(lane: Any) -> str:
    """The gank call heard by the friends: ONLY the lane word + "gank" ("Top gank", "Mid gank",
    "Bot gank", "Jungle gank"), after the danger chime. ``""`` when the lane is unknown (nothing is said)."""
    ln = str(lane or "").strip().lower()
    return f"{ln.capitalize()} gank" if ln in _GANK_LANES else ""


def objective_text(key: Any) -> str:
    """"Dragon dans 30 secondes" / "Baron ..." / "Larves ..."; ``""`` for any other objective."""
    name = OBJECTIVE_KEYS.get(str(key or "").strip().lower())
    return f"{name} dans {OBJECTIVE_LEAD_S} secondes" if name else ""


def seen_text(lane: Any) -> str:
    """"Jungler ennemi vu top / mid / bot"; ``""`` when the lane is unknown."""
    ln = str(lane or "").strip().lower()
    return f"Jungler ennemi vu {ln}" if ln in _LANES else ""


def nearest_lane(u: Any, v: Any) -> str | None:
    """The lane nearest to a minimap position (geometry.py convention: blue base bottom-left, mid
    lane ``u + v = 1``, top along the left / top edges, bot along the bottom / right edges)."""
    try:
        uu, vv = float(u), float(v)
        if not (0.0 <= uu <= 1.0 and 0.0 <= vv <= 1.0):
            return None
        d = {"top": min(uu, vv), "mid": abs(uu + vv - 1.0) / 1.4142, "bot": min(1.0 - uu, 1.0 - vv)}
        return min(d, key=d.get)
    except (TypeError, ValueError):
        return None


def summary_line(analysis: Any) -> str:
    """The one end-of-game line for the friends: "Victoire en 32 minutes, 9 ganks évités sur 10."
    (the gank part only when a gank was faced). ``""`` when the result is unknown. Never raises."""
    try:
        a = analysis if isinstance(analysis, dict) else {}
        s = a.get("summary") if isinstance(a.get("summary"), dict) else {}
        res = {"Win": "Victoire", "Lose": "Défaite"}.get(str(s.get("result") or ""))
        if not res:
            return ""
        dur = float(s.get("duration") or 0.0)
        mins = int(round(dur / 60.0)) if dur == dur and dur > 0 else 0
        head = f"{res} en {mins} minutes" if mins else res
        faced = int(a.get("ganks_faced") or 0)
        if faced >= 1:
            ok = max(0, min(faced, int(a.get("ganks_survived") or 0)))
            return f"{head}, {ok} gank{'s' if ok > 1 else ''} évité{'s' if ok > 1 else ''} sur {faced}."
        return head + "."
    except Exception:
        return ""


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
    """A WAV (any rate / width / channels, extensible header too) -> 48 kHz stereo signed 16-bit
    little-endian, padded to whole 20 ms frames (what discord.PCMAudio sends), level unchanged.
    ``b""`` if unreadable (discord_audio.to_pcm48)."""
    return da.to_pcm48(data)


def missing_permissions(ch: Any, me: Any) -> list[str] | None:
    """The voice permissions the bot lacks in ``ch`` (French labels, :data:`VOICE_PERMS`); ``[]`` = all
    granted, ``None`` = unknown (no cached member / no permission data). Never raises."""
    try:
        if me is None:
            return None
        p = ch.permissions_for(me)
        return [lbl for attr, lbl in VOICE_PERMS if not bool(getattr(p, attr, False))]
    except Exception:
        return None


#: Discord voice gateway close codes -> the reason shown to the player
VOICE_CLOSE_CODES = {
    4004: "authentification vocale refusée",
    4006: "session vocale invalide (Discord l'a refusée, nouvel essai)",
    4009: "session vocale expirée",
    4011: "serveur vocal introuvable",
    4014: "déconnecté du salon (expulsé, salon supprimé ou bot déplacé)",
    4015: "le serveur vocal de Discord a planté, nouvel essai",
    4016: "mode de chiffrement inconnu (mets TreeAI à jour)",
    4017: "chiffrement de bout en bout DAVE exigé (module davey manquant ?)",
    4020: "requête vocale refusée par Discord",
    4021: "connexion vocale limitée par Discord, réessaie plus tard",
    4022: "appel terminé par Discord",
}


def join_error_text(exc: BaseException) -> str:
    """Why joining a voice channel failed, in French, with the technical cause (never raises)."""
    try:
        name = type(exc).__name__
        msg = str(exc)
        low = (name + " " + msg).lower()
        code = getattr(exc, "code", None)
        if name == "ConnectionClosed" or isinstance(code, int) and 4000 <= code < 5000:
            why = VOICE_CLOSE_CODES.get(code) if isinstance(code, int) else None
            return f"{why or 'connexion vocale fermée par Discord'} (code {code})"
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            return "le salon vocal ne répond pas : pare-feu ou UDP bloqué ?"
        if "opus" in low:
            return "bibliothèque audio opus manquante"
        if "davey" in low or "dave" in low.split():
            return "module davey (chiffrement DAVE) manquant"
        if "nacl" in low:
            return "module PyNaCl manquant"
        if "already connected" in low:
            return "déjà connecté à un salon vocal"
        return error_text(exc)
    except Exception:
        return "erreur inconnue"


def _member_name(m: Any) -> str:
    return str(getattr(m, "display_name", "") or getattr(m, "global_name", "") or getattr(m, "name", "") or "")


def _occupant(m: Any) -> dict:
    """A voice member as plain data for the name matching (discord_friends.py): every name he has."""
    return {"id": str(getattr(m, "id", "") or ""), "display": str(getattr(m, "display_name", "") or ""),
            "global": str(getattr(m, "global_name", "") or ""), "name": str(getattr(m, "name", "") or "")}


def describe_guilds(guilds: Iterable[Any], user_id: str = "", bot_id: Any = None,
                    bot_channel_id: Any = None) -> tuple[list[dict], dict]:
    """The servers of the bot as plain data for the launcher (from discord.py's cache, no network):
    ``([{id, name, members, voice_channels: [{id, name, members, people, can_speak, missing, me_here,
    bot_here}]}], user)`` with ``user = {id, found, name, channel_id, channel, guild, guild_id}`` (the
    player in voice). Voice occupants come from ``channel.voice_states`` (the ``voice_states`` intent,
    no privileged intent needed); names from ``channel.members`` when cached. Duck-typed, never raises."""
    uid = int(user_id) if str(user_id or "").isdigit() else None
    user: dict[str, Any] = {"id": str(user_id or ""), "found": False, "name": "", "channel_id": None,
                            "channel": "", "guild": "", "guild_id": None}
    out: list[dict] = []
    for g in list(guilds or []):
        try:
            me = getattr(g, "me", None)
            chans: list[dict] = []
            raw = list(getattr(g, "voice_channels", None) or []) + list(getattr(g, "stage_channels", None) or [])
            raw.sort(key=lambda c: (int(getattr(c, "position", 0) or 0), int(getattr(c, "id", 0) or 0)))
            for ch in raw:
                ids = set(getattr(ch, "voice_states", None) or {})
                members = {getattr(m, "id", None): m for m in list(getattr(ch, "members", None) or [])}
                people = [_member_name(members[i]) for i in ids if i != bot_id and i in members]
                occupants = sorted((_occupant(members[i]) for i in ids if i != bot_id and i in members),
                                   key=lambda o: (o["display"] or o["name"]).casefold())
                missing = missing_permissions(ch, me)
                cid = str(getattr(ch, "id", ""))
                here = uid is not None and uid in ids
                chans.append({"id": cid, "name": str(getattr(ch, "name", "") or "salon vocal"),
                              "members": len(ids - {bot_id}), "people": sorted(p for p in people if p),
                              "can_speak": missing is None or not missing, "missing": missing or [],
                              "me_here": here, "bot_here": str(bot_channel_id or "") == cid or (bot_id in ids),
                              "occupants": occupants})
                if here:
                    m = members.get(uid)
                    user.update(found=True, channel_id=cid, channel=chans[-1]["name"], guild=str(g.name),
                                guild_id=str(g.id), name=_member_name(m) if m is not None else user["name"])
            if uid is not None and not user["name"]:
                m = g.get_member(uid) if callable(getattr(g, "get_member", None)) else None
                if m is not None:
                    user.update(found=True, name=_member_name(m))
            out.append({"id": str(getattr(g, "id", "")), "name": str(getattr(g, "name", "") or "serveur"),
                        "members": int(getattr(g, "member_count", 0) or 0), "voice_channels": chans})
        except Exception:
            log.debug("Discord: describe guild failed", exc_info=True)
    return out, user


# ------------------------------------------------------------------------------ the filter
class CallFilter:
    """What may be said: only :data:`SPOKEN_KINDS`; the matchup and the summary once per game; gank
    calls deduped (same lane: once per :data:`GANK_SAME_S`), spaced (:data:`GANK_MIN_GAP_S`) and capped
    (:data:`GANK_MAX_PER_MIN`); an objective once per spawn; "jungler seen" (lowest priority) once
    per lane per :data:`SEEN_SAME_LANE_S`, never near a gank call, :data:`SEEN_MAX_PER_MIN`; every
    call together at most :data:`CALLS_MAX_PER_MIN` (a gank call is never refused by that cap: the
    others yield). Pure (clock injected), not thread-safe (DiscordCaller locks it)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.reset()

    def reset(self) -> None:
        self._friends_done = False
        self._matchup_done = False
        self._summary_done = False
        self._ranks_done = False
        self._last_gank = -1e9
        self._target_t: dict[str, float] = {}
        self._gank_times: collections.deque[float] = collections.deque()
        self._seen: dict[str, float] = {}
        self._objectives: set[str] = set()
        self._seen_lane: dict[str, float] = {}
        self._seen_times: collections.deque[float] = collections.deque()
        self._all_times: collections.deque[float] = collections.deque()
        self._team: set[str] = set()
        self._last_team = -1e9
        self._friend_t: dict[str, float] = {}       # friend (champion alias key) -> last line said to him

    @staticmethod
    def _recent(times: collections.deque[float], now: float) -> int:
        while times and now - times[0] > 60.0:
            times.popleft()
        return len(times)

    def admit(self, kind: str, key: str = "", target: str = "", targets: tuple = (), rule: str = "") -> bool:
        """``key``: the lane of a gank call; ``target``: who is ganked ("" = me, else the friend's
        champion): the same person at most every :data:`GANK_MIN_GAP_S` (me) / :data:`ALLY_SAME_S` (a
        friend), any two gank calls :data:`GANK_ANY_GAP_S` apart, one call per lane per
        :data:`GANK_SAME_S` (two friends of the bot lane: one call)."""
        now = float(self._clock())
        full = self._recent(self._all_times, now) >= CALLS_MAX_PER_MIN
        if kind == "matchup":
            if self._matchup_done:
                return False
            self._matchup_done = True
            return True
        if kind == "matchups":
            if getattr(self, "_friends_done", False):
                return False
            self._friends_done = True
            return True
        if kind == "ranks":
            if getattr(self, "_ranks_done", False):
                return False
            self._ranks_done = True
            return True
        if kind == "summary":
            if self._summary_done:
                return False
            self._summary_done = True
            return True
        if kind == "objective":
            k = key or "?"
            if k in self._objectives or full or now - self._last_gank < GANK_ANY_GAP_S:
                return False
            self._objectives.add(k)
            self._all_times.append(now)
            return True
        if kind == "team_call":
            k = key or "?"
            if k in self._team or full or now - self._last_gank < GANK_ANY_GAP_S or now - self._last_team < TEAM_GAP_S:
                return False
            self._team.add(k)
            self._last_team = now
            self._all_times.append(now)
            return True
        if kind == "team_brain":
            # team_brain.py: one line per friend per BRAIN_FRIEND_S (gank calls on him count and go
            # first), a team line counts for every friend and keeps the team-call spacing
            k = key or "?"
            who = [_fkey(x) for x in targets if _fkey(x)]
            if not who or full or k in self._team or now - self._last_gank < GANK_ANY_GAP_S:
                return False
            if rule == "jungler" and now - self._last_gank < BRAIN_AFTER_GANK_S:
                return False
            if rule == "objective" and now - self._last_team < TEAM_GAP_S:
                return False
            if any(now - self._friend_t.get(w, -1e9) < BRAIN_FRIEND_S for w in who):
                return False
            for w in who:
                self._friend_t[w] = now
            if rule == "objective":
                self._team.add(k)
                self._last_team = now
            self._all_times.append(now)
            return True
        if kind == "jungler_seen":
            k = key or "?"
            if full or now - self._last_gank < SEEN_AFTER_GANK_S or now - self._seen_lane.get(k, -1e9) < SEEN_SAME_LANE_S:
                return False
            if self._recent(self._seen_times, now) >= SEEN_MAX_PER_MIN:
                return False
            self._seen_lane[k] = now
            self._seen_times.append(now)
            self._all_times.append(now)
            return True
        if kind != "gank":
            return False
        k = key or "?"
        who = str(target or "")
        if now - self._seen.get(k, -1e9) < GANK_SAME_S:
            return False
        if now - self._target_t.get(who, -1e9) < (ALLY_SAME_S if who else GANK_MIN_GAP_S):
            return False
        if now - self._last_gank < GANK_ANY_GAP_S:
            return False
        if self._recent(self._gank_times, now) >= GANK_MAX_PER_MIN:
            return False
        self._seen[k] = now
        self._target_t[who] = now
        if who:
            self._friend_t[_fkey(who)] = now
        self._last_gank = now
        self._gank_times.append(now)
        self._all_times.append(now)
        if len(self._seen) > 64:
            self._seen = {a: b for a, b in self._seen.items() if now - b < GANK_SAME_S}
        return True


def _fkey(x: Any) -> str:
    return "".join(ch for ch in str(x or "").lower() if ch.isalnum())


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
    who: str = ""                           # ally_gank: the friend's champion (diagnostics)
    data: Any = None                        # matchups: the lines of my team's lanes
    check: Callable[[], bool] | None = None  # coach: freshness guard (False = its facts changed: dropped)


def _pc_mic_on() -> bool:
    try:
        from treeaicoach.voice_assistant import PC_MIC_ENABLED

        return bool(PC_MIC_ENABLED)
    except Exception:
        return False


@dataclass
class _Settings:
    enabled: bool = False
    token: str = field(default="", repr=False)
    user_id: str = ""
    neural_voice: str = ""
    neural_rate: str = ""
    volume: int = da.DEFAULT_VOLUME
    only: bool = False              # discord_only: the bot says its calls instead of the PC voice
    objectives: bool = False
    jungler_seen: bool = False
    summary: bool = False
    mute_hotkey: str = ""
    ally_ganks: bool = True         # discord_ally_ganks: the ganks of the friends in the voice channel
    say_name: bool = False          # discord_say_name: "Paul, bot gank"
    team_calls: bool = False        # discord_team_calls: "Ils sont 4 bot : Baron maintenant" (team_calls.py)
    links: tuple = ()               # discord_links as sorted (user id, Riot ID) pairs
    route: str = "both"             # discord_route (+ legacy discord_only): both | discord | pc
    listen: bool = False            # discord_listen: « Hey coach » heard in the voice channel
    friends_matchup: bool = True    # discord_friends_matchup
    owner_only: bool = True        # discord_listen_owner_only
    own_assistant: bool = False     # voice_assistant: the player's own « Hey coach » is heard by his PC
    ranks: bool = True              # discord_ranks: game-start ranks (player_intel.discord_ranks), by champion
    brain: bool = True              # discord_team_brain: personal calls per friend (team_brain.py)
    beeps: bool = True              # beep_on_danger: the danger chime in the channel too (off = words only)

    def wants(self, kind: str) -> bool:
        """The switch of an optional call kind is on (gank / matchup / test: always)."""
        if self.route == "pc" and kind not in ("test", "chime", "ai_answer"):
            return False                   # "Tout sur mon PC": the bot says nothing (but answers its askers)
        if kind in ROUTED_KINDS:
            return self.route == "discord"
        return {"objective": self.objectives, "jungler_seen": self.jungler_seen,
                "summary": self.summary, "ally_gank": self.ally_ganks, "team_call": self.team_calls,
                "matchups": self.friends_matchup, "chime": self.listen, "ai_answer": self.listen,
                "ranks": self.ranks, "team_brain": self.brain}.get(kind, True)

    def key(self) -> tuple:
        """What needs a new gateway session when it changes (the rest is read live)."""
        return (self.enabled, self.token)


class BadToken(Exception):
    """Discord refused the token."""


# ------------------------------------------------------------------------------ discord.py layer
class DiscordPyBackend:
    """The real Discord layer (discord.py 2.7 voice + DAVE). Every method runs on the bot loop."""

    def __init__(self) -> None:
        import discord  # noqa: PLC0415  (slow import: on the bot thread only)

        self._d = discord
        for name in ("discord", "discord.client", "discord.gateway", "discord.voice_state", "discord.http",
                     "discord.voice_client", "discord.player"):
            lg = logging.getLogger(name)
            # the voice connection's own messages go to our log (diagnostic of "never joins"), tokens masked
            lg.setLevel(logging.INFO if name in ("discord.gateway", "discord.voice_state", "discord.voice_client")
                        else logging.WARNING)
            if not any(isinstance(f, _RedactFilter) for f in lg.filters):
                lg.addFilter(_RedactFilter())
        self._intents = discord.Intents.none()
        self._intents.guilds = True
        self._intents.voice_states = True
        self._task: asyncio.Task | None = None
        self.tls = "système"
        self.listener: Callable[[], None] | None = None     # voice / guild change -> the caller refreshes
        self.receiver: Any = None           # discord_listen.Receiver: the voice websocket messages go to it
        self._early_ws: collections.deque = collections.deque(maxlen=64)   # ... kept until it exists
        self._deaf = True                   # self-deafened (nothing received) unless « Hey coach » listens
        self._vc_cls: Any = None
        self.client = self._make_client(certifi_ca=False)

    def _make_client(self, certifi_ca: bool) -> Any:
        """discord.Client with an explicit TLS context (Windows store, or certifi's bundle when the
        store fails: frozen exe / outdated roots) and the system proxy if one is set."""
        import ssl  # noqa: PLC0415
        import urllib.request  # noqa: PLC0415

        kw: dict[str, Any] = {}
        try:
            ctx = ssl.create_default_context()
            if certifi_ca:
                import certifi  # noqa: PLC0415

                ctx = ssl.create_default_context(cafile=certifi.where())
                self.tls = "certifi"
            import aiohttp  # noqa: PLC0415

            kw["connector"] = aiohttp.TCPConnector(ssl=ctx)
        except Exception:
            log.debug("Discord: TLS context", exc_info=True)
        try:
            proxy = urllib.request.getproxies().get("https") or urllib.request.getproxies().get("http")
            if proxy:
                kw["proxy"] = proxy if "://" in proxy else "http://" + proxy
        except Exception:
            pass
        client = self._d.Client(intents=self._intents, max_messages=None, chunk_guilds_at_startup=False, **kw)
        self._hook(client)
        return client

    def _hook(self, client: Any) -> None:
        """Voice and guild events: the launcher's view refreshes at once (no member intent needed)."""
        def changed() -> None:
            fn = self.listener
            if fn is not None:
                try:
                    fn()
                except Exception:
                    pass

        async def on_voice_state_update(_m: Any, _b: Any, _a: Any) -> None:
            changed()

        async def on_guild_join(_g: Any) -> None:
            changed()

        async def on_guild_remove(_g: Any) -> None:
            changed()

        async def on_guild_update(_b: Any, _a: Any) -> None:
            changed()

        async def on_guild_channel_update(_b: Any, _a: Any) -> None:
            changed()

        for fn in (on_voice_state_update, on_guild_join, on_guild_remove, on_guild_update, on_guild_channel_update):
            client.event(fn)

    async def start(self, token: str) -> tuple[str, str | None]:
        """Log in + open the gateway; ``(bot name, application id)``. BadToken if refused."""
        d = self._d
        try:
            try:
                await self.client.login(token)
            except Exception as exc:          # certificate refused by the Windows store: certifi
                if "certificate" not in (type(exc).__name__ + str(exc)).lower() or self.tls == "certifi":
                    raise
                log.warning("Discord: TLS refused with the Windows store, retry with certifi")
                try:
                    await self.client.close()
                except Exception:
                    pass
                self.client = self._make_client(certifi_ca=True)
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

    def describe(self, user_id: str) -> dict:
        """Servers, voice channels, the player in voice, the bot's channel and latency (cache only)."""
        cur = self.current_channel()
        bot_id = getattr(self.client.user, "id", None)
        guilds, user = describe_guilds(self.client.guilds, user_id, bot_id, getattr(cur, "id", None))
        try:
            lat = float(self.client.latency)
            lat_ms = int(round(lat * 1000)) if lat == lat and 0 <= lat < 60 else None
        except Exception:
            lat_ms = None
        chan = None
        if cur is not None:
            chan = {"id": str(cur.id), "name": self.channel_name(cur), "guild": str(getattr(cur.guild, "name", "")),
                    "guild_id": str(getattr(cur.guild, "id", ""))}
        return {"guilds": guilds, "user": user, "channel": chan, "latency_ms": lat_ms,
                "bot_id": str(bot_id or "")}

    async def user_name(self, user_id: str) -> str | None:
        """The name of a Discord user (one REST call): ``""`` if this id does not exist, None if
        Discord did not answer."""
        try:
            u = await asyncio.wait_for(self.client.fetch_user(int(user_id)), USER_LOOKUP_TIMEOUT_S)
            return _member_name(u) or str(getattr(u, "name", "") or "")
        except self._d.NotFound:
            return ""
        except Exception:
            return None

    def get_voice_channel(self, channel_id: str) -> Any:
        """A voice channel of the bot's servers by id (cache), None if gone."""
        try:
            ch = self.client.get_channel(int(channel_id))
        except (TypeError, ValueError):
            return None
        return ch if ch is not None and hasattr(ch, "voice_states") else None

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
        missing = missing_permissions(ch, getattr(getattr(ch, "guild", None), "me", None))
        return missing is None or not missing

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
            await ch.connect(timeout=JOIN_TIMEOUT_S, reconnect=True, self_deaf=self._deaf, cls=self._vc_class())

    def _vc_class(self) -> Any:
        """discord.VoiceClient whose voice websocket messages also go to :attr:`receiver` (SPEAKING:
        which SSRC is which member; discord.py has no receive API of its own)."""
        if self._vc_cls is None:
            from discord.voice_state import VoiceConnectionState  # noqa: PLC0415

            backend = self

            async def hook(_ws: Any, msg: Any) -> None:
                r = backend.receiver
                if r is not None:
                    r.on_ws(msg)
                else:                        # SPEAKING before « Hey coach » listens: kept for the receiver
                    try:
                        if isinstance(msg, dict) and msg.get("op") in (5, 11, 13):
                            backend._early_ws.append(msg)
                    except Exception:
                        pass

            class ListeningVoiceClient(self._d.VoiceClient):  # type: ignore[name-defined,misc]
                def create_connection_state(self) -> Any:
                    return VoiceConnectionState(self, hook=hook)

            self._vc_cls = ListeningVoiceClient
        return self._vc_cls

    async def set_listen(self, on: bool, receiver: Any) -> None:
        """« Hey coach » in the channel: undeafen + read the voice socket (or deafen + stop)."""
        if receiver is not None:
            if self.receiver is not receiver:
                for msg in list(self._early_ws):     # SSRC -> member seen before the receiver existed
                    receiver.on_ws(msg)
                self._early_ws.clear()
            self.receiver = receiver
            receiver.listener.bot_user_id = str(getattr(self.client.user, "id", "") or "")
        vc = self._vc()
        want_deaf = not on
        if vc is None or not vc.is_connected():
            self._deaf = want_deaf
            if receiver is not None:
                receiver.detach()
            return
        if want_deaf != self._deaf:
            await vc.guild.change_voice_state(channel=vc.channel, self_deaf=want_deaf)
            self._deaf = want_deaf
        if receiver is not None:
            if on:
                receiver.attach(vc)
            else:
                receiver.detach()

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

    def stop(self) -> None:
        """Cut the sentence being played (push-to-mute)."""
        vc = self._vc()
        if vc is not None and vc.is_playing():
            vc.stop()

    async def post(self, ch: Any, text: str) -> None:
        """Write ``text`` in the text chat of a voice channel (no mention can ping anyone)."""
        await ch.send(text, allowed_mentions=self._d.AllowedMentions.none())

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


# ------------------------------------------------------------------------------ the caller
class DiscordCaller:
    """The bot of the app (one per process: :func:`caller`). Thread-safe, never raises.

    ``backend_factory()`` builds the Discord layer (``DiscordPyBackend`` by default; tests pass a
    fake), ``speech(text, voice, rate) -> wav bytes`` replaces the synthesis (Edge -> OneCore -> SAPI
    by default); the clips recorded at build time stay the last resort either way (``recorded``)."""

    def __init__(self, *, backend_factory: Callable[[], Any] | None = None,
                 speech: Callable[[str, str, str], bytes | None] | None = None,
                 beep: Callable[[], bytes] | None = None,
                 clock: Callable[[], float] = time.monotonic, recorded: bool = True) -> None:
        self._factory = backend_factory or DiscordPyBackend
        self.voice = da.BotVoice([("custom", speech)] if speech is not None else None, recorded=recorded)
        self._beep = beep or _danger_chime
        self._clips: dict[str, tuple[bytes, str]] = {}   # sentence -> (PCM at unity volume, source)
        self._clips_key: tuple[str, str] | None = None
        self._voice_source = ""             # source of the gank clips ("" = not rendered yet / none)
        self._voice_checked = False         # the gank clips were rendered once (the page can tell "none")
        self._muted = False                 # hotkey_discord_mute: the bot says nothing
        self._hotkeys: Any = None
        self._hotkey_key = ""
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
        self._state = "off"                 # off | no_token | connecting | connected | bad_token | error | unavailable
        self._last_error = ""
        self._view: dict[str, Any] = {}     # backend.describe() + bot name (refreshed on the bot loop)
        self._view_t = 0.0
        self._dirty = False
        self._events: collections.deque[dict] = collections.deque(maxlen=EVENTS_MAX)
        self._user_names: dict[str, str | None] = {}
        self._join_failed: dict[str, float] = {}   # channel id -> when joining it failed (no retry for 30 s)
        self._manual: str | None = None            # channel joined by hand from the launcher (no auto-follow)
        self._left_from: str | None = None         # "Quitter": the player's channel at that moment (no rejoin)
        self._in_game = False
        self._roster: list[dict] = []       # my team in the game (engine, "roster" call): riot, champion, alias...
        self._map_sig: Any = None           # signature of the last voice <-> players matching
        self._map: list[dict] = []          # discord_friends.match() of the voice members
        self.spoken: list[str] = []         # what was said (tests / diagnostics), last 50
        self._playing = False               # the bot is playing a sound in the channel
        self._listener: Any = None          # discord_listen.VoiceListener (« Hey coach » in the channel)
        self._receiver: Any = None          # discord_listen.Receiver (the voice socket reader)
        #: engine hook: ``(who, question)`` asked out loud in the voice channel -> ask the AI
        self.on_voice_question: Callable[[str, str], Any] | None = None
        self.dropped = 0

    # -- public, any thread ---------------------------------------------------------------
    def configure(self, cfg: Any) -> None:
        """Apply the Config fields ``discord_enabled`` / ``discord_token`` / ``discord_user_id``
        (+ the natural voice). Starts / restarts / stops the bot. Never raises."""
        try:
            try:
                vol = max(0, min(100, int(getattr(cfg, "discord_volume", da.DEFAULT_VOLUME))))
            except (TypeError, ValueError):
                vol = da.DEFAULT_VOLUME
            s = _Settings(enabled=bool(getattr(cfg, "discord_enabled", False)),
                          token=clean_token(getattr(cfg, "discord_token", "")),
                          user_id=clean_user_id(getattr(cfg, "discord_user_id", "")),
                          neural_voice=str(getattr(cfg, "neural_voice", "") or ""),
                          neural_rate=str(getattr(cfg, "neural_rate", "") or ""),
                          volume=vol, only=getattr(cfg, "discord_only", False) is True,
                          objectives=getattr(cfg, "discord_objectives", False) is True,
                          jungler_seen=getattr(cfg, "discord_jungler_seen", False) is True,
                          summary=getattr(cfg, "discord_summary", False) is True,
                          mute_hotkey=str(getattr(cfg, "hotkey_discord_mute", "") or "").strip(),
                          ally_ganks=getattr(cfg, "discord_ally_ganks", True) is not False,
                          say_name=getattr(cfg, "discord_say_name", False) is True,
                          team_calls=getattr(cfg, "discord_team_calls", False) is True,
                          ranks=getattr(cfg, "discord_ranks", True) is not False,
                          brain=getattr(cfg, "discord_team_brain", True) is not False,
                          links=tuple(sorted(df.clean_links(getattr(cfg, "discord_links", None) or {}).items())),
                          route=route_of(cfg), listen=getattr(cfg, "discord_listen", False) is True,
                          friends_matchup=getattr(cfg, "discord_friends_matchup", True) is not False,
                          own_assistant=getattr(cfg, "voice_assistant", False) is True and _pc_mic_on(),
                          owner_only=getattr(cfg, "discord_listen_owner_only", True) is not False,
                          beeps=getattr(cfg, "beep_on_danger", True) is not False)
            with self._lock:
                changed = s.key() != self._settings.key()
                if s.user_id != self._settings.user_id:
                    self._dirty = True
                more = (s.objectives and not self._settings.objectives) or \
                    (s.jungler_seen and not self._settings.jungler_seen) or \
                    (s.say_name and not self._settings.say_name)
                self._settings = s
            self._sync_hotkey(s)
            if more and self._state == "connected":       # a call kind switched on: render its clips now
                threading.Thread(target=self.prerender, name="TreeAI-discord-clips", daemon=True).start()
            with self._lock:
                if not changed:
                    self._poke()
                    return
                self._gen += 1
                if not s.enabled:
                    self._status, self._state = ("Bot Discord désactivé.", False), "off"
                elif not s.token:
                    self._status, self._state = ("Colle le token du bot.", False), "no_token"
                else:
                    self._status, self._state = ("Connexion à Discord...", False), "connecting"
                self._view = {}
            if s.enabled and s.token:
                self._ensure_thread()
            self._poke()
        except Exception:
            log.exception("Discord: configure failed")

    def on_call(self, kind: str, text: str = "", info: dict | None = None) -> bool:
        """THE engine hook. ``kind``: ``gank`` (``info["lane"]``: "top" / "mid" / "bot" / "jungle"),
        ``matchup`` (``text``: the heuristic sentence, ``info["ai"]``: optional blocking callable
        returning the AI sentence), and when their switch is on ``objective`` (``info["key"]``: dragon /
        baron / grubs, ``info["spawn"]``), ``jungler_seen`` (``info["lane"]``), ``summary`` (``text``)
        are spoken, filtered; ``game_start`` / ``game_end`` join / leave the voice channel; anything
        else is ignored. Returns True when something was queued. Non-blocking, never raises."""
        try:
            info = info if isinstance(info, dict) else {}
            if kind == "roster":                         # kept even while the bot is off (the page shows it)
                with self._lock:
                    self._roster = [dict(p) for p in (info.get("players") or []) if isinstance(p, dict)][:10]
                    self._map_sig = None
                    names = self._settings.say_name and self._state == "connected"
                    champs = [str(p.get("champion") or "") for p in sorted(
                        self._roster, key=lambda p: (not p.get("me"), bool(p.get("enemy"))))]
                stt_mod = sys.modules.get("treeaicoach.stt_local")
                if stt_mod is not None and any(champs):  # Whisper's prompt: this game's champions first
                    try:
                        stt_mod.transcriber().set_names([c for c in champs if c])
                    except Exception:
                        log.debug("whisper names failed", exc_info=True)
                if names:                                # "Paul, bot gank" ready before the first gank
                    threading.Thread(target=self.render_names, name="TreeAI-discord-names", daemon=True).start()
                return True
            with self._lock:
                s = self._settings
                if not (s.enabled and s.token) or self._stopping:
                    return False
                now = float(self._clock())
                if kind in CONTROL_KINDS:
                    self._in_game = kind == "game_start"
                    if self._in_game:
                        self._filter.reset()
                    else:
                        self._roster, self._map_sig = [], None
                    # what the player asked from the launcher (test, join / leave a channel) is kept;
                    # one join / leave at a time (game_end twice = one leave)
                    self._jobs = collections.deque(j for j in self._jobs if j.kind in USER_JOBS)
                    if self._in_game or not s.listen:     # listening: the bot stays in voice after the game
                        self._jobs.append(_Job("join" if self._in_game else "leave", t=now))
                elif kind in SPEAKING_KINDS and (self._muted or not s.wants(kind)):
                    self.dropped += 1                    # bot muted (hotkey) / this call is switched off
                    return False
                elif kind in ("gank", "ally_gank"):
                    lane = str(info.get("lane") or "").strip().lower()
                    who = str(info.get("ally") or "") if kind == "ally_gank" else ""
                    say = gank_text(lane)
                    if kind == "ally_gank" and not who:
                        say = ""
                    if not say or not self._filter.admit("gank", lane, target=who):
                        self.dropped += 1
                        return False
                    name = df.spoken_name(info.get("name")) if who and s.say_name else ""
                    if name:
                        say = f"{name}, {say[0].lower()}{say[1:]}"
                    self._queue_say(_Job("say", say, now, GANK_STALE_S, "gank", lane=lane, who=who))
                elif kind == "matchup":
                    say = " ".join(str(text or "").split())
                    ai = info.get("ai") if callable(info.get("ai")) else None
                    if not (say or ai) or not self._filter.admit("matchup"):
                        self.dropped += 1
                        return False
                    self._queue_say(_Job("say", say, now, MATCHUP_STALE_S, "matchup", ai=ai,
                                         lane=str(info.get("opp") or "")))
                elif kind == "objective":
                    key = str(info.get("key") or "").strip().lower()
                    say = objective_text(key)
                    spawn = info.get("spawn")
                    try:
                        uid = f"{key}:{int(round(float(spawn)))}"
                    except (TypeError, ValueError):
                        uid = key
                    if not say or not self._filter.admit("objective", uid):
                        self.dropped += 1
                        return False
                    self._queue_say(_Job("say", say, now, OBJECTIVE_STALE_S, "objective"))
                elif kind == "jungler_seen":
                    lane = str(info.get("lane") or "").strip().lower()
                    say = seen_text(lane)
                    busy = any(j.kind == "say" for j in self._jobs)      # lowest priority: never queued behind
                    if not say or busy or not self._filter.admit("jungler_seen", lane):
                        self.dropped += 1
                        return False
                    self._queue_say(_Job("say", say, now, SEEN_STALE_S, "jungler_seen", lane=lane))
                elif kind == "team_call":
                    say = " ".join(str(text or "").split())[:TEAM_MAX_CHARS]
                    if not say or not self._filter.admit("team_call", str(info.get("key") or say)):
                        self.dropped += 1
                        return False
                    self._queue_say(_Job("say", say, now, TEAM_STALE_S, "team_call"))
                elif kind == "team_brain":
                    say = " ".join(str(text or "").split())[:TEAM_MAX_CHARS]
                    rule = str(info.get("rule") or "")
                    targets = tuple(str(x) for x in (info.get("targets") or ()))
                    busy = rule != "objective" and any(j.kind == "say" for j in self._jobs)
                    if not say or busy or not self._filter.admit("team_brain", str(info.get("key") or say),
                                                                 targets=targets, rule=rule):
                        self.dropped += 1
                        return False
                    self._queue_say(_Job("say", say, now, BRAIN_STALE_S, "team_brain",
                                         who=",".join(targets[:2])))
                elif kind == "ranks":
                    # once per game: the message in the voice channel's text chat + a few short lines.
                    # By champion only (player_intel.discord_ranks): nothing of it is ever logged.
                    msg = str(text or "").strip()[:TEXT_MAX_CHARS]
                    lines = [" ".join(str(x).split())[:TEAM_MAX_CHARS] for x in (info.get("lines") or [])][:3]
                    if not msg or not self._filter.admit("ranks"):
                        self.dropped += 1
                        return False
                    self._jobs.append(_Job("post", msg, now, RANKS_STALE_S, "ranks"))
                    for line in (x for x in lines if x):
                        self._queue_say(_Job("say", line, now, RANKS_STALE_S, "ranks"))
                elif kind == "summary":
                    say = " ".join(str(text or "").split())
                    if not say or not self._filter.admit("summary"):
                        self.dropped += 1
                        return False
                    self._queue_say(_Job("say", say, now, SUMMARY_STALE_S, "summary"))
                elif kind in ("coach", "ai_answer"):
                    say = " ".join(str(text or "").split())[:240]
                    if not say:
                        return False
                    chk = info.get("check") if kind == "coach" and callable(info.get("check")) else None
                    self._queue_say(_Job("say", say, now, COACH_STALE_S if kind == "coach" else AI_ANSWER_STALE_S,
                                         kind, check=chk))
                elif kind == "beep":
                    tone = str(info.get("tone") or "gank")
                    self._queue_say(_Job("say", "", now, BEEP_STALE_S, "beep", lane=tone))
                elif kind == "matchups":
                    lines = [dict(x) for x in (info.get("lines") or []) if isinstance(x, dict)][:5]
                    ai = info.get("ai") if callable(info.get("ai")) else None
                    if not lines or not self._filter.admit("matchups"):
                        self.dropped += 1
                        return False
                    self._queue_say(_Job("say", "", now, FRIENDS_STALE_S, "matchups", ai=ai, data=lines))
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
        if len(says) >= QUEUE_MAX:            # full: the least urgent (then the oldest) goes
            worst = min(says, key=lambda j: (PRIORITY.get(j.call_kind, 0), j.t))
            self._jobs.remove(worst)
            self.dropped += 1
        self._jobs.append(job)

    def speaks_for(self, kind: str) -> bool:
        """Route "discord" (« Tout par Discord », or the legacy ``discord_only``): the bot is on, in a
        voice channel, not muted, and says this kind of call (``coach`` = any coach sentence, ``beep``
        = a danger tone), so the PC stays silent for it (engine). Any thread, cheap."""
        with self._lock:
            s = self._settings
            return (s.enabled and bool(s.token) and s.route == "discord" and not self._muted
                    and not self._stopping and self._state == "connected" and bool(self._view.get("channel"))
                    and kind in (SPOKEN_KINDS | ROUTED_KINDS) and s.wants(kind))

    def set_muted(self, muted: bool | None = None) -> bool:
        """Silence the bot (``None`` toggles: the push-to-mute hotkey). Pending calls are dropped and
        the sentence being said is cut. Returns the new state. Never raises."""
        with self._lock:
            self._muted = (not self._muted) if muted is None else bool(muted)
            m = self._muted
            if m:
                self._jobs = collections.deque(j for j in self._jobs if j.kind != "say")
                self._jobs.append(_Job("hush", t=float(self._clock())))
        self._event("Bot coupé (raccourci)" if m else "Bot rétabli (raccourci)")
        log.info("Discord : bot %s", "coupé" if m else "rétabli")
        self._poke()
        return m

    def _sync_hotkey(self, s: _Settings) -> None:
        """The push-to-mute hotkey (``hotkey_discord_mute``, "" = none): its own RegisterHotKey
        listener while the bot is on. Never raises."""
        key = s.mute_hotkey if s.enabled and s.token and not self._stopping else ""
        if key == self._hotkey_key:
            return
        old, self._hotkeys, self._hotkey_key = self._hotkeys, None, key
        if old is not None:
            try:
                old.stop()
            except Exception:
                log.debug("Discord: hotkey stop", exc_info=True)
        if not key or sys.platform != "win32":
            return
        try:
            from treeaicoach.hotkeys import HotkeyListener  # noqa: PLC0415

            hk = HotkeyListener({key: lambda: self.set_muted(None)})
            hk.start()
            self._hotkeys = hk
        except Exception:
            log.warning("Discord : raccourci %s indisponible", key, exc_info=True)

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

    def join(self, channel_id: Any) -> bool:
        """"Rejoindre ce salon" (launcher): the bot joins this voice channel now. Non-blocking."""
        return self._command(_Job("join_ch", str(channel_id or ""), t=float(self._clock())))

    def leave(self) -> bool:
        """"Quitter le salon" (launcher). Non-blocking."""
        return self._command(_Job("leave_now", t=float(self._clock())))

    def say_test(self, kind: str) -> bool:
        """"Dire « Bot gank »" (``kind="gank"``) / "Dire le matchup" (``"matchup"``, an example sentence):
        joins the player's channel if needed and says it (outside the game filter). Non-blocking."""
        if kind == "gank":
            job = _Job("test", "Bot gank", float(self._clock()), TEST_STALE_S, "gank", lane="bot")
        else:
            job = _Job("test", EXAMPLE_MATCHUP, float(self._clock()), TEST_STALE_S, "matchup")
        return self._command(job)

    def _command(self, job: _Job) -> bool:
        with self._lock:
            s = self._settings
            if not (s.enabled and s.token) or self._stopping:
                return False
            self._jobs.append(job)
        self._ensure_thread()
        self._poke()
        return True

    # -- who is who in the voice channel (discord_friends.py) ----------------------------------
    def _voice_members_locked(self) -> list[dict]:
        """The people of the voice channel that matters: the bot's, else the player's (cache)."""
        v = self._view
        cid = (v.get("channel") or {}).get("id") or (v.get("user") or {}).get("channel_id")
        if not cid:
            return []
        for g in v.get("guilds") or []:
            for ch in g.get("voice_channels") or []:
                if str(ch.get("id")) == str(cid):
                    return [dict(o) for o in ch.get("occupants") or []]
        return []

    def _mapping_locked(self) -> list[dict]:
        members = self._voice_members_locked()
        s = self._settings
        sig = (tuple(tuple(sorted(m.items())) for m in members),
               tuple(tuple(sorted((k, str(x)) for k, x in p.items())) for p in self._roster), s.links, s.user_id)
        if sig != self._map_sig:
            self._map_sig = sig
            self._map = df.match(members, self._roster, dict(s.links), s.user_id)
        return self._map

    def voice_map(self) -> list[dict]:
        """Who plays what among the voice members (discord_friends.match entries). Any thread."""
        with self._lock:
            return [dict(e) for e in self._mapping_locked()]

    def ally_targets(self) -> dict[str, str]:
        """``{champion alias: Discord name}`` of the friends of the voice channel who play in my team
        (me excluded): the engine watches their ganks (ally_gank.py). ``{}`` when the bot is off,
        muted or the friends' calls are switched off. Any thread, cheap (cached matching)."""
        try:
            with self._lock:
                s = self._settings
                if not (s.enabled and s.token and s.ally_ganks) or self._muted or self._stopping \
                        or not self._roster:
                    return {}
                out: dict[str, str] = {}
                for e in self._mapping_locked():
                    p = e.get("player")
                    if p and not p.get("me") and not p.get("enemy") and e.get("source") in ("auto", "manuel") \
                            and p.get("alias"):
                        out[str(p["alias"])] = str(e.get("spoken") or e.get("name") or "")
                return out
        except Exception:
            log.debug("Discord: ally targets failed", exc_info=True)
            return {}

    def teammates(self) -> dict[str, str]:
        """``{champion alias: Discord display name}`` of the friends of the voice channel who play in my
        team (me excluded), whatever the call switches; ``{}`` when the bot is off or no one matched.
        In memory only (the post-game recap, ``discord_team_recap``): read it BEFORE ``game_end``,
        which forgets the roster. Any thread, never raises."""
        try:
            with self._lock:
                s = self._settings
                if not (s.enabled and s.token) or self._stopping or not self._roster:
                    return {}
                out: dict[str, str] = {}
                for e in self._mapping_locked():
                    p = e.get("player")
                    if p and not p.get("me") and not p.get("enemy") and e.get("source") in ("auto", "manuel") \
                            and p.get("alias"):
                        out[str(p["alias"])] = str(e.get("name") or e.get("spoken") or "")
                return out
        except Exception:
            log.debug("Discord: teammates failed", exc_info=True)
            return {}

    def post_text(self, text: str) -> bool:
        """Queue a message for the text chat of the voice channel (the bot's, else the player's).
        True when queued. Non-blocking, never raises."""
        try:
            text = str(text or "").strip()[:TEXT_MAX_CHARS]
            if not text:
                return False
            return self._command(_Job("post", text, float(self._clock()), TEXT_STALE_S))
        except Exception:
            log.debug("Discord: post_text failed", exc_info=True)
            return False

    def in_voice(self) -> bool:
        """The bot is in a voice channel right now (last refresh)."""
        with self._lock:
            return bool(self._view.get("channel"))

    def snapshot(self) -> dict:
        """The bot as the launcher shows it (any thread, cheap: a copy under the lock). Keys:
        ``state`` (off / no_token / connecting / connected / bad_token / error / unavailable),
        ``status``, ``ok``, ``error``, ``bot_name``, ``guilds``, ``user``, ``channel`` (where the bot is),
        ``latency_ms``, ``events`` (newest first), ``in_game``, ``age_s``."""
        with self._lock:
            v = dict(self._view)
            return {"state": self._state, "status": self._status[0], "ok": self._status[1],
                    "error": self._last_error if self._state in ("error", "unavailable") else "",
                    "bot_name": str(v.get("bot_name", "")), "guilds": list(v.get("guilds", [])),
                    "user": dict(v.get("user") or {"id": self._settings.user_id, "found": False}),
                    "channel": v.get("channel"), "latency_ms": v.get("latency_ms"),
                    "events": list(reversed(self._events)), "in_game": self._in_game,
                    "user_id": self._settings.user_id, "muted": self._muted,
                    "voice_source": self._voice_source, "voice_checked": self._voice_checked,
                    "voice_failures": dict(self.voice.failures), "volume": self._settings.volume,
                    "voice_map": [dict(e) for e in self._mapping_locked()],
                    "roster": [dict(p) for p in self._roster],
                    "listen": self.listen_view(),
                    "age_s": float(self._clock()) - self._view_t if self._view_t else None}

    def _event(self, text: str, channel: str = "", ok: bool = True) -> None:
        with self._lock:
            self._events.append({"t": time.strftime("%H:%M:%S"), "text": mask_secrets(text)[:140],
                                 "channel": channel, "ok": bool(ok)})

    async def _refresh(self, backend: Any, name: str) -> None:
        """Re-read the bot's servers / channels / the player (cache) for :meth:`snapshot`."""
        describe = getattr(backend, "describe", None)
        if describe is None:
            return
        with self._lock:
            uid = self._settings.user_id
        try:
            v = dict(describe(uid))
        except Exception:
            log.debug("Discord: describe failed", exc_info=True)
            return
        user = dict(v.get("user") or {})
        if uid and not user.get("name"):                 # not in voice: one REST lookup of the name
            with self._lock:
                known = uid in self._user_names
            if not known and callable(getattr(backend, "user_name", None)):
                with self._lock:
                    self._user_names[uid] = None
                try:
                    nm = await backend.user_name(uid)
                except Exception:
                    nm = None
                with self._lock:
                    self._user_names[uid] = nm
            with self._lock:
                nm = self._user_names.get(uid)
            if nm is not None:
                user.update(name=nm, exists=bool(nm))
        v["user"] = user
        v["bot_name"] = name
        with self._lock:
            self._view, self._view_t, self._dirty = v, float(self._clock()), False

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
            self._sync_hotkey(_Settings())
            if self._listener is not None:
                self._listener.stop()
            self._poke()
            if th is not None and th.is_alive() and th is not threading.current_thread():
                th.join(max(0.05, float(timeout)))
        except Exception:
            log.debug("Discord: shutdown failed", exc_info=True)

    # -- internals ------------------------------------------------------------------------
    def _clip(self, text: str, voice: str, rate: str, *, chime: bool = False, fast: bool = False) -> bytes:
        """``text`` as 48 kHz stereo PCM at unity volume (the danger chime first when ``chime``),
        cached per voice. Every sentence comes from :class:`discord_audio.BotVoice` (verified audible:
        an empty synthesis falls through to the next voice, never to the chime alone). ``fast``:
        not cached yet -> the clip recorded at build time at once if there is one (a gank call cannot
        wait for the network), the full chain otherwise. Blocking. ``b""`` when no voice at all."""
        with self._lock:
            chime = bool(chime and self._settings.beeps)      # « bip » off: the words only, here too
        key = ("!" if chime else "") + text
        with self._lock:
            if self._clips_key != (voice, rate):
                self._clips_key, self._clips = (voice, rate), {}
            hit = self._clips.get(key)
        if hit:
            return hit[0]
        wav, src = b"", ""
        if fast:
            rec = da.recorded_wav(text)
            if rec and da.wav_audible(rec):
                wav, src = rec, "clip"
        if not wav:
            wav, src = self.voice.synth(text, voice, rate)
        words = da.speech_pcm(wav) if wav else b""
        if not da.is_audible(words):
            log.warning("Discord : aucune voix pour « %s » (%s)", text,
                        "; ".join(f"{k} : {v}" for k, v in self.voice.failures.items()) or "?")
            return b""
        clip = words
        if chime:
            beep = da.chime_pcm(self._beep() or b"")
            clip = beep + b"\x00" * (da.FRAME_BYTES * 4) + words if beep else words
        with self._lock:
            if self._clips_key == (voice, rate) and not (fast and src == "clip"):
                self._clips[key] = (clip, src)     # a fast stand-in is not cached: the real voice next time
            if text == gank_text("bot") or not self._voice_source:
                self._voice_source = src
        return clip

    def _cached_clip(self, text: str, voice: str, rate: str) -> bytes:
        """A gank sentence with the chime already rendered (``b""`` if not yet)."""
        with self._lock:
            key = ("!" if self._settings.beeps else "") + text
            hit = self._clips.get(key) if self._clips_key == (voice, rate) else None
        return hit[0] if hit else b""

    def _render_quiet(self, text: str, voice: str, rate: str) -> None:
        try:
            self._clip(text, voice, rate, chime=True)
        except Exception:
            log.debug("Discord: name clip failed", exc_info=True)

    def render_names(self) -> int:
        """Render the named gank sentences not cached yet (blocking, daemon thread). Never raises."""
        n = 0
        with self._lock:
            s = self._settings
        for text in self.named_texts():
            if self._cached_clip(text, s.neural_voice, s.neural_rate):
                continue
            try:
                if self._clip(text, s.neural_voice, s.neural_rate, chime=True):
                    n += 1
            except Exception:
                log.debug("Discord: name clip failed", exc_info=True)
        return n

    def named_texts(self) -> list[str]:
        """"Paul, bot gank"... for every friend of the voice channel and lane (``discord_say_name``)."""
        with self._lock:
            s = self._settings
            if not s.say_name:
                return []
            names = [df.spoken_name(e.get("spoken") or e.get("name")) for e in self._mapping_locked()
                     if e.get("player") and not e["player"].get("me") and not e["player"].get("enemy")]
        out = []
        for n in names:
            if n:
                out += [f"{n}, {ln} gank" for ln in _GANK_LANES]
        return out[:24]

    def _gank_clip(self, lane: str, voice: str, rate: str, fast: bool = False) -> bytes:
        """The danger chime then "Top gank" (48 kHz stereo PCM, unity volume). ``b""`` when no voice
        works (the chime is NOT played alone: friends would hear a beep meaning nothing)."""
        text = gank_text(lane)
        return self._clip(text, voice, rate, chime=True, fast=fast) if text else b""

    def prerender(self) -> int:
        """Render the gank clips now (bot start; + objective / jungler-seen sentences when switched on),
        each VERIFIED audible, so a call plays instantly; logs the voice source and puts it in the
        launcher's log. Blocking (run on a daemon thread); returns how many clips are ready. Never raises."""
        with self._lock:
            s = self._settings
        n = 0
        jobs: list[tuple[str, bool]] = [(gank_text(ln), True) for ln in _GANK_LANES]
        if s.objectives:
            jobs += [(objective_text(k), False) for k in OBJECTIVE_KEYS]
        if s.jungler_seen:
            jobs += [(seen_text(ln), False) for ln in _LANES]
        jobs += [(t, True) for t in self.named_texts()]
        for text, chime in jobs:
            try:
                if self._clip(text, s.neural_voice, s.neural_rate, chime=chime):
                    n += 1
            except Exception:
                log.warning("Discord : clip « %s » impossible", text, exc_info=True)
        with self._lock:
            first = not self._voice_checked
            self._voice_checked = True
            src = self._voice_source if n else ""
            if not n:
                self._voice_source = ""
        label = da.SOURCE_LABELS.get(src, "")
        log.info("Discord : %d/%d annonces prêtes, voix %s", n, len(jobs), src or "AUCUNE")
        if first or not n:
            if n:
                self._event(f"Voix du bot : {label} ({n}/{len(jobs)} annonces prêtes)")
            else:
                why = "; ".join(f"{k} : {v}" for k, v in self.voice.failures.items())
                self._event(f"Aucune voix pour le bot ({why or 'cause inconnue'})", ok=False)
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
        """The next job: commands in order; among the sentences, the most urgent first (a gank call
        never waits behind "Dragon dans 30 secondes")."""
        with self._lock:
            if not self._jobs:
                return None
            first = self._jobs[0]
            if first.kind == "say":
                says = [j for j in self._jobs if j.kind == "say"]
                best = max(says, key=lambda j: (PRIORITY.get(j.call_kind, 0), -j.t))
                if PRIORITY.get(best.call_kind, 0) > PRIORITY.get(first.call_kind, 0):
                    self._jobs.remove(best)
                    return best
            return self._jobs.popleft()

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
                log.warning("Discord : module indisponible (%s)", error_text(exc))
                self._set_status("Voix Discord indisponible dans cette version (module manquant).", False)
                with self._lock:
                    self._state, self._last_error = "unavailable", error_text(exc)
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
            with self._lock:
                why = self._last_error or ""
                self._state = "error"
            self._set_status(f"Discord ne répond pas ({why}), nouvel essai dans {int(delay)} s." if why
                             else f"Discord ne répond pas, nouvel essai dans {int(delay)} s.", False)
            self._event(f"Erreur : {why or 'Discord ne répond pas'}", ok=False)
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
        with self._lock:
            self._state = "connecting"
        if hasattr(backend, "listener"):
            def changed() -> None:
                with self._lock:
                    self._dirty = True
                self._poke()
            backend.listener = changed
        try:
            name, _app_id = await backend.start(s.token)
        except BadToken:
            self._set_status("Token invalide : recopie-le depuis le portail Discord (onglet Bot).", False)
            with self._lock:
                self._state = "bad_token"
            self._event("Token refusé par Discord", ok=False)
            return "bad_token"
        except Exception as exc:
            why = error_text(exc)
            log.warning("Discord : connexion impossible (%s)", why)
            with self._lock:
                self._last_error = why
            return "error"
        with self._lock:
            self._state, self._last_error = "connected", ""
        self._event(f"Connecté en tant que {name}")
        self._idle_status(backend, name)
        await self._refresh(backend, name)
        try:
            await self._follow(backend, s, name)    # the player is already in voice: join now
        except Exception as exc:
            log.warning("Discord : suivi du salon vocal (%s)", error_text(exc))
        threading.Thread(target=self.prerender, name="TreeAI-discord-clips", daemon=True).start()
        while True:
            with self._lock:
                if self._stopping or self._gen != gen:
                    return "ok"
                s = self._settings
                due = self._dirty or float(self._clock()) - self._view_t >= SNAPSHOT_EVERY_S
            if not backend.alive():
                with self._lock:
                    self._last_error = "connexion perdue"
                self._event("Connexion à Discord perdue", ok=False)
                return "error"
            if due:
                try:                                 # never ends the session (end of game: members leave,
                    await self._refresh(backend, name)   # channels change, the cache moves under us)
                    try:
                        await self._follow(backend, s, name)
                    except Exception as exc:
                        log.warning("Discord : suivi du salon vocal (%s)", error_text(exc))
                    with self._lock:
                        again = self._dirty
                    if again:                        # joined / left just now: the launcher sees it at once
                        await self._refresh(backend, name)
                    await self._sync_listen(backend, s)
                except Exception:
                    log.warning("Discord : rafraîchissement échoué", exc_info=True)
                    with self._lock:
                        self._view_t, self._dirty = float(self._clock()), False
            job = self._pop()
            if job is None:
                await self._wait(SNAPSHOT_EVERY_S)
                continue
            try:
                await self._handle(backend, job, s, name)
            except Exception as exc:
                why = error_text(exc)
                log.warning("Discord : %s a échoué (%s)", job.kind, why)
                self._event(f"Erreur ({job.kind}) : {why}", ok=False)
                if job.kind == "test":
                    self._done(job, f"Le bot n'a pas pu parler ({why}).", False)
            finally:
                if job.call_kind == "ai_answer":
                    self._listen_answered()
            with self._lock:
                self._dirty = True

    # -- « Hey coach » said in the voice channel (discord_listen.py) ---------------------------
    def _listen_parts(self) -> tuple[Any, Any]:
        if self._listener is None:
            from treeaicoach import discord_listen as dl  # noqa: PLC0415

            lst = dl.VoiceListener()
            lst.should_listen = self._should_listen
            lst.bot_speaking = lambda: self._playing
            lst.on_wake = self._on_wake
            lst.on_question = self._on_question
            lst.on_test = self._on_listen_test
            rec = dl.Receiver(lst)
            rec.candidates = self._voice_member_ids
            self._listener, self._receiver = lst, rec
        return self._listener, self._receiver

    def _voice_member_ids(self) -> list[str]:
        with self._lock:
            return [str(m.get("id") or "") for m in self._voice_members_locked()]

    def _should_listen(self) -> bool:
        with self._lock:
            s = self._settings
            return (s.enabled and s.listen and not self._muted and not self._stopping     # in and out of games
                    and self._state == "connected")

    def listen_test(self) -> tuple[bool, str]:
        """« Tester : dis « Hey coach » » on the Discord page: the bot hears the channel for 30 s even
        outside a game (switch « Écouter » not needed); each member's line shows his level and last
        words; a question heard is written in the log, never sent to the AI. Never raises."""
        try:
            with self._lock:
                connected = self._state == "connected" and not self._stopping
                in_voice = bool(self._view.get("channel"))
            if not connected:
                return False, "Le bot n'est pas connecté."
            if not in_voice:
                return False, "Fais d'abord venir le bot dans ton salon vocal (« Rejoindre ce salon »)."
            lst, _rec = self._listen_parts()
            lst.test()
            with self._lock:
                self._dirty = True
            self._poke()
            return True, "Test : dites « Hey coach » puis une question dans le vocal (30 s)."
        except Exception:
            log.warning("Discord : test d'écoute impossible", exc_info=True)
            return False, "Test d'écoute impossible."

    def _on_listen_test(self, uid: str, text: str) -> None:
        who = self.asker(uid) or "quelqu'un"
        self._event(f"Test écoute : {who} a dit « {text} »" if text else
                    f"Test écoute : « Hey coach » de {who} entendu, question vide", ok=bool(text))

    def listen_view(self) -> dict:
        """The « Écoute » part of the page: per member ``{uid: {level, bar, heard, stage}}``, the chain's
        counters (packets, unmapped, undecryptable…), whether a test runs. Any thread, cheap."""
        lst, rec = self._listener, self._receiver
        if lst is None:
            return {"members": {}, "stats": {}, "testing": False, "status": ""}
        return {"members": lst.member_views(), "stats": rec.stats() if rec is not None else {},
                "testing": lst.testing(), "status": lst.status,       # (no should_listen: under the lock)
                "engine": lst.whisper_status(), "last_engine": lst.last_engine}

    def _on_wake(self, _uid: str) -> None:
        """Wake phrase heard: the soft « je t'écoute » chime in the channel, at once."""
        with self._lock:
            self._queue_say(_Job("say", "", float(self._clock()), CHIME_STALE_S, "chime"))
        self._poke()

    def asker(self, uid: Any) -> str:
        """Who asked, for the AI: « Paul (Darius) », « toi (Garen) », else the Discord name."""
        with self._lock:
            e = next((e for e in self._mapping_locked() if str(e.get("id")) == str(uid)), None)
        if e is None:
            return ""
        p = e.get("player") or {}
        name = "toi" if p.get("me") else (df.spoken_name(e.get("spoken") or e.get("name")) or str(e.get("name") or ""))
        champ = str(p.get("champion") or "")
        return f"{name} ({champ})" if champ else name

    def _on_question(self, uid: str, text: str) -> None:
        fn = self.on_voice_question
        if fn is None:
            self._listen_answered()
            return
        fn(self.asker(uid), text)

    def _listen_answered(self) -> None:
        lst = self._listener
        if lst is not None:
            lst.answered()

    async def _sync_listen(self, backend: Any, s: _Settings) -> None:
        fn = getattr(backend, "set_listen", None)
        if not callable(fn):
            return
        testing = self._listener is not None and self._listener.testing()
        on = (bool(s.listen) or testing) and self._current(backend) is not None
        if not on and self._receiver is None:
            return
        if on:                                   # the PC assistant already hears the player: no double answer
            # the player is always heard on Discord too (his PC mic may be silent); a « Hey coach » heard
            # by both is answered once (voice_assistant.claim_wake)
            self._listen_parts()[0].ignore_ids = frozenset()
            self._listen_parts()[0].owner_id = s.user_id if s.own_assistant else ""
            self._listen_parts()[0].only_id = s.user_id if s.owner_only else ""
        try:
            await fn(on, self._listen_parts()[1] if on else self._receiver)
        except Exception as exc:
            log.warning("Discord : écoute du salon (%s)", error_text(exc))

    def _idle_status(self, backend: Any, name: str) -> None:
        if backend.guild_count() == 0:
            self._set_status(f"Connecté ({name}) mais sur aucun serveur : invite-le avec le lien.", False)
        else:
            self._set_status(f"Connecté ({name}). Il rejoindra ton salon vocal au début de la partie.", True)

    @staticmethod
    def _current(backend: Any) -> Any:
        fn = getattr(backend, "current_channel", None)
        try:
            return fn() if callable(fn) else None
        except Exception:
            return None

    async def _ensure_voice(self, backend: Any, s: _Settings) -> bool:
        cur = self._current(backend)
        with self._lock:
            manual = self._manual
        ch = cur if manual is not None and cur is not None else backend.find_channel(s.user_id)
        if ch is None:
            ch = cur                             # joined by hand from the launcher: stay there
        if ch is None:
            if backend.guild_count() == 0:
                self._set_status("Le bot n'est sur aucun serveur : invite-le avec le lien.", False)
            elif s.user_id:
                self._set_status("Rejoins un salon vocal sur un serveur où le bot est invité.", False)
            else:
                self._set_status("Rejoins un salon vocal, ou indique ton identifiant Discord.", False)
            return False
        return await self._join(backend, ch)

    async def _join(self, backend: Any, ch: Any) -> bool:
        """Join ``ch`` (no-op if already there). False with the exact cause in the status / log."""
        cname = backend.channel_name(ch)
        cur = self._current(backend)
        if cur is not None and _same(cur, ch):
            self._set_status(f"Connecté à #{cname}.", True)
            return True
        if not backend.can_speak(ch):
            self._set_status(f"Le bot n'a pas le droit de parler dans #{cname}.", False)
            self._event(f"Pas le droit de parler dans #{cname}", cname, ok=False)
            return False
        try:
            await asyncio.wait_for(backend.join(ch), JOIN_TIMEOUT_S + 5.0)
        except Exception as exc:
            why = join_error_text(exc)
            log.warning("Discord : impossible de rejoindre le salon vocal (%s ; %s)", why, error_text(exc))
            with self._lock:
                self._join_failed[str(getattr(ch, "id", ch))] = float(self._clock())
            self._set_status(f"Impossible de rejoindre #{cname} : {why}.", False)
            self._event(f"Impossible de rejoindre #{cname} : {why}", cname, ok=False)
            return False
        with self._lock:
            self._join_failed.pop(str(getattr(ch, "id", ch)), None)
            self._dirty = True
        self._event(f"A rejoint #{cname}", cname)
        self._set_status(f"Connecté à #{cname}.", True)
        return True

    async def _follow(self, backend: Any, s: _Settings, name: str) -> None:
        """"Suivre mon salon": as soon as the player is seen in a voice channel (on connect and on
        every voice event) the bot joins it; it leaves when the player leaves voice. Without a user
        id: the only occupied voice channel. A channel joined by hand stays until "Quitter"."""
        try:
            target = backend.find_channel(s.user_id)
        except Exception:
            target = None
        cur = self._current(backend)
        now = float(self._clock())
        with self._lock:
            manual, left_from = self._manual, self._left_from
            tid = str(getattr(target, "id", target)) if target is not None else None
            failed_t = self._join_failed.get(tid or "", -1e9)
            if left_from is not None and tid != left_from:
                self._left_from = left_from = None       # the player moved: follow again
        if manual is not None:
            if cur is not None and str(getattr(cur, "id", cur)) == manual:
                return
            with self._lock:
                self._manual = None
        if target is None:
            if cur is not None and s.user_id and manual is None:
                await backend.leave()
                self._event("Tu as quitté le vocal : le bot quitte aussi", backend.channel_name(cur))
                with self._lock:
                    self._dirty = True
                self._idle_status(backend, name)
            return
        if tid == left_from or (cur is not None and _same(cur, target)) or now - failed_t < 30.0:
            return
        await self._join(backend, target)

    async def _play(self, backend: Any, pcm: bytes, s: _Settings) -> None:
        """Play at the bot's volume; ``_playing`` meanwhile (the listener ignores the channel: the
        bot never wakes itself through a friend's speakers)."""
        self._playing = True
        try:
            await backend.play(da.apply_volume(pcm, s.volume))
        finally:
            self._playing = False

    async def _say_answer(self, backend: Any, job: _Job, text: str, s: _Settings) -> None:
        """The AI answer to a « Hey coach » of the channel: its first sentence is synthesised and played
        at once while the rest is synthesised (3.4.1: the whole answer was synthesised first)."""
        import re  # noqa: PLC0415

        v, r = s.neural_voice, s.neural_rate
        parts = [p for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p]
        first, rest = (parts[0], " ".join(parts[1:])) if len(parts) > 1 else (text, "")
        rest_task = asyncio.ensure_future(self._blocking(self.voice.synth, rest, v, r, timeout=CHAIN_TIMEOUT_S)) \
            if rest else None
        said = []
        try:
            for i, sentence in enumerate((first, rest)):
                if not sentence:
                    break
                if i == 0:
                    wav, _src = await self._blocking(self.voice.synth, sentence, v, r, timeout=CHAIN_TIMEOUT_S)
                else:
                    task, rest_task = rest_task, None
                    try:
                        wav, _src = await task       # type: ignore[misc]
                    except Exception as exc:
                        log.info("Discord : fin de la réponse IA non synthétisée (%s)", type(exc).__name__)
                        break
                pcm = da.speech_pcm(wav) if wav else b""
                if not da.is_audible(pcm):
                    why = "; ".join(f"{k} : {v_}" for k, v_ in self.voice.failures.items())
                    self._event(f"Aucune voix pour « {sentence} »" + (f" ({why})" if why else ""), ok=False)
                    break
                if float(self._clock()) - job.t > job.stale_s or not _job_valid(job):
                    self.dropped += 1
                    break
                if i == 0:
                    try:
                        from treeaicoach import voice_latency  # noqa: PLC0415

                        voice_latency.playing(text)
                    except Exception:
                        pass
                await self._play(backend, pcm, s)
                said.append(sentence)
        finally:
            if rest_task is not None:
                rest_task.cancel()
        if said:
            cur = self._current(backend)
            self._event(f"A dit « {' '.join(said)} »", backend.channel_name(cur) if cur is not None else "")
            with self._lock:
                self.spoken.append(" ".join(said))
                del self.spoken[:-50]

    def _friends_lines(self, job: _Job) -> list[tuple[str, dict]]:
        """(spoken name, lane line) of every friend of the voice channel playing in my team (me
        excluded: my matchup is said apart), in the lines' order, at most FRIENDS_MAX."""
        lines = list(job.data or [])
        with self._lock:
            mapping = [e for e in self._mapping_locked() if e.get("player") and not e["player"].get("me")
                       and not e["player"].get("enemy") and e.get("source") in ("auto", "manuel")]
        out = []
        for ln in lines:
            alias = str(ln.get("alias") or "").lower()
            e = next((e for e in mapping if str(e["player"].get("alias") or "").lower() == alias), None)
            if e is not None and ln.get("opp"):
                out.append((df.spoken_name(e.get("spoken") or e.get("name")) or str(e.get("name") or ""), ln))
        return out[:FRIENDS_MAX]

    async def _say_friends(self, backend: Any, job: _Job, s: _Settings) -> None:
        """Game start: « Paul, Darius contre Garen : favorable jusqu'au 6 » for each friend, ONE AI
        request for all of them (the coach's sentences otherwise), one after the other with a short
        gap; a gank call waiting cuts the rest (it is more urgent)."""
        friends = self._friends_lines(job)
        if not friends or self._current(backend) is None:
            return
        said_ai: dict[str, str] = {}
        if job.ai is not None:
            try:
                ans = await self._blocking(job.ai, [ln for _n, ln in friends], timeout=AI_TIMEOUT_S + 4.0)
                said_ai = parse_friends_answer(ans, [ln.get("champion") for _n, ln in friends])
            except Exception as exc:
                log.info("Discord : phrases IA des matchups indisponibles (%s)", type(exc).__name__)
        v, r = s.neural_voice, s.neural_rate
        for i, (who, ln) in enumerate(friends):
            if float(self._clock()) - job.t > job.stale_s:
                return
            with self._lock:
                urgent = self._muted or any(PRIORITY.get(j.call_kind, 0) >= PRIORITY["team_call"]
                                            for j in self._jobs if j.kind == "say")
            if urgent:
                self.dropped += len(friends) - i
                return
            body = said_ai.get(str(ln.get("champion"))) or str(ln.get("text") or "")
            if not body:
                continue
            text = f"{who}, {body}" if who else body
            wav, _src = await self._blocking(self.voice.synth, text, v, r, timeout=CHAIN_TIMEOUT_S)
            pcm = da.speech_pcm(wav) if wav else b""
            if not da.is_audible(pcm):
                continue
            if i:
                await asyncio.sleep(FRIENDS_GAP_FRAMES * 0.02)
            await self._play(backend, pcm, s)
            self._event(f"A dit « {text} »", backend.channel_name(self._current(backend) or ""))
            with self._lock:
                self.spoken.append(text)
                del self.spoken[:-50]

    async def _handle(self, backend: Any, job: _Job, s: _Settings, name: str) -> None:
        if job.kind == "leave":                  # game end: the bot stays with the player if he is in voice
            try:
                with_player = backend.find_channel(s.user_id) is not None
            except Exception:
                with_player = False
            if with_player:                      # (without a user id too: leaving then re-following
                self._idle_status(backend, name)  # 2 s later flapped, and lost the summary)
                return
        if job.kind in ("leave", "leave_now"):
            cur = self._current(backend)
            if job.kind == "leave_now":
                u = backend.find_channel(s.user_id)
                with self._lock:
                    self._manual = None
                    self._left_from = str(getattr(u, "id", u)) if u is not None else None
            await backend.leave()
            if cur is not None or job.kind == "leave_now":
                self._event("A quitté le salon vocal", backend.channel_name(cur) if cur is not None else "")
            self._idle_status(backend, name)
            return
        if job.kind == "join_ch":
            getter = getattr(backend, "get_voice_channel", None)
            ch = getter(job.text) if callable(getter) else None
            if ch is None:
                self._event("Ce salon vocal n'existe plus", ok=False)
                return
            if await self._join(backend, ch):
                with self._lock:
                    self._manual, self._left_from = str(getattr(ch, "id", ch)), None
            return
        if job.kind == "join":
            await self._ensure_voice(backend, s)
            return
        if job.kind == "post":                   # post-game recap / game-start ranks in the voice channel's text chat
            what = "Rangs de la partie" if job.call_kind == "ranks" else "Bilan d'équipe"
            ch = self._current(backend)
            if ch is None:
                try:
                    ch = backend.find_channel(s.user_id)
                except Exception:
                    ch = None
            poster = getattr(backend, "post", None)
            if ch is None or not callable(poster) or float(self._clock()) - job.t > job.stale_s:
                self._event(f"{what} non envoyé : aucun salon vocal", ok=False)
                return
            await poster(ch, job.text)
            self._event(f"{what} envoyé", backend.channel_name(ch))
            return
        if job.kind == "hush":                   # push-to-mute: cut the sentence being said
            stop = getattr(backend, "stop", None)
            if callable(stop):
                stop()
            return
        # "say" / "test"
        if float(self._clock()) - job.t > job.stale_s:
            self.dropped += 1
            return
        with self._lock:
            if self._muted and job.kind == "say":
                self.dropped += 1
                return
        if job.call_kind in ("beep", "chime"):   # a tone only where the bot already is
            if job.call_kind == "beep" and not s.beeps:
                return                           # « bip » off (beep_on_danger): no tone in the channel
            if self._current(backend) is not None:
                pcm = da.chime_pcm(_tone_wav("info" if job.call_kind == "chime" else (job.lane or "gank")))
                if pcm:
                    await self._play(backend, pcm, s)
            return
        if job.call_kind == "matchups":
            await self._say_friends(backend, job, s)
            return
        was_in_voice = self._current(backend) is not None
        if job.call_kind == "summary" and not was_in_voice:
            return                               # the end-of-game line never pulls the bot into a channel
        if not await self._ensure_voice(backend, s):
            if job.kind == "test":
                self._done(job, self.status()[0], False)
            return
        v, r = s.neural_voice, s.neural_rate
        text = job.text
        if job.call_kind == "gank" and text != gank_text(job.lane):
            pcm = self._cached_clip(text, v, r)     # "Paul, bot gank": rendered beforehand, else plain
            if not pcm:
                pcm = await self._blocking(self._gank_clip, job.lane, v, r, True, timeout=CHAIN_TIMEOUT_S)
                threading.Thread(target=self._render_quiet, args=(text, v, r), name="TreeAI-discord-name",
                                 daemon=True).start()
                text = gank_text(job.lane)
        elif job.call_kind == "gank":
            pcm = await self._blocking(self._gank_clip, job.lane, v, r, True, timeout=CHAIN_TIMEOUT_S)
        elif job.call_kind in ("objective", "jungler_seen"):
            pcm = await self._blocking(self._clip, text, v, r, timeout=CHAIN_TIMEOUT_S)
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
            if job.call_kind == "ai_answer" and job.kind != "test":
                await self._say_answer(backend, job, text, s)
                return
            wav, _src = await self._blocking(self.voice.synth, text, v, r, timeout=CHAIN_TIMEOUT_S)
            pcm = da.speech_pcm(wav) if wav else b""
            if not da.is_audible(pcm):
                pcm = b""
            if job.kind == "test" and job.call_kind == "test":   # the test ends with a real gank call
                gank = await self._blocking(self._gank_clip, "bot", v, r, timeout=CHAIN_TIMEOUT_S)
                pcm = (pcm + b"\x00" * (da.FRAME_BYTES * 15) + gank) if pcm and gank else (pcm or gank)
        if not pcm:
            why = "; ".join(f"{k} : {v_}" for k, v_ in self.voice.failures.items())
            self._event(f"Aucune voix pour « {text} »" + (f" ({why})" if why else ""), ok=False)
            if job.kind == "test":
                self._done(job, "Aucune voix disponible pour le bot (ni naturelle, ni Windows, ni enregistrée).",
                           False)
            return
        if job.kind != "test" and (float(self._clock()) - job.t > job.stale_s or not _job_valid(job)):
            self.dropped += 1
            return
        await self._play(backend, pcm, s)
        cur = self._current(backend)
        self._event(f"A dit « {text} »", backend.channel_name(cur) if cur is not None else "")
        with self._lock:
            self.spoken.append(text)
            del self.spoken[:-50]
        if job.kind == "test":
            msg = self.status()[0]
            self._done(job, f"{msg} Le bot vient de parler.", True)
            with self._lock:
                in_game = self._in_game
            if not in_game and not was_in_voice:
                await backend.leave()
                self._set_status(msg.replace("Connecté à", "Testé dans"), True)


def _job_valid(job: _Job) -> bool:
    """The job's freshness guard (True without one / when it fails)."""
    chk = job.check
    if chk is None:
        return True
    try:
        return bool(chk())
    except Exception:
        return True


def _tone_wav(name: str) -> bytes:
    """One of the app's chimes (gank / recule / siege / info) as WAV bytes."""
    try:
        from treeaicoach import chimes  # noqa: PLC0415

        return chimes.wav_bytes(name if name in ("gank", "recule", "siege", "info") else "gank", 100)
    except Exception:
        return b""


def _same(a: Any, b: Any) -> bool:
    try:
        return a is b or bool(a == b)          # discord.py channels compare by id
    except Exception:
        return False


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
           "application_id_from_token", "invite_url", "discord_available", "SPOKEN_KINDS", "CONTROL_KINDS",
           "describe_guilds", "missing_permissions", "EXAMPLE_MATCHUP",
           "join_error_text", "objective_text", "seen_text", "summary_line", "nearest_lane", "speech_probe"]


def probe(token: str = "") -> str:
    """Packaging check (``TreeAICoach.exe --discord-probe out.txt``): imports, opus, DAVE, client
    creation and one login attempt (a fake token by default: "LoginFailure" = Discord reached)."""
    lines: list[str] = []
    try:
        import discord  # noqa: PLC0415

        lines.append(f"discord.py {discord.__version__}")
        import nacl  # noqa: PLC0415, F401
        import davey  # noqa: PLC0415, F401

        lines.append("nacl + davey: ok")
        try:
            if not discord.opus.is_loaded():
                discord.opus._load_default()
            lines.append(f"opus: {discord.opus.is_loaded()}")
        except Exception as exc:
            lines.append(f"opus: {error_text(exc)}")

        async def _login() -> str:
            b = DiscordPyBackend()
            try:
                await asyncio.wait_for(b.client.login(token or ".".join(["MTIzNDU2Nzg5MDEyMzQ1Njc4OQ", "Gabc",
                                                                           "x" * 27])), 20.0)
                return "login: ok"
            except Exception as exc:
                return f"login: {error_text(exc)} (tls {b.tls})"
            finally:
                await b.client.close()
        lines.append(asyncio.run(_login()))
    except Exception as exc:
        lines.append(f"FAILED: {error_text(exc)}")
    lines += speech_probe()
    return "\n".join(lines) + "\n"


def speech_probe(text: str = "Bot gank") -> list[str]:
    """The bot's exact voice path for ``text`` (packaging check of 2.9.0's "le bot n'arrive pas à
    parler"): each voice of the chain alone (bytes, duration, level or the error), then what the bot
    would play (chime + words at the default volume) and its source. Never raises."""
    out: list[str] = []
    try:
        try:
            from importlib.metadata import version  # noqa: PLC0415

            out.append(f"edge-tts {version('edge-tts')}")
        except Exception as exc:
            out.append(f"edge-tts: {error_text(exc)}")
        voice = da.BotVoice()
        for name, fn in voice.chain():
            t0 = time.monotonic()
            try:
                wav = fn(text, "", "")
                if not wav:
                    out.append(f"voice {name}: no audio ({voice.failures.get(name) or 'none / not on this OS'}, "
                               f"{time.monotonic() - t0:.1f} s)")
                    continue
                pcm = da.to_pcm48(wav)
                st = da.pcm_stats(pcm)
                ok = "ok" if da.is_audible(pcm) else "VIDE"
                out.append(f"voice {name}: {ok}, {len(wav or b'')} bytes, {st['seconds']:.2f} s, "
                           f"{st['rms_db']} dBFS RMS ({time.monotonic() - t0:.1f} s)")
            except Exception as exc:
                out.append(f"voice {name}: FAILED {error_text(exc)} ({time.monotonic() - t0:.1f} s)")
        c = DiscordCaller(backend_factory=lambda: None)
        clip = c._gank_clip("bot", "", "")
        st = da.pcm_stats(da.apply_volume(clip, da.DEFAULT_VOLUME))
        out.append(f"bot clip « {text} »: {len(clip)} bytes, {st['seconds']:.2f} s, {st['rms_db']} dBFS RMS, "
                   f"peak {st['peak_db']} dBFS at volume {da.DEFAULT_VOLUME}, source "
                   f"{c._voice_source or 'NONE'} ({da.SOURCE_LABELS.get(c._voice_source, 'aucune')})")
    except Exception as exc:
        out.append(f"speech FAILED: {error_text(exc)}")
    return out
