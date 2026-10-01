"""Player skill levels: one click adapts how much the coach says and shows.

The more experienced the player, the less basic guidance: an expert only gets what a
Challenger would still want to hear (ganks, fight calls, real dangers, power spikes),
a beginner gets explanations, reminders and the full map layer.
"""
from __future__ import annotations

import dataclasses
from typing import Any

SKILL_LEVELS: tuple[tuple[str, str], ...] = (
    ("debutant", "Débutant"),
    ("intermediaire", "Intermédiaire"),
    ("avance", "Avancé"),
    ("expert", "Expert"),
)

SKILL_HELP: dict[str, str] = {
    "debutant": "Tout est expliqué, une chose à la fois : la prochaine action, les rappels (retour, balises, achats).",
    "intermediaire": "L'essentiel : ganks, combats, objectifs et conseils précis. Les bases ne sont plus rappelées.",
    "avance": "Seulement les infos qui changent une décision : ganks, combats, objectifs, dangers, pics de puissance.",
    "expert": "Le strict minimum, comme un coach en tournoi : gank, ATTAQUE / RECULE et vrais dangers. IA seulement en urgence.",
}

#: Minimum tip priority shown in the HUD per level (tips.Tip.prio: 1 generic … 4 urgent).
TIP_MIN_PRIO: dict[str, int] = {"debutant": 1, "intermediaire": 2, "avance": 3, "expert": 4}

#: Config values applied when a level is chosen (only fields that exist on Config are used).
SKILL_PRESETS: dict[str, dict[str, Any]] = {
    "debutant": {
        "voice_level": "normal", "text_tips": True, "tip_toasts": False,
        "recall_reminder": True, "control_ward_reminder": True, "objective_timers": True,
        "item_advice": True, "item_advice_toasts": True, "alert_jungler_spotted": True,
        "overlay_show_roles": False, "overlay_show_last_seen": False, "overlay_show_allies": False,
        "hud_detailed": False, "death_recap": True,
    },
    "intermediaire": {
        "voice_level": "minimal", "text_tips": True, "tip_toasts": False,
        "recall_reminder": True, "control_ward_reminder": True, "objective_timers": True,
        "item_advice": True, "item_advice_toasts": True, "alert_jungler_spotted": True,
        "overlay_show_roles": False, "overlay_show_last_seen": False, "overlay_show_allies": False,
        "hud_detailed": False, "death_recap": True,
    },
    "avance": {
        "voice_level": "minimal", "text_tips": True, "tip_toasts": False,
        "recall_reminder": False, "control_ward_reminder": False, "objective_timers": True,
        "item_advice": True, "item_advice_toasts": False, "alert_jungler_spotted": True,
        "overlay_show_roles": False, "overlay_show_last_seen": False, "overlay_show_allies": False,
        "hud_detailed": False, "death_recap": False,
    },
    "expert": {
        "voice_level": "minimal", "text_tips": True, "tip_toasts": False,
        "recall_reminder": False, "control_ward_reminder": False, "objective_timers": False,
        "item_advice": False, "item_advice_toasts": False, "alert_jungler_spotted": False,
        "overlay_show_roles": False, "overlay_show_last_seen": False, "overlay_show_allies": False,
        "hud_detailed": False, "death_recap": False,
    },
}


def normalize(level: Any) -> str:
    """A valid level key ("intermediaire" for anything unknown)."""
    key = str(level or "").strip().lower()
    key = (key.replace("é", "e").replace("è", "e").replace(" ", ""))
    aliases = {"beginner": "debutant", "intermediate": "intermediaire", "advanced": "avance",
               "debutant": "debutant", "intermediaire": "intermediaire", "avance": "avance", "expert": "expert"}
    return aliases.get(key, "intermediaire")


def label(level: Any) -> str:
    return dict(SKILL_LEVELS)[normalize(level)]


def preset_changes(cfg: Any, level: Any) -> dict[str, Any]:
    """Config changes for ``level`` (fields that exist on ``cfg``), including ``skill_level``."""
    key = normalize(level)
    out = {k: v for k, v in SKILL_PRESETS[key].items() if hasattr(cfg, k)}
    if hasattr(cfg, "skill_level"):
        out["skill_level"] = key
    return out


def apply(cfg: Any, level: Any) -> Any:
    """A validated copy of ``cfg`` with the level preset applied. Never raises."""
    try:
        new = dataclasses.replace(cfg, **preset_changes(cfg, level))
        return new.validated() if hasattr(new, "validated") else new
    except Exception:
        return cfg


def tip_min_prio(cfg: Any) -> int:
    return TIP_MIN_PRIO[normalize(getattr(cfg, "skill_level", "intermediaire"))]


__all__ = ["SKILL_LEVELS", "SKILL_HELP", "SKILL_PRESETS", "TIP_MIN_PRIO", "normalize", "label",
           "preset_changes", "apply", "tip_min_prio"]
