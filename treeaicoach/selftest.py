"""Self-test (``TreeAICoach.exe --selftest``), also run by the Windows CI on the built exe.

:func:`run_selftest` checks, without the game and without network:

* the bundled resources (textures, champion icons, ONNX model, selftest images);
* the configuration round trip (in a temporary directory);
* the ONNX detector (precision / recall on ``assets/selftest``, both >= 0.8) when the model is
  bundled - otherwise the classic detector is used and the report says so;
* the classic detector on a rendered minimap;
* the minimap locator on a synthetic 1920x1080 screenshot;
* the Live Client parser on an embedded sample payload;
* the demo scenario, accelerated with a fake clock, through the REAL engine (``step()``):
  a DANGER gank alert inside ``GANK_WINDOW``, no gank alert before it, a fog estimate once the
  jungler walks into the fog, the objective timer, the macro coach (objective setup + "Darius
  est mort" tip), the praise of my solo kill with its toast (rendered top-centre, never over
  the minimap), no coaching chatter during the gank, a HUD insight, overlay renderers producing
  images;
* the post-game analysis + HTML report on a small record written by the real recorder;
* the voice engine initialisation (nothing is spoken unless ``voice=True``).

Returns 0 when everything passed, 1 otherwise, and writes a readable French + English report.
Runs in well under 60 s. Never raises.
"""

from __future__ import annotations

import json
import logging
import math
import os
import platform
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

log = logging.getLogger(__name__)

MIN_PRECISION = 0.8
MIN_RECALL = 0.8
MATCH_FRAC = 0.5          # a detection matches a label if centre distance < 0.5 * r_label
DEMO_FPS = 6.0

#: Minimal but complete Live Client payload (allgamedata) used by the parser check.
SAMPLE_PAYLOAD: dict[str, Any] = {
    "activePlayer": {"riotId": "Sylvain#EUW", "summonerName": "Sylvain#EUW", "currentGold": 1450.5,
                     "level": 9},
    "allPlayers": [
        {"championName": n, "rawChampionName": f"game_character_displayname_{a}", "team": team,
         "position": pos, "riotId": f"{rid}#EUW", "riotIdGameName": rid, "summonerName": f"{rid}#EUW",
         "isBot": False, "isDead": False, "respawnTimer": 0.0, "level": 9, "skinID": 0,
         "items": [{"itemID": 1055, "slot": 0}],
         "scores": {"kills": 1, "deaths": 0, "assists": 2, "creepScore": 80, "wardScore": 7.0},
         "summonerSpells": {"summonerSpellOne": {"displayName": s1, "rawDisplayName": f"x_{s1}"},
                            "summonerSpellTwo": {"displayName": "Saut éclair",
                                                 "rawDisplayName": "SummonerFlash"}}}
        for (n, a, team, pos, rid, s1) in (
            ("Garen", "Garen", "ORDER", "TOP", "Sylvain", "Téléportation"),
            ("Vi", "Vi", "ORDER", "JUNGLE", "Poings", "Châtiment"),
            ("Lux", "Lux", "ORDER", "MIDDLE", "Lumiere", "Embrasement"),
            ("Jinx", "Jinx", "ORDER", "BOTTOM", "Canon", "Soins"),
            ("Thresh", "Thresh", "ORDER", "UTILITY", "Lanterne", "Embrasement"),
            ("Darius", "Darius", "CHAOS", "TOP", "Hache", "Téléportation"),
            ("Lee Sin", "LeeSin", "CHAOS", "JUNGLE", "Moine", "Châtiment"),
            ("Ahri", "Ahri", "CHAOS", "MIDDLE", "Renarde", "Embrasement"),
            ("Caitlyn", "Caitlyn", "CHAOS", "BOTTOM", "Sherif", "Soins"),
            ("Nautilus", "Nautilus", "CHAOS", "UTILITY", "Ancre", "Embrasement"),
        )
    ],
    "events": {"Events": [{"EventID": 0, "EventName": "GameStart", "EventTime": 0.02},
                          {"EventID": 1, "EventName": "DragonKill", "EventTime": 600.0,
                           "DragonType": "Fire", "KillerName": "Poings#EUW", "Stolen": "False"}]},
    "gameData": {"gameMode": "CLASSIC", "gameTime": 754.3, "mapName": "Map11", "mapNumber": 11,
                 "mapTerrain": "Default"},
}


