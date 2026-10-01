"""ONE presentation router: every coaching message goes through here before reaching the player.

Real feedback: "too many small tips in the small panel that nobody can see; the old system was
good because it prioritised the RIGHT info with an engine deciding whether something goes to a
BANNER, or just in the panel, and whether it's spoken". Every system (gank / personal danger,
macro "coups de génie", tips, objectives, death cause, play badges, AI plan, praise) hands a
:class:`Message` ``{kind, urgency, value, ttl, topic}``; :meth:`Presenter.offer` scores it in its
:class:`Context` (fight, gank, dead, siege, skill level, recently shown topics) and returns exactly
one channel:

* ``BANNER`` - the big top-centre banner / toast (at most ONE on screen, danger and top calls);
* ``PANEL``  - THE single action line of the HUD card;
* ``BADGE``  - the play-rating badge (plays.py / fx_overlay.py);
* ``DROP``   - nothing.

plus a ``voice`` flag (only the voice whitelist of :mod:`treeaicoach.voice_policy`; the
:class:`~treeaicoach.voice_policy.VoiceGate` still has the last word on speech).

Routing table (:data:`ROUTES`, also in docs/ARCHITECTURE.md §20)::

    kind         normal     fight / gank   dead       voice   value
    danger       BANNER     BANNER         BANNER*    yes     1.00   (* siege / ace only)
    retreat      BANNER     BANNER         DROP       yes     0.95
    engage       BANNER     BANNER         DROP       no      0.80
    macro        BANNER**   DROP           PANEL      no      0.70   (** PANEL below the level's banner bar)
    plan         BANNER**   DROP           PANEL      no      0.75   (game goal / lane plan, start of game)
    objective    PANEL      DROP           PANEL      yes***  0.60   (*** <= 20 s and my role plays it)
    death_cause  PANEL      DROP           PANEL      no      0.65
    warning      PANEL      DROP           DROP       no      0.55
    ai           PANEL      DROP           PANEL      no      0.50
    insight      PANEL      DROP           DROP       no      0.40
    tip          PANEL      DROP           DROP       no      0.30
    praise       BADGE      DROP           DROP       no      0.35
    play         BADGE      DROP (gank)    BADGE      no      0.45

Hard caps: the panel shows ONE line and changes at most every :data:`PANEL_MIN_S` (5 s) unless
the urgency rises; a non-danger banner at most every :data:`BANNER_GAP_S` (20 s); a topic is
shown once per :data:`TOPIC_DEDUPE_S` (40 s) unless it is a danger; low-value messages are
dropped below the level's bar (:data:`PANEL_MIN_VALUE`, :data:`BANNER_MIN_VALUE`).

Pure Python, no I/O, never raises from the public API.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

BANNER, PANEL, BADGE, DROP = "banner", "panel", "badge", "drop"
CHANNELS = (BANNER, PANEL, BADGE, DROP)

PANEL_MIN_S = 5.0          # the panel line changes at most this often (unless the urgency rises)
BANNER_GAP_S = 20.0        # at most one non-danger banner per 20 s
TOPIC_DEDUPE_S = 40.0      # one message per topic per 40 s (danger excepted)
DANGER = 3                 # urgency scale: 0 info, 1 notice, 2 warning, 3 danger
BIG_PRAISE = ("multi:", "shutdown:", "solo:", "steal:")   # (= voice_policy.BIG_PRAISE_PREFIXES)


@dataclass(frozen=True)
class Route:
    normal: str
    fight: str
    dead: str
    voice: bool
    value: float


#: THE routing table (see the module docstring).
ROUTES: dict[str, Route] = {
    "danger": Route(BANNER, BANNER, BANNER, True, 1.00),
    "retreat": Route(BANNER, BANNER, DROP, True, 0.95),
    "engage": Route(BANNER, BANNER, DROP, False, 0.80),
    "macro": Route(BANNER, DROP, PANEL, False, 0.70),
    "plan": Route(BANNER, DROP, PANEL, False, 0.75),
    "objective": Route(PANEL, DROP, PANEL, True, 0.60),
    "death_cause": Route(PANEL, DROP, PANEL, False, 0.65),
    "warning": Route(PANEL, DROP, DROP, False, 0.55),
    "ai": Route(PANEL, DROP, PANEL, False, 0.50),
    "insight": Route(PANEL, DROP, DROP, False, 0.40),
    "tip": Route(PANEL, DROP, DROP, False, 0.30),
    "praise": Route(BADGE, DROP, DROP, False, 0.35),
    "play": Route(BADGE, BADGE, BADGE, False, 0.45),
}
#: minimum value for the panel / for a non-danger banner, per player level
PANEL_MIN_VALUE: dict[str, float] = {"debutant": 0.0, "intermediaire": 0.35, "avance": 0.5, "expert": 0.75}
BANNER_MIN_VALUE: dict[str, float] = {"debutant": 0.6, "intermediaire": 0.7, "avance": 0.75, "expert": 0.9}


@dataclass(frozen=True)
class Message:
    kind: str                        # a ROUTES key
    text: str                        # the line itself (panel) / the banner subtitle
    title: str = ""                  # banner caption
    urgency: int = 1                 # 0 info .. 3 danger
    value: float | None = None       # 0..1 (None: the kind's default)
    ttl: float = 10.0                # seconds the line stays valid on the panel
    topic: str | None = None         # dedupe key (None: the text)
    siege: bool = False              # a base siege / ace danger (shown even while dead)


@dataclass(frozen=True)
class Context:
    t: float
    fight: bool = False
    gank: bool = False               # gank / personal danger threat active
    dead: bool = False
    siege: bool = False
    skill: str = "intermediaire"


@dataclass(frozen=True)
class Decision:
    channel: str
    voice: bool = False
    reason: str = ""


@dataclass
class _Panel:
    text: str
    urgency: int
    since: float
    until: float


#: French imperatives (tutoiement) a card instruction may start with ("Recule vers ta tour").
CARD_VERBS = frozenset("""
va recule pousse rentre achète pose frappe joue reste attends défends farme prends aide regroupe
change évite tue retourne suis garde bloque contrôle place utilise vise arrête laisse tiens protège sors
cours fuis prépare lance engage attaque rejoins tourne ramasse récupère monte descends gèle fais ne
regarde surveille mets reviens profite plaque tape nettoie cache harcèle sécurise vole balise
continue termine finis avance repousse punis dépense économise sauve groupe concentre-toi regroupe-toi
enchaîne passe passe-toi téléporte-toi envahis force conteste rapproche-toi
""".split())
_STRIP = " .!"
_COMMON_STARTS = frozenset("le la les leur leurs ton ta tes peu ils il elle tu un une des jungler sbires phase "
                           "ennemis vous votre nous on ta plus encore tour vague".split())


def _first_word(text: str) -> str:
    import re

    return re.split(r"[\s:,!.']", text.strip(), maxsplit=1)[0].lower()


def card_line(text: str | None) -> str | None:
    """THE card wording of an advice line: an instruction, verb first ("Recule vers ta tour : 2
    contre 1"). A "why : what" line is turned around ("Darius est mort : pousse ta vague" ->
    "Pousse ta vague : Darius est mort"); a line that is no instruction at all (a statement, a
    praise, a statistic) gives None: it is not card material. Never raises."""
    try:
        t = " ".join(str(text or "").replace(" — ", " : ").split()).rstrip(_STRIP)
        if not t:
            return None
        if _first_word(t) in CARD_VERBS:
            return t
        head, sep, tail = t.partition(" : ")
        if sep and _first_word(tail) in CARD_VERBS:
            tail = tail.rstrip(_STRIP)
            w0 = _first_word(head)
            why = head[:1].lower() + head[1:] if (w0 in _COMMON_STARTS or head.lower().startswith("l'")) else head
            return f"{tail[:1].upper()}{tail[1:]} : {why}"
        return None
    except Exception:
        return None


_CLAUSE = r"(?:^|[:;,.!·] *|\bpuis )"
_PUSH_RE = None
_RETREAT_RE = None


def line_stance(text: str | None) -> str | None:
    """``"push"`` (pousse / plaque / frappe la tour / attaque / joue agressif...), ``"retreat"``
    (recule / reste près de ta tour / joue prudent / ne t'avance pas...) or None: two lines of
    opposite stances must never follow each other within :data:`CONTRADICTION_S`. Never raises."""
    global _PUSH_RE, _RETREAT_RE
    try:
        import re

        if _PUSH_RE is None:
            _PUSH_RE = re.compile(r"(?i)" + _CLAUSE + r"(pousse|plaque|frappe (la|leur|une|les) tours?|attaque|"
                                  r"engage|vas-y|joue agressif|punis|mets la pression|va taper)\b")
            _RETREAT_RE = re.compile(r"(?i)" + _CLAUSE + r"(recule|reste sous ta tour|reste près de ta tour|"
                                     r"arrête de pousser|ne pousse|fuis|joue prudent|ne (te )?bats pas|"
                                     r"ne t'avance pas)\b")
        t = str(text or "")
        r = bool(_RETREAT_RE.search(t))
        p = bool(_PUSH_RE.search(t))
        if r and not p:
            return "retreat"
        if p and not r:
            return "push"
        return None
    except Exception:
        return None


#: two lines of opposite stances (push / retreat) never within this many seconds
CONTRADICTION_S = 10.0


def message_kind(toast_kind: str, key: str = "") -> str:
    """Router kind of an engine toast ``(kind, key)`` (``key`` prefixes say who sent it)."""
    k = str(key or "")
    for prefix, kind in (("genie", "macro"), ("urgent:genie", "macro"), ("call:retreat", "retreat"),
                         ("call:engage", "engage"), ("call:", "macro"), ("macro", "macro"), ("tip:", "tip"),
                         ("ai", "ai"), ("objective", "objective"), ("death", "death_cause"),
                         ("text:objective", "objective"), ("text:death", "death_cause"),
                         ("text:personal", "danger"), ("plan:", "plan"), ("goal:", "plan")):
        if k.startswith(prefix):
            return kind
    return {"danger": "danger", "warning": "warning", "praise": "praise"}.get(str(toast_kind), "insight")


class Presenter:
    """Stateful router (one per game). Thread-compatible: called from the engine thread only."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._topics: dict[str, float] = {}
        self._last_banner_t = -1e9
        self._banner_ids: dict[Any, bool] = {}
        self._panel: _Panel | None = None
        self.log: list[tuple[float, str, str, str]] = []      # (t, kind, channel, text) for tests / sim

    # ------------------------------------------------------------------ routing
    def offer(self, msg: Message, ctx: Context) -> Decision:
        """Where ``msg`` goes now (records it when shown). Never raises."""
        try:
            d = self._decide(msg, ctx)
            if d.channel != DROP:
                self._topics[msg.topic or msg.text] = ctx.t
                if (d.channel == BANNER and msg.urgency < DANGER) or (d.channel == BADGE and msg.kind == "praise"):
                    self._last_banner_t = ctx.t
                if d.channel == PANEL:
                    self._panel = _Panel(msg.text, int(msg.urgency), ctx.t, ctx.t + max(1.0, float(msg.ttl)))
            self.log.append((ctx.t, msg.kind, d.channel, msg.text))
            if len(self.log) > 2000:
                del self.log[:1000]
            return d
        except Exception:
            log.debug("presenter offer failed", exc_info=True)
            return Decision(DROP, False, "error")

    def _decide(self, msg: Message, ctx: Context) -> Decision:
        route = ROUTES.get(msg.kind, ROUTES["insight"])
        skill = ctx.skill if ctx.skill in PANEL_MIN_VALUE else "intermediaire"
        value = route.value if msg.value is None else float(msg.value)
        danger = msg.urgency >= DANGER or msg.kind in ("danger", "retreat")
        if ctx.dead:
            ch = route.dead
            if msg.kind == "danger" and not msg.siege:
                ch = DROP
        elif ctx.fight or ctx.gank:
            ch = route.fight
            if msg.kind == "play" and ctx.gank:
                ch = DROP
        else:
            ch = route.normal
        if ch == DROP:
            return Decision(DROP, False, "context")
        if ch == BADGE and msg.kind == "praise" and not danger and ctx.t - self._last_banner_t < BANNER_GAP_S \
                and not str(msg.topic or "").startswith(BIG_PRAISE):
            return Decision(DROP, False, "banner-gap")         # a praise toast is a banner too (a solo
            #                                                    kill / multi kill / steal still shows)
        topic = msg.topic or msg.text
        last = self._topics.get(topic)
        if not danger and last is not None and 0.0 <= ctx.t - last < TOPIC_DEDUPE_S:
            return Decision(DROP, False, "topic")
        if ch == BANNER and not danger:
            if value < BANNER_MIN_VALUE[skill]:
                ch = PANEL if route.normal == BANNER and msg.kind in ("macro", "plan") else ch
                if ch == BANNER:
                    return Decision(DROP, False, "value")
            elif ctx.t - self._last_banner_t < BANNER_GAP_S:
                if msg.kind in ("macro", "plan"):
                    ch = PANEL
                else:
                    return Decision(DROP, False, "banner-gap")
        if ch == PANEL:
            if not danger and value < PANEL_MIN_VALUE[skill] and msg.urgency < 2:
                return Decision(DROP, False, "value")
            p = self._panel
            if p is not None and ctx.t < p.until and ctx.t - p.since < PANEL_MIN_S and msg.urgency <= p.urgency:
                return Decision(DROP, False, "panel-busy")
        voice = bool(route.voice) and (danger or msg.kind == "objective")
        return Decision(ch, voice, "ok")

    # ------------------------------------------------------------------ panel / banners
    def panel(self, now: float) -> str | None:
        """The current panel line routed here (None once its ttl is over)."""
        p = self._panel
        return p.text if p is not None and now < p.until else None

    def filter_panel_line(self, line: str | None, tone: str | None, ctx: Context) -> str | None:
        """Last word on the HUD action line built by the engine: during a fight / gank, or dead,
        only a danger / warning toned line stays (low-value tips are dropped entirely)."""
        if not line:
            return None
        t = str(tone or "").lower()
        if (ctx.fight or ctx.gank) and t not in ("danger", "warning"):
            return None
        return line

    def banner_ok(self, style: str, ident: Any, ctx: Context) -> bool:
        """A director banner (tactics.Banner: engage / retreat / call) identified by ``ident``:
        the retreat (danger) always shows; another banner at most once per :data:`BANNER_GAP_S`
        and never while dead. The decision is kept for the banner's whole life."""
        try:
            if ident in self._banner_ids:
                return self._banner_ids[ident]
            if len(self._banner_ids) > 64:
                self._banner_ids.clear()
            kind = "retreat" if style == "retreat" else ("engage" if style == "engage" else "macro")
            d = self.offer(Message(kind, str(ident), urgency=DANGER if kind == "retreat" else 2,
                                   topic=f"banner:{ident}"), ctx)
            ok = d.channel == BANNER
            self._banner_ids[ident] = ok
            return ok
        except Exception:
            return True

    def counts(self) -> dict[str, int]:
        out = {c: 0 for c in CHANNELS}
        for _t, _k, ch, _x in self.log:
            out[ch] = out.get(ch, 0) + 1
        return out


__all__ = ["BANNER", "PANEL", "BADGE", "DROP", "ROUTES", "Route", "Message", "Context", "Decision", "Presenter",
           "message_kind", "card_line", "CARD_VERBS", "line_stance", "CONTRADICTION_S", "PANEL_MIN_S", "BANNER_GAP_S", "TOPIC_DEDUPE_S", "PANEL_MIN_VALUE", "BANNER_MIN_VALUE"]
