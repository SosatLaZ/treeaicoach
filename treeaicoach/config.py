"""User configuration: dataclass with defaults, validation and atomic JSON persistence.

* ``Config.validated()`` returns a sanitized copy: wrong types -> field default, numbers
  clamped to their range, enum strings normalized, ``manual_minimap_rect`` checked,
  hotkey names canonicalized (``"ctrl + f9"`` -> ``"Ctrl+F9"``, duplicates disabled),
  objective lead times sorted/deduplicated, window positions / geometry checked.
* ``load_config()`` never raises: a missing file gives the defaults, a corrupt file is
  renamed ``config.json.bak`` and the defaults are used, unknown keys are ignored.
* ``save_config()`` writes atomically (temp file in the same folder + ``os.replace``), UTF-8.

The ranges/choices below are public so that the UI can build its sliders and menus from them.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import numbers
import os
import re
import tempfile
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from treeaicoach import paths
from treeaicoach.hotkeys import normalize_hotkey

log = logging.getLogger(__name__)

CONFIG_VERSION = 1                 # written as "config_version" (ignored on load for now)
MAX_CONFIG_BYTES = 1_000_000       # a bigger config.json is treated as corrupt
BACKUP_SUFFIX = ".bak"

INT_RANGES: dict[str, tuple[int, int]] = {
    "voice_rate": (-10, 10),       # SAPI rate
    "voice_volume": (0, 100),
    "recall_gold_threshold": (300, 5000),
}
FLOAT_RANGES: dict[str, tuple[float, float]] = {
    "sensitivity": (0.6, 1.6),
    "warn_radius": (0.05, 0.50),   # normalized by minimap width
    "danger_radius": (0.03, 0.35),
    "target_fps": (2.0, 20.0),
    "detection_threshold": (0.0, 0.95),   # 0 = use model_meta.json
    "collect_interval_s": (0.5, 60.0),
    "radar_scale": (0.5, 2.0),     # 1.0 = same size as the minimap
    "fog_max_s": (10.0, 180.0),
}
# A non-zero detection threshold is raised to at least this value (0 keeps its special meaning).
DETECTION_THRESHOLD_MIN = 0.05
CHOICES: dict[str, tuple[str, ...]] = {
    "detector_backend": ("auto", "onnx", "classic"),
    "minimap_mode": ("auto", "manual"),
    "minimap_side": ("auto", "right", "left"),
    "radar_position": ("above_minimap", "left_of_minimap", "top_left", "custom"),
    "hud_position": ("above_minimap", "top_left", "top_right", "left_middle", "custom"),
    "overlay_mode": ("minimap", "radar", "off"),
    "fog_mode": ("jungler", "all", "off"),
    "voice_engine": ("auto", "neural", "onecore", "sapi"),
    # v2 voice policy (voice_policy.py): minimal = ganks, objectives at 60 s, stance, big plays only
    "voice_level": ("minimal", "normal", "bavard"),
}
BOOL_FIELDS: tuple[str, ...] = (
    "beep_on_danger",
    "alert_jungler_approach",
    "alert_roam",
    "alert_collapse",
    "alert_jungler_spotted",
    "alert_laner_mia",
    "safe_mode",
    "download_skin_icons",
    "autostart",
    "collect_samples",
    "show_preview",
    # v1.1 (§6.7)
    "objective_timers",
    "recall_reminder",
    "control_ward_reminder",
    "death_recap",
    "post_game_report",
    "open_report_automatically",
    # v1.2 (§7.4)
    "overlay_enabled",
    "radar_enabled",
    "hud_enabled",
    "danger_flash",
    "overlay_hide_from_capture",
    "overlay_show_frame",
    "overlay_show_allies",
    "overlay_show_roles",
    "overlay_show_ghosts",
    "hud_detailed",
    "text_tips",
    "tip_toasts",
    "stance_voice",
    "break_reminder",
    # updates
    "check_updates_on_start",
)
#: Global hotkey fields (see hotkeys.py for the accepted names; "" = disabled), in priority order:
#: when two fields hold the same key, the later one is disabled.
HOTKEY_FIELDS: tuple[str, ...] = ("hotkey_jungler", "hotkey_mute", "hotkey_overlay")
#: Custom overlay window positions: ``[x, y]`` screen pixels or None.
XY_FIELDS: tuple[str, ...] = ("radar_xy", "hud_xy")
#: objective_lead_s: seconds before a spawn at which it is announced.
OBJECTIVE_LEAD_RANGE = (5, 300)
OBJECTIVE_LEAD_MAX_COUNT = 4
DEFAULT_OBJECTIVE_LEAD_S: tuple[int, ...] = (60, 20)
#: ui_geometry: Tk geometry string "WxH" or "WxH+X+Y" ("" = let the UI decide).
UI_GEOMETRY_SIZE_RANGE = (200, 20000)
UI_GEOMETRY_MAX_LEN = 64
_UI_GEOMETRY_RE = re.compile(r"=?(\d{1,5})x(\d{1,5})(?:([+-]-?\d{1,6})([+-]-?\d{1,6}))?")
#: "custom" position -> fallback position when the matching ``*_xy`` field is missing.
CUSTOM_POSITION_FALLBACK: dict[str, tuple[str, str]] = {
    "radar_position": ("radar_xy", "above_minimap"),
    "hud_position": ("hud_xy", "above_minimap"),
}
VOICE_NAME_MAX_LEN = 256
#: Update settings: free text fields (URL / GitHub token), printable, stripped, bounded.
UPDATE_TEXT_FIELDS: dict[str, int] = {"update_channel_url": 2048, "github_token": 512}

# v1.5 interface settings (ui.py / ui_kit.py; overlay_* and layer_* are read by the overlay with getattr)
INT_RANGES.update({
    "voice_quiet_start_s": (0, 300),   # no non-danger speech during the first N seconds of a game
    "quiet_start_h": (0, 23),
    "quiet_end_h": (0, 23),
})
FLOAT_RANGES.update({
    "overlay_opacity": (0.3, 1.0),
    "overlay_scale": (0.6, 1.6),
})
CHOICES.update({
    "ui_last_page": ("dashboard", "alerts", "overlay", "analysis", "settings", "help"),
    "ui_scaling": ("auto", "90", "100", "110", "125", "150"),
    "voice_language": ("fr", "en"),
})
BOOL_FIELDS = BOOL_FIELDS + (
    "voice_info_alerts", "quiet_hours", "colorblind", "layer_roles", "layer_arrows", "layer_zones",
    "layer_ghosts", "ui_remember_page", "ui_onboarding_done", "ui_start_minimized", "ui_minimize_on_game",
    "ui_confirm_quit", "ui_notify_report", "ui_notify_game",
)
UPDATE_TEXT_FIELDS["ui_seen_changelog"] = 32
BOOL_FIELDS = BOOL_FIELDS + ("item_advice", "item_advice_toasts", "item_advice_speak")
# optional LLM advice (ai_advisor.py) + "mode annonceur" / win probability (hype.py)
CHOICES.update({
    "ai_provider": ("off", "gemini", "groq", "openrouter", "ollama", "anthropic"),
    "caster_style": ("sobre", "coach", "caster"),
})
BOOL_FIELDS = BOOL_FIELDS + ("ai_speak", "win_prob_hud")
UPDATE_TEXT_FIELDS["ai_api_key"] = 512
UPDATE_TEXT_FIELDS["ai_model"] = 128

# manual_minimap_rect: {"screen_w","screen_h","x","y","w","h"} in physical screen pixels.
RECT_KEYS: tuple[str, ...] = ("screen_w", "screen_h", "x", "y", "w", "h")
RECT_SCREEN_RANGE = (200, 32768)   # screen_w / screen_h
RECT_SIZE_MIN = 32                 # minimap w / h (and w <= screen_w, h <= screen_h)
RECT_COORD_LIMIT = 65536           # |x|, |y| (multi-monitor virtual coords may be negative)

_META_KEYS = frozenset({"config_version"})
_io_lock = threading.RLock()


class _Invalid:
    """Sentinel for 'value of the wrong type / unusable'."""

    def __repr__(self) -> str:
        return "<invalid>"


_INVALID: Any = _Invalid()


@dataclass
class Config:
    """All user settings, with defaults (see docs/ARCHITECTURE.md §4.2)."""

    # voice
    voice_name: str = ""            # "" = best available French voice
    voice_rate: int = 2             # -10..10 (SAPI)
    voice_volume: int = 100         # 0..100
    voice_engine: str = "auto"      # auto (= neural) | neural | onecore | sapi (see voice.py)
    neural_voice: str = "fr-FR-DeniseNeural"   # Microsoft Edge neural voice id (online)
    neural_rate: str = "+15%"       # neural voice speed, "-50%".."+100%" (independent of voice_rate)
    beep_on_danger: bool = True
    # alerts
    alert_jungler_approach: bool = True
    alert_roam: bool = True
    alert_collapse: bool = True
    alert_jungler_spotted: bool = True
    alert_laner_mia: bool = False
    safe_mode: bool = False         # "mode sûr": no gank / jungler-tracking alerts, no fog
    sensitivity: float = 1.0        # 0.6..1.6, multiplies the radii
    warn_radius: float = 0.22       # normalized minimap (~3300 game units)
    danger_radius: float = 0.12     # (~1800 units)
    # capture / detection
    target_fps: float = 8.0         # 2..20
    detector_backend: str = "auto"  # "auto" | "onnx" | "classic"
    detection_threshold: float = 0.0  # 0 = value from model_meta.json
    minimap_mode: str = "auto"      # "auto" | "manual"
    minimap_side: str = "auto"      # "auto" | "right" | "left"
    manual_minimap_rect: dict | None = None   # {"screen_w","screen_h","x","y","w","h"} screen pixels
    download_skin_icons: bool = True
    #: Calibrated champion icon diameter / minimap width, per minimap size ("316x316": 0.094),
    #: learned live by the roster matcher (prior for the next games).
    icon_scale_by_res: dict = field(default_factory=dict)
    # misc
    autostart: bool = True          # start the analysis at launch
    collect_samples: bool = False   # save minimaps for re-training
    collect_interval_s: float = 2.0
    show_preview: bool = False
    # v1.1 helpers (§6.7)
    objective_timers: bool = True
    objective_lead_s: list[int] = field(default_factory=lambda: list(DEFAULT_OBJECTIVE_LEAD_S))
    recall_reminder: bool = True
    recall_gold_threshold: int = 1300
    control_ward_reminder: bool = True
    hotkey_jungler: str = "F9"      # "" = disabled; e.g. "Ctrl+F9"
    death_recap: bool = True
    post_game_report: bool = True
    open_report_automatically: bool = True
    # v1.2 overlay (§7.4)
    overlay_enabled: bool = True
    radar_enabled: bool = True
    radar_position: str = "above_minimap"   # "above_minimap" | "left_of_minimap" | "top_left" | "custom"
    radar_scale: float = 1.0        # 0.5..2.0 (1.0 = minimap size)
    radar_xy: list[int] | None = None       # [x, y] screen pixels when radar_position == "custom"
    hud_enabled: bool = True
    hud_position: str = "above_minimap"  # "above_minimap" | "top_left" | "top_right" | "left_middle" | "custom"
    overlay_mode: str = "minimap"   # "minimap" (marks on the real minimap) | "radar" | "off"
    hud_xy: list[int] | None = None
    danger_flash: bool = True
    # minimap overlay: hide it from screen capture (False = visible in screenshots / streams)
    overlay_hide_from_capture: bool = False
    overlay_show_frame: bool = True  # discreet frame + "TreeAI" label on the minimap layer
    # v2 decluttered minimap layer: only enemies / jungler fog / approach arrows / danger ring by default
    overlay_show_allies: bool = False    # thin blue rings on allies + teal ring on me
    overlay_show_roles: bool = False     # role tags (TOP/MID/ADC/SUP) on enemies (the jungler always has "JGL")
    overlay_show_ghosts: bool = False    # last seen marks + fog zones of every hidden enemy (not only the jungler)
    hud_detailed: bool = False           # HUD: also the jungler line and the 5 enemy portraits
    text_tips: bool = True               # rotating written tips in the HUD (never spoken)
    tip_toasts: bool = False             # ... also as a small toast
    stance_voice: bool = True            # speak the stance (PRUDENT / AGRESSIF) when it changes
    voice_level: str = "minimal"         # "minimal" | "normal" | "bavard" (the rest is written: HUD + toasts)
    fog_mode: str = "jungler"       # "jungler" | "all" | "off"
    fog_max_s: float = 60.0         # 10..180
    hotkey_mute: str = "F10"
    hotkey_overlay: str = "F11"
    break_reminder: bool = True
    # UI (§8.2)
    ui_geometry: str = ""           # main window geometry "WxH+X+Y" ("" = default)
    # updates (updater.py)
    update_channel_url: str = ""    # "" = default GitHub URL of release/version.json
    github_token: str = ""          # personal access token for the private repo ("" = none)
    check_updates_on_start: bool = True
    # v1.5 voice comfort (ui_kit.VoiceGate: DANGER announcements always pass)
    voice_info_alerts: bool = True   # INFO announcements (objectives, tips, praise...)
    voice_quiet_start_s: int = 0     # 0..300: silence (except danger) at the start of a game
    quiet_hours: bool = False
    quiet_start_h: int = 23
    quiet_end_h: int = 8
    voice_language: str = "fr"       # "fr" | "en" (en: not available yet)
    # v1.5 overlay look (read by the overlay with getattr)
    overlay_opacity: float = 1.0     # 0.3..1.0
    overlay_scale: float = 1.0       # 0.6..1.6 (markers / HUD size)
    layer_roles: bool = False        # role badges on the minimap layer
    layer_arrows: bool = True        # movement arrows
    layer_zones: bool = True         # danger / warning circles
    layer_ghosts: bool = False       # last-seen "ghost" portraits in the fog
    colorblind: bool = False         # colour-blind friendly palette (UI + overlay)
    # v1.5 interface
    ui_last_page: str = "dashboard"
    ui_remember_page: bool = True
    ui_onboarding_done: bool = False
    ui_seen_changelog: str = ""
    ui_start_minimized: bool = False
    ui_minimize_on_game: bool = False
    ui_confirm_quit: bool = True
    ui_notify_report: bool = True
    ui_notify_game: bool = True
    ui_scaling: str = "auto"         # "auto" | "90" | "100" | "110" | "125" | "150" (% of the system scale)
    # build advice (itemization.py): written by default (HUD line + toast), spoken only if item_advice_speak
    item_advice: bool = True
    item_advice_toasts: bool = True
    item_advice_speak: bool = False
    # optional LLM advice (ai_advisor.py): off by default, the key stays on this PC (never exported)
    ai_provider: str = "off"         # "off" | "gemini" | "groq" | "openrouter" | "ollama" | "anthropic"
    ai_api_key: str = ""
    ai_model: str = ""               # "" = default model of the provider
    ai_speak: bool = False           # written only (toast + HUD) unless enabled
    # "mode annonceur" (hype.py): sobre = nothing spoken, coach = win-probability swings, caster = + hype lines
    caster_style: str = "coach"
    win_prob_hud: bool = True        # show the live win probability (HUD line / dashboard)

    def effective_warn_radius(self) -> float:
        """``warn_radius * sensitivity`` (clamped; defaults if the fields are invalid)."""
        return self._field_ok("warn_radius") * self._field_ok("sensitivity")

    def effective_danger_radius(self) -> float:
        """``danger_radius * sensitivity`` (clamped, <= warn radius; defaults if invalid)."""
        danger = min(self._field_ok("danger_radius"), self._field_ok("warn_radius"))
        return danger * self._field_ok("sensitivity")

    def _field_ok(self, name: str) -> Any:
        """Sanitized value of one field, silently (cheap: safe to call every frame)."""
        default = _DEFAULTS.get(name)
        try:
            return _validate_field(name, getattr(self, name, default), default)
        except Exception:
            return default

    def validated(self) -> Config:
        """Return a sanitized copy (types fixed, values clamped). Never raises."""
        try:
            return _validate(self)
        except Exception:  # defensive: validation must never take the app down
            log.exception("Config validation failed; using defaults")
            return Config()

    def to_dict(self) -> dict[str, Any]:
        """Plain dict of all fields (deep copy)."""
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name, None)
            try:
                out[f.name] = copy.deepcopy(value)
            except Exception:
                out[f.name] = value
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> Config:
        """Build a validated Config from a mapping; unknown keys ignored. Never raises."""
        try:
            if not isinstance(data, Mapping):
                if data is not None:
                    log.warning("Config data is %s, not an object; using defaults", type(data).__name__)
                return cls()
            names = {f.name for f in fields(cls)}
            known = {k: v for k, v in data.items() if isinstance(k, str) and k in names}
            unknown = [str(k) for k in data if k not in names and k not in _META_KEYS]
            if unknown:
                log.info("Ignoring unknown config keys: %s", ", ".join(sorted(unknown)[:20]))
            return cls(**known).validated()
        except Exception:
            log.exception("Cannot build Config from data; using defaults")
            return cls()


# --------------------------------------------------------------------------- validation

_DEFAULTS: dict[str, Any] = Config().to_dict()   # (default_factory fields included)


def _real(value: Any) -> Any:
    """Finite float from a real number (not bool), else _INVALID."""
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return _INVALID
    try:
        f = float(value)
    except (OverflowError, ValueError, TypeError):
        return _INVALID
    return f if math.isfinite(f) else _INVALID


def _as_bool(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, numbers.Integral) and value in (0, 1):
        return bool(value)
    return _INVALID


def _as_int(value: Any, lo: int, hi: int) -> Any:
    f = _real(value)
    if f is _INVALID:
        return _INVALID
    return int(min(max(round(f), lo), hi))


def _as_float(value: Any, lo: float, hi: float) -> Any:
    f = _real(value)
    if f is _INVALID:
        return _INVALID
    return float(min(max(f, lo), hi))


def _as_choice(value: Any, choices: tuple[str, ...]) -> Any:
    if not isinstance(value, str):
        return _INVALID
    s = value.strip().lower()
    return s if s in choices else _INVALID


def _as_voice_name(value: Any) -> Any:
    if not isinstance(value, str):
        return _INVALID
    s = "".join(ch for ch in value if ch.isprintable()).strip()
    return s[:VOICE_NAME_MAX_LEN]


_NEURAL_VOICE_RE = re.compile(r"[a-z]{2,3}-[A-Z]{2}-[A-Za-z]{2,40}Neural")


def _as_neural_voice(value: Any) -> Any:
    if not isinstance(value, str):
        return _INVALID
    s = value.strip()
    return s if _NEURAL_VOICE_RE.fullmatch(s) else _INVALID


_NEURAL_RATE_RE = re.compile(r"([+-]?)(\d{1,3})\s*%?")


def _as_neural_rate(value: Any) -> Any:
    """``"+15%"`` / ``"-10%"`` / ``15`` -> canonical ``"+15%"`` (clamped to -50..+100)."""
    if isinstance(value, bool):
        return _INVALID
    if isinstance(value, (int, float)):
        if value != value:  # NaN
            return _INVALID
        n = int(round(value))
    elif isinstance(value, str):
        m = _NEURAL_RATE_RE.fullmatch(value.strip())
        if m is None:
            return _INVALID
        n = int(m.group(2)) * (-1 if m.group(1) == "-" else 1)
    else:
        return _INVALID
    n = max(-50, min(100, n))
    return f"{n:+d}%"


def _as_rect(value: Any) -> dict[str, int] | None:
    """Validate manual_minimap_rect; anything unusable -> None."""
    if value is None or not isinstance(value, Mapping):
        return None
    out: dict[str, int] = {}
    for k in RECT_KEYS:
        f = _real(value.get(k))
        if f is _INVALID:
            return None
        out[k] = int(round(f))
    s_lo, s_hi = RECT_SCREEN_RANGE
    if not (s_lo <= out["screen_w"] <= s_hi and s_lo <= out["screen_h"] <= s_hi):
        return None
    if not (RECT_SIZE_MIN <= out["w"] <= out["screen_w"] and RECT_SIZE_MIN <= out["h"] <= out["screen_h"]):
        return None
    if abs(out["x"]) > RECT_COORD_LIMIT or abs(out["y"]) > RECT_COORD_LIMIT:
        return None
    return out


ICON_SCALE_RANGE = (0.03, 0.2)
ICON_SCALE_MAX_ENTRIES = 32
_RES_KEY_RE = re.compile(r"\d{2,5}x\d{2,5}")


def _as_scale_map(value: Any) -> Any:
    """Validate icon_scale_by_res ({"WxH": ratio}); bad entries dropped, non-dict -> _INVALID."""
    if not isinstance(value, Mapping):
        return _INVALID
    out: dict[str, float] = {}
    for k, v in list(value.items())[:4 * ICON_SCALE_MAX_ENTRIES]:
        if not isinstance(k, str) or not _RES_KEY_RE.fullmatch(k):
            continue
        f = _real(v)
        if f is _INVALID or not ICON_SCALE_RANGE[0] <= f <= ICON_SCALE_RANGE[1]:
            continue
        out[k] = round(float(f), 5)
        if len(out) >= ICON_SCALE_MAX_ENTRIES:
            break
    return out


def _as_hotkey(value: Any) -> Any:
    """Canonical hotkey name ("" = disabled); invalid -> _INVALID."""
    res = normalize_hotkey(value)
    return _INVALID if res is None else res


def _as_xy(value: Any) -> list[int] | None:
    """``[x, y]`` integer screen coordinates, or None if unusable."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    out: list[int] = []
    for c in value:
        f = _real(c)
        if f is _INVALID or abs(f) > RECT_COORD_LIMIT:
            return None
        out.append(int(round(f)))
    return out