@dataclass
class CheckResult:
    """Outcome of one check."""

    name_fr: str
    name_en: str
    ok: bool = False
    critical: bool = True
    details: list[str] = field(default_factory=list)
    seconds: float = 0.0
    skipped: bool = False


class _Fail(Exception):
    """A failed expectation inside a check."""


def _expect(cond: bool, msg: str) -> None:
    if not cond:
        raise _Fail(msg)


def _out(text: str) -> None:
    """Print only when a console exists (the windowed exe has ``sys.stdout = None``)."""
    try:
        if sys.stdout is not None:
            sys.stdout.write(text + "\n")
            sys.stdout.flush()
    except Exception:
        pass


# ============================================================================ checks
def check_assets(res: CheckResult, ctx: dict[str, Any]) -> None:
    from treeaicoach.paths import asset_path

    textures = sorted(asset_path("minimap").glob("2dlevelminimap_*.png"))
    res.details.append(f"Textures de minimap / minimap textures : {len(textures)}")
    _expect(bool(textures), "aucune texture de minimap / no minimap texture")
    _expect(asset_path("minimap", "2dlevelminimap_base_baron1.png").is_file(),
            "texture de base absente / base texture missing")
    icons = list(asset_path("icons", "champions").glob("*.png"))
    res.details.append(f"Icônes de champions / champion icons : {len(icons)}")
    _expect(len(icons) >= 100, "icônes de champions manquantes / champion icons missing")
    model = asset_path("model", "minimap_detector.onnx")
    ctx["has_model"] = model.is_file() and asset_path("model", "model_meta.json").is_file()
    res.details.append("Modèle ONNX / ONNX model : " + ("présent / present" if ctx["has_model"]
                                                         else "absent (détecteur classique / classic detector)"))
    labels = asset_path("selftest", "labels.json")
    ctx["has_selftest_set"] = labels.is_file()
    res.details.append("Images d'autotest / selftest images : " + ("présentes / present" if labels.is_file()
                                                                   else "absentes / missing"))


def check_config(res: CheckResult, ctx: dict[str, Any]) -> None:
    from treeaicoach.config import Config, load_config, save_config

    with tempfile.TemporaryDirectory(prefix="treeaicoach_selftest_") as tmp:
        path = Path(tmp) / "config.json"
        cfg = Config()
        cfg.voice_rate = 3
        cfg.sensitivity = 1.3
        cfg.fog_mode = "all"
        cfg.manual_minimap_rect = {"screen_w": 1920, "screen_h": 1080, "x": 1650, "y": 810, "w": 260, "h": 260}
        save_config(cfg, path)
        back = load_config(path)
        _expect(back.to_dict() == cfg.validated().to_dict(), "aller-retour différent / round trip mismatch")
        path.write_text("{ corrompu", encoding="utf-8")
        _expect(load_config(path).to_dict() == Config().to_dict(),
                "fichier corrompu mal géré / corrupt file not handled")
    res.details.append("Sauvegarde, relecture et fichier corrompu OK / save, load and corrupt file OK")


def _match(dets: list[Any], icons: list[dict]) -> tuple[int, int, int]:
    """Greedy one-to-one matching: (true positives, detections, labels)."""
    gts = [(float(g["u"]), float(g["v"]), float(g["r"])) for g in icons]
    used = [False] * len(gts)
    tp = 0
    for d in sorted(dets, key=lambda d: -float(d.score)):
        best, best_d = -1, math.inf
        for i, (u, v, r) in enumerate(gts):
            if used[i]:
                continue
            dd = math.hypot(d.u - u, d.v - v)
            if dd < MATCH_FRAC * r and dd < best_d:
                best, best_d = i, dd
        if best >= 0:
            used[best] = True
            tp += 1
    return tp, len(dets), len(gts)


