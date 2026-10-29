"""In-game analysis report ("Rapport d'analyse"): what happened in the LAST 60 s, in one small zip.

The forward 60 s diagnostic (:mod:`treeaicoach.diag`) only sees what comes after the key press; when
something just went wrong ("un ennemi affiché faux", "il a spammé", "le shop n'est pas vu") the player
wants to send what was ALREADY on screen. :func:`engine_tick` (analysis thread, ~1 Hz) keeps a rolling
buffer in memory: the minimap crop as JPEG + the detections / tracks of that frame (60 samples, 1 fps),
plus a light performance sample every 5 s. Read-only taps record the threat gate's decisions (said /
held / dropped) and the Whisper transcripts. :func:`request` (hotkey ``Ctrl+F8`` with the diagnostic,
button Avancé > « Rapport d'analyse ») writes ``<user data>/reports/rapport_AAAAMMJJ_HHMMSS.zip``
(<= :data:`MAX_BYTES`):

* ``frames/NNN.jpg`` + ``frames.json`` (per frame: detections u / v / r / score / class / ids,
  tracks, Live Client game time);
* ``screen.jpg`` (one full game window capture: the shop when it is open);
* ``state.json``: Live Client roster (champion names, teams, roles, levels: no Riot ID / puuid),
  ranks shown (:mod:`treeaicoach.player_intel`, by champion), shop / item card / item set state,
  voice (Whisper engine loaded?, status, last 10 transcripts and which engine), threat gate
  decisions with reasons, last 30 coach lines with their rule, CPU per thread / fps / tick, app
  version and settings (no key, token, name, path).

Everything goes through :func:`scrub` (no puuid / Riot ID / summoner name / token). Memory only until
the user asks; nothing leaves the PC. ``tools/analyze_report.py`` prints a short diagnosis of a
report. Never raises.
"""

from __future__ import annotations

import collections
import dataclasses
import io
import json
import logging
import re
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

BUFFER_S = 60                    # seconds of minimap kept (1 fps)
SAMPLE_EVERY_S = 1.0
PERF_EVERY_S = 5.0
MAX_BYTES = 6 * 1024 * 1024      # the zip stays below this (crops thinned out if needed)
CROP_JPEG_Q = 90
SCREEN_JPEG_Q = 85
SCREEN_MAX_W = 2560
SCREEN_WAIT_S = 3.0              # the full screenshot is taken by the analysis thread: wait that long for it
COACH_LINES = 30
TRANSCRIPTS = 10
GATE_DECISIONS = 120
REPORT_DIR = "reports"

#: keys never written (ids, names, secrets) - matched on the key name, anywhere in the report
_SECRET_KEY = re.compile(r"(?i)puuid|summoner|riot|game_?name|tag_?line|token|api_?key|secret|passw|account|"
                         r"webhook|e-?mail|guild|channel_id|user_?id|discord_id|license|cookie|auth")
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_PUUID = re.compile(r"\b[A-Za-z0-9_-]{60,}\b")
_RIOT_ID = re.compile(r"[^\s#\"]{2,16}#[A-Za-z0-9]{2,5}\b")


def scrub(x: Any, depth: int = 0) -> Any:
    """JSON-able copy of ``x`` without any id / name / secret (keys dropped, strings masked)."""
    try:
        if depth > 12:
            return None
        if x is None or isinstance(x, bool):
            return x
        if isinstance(x, int):
            return int(x)
        if isinstance(x, float):
            return round(x, 4) if x == x and abs(x) != float("inf") else None
        if isinstance(x, str):
            s = x[:400]
            s = _UUID.sub("<id>", s)
            s = _PUUID.sub("<id>", s)
            s = _RIOT_ID.sub("<joueur>", s)
            try:
                from treeaicoach.diag import _mask_paths
                s = _mask_paths(s)
            except Exception:
                pass
            return s
        try:
            import numpy as np
            if isinstance(x, np.generic):
                return scrub(x.item(), depth)
            if isinstance(x, np.ndarray):
                return None if x.size > 64 else scrub(x.tolist(), depth + 1)
        except Exception:
            pass
        if isinstance(x, dict):
            return {str(k): scrub(v, depth + 1) for k, v in x.items() if not _SECRET_KEY.search(str(k))}
        if isinstance(x, (list, tuple, set, frozenset, collections.deque)):
            return [scrub(v, depth + 1) for v in list(x)[:500]]
        if isinstance(x, Path):
            return scrub(str(x), depth)
        if dataclasses.is_dataclass(x) and not isinstance(x, type):
            return scrub({f.name: getattr(x, f.name, None) for f in dataclasses.fields(x)}, depth + 1)
        if hasattr(x, "value") and isinstance(getattr(x, "value"), (str, int)):   # enums
            return scrub(x.value, depth)
        return scrub(str(x), depth)
    except Exception:
        return None


