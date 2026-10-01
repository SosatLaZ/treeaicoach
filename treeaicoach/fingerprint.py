"""Configuration fingerprint ("Empreinte de config"): everything that makes TreeAI analyse
differently from one PC to another, in one comparable text.

Real report: "the same version doesn't analyse the same way on my friend's PC and mine". The
sources of per-PC divergence are:

* **saved settings** (``config.json``): level / presets, voice level, detection backend and
  threshold, target rate, performance mode, capture backend, manual minimap rectangle, fog mode,
  safe mode..., and the one-time migrations applied to old files (they show up as non-default
  values);
* **automatic decisions of this session**: low-end performance budget (CPU cores or slow ticks
  measured during the first 30 s), adaptive rates, self-check load level (allégé / minimal),
  capture backend switch (DXGI -> mss), disabled backends;
* **learned state**: icon scale per minimap size (``icon_scale_by_res`` in the settings), the
  live calibration of this game, learned icons of custom skins (``<cache>/learned_icons``),
  ``minimap_cache.json`` (located rectangles per window size / game settings);
* **files shipped with the build**: ``det_params.json`` tuning overrides (hash), ONNX model
  version, game data (Data Dragon) versions - cached copies can be newer than the bundled ones;
* **the machine and the game**: resolution / DPI, minimap scale, HUD scale, window mode,
  colour-blind mode of the game, CPU, Windows build, voice backend, AI provider.

:func:`collect` gathers them (engine optional), :func:`to_text` renders ``clé = valeur`` lines
(no personal data: no key, no token, no summoner name, paths reduced to counts), :func:`compare`
lists the differences between two such texts (the "Comparer" button: my PC vs the text a friend
copied). Pure Python, never raises.
"""

from __future__ import annotations

import hashlib
import json
import logging
import platform
import sys
from dataclasses import fields
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

HEADER = "Empreinte TreeAI"
#: Settings never put in a fingerprint (secrets, window geometry, purely cosmetic UI memory).
PRIVATE_SETTINGS = frozenset({"ai_api_key", "github_token", "ui_geometry", "update_channel_url",
                              "ui_seen_changelog", "ui_onboarding_done"})
#: Key settings always listed (even at their default value), in this order.
KEY_SETTINGS = ("skill_level", "voice_level", "target_fps", "perf_mode", "adaptive_rate", "capture_backend",
                "detector_backend", "detection_threshold", "minimap_mode", "minimap_side", "sensitivity",
                "fog_mode", "safe_mode", "overlay_mode", "overlay_hide_from_capture", "low_priority",
                "eco_qos_v2", "pause_when_unfocused", "download_skin_icons", "voice_engine", "danger_voice",
                "ai_provider", "selfcheck_enabled")


def _short(x: Any) -> str:
    if isinstance(x, float):
        return f"{x:.4g}"
    if isinstance(x, (dict, list, tuple)):
        return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":"))[:160]
    return str(x)


def _hash(obj: Any, n: int = 8) -> str:
    try:
        raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha1(raw).hexdigest()[:n]
    except Exception:
        return "?"


def _file_hash(path: Path) -> str | None:
    try:
        return hashlib.sha1(Path(path).read_bytes()).hexdigest()[:8]
    except Exception:
        return None


def settings_part(cfg: Any) -> dict[str, Any]:
    """Key settings + every other setting that differs from the defaults + a hash of all of
    them (secrets excluded)."""
    out: dict[str, Any] = {}
    try:
        from treeaicoach.config import Config

        default = Config()
        names = [f.name for f in fields(Config) if f.name not in PRIVATE_SETTINGS]
        values = {n: getattr(cfg, n, None) for n in names}
        for n in KEY_SETTINGS:
            if n in values:
                out[f"réglage.{n}"] = _short(values[n])
        for n in names:
            if n in KEY_SETTINGS:
                continue
            v = values[n]
            if v != getattr(default, n, None):
                if n == "ai_model":
                    v = str(v)[:40]
                out[f"réglage.{n}"] = _short(v)
        out["réglages.empreinte"] = _hash({n: _short(values[n]) for n in names if n != "icon_scale_by_res"})
        out["réglages.modifiés"] = sum(1 for n in names if values[n] != getattr(default, n, None))
        out["réglage.ai_cle"] = "oui" if str(getattr(cfg, "ai_api_key", "") or "").strip() else "non"
    except Exception:
        log.debug("fingerprint settings failed", exc_info=True)
    return out