def _as_lead_list(value: Any) -> Any:
    """Distinct lead times (s), clamped, sorted in decreasing order; [] allowed (= no announcement)."""
    if not isinstance(value, (list, tuple)):
        return _INVALID
    lo, hi = OBJECTIVE_LEAD_RANGE
    leads: set[int] = set()
    for item in list(value)[:64]:
        f = _real(item)
        if f is _INVALID:
            continue
        leads.add(int(min(max(round(f), lo), hi)))
    if value and not leads:
        return _INVALID
    return sorted(leads, reverse=True)[:OBJECTIVE_LEAD_MAX_COUNT]


def _as_geometry(value: Any) -> Any:
    """Tk geometry string ("1100x700+120+80", negative offsets allowed) or ""; invalid -> _INVALID."""
    if not isinstance(value, str) or len(value) > UI_GEOMETRY_MAX_LEN:
        return _INVALID
    s = value.strip()
    if not s:
        return ""
    m = _UI_GEOMETRY_RE.fullmatch(s)
    if m is None:
        return _INVALID
    lo, hi = UI_GEOMETRY_SIZE_RANGE
    w, h = int(m.group(1)), int(m.group(2))
    if not (lo <= w <= hi and lo <= h <= hi):
        return _INVALID
    if m.group(3) is None:
        return f"{w}x{h}"
    offsets = []
    for g in (m.group(3), m.group(4)):
        sign, num = g[0], int(g[1:])
        if abs(num) > RECT_COORD_LIMIT:
            return _INVALID
        offsets.append(f"{sign}{num}")
    return f"{w}x{h}{offsets[0]}{offsets[1]}"