def check_onnx(res: CheckResult, ctx: dict[str, Any]) -> None:
    if not ctx.get("has_model"):
        res.skipped = True
        res.critical = False
        res.details.append("Modèle ONNX absent : l'application utilise le détecteur classique "
                           "/ no ONNX model: the app uses the classic detector")
        return
    import cv2

    from treeaicoach.detector import OnnxDetector
    from treeaicoach.paths import asset_path

    det = OnnxDetector()
    ctx["onnx_ok"] = True
    if not ctx.get("has_selftest_set"):
        res.details.append("Modèle chargé ; pas d'images d'autotest / model loaded; no selftest images")
        return
    entries = json.loads(asset_path("selftest", "labels.json").read_text(encoding="utf-8"))
    tp = nd = ng = 0
    times = []
    for e in entries:
        img = cv2.imread(str(asset_path("selftest", e["file"])), cv2.IMREAD_COLOR)
        _expect(img is not None, f"image illisible / unreadable image {e.get('file')}")
        t0 = time.perf_counter()
        dets = det.detect(img)
        times.append(time.perf_counter() - t0)
        a, b, c = _match(dets, list(e.get("icons", [])))
        tp, nd, ng = tp + a, nd + b, ng + c
    precision = tp / nd if nd else (1.0 if ng == 0 else 0.0)
    recall = tp / ng if ng else 1.0
    res.details.append(f"{len(entries)} images, {ng} icônes / icons : précision / precision {precision:.2f}, "
                       f"rappel / recall {recall:.2f}, {1000 * float(np.median(times)):.1f} ms/image")
    _expect(precision >= MIN_PRECISION and recall >= MIN_RECALL,
            f"précision ou rappel < {MIN_PRECISION} / precision or recall below {MIN_PRECISION}")


def _demo_frame(second: float = 36.0) -> tuple[np.ndarray, Any]:
    from treeaicoach.demo import DemoSource

    src = DemoSource(size=280)
    return src.render_at(second), src


