"""Coaching stages of a tick: director / macro / wards, speech, scoreboard, items, hype / AI,
play ratings, stance and tips, and the one-line HUD router (``_hud_line``) + toasts.

Mixin of :class:`treeaicoach.engine.CoachEngine` (split out of ``engine.py`` without any
behaviour change): the methods use the engine's state (``self._lock``, ``self._cfg`` ...),
created in ``CoachEngine.__init__``. Not meant to be used on its own.
"""

from __future__ import annotations

import dataclasses
import logging
import math
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from treeaicoach import geometry
from treeaicoach.alerts import Alert, AlertKind, Level, make_alert
from treeaicoach.engine_base import (
    DEAD_TEXT_KINDS,
    EARLY_ADVICE_GT_S,
    GANK_KINDS,
    GO_WORDS,
    HUD_DWELL_S,
    HUD_GAP_S,
    HUD_LINGER_S,
    HUD_RETIRE_S,
    ON_SCREEN_MARGIN,
    TEXT_MSG_S,
    THREAT_HOLD_S,
    TIP_TOAST_GAP_S,
    _line_shape,
)
from treeaicoach.fmtutil import finite_loose as _finite
from treeaicoach.live_client import GameInfo

log = logging.getLogger("treeaicoach.engine")   # same logger as before the split

def _same_call(line: str | None, call: Any) -> bool:
    """The card line IS this planner call (its card wording: presenter.card_line)."""
    try:
        from treeaicoach.presenter import card_line

        text = str(getattr(call, "text", "") or "")
        return bool(line) and (line == text or line == card_line(text))
    except Exception:
        return False


def _digits(a: Any) -> str:
    """A line without its live numbers."""
    return __import__("re").sub(r"\d+([,.]\d+)?", "#", str(a))


def _ticking(a: str, b: str) -> bool:
    """Same line but its live numbers ("Dragon dans 0:45" / "Dragon dans 0:44")."""
    return _digits(a) == _digits(b)


#: a statistic ("5,5 par minute, vise 7", "4,6 sbires/min"): never the card's instruction
STAT_LINE_RE = __import__("re").compile(r"(?i)(\d+([,.]\d+)? (sbires/min|par minute)|score de vision|/min, vise)")
#: a line with a live countdown ("Dragon dans 0:45", "Baron dans 20 s")
COUNTDOWN_RE = __import__("re").compile(r"(?i)\bdans (\d+:\d\d|\d+ s)\b")
#: a card line about wards (one ward call at a time: CoachingMixin.WARD_TOPIC_S)
WARD_LINE_RE = __import__("re").compile(r"(?i)^(balise|pose (ta|une) balise|garde (le buisson|une balise)|"
                                        r"surveille ta rivière|va baliser|achète une balise)")


#: A written toast with the same text within this many seconds is not shown again.
TEXT_TOAST_DEDUP_S = 5.0