def _validate_field(name: str, value: Any, default: Any) -> Any:
    """Sanitized value for one field (default if the value is unusable)."""
    if name in BOOL_FIELDS:
        res = _as_bool(value)
    elif name in INT_RANGES:
        res = _as_int(value, *INT_RANGES[name])
    elif name == "detection_threshold":
        res = _as_float(value, *FLOAT_RANGES[name])
        if res is not _INVALID and 0.0 < res < DETECTION_THRESHOLD_MIN:
            res = DETECTION_THRESHOLD_MIN
    elif name in FLOAT_RANGES:
        res = _as_float(value, *FLOAT_RANGES[name])
    elif name in CHOICES:
        res = _as_choice(value, CHOICES[name])
    elif name == "voice_name":
        res = _as_voice_name(value)
    elif name == "neural_voice":
        res = _as_neural_voice(value)
    elif name == "neural_rate":
        res = _as_neural_rate(value)
    elif name == "manual_minimap_rect":
        res = _as_rect(value)
    elif name == "icon_scale_by_res":
        res = _as_scale_map(value)
    elif name in HOTKEY_FIELDS:
        res = _as_hotkey(value)
    elif name in XY_FIELDS:
        res = _as_xy(value)
    elif name == "objective_lead_s":
        res = _as_lead_list(value)
    elif name == "ui_geometry":
        res = _as_geometry(value)
    elif name in UPDATE_TEXT_FIELDS:
        res = _as_voice_name(value)
        if res is not _INVALID:
            res = res[: UPDATE_TEXT_FIELDS[name]]
    else:  # a field without a rule: keep it as is
        res = value
    return copy.deepcopy(default) if res is _INVALID else res