def check_classic(res: CheckResult, ctx: dict[str, Any]) -> None:
    from treeaicoach.detector import ClassicDetector

    frame, src = _demo_frame(36.0)
    truth = [p for p in src.positions(36.0).values() if p[2]]
    det = ClassicDetector()
    t0 = time.perf_counter()
    dets = det.detect(frame)
    dt = time.perf_counter() - t0
    tp, _n, _g = _match(dets, [{"u": u, "v": v, "r": 0.047} for u, v, _vis in truth])
    res.details.append(f"{len(dets)} détections, {tp}/{len(truth)} champions retrouvés / found, "
                       f"{dt * 1000:.1f} ms")
    _expect(tp >= max(3, len(truth) // 2), "trop peu d'icônes détectées / too few icons detected")


def check_locator(res: CheckResult, ctx: dict[str, Any]) -> None:
    import cv2

    from treeaicoach.capture import Rect
    from treeaicoach.minimap_locator import MinimapLocator

    frame, _src = _demo_frame(10.0)
    W, H, side, margin = 1920, 1080, 256, 12
    rng = np.random.default_rng(7)
    screen = (rng.integers(20, 90, size=(H // 8, W // 8, 3), dtype=np.uint8))
    screen = cv2.resize(screen, (W, H), interpolation=cv2.INTER_CUBIC)
    x0, y0 = W - side - margin, H - side - margin
    cv2.rectangle(screen, (x0 - 6, y0 - 6), (x0 + side + 5, y0 + side + 5), (70, 110, 120), -1)
    screen[y0:y0 + side, x0:x0 + side] = cv2.resize(frame, (side, side), interpolation=cv2.INTER_AREA)
    t0 = time.perf_counter()
    loc = MinimapLocator().locate(screen, Rect(0, 0, W, H), side="auto")
    dt = time.perf_counter() - t0
    _expect(loc is not None, "minimap non trouvée / minimap not found")
    r = loc.rect
    err = max(abs(r.x - x0), abs(r.y - y0), abs(r.w - side), abs(r.h - side))
    res.details.append(f"Trouvée / found {r.x},{r.y} {r.w}x{r.h} (attendu / expected {x0},{y0} {side}x{side}), "
                       f"score {loc.score:.2f}, {dt * 1000:.0f} ms")
    _expect(err <= 0.06 * side, f"position imprécise / inaccurate position ({err} px)")


def check_live_client(res: CheckResult, ctx: dict[str, Any]) -> None:
    from treeaicoach.live_client import parse_allgamedata

    game = parse_allgamedata(json.loads(json.dumps(SAMPLE_PAYLOAD)), now=0.0)
    _expect(game is not None, "échec du parsing / parse failed")
    _expect(game.me is not None and game.me.champion_alias == "Garen", "joueur actif / active player")
    _expect(len(game.allies) == 4 and len(game.enemies) == 5, "équipes / teams")
    jg = game.enemy_jungler()
    _expect(jg is not None and jg.champion_alias == "LeeSin", "jungler ennemi / enemy jungler")
    _expect(game.is_summoners_rift, "carte / map")
    _expect(parse_allgamedata({"garbage": 1}) is None, "données invalides / invalid data")
    res.details.append(f"Moi / me : {game.me.champion_name} ({game.me.team}), jungler ennemi / enemy jungler : "
                       f"{jg.champion_name}, or / gold {game.current_gold:.0f}")


class _SilentVoice:
    """Voice stand-in collecting the spoken sentences."""

    backend = "selftest"

    def __init__(self) -> None:
        self.said: list[tuple[str, int]] = []

    def say(self, text: str, level: int = 1) -> None:
        self.said.append((text, int(level)))


def check_demo(res: CheckResult, ctx: dict[str, Any]) -> None:
    from treeaicoach.alerts import AlertKind, Level
    from treeaicoach.config import Config
    from treeaicoach.demo import DemoSource
    from treeaicoach.engine import CoachEngine, EngineState
    from treeaicoach.overlay_render import render_flash, render_hud, render_radar

    from treeaicoach import toasts as tst

    gank_kinds = {AlertKind.JUNGLER_APPROACH, AlertKind.ROAM_APPROACH, AlertKind.COLLAPSE}
    coach_kinds = {AlertKind.MACRO_TIP, AlertKind.PRAISE, AlertKind.SCOREBOARD}
    now = [0.0]
    src = DemoSource(size=280)
    voice = _SilentVoice()
    engine = CoachEngine(Config(), voice, frame_source=src, clock=lambda: now[0],
                         enable_hotkeys=False, manage_overlay=False)
    w0, w1 = src.GANK_WINDOW
    hide = getattr(src, "JUNGLER_HIDE_AT", 6.5)
    alerts: list[tuple[float, Any]] = []
    fog_seen_at: float | None = None
    overlay_state = None
    toast_state = None
    toasts_seen: list[tuple[float, Any]] = []
    insights: set[str] = set()
    worst = 0.0
    total = 0.0
    expected = dict(getattr(src, "EXPECTED", {}) or {})
    end_s = max([w1] + [b for _a, b in expected.values()])
    n = int(round(end_s * DEMO_FPS)) + 1
    for i in range(n):
        t = i / DEMO_FPS
        now[0] = t
        t0 = time.perf_counter()
        said = engine.step(t)
        dt = time.perf_counter() - t0
        total += dt
        if i > 0:
            worst = max(worst, dt)
        alerts += [(t, a) for a in said]
        if fog_seen_at is None and t > hide and engine.fog_tracker is not None and engine.fog_tracker.estimates():
            fog_seen_at = t
        if overlay_state is None and w0 + 4.0 <= t:
            overlay_state = engine.get_overlay_state()
        if i % 3 == 0:
            ost = engine.get_overlay_state()
            if ost.insight:
                insights.add(ost.insight)
            for tv in ost.toasts or []:
                if all(tv.toast.key != k.toast.key for _t, k in toasts_seen):
                    toasts_seen.append((t, tv))
                    if toast_state is None:
                        toast_state = ost
    st = engine.get_status()
    res.details.append(f"Détecteur / detector : {st.detector} ; {n} images, {1000 * total / n:.1f} ms/image en "
                       f"moyenne / on average (max {1000 * worst:.0f} ms)")
    for t, a in alerts:
        res.details.append(f"  {t:5.1f} s  [{Level(a.level).name}] {a.text}")
    early = [(t, a) for t, a in alerts if a.kind in gank_kinds and t < w0]
    danger = [(t, a) for t, a in alerts if a.kind in gank_kinds and a.level >= Level.DANGER and w0 <= t <= w1]
    _expect(st.state == EngineState.RUNNING, f"état du moteur / engine state {st.state.value}")
    _expect(not early, f"alerte de gank trop tôt / gank alert too early ({early[0][0]:.1f} s)" if early else "")
    _expect(bool(danger), f"pas d'alerte DANGER dans / no DANGER alert in {src.GANK_WINDOW}")
    _expect(fog_seen_at is not None, "pas d'estimation de brouillard / no fog estimate")
    res.details.append(f"Alerte DANGER à / DANGER alert at {danger[0][0]:.1f} s ; brouillard / fog estimate at "
                       f"{fog_seen_at:.1f} s")
    # ---- coaching layer: objective timer, macro tips, praise + toast, nothing during the gank
    for t, tv in toasts_seen:
        res.details.append(f"  {t:5.1f} s  [TOAST {tv.toast.kind}] {tv.toast.title} — {tv.toast.subtitle}")

    # spoken (voice policy) + written-only (HUD line / toast) messages
    written = list(getattr(engine, "text_messages", []) or [])
    for t, kind, text in written:
        res.details.append(f"  {t:5.1f} s  [ÉCRIT / TEXT {kind}] {text}")
    msgs = [(t, a.kind.value, a.text) for t, a in alerts] + [(t, k, x) for t, k, x in written]

    def first(kind: Any, lo: float, hi: float, needle: str = "") -> float | None:
        kv = getattr(kind, "value", kind)
        return next((t for t, k, x in msgs if k == kv and lo <= t <= hi and needle in x), None)
    if expected:
        win = expected.get("objective_soon", (0.0, end_s))
        _expect(first(AlertKind.OBJECTIVE_SOON, *win) is not None,
                f"pas de minuteur d'objectif / no objective timer alert in {win}")
        win = expected.get("macro_setup", (0.0, end_s))
        _expect(first(AlertKind.MACRO_TIP, *win) is not None,
                f"pas de conseil macro (objectif) / no macro objective tip in {win}")
        win = expected.get("macro_lane_dead", (0.0, end_s))
        _expect(first(AlertKind.MACRO_TIP, win[0], win[1], "mort") is not None,
                f"pas de conseil « adversaire mort » / no lane-opponent-dead macro tip in {win}")
        win = expected.get("praise", (0.0, end_s))
        _expect(any(a.kind == AlertKind.PRAISE and win[0] <= t <= win[1] for t, a in alerts),
                f"pas de félicitation vocale (solo kill) / no spoken praise in {win}")
        win = expected.get("toast", (0.0, end_s))
        praise_toasts = [(t, tv) for t, tv in toasts_seen if tv.toast.kind == "praise" and win[0] <= t <= win[1]]
        _expect(bool(praise_toasts), f"pas de toast de félicitation / no praise toast in {win}")
        d0 = danger[0][0]
        chatter = [(t, a) for t, a in alerts if a.kind in coach_kinds and d0 - 3.0 <= t <= d0 + 6.0]
        _expect(not chatter, f"conseil pendant le gank / coaching during the gank ({chatter[0][0]:.1f} s)"
                if chatter else "")
        _expect(bool(insights), "pas d'info macro sur le HUD / no HUD insight")
        res.details.append("Info HUD / HUD insight : « " + sorted(insights)[0] + " »")
        # the toast layer renders at the top-centre, never over the minimap
        scr, mm = (0, 0, 1920, 1080), (1640, 800, 270, 270)
        x, y, w, h = tst.toast_layer_rect(scr, mm)
        _expect(not (x < mm[0] + mm[2] and mm[0] < x + w and y < mm[1] + mm[3] and mm[1] < y + h)
                and y < 1080 * 0.25 and abs((x + w / 2) - 960) < 40, "toasts mal placés / toasts misplaced")
        views = [tv for _t, tv in praise_toasts[:1]]
        views = [tst.ToastView(v.toast, 1.0) for v in views]
        layer = tst.render_toast_layer(views, tst.scale_for_screen(scr))
        _expect(layer.ndim == 3 and layer.shape[2] == 4 and int(layer[..., 3].max()) > 0,
                "rendu de toast vide / empty toast render")
        res.details.append(f"Toast : {layer.shape[1]}x{layer.shape[0]} à / at ({x}, {y}) "
                           f"(minimap {mm[0]},{mm[1]})")
    _expect(overlay_state is not None, "pas d'état d'overlay / no overlay state")
    radar = render_radar(overlay_state, 256)
    hud = render_hud(overlay_state, 340)
    flash = render_flash(640, 360, 1.0, None)
    for name, img in (("radar", radar), ("HUD", hud), ("flash", flash)):
        _expect(isinstance(img, np.ndarray) and img.ndim == 3 and img.shape[2] == 4 and int(img[..., 3].max()) > 0,
                f"rendu {name} vide / empty {name} render")
    preview = engine.get_preview()
    _expect(preview is not None and preview.ndim == 3, "aperçu vide / empty preview")
    res.details.append(f"Overlay : radar {radar.shape[1]}x{radar.shape[0]}, HUD {hud.shape[1]}x{hud.shape[0]}, "
                       f"flash {flash.shape[1]}x{flash.shape[0]} ; F9 : « {engine.jungler_status_text()} »")


def check_report(res: CheckResult, ctx: dict[str, Any]) -> None:
    from treeaicoach.analysis import analyze_game
    from treeaicoach.demo import DemoSource
    from treeaicoach.recorder import GameRecorder
    from treeaicoach.report import render_report_html, write_report
    from treeaicoach.tracker import Tracker

    from treeaicoach.alerts import AlertKind, Level, make_alert
    from treeaicoach.scoreboard import ScoreboardAnalyzer

    src = DemoSource(size=128)
    with tempfile.TemporaryDirectory(prefix="treeaicoach_selftest_") as tmp:
        rec = GameRecorder(out_dir=Path(tmp))
        tracker = Tracker()
        board = ScoreboardAnalyzer()
        for k in range(0, 61, 2):
            s = float(k)
            game = src.game_info(s, s)
            rec.on_game_info(game, s)
            ident = [_Ident(u, v, alias) for alias, (u, v, vis) in src.positions(s).items() if vis]
            tracker.update(s, ident)
            rec.on_tracks(tracker, s, game.game_time)
            board.update(game, s, tracker=tracker)
            rec.on_scoreboard(board.summary().to_dict(), game.game_time)
            if k == 50:
                rec.on_alert(make_alert(AlertKind.PRAISE, Level.INFO, s, alias="Darius",
                                        text="Solo kill sur Darius, tu domines !", key="solo:2"), game.game_time)
        path = rec.finish()
        _expect(path is not None and path.is_file(), "enregistrement non écrit / record not written")
        record = json.loads(path.read_text(encoding="utf-8"))
        analysis = analyze_game(record)
        _expect(bool(analysis.get("ok", True)), f"analyse en erreur / analysis errors {analysis.get('errors')}")
        html = render_report_html(record, analysis)
        _expect("<html" in html.lower() and len(html) > 1000, "HTML invalide / invalid HTML")
        sb = analysis.get("scoreboard") or {}
        _expect(bool(sb.get("available")) and sb.get("my_matchup") is not None and sb.get("praise_count") == 1,
                "tableau des scores absent de l'analyse / scoreboard missing from the analysis")
        _expect("Tableau des scores" in html and "Solo kill sur Darius" in html,
                "tableau des scores absent du rapport / scoreboard missing from the report")
        _expect(bool(analysis.get("spoken_summary")), "pas de résumé vocal / no spoken summary")
        out = write_report(path)
        _expect(out is not None and Path(out).is_file(), "rapport non écrit / report not written")
        res.details.append(f"Enregistrement / record {path.stat().st_size} o, rapport / report "
                           f"{Path(out).stat().st_size} o, {len(analysis.get('tips') or [])} conseil(s) / tip(s)")
        res.details.append(f"Tab : {sb['my_matchup'].get('role_short')} {sb['my_matchup'].get('ally')} vs "
                           f"{sb['my_matchup'].get('enemy')} {sb['my_matchup'].get('gold_label')} ; résumé vocal / "
                           f"spoken summary : « {analysis.get('spoken_summary')} »")


@dataclass
class _Ident:
    """Minimal ``Identified`` for the report check (perfect identification)."""

    u: float
    v: float
    alias: str

    @property
    def det(self) -> Any:
        return self

    @property
    def r(self) -> float:
        return 0.047

    @property
    def score(self) -> float:
        return 1.0

    @property
    def relation(self) -> str:
        from treeaicoach.demo import CHAMPIONS

        return next((c.relation for c in CHAMPIONS if c.alias == self.alias), "enemy")

    @property
    def team(self) -> str | None:
        from treeaicoach.demo import CHAMPIONS

        return next((c.team for c in CHAMPIONS if c.alias == self.alias), None)

    @property
    def id_score(self) -> float:
        return 1.0


def check_voice(res: CheckResult, ctx: dict[str, Any]) -> None:
    from treeaicoach.voice import VoiceEngine

    speak = bool(ctx.get("voice"))
    v = VoiceEngine()
    try:
        if speak:
            v.start()
            v.wait_ready(5.0)
            v.say("Autotest de TreeAI Coach : la voix fonctionne.", 1)
            v.wait_idle(8.0)
        res.details.append(f"Moteur vocal / voice backend : {v.backend}"
                           + (" (phrase de test prononcée / test sentence spoken)" if speak else ""))
    finally:
        v.stop()


CHECKS: list[tuple[str, str, Callable[[CheckResult, dict[str, Any]], None], bool]] = [
    ("Ressources embarquées", "Bundled assets", check_assets, True),
    ("Configuration", "Configuration", check_config, True),
    ("Détecteur ONNX", "ONNX detector", check_onnx, True),
    ("Détecteur classique", "Classic detector", check_classic, True),
    ("Localisation de la minimap", "Minimap locator", check_locator, True),
    ("API Live Client", "Live Client parser", check_live_client, True),
    ("Scénario de démo (moteur complet)", "Demo scenario (full engine)", check_demo, True),
    ("Analyse et rapport d'après-partie", "Post-game analysis and report", check_report, True),
    ("Voix", "Voice", check_voice, True),
]


def run_selftest(out_path: Path | None = None, voice: bool = False) -> int:
    """Run every check; write the report to ``out_path`` (if given). 0 if OK, else 1. Never raises."""
    try:
        return _run(out_path, voice)
    except Exception:
        log.exception("Selftest crashed")
        try:
            if out_path is not None:
                Path(out_path).write_text("AUTOTEST : ERREUR INTERNE / INTERNAL ERROR\n" + traceback.format_exc(),
                                          encoding="utf-8")
        except Exception:
            pass
        return 1


def _run(out_path: Path | None, voice: bool) -> int:
    from treeaicoach import APP_NAME, __version__

    t_start = time.perf_counter()
    ctx: dict[str, Any] = {"voice": voice}
    results: list[CheckResult] = []
    for name_fr, name_en, fn, critical in CHECKS:
        res = CheckResult(name_fr, name_en, critical=critical)
        t0 = time.perf_counter()
        try:
            fn(res, ctx)
            res.ok = True
        except _Fail as exc:
            res.ok = False
            if str(exc):
                res.details.append(f"ÉCHEC / FAILED : {exc}")
        except Exception as exc:
            res.ok = False
            res.details.append(f"ERREUR / ERROR : {type(exc).__name__}: {exc}")
            res.details += ["    " + ln for ln in traceback.format_exc().rstrip().splitlines()[-6:]]
            log.exception("Selftest check %s failed", name_en)
        res.seconds = time.perf_counter() - t0
        results.append(res)
        _out(f"[{'OK' if res.ok else ('--' if not res.critical else 'ECHEC')}] {name_fr} / {name_en} "
             f"({res.seconds:.1f} s)")
    failed = [r for r in results if not r.ok and r.critical]
    elapsed = time.perf_counter() - t_start
    lines = [
        f"{APP_NAME} {__version__} — autotest / self-test",
        f"Date : {time.strftime('%Y-%m-%d %H:%M:%S')} ; Python {platform.python_version()} ; "
        f"{platform.system()} {platform.release()} ({'exe' if getattr(sys, 'frozen', False) else 'source'})",
        "",
    ]
    for r in results:
        tag = "OK   " if r.ok else ("SAUTÉ" if r.skipped else ("ÉCHEC" if r.critical else "AVERT"))
        if r.ok and r.skipped:
            tag = "SAUTÉ"
        lines.append(f"[{tag}] {r.name_fr} / {r.name_en}  ({r.seconds:.1f} s)")
        lines += [f"        {d}" for d in r.details]
    lines += ["", f"Durée totale / total time : {elapsed:.1f} s",
              ("RÉSULTAT : RÉUSSI / RESULT: PASSED" if not failed else
               f"RÉSULTAT : ÉCHEC ({len(failed)}) / RESULT: FAILED ({len(failed)}) : "
               + ", ".join(r.name_en for r in failed))]
    text = "\n".join(lines) + "\n"
    if out_path is not None:
        try:
            p = Path(out_path)
            if p.parent and not p.parent.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        except Exception:
            log.exception("Cannot write the selftest report to %s", out_path)
    for ln in lines[-2:]:
        _out(ln)
    log.info("Selftest %s in %.1f s", "passed" if not failed else "FAILED", elapsed)
    return 0 if not failed else 1


__all__ = ["run_selftest", "SAMPLE_PAYLOAD", "CheckResult"]


if __name__ == "__main__":  # pragma: no cover
    os.environ.setdefault("TREEAICOACH_HOME", tempfile.mkdtemp(prefix="treeaicoach_home_"))
    sys.exit(run_selftest(Path(sys.argv[1]) if len(sys.argv) > 1 else None))
