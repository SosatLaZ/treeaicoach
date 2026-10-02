"""Read side for the UI and the overlay: preview image, :meth:`get_overlay_state` (HUD card,
enemies, fog, toasts) and the F9 jungler status.

Mixin of :class:`treeaicoach.engine.CoachEngine` (split out of ``engine.py`` without any
behaviour change): the methods use the engine's state (``self._lock``, ``self._cfg`` ...),
created in ``CoachEngine.__init__``. Not meant to be used on its own.
"""

from __future__ import annotations

import dataclasses
import logging
import math
import time
from typing import Any

import cv2
import numpy as np

from treeaicoach import geometry
from treeaicoach.alerts import AlertKind, Level
from treeaicoach.capture import Rect
from treeaicoach.engine_base import (
    FLASH_DECAY_S,
    HOTKEY_DEBOUNCE_S,
    OVERLAY_MAX_AGE_S,
    OVERLAY_MIN_PERIOD_S,
    ROLE_NOTICE_S,
    THREAT_HOLD_S,
    TRIVIAL_BUY_AFTER_S,
    TRIVIAL_BUY_GOLD,
    siege_state,
)
from treeaicoach.fmtutil import finite_loose as _finite, seconds_fr
from treeaicoach.live_client import GameInfo, PlayerInfo

log = logging.getLogger("treeaicoach.engine")   # same logger as before the split


def overlay_cache_fresh(cache: tuple, now: float, frame_id: int) -> bool:
    """Is the cached overlay state ``(built_at, state, frame_id)`` still good at ``now``?

    The build (~3-4 ms of Python, on the OVERLAY thread) used to run at 12 Hz whatever happened;
    positions do not need it (the overlay predicts them at render time through ``state.predict``).
    Rebuilt: after a new analysis tick (at most :data:`OVERLAY_MIN_PERIOD_S`), at the same rate
    while something animates from the state (toasts sliding / counting down, danger flash fading,
    a fresh alert), and at least every :data:`OVERLAY_MAX_AGE_S` (per-second countdowns,
    "seen N s ago", text pushed from another thread)."""
    try:
        age = now - float(cache[0])
        if not 0.0 <= age < OVERLAY_MAX_AGE_S:
            return False
        if age < OVERLAY_MIN_PERIOD_S:
            return True
        old_fid = cache[2] if len(cache) > 2 else None
        if old_fid != frame_id:
            return False
        st = cache[1]
        la = getattr(st, "last_alert", None)
        animating = bool(getattr(st, "toasts", None)) or float(getattr(st, "flash", 0.0) or 0.0) > 0.0 \
            or bool(la and float(la[2]) < 4.5) or bool(getattr(st, "world", None))
        return not animating
    except Exception:
        return False