def _short_repr(value: Any, limit: int = 60) -> str:
    try:
        r = repr(value)
    except Exception:
        r = f"<{type(value).__name__}>"
    return r if len(r) <= limit else r[: limit - 3] + "..."


def _same(a: Any, b: Any) -> bool:
    try:
        if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
            return False
        return bool(a == b) and isinstance(a, bool) == isinstance(b, bool)
    except Exception:  # e.g. numpy arrays: ambiguous truth value
        return False


def _validate(cfg: Config) -> Config:
    defaults = Config()
    out: dict[str, Any] = {}
    fixes: list[str] = []
    for f in fields(Config):
        default = getattr(defaults, f.name)
        raw = getattr(cfg, f.name, default)
        new = _validate_field(f.name, raw, default)
        out[f.name] = new
        if not _same(raw, new):
            fixes.append(f"{f.name}={_short_repr(raw)} -> {new!r}")

    # cross-field rules
    if out["danger_radius"] > out["warn_radius"]:
        fixes.append(f"danger_radius {out['danger_radius']!r} > warn_radius -> {out['warn_radius']!r}")
        out["danger_radius"] = out["warn_radius"]
    if out["minimap_mode"] == "manual" and out["manual_minimap_rect"] is None:
        fixes.append("minimap_mode 'manual' without manual_minimap_rect -> 'auto'")
        out["minimap_mode"] = "auto"
    for pos_field, (xy_field, fallback) in CUSTOM_POSITION_FALLBACK.items():
        if out[pos_field] == "custom" and out[xy_field] is None:
            fixes.append(f"{pos_field} 'custom' without {xy_field} -> {fallback!r}")
            out[pos_field] = fallback
    used_keys: set[str] = set()
    for hk_field in HOTKEY_FIELDS:
        key = out[hk_field]
        if key and key in used_keys:
            fixes.append(f"{hk_field} {key!r} already used by another hotkey -> ''")
            out[hk_field] = ""
        elif key:
            used_keys.add(key)

    if fixes:
        log.warning("Config: corrected %d value(s): %s", len(fixes), "; ".join(fixes))
    return Config(**out)