def public_state(obj: Any, limit: int = 40) -> dict | None:
    """Public scalar / short attributes of ``obj`` (a watcher, a card...), scrubbed. None for None."""
    if obj is None:
        return None
    out: dict[str, Any] = {"type": type(obj).__name__}
    try:
        items = vars(obj).items() if hasattr(obj, "__dict__") else (
            (f.name, getattr(obj, f.name, None)) for f in dataclasses.fields(obj))
        for k, v in items:
            if k.startswith("_") or callable(v) or len(out) > limit:
                continue
            if isinstance(v, (bool, int, float, str, type(None))) or (isinstance(v, (list, tuple)) and len(v) <= 20):
                out[k] = v
    except Exception:
        pass
    return scrub(out)


# ----------------------------------------------------------------------------- rolling buffer
class ReportBuffer:
    """What the report needs, kept in memory (bounded). Thread-safe; every method never raises."""

    def __init__(self, seconds: int = BUFFER_S) -> None:
        self._lock = threading.Lock()
        self.frames: collections.deque = collections.deque(maxlen=max(1, int(seconds)))
        self.perf: collections.deque = collections.deque(maxlen=24)
        self.gate: collections.deque = collections.deque(maxlen=GATE_DECISIONS)
        self.transcripts: collections.deque = collections.deque(maxlen=TRANSCRIPTS)
        self.last_sample = -1e18
        self.last_perf = -1e18
        self.pending: dict | None = None        # a report asked: {"t", "screen", "done"}

    def add_frame(self, jpg: bytes | None, info: dict) -> None:
        with self._lock:
            self.frames.append((jpg, info))

    def add_gate(self, entry: dict) -> None:
        with self._lock:
            self.gate.append(entry)

    def add_transcript(self, entry: dict) -> None:
        with self._lock:
            self.transcripts.append(entry)

    def copy(self) -> dict:
        with self._lock:
            return {"frames": list(self.frames), "perf": list(self.perf), "gate": list(self.gate),
                    "transcripts": list(self.transcripts)}


_BUF = ReportBuffer()


def buffer() -> ReportBuffer:
    return _BUF


def _f(x: Any, nd: int = 4) -> float | None:
    try:
        v = float(x)
        return round(v, nd) if v == v else None
    except (TypeError, ValueError):
        return None


def frame_info(engine: Any, now: float) -> dict:
    """Detections / tracks / game time of the engine's current frame (light: no preview, no health)."""
    lock = getattr(engine, "_lock", None)
    if lock is not None:
        with lock:
            identified = list(getattr(engine, "_identified", None) or ())
            game = getattr(engine, "_game", None)
    else:
        identified, game = list(getattr(engine, "_identified", None) or ()), getattr(engine, "_game", None)
    dets = []
    for x in identified:
        det = getattr(x, "det", x)
        dets.append({"u": _f(getattr(det, "u", None)), "v": _f(getattr(det, "v", None)),
                     "r": _f(getattr(det, "r", None), 2), "score": _f(getattr(det, "score", None), 3),
                     "cls": getattr(det, "cls", None), "det_alias": getattr(det, "alias", None),
                     "alias": getattr(x, "alias", None), "relation": getattr(x, "relation", None),
                     "id_score": _f(getattr(x, "id_score", None), 3)})
    tracks = []
    tr = getattr(engine, "_tracker", None)
    try:
        for k in (tr.tracks() if tr is not None else ()):
            pos = k.position()
            tracks.append({"key": k.key, "alias": k.alias, "relation": k.relation, "visible": bool(k.visible),
                           "pos": None if pos is None else [_f(pos[0]), _f(pos[1])],
                           "age_s": _f(now - k.last_seen, 2), "score": _f(k.score, 3),
                           "id_score": _f(getattr(k, "id_score", None), 3),
                           "stacked_with": getattr(k, "stacked_with", None)})
    except Exception:
        log.debug("report tracks failed", exc_info=True)
    gt = _f(getattr(game, "game_time", None), 1) if game is not None else None
    return {"t": _f(now, 2), "game_time": gt, "detections": dets, "tracks": tracks,
            "fps": _f(getattr(engine, "_fps", None), 1), "tick_ms": _f(getattr(engine, "_tick_ms", None), 1)}