class CoachingMixin:
    """Coaching stages of a tick: director / macro / wards, speech, scoreboard, items, hype / AI,"""

    def _tactics_tick(self, t: float, gt: float, game: GameInfo, tracker: Any,
                      gank_alerts: list[Alert], threat: int = 0) -> tuple[list[Alert], list[Alert]]:
        """v3 director (tactics.py): ``(alerts to route, gank alerts + fight call for the fast path)``.
        Gank alerts not worth the voice (grouped, screened, fight...) are written or dropped here."""
        gank = [a for a in gank_alerts if a.kind in GANK_KINDS]
        tac = self._tactics
        if tac is None:
            return [], gank
        try:
            heavy = "tactics" in self._heavy_now
            out = tac.tick(t, gt, game, tracker, heavy=heavy, scoreboard=self.scoreboard_summary(),
                           roles=self._role_resolver,
                           objectives=self._objectives.states() if self._objectives is not None else [],
                           danger_radius=self._cfg.effective_danger_radius(),
                           stance=self._stance.current() if self._stance is not None else None,
                           waves=self._macro_waves(), jungle_intel=self.jungle_intel(), threat=threat,
                           card_age=self._card_age(t), recent_stances=self._recent_stances(t))
            for kind, title, sub, key in out.toasts:
                self._toast(kind, title, sub, None, key, t)
            self._macro_show(out, t, gt)
            calls = [a for a in out.alerts if str(a.key).startswith("call:")]
            keep, written = tac.triage_ganks(gank, game, self.scoreboard_summary())
            for a in written:
                self._write_text(a, t, gt)
            return [a for a in out.alerts if a not in calls], calls + keep
        except Exception:
            self._errors += 1
            self._err.exception("Tactical director failed")
            return [], gank

    def _recent_stances(self, t: float) -> frozenset:
        """Push / retreat stances shown on the card in the last presenter.CONTRADICTION_S seconds."""
        try:
            from treeaicoach.presenter import CONTRADICTION_S

            # lines that APPEARED in the last 10 s (a retreat line still shown 30 s later is no reason
            # to hold "Plaque la tour : Darius est mort"), + "retreat" after an alarm
            starts = getattr(self, "_stance_starts", None) or []
            out = {s_ for t_, s_ in starts if 0.0 <= t - t_ <= CONTRADICTION_S}
            if 0.0 <= t - getattr(self, "_hud_alarm_t", -1e9) <= CONTRADICTION_S:
                out.add("retreat")
            return frozenset(out)
        except Exception:
            return frozenset()

    def _card_age(self, t: float) -> float | None:
        """Seconds since the HUD card line last changed (a new call waits for it to be read)."""
        shown = getattr(self, "_hud_shown", None)
        if shown is None:
            return None
        try:
            return max(0.0, float(t) - float(shown[1]))
        except (TypeError, ValueError):
            return None

    # ================================================================== COUPS DE GÉNIE (macro.py)
    def _macro_waves(self) -> dict:
        """Per-lane wave state of the coach (waves.py, as dicts) for the macro planner."""
        try:
            return dict(self._coach.waves() or {}) if self._coach is not None else {}
        except Exception:
            return {}

    def _macro_show(self, out: Any, t: float, gt: float) -> None:
        """A new macro call: HUD line (held while active) + "COUP DE GÉNIE" badge (plays fx) +
        record; a cancelled one leaves the HUD line. The arrow / banner are the director's."""
        c = getattr(out, "macro_cancelled", None)
        if c is not None:
            msg = self._text_msg
            if msg is not None and msg[1] == c.text:
                self._text_msg = None
        c = getattr(out, "macro_new", None)
        if c is None:
            return
        self._text_msg, self._text_kind = (t, c.text), "genie"
        self._topic_seen(f"genie:{c.kind}", t)          # the call owns its topic: no toast repeats it
        self.text_messages.append((t, "genie", f"{c.text} ({c.why})"))
        del self.text_messages[:-100]
        self.macro_calls = (getattr(self, "macro_calls", []) + [(gt, c)])[-50:]
        mm = getattr(self, "_mastermind", None)
        if mm is not None and c.kind == "gc_window":
            mm.note_call(gt, c.text)                      # post-game: the windows we told the player
        rec = self._recorder
        if rec is not None:
            try:
                rec.on_alert(make_alert(AlertKind.MACRO_TIP, Level.INFO, t, text=c.text,
                                        key=f"macro:genie:{c.kind}"), gt)
            except Exception:
                log.debug("macro call record failed", exc_info=True)
        if c.genius:
            self._push_fx(SimpleNamespace(cls="brilliant", rule=f"genie:{c.kind}", reason=c.text, t=t, gt=gt,
                                          key=f"genie:{c.ident}", alias=None, size="big", title="COUP DE GÉNIE"))

    def _push_fx(self, play: Any) -> None:
        """Animate one badge (fx_overlay.PlayFx, Windows only; recorded in ``fx_pushed`` elsewhere)."""
        try:
            self.fx_pushed = (getattr(self, "fx_pushed", []) + [play])[-50:]
            fx = getattr(self, "_play_fx", None)
            if fx is None and sys.platform == "win32" and self._running:
                from treeaicoach.fx_overlay import PlayFx

                fx = self._play_fx = PlayFx(self._cfg, self._screen_rects,
                                            lambda: self._overlay_visible and self._in_game)
            if fx is not None:
                fx.apply_config(self._cfg)
                fx.push(play)
        except Exception:
            self._err.exception("Badge animation failed")

    # ================================================================== ward guide
    def request_ward_guide(self) -> bool:
        """Hotkey (``cfg.hotkey_ward``, F7): show the best ward spots now (minimap + game view).
        Visual only. Never raises."""
        try:
            wg = self._ward_guide
            return bool(wg is not None and self._in_game and wg.request(self._clock()))
        except Exception:
            log.exception("request_ward_guide failed")
            return False

    def _ward_recommend(self, game: Any, tracker: Any) -> list[Any]:
        """Best 1-2 ward spots right now (hotkey), from wards.recommend. Never raises."""
        try:
            from treeaicoach import wards

            me = tracker.me() if tracker is not None else None
            me_pos = me.position() if me is not None else None
            role = None
            res = self._role_resolver
            if res is not None and hasattr(res, "my_role"):
                role = res.my_role()
            role = role or (str(getattr(game.me, "position", "") or "").upper() if game.me is not None else None)
            obj = None
            for o in (self._objectives.states() if self._objectives is not None else []):
                rem = _finite(getattr(o, "remaining", None))
                key = str(getattr(o, "key", "") or "")
                if key in wards.OBJ_PIT and (getattr(o, "alive", False) or (rem is not None and rem <= 90.0)):
                    r = 0.0 if getattr(o, "alive", False) or rem is None else float(rem)
                    if obj is None or r < obj[1]:
                        obj = (key, r)
            tac = self._tactics
            phase = (tac.phase() if tac is not None else None) or "laning"
            st = self._stance.current() if self._stance is not None else None
            ahead = _finite(getattr(st, "score", None)) or 0.0
            return list(wards.recommend(game.my_team, role, phase=phase, me_pos=me_pos, objective=obj,
                                        ahead=ahead, n=2))
        except Exception:
            self._err.exception("ward recommend failed")
            return []

    def _ward_guide_tick(self, t: float, game: Any, tracker: Any, frame: Any, identified: list[Any]) -> None:
        """Feed the ward guide: the director's ward advice / the hotkey -> guides; camera rectangle and
        ward-placed detection on the minimap frame while a guide is active. Never raises."""
        wg = self._ward_guide
        if wg is None:
            return
        try:
            tac = self._tactics
            fighting = tac is not None and tac.in_fight()
            advice = tac.wards.current(t) if tac is not None and not fighting else None
            avoid = []
            for d in identified or ():
                det = getattr(d, "det", d)
                u, v = _finite(getattr(det, "u", None)), _finite(getattr(det, "v", None))
                if u is not None and v is not None:
                    avoid.append((u, v))
            wg.update(t, advice, frame, lambda: self._ward_recommend(game, tracker), avoid)
        except Exception:
            self._err.exception("Ward guide failed")

    def _ward_overlay(self, now: float, tac: Any, minimap_rect: Any, screen_rect: Any,
                      me_uv: Any) -> tuple[list, list]:
        """``(minimap guides, game-view markers)``: the director's guides with its plain ward rings
        replaced by the ward guide's (when enabled). Nothing from the guide during a fight."""
        guides = list(tac.guides(now)) if tac is not None else []
        wg = self._ward_guide
        if wg is None or not wg.enabled:
            return guides, []
        try:
            guides = [g for g in guides if getattr(g, "kind", "") != "ward"]
            if tac is not None and tac.in_fight():
                return guides, []
            guides += wg.minimap_guides(now)
            return guides, wg.world_markers(now, screen_rect, minimap_rect, me_uv)
        except Exception:
            self._err.exception("Ward guide overlay failed")
            return guides, []

    def _speech_budget(self, said: list[Alert], t: float, busy: bool = False) -> list[Alert]:
        """THE voice gate's budget (voice_policy.VoiceGate): critical alerts pass, the rest within
        the budget (or queued); a queued message may be released when nothing else is said."""
        tac = self._tactics
        if tac is None:
            return said
        try:
            ctx = tac.speech_context()
            out = tac.gate.filter_speech(said, t, ctx)
            if not out and not busy:
                q = tac.gate.pop_ready(t, ctx)
                if q is not None:
                    out = [q]
            return out
        except Exception:
            self._err.exception("Speech budget failed")
            return said

    def _speak_alerts(self, said: list[Alert], t: float, gt: float) -> None:
        rec = self._recorder
        if self._gate is not None:
            for a in said:
                self._gate.record(a, t)
        if self._tactics is not None:
            for a in said:
                self._tactics.gate.note_spoken(a, t)
        for a in said:
            if int(a.level) >= Level.DANGER and not self._muted:
                self._danger_beep(a)       # beep-first: the tone before any TTS work
            self._say(a.text, int(a.level))
            with self._lock:
                self._last_alert, self._last_alert_t = a, t
                self._recent.append((gt, a.text, int(a.level), a.kind.value if isinstance(a.kind, AlertKind)
                                     else str(a.kind)))
            if rec is not None:
                rec.on_alert(a, gt)

    def _say_gank_now(self, gank: list[Alert], t: float, gt: float, frame: Any = None) -> list[Alert]:
        """Gank alerts go to the voice right after the gank check (throttled, routed), before
        the coaching stages of the tick. Returns the alerts said. Never raises.

        A WARNING gank alert whose enemies are all inside my camera view (white rectangle of the
        minimap: they are on my screen already) is written, not spoken (DANGER is always spoken)."""
        if not gank:
            return []
        try:
            calls = [a for a in gank if str(a.key).startswith("call:")]   # fight decision: not throttled
            gank = self._written_if_on_screen([a for a in gank if a not in calls], t, gt, frame) + calls
            routed = self._route_messages([a for a in gank if a not in calls], t, gt)
            said = self._speech_budget(self._route_messages(calls, t, gt) + self._throttler.filter(routed, t), t)
            self._speak_alerts(said, t, gt)
            return said
        except Exception:
            self._errors += 1
            self._err.exception("Gank alert fast path failed")
            return []

    def _camera_rect_now(self, t: float, frame: Any) -> Any:
        """Camera rectangle on the minimap (``camera_proj.CameraRect``-like: u0, v0, u1, v1), from
        a camera tracker already fed elsewhere, else found on this frame; None if unknown."""
        for obj in (getattr(self, "_camera", None), getattr(self, "_camera_tracker", None),
                    getattr(getattr(self, "_ward_guide", None), "camera", None)):
            cur = getattr(obj, "current", None)
            if callable(cur):
                rect = cur(t)
                if rect is not None:
                    return rect
        if frame is None:
            return None
        from treeaicoach import camera_proj

        find = getattr(camera_proj, "find_camera_rect", None)
        return find(frame) if callable(find) else None

    def _written_if_on_screen(self, gank: list[Alert], t: float, gt: float, frame: Any) -> list[Alert]:
        """Gank alerts to speak: WARNING ones (and pre-alerts) whose enemies are all inside the
        camera view (on my screen) are throttled and written instead (HUD line + toast; the
        overlay threat level is unchanged). DANGER ("recule !") is always spoken: it is an
        instruction, not news, and its latency is guaranteed. Without a camera rectangle, or for
        a beginner (``skill_level == "debutant"``: real game, he died to enemies on his screen),
        everything is spoken. Never raises."""
        tracker = self._tracker
        if tracker is None or not any(int(a.level) < Level.DANGER for a in gank):
            return gank
        if str(getattr(self._cfg, "skill_level", "") or "") == "debutant":
            return gank          # a beginner does not read an enemy on his screen as a gank: spoken
        try:
            rect = self._camera_rect_now(t, frame)
            box = tuple(_finite(getattr(rect, k, None)) for k in ("u0", "v0", "u1", "v1")) \
                if rect is not None else ()
            if len(box) != 4 or any(c is None for c in box):
                return gank
            u0, v0, u1, v1 = (float(c) for c in box)        # type: ignore[arg-type]
            m = ON_SCREEN_MARGIN
            speak: list[Alert] = []
            seen: list[Alert] = []
            for a in gank:
                if int(a.level) >= Level.DANGER:
                    speak.append(a)
                    continue
                members = tuple(a.members) or ((a.alias,) if a.alias else ())
                pts = []
                for k in members:
                    tr = tracker.get(k)
                    pts.append(tr.position() if tr is not None and tr.visible else None)
                on = bool(pts) and all(p is not None and u0 + m <= p[0] <= u1 - m and v0 + m <= p[1] <= v1 - m
                                       for p in pts)
                (seen if on else speak).append(a)
            for a in self._throttler.filter(self._route_messages(seen, t, gt), t):
                self._write_text(a, t, gt)
            return speak
        except Exception:
            self._err.exception("On-screen gank check failed")
            return gank

    def _scoreboard_and_praise(self, t: float, tracker: Any, game: GameInfo, threat: int,
                               gank_alerts: list[Alert], gt: float) -> list[Alert]:
        """Tab scoreboard insights + praise -> INFO alerts (voice, throttled) and toasts. Never raises."""
        out: list[Alert] = []
        cfg = self._cfg
        roles = self._role_resolver
        sb, pr = self._scoreboard, self._praise
        summary = None
        try:
            if sb is not None:
                sb.track_positions(t, tracker, game)
                insights = sb.update(game, t, roles=roles, tracker=tracker, threat=threat)
                summary = sb.summary()
                if getattr(cfg, "scoreboard_insights", True):
                    for ins in insights:
                        out.append(make_alert(AlertKind.SCOREBOARD, Level.INFO, t, alias=ins.alias,
                                              text=ins.text, key=ins.key))
                        self._toast(ins.toast_kind, ins.title, ins.subtitle, ins.alias, ins.key, t)
                rec = self._recorder
                if rec is not None and summary is not self._sb_recorded and summary.players:
                    self._sb_recorded = summary
                    on_sb = getattr(rec, "on_scoreboard", None)
                    if callable(on_sb):
                        on_sb(summary.to_dict(), gt)
        except Exception:
            self._errors += 1
            self._err.exception("Scoreboard analysis failed")
        try:
            if pr is not None:
                if any(int(a.level) >= Level.DANGER and a.kind in GANK_KINDS for a in gank_alerts):
                    pr.note_danger(t)
                role = None
                try:
                    role = roles.my_role() if roles is not None else None
                except Exception:
                    role = None
                for p in pr.update(t, game, threat=threat, scoreboard=summary, role=role):
                    if not getattr(cfg, "praise_enabled", True):
                        continue
                    out.append(make_alert(AlertKind.PRAISE, Level.INFO, t, alias=p.alias, text=p.text, key=p.key))
                    self._toast("praise", p.title, p.subtitle, p.alias, p.key, t)
        except Exception:
            self._errors += 1
            self._err.exception("Praise failed")
        return out

    def _item_advice(self, t: float, game: GameInfo, me_pos: Any, gt: float) -> list[Alert]:
        """Build advice (itemization.ItemAdvisor): toast + HUD line, spoken only if enabled. Never raises."""
        cfg = self._cfg
        if not getattr(cfg, "item_advice", True):
            return []
        try:
            adv = getattr(self, "_item_adv", None)
            if adv is None or gt + 5.0 < getattr(self, "_item_adv_gt", 0.0):
                from treeaicoach.itemization import ItemAdvisor
                adv = self._item_adv = ItemAdvisor()
            self._item_adv_gt = gt
            in_base = False
            if me_pos is not None:
                z = geometry.classify_zone(*me_pos)
                in_base = geometry.is_base(z) and geometry.zone_owner(z) == game.my_team
            roles = self._role_resolver
            role = roles.my_role() if roles is not None and hasattr(roles, "my_role") else None
            out: list[Alert] = []
            soon = False
            try:
                for o in (self._objectives.states() if self._objectives is not None else []):
                    rem = getattr(o, "remaining", None)
                    if getattr(o, "key", "") in ("dragon", "baron", "herald", "grubs", "elder") and (
                            getattr(o, "alive", False) or (rem is not None and 0 <= rem <= 120)):
                        soon = True
            except Exception:
                soon = False
            for a in adv.update(t, game, role=role, in_base=in_base, objective_soon=soon):
                if getattr(cfg, "item_advice_toasts", True):
                    self._toast("insight", a.title, a.subtitle, None, a.key, t)
                if getattr(cfg, "item_advice_speak", False):
                    out.append(make_alert(AlertKind.MACRO_TIP, Level.INFO, t, text=a.text, key=a.key))
            return out
        except Exception:
            self._errors += 1
            self._err.exception("Item advice failed")
            return []

    def _hype_and_ai(self, t: float, game: GameInfo, gt: float, threat: int, me_pos: Any,
                     alerts: list[Alert]) -> list[Alert]:
        """hype.py (win probability, caster lines) + ai_advisor.py (optional LLM tip). Never raises."""
        cfg = self._cfg
        try:
            from treeaicoach.ai_advisor import AIAdvisor
            from treeaicoach.hype import HypeCaster, restyle_praise

            hc, ai = getattr(self, "_hype", None), getattr(self, "_ai", None)
            if hc is None or ai is None:
                hc, ai = self._hype, self._ai = HypeCaster(cfg), AIAdvisor(cfg, clock=self._clock)
            elif gt + 5.0 < getattr(self, "_extras_gt", 0.0):          # new game
                hc.reset()
                ai.reset()
                self._ai_held = None                                  # never a plan of the last game
            self._extras_gt = gt
            hc.apply_config(cfg)
            ai.apply_config(cfg)
            summary = self.scoreboard_summary()
            if hc.style == "caster":
                alerts = [dataclasses.replace(a, text=restyle_praise(a.key, a.text, "caster"))
                          if a.kind == AlertKind.PRAISE else a for a in alerts]
            # hype / win-probability lines go through the voice gate like every other message
            # (visual first: written in "minimal" / "normal", spoken in "bavard")
            for i, line in enumerate(hc.update(t, game, summary, threat=threat)):
                swing = "Victoire" in line or "victoire" in line
                alerts = list(alerts) + [make_alert(AlertKind.PRAISE, Level.INFO, t, text=line,
                                                    key=f"hype:swing:{int(t)}" if swing else f"caster:{int(t)}:{i}")]
            in_base = False
            if me_pos is not None:
                z = geometry.classify_zone(*me_pos)
                in_base = geometry.is_base(z) and geometry.zone_owner(z) == game.my_team
            from treeaicoach.ai_advisor import engine_context

            in_fight = self._ai_in_fight()
            ai.update(t, game, in_base=in_base, roles=self._role_resolver, scoreboard=summary,
                      objectives=self._objectives.states() if self._objectives is not None else [],
                      item_text=self.item_advice_text(), threat=threat, context=lambda: engine_context(self, t),
                      win_prob=hc.win_probability(), in_fight=in_fight)
            self._ai_publish(t, gt, ai, threat, in_fight)
        except Exception:
            self._errors += 1
            self._err.exception("Hype / AI advice failed")
        return alerts

    def _plays_tick(self, t: float, gt: float, game: GameInfo, threat: int, me_pos: Any) -> None:
        """Play ratings (plays.py, chess.com style): classify, feed the AI advisor, animate the badge
        (fx_overlay.py, own click-through window, Windows only). Never raises."""
        try:
            from treeaicoach import plays

            pc = getattr(self, "_plays", None)
            if pc is None:
                pc = self._plays = plays.PlayClassifier(self._cfg)
                self.recent_plays: list[Any] = []
            pc.apply_config(self._cfg)
            n_before = len(pc.history())
            shown = pc.update(plays.build_context(self, t, gt, game, threat, me_pos))
            ai = getattr(self, "_ai", None)
            note = getattr(ai, "note_play", None)
            if callable(note):
                for p in pc.history()[n_before:]:
                    note(p)
            for p in shown:
                self.recent_plays = (self.recent_plays + [p])[-20:]
                self.text_messages.append((t, "play", f"{p.title} : {p.reason}"))
                fx = getattr(self, "_play_fx", None)
                if fx is None and sys.platform == "win32" and self._running:
                    from treeaicoach.fx_overlay import PlayFx

                    fx = self._play_fx = PlayFx(self._cfg, self._screen_rects,
                                                lambda: self._overlay_visible and self._in_game)
                pr = getattr(self, "_presenter", None)
                if pr is not None:
                    from treeaicoach import presenter as prs

                    if pr.offer(prs.Message("play", f"{p.title} : {p.reason}", topic=f"play:{t:.0f}"),
                                self._presenter_ctx(t)).channel == prs.DROP:
                        continue                       # no badge over a gank
                if fx is not None:
                    fx.apply_config(self._cfg)
                    fx.push(p)
        except Exception:
            self._errors += 1
            self._err.exception("Play ratings failed")

    def plays_summary(self) -> dict | None:
        """Counts per rating class + "précision" 0-100 of the current / last game (plays.summarize)."""
        pc = getattr(self, "_plays", None)
        try:
            return pc.summary() if pc is not None and pc.history() else None
        except Exception:
            return None

    def _ai_in_fight(self) -> bool:
        """A fight is on (the tactical director's fight tracker, the same source as the presenter).
        Bug fixed: this used to look for ``_fight`` / ``_fight_tracker`` attributes the engine does
        not have, so the AI never knew about fights."""
        tac = getattr(self, "_tactics", None)
        fn = getattr(tac, "in_fight", None)
        if callable(fn):
            try:
                return bool(fn())
            except Exception:
                return False
        return False

    def _ai_publish(self, t: float, gt: float, ai: Any, threat: int, in_fight: bool) -> None:
        """Show a finished AI / offline plan through the presenter, or hold / drop it:

        * held while a fight / gank threat is on, and while it contradicts the live game-changer
          card (an explained disagreement waits for the card to go; an unexplained one is dropped);
        * dropped once older than its moment allows (``Advice.max_age``): never a stale plan;
        * shown = routed by the presenter (kind "ai"); only then the HUD state / voice / timeline."""
        adv = ai.poll()
        if adv is not None:
            prev = getattr(self, "_ai_held", None)
            if prev is not None and prev is not adv:
                ai.note_dropped(prev, "replaced")
            self._ai_held = adv
        adv = getattr(self, "_ai_held", None)
        if adv is None:
            return
        if t - adv.t > adv.max_age:
            self._ai_held = None
            ai.note_dropped(adv, "stale")
            return
        if not adv.error and (in_fight or threat >= int(Level.WARNING)):
            return                                          # after the fight, if still fresh
        tac = getattr(self, "_tactics", None)
        card = tac.macro_active() if tac is not None and hasattr(tac, "macro_active") else None
        verdict = adv.check_card(card)
        if verdict == "conflict":
            self._ai_held = None
            ai.note_dropped(adv, "conflict")
            return
        if verdict == "explained":
            return                                          # shown once the card is gone (if still fresh)
        self._ai_held = None
        kind, key = ("warning" if adv.error else "insight"), f"ai:{adv.t:.0f}"
        if getattr(self, "_ai_offer_t", None) == t:
            self._ai_held = adv                             # one offer per tick
            return
        self._ai_offer_t = t
        if self._toasts is not None and getattr(self._cfg, "toasts_enabled", True):
            # the answer to F8 (asked by the player) outranks the "question envoyée" line it replaces
            shown = self._toast(kind, adv.title, adv.text, None, key, t, urgency=adv.urgency)
        else:                                               # no toast window: the HUD line only, still routed
            shown = self._ai_hud_line(adv, key, t)
        if shown is False:
            if t - adv.t + 1.0 > adv.max_age:
                ai.note_dropped(adv, "presenter")
            else:
                self._ai_held = adv                         # panel busy / banner gap: retried while fresh
            return
        self.last_ai_advice = adv.text
        self.ai_answer_seq = getattr(self, "ai_answer_seq", 0) + 1
        self.text_messages.append((t, "ai", adv.text))
        ai.note_shown(adv, gt)
        if getattr(self._cfg, "ai_speak", False) and threat < Level.WARNING and not adv.error:
            self._say(adv.text, int(Level.INFO))

    def _ai_hud_line(self, adv: Any, key: str, t: float) -> bool:
        """The AI line on the HUD card when toasts are off, through the presenter. Never raises."""
        try:
            pr = getattr(self, "_presenter", None)
            if pr is not None:
                from treeaicoach import presenter as prs

                d = pr.offer(prs.Message("ai", adv.text, adv.title, topic=key, urgency=adv.urgency),
                             self._presenter_ctx(t))
                if d.channel == prs.DROP:
                    return False
            self._text_msg, self._text_kind = (t, adv.text), "ai"
            return True
        except Exception:
            return False

    def ask_ai(self) -> str:
        """"Demander à l'IA" (hotkey / button): manual AI request, answer later as toast + HUD line.

        Returns a French acknowledgement (also shown as a toast). Never raises, never blocks."""
        try:
            from treeaicoach.ai_advisor import AIAdvisor, engine_context

            ai = getattr(self, "_ai", None)
            if ai is None:
                ai = self._ai = AIAdvisor(self._cfg, clock=self._clock)
            ai.apply_config(self._cfg)
            now = self._clock()
            with self._lock:
                game = self._game if self._in_game else None
            msg = ai.ask(now, game, roles=self._role_resolver, scoreboard=self.scoreboard_summary(),
                         objectives=self._objectives.states() if self._objectives is not None else [],
                         item_text=self.item_advice_text(), context=lambda: engine_context(self, now))
            self.last_ai_ack = msg
            self._toast("insight", "IA", msg, None, f"ai-ask:{now:.0f}", now)
            return msg
        except Exception:
            self._err.exception("ask_ai failed")
            return "Conseil IA indisponible."

    def _ai_postgame_review(self, record: Path, html: Path) -> None:
        """AI review of the finished game appended to the HTML report (provider configured). Never raises."""
        try:
            cfg = self._cfg
            if str(getattr(cfg, "ai_provider", "off") or "off") == "off" or self._demo:
                return
            import json

            from treeaicoach.ai_advisor import append_review_html, postgame_review
            from treeaicoach.analysis import analyze_game

            data = json.loads(Path(record).read_text(encoding="utf-8"))
            ai = getattr(self, "_ai", None)
            review = postgame_review(cfg, analyze_game(data), timeline=list(getattr(ai, "timeline", None) or ()))
            if review and append_review_html(html, review, str(cfg.ai_provider)):
                self.last_ai_review = review
                log.info("AI post-game review added to %s", html)
        except Exception:
            log.exception("AI post-game review failed")

    def win_probability(self) -> float | None:
        """Live probability (0..1) that my team wins (hype.py model), None outside a game."""
        hc = getattr(self, "_hype", None)
        return hc.win_probability() if hc is not None and self._in_game else None

    def hype_stats(self) -> dict:
        """Win-probability statistics of the current / last game (shareable summary)."""
        hc = getattr(self, "_hype", None)
        return hc.stats() if hc is not None else {}

    def ai_status(self) -> tuple[int, str | None]:
        """``(sequence, French error)`` of the optional AI advisor (the sequence changes per new error)."""
        ai = getattr(self, "_ai", None)
        return ai.status() if ai is not None else (0, None)

    def ai_budget(self) -> dict | None:
        """Per-game AI counters (``auto_used / auto_max``, ``urgent_used``, ``manual``), None when off."""
        ai = getattr(self, "_ai", None)
        try:
            return ai.budget_info() if ai is not None and ai.enabled else None
        except Exception:
            return None

    def ai_budget_text(self) -> str:
        """"IA 3/5" (empty when the AI advice is off)."""
        from treeaicoach.ai_advisor import budget_text

        return budget_text(self.ai_budget())

    def play_gauge(self) -> Any:
        """The "jouer plus fort ou non" gauge (:class:`treeaicoach.coach.Gauge`), None outside a game."""
        g = self._gauge
        return g.current() if g is not None and self._in_game else None

    def coach_extras(self) -> dict:
        """Coaching extras of the current / last game for the UI and the report (coach_plus.py):
        ``goal`` (label), ``goal_status`` ("en cours" / "réussi" / "raté"), ``plan`` (matchup card
        lines), ``death_causes`` (cause keys of my deaths). {} before the first game. Never raises."""
        try:
            plus = getattr(self, "_coach_plus", None)
            if plus is None:
                return {}
            g = plus.goals.goal
            card = plus.card
            return {"goal": g.label if g is not None else None, "goal_status": plus.goals.status,
                    "plan": list(card.lines) + ([card.jungle] if card is not None and card.jungle else [])
                    if card is not None else [],
                    "death_causes": plus.death_causes()}
        except Exception:
            return {}

    def top_tip(self) -> tuple[str, str] | None:
        """``(text, tone)`` of the ONE written advice shown on the HUD right now, else None."""
        try:
            text = self._hud_line(self._clock())
            return (text, self._tip_tone(text)) if text else None
        except Exception:
            return None

    def _is_go_line(self, text: str | None) -> bool:
        """A "play harder" line (tone "go" or its wording): hidden under a PRUDENT / SAFE gauge
        and during a base siege / ace."""
        if not text:
            return False
        low = text.casefold()
        return self._tip_tone(text) == "go" or any(w in low for w in GO_WORDS)

    def _tip_tone(self, text: str | None) -> str:
        """Tone of the HUD line ("danger" / "warning" / "go" / "info")."""
        if not text:
            return "info"
        text = (getattr(self, "_card_src", None) or {}).get(text, text)
        rot = self._tip_rotator
        tip = rot.current_tip() if rot is not None else None
        memo = getattr(self, "_tone_memo", None)
        if memo is None or len(memo) > 300:
            memo = self._tone_memo = {}
        if tip is not None and text == self._tip_text:
            tone = str(getattr(tip, "tone", "info") or "info")
            memo[text] = tone             # a tip keeps its tone after the rotator moved on (no colour flip)
            return tone
        if text in memo:
            return memo[text]
        low = text.casefold()
        if any(w in low for w in ("recule", "danger", "gank !", "fuis", "ta base", "ace :", "annule ton rappel",
                                  "reste sous ta tour :")):
            return "danger"
        if any(w in low for w in ("attention", "prudent", "évite", "safe")):
            return "warning"
        return "go" if any(w in low for w in GO_WORDS) else "info"

    def detected_role(self) -> tuple[str | None, str | None]:
        """``(my role short name, swap notice)`` for the dashboard, e.g. ``("MID", None)``."""
        try:
            from treeaicoach.roles import ROLE_SHORT

            game = self._game
            res = self._role_resolver
            role = None
            if res is not None and hasattr(res, "my_role"):
                role = res.my_role()
            if role is None and game is not None and game.me is not None:
                role = getattr(game.me, "position", None) or None
            return (ROLE_SHORT.get(role, role) if role else None), self._role_notice(self._clock())
        except Exception:
            return None, None

    def item_advice_text(self) -> str | None:
        """Current build advice line for the UI / HUD ("Prochain objet : ..."), None if none."""
        adv = getattr(self, "_item_adv", None)
        rec = adv.current() if adv is not None and getattr(self._cfg, "item_advice", True) else None
        return rec.text if rec is not None else None

    # ================================================================== MASTERMIND (mastermind.py)
    TEAM_PLAN_GT = (80.0, 200.0)       # the game-start team plan banner, once, in this game-time window

    def _mastermind_tick(self, t: float, game: GameInfo, facts: dict | None) -> Any:
        """The game model (mastermind.MastermindModel: comps, power windows, win conditions,
        threats), updated at most once per game second; registered for the game-changer rule;
        the game-start team plan banner ("PLAN D'ÉQUIPE"). Returns the model. Never raises."""
        try:
            from treeaicoach import mastermind

            mm = getattr(self, "_mastermind", None)
            if mm is None:
                mm = self._mastermind = mastermind.MastermindModel()
            mastermind.set_active(mm)
            gt = _finite((facts or {}).get("gt")) or _finite(getattr(game, "game_time", None)) or 0.0
            last = mm.last_gt
            if last is not None and 0.0 <= gt - last < 1.0:
                return mm
            role, opp = None, None
            try:
                roles = self._role_resolver
                role = roles.my_role() if roles is not None and hasattr(roles, "my_role") else None
            except Exception:
                role = None
            for o in (facts or {}).get("opponents") or []:
                if isinstance(o, dict) and o.get("alias"):
                    opp = str(o["alias"])
                    break
            r = mm.update(gt, game, role=role or (facts or {}).get("my_role"), lane_opp=opp)
            self._team_plan_toast(t, gt, game, r)
            return mm
        except Exception:
            self._errors += 1
            self._err.exception("Mastermind failed")
            return None

    def _team_plan_toast(self, t: float, gt: float, game: Any, reading: Any) -> None:
        """Once per game, at the start: "PLAN D'ÉQUIPE" banner (our main win condition, verb first)."""
        mm = getattr(self, "_mastermind", None)
        if reading is None or mm is None or mm.plan_shown:
            return
        if not (self.TEAM_PLAN_GT[0] <= gt <= self.TEAM_PLAN_GT[1]):
            return
        me = getattr(game, "me", None)
        tac = self._tactics
        if me is None or bool(getattr(me, "is_dead", False)) or (tac is not None and tac.in_fight()):
            return
        from treeaicoach.game_plan import team_card

        card = team_card(reading)
        mm.plan_shown = True
        if card is not None:
            self._toast("insight", card.title, card.subtitle, None, "plan:team", t)

    def mastermind_snapshot(self) -> dict | None:
        """The mastermind reading as a compact dict (AI advisor snapshot, UI). None before data."""
        mm = getattr(self, "_mastermind", None)
        return mm.snapshot() if mm is not None else None

    def mastermind_reading(self) -> Any:
        """The latest mastermind.Reading (None before data)."""
        mm = getattr(self, "_mastermind", None)
        return mm.current() if mm is not None else None

    def _stance_and_tips(self, t: float, game: GameInfo, threat: int) -> list[Alert]:
        """Stance (HUD pill, spoken on change) + rotating written tip. Never raises."""
        out: list[Alert] = []
        try:
            me_tr = self._tracker.me() if self._tracker is not None else None
            if threat and me_tr is not None and self._grouped(t, me_tr.position()):
                threat = 0               # a "gank" inside a team fight: the fight decides, not SAFE
            facts = self._coach.facts() if self._coach is not None else {}
            summary = self.scoreboard_summary()
            plus = self._coach_plus_tick(t, game, facts, threat)
            mm = self._mastermind_tick(t, game, facts)
            if self._stance is not None:
                extra_f = list(plus.factors() if plus is not None else []) + self._macro_factors()
                extra_f += mm.gauge_factors() if mm is not None else []
                out += list(self._stance.update(t, facts, game, summary, threat=threat, extra=extra_f) or [])
            if self._gauge is not None:
                st = self._stance.current() if self._stance is not None else None
                tac = self._tactics
                fs = tac.fight.state() if tac is not None else None
                me = getattr(game, "me", None)
                alive = me is not None and not bool(getattr(me, "is_dead", False))
                self._gauge.update(t, st, fs, summary, threat,
                                   active=alive and (st is not None or bool(getattr(fs, "active", False))))
            rot = self._tip_rotator
            if rot is not None and getattr(self._cfg, "text_tips", True):
                from treeaicoach.tips import build_context

                stance = self._stance.current() if self._stance is not None else None
                prev_id = rot.current_id()
                adv = getattr(self, "_item_adv", None)
                rec = adv.current() if adv is not None else None
                item = getattr(rec, "item_name", None) if rec is not None else None
                extra: dict = {}
                if plus is not None:
                    from treeaicoach.coach_plus import buy_fields, shop_fields
                    extra = {**plus.tip_fields(), **buy_fields(rec, bool(facts.get("in_base"))), **shop_fields(rec)}
                extra.update(self._tip_consistency_fields(t, facts))
                self._tip_text = rot.update(t, build_context(facts, game, summary, stance, item=item, extra=extra))
                # a toast only for a NEW tip (its live numbers refreshing is not news)
                # (and at most one tip toast every TIP_TOAST_GAP_S, contextual tips only: the HUD line
                # already shows every tip, the toast is a beginner's extra nudge)
                tip_now = rot.current_tip()
                if self._tip_text and rot.current_id() != prev_id and getattr(self._cfg, "tip_toasts", False) \
                        and int(getattr(tip_now, "prio", 1) or 1) >= 3 \
                        and t - getattr(self, "_tip_toast_t", -math.inf) >= TIP_TOAST_GAP_S:
                    self._tip_toast_t = t
                    self._toast("insight", "ASTUCE", self._tip_text, None, f"tip:{rot.current_id()}", t)
            else:
                self._tip_text = None
        except Exception:
            self._errors += 1
            self._err.exception("Stance / tips failed")
        try:   # resources I forget (skill points, spells / summoners at my death, potion, trinket, gold)
            from treeaicoach.resources_coach import engine_tick as resources_tick

            resources_tick(self, t, game, threat)
        except Exception:
            self._err.exception("Resources coach failed")
        return out

    # ------------------------------------------------------------------ cross-system consistency (V2 audit)
    RECALL_KEYS = ("recall_gold",)
    RECALL_TIP_IDS = frozenset({"gold_back", "comp_ready", "wave_push_back", "obj_recall_now"})
    RECALL_TOPIC_S = 120.0

    def _macro_factors(self) -> list[tuple[float, str]]:
        """The active COUP DE GÉNIE call as a gauge reason, so the gauge and the call never
        disagree ("Plaque la tour" while the gauge says SAFE): +2 for a "go" call, -2 for a
        "recule" call. Never raises."""
        try:
            tac = self._tactics
            c = tac.macro_active() if tac is not None else None
            if c is None:
                return []
            w = {"safe": 2.0, "danger": -2.0}.get(getattr(c, "color", ""), 0.0)
            return [(w, f"appel : {str(c.title).rstrip(' !').lower()}")] if w else []
        except Exception:
            log.debug("macro gauge factor failed", exc_info=True)
            return []

    def _tip_consistency_fields(self, t: float, facts: dict | None = None) -> dict:
        """TipContext fields that keep the written tip in line with the other systems: the tone of
        the active macro call (a "go" call hides the cautious tips and vice versa), whether a
        recall reminder was shown recently (one recall message per trip, not four), a base visit
        just happened (no "rentre acheter" 10 s after leaving the shop), a ward line was just shown
        (one ward call, not four) and the calls of the last 90 s (their tips wait)."""
        out: dict = {}
        try:
            tac = self._tactics
            c = tac.macro_active() if tac is not None else None
            if c is not None:
                out["macro_tone"] = {"safe": "go", "danger": "danger"}.get(getattr(c, "color", ""))
                if getattr(c, "kind", "") == "wave_recall":
                    self._recall_topic_t = t
            last = getattr(self, "_recall_topic_t", None)
            out["recall_said"] = last is not None and 0.0 <= t - last < self.RECALL_TOPIC_S
            in_base = bool((facts or {}).get("in_base"))
            if in_base:
                self._base_t = t
            out["base_recent"] = self._base_recent(t) and not in_base
            out["ward_recent"] = self._ward_recent(t)
            if tac is not None:
                out["recent_calls"] = tac.macro.recent_kinds(t)
        except Exception:
            log.debug("tip consistency failed", exc_info=True)
            pass
        return out

    BASE_RECENT_S = 75.0           # after a base visit: no "rentre acheter" reminder this long
    WARD_TOPIC_S = 180.0           # after a ward line on the card: no other ward line this long

    def _base_recent(self, t: float) -> bool:
        last = getattr(self, "_base_t", None)
        return last is not None and 0.0 <= t - last < self.BASE_RECENT_S

    def _ward_recent(self, t: float) -> bool:
        last = getattr(self, "_ward_topic_t", None)
        return last is not None and 0.0 <= t - last < self.WARD_TOPIC_S

    def _recall_consistency(self, alerts: list[Alert], t: float) -> list[Alert]:
        """Recall reminders ("Tu as 1300 pièces d'or, pense à rentrer") are dropped when another
        system already said it (macro "rentre" call, a recall tip on screen) or while a macro call
        asks for something else; any shown one marks the recall topic. Never raises."""
        try:
            keep = []
            rot = self._tip_rotator
            tip_id = rot.current_id() if rot is not None else None
            tac = self._tactics
            active = tac.macro_active() if tac is not None else None
            for a in alerts:
                if str(a.key).startswith(self.RECALL_KEYS):
                    last = getattr(self, "_recall_topic_t", None)
                    if (last is not None and 0.0 <= t - last < self.RECALL_TOPIC_S) or tip_id in self.RECALL_TIP_IDS \
                            or active is not None or self._base_recent(t):
                        continue
                    self._recall_topic_t = t
                keep.append(a)
            return keep
        except Exception:
            log.debug("recall consistency failed", exc_info=True)
            return alerts

    def _coach_plus_tick(self, t: float, game: GameInfo, facts: dict, threat: int) -> Any:
        """coach_plus.CoachPlus (power spikes, matchup card, session goal, death cause): its toasts
        (filtered by the player's level, held during a gank / fight) + the object for the gauge /
        tips. Visual only. Never raises."""
        try:
            plus = getattr(self, "_coach_plus", None)
            if plus is None:
                from treeaicoach.coach_plus import CoachPlus
                plus = self._coach_plus = CoachPlus()
            from treeaicoach import skill
            tac = self._tactics
            busy = threat >= Level.WARNING or (tac is not None and tac.in_fight())
            gt = _finite(facts.get("gt")) or _finite(getattr(game, "game_time", None)) or 0.0
            notes = plus.update(t, gt, game, facts, tac.map_state() if tac is not None else None,
                                busy=busy, min_prio=skill.tip_min_prio(self._cfg))
            for n in notes:
                if n.kind == "praise" and not getattr(self._cfg, "praise_enabled", True):
                    continue
                self._toast(n.kind, n.title, n.text, None, n.key, t)
                if n.hud:
                    self._text_msg = (t, n.text)
                    self._text_kind = "death_cause" if str(n.key).startswith("death") else "coach_plus"
                    self.text_messages.append((t, "coach_plus", n.text))
                    del self.text_messages[:-100]
            return plus
        except Exception:
            self._errors += 1
            self._err.exception("Coach extras failed")
            return None

    def _route_messages(self, alerts: list[Alert], t: float, gt: float) -> list[Alert]:
        """Voice policy: returns the alerts to SPEAK (through the throttler); the others are
        written (HUD line + toast), all of them anti-spam gated. Never raises."""
        try:
            from treeaicoach import voice_policy as vp
        except Exception:
            return alerts
        level = getattr(self._cfg, "voice_level", vp.DEFAULT_VOICE_LEVEL)
        gate = self._gate
        tac = self._tactics
        ctx = tac.speech_context() if tac is not None else None
        voice: list[Alert] = []
        for a in alerts:
            try:
                if a.kind == AlertKind.CONTROL_WARD and self._ward_recent(t):
                    continue                     # one ward call at a time (the card just said it)
                way = tac.gate.decide(a, t, ctx, level) if tac is not None else vp.route(a, level)
                if way == "drop":
                    continue
                if way == "voice":
                    if gate is None or gate.check(a, t):
                        voice.append(a)
                    continue
                if gate is not None and not gate.allow(a, t):
                    continue
                self._write_text(a, t, gt)
            except Exception:
                self._err.exception("Message routing failed")
        return voice

    def _write_text(self, a: Alert, t: float, gt: float) -> None:
        """A written-only message: HUD line + toast (by kind) + record."""
        from treeaicoach import voice_policy as vp

        kind = vp.kind_name(a)
        if not self._keeps_death_lesson(kind):
            self._text_msg, self._text_kind = (t, a.text), kind
        self.text_messages.append((t, kind, a.text))
        del self.text_messages[:-100]
        toast = vp.TEXT_TOAST.get(kind)
        if kind == "personal_danger" and int(a.level) < int(Level.DANGER):
            toast = None                 # a written warning ("Farme sous ta tour") is the card, not a red banner
        banner = self._tactics.banner(t) if self._tactics is not None else None
        last = getattr(self, "_text_toast_last", None)
        if last is not None and last[0] == a.text and 0.0 <= t - last[1] < TEXT_TOAST_DEDUP_S:
            toast = None                 # the same written line again on the next ticks: one banner only
        if toast is not None and not (banner is not None and banner.subtitle == a.text):
            self._toast(toast[0], toast[1], a.text, a.alias, f"text:{a.key}", t)
            self._text_toast_last = (a.text, t)
        rec = self._recorder
        if rec is not None:
            rec.on_alert(a, gt)

    def _hud_line(self, now: float) -> str | None:
        """The ONE written HUD line: a fresh written-only message (10 s), else an urgent live
        insight of the coach, else the rotating tip, else the coach / Tab line. A line stays at
        least :data:`HUD_DWELL_S` (readable) while it is still valid, unless the new one is a danger."""
        valid: list[str] = []
        cand: str | None = None
        msg = self._text_msg
        game = self._game
        me_dead = bool(getattr(getattr(game, "me", None), "is_dead", False)) if game is not None else False
        if me_dead and msg is not None and getattr(self, "_text_kind", None) not in DEAD_TEXT_KINDS:
            msg = self._text_msg = None  # dead: only the death lesson / an objective / a call (a lane
            #                              advice written now would also be stale at the respawn)
        if msg is not None and not me_dead and msg[0] < getattr(self, "_hud_alarm_t", -1e9) \
                and getattr(self, "_text_kind", None) not in DEAD_TEXT_KINDS:
            msg = self._text_msg = None  # written before a gank / fight alarm: the situation changed
        if msg is not None and (0.0 <= now - msg[0] < TEXT_MSG_S
                                or (me_dead and getattr(self, "_text_kind", None) == "death_cause")):
            valid.append(msg[1])         # (the death lesson stays the whole death)
        mc = self._tactics.macro_active() if self._tactics is not None else None
        if mc is not None and mc.text not in valid and (msg is None or msg[0] <= mc.t or now - msg[0] >= TEXT_MSG_S
                                                        or self._tip_tone(msg[1]) not in ("danger", "warning")):
            valid.insert(0, mc.text)                  # an active macro call keeps the line while it is valid
        coach = self._coach
        # before the minions (1:05) the lane-phase advice makes no sense (seen in a real game:
        # "Joue agressif avant le niveau 6" in the fountain at 0:26): written messages / calls only
        game = self._game
        early = False
        try:
            if game is not None:
                gt_now = (_finite(game.game_time) or 0.0) + min(max(0.0, now - self._game_t), 3.0)
                early = gt_now < EARLY_ADVICE_GT_S
        except Exception:
            early = False
        me_dead = bool(getattr(getattr(game, "me", None), "is_dead", False)) if game is not None else False
        if me_dead:      # dead: the respawn countdown + the death cause / active call only
            early = True
        siege, siege_line = self._siege(now)
        if siege is not None:   # ace / siege: that line first, no "à toi de jouer", no tip
            valid = [siege_line] + [v for v in valid if not self._is_go_line(v)]
            early = True
        if coach is not None and not early:
            try:
                urgent = [it for it in coach.insight_items() if it[0] >= 65 and it[2] != "objective"]
                valid += [it[1] for it in urgent[:1]]
            except Exception:
                pass
        rot = self._tip_rotator
        tip_id = str(rot.current_id() or "") if rot is not None else ""
        if self._tip_text and not early and not tip_id.startswith("dead_"):   # (a "while dead" tip is stale alive)
            valid.append(self._tip_text)
        # the card holds ONE instruction, verb first (presenter.card_line): "why : what" lines are
        # turned around, statements / praise / statistics never take the line
        try:
            from treeaicoach.presenter import card_line

            src = getattr(self, "_card_src", None)
            if src is None or len(src) > 200:
                src = self._card_src = {}
            lines: list[str] = []
            for v in valid:
                c = card_line(v)
                if c and STAT_LINE_RE.search(c):
                    continue             # a statistic is a debrief (report), not an instruction for the card
                if c and c not in lines:
                    src[c] = v
                    lines.append(c)
            valid = lines
        except Exception:
            log.debug("card line failed", exc_info=True)
        # never contradict the gauge: no "à toi de jouer" line under a PRUDENT / SAFE gauge (no
        # "pousse" either under SAFE: the card says "Joue prudent : reste sous ta tour")
        g = None
        try:
            from treeaicoach.presenter import line_stance

            g = self._gauge.current() if self._gauge is not None else None
            if g is not None and int(g.step) <= -1 and len(valid) > 0:
                valid = [v for v in valid if not self._is_go_line(v)
                         and not (int(g.step) <= -2 and line_stance(v) == "push"
                                  and (mc is None or v != mc.text))]
        except Exception:
            pass
        if not early and siege is None:
            valid = self._with_objective_line(valid, now, mc.text if mc is not None else None)
        valid = self._no_contradiction(valid, now, siege is not None or me_dead)
        # the gauge's two extremes ARE the instruction when nothing else is said (beginner levels)
        try:
            from treeaicoach import overlay_render as orr

            step = int(g.step) if g is not None else None
            lvl = str(getattr(self._cfg, "skill_level", "") or "intermediaire")
            if not valid and step in orr.GAUGE_LINE and lvl in orr.GAUGE_LEVELS and not early:
                valid = self._no_contradiction([orr.GAUGE_LINE[step]], now, False)
        except Exception:
            log.debug("gauge line failed", exc_info=True)
        # a line replaced by another one is not shown again for HUD_RETIRE_S (no A -> B -> A flicker;
        # a danger interruption does not retire it)
        retired = getattr(self, "_hud_retired", None)
        if retired is None:
            retired = self._hud_retired = {}
        live = {_digits(v) for v in valid}
        valid = [v for v in valid
                 if not (0.0 <= now - retired.get(_line_shape(v), -1e9) < HUD_RETIRE_S)
                 or self._tip_tone(v) == "danger"]
        expired = getattr(self, "_hud_expired", None)
        if expired:                  # a line that had its whole show window stays off while it is valid
            expired &= live
            valid = [v for v in valid if _digits(v) not in expired or self._tip_tone(v) == "danger"]
        cand = valid[0] if valid else None
        shown = getattr(self, "_hud_shown", None)
        pr = getattr(self, "_presenter", None)
        if pr is not None and cand is not None:     # fight / gank: no low-value line at all
            ctx = self._presenter_ctx(now)
            if siege is None:
                cand = pr.filter_panel_line(cand, self._tip_tone(cand), ctx)
        cand = self._steady_line(cand, shown, valid, now, me_dead, siege is not None, mc)
        self._note_stance(cand, now)
        if cand and WARD_LINE_RE.match(cand):
            self._ward_topic_t = now
        if shown is None or shown[0] != cand:
            if shown is not None and shown[0] and _line_shape(shown[0]) != _line_shape(cand or ""):
                retired[_line_shape(shown[0])] = now
                if len(retired) > 100:
                    for k in sorted(retired, key=retired.get)[:50]:
                        del retired[k]
            same = shown is not None and shown[0] is not None and cand is not None \
                and _ticking(shown[0], cand)
            if cand is not None and not same:
                try:
                    from treeaicoach.presenter import line_stance

                    st_ = line_stance(cand)
                    if st_ is not None:
                        starts = getattr(self, "_stance_starts", None)
                        if starts is None:
                            starts = self._stance_starts = []
                        starts.append((now, st_))
                        del starts[:-20]
                except Exception:
                    pass
            # a countdown ticking ("Dragon dans 0:45" -> "0:44") is the same line: it keeps its age
            self._hud_shown = (cand, shown[1] if same else now)
        return cand

    def _objective_line(self, now: float) -> str | None:
        """"Va bot : Dragon dans 0:45" in the last 60 s before a spawn my role plays (beginner to
        advanced; overlay_render._objective_instruction). Never raises."""
        try:
            from treeaicoach import overlay_render as orr

            game = self._game
            if game is None or self._objectives is None:
                return None
            gt = (_finite(game.game_time) or 0.0) + min(max(0.0, now - self._game_t), 3.0)
            me = self._tracker.me() if self._tracker is not None else None
            res = self._role_resolver
            role = res.my_role() if res is not None and hasattr(res, "my_role") else None
            if role is None and game.me is not None:
                role = getattr(game.me, "position", None) or None
            st = SimpleNamespace(objectives=self._objectives.states(), game_time=gt, me_uv=me.position()
                                 if me is not None else None, my_role=str(role).upper() if role else None,
                                 skill_level=getattr(self._cfg, "skill_level", None))
            obj = orr._objective_instruction(st)
            return obj[0] if obj is not None else None
        except Exception:
            log.debug("objective line failed", exc_info=True)
            return None

    def _with_objective_line(self, valid: list[str], now: float, call: str | None = None) -> list[str]:
        """The objective instruction goes before every line but a danger / warning one and the
        active planner call (its banner / voice say the same thing: one source), and the lines
        about the same objective are dropped (one message per subject)."""
        line = self._objective_line(now)
        if line is None:
            return valid
        name = line.split(" : ", 1)[-1].split(" dans ", 1)[0].strip().lower()
        if call is not None and call in valid and name in call.lower():
            # the active planner call IS about this objective ("Prépare le dragon : ton équipe domine"):
            # it replaces the countdown line while it lasts (one message per subject)
            return [call] + [v for v in valid if v != call and name not in v.lower()]
        urgent = [v for v in valid if (self._tip_tone(v) in ("danger", "warning") or (call is not None and v == call))
                  and name not in v.lower()]
        rest = [v for v in valid if v not in urgent and name not in v.lower()]
        return urgent + [line] + rest

    def _steady_line(self, cand: str | None, shown: tuple | None, valid: list[str], now: float, dead: bool,
                     siege: bool, mc: Any) -> str | None:
        """No flicker on the card: a line stays at least :data:`HUD_DWELL_S`, lingers
        :data:`HUD_LINGER_S` after it stopped being valid, and after the card emptied a new
        non-urgent line waits :data:`HUD_GAP_S`. A danger / warning line, an active macro call,
        a siege, my death or my respawn switch at once. Never raises."""
        try:
            prev_dead = getattr(self, "_hud_dead_prev", None)
            self._hud_dead_prev = dead
            if shown is None or siege or prev_dead is None or prev_dead != dead:
                return cand
            line, since = shown
            if self._alarm_now(now):
                # the red / orange alarm card covers the line: its dwell starts when it is seen again
                self._hud_alarm_t = now
                since = now
                self._hud_shown = (line, now)
            if line is not None and any(_line_shape(v) == _line_shape(line) for v in valid):
                self._hud_valid_t = now
            # the card shows a calm line only ADVICE_SHOW_S[level] (overlay_render): past that, the
            # line is over here too (retired), so the next one waits HUD_GAP_S like after any blank
            from treeaicoach import overlay_render as orr

            lvl = str(getattr(self._cfg, "skill_level", "") or "intermediaire")
            win = orr.ADVICE_SHOW_S.get(lvl, orr.ADVICE_SHOW_S["intermediaire"])
            if line is not None and win is not None and now - since >= win \
                    and (cand is None or cand == line or _ticking(cand, line)):
                tone = self._tip_tone(line)
                if not (tone == "danger" or (tone == "warning" and lvl != "expert")):
                    exp = getattr(self, "_hud_expired", None)
                    if not isinstance(exp, set):
                        exp = self._hud_expired = set()
                    exp.add(_digits(line))                # shown its whole window: not again while valid
                    return None
            if cand == line:
                return cand
            if cand is not None and line is not None and _ticking(cand, line):
                return cand                  # same line, live numbers (a countdown never freezes)
            if cand is not None and line is not None and cand.split(" : ", 1)[0] == line.split(" : ", 1)[0]:
                if not COUNTDOWN_RE.search(line):
                    return line              # same instruction (a call repeating a tip): keep the card still
            rank = {"danger": 3, "warning": 2}
            r_new = rank.get(self._tip_tone(cand), 1) if cand is not None else 0
            r_old = rank.get(self._tip_tone(line), 1) if line is not None else 0
            # the active planner call takes the card at once when it is a coup de génie / a danger call,
            # or when it just started (its banner and voice say it now: the card must agree; the
            # planner only starts a non-urgent call once the card has been still CARD_SETTLE_S)
            call = mc is not None and cand is not None and _same_call(cand, mc) \
                and (bool(getattr(mc, "genius", False)) or getattr(mc, "color", "") == "danger"
                     or 0.0 <= now - float(getattr(mc, "t", -1e9) or -1e9) < 2.0)
            if cand is not None and (call or (r_new > r_old if line is not None else r_new == 3)):
                return cand                                     # more urgent than what is shown: at once
            # a countdown no longer valid ("Baron dans 0:20" once the Baron is up) is never kept
            keep_ok = line is not None and bool(self._no_contradiction([line], now, False)) \
                and not (COUNTDOWN_RE.search(line) and not any(_line_shape(v) == _line_shape(line) for v in valid))
            if line is not None and 0.0 <= now - since < HUD_DWELL_S and keep_ok:
                return line                                     # dwell
            if cand is None and line is not None and keep_ok \
                    and 0.0 <= now - getattr(self, "_hud_valid_t", -1e9) < HUD_LINGER_S \
                    and getattr(self, "_hud_alarm_t", -1e9) <= getattr(self, "_hud_valid_t", -1e9):
                return line                                     # linger
            if line is None and cand is not None and r_new < 3 and 0.0 <= now - since < HUD_GAP_S:
                return None                                     # quiet gap after the card left
            return cand
        except Exception:
            return cand

    def _keeps_death_lesson(self, kind: str) -> bool:
        """While I am dead, the death lesson keeps the card: a written message that the dead card
        does not show (DEAD_TEXT_KINDS) must not erase it (the card went blank before the respawn)."""
        try:
            game = self._game
            dead = bool(getattr(getattr(game, "me", None), "is_dead", False)) if game is not None else False
            return dead and getattr(self, "_text_kind", None) == "death_cause" and self._text_msg is not None \
                and kind not in DEAD_TEXT_KINDS
        except Exception:
            return False

    def _siege_alert(self, t: float) -> list[Alert]:
        """"Ta base est attaquée, défends !" (DANGER: beep + voice, even while dead) when a siege of
        my base starts (not for an ace: nobody to defend with). Never raises."""
        try:
            state = self._siege(t)[0]
            prev = getattr(self, "_siege_prev", None)
            self._siege_prev = state
            if state == "siege" and prev is None:
                from treeaicoach.voice_policy import SIEGE_KEY

                return [make_alert(AlertKind.PERSONAL_DANGER, Level.DANGER, t, key=SIEGE_KEY,
                                   text="Ta base est attaquée, défends !")]
        except Exception:
            log.debug("siege alert failed", exc_info=True)
        return []

    def _alarm_now(self, now: float) -> bool:
        """A gank warning / danger or a personal danger (2 v 1, low HP with an enemy on me) right now."""
        try:
            if any(lvl >= int(Level.WARNING) and now - t_ <= THREAT_HOLD_S for t_, lvl, _a in list(self._threat_hist)):
                return True
            return self._personal_word(now, self._game) is not None
        except Exception:
            return False

    def _note_stance(self, line: str | None, now: float) -> None:
        """Remember the push / retreat stance of what the player was just told (the shown line and
        any alarm, which means "recule")."""
        try:
            from treeaicoach.presenter import CONTRADICTION_S, line_stance

            mem = getattr(self, "_stance_mem", None)
            if mem is None:
                mem = self._stance_mem = []
            # the stance of a line counts from when it APPEARED (a "recule" card held 30 s does not
            # forbid "Plaque la tour : Darius est mort" for ever; the judge measures the same way)
            st = line_stance(line) if line else None
            if st is not None and line != getattr(self, "_stance_last_line", None):
                mem.append((now, st))
            self._stance_last_line = line
            if self._alarm_now(now):
                mem.append((now, "retreat"))
            mem[:] = [(t_, s_) for t_, s_ in mem if 0.0 <= now - t_ <= CONTRADICTION_S][-40:]
        except Exception:
            log.debug("stance memory failed", exc_info=True)

    def _no_contradiction(self, valid: list[str], now: float, urgent: bool) -> list[str]:
        """Drop the candidate lines whose stance (push / retreat) contradicts what was shown in the
        last :data:`presenter.CONTRADICTION_S` seconds (LESSONS 6). A danger line always passes."""
        try:
            from treeaicoach.presenter import CONTRADICTION_S, line_stance

            if urgent:
                return valid
            mem = [(t_, s_) for t_, s_ in getattr(self, "_stance_mem", None) or [] if 0.0 <= now - t_ <= CONTRADICTION_S]
            if self._alarm_now(now):
                mem.append((now, "retreat"))
            if not mem:
                return valid
            alarm = self._alarm_now(now)          # a gank / 2 v 1 right now: "recule" always passes
            out = []
            for v in valid:
                st = line_stance(v)
                if st is not None and not (alarm and st == "retreat") and any(s_ != st for _t, s_ in mem):
                    continue
                out.append(v)
            return out
        except Exception:
            return valid

    def _topic_seen(self, key: str, t: float) -> bool:
        """One toast per subject (voice_policy.topic_of): True when this topic was already shown in
        the last TOPIC_TOAST_S seconds (the toast is then dropped; the HUD line still updates).
        Danger / praise toasts are never deduplicated here. Records the topic otherwise."""
        try:
            from treeaicoach import voice_policy as vp

            topic = vp.topic_of(key)
            if topic is None:
                return False
            seen = getattr(self, "_topic_t", None)
            if seen is None:
                seen = self._topic_t = {}
            last = seen.get(topic)
            if last is not None and 0.0 <= t - last < vp.TOPIC_TOAST_S:
                return True
            seen[topic] = t
            return False
        except Exception:
            log.debug("toast topic failed", exc_info=True)
            return False

    def _toast(self, kind: str, title: str, subtitle: str, alias: str | None, key: str, t: float,
               urgency: int | None = None) -> bool:
        """Offer a message to the presenter; True if it reached the player (banner / HUD line).
        ``urgency``: overrides the urgency derived from ``kind`` (e.g. the answer to F8)."""
        q = self._toasts
        if q is None or not getattr(self._cfg, "toasts_enabled", True):
            return False
        if kind not in ("danger", "praise") and self._topic_seen(key, t):
            return False
        pr = getattr(self, "_presenter", None)
        if pr is not None:
            from treeaicoach import presenter as prs

            mk = prs.message_kind(kind, key)
            d = pr.offer(prs.Message(mk, subtitle or title, title,
                                     urgency=urgency if urgency is not None else {"danger": 3, "warning": 2}.get(kind, 1),
                                     topic=key,
                                     siege=str(key).startswith(("text:siege", "siege", "ace", "urgent:ace"))),
                         self._presenter_ctx(t))
            if d.channel == prs.DROP:
                return False
            if d.channel == prs.PANEL:
                if not self._keeps_death_lesson(mk):
                    self._text_msg = (t, subtitle or title)   # the ONE HUD line, no toast
                    self._text_kind = mk
                    return True
                return False
        icon = None
        if alias:
            skin = 0
            game = self._game
            p = game.player_by_alias(alias) if game is not None else None
            if p is not None:
                skin = p.skin_id
            icon = self._icon(alias, skin)
        q.push(kind, title, subtitle, icon=icon, key=key, t=t)
        return True

    def _presenter_ctx(self, t: float) -> Any:
        """Context of the presentation router (fight, gank threat, dead, siege, level). Never raises."""
        from treeaicoach.presenter import Context

        try:
            tac = self._tactics
            fight = bool(tac is not None and tac.in_fight())
            gank = any(lvl >= int(Level.WARNING) and t - t_ <= THREAT_HOLD_S for t_, lvl, _a in list(self._threat_hist))
            game = self._game
            dead = bool(getattr(getattr(game, "me", None), "is_dead", False)) if game is not None else False
            siege = self._siege(t)[0] is not None
            return Context(t=float(t), fight=fight, gank=gank, dead=dead, siege=siege,
                           skill=str(getattr(self._cfg, "skill_level", "intermediaire") or "intermediaire"))
        except Exception:
            return Context(t=float(t))