# --------------------------------------------------------------------------- persistence


def _replace_with_retry(src: str | os.PathLike[str], dst: str | os.PathLike[str],
                        attempts: int = 6, delay: float = 0.05) -> None:
    """``os.replace`` retried briefly (Windows: antivirus/indexer may hold the file). Raises on failure."""
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(delay * (i + 1))


def backup_path(path: Path) -> Path:
    """Where a corrupt config file is moved (``config.json.bak``)."""
    return path.with_name(path.name + BACKUP_SUFFIX)


def _backup_corrupt(path: Path, reason: str) -> None:
    bak = backup_path(path)
    try:
        _replace_with_retry(path, bak)
        log.warning("Config file %s is invalid (%s); moved to %s, using defaults", path, reason, bak)
    except Exception as exc:
        log.warning("Config file %s is invalid (%s) and could not be moved (%s); using defaults",
                    path, reason, exc)


def _resolve(path: str | os.PathLike[str] | None) -> Path:
    return Path(path) if path is not None else paths.config_path()


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """Load the config (default path: ``paths.config_path()``). Never raises.

    Missing/unreadable file -> defaults; corrupt file -> renamed ``.bak`` + defaults;
    unknown keys ignored; invalid values replaced by their default.
    """
    try:
        p = _resolve(path)
    except Exception:
        log.exception("Invalid config path %r; using defaults", path)
        return Config()
    with _io_lock:
        try:
            if not p.exists():
                log.info("No config file at %s; using defaults", p)
                return Config()
            if not p.is_file():
                log.warning("Config path %s is not a file; using defaults", p)
                return Config()
            size = p.stat().st_size
            if size > MAX_CONFIG_BYTES:
                _backup_corrupt(p, f"file too large ({size} bytes)")
                return Config()
            raw = p.read_bytes()
        except Exception as exc:
            log.warning("Cannot read config file %s (%s); using defaults", p, exc)
            return Config()
        try:
            text = raw.decode("utf-8-sig")   # tolerate a BOM (Notepad)
            data = json.loads(text)
        except (ValueError, RecursionError) as exc:  # UnicodeDecodeError, JSONDecodeError
            _backup_corrupt(p, type(exc).__name__)
            return Config()
        except Exception as exc:
            log.warning("Unexpected error parsing %s (%s); using defaults", p, exc)
            return Config()
        if not isinstance(data, dict):
            _backup_corrupt(p, f"top-level JSON is {type(data).__name__}, not an object")
            return Config()
    if "overlay_mode" not in data and data.get("hud_position") == "top_left":
        # pre-"overlay_mode" file: the old default HUD position (top-left, over LoL's ally
        # portraits) moves to the new default, just above the minimap
        data = {**data, "hud_position": "above_minimap"}
    cfg = Config.from_dict(data)
    log.info("Config loaded from %s", p)
    return cfg


def save_config(cfg: Config, path: str | os.PathLike[str] | None = None) -> bool:
    """Atomically write the (validated) config as UTF-8 JSON. Never raises.

    Returns True on success, False on failure (logged). The previous file is left intact
    if anything goes wrong.
    """
    tmp_name: str | None = None
    p: Path | None = None
    try:
        p = _resolve(path)
        if isinstance(cfg, Config):
            good = cfg.validated()
        else:
            good = Config.from_dict(cfg if isinstance(cfg, Mapping) else None)
        payload: dict[str, Any] = {"config_version": CONFIG_VERSION}
        payload.update(good.to_dict())
        text = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        with _io_lock:
            p.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=str(p.parent))
            try:
                fh = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
            except Exception:
                os.close(fd)
                raise
            with fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            _replace_with_retry(tmp_name, p)
            tmp_name = None
        log.debug("Config saved to %s", p)
        return True
    except Exception as exc:
        log.error("Cannot save config to %s: %s", p if p is not None else path, exc)
        return False
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