def machine_part() -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        from treeaicoach import __version__
        from treeaicoach.sysperf import APPLIED, cpu_count, cpu_name

        out["app.version"] = __version__
        out["app.exe"] = "oui" if getattr(sys, "frozen", False) else "non"
        out["système"] = platform.platform(terse=True)
        if sys.platform == "win32":
            out["système.build"] = int(sys.getwindowsversion().build)
        out["cpu.coeurs"] = cpu_count()
        out["cpu.nom"] = cpu_name()[:60]
        out["processus.priorité_basse"] = bool(APPLIED.get("priority", False))
        out["processus.ecoqos"] = bool(APPLIED.get("eco_qos", False))
    except Exception:
        log.debug("fingerprint machine failed", exc_info=True)
    return out


def files_part() -> dict[str, Any]:
    """Build files and per-user caches that change the analysis."""
    out: dict[str, Any] = {}
    try:
        from treeaicoach import det_params

        out["détection.det_params"] = f"{len(det_params.load())} réglage(s), fichier {_file_hash(det_params.PARAMS_FILE) or 'absent'}"
    except Exception:
        pass
    try:
        from treeaicoach.detector import default_meta_path, load_model_meta

        meta = load_model_meta(default_meta_path())
        out["détection.modèle"] = str(meta.get("version") or "absent")
    except Exception:
        out["détection.modèle"] = "?"
    try:
        from treeaicoach.paths import cache_dir, user_data_dir

        learned = Path(cache_dir()) / "learned_icons"
        out["cache.icônes_apprises"] = len(list(learned.glob("*.png"))) if learned.is_dir() else 0
        mc = Path(user_data_dir()) / "minimap_cache.json"
        n = 0
        if mc.is_file():
            data = json.loads(mc.read_text(encoding="utf-8"))
            n = len(data) if isinstance(data, dict) else 0
        out["cache.minimap_cache"] = n
    except Exception:
        log.debug("fingerprint caches failed", exc_info=True)
    try:
        from treeaicoach.game_data import data_versions

        for name, v in data_versions().items():
            if isinstance(v, dict):
                ver = v.get("version") or v.get("patch") or v.get("schema")
                src = v.get("source")
                out[f"données.{name}"] = f"{ver}" + (f" ({src})" if src else "")
    except Exception:
        pass
    return out


def engine_part(eng: Any) -> dict[str, Any]:
    """What the running engine decided / learned on this PC (this session)."""
    out: dict[str, Any] = {}
    if eng is None:
        return out
    try:
        b = eng._budget.describe()
        prof = eng._budget.profile
        out["perf.profil"] = f"{b.get('profile')} ({b.get('reason') or 'auto'})"
        out["perf.charge"] = str(prof.load)
        out["perf.img_s"] = f"{prof.calm_fps:g}-{prof.burst_fps:g}"
        out["perf.overlay_img_s"] = f"{prof.overlay_fps:g}"
        out["perf.coaching_hz"] = f"{prof.heavy_hz:g}"
        out["perf.onnx_1_sur"] = int(prof.onnx_every)
        out["perf.adaptatif"] = bool(eng._adaptive)
    except Exception:
        pass
    try:
        cap = eng._capture
        if cap is not None:
            out["capture.backend"] = str(getattr(cap, "current", None) or getattr(cap, "name", None) or type(cap).__name__)
            dis = getattr(cap, "_disabled", None)
            if dis:
                out["capture.désactivés"] = ", ".join(sorted(dis))
            st = getattr(cap, "stats", None)
            if isinstance(st, dict) and st.get("switches"):
                out["capture.changements"] = f"{st.get('switches')} ({st.get('last_switch')})"
    except Exception:
        pass
    try:
        win, info = eng._window, eng._win_info
        if win is not None:
            out["jeu.fenêtre"] = f"{win.w}x{win.h}"
        if info is not None:
            out["jeu.échelle_windows"] = f"{round(info.dpi / 96 * 100)} %"
        r = eng._minimap_rect
        if r is not None:
            out["minimap.taille_px"] = f"{r.w}x{r.h}"
            if win is not None and win.h:
                out["minimap.taille_rel"] = f"{r.h / win.h:.3f}"
        out["minimap.méthode"] = str(eng._locate_method)
        if getattr(eng, "_locate_score", None) is not None:
            out["minimap.score"] = f"{eng._locate_score:.2f}"
        w = getattr(eng, "_settings_watcher", None)
        gs = w.get() if w is not None else None
        if gs is not None:
            out["jeu.mode_fenêtre"] = {0: "plein écran", 1: "fenêtré", 2: "sans bordure"}.get(gs.window_mode, str(gs.window_mode))
            out["jeu.échelle_minimap"] = _short(gs.minimap_scale)
            out["jeu.échelle_hud"] = _short(gs.global_scale)
            out["jeu.minimap_à_gauche"] = bool(gs.flip_minimap)
            out["jeu.daltonien"] = bool(gs.colorblind)
    except Exception:
        pass
    try:
        det = eng._detector
        out["détection.détecteur"] = str(getattr(det, "name", "") or type(det).__name__ if det is not None else "-")
        m = getattr(det, "matcher", None)
        if m is not None:
            sc = getattr(m, "scale", None)
            out["détection.échelle_icônes"] = "non calibrée" if sc is None else f"{sc:.4f}"
            la = m.learned_aliases() if callable(getattr(m, "learned_aliases", None)) else {}
            out["détection.icônes_apprises"] = len(la)
        store = getattr(eng._cfg, "icon_scale_by_res", None)
        if isinstance(store, dict):
            out["détection.échelles_mémorisées"] = len(store)
    except Exception:
        pass
    try:
        v = eng._voice
        out["voix.moteur"] = str(getattr(v, "backend", "") or type(v).__name__)
        out["voix.danger"] = eng._danger_voice_mode()
        sc = eng._selfcheck
        out["autodiag.état"] = sc.summary().get("state")
        adapt = [e["text"] for e in list(sc.events) if e.get("kind") in ("action", "info")
                 and e.get("rule") in ("perf", "capture", "champions", "voice", "ai", "adapt")][-6:]
        if adapt:
            out["autodiag.adaptations"] = " | ".join(adapt)
    except Exception:
        pass
    return out