def _jpeg(img: Any, quality: int, max_w: int = 0) -> bytes | None:
    try:
        import cv2
        import numpy as np

        if not isinstance(img, np.ndarray) or not img.size:
            return None
        if max_w and img.shape[1] > max_w:
            h, w = img.shape[:2]
            img = cv2.resize(img, (max_w, max(1, int(round(h * max_w / w)))), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
        return buf.tobytes() if ok else None
    except Exception:
        return None


# ----------------------------------------------------------------------------- read-only taps
def _tap_gate(engine: Any, buf: ReportBuffer) -> None:
    """Record the threat gate's decisions (offer / allow) with a reason. Idempotent per gate instance."""
    gate = getattr(engine, "_threat_gate", None)
    if gate is None or getattr(gate, "_report_tapped", False):
        return
    offer, allow = getattr(gate, "offer", None), getattr(gate, "allow", None)
    if not callable(offer) or not callable(allow):
        return

    def t_offer(t, item, kind, level, members=(), dist=None, companion=False):
        held0 = getattr(gate, "held", 0)
        ok = offer(t, item, kind, level, members, dist, companion)
        try:
            if companion:
                why = "raison écrite d'un « Recule ! »" + ("" if ok else " (sans le Recule : bloquée)")
            elif ok:
                why = "dite"
            elif getattr(gate, "held", 0) > held0:
                why = "retenue (ennemi encore loin)"
            else:
                why = "bloquée (même menace déjà annoncée / trop tôt)"
            buf.add_gate({"t": _f(t, 1), "op": "offer", "kind": str(getattr(kind, "value", kind)),
                          "level": int(level), "members": [str(m) for m in members or ()][:5],
                          "dist": _f(dist, 3), "ok": bool(ok), "why": why,
                          "text": str(getattr(item, "text", "") or "")[:120]})
        except Exception:
            pass
        return ok

    def t_allow(t, kind, level, members=(), dist=None):
        ok = allow(t, kind, level, members, dist)
        try:
            buf.add_gate({"t": _f(t, 1), "op": "allow", "kind": str(getattr(kind, "value", kind)),
                          "level": int(level), "members": [str(m) for m in members or ()][:5],
                          "dist": _f(dist, 3), "ok": bool(ok), "why": "dite" if ok else "bloquée (doublon)"})
        except Exception:
            pass
        return ok

    try:
        gate.offer, gate.allow = t_offer, t_allow
        gate._report_tapped = True
    except Exception:
        pass


def _tap_whisper(buf: ReportBuffer) -> None:
    """Record the Whisper thread's transcripts (text + source "pc" / "discord", never a Discord id)."""
    try:
        from treeaicoach import stt_local
    except Exception:
        return
    w = getattr(stt_local, "_WORKER", None)
    if w is None or getattr(w, "_report_tapped", False):
        return
    tr, sub = getattr(w, "transcribe", None), getattr(w, "submit", None)

    def note(key: Any, text: Any, t0: float) -> None:
        try:
            src = "pc" if str(key or "") in ("pc", "") else "discord"
            buf.add_transcript({"t": round(time.time(), 1), "source": src, "text": str(text or "")[:200],
                                "ms": round((time.monotonic() - t0) * 1000), "engine": "Whisper"})
        except Exception:
            pass

    if callable(tr):
        def t_tr(pcm, *a, **kw):
            t0 = time.monotonic()
            out = tr(pcm, *a, **kw)
            note(kw.get("key", ""), out, t0)
            return out
        w.transcribe = t_tr
    if callable(sub):
        def t_sub(pcm, *a, **kw):
            t0, done = time.monotonic(), kw.get("done")

            def d2(text, _done=done):
                note(kw.get("key", ""), text, t0)
                if callable(_done):
                    return _done(text)
            if callable(done):
                kw["done"] = d2
            return sub(pcm, *a, **kw)
        w.submit = t_sub
    try:
        w._report_tapped = True
    except Exception:
        pass


# ----------------------------------------------------------------------------- engine hook
def engine_tick(engine: Any, t: float, buf: ReportBuffer | None = None) -> None:
    """Analysis thread, ~1 Hz during a game: one minimap sample per second (JPEG ~1 ms), a perf sample
    every 5 s, the taps, and the full screenshot of a pending report. Never raises."""
    try:
        buf = buf or _BUF
        cfg = getattr(engine, "_cfg", None)
        if getattr(cfg, "report_buffer", True) is False or getattr(engine, "_demo", False):
            return
        now = float(t)
        p = buf.pending
        if p is not None and p.get("screen") is None:
            p["screen"] = grab_screen(engine) or b""
        if now - buf.last_sample >= SAMPLE_EVERY_S * 0.95:
            buf.last_sample = now
            lock = getattr(engine, "_lock", None)
            if lock is not None:
                with lock:
                    frame = getattr(engine, "_frame", None)
                    frame = None if frame is None else frame.copy()
            else:
                frame = getattr(engine, "_frame", None)
            buf.add_frame(_jpeg(frame, CROP_JPEG_Q), frame_info(engine, now))
        if now - buf.last_perf >= PERF_EVERY_S:
            buf.last_perf = now
            meter = getattr(engine, "_thread_cpu", None)
            threads = getattr(meter, "percent", None)
            threads = threads() if callable(threads) else threads
            with buf._lock:
                buf.perf.append({"t": _f(now, 1), "fps": _f(getattr(engine, "_fps", None), 1),
                                 "tick_ms": _f(getattr(engine, "_tick_ms", None), 1),
                                 "threads": {str(k): _f(v, 1) for k, v in dict(threads or {}).items()}})
            _tap_gate(engine, buf)
            _tap_whisper(buf)
    except Exception:
        log.debug("analysis report tick failed", exc_info=True)


def grab_screen(engine: Any) -> bytes | None:
    """One full game window capture as JPEG (analysis thread: it owns the capture objects)."""
    try:
        img = None
        win = getattr(engine, "_window", None)
        if win is not None and getattr(engine, "_frame_source", None) is None:
            from treeaicoach.engine import _as_bgr  # noqa: PLC0415
            img = _as_bgr(engine._grabber().grab(win))
        return _jpeg(img, SCREEN_JPEG_Q, SCREEN_MAX_W)
    except Exception:
        log.debug("report screen grab failed", exc_info=True)
        return None


# ----------------------------------------------------------------------------- state
def settings_dict(cfg: Any) -> dict:
    """The settings worth reading (bool / number / short text), never a key, token, name, path or device."""
    out: dict[str, Any] = {}
    for f in dataclasses.fields(cfg) if dataclasses.is_dataclass(cfg) else ():
        v = getattr(cfg, f.name, None)
        if _SECRET_KEY.search(f.name) or re.search(r"(?i)key$|_dir$|path|folder|url|name$|device|hotword|wake_?word|"
                                                     r"nick|friend", f.name):
            continue
        if isinstance(v, (bool, int, float)) or (isinstance(v, str) and len(v) <= 40):
            out[f.name] = v
    return out


def voice_status(engine: Any = None, buf: ReportBuffer | None = None) -> dict:
    """Whisper / wake word / TTS state and the last transcripts. Never raises."""
    buf = buf or _BUF
    out: dict[str, Any] = {}
    try:
        from treeaicoach import stt_local
        s = getattr(stt_local, "_SINGLETON", None)
        w = getattr(stt_local, "_WORKER", None)
        out["whisper"] = {"created": s is not None, "ready": bool(s.ready()) if s is not None else False,
                          "status": getattr(s, "status", None), "model": getattr(s, "name", None),
                          "device": getattr(s, "device", None) or getattr(s, "compute", None),
                          "engine_error": getattr(stt_local, "ENGINE_ERROR", "")}
        out["worker"] = public_state(w)
    except Exception:
        out["whisper"] = None
    try:
        from treeaicoach import voice_latency     # « Latence question : vad … · total … » per question
        out["latency"] = voice_latency.snapshot()
    except Exception:
        pass
    try:
        from treeaicoach import voice_assistant
        a = getattr(voice_assistant, "_SINGLETON", None)
        out["assistant"] = public_state(a, 25)
    except Exception:
        pass
    v = getattr(engine, "_voice", None)
    out["tts"] = {"backend": str(getattr(v, "backend", "") or type(v).__name__) if v is not None else None}
    out["transcripts"] = buf.copy()["transcripts"]
    return out


def perf_summary(buf: ReportBuffer | None = None) -> dict:
    """Mean / max CPU % per thread, fps and tick over the buffered perf samples (last ~2 min of game)."""
    buf = buf or _BUF
    samples = buf.copy()["perf"]
    per: dict[str, list[float]] = {}
    for smp in samples:
        for k, v in (smp.get("threads") or {}).items():
            if v is not None:
                per.setdefault(k, []).append(float(v))
    def stats(xs: list) -> dict:
        xs = [float(x) for x in xs if x is not None]
        return {"mean": round(sum(xs) / len(xs), 1), "max": round(max(xs), 1)} if xs else {}
    return {"samples": len(samples), "threads": {k: stats(v) for k, v in sorted(per.items())},
            "fps": stats([smp.get("fps") for smp in samples]), "tick_ms": stats([smp.get("tick_ms") for smp in samples])}


def _player(p: Any) -> dict:
    return {"champion": getattr(p, "champion_alias", None), "name": getattr(p, "champion_name", None),
            "team": getattr(p, "team", None), "position": getattr(p, "position", None),
            "level": getattr(p, "level", None), "dead": getattr(p, "is_dead", None),
            "items": [getattr(i, "item_id", i) for i in (getattr(p, "items", None) or ())][:7]}


def collect_state(engine: Any, buf: ReportBuffer | None = None) -> dict:
    """Everything but the images (scrubbed by the writer). Never raises (missing parts -> None)."""
    buf = buf or _BUF
    st: dict[str, Any] = {"created": time.strftime("%Y-%m-%d %H:%M:%S")}

    def part(name: str, fn: Callable[[], Any]) -> None:
        try:
            st[name] = fn()
        except Exception as exc:
            st[name] = {"error": f"{type(exc).__name__}"}

    def app() -> dict:
        from treeaicoach import __version__
        out = {"version": __version__}
        try:
            from treeaicoach.diag import system_info
            si = system_info()
            out.update({k: si.get(k) for k in ("python", "platform", "cpu", "cores", "frozen", "windows_build")})
        except Exception:
            pass
        return out
    part("app", app)

    part("settings", lambda: settings_dict(getattr(engine, "_cfg", None) or getattr(engine, "cfg", None)))

    def live() -> dict | None:
        game = getattr(engine, "_game", None)
        if game is None:
            return None
        me = getattr(game, "me", None)
        return {"game_time": getattr(game, "game_time", None), "mode": getattr(game, "game_mode", None),
                "my_team": getattr(game, "my_team", None), "me": _player(me) if me is not None else None,
                "gold": getattr(game, "current_gold", None),
                "players": [_player(p) for p in (game.all_players() if hasattr(game, "all_players") else ())]}
    part("live_client", live)

    def ranks() -> dict | None:
        from treeaicoach import player_intel as pi
        w = getattr(pi, "_default", None)
        snap = w.snapshot() if w is not None else ()
        return {"enabled": pi.enabled(getattr(engine, "_cfg", None)), "queue_id": getattr(w, "_queue_id", None),
                "players": [dict(scrub(p), shown=pi.card_line(p)) for p in snap]}
    part("ranks", ranks)

    def shop() -> dict:
        out = {"shop_watcher": public_state(getattr(engine, "_shop_watcher", None)),
               "item_card": public_state(getattr(engine, "_item_card_state", None)),
               "item_advice": scrub(getattr(engine, "_item_adv", None))}
        try:
            from treeaicoach import client_sync
            out["item_set"] = public_state(getattr(client_sync, "_default", None))
        except Exception:
            pass
        return out
    part("shop", shop)

    part("voice", lambda: voice_status(engine, buf))

    def coach() -> list:
        rec = list(getattr(engine, "_recent", None) or ())[-COACH_LINES:]
        return [{"game_time": r[0], "text": r[1], "level": r[2], "rule": r[3]} for r in rec if len(r) >= 4]
    part("coach_lines", coach)

    def perf() -> dict:
        h = engine.health() if callable(getattr(engine, "health", None)) else {}
        return {"samples": buf.copy()["perf"], "performance": (h or {}).get("performance"),
                "capture": (h or {}).get("capture"), "selfcheck": (h or {}).get("selfcheck")}
    part("perf", perf)
    st["threat_gate"] = buf.copy()["gate"]
    try:
        g = getattr(engine, "_threat_gate", None)
        st["threat_gate_totals"] = {k: getattr(g, k, None) for k in ("passed", "dropped", "held")} if g else None
    except Exception:
        st["threat_gate_totals"] = None
    return st


# ----------------------------------------------------------------------------- last game
def game_summary(rec: dict, name: str = "") -> dict:
    """Short, id-free summary of a game record (result, KDA, alerts per rule, self-check problems...)."""
    alerts = rec.get("alerts") or []
    rules = collections.Counter(str(a[1]) for a in alerts if isinstance(a, (list, tuple)) and len(a) > 1)
    dur = float(rec.get("duration") or 0) or None
    sc = rec.get("selfcheck") or {}
    return scrub({"file": name, "summary": rec.get("summary"), "app_version": (rec.get("meta") or {}).get("app_version"),
                  "roster": [{k: p.get(k) for k in ("alias", "team", "position", "is_me")} for p in rec.get("roster") or ()
                             if isinstance(p, dict)],
                  "alerts": len(alerts), "alerts_per_min": round(len(alerts) * 60 / dur, 2) if dur else None,
                  "alerts_by_rule": dict(rules.most_common(15)),
                  "sightings": {k: len(v) for k, v in (rec.get("sightings") or {}).items() if isinstance(v, list)},
                  "selfcheck": {k: sc.get(k) for k in ("ok", "problems", "profile_max", "overhead_ms")},
                  "incomplete": rec.get("incomplete")})


def last_game_summary(games_dir: Path | None = None) -> dict | None:
    """Summary of the newest finished game record (``games/*.json``), None when there is none."""
    try:
        from treeaicoach import storage  # noqa: PLC0415

        if games_dir is None:
            from treeaicoach.paths import user_data_dir  # noqa: PLC0415
            games_dir = Path(user_data_dir()) / "games"
        recs = storage.game_records(Path(games_dir))
        if not recs:
            return None
        rec = json.loads(recs[-1].read_text(encoding="utf-8"))
        return game_summary(rec, recs[-1].name) if isinstance(rec, dict) else None
    except Exception:
        log.debug("last game summary failed", exc_info=True)
        return None


def _readme(state: dict, n_frames: int) -> str:
    lines = ["Rapport d'analyse TreeAI Coach", ""]
    if state.get("no_game"):
        lines += ["AUCUNE PARTIE EN COURS au moment du rapport.",
                  f"Les {n_frames} images du tampon sont les dernières secondes de la partie précédente"
                  if n_frames else "Le tampon est vide (pas de partie depuis le lancement de TreeAI).",
                  "last_game.json : le résumé de la dernière partie enregistrée." if state.get("last_game")
                  else "Aucune partie enregistrée pour l'instant.", ""]
    else:
        lines += [f"Partie en cours : {n_frames} images des 60 dernières secondes.", ""]
    lines += ["frames.json + frames/ : minimap et détections ; state.json : roster, voix, conseils, perfs ;",
              "screen.jpg : la fenêtre du jeu. Lecture : python tools/analyze_report.py <ce zip>"]
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------- writing
def reports_dir() -> Path:
    from treeaicoach.paths import user_data_dir
    return Path(user_data_dir()) / REPORT_DIR


def write_report(engine: Any, out_dir: Path | None = None, buf: ReportBuffer | None = None,
                 screen: bytes | None = None, max_bytes: int = MAX_BYTES) -> Path | None:
    """The zip of the buffer + state (+ ``screen``). Returns its path, None on failure. Never raises."""
    try:
        buf = buf or _BUF
        data = buf.copy()
        state = scrub(collect_state(engine, buf))
        frames = data["frames"]
        state["no_game"] = getattr(engine, "_game", None) is None
        state["perf_summary"] = perf_summary(buf)
        last_game = last_game_summary() if state["no_game"] else None
        if last_game:
            state["last_game"] = {"file": last_game.get("file"), "summary": last_game.get("summary")}
        root = Path(out_dir) if out_dir is not None else reports_dir()
        root.mkdir(parents=True, exist_ok=True)
        name = time.strftime("rapport_%Y%m%d_%H%M%S")
        path, i = root / f"{name}.zip", 1
        while path.exists():
            path, i = root / f"{name}_{i}.zip", i + 1
        step = 1
        while True:
            kept = frames[::step]
            raw = io.BytesIO()
            with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                infos = []
                for n, (jpg, info) in enumerate(kept):
                    rec = dict(scrub(info), index=n)
                    if jpg:
                        rec["image"] = f"frames/{n:03d}.jpg"
                        zf.writestr(rec["image"], jpg, compress_type=zipfile.ZIP_STORED)
                    infos.append(rec)
                zf.writestr("frames.json", json.dumps(infos, ensure_ascii=False))
                if screen:
                    zf.writestr("screen.jpg", screen, compress_type=zipfile.ZIP_STORED)
                state["report"] = {"frames": len(kept), "frame_step": step, "buffer_s": BUFFER_S,
                                   "screen": bool(screen), "format": 2,
                                   "frames_from": "partie précédente" if state["no_game"] and kept else
                                   ("partie en cours" if kept else None)}
                zf.writestr("state.json", json.dumps(state, ensure_ascii=False, indent=1))
                zf.writestr("LISEZMOI.txt", _readme(state, len(kept)))
                if last_game:
                    zf.writestr("last_game.json", json.dumps(last_game, ensure_ascii=False, indent=1))
            if raw.tell() <= max_bytes or len(kept) <= 1:
                break
            step *= 2
        path.write_bytes(raw.getvalue())
        log.info("Analysis report written: %s (%d frames, %.1f MB)", path.name, len(kept), raw.tell() / 1e6)
        return path
    except Exception:
        log.exception("Analysis report failed")
        return None


_last: dict[str, Any] = {"path": None, "error": None}


def request(engine: Any, out_dir: Path | None = None, wait_s: float = SCREEN_WAIT_S,
            buf: ReportBuffer | None = None, block: bool = False) -> Any:
    """Write a report now (hotkey / button, any thread): the analysis thread grabs the full window at
    its next tick (waited up to ``wait_s``), then a writer thread zips. ``block``: return the path,
    else the writer thread. Never raises."""
    buf = buf or _BUF
    pend = {"t": time.monotonic(), "screen": None}

    def run() -> Path | None:
        try:
            if buf.pending is None:
                buf.pending = pend
            t_end = time.monotonic() + max(0.0, wait_s)
            while pend["screen"] is None and time.monotonic() < t_end:
                time.sleep(0.05)
            path = write_report(engine, out_dir, buf, pend.get("screen") or None)
            _last.update(path=path, error=None if path else "écriture impossible")
            return path
        except Exception as exc:
            _last.update(path=None, error=str(exc))
            return None
        finally:
            if buf.pending is pend:
                buf.pending = None

    if block:
        return run()
    th = threading.Thread(target=run, name="TreeAI-report", daemon=True)
    th.start()
    return th


def last_report() -> dict:
    return dict(_last)


__all__ = ["settings_dict", "voice_status", "perf_summary", "game_summary", "last_game_summary",
           "ReportBuffer", "buffer", "engine_tick", "request", "write_report", "collect_state", "scrub",
           "public_state", "reports_dir", "last_report", "MAX_BYTES"]