class OverlayStateMixin:
    """Read side for the UI and the overlay: preview image, :meth:`get_overlay_state` (HUD card,"""

    def get_preview(self) -> np.ndarray | None:
        """Annotated copy of the last minimap (detections, identities, me, fog), BGR. Never raises."""
        try:
            with self._lock:
                frame, identified, fid = self._frame, list(self._identified), self._frame_id
                cache = self._preview_cache
            if frame is None:
                return None
            if cache is not None and cache[0] == fid:
                return cache[1].copy()
            img = self._annotate(frame, identified)
            with self._lock:
                self._preview_cache = (fid, img)
            return img.copy()
        except Exception:
            self._err.exception("get_preview failed")
            return None

    def _annotate(self, frame: np.ndarray, identified: list[Any]) -> np.ndarray:
        img = frame.copy()
        h, w = img.shape[:2]
        fogs = self._fog.estimates() if self._fog is not None and not getattr(self._cfg, "safe_mode", False) else []
        for fe in fogs:
            region = getattr(fe, "region", None)
            if isinstance(region, np.ndarray) and region.ndim == 2 and region.any():
                m = cv2.resize(region.astype(np.uint8) * 255, (w, h), interpolation=cv2.INTER_LINEAR)
                sel = m > 127
                tint = img[sel].astype(np.float32) * 0.65 + np.array([40, 40, 200], np.float32) * 0.35
                img[sel] = tint.astype(np.uint8)
                cnts, _ = cv2.findContours((m > 127).astype(np.uint8), cv2.RETR_EXTERNAL,
                                           cv2.CHAIN_APPROX_SIMPLE)
                cv2.drawContours(img, cnts, -1, (60, 60, 235), 1, cv2.LINE_AA)
            lu, lv = fe.last_uv
            cv2.drawMarker(img, (int(lu * w), int(lv * h)), (60, 60, 235), cv2.MARKER_TILTED_CROSS,
                           max(6, w // 30), 2, cv2.LINE_AA)
        colors = {"enemy": (70, 70, 240), "ally": (235, 170, 60), "self": (60, 220, 250)}
        labels: list[tuple[int, int, int, str, tuple[int, int, int]]] = []
        for x in identified:
            det = getattr(x, "det", x)
            rel = getattr(x, "relation", getattr(det, "cls", "enemy"))
            col = colors.get(rel, (200, 200, 200))
            cx, cy = int(det.u * w), int(det.v * h)
            rad = max(4, int(det.r * w) + 2)
            cv2.circle(img, (cx, cy), rad, col, 3 if rel == "self" else 2, cv2.LINE_AA)
            label = getattr(x, "alias", None) or "?"
            if rel == "self":
                label = "moi"
            labels.append((cx, cy, rad, label, col))
        fs = max(0.3, w / 800.0)
        for cx, cy, rad, label, col in labels:
            (tw, th), _base = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, fs, 1)
            x0 = int(min(max(0, cx - tw // 2), w - tw - 2))
            y0 = cy + rad + th + 3
            if y0 > h - 2:
                y0 = cy - rad - 4
            sub = img[max(0, y0 - th - 2):min(h, y0 + 3), max(0, x0 - 2):min(w, x0 + tw + 2)]
            sub[:] = (sub.astype(np.uint16) * 2 // 7).astype(np.uint8)
            cv2.putText(img, label, (x0, y0), cv2.FONT_HERSHEY_SIMPLEX, fs, col, 1, cv2.LINE_AA)
        return img

    # ---------------------------------------------------------------- overlay state
    def _icon(self, alias: str | None, skin: int = 0) -> np.ndarray | None:
        if not alias:
            return None
        key = f"{alias}:{skin}"
        if key not in self._icons:
            icon = None
            db = self._champion_db()
            if db is not None:
                try:
                    icon = db.load_icon(alias, skin)
                except Exception:
                    icon = None
            if len(self._icons) > 40:
                self._icons.clear()
            self._icons[key] = icon
        return self._icons[key]

    def get_overlay_state(self) -> Any:
        """Immutable :class:`OverlayState` snapshot (None outside a game / overlay hidden); cached,
        see :func:`overlay_cache_fresh`."""
        try:
            now = self._clock()
            if self.overlay_paused(now):     # minimized / alt-tabbed: never draw over other apps
                return None
            with self._lock:
                if not self._overlay_visible or not self._in_game or self._game is None:
                    return None
                cache = self._overlay_cache
                fid = self._frame_id
                if cache is not None and overlay_cache_fresh(cache, now, fid):
                    return cache[1]
            state = self._build_overlay_state(now)
            with self._lock:
                self._overlay_cache = (now, state, fid)
            return state
        except Exception:
            self._err.exception("get_overlay_state failed")
            return None

    def _screen_rects(self) -> tuple[Rect | None, Rect | None]:
        if self._frame_source is None:
            return self._minimap_rect, self._window
        # demo / frame source: pretend the minimap sits at its usual place on the main screen
        if self._demo_rects is None:
            rects: tuple[Rect | None, Rect | None] = (None, None)
            try:
                from treeaicoach.capture import monitor_rects
                from treeaicoach.minimap_locator import fallback_rect

                mons = monitor_rects()
                if mons:
                    rects = (fallback_rect(mons[0], "right"), mons[0])
            except Exception:
                log.debug("No monitor information", exc_info=True)
            self._demo_rects = rects          # computed once (monitor enumeration is not free)
        return self._demo_rects

    def _build_overlay_state(self, now: float) -> Any:
        from treeaicoach.overlay_render import EnemyView, OverlayState

        with self._lock:
            game = self._game
            game_t = self._game_t
            last_alert, last_alert_t = self._last_alert, self._last_alert_t
            hist = list(self._threat_hist)
            last_danger = self._last_danger_t
        tracker = self._tracker
        cfg = self._cfg
        me = tracker.me() if tracker is not None else None
        me_uv = me.position() if me is not None and me.visible else (
            me.position() if me is not None and now - me.last_seen < 3.0 else None)
        jungler = game.enemy_jungler() if game is not None else None
        jungler_alias = jungler.champion_alias if jungler is not None else None
        enemies: list[Any] = []
        roster: list[PlayerInfo] = list(game.enemies) if game is not None else []
        seen_keys: set[str] = set()
        for p in roster[:5]:
            tr = tracker.get(p.champion_alias) if (tracker is not None and p.champion_alias) else None
            enemies.append(self._enemy_view(EnemyView, p.champion_alias, p.champion_name, p.skin_id, tr,
                                            me_uv, now, p.champion_alias == jungler_alias))
            if tr is not None:
                seen_keys.add(tr.key)
        if tracker is not None:
            for tr in tracker.enemies(visible_only=True):
                if tr.key not in seen_keys and len(enemies) < 10:
                    enemies.append(self._enemy_view(EnemyView, tr.alias, tr.alias or "?", 0, tr, me_uv,
                                                    now, False))
        dead = {p.champion_alias for p in roster if getattr(p, "is_dead", False)}
        for v in enemies:              # dead enemies: no ghost on the map (HUD row shows the timer)
            if v.alias in dead and hasattr(v, "dead"):
                v.dead = True
        allies, roles = self._overlay_allies_roles(EnemyView, game, tracker, now)
        for v in enemies:
            v.role = roles.get(v.key) or roles.get(v.alias or "")
        level = max((lvl for t_, lvl, _a in hist if now - t_ <= THREAT_HOLD_S), default=0)
        top = max((a for t_, lvl, a in hist if now - t_ <= THREAT_HOLD_S and lvl == level),
                  key=lambda a: a.t, default=None)
        siege, _siege_line = self._siege(now)
        if siege is not None:           # base siege / ace dominate: never "SÛR" while the base falls
            level = max(level, int(Level.DANGER))
        personal = self._personal_word(now, game)     # 2 v 1 / low HP with an enemy on me (danger.py)
        if siege == "ace":
            text = "DANGER — ACE"
        elif siege == "siege":
            text = "DANGER — TA BASE EST ATTAQUÉE"
        elif level >= Level.WARNING and self._grouped(now, me_uv):
            # a gank alarm with my team around me is a team fight: "COMBAT", not "GANK, recule"
            level, text = int(Level.WARNING), "ATTENTION — COMBAT"
        elif self._team_fight_now() and (level >= Level.WARNING or self._team_fight_now() != "retreat"):
            # a team fight going on (fight tracker: allies engaged): never the word "GANK" in a 5 v 5;
            # a lost fight (RECULE call) stays red
            if self._team_fight_now() == "retreat":
                level, text = int(Level.DANGER), "DANGER — COMBAT PERDU"
            else:
                level, text = int(Level.WARNING), "ATTENTION — COMBAT"
        elif level >= Level.DANGER:
            text = "DANGER — GANK !"
        elif personal is not None and personal[0] >= Level.DANGER:
            level, text = int(Level.DANGER), f"DANGER — {personal[1]}"
        elif level == Level.WARNING:
            who = self._display_name(game, top.alias) if top is not None else None
            text = f"ATTENTION — {who.upper()} ARRIVE" if who else "ATTENTION — ENNEMI PROCHE"
        elif personal is not None:
            level, text = int(Level.WARNING), f"ATTENTION — {personal[1]}"
        else:
            text = "SÛR"
        flash = 0.0
        tac = self._tactics
        fighting = tac is not None and tac.in_fight()
        if cfg.danger_flash and last_danger is not None and 0.0 <= now - last_danger < FLASH_DECAY_S and not fighting:
            flash = float(1.0 - (now - last_danger) / FLASH_DECAY_S)
        la = None
        if last_alert is not None and last_alert_t is not None:
            la = (last_alert.text, int(last_alert.level), max(0.0, now - last_alert_t))
        minimap_rect, screen_rect = self._screen_rects()
        guides, world = self._ward_overlay(now, tac, minimap_rect, screen_rect, me_uv)
        tip = self._hud_line(now)
        gt = (_finite(game.game_time) or 0.0) + min(max(0.0, now - game_t), 3.0) if game else None
        fogs = self._fog.estimates() if self._fog is not None and not getattr(self._cfg, "safe_mode", False) else []
        return OverlayState(
            minimap_rect=minimap_rect, screen_rect=screen_rect, me_uv=me_uv,
            my_team=game.my_team if game is not None else None,
            enemies=enemies, fogs=fogs, threat_level=int(level), threat_text=text, last_alert=la,
            objectives=self._objectives.states() if self._objectives is not None else [],
            game_time=gt, warn_radius=cfg.effective_warn_radius(),
            danger_radius=cfg.effective_danger_radius(), flash=flash,
            jungler_line=self._jungler_line(game, jungler, now),
            hint=self._reminders.hint() if self._reminders is not None else None,
            insight=(self._coach.insight() if self._coach is not None else None) or self._scoreboard_hud_line(),
            tip=tip,
            stance=getattr(self._stance.current(), "level", None) if self._stance is not None else None,
            stance_reason=getattr(self._stance.current(), "reason", None) if self._stance is not None else None,
            show_allies=bool(getattr(cfg, "overlay_show_allies", False)),
            show_roles=bool(getattr(cfg, "overlay_show_roles", False)) or bool(getattr(cfg, "layer_roles", False)),
            show_ghosts=bool(getattr(cfg, "overlay_show_ghosts", False)) or bool(getattr(cfg, "layer_ghosts", False)),
            show_last_seen=bool(getattr(cfg, "overlay_show_last_seen", True)),
            hud_detailed=bool(getattr(cfg, "hud_detailed", False)),
            me_icon=self._icon(game.me.champion_alias, game.me.skin_id) if game and game.me else None,
            allies=allies, roles=roles,
            toasts=self._overlay_toasts(now),
            guides=guides, world=world,
            phase=tac.phase() if tac is not None else None,
            role_notice=self._role_notice(now),
            tip_curated=True,               # _hud_line: the final card line (gauge line, no contradiction)
            **self._timer_fields(game, cfg),
            **self._hud_card_fields(game, me_uv, tip, now),
            **self._prediction_fields(me, game, game_t, now),
        )

    def _timer_fields(self, game: Any, cfg: Any) -> dict[str, Any]:
        """Timers column of the minimap layer (overlay_render.timer_rows): the toggle, the Baron /
        Elder buffs (objectives.ObjectiveTimers.buffs) and the game time each dead enemy respawns
        (Live Client ``isDead`` / ``respawnTimer``, the Tab screen's data). Never raises."""
        out: dict[str, Any] = {"show_timers": bool(getattr(cfg, "overlay_timers", True))}
        try:
            obj = self._objectives
            out["buffs"] = obj.buffs() if obj is not None and hasattr(obj, "buffs") else []
            base = _finite(game.game_time) if game is not None else None
            if base is not None:
                out["enemy_respawns"] = [float(base) + float(p.respawn_timer) for p in game.enemies
                                         if getattr(p, "is_dead", False)
                                         and (_finite(getattr(p, "respawn_timer", 0.0)) or 0.0) > 0.0]
        except Exception:
            log.debug("timer fields failed", exc_info=True)
        return out

    def _grouped(self, now: float, me_uv: Any) -> bool:
        """>= 2 visible allies next to me and at least as many of us as of them (a team fight)."""
        try:
            from treeaicoach.voice_policy import GROUPED_MIN, GROUPED_R

            tr = self._tracker
            if tr is None or me_uv is None:
                return False
            me = tr.me()
            al = [a for a in tr.allies(visible_only=True) if a is not me and a.position() is not None
                  and geometry.dist(a.position(), me_uv) < GROUPED_R]
            en = [e for e in tr.enemies(visible_only=True) if e.position() is not None
                  and geometry.dist(e.position(), me_uv) < GROUPED_R]
            return len(al) >= GROUPED_MIN and len(al) + 1 >= len(en)
        except Exception:
            return False

    def _personal_word(self, now: float, game: Any) -> tuple[int, str] | None:
        """``(level, short word)`` of the personal danger (danger.py) right now: ``(2, "2 CONTRE
        1")`` / ``(2, "PEU DE VIE")`` while the "Recule" condition holds, ``(1, ...)`` for a low HP
        or outnumbered warning; None when calm, dead or stale. Never raises."""
        try:
            pd = getattr(self, "_danger", None)
            st = pd.state() if pd is not None else None
            if st is None or game is None or bool(getattr(getattr(game, "me", None), "is_dead", False)):
                return None
            lvl = int(getattr(st, "level", 0) or 0)
            if lvl <= 0 or now - float(getattr(st, "t", now)) > 1.5:
                return None
            reason = str(getattr(st, "reason", "") or "")
            if getattr(st, "rule", None) == "recall":
                return lvl, "RAPPEL EN DANGER"      # card line: "Annule ton rappel : Lux peut l'interrompre"
            if "contre" in reason:
                return lvl, reason.upper()
            foes = list(getattr(st, "foes", ()) or ())
            n = sum(1 for f in foes if float(getattr(f, "d", 1.0)) < 0.12)
            allies = int(getattr(st, "allies_near", 0) or 0)
            if n >= 2 and n > allies + 1:
                return lvl, f"{n} CONTRE {allies + 1}"
            hp = getattr(st, "hp", None)
            if lvl >= 2 or (n >= 1 and hp is not None and float(hp) < 0.4):
                return lvl, "PEU DE VIE"
            return None                 # a one-tick written warning (lane spike...) is no state
        except Exception:
            return None

    def _prediction_fields(self, me: Any, game: Any = None, game_t: float = 0.0,
                           now: float = 0.0) -> dict[str, Any]:
        """``predict`` / ``me_key`` (render-time positions) and ``me_dead`` / ``respawn_s`` (HUD
        dead state) of the overlay state, when the renderer supports them."""
        try:
            from treeaicoach.overlay_render import OverlayState

            names = {f.name for f in dataclasses.fields(OverlayState)}
            out: dict[str, Any] = {}
            if "predict" in names and self._motion is not None:
                out["predict"] = self.predict_positions
            if "me_key" in names:
                out["me_key"] = me.key if me is not None else None
            p = getattr(game, "me", None)
            if "me_dead" in names and p is not None and bool(getattr(p, "is_dead", False)):
                out["me_dead"] = True
                rt = _finite(getattr(p, "respawn_timer", None))
                if rt is not None and "respawn_s" in names:
                    out["respawn_s"] = max(0.0, rt - max(0.0, now - game_t))
            return out
        except Exception:
            return {}

    def _team_fight_now(self) -> str | None:
        """``"retreat"`` / ``"engage"`` / ``"fight"`` while the fight tracker sees a team fight
        (allies engaged around me), else None. Never raises."""
        try:
            tac = self._tactics
            if tac is None or not tac.in_fight():
                return None
            fs = tac.fight.state()
            return str(getattr(fs, "call", None) or "fight")
        except Exception:
            return None

    def _hud_card_fields(self, game: Any, me_uv: Any, tip: str | None, now: float) -> dict[str, Any]:
        """HUD v3 card extras: gauge (+ reason, since), advice tone + fade start, item chip (in
        base), AI counter. Times are converted to ``time.monotonic`` (the overlay's clock). Never raises."""
        out: dict[str, Any] = {}
        try:
            to_mono = time.monotonic() - now
            g = self._gauge.current() if self._gauge is not None else None
            if g is not None:
                out.update(gauge=int(g.step), gauge_reason=g.reason or None, gauge_since=float(g.since) + to_mono)
            if tip != self._hud_tip_prev:
                from treeaicoach.engine_coaching import _ticking

                ticking = tip is not None and self._hud_tip_prev is not None and _ticking(tip, self._hud_tip_prev)
                self._hud_tip_prev = tip
                if not ticking:              # a countdown ticking is the same line (same age, no fade)
                    self._hud_tip_since = now
                    self._hud_tip_careful = g is not None and int(g.step) <= -1
            if tip:
                out.update(tip_tone=self._tip_tone(tip), tip_since=self._hud_tip_since + to_mono,
                           card_careful=bool(getattr(self, "_hud_tip_careful", False)))
            in_base = False
            if me_uv is not None and game is not None:
                z = geometry.classify_zone(*me_uv)
                in_base = geometry.is_base(z) and geometry.zone_owner(z) == game.my_team
            out["in_base"] = bool(in_base)
            adv = getattr(self, "_item_adv", None)
            rec = adv.current() if adv is not None and getattr(self._cfg, "item_advice", True) else None
            if rec is not None and not self._trivial_component_buy(rec, game, now):
                names = list(getattr(rec, "buy_now_names", ()) or ())
                out["item_hint"] = "Achète " + (" + ".join(names[:2]) if names and not rec.completes
                                                else rec.item_name)
            out["ai_counter"] = self.ai_budget_text() or None
            res = self._role_resolver      # compact HUD: objective note only for the objectives my role plays
            role = res.my_role() if res is not None and hasattr(res, "my_role") else None
            if role is None and game is not None and game.me is not None:
                role = getattr(game.me, "position", None) or None
            out["my_role"] = str(role).upper() if role else None
        except Exception:
            log.debug("HUD card fields failed", exc_info=True)
        return out

    def _siege(self, now: float) -> tuple[str | None, str | None]:
        """Current base-siege / ace state (see :func:`siege_state`). Never raises. Memoized per
        ``now`` (the overlay state build and the HUD line ask it several times at the same time)."""
        game = self._game
        if game is None:
            return None, None
        memo = getattr(self, "_siege_memo", None)
        if memo is not None and memo[0] == now and memo[1] is game and memo[2] == self._frame_id:
            return memo[3]
        out = self._siege_now(now, game)
        self._siege_memo = (now, game, self._frame_id, out)
        return out

    def _siege_now(self, now: float, game: Any) -> tuple[str | None, str | None]:
        try:
            gt = (_finite(game.game_time) or 0.0) + min(max(0.0, now - self._game_t), 3.0)
            n = 0
            tr = self._tracker
            if tr is not None:
                for e in tr.enemies(visible_only=False):
                    seen = getattr(e, "last_seen", None)
                    if seen is not None and now - seen > 5.0:    # cheap test first: position() is costly
                        continue
                    pos = e.position()
                    if pos is None or now - e.last_seen > 5.0:
                        continue
                    z = geometry.classify_zone(*pos)
                    if geometry.is_base(z) and geometry.zone_owner(z) == game.my_team:
                        n += 1
            return siege_state(game, gt, n)
        except Exception:
            return None, None

    def _trivial_component_buy(self, rec: Any, game: Any, now: float) -> bool:
        """After 20:00, a lone cheap component (< 500 gold, e.g. "Épée longue" with a near-complete
        build) that does not complete an item is not worth the HUD chip (real screenshot, 25:08)."""
        try:
            if getattr(rec, "completes", False) or game is None:
                return False
            gt = (_finite(game.game_time) or 0.0) + min(max(0.0, now - self._game_t), 3.0)
            if gt < TRIVIAL_BUY_AFTER_S:
                return False
            ids = list(getattr(rec, "buy_now", ()) or ())
            if not ids:
                return False
            from treeaicoach.itemization import load_items

            items = load_items()
            return all(i in items and int(items[i].gold) < TRIVIAL_BUY_GOLD for i in ids)
        except Exception:
            return False

    def _role_notice(self, now: float) -> str | None:
        """"Rôle détecté : MID (échange de voie)" for 20 s after a lane swap is detected. Never raises."""
        try:
            from treeaicoach.roles import ROLE_SHORT

            res = self._role_resolver
            sw = res.my_swap() if res is not None and hasattr(res, "my_swap") else None
            if sw is None or not (0.0 <= now - float(sw[1]) <= ROLE_NOTICE_S):
                return None
            return f"Rôle détecté : {ROLE_SHORT.get(sw[0], sw[0])} (échange de voie)"
        except Exception:
            return None

    def _overlay_toasts(self, now: float) -> list:
        """Toasts of the overlay, the director's big banner (live fight decision) on top."""
        views = list(self._toasts.active(now)) if self._toasts is not None else []
        tac = self._tactics
        if tac is None or not getattr(self._cfg, "toasts_enabled", True):
            return views
        try:
            b = tac.banner(now)
            pr = getattr(self, "_presenter", None)
            if b is not None and pr is not None:
                ident = (str(getattr(b, "style", "")), str(getattr(b, "title", "")), getattr(b, "since", None))
                if not pr.banner_ok(ident[0], ident, self._presenter_ctx(now)):
                    b = None
            if b is not None:
                from treeaicoach.toasts import banner_view

                v = banner_view(b, now)
                if v is not None:
                    views = [v] + views[:1]
        except Exception:
            log.debug("banner view failed", exc_info=True)
        return views

    def _scoreboard_hud_line(self) -> str | None:
        try:
            s = self.scoreboard_summary()
            line = s.hud_line() if s is not None else None
            wp = self.win_probability() if getattr(self._cfg, "win_prob_hud", True) else None
            if wp is not None:
                line = f"{line} · victoire {int(round(100 * wp))} %" if line else f"Victoire {int(round(100 * wp))} %"
            return line
        except Exception:
            return None

    def _overlay_allies_roles(self, cls: Any, game: GameInfo | None, tracker: Any,
                              now: float) -> tuple[list[Any], dict[str, str]]:
        """Allied views (roster order, then anonymous visible allies) + alias -> role map. Never raises."""
        allies: list[Any] = []
        roles: dict[str, str] = {}
        try:
            # resolved roles first (roles.RoleResolver: observed lanes beat the champ select
            # position after a lane swap), the Riot position only as a fallback
            resolver = getattr(self, "_role_resolver", None) or getattr(self, "_roles", None)
            extra = resolver.roles() if callable(getattr(resolver, "roles", None)) else resolver
            if isinstance(extra, dict):
                for k, info in extra.items():
                    role = getattr(info, "role", info)
                    if k and isinstance(role, str) and role:
                        roles[str(k)] = role
            for p in (game.all_players() if game is not None else []):
                if p.champion_alias and getattr(p, "position", "") and not roles.get(p.champion_alias):
                    roles[p.champion_alias] = str(p.position)
            seen: set[str] = set()
            for p in (list(game.allies) if game is not None else [])[:4]:
                tr = tracker.get(p.champion_alias) if (tracker is not None and p.champion_alias) else None
                v = self._enemy_view(cls, p.champion_alias, p.champion_name, p.skin_id, tr, None, now, False)
                v.relation, v.role = "ally", roles.get(p.champion_alias)
                allies.append(v)
                if tr is not None:
                    seen.add(tr.key)
            if tracker is not None and hasattr(tracker, "allies"):
                for tr in tracker.allies(visible_only=True):
                    if tr.key not in seen and len(allies) < 8:
                        v = self._enemy_view(cls, tr.alias, tr.alias or "?", 0, tr, None, now, False)
                        v.relation, v.role = "ally", roles.get(tr.alias or "")
                        allies.append(v)
        except Exception:
            log.debug("overlay allies / roles failed", exc_info=True)
        return allies, roles

    def _enemy_view(self, cls: Any, alias: str | None, name: str, skin: int, tr: Any,
                    me_uv: tuple[float, float] | None, now: float, is_jungler: bool) -> Any:
        uv = tr.position() if tr is not None else None
        visible = bool(tr.visible) if tr is not None else False
        ago = max(0.0, now - tr.last_seen) if tr is not None else None
        vel = tr.velocity() if tr is not None and visible else None
        approaching = False
        if visible and uv is not None and me_uv is not None and vel is not None:
            dx, dy = me_uv[0] - uv[0], me_uv[1] - uv[1]
            d = math.hypot(dx, dy)
            if d > 1e-6:
                approaching = (vel[0] * dx + vel[1] * dy) / d > 0.006 and d < 0.3
        view = cls(key=tr.key if tr is not None else (alias or "?"), alias=alias, name=name or (alias or "?"),
                   visible=visible, uv=uv, last_seen_ago=ago, is_jungler=is_jungler,
                   approaching=approaching, icon=self._icon(alias, skin), velocity=vel)
        if tr is not None:     # freshness (pipeline v2): stale / stacked / anonymous -> drawn as a ghost
            try:
                view.age = ago
                view.stacked = getattr(tr, "stacked_with", None) is not None
                view.confidence = 1.0 if tr.alias else 0.4
            except Exception:
                pass
        return view

    @staticmethod
    def _display_name(game: GameInfo | None, alias: str | None) -> str | None:
        if not alias:
            return None
        if game is not None:
            p = game.player_by_alias(alias)
            if p is not None and p.champion_name:
                return p.champion_name
        return alias

    def _jungler_line(self, game: GameInfo | None, jungler: PlayerInfo | None, now: float) -> str | None:
        if game is None or jungler is None:
            return None
        name = jungler.champion_name or jungler.champion_alias
        if bool(getattr(jungler, "is_dead", False)):     # Live Client: never "vu il y a 55 s" while dead
            rt = _finite(getattr(jungler, "respawn_timer", None))
            left = None if rt is None else max(0.0, rt - max(0.0, now - self._game_t))
            return f"Jungler : {name} — mort ({int(math.ceil(left))} s)" if left else f"Jungler : {name} — mort"
        tr = self._tracker.get(jungler.champion_alias) if self._tracker is not None else None
        if tr is None or tr.position() is None:
            return f"Jungler : {name} — pas encore vu"
        zone = geometry.zone_name_fr(geometry.classify_zone(*tr.position()), game.my_team)
        if tr.visible:
            return f"Jungler : {name} — visible, {zone}" if zone else f"Jungler : {name} — visible"
        ago = int(max(0.0, now - tr.last_seen))
        when = ("vu à l'instant" if ago < 2 else f"vu il y a {ago} s" if ago < 60
                else f"vu il y a {ago // 60}:{ago % 60:02d}")
        return f"Jungler : {name} — {when}, {zone}" if zone else f"Jungler : {name} — {when}"

    def jungle_intel(self) -> Any:
        """Enemy jungler Tab intel (``jungle_intel.JungleIntel``: farming side, recall, text
        line), or None. Never raises."""
        try:
            ji = self._jungle_intel
            return ji.state() if ji is not None and self._in_game else None
        except Exception:
            return None

    # ---------------------------------------------------------------- F9
    def jungler_status_text(self) -> str:
        """French answer to "where is the enemy jungler?" (F9). Never raises."""
        try:
            with self._lock:
                game, in_game = self._game, self._in_game
            if game is None or not in_game:
                return "Pas de partie en cours."
            jungler = game.enemy_jungler()
            if jungler is None:
                return "Jungler ennemi inconnu."
            name = jungler.champion_name or jungler.champion_alias or "Le jungler ennemi"
            tr = self._tracker.get(jungler.champion_alias) if self._tracker is not None else None
            pos = tr.position() if tr is not None else None
            if pos is None:
                return "Jungler ennemi pas encore vu."
            zone = geometry.classify_zone(*pos)
            if tr.visible:
                label = geometry.zone_label_fr(zone, game.my_team)
                return f"{name} est visible, {label}." if label else f"{name} est visible."
            ago = int(round(max(0.0, self._clock() - tr.last_seen)))
            where = geometry.zone_name_fr(zone, game.my_team)
            if ago >= 120:
                head = f"{name} vu il y a plus de 2 minutes"
            else:
                head = f"{name} vu il y a {seconds_fr(ago)}"
            return f"{head}, {where}." if where else f"{head}."
        except Exception:
            log.exception("jungler_status_text failed")
            return "Position du jungler ennemi inconnue."

    def speak_jungler_status(self) -> None:
        """Hotkey callback: speak :meth:`jungler_status_text` (debounced). Never raises."""
        try:
            now = self._clock()
            if now - self._last_where_t < HOTKEY_DEBOUNCE_S:
                return
            self._last_where_t = now
            text = self.jungler_status_text()
            self._say(text, 1)
            with self._lock:
                self._recent.append((None, text, 0, AlertKind.JUNGLER_WHERE.value))
        except Exception:
            log.exception("speak_jungler_status failed")
