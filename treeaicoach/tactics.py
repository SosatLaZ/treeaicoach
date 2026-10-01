"""The "chef d'orchestre": fight calls, game phase, end-game calls, positioning, wards, voice gate.

:class:`TacticalDirector` is the one object the engine talks to for the v3 coaching layer:

* every tick (cheap): :mod:`treeaicoach.phase` map state (cached per poll), the fight tracker
  (:mod:`treeaicoach.fight`: FIGHT / RECULE decision + win chance), the speech context of the
  voice gate (:class:`treeaicoach.voice_policy.VoiceGate`: concentration rules);
* at the coaching rate (``heavy`` ticks): end-game calls (:class:`phase.EndGameCaller`),
  "right place at the right time" (:mod:`treeaicoach.positioning`) and ward spots
  (:mod:`treeaicoach.wards`);
* gank triage (:func:`voice_policy.triage_gank`): grouped / screened / opportunity;
* VISUALS first: minimap guides (:class:`MapGuide`: retreat / objective / regroup arrows, ward
  spots; at most :data:`MAX_GUIDES`, priority ranked) and the big banner (live fight banner
  "FIGHT 70 %" / "RECULE 30 %", or a short call banner "BARON !").

Everything returns plain data; the engine routes the alerts (voice gate -> voice or written),
pushes the toasts and puts guides / banner in the overlay state. Thread-safe, never raises.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass, field
from typing import Any

from treeaicoach import geometry
from treeaicoach.alerts import Alert, AlertKind, Level

log = logging.getLogger(__name__)

MAX_GUIDES = 6
CALL_BANNER_S = 2.2            # short call banner ("BARON !") duration
GUIDE_S = {"objective": 14.0, "retreat": 6.0, "group": 12.0, "lane": 10.0, "alone": 10.0, "push": 10.0,
           "call": 12.0}
CONCENTRATION_R = 0.10         # enemies this close to me = I am busy (no chatter)
WRITTEN_GANK_GAP_S = 12.0      # a gank alert turned into text is written at most this often
HOLD_PRAISE_S = 45.0           # praise held during a fight is released after it (if fresh)
PRIORITY = {"retreat": 100, "alone": 95, "call": 90, "objective": 80, "push": 75, "group": 60, "lane": 50,
            "ward": 40}


@dataclass(frozen=True)
class MapGuide:
    """One visual guide on the minimap layer."""

    kind: str                                   # "retreat" | "objective" | "group" | "lane" | "push" | "ward" ...
    uv: tuple[float, float]                     # target / ward spot
    label: str = ""
    priority: int = 50
    arrow: bool = True                          # arrow from me to uv (False: icon only, e.g. ward)
    color: str = "gold"                         # "gold" | "danger" | "safe" | "teal"
    until: float = 0.0
    since: float = 0.0


@dataclass(frozen=True)
class Banner:
    """Big top-centre banner (rendered by :mod:`treeaicoach.toasts`)."""

    style: str                                  # "engage" | "retreat" | "call"
    title: str                                  # "FIGHT 70 %", "RECULE 30 %", "BARON !"
    subtitle: str = ""
    since: float = 0.0
    until: float = math.inf
    pct: int | None = None


@dataclass
class TickOut:
    alerts: list = field(default_factory=list)          # Alerts to route (gate decides voice / text)
    toasts: list = field(default_factory=list)          # (kind, title, subtitle, key)
    in_fight: bool = False


def _f(x: Any, default: float | None = None) -> float | None:
    if x is None or isinstance(x, bool):
        return default
    try:
        v = float(x)
    except (TypeError, ValueError, OverflowError):
        return default
    return v if math.isfinite(v) else default


class TacticalDirector:
    """See the module docstring. One per engine; ``reset()`` on a new game."""

    def __init__(self, cfg: Any = None) -> None:
        from treeaicoach.fight import FightTracker
        from treeaicoach.phase import EndGameCaller
        from treeaicoach.positioning import PositionCoach
        from treeaicoach.voice_policy import SpeechContext, VoiceGate
        from treeaicoach.wards import WardAdvisor

        self._lock = threading.RLock()
        self.cfg = cfg
        self.fight = FightTracker()
        self.calls = EndGameCaller()
        self.position = PositionCoach()
        self.wards = WardAdvisor()
        self.gate = VoiceGate()
        self._ctx_cls = SpeechContext
        self.reset()

    def apply_config(self, cfg: Any) -> None:
        self.cfg = cfg

    def reset(self) -> None:
        with self._lock:
            for c in (self.fight, self.calls, self.position, self.wards, self.gate):
                try:
                    c.reset()
                except Exception:
                    log.exception("reset failed for %r", type(c).__name__)
            self._map: Any = None
            self._map_key: tuple | None = None
            self._guides: list[MapGuide] = []
            self._banner: Banner | None = None
            self._held: list[tuple[float, Alert]] = []
            self._ctx = self._ctx_cls()
            self._seen: tuple = ((), (), None)
            self._written_t: dict[str, float] = {}

    # ------------------------------------------------------------------ public state
    def speech_context(self) -> Any:
        with self._lock:
            return self._ctx

    def map_state(self) -> Any:
        with self._lock:
            return self._map

    def in_fight(self) -> bool:
        return self.fight.in_fight()

    def phase(self) -> str | None:
        st = self.map_state()
        return getattr(st, "phase", None) if st is not None else None

    def guides(self, now: float) -> list[MapGuide]:
        """Active minimap guides, highest priority first, at most :data:`MAX_GUIDES`."""
        with self._lock:
            self._guides = [g for g in self._guides if now < g.until]
            out = sorted(self._guides, key=lambda g: -g.priority)
        fs = self.fight.state()
        if fs.active and fs.call == "retreat" and fs.safe_uv is not None:
            out.insert(0, MapGuide("retreat", fs.safe_uv, "REPLI", PRIORITY["retreat"], True, "danger", now + 1.0))
        try:
            adv = self.wards.current(now)
            if adv is not None and not fs.active:
                for p in adv.picks:
                    out.append(MapGuide("ward", p.uv, p.label, PRIORITY["ward"], False, "gold", adv.until))
        except Exception:
            pass
        return out[:MAX_GUIDES]

    def banner(self, now: float) -> Banner | None:
        """The big banner to show now (live fight decision first)."""
        fs = self.fight.state()
        if fs.active:
            style = {"engage": "engage", "retreat": "retreat"}.get(fs.call or "", "call")
            return Banner(style, fs.banner, fs.reason, fs.since or now, math.inf, fs.win_pct)
        with self._lock:
            b = self._banner
            if b is not None and now < b.until:
                return b
            self._banner = None
        return None

    # ------------------------------------------------------------------ tick
    def tick(self, t: float, gt: float, game: Any, tracker: Any, *, heavy: bool = True, scoreboard: Any = None,
             roles: Any = None, objectives: Any = None, danger_radius: float = 0.12,
             stance: Any = None) -> TickOut:
        out = TickOut()
        try:
            with self._lock:
                self._tick(out, float(t), float(gt), game, tracker, heavy, scoreboard, roles,
                           list(objectives or []), float(danger_radius), stance)
        except Exception:
            log.exception("TacticalDirector.tick failed")
        return out

    def _role(self, roles: Any, game: Any) -> str | None:
        try:
            r = roles.my_role() if roles is not None and hasattr(roles, "my_role") else None
        except Exception:
            r = None
        me = getattr(game, "me", None)
        return (r or str(getattr(me, "position", "") or "").upper() or None)

    def _lane_opponents(self, roles: Any) -> tuple:
        try:
            return tuple(roles.lane_opponents() or ()) if roles is not None and hasattr(roles, "lane_opponents") else ()
        except Exception:
            return ()

    def _tick(self, out: TickOut, t: float, gt: float, game: Any, tracker: Any, heavy: bool, scoreboard: Any,
              roles: Any, objectives: list[Any], danger_r: float, stance: Any) -> None:
        from treeaicoach import phase as ph
        from treeaicoach.fight import CALL_WORD, my_hp, snapshot

        me = getattr(game, "me", None)
        if me is None:
            return
        role = self._role(roles, game)
        key = (len(getattr(game, "events", None) or []), int(gt), id(game))
        if key != self._map_key:
            self._map = ph.map_state(game, gt, role)
            self._map_key = key
        st = self._map
        me_uv, allies, enemies = snapshot(tracker, t) if tracker is not None else (None, [], [])
        self._seen = (allies, enemies, me_uv)
        up = self.fight.update(t, game, me_uv, allies, enemies, scoreboard=scoreboard, map_state=st,
                               lane_opponents=self._lane_opponents(roles), gt=gt)
        fs = up.state
        out.in_fight = fs.active
        # ---- speech context (concentration rules of the voice gate)
        in_base = False
        if me_uv is not None:
            z = geometry.classify_zone(*me_uv)
            in_base = geometry.is_base(z) and geometry.zone_owner(z) == geometry.normalize_team(getattr(me, "team", None))
        # concentration: enemies really on me (my lane opponents next to me are just laning,
        # unless I am low)
        hp = my_hp(game)
        lane_opps = {str(a).lower() for a in self._lane_opponents(roles)}
        laning = hp is None or hp >= 0.5
        near = [e for e in enemies if e.visible and e.uv is not None and me_uv is not None
                and geometry.dist(e.uv, me_uv) < CONCENTRATION_R
                and not (laning and str(e.alias or "").lower() in lane_opps)]
        danger = any(geometry.dist(e.uv, me_uv) < min(danger_r, CONCENTRATION_R) for e in near)
        self._ctx = self._ctx_cls(in_fight=fs.active, hp=hp, enemies_near=len(near), enemy_in_danger=danger,
                                  dead=bool(getattr(me, "is_dead", False)), in_base=in_base)
        # ---- fight call / end
        if up.new_call is not None:
            out.alerts.append(Alert(kind=AlertKind.MACRO_TIP, level=Level.WARNING, text=CALL_WORD[up.new_call],
                                    key=f"call:{up.new_call}", t=t))
        if up.ended_summary:
            if up.won:
                out.alerts.append(Alert(kind=AlertKind.PRAISE, level=Level.INFO, text=up.ended_summary,
                                        key=f"fight_won:{int(gt)}", t=t))
                out.toasts.append(("praise", "COMBAT GAGNÉ", up.ended_summary, f"fight:{int(gt)}"))
            else:
                out.alerts.append(Alert(kind=AlertKind.MACRO_TIP, level=Level.INFO, text=up.ended_summary,
                                        key=f"macro_tip:fight_end:{int(gt)}", t=t))
        if not fs.active and self._held:                     # praise held during the fight
            fresh = [a for t0, a in self._held if t - t0 <= HOLD_PRAISE_S]
            self._held = []
            out.alerts.extend(fresh[:2])
        if not heavy:
            return
        # ---- end-game calls
        calls = self.calls.update(t, st, objectives)
        for c in calls:
            prefix = "urgent:" if c.speak else "macro_tip:"
            out.alerts.append(Alert(kind=AlertKind.MACRO_TIP, level=Level.INFO, text=c.text, key=prefix + c.key, t=t))
            self._banner = Banner({"engage": "engage", "retreat": "retreat"}.get(c.color, "call"), c.title,
                                  c.text, t, t + CALL_BANNER_S + (1.0 if c.speak else 0.0))
            if c.target is not None:
                self._add_guide(MapGuide("call", c.target, c.title, PRIORITY["call"], True,
                                         {"engage": "safe", "retreat": "danger"}.get(c.color, "gold"),
                                         t + GUIDE_S["call"], t))
        # ---- positioning ("right place at the right time")
        adv = self.position.update(t, game, st, role=role, me_pos=me_uv, allies=allies, enemies=enemies,
                                   objectives=objectives, in_base=in_base, quiet=fs.active)
        if adv is not None:
            prefix = "urgent:" if adv.speak else "macro_tip:"
            out.alerts.append(Alert(kind=AlertKind.MACRO_TIP, level=Level.INFO, text=adv.text, key=prefix + adv.key, t=t))
            if adv.kind in ("alone", "objective"):
                self._banner = Banner("retreat" if adv.kind == "alone" else "call", adv.title, adv.text, t,
                                      t + CALL_BANNER_S + 0.8)
            if adv.target is not None:
                color = {"alone": "danger", "objective": "gold", "group": "teal", "lane": "teal"}.get(adv.kind, "gold")
                self._add_guide(MapGuide(adv.kind, adv.target, adv.title, PRIORITY.get(adv.kind, 50), True, color,
                                         t + GUIDE_S.get(adv.kind, 10.0), t))
        for k, text in self.position.pop_praise():
            a = Alert(kind=AlertKind.PRAISE, level=Level.INFO, text=text, key=k, t=t)
            if fs.active:
                self._held.append((t, a))
            else:
                out.alerts.append(a)
                out.toasts.append(("praise", "BIEN PLACÉ", text, k))
        # ---- wards
        jside = None
        try:
            jg = roles.enemy_jungler() if roles is not None and hasattr(roles, "enemy_jungler") else None
            if not jg:
                p = game.enemy_jungler() if hasattr(game, "enemy_jungler") else None
                jg = getattr(p, "champion_alias", None)
            for e in enemies:
                if jg and str(e.alias or "").lower() == str(jg).lower() and e.uv is not None and e.hidden_s < 40:
                    jside = geometry.side_of(*e.uv)
        except Exception:
            jside = None
        ahead = _f(getattr(stance, "score", None), 0.0) or 0.0
        wa = self.wards.update(t, game, me_pos=me_uv, in_base=in_base, role=role, phase=getattr(st, "phase", "laning"),
                               objectives=objectives, jungler_side=jside, ahead=ahead,
                               quiet=fs.active or self._ctx.concentrating)
        if wa is not None and wa.text:
            out.alerts.append(Alert(kind=AlertKind.CONTROL_WARD, level=Level.INFO, text=wa.text, key=wa.key, t=t))

    def hold_if_fighting(self, alerts: list[Alert], t: float) -> list[Alert]:
        """During a fight: only the fight call passes; praise is held for after, the rest dropped."""
        if not self.fight.in_fight():
            return alerts
        keep = []
        with self._lock:
            for a in alerts:
                key = str(getattr(a, "key", "") or "")
                if key.startswith("call:"):
                    keep.append(a)
                elif getattr(a, "kind", None) == AlertKind.PRAISE:
                    self._held.append((t, a))
                    del self._held[:-4]
        return keep

    def _add_guide(self, g: MapGuide) -> None:
        self._guides = [x for x in self._guides if x.kind != g.kind] + [g]

    # ------------------------------------------------------------------ ganks
    def triage_ganks(self, alerts: list[Alert], game: Any, scoreboard: Any = None) -> tuple[list[Alert], list[Alert]]:
        """``(to_route, written)``: gank alerts still worth the voice gate, and the ones turned into
        written lines (grouped / screened -> unchanged text; opportunity -> new text). Dropped
        ones (fight, dead, base) are in neither. Never raises."""
        from treeaicoach.voice_policy import triage_gank

        keep: list[Alert] = []
        written: list[Alert] = []
        with self._lock:
            allies, enemies, me_uv = self._seen
            ctx = self._ctx
        for a in alerts:
            try:
                name = None
                p = game.player_by_alias(a.alias) if a.alias and hasattr(game, "player_by_alias") else None
                if p is not None:
                    name = p.champion_name
                dec, text = triage_gank(a, me_pos=me_uv, allies=list(allies), enemies=list(enemies), game=game,
                                        in_fight=ctx.in_fight, in_base=ctx.in_base, scoreboard=scoreboard, name=name)
            except Exception:
                dec, text = "speak", None
            if dec == "speak":
                keep.append(a)
            elif dec == "text":
                k = f"{a.kind}:{a.alias}"
                with self._lock:
                    last = self._written_t.get(k)
                    if last is None or a.t - last >= WRITTEN_GANK_GAP_S:
                        self._written_t[k] = a.t
                        written.append(a)
            elif dec == "opportunity":
                written.append(Alert(kind=AlertKind.MACRO_TIP, level=Level.INFO, text=text or a.text,
                                     key=f"macro_tip:opportunity:{a.alias}", t=a.t, alias=a.alias))
        return keep, written


__all__ = ["TacticalDirector", "MapGuide", "Banner", "TickOut", "MAX_GUIDES"]