def collect(eng: Any = None, cfg: Any = None) -> dict[str, Any]:
    """The fingerprint as an ordered ``{clé: valeur}`` dict (+ ``empreinte`` short hash of the
    parts that should be equal on two PCs running the same settings). Never raises."""
    cfg = cfg if cfg is not None else getattr(eng, "_cfg", None)
    out: dict[str, Any] = {}
    out.update(machine_part())
    out.update(files_part())
    if cfg is not None:
        out.update(settings_part(cfg))
    out.update(engine_part(eng))
    comparable = {k: v for k, v in out.items() if k.startswith(("app.", "réglage", "détection.det", "détection.modèle",
                                                               "données."))}
    out["empreinte"] = _hash(comparable)
    return out


def to_text(fp: dict[str, Any]) -> str:
    """``clé = valeur`` lines (copy / paste, diagnostic bundle)."""
    try:
        lines = [f"{HEADER} {fp.get('app.version', '?')} · {fp.get('empreinte', '?')}"]
        lines += [f"{k} = {v}" for k, v in fp.items() if k != "empreinte"]
        return "\n".join(lines) + "\n"
    except Exception:
        return f"{HEADER} ?\n"


def parse(text: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in str(text or "").splitlines():
        if " = " in line:
            k, _sep, v = line.partition(" = ")
            out[k.strip()] = v.strip()
    return out


#: Keys that differ between two PCs by nature (not a cause of a different analysis by themselves).
NOISY = ("cpu.nom", "système", "système.build", "autodiag.", "minimap.score", "capture.changements")


def compare(mine: Any, theirs: Any) -> list[str]:
    """Differences between two fingerprints (texts or dicts): ``"clé : moi X / l'autre Y"``,
    the causes of a different analysis first. [] when the other text is no fingerprint."""
    a = parse(mine) if not isinstance(mine, dict) else {k: _short(v) for k, v in mine.items()}
    b = parse(theirs) if not isinstance(theirs, dict) else {k: _short(v) for k, v in theirs.items()}
    if not b:
        return []
    keys = [k for k in dict.fromkeys(list(a) + list(b))]
    diff = [k for k in keys if a.get(k) != b.get(k)]
    diff.sort(key=lambda k: (k.startswith(NOISY), not k.startswith(("réglage", "perf", "capture", "détection")), k))
    return [f"{k} : moi {a.get(k, '-')} / l'autre {b.get(k, '-')}" for k in diff]


__all__ = ["collect", "to_text", "parse", "compare", "settings_part", "HEADER"]
