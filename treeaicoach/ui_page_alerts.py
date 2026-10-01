"""Réglages > Voix ("ce que tu entends"): how much the coach speaks, gank alerts, reminders, the voice.

Mixin of :class:`treeaicoach.ui.CoachApp`: the methods use the app state (``self.cfg``, ``self.ctk``,
widgets ...) created in ``CoachApp.__init__`` and run on the Tk thread only. Not meant to be used on
its own. (This was the "Alertes" page before the settings were grouped in one page.)
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from treeaicoach.ui_common import (
    AUTO_VOICE,
    BORDER_GOLD,
    BTN_H_SMALL,
    DANGER_MODES,
    DIM,
    ENGINE_LABELS,
    GOLD,
    PANEL_HI,
    RADIUS,
    TEXT,
    VOICE_LEVELS,
    Dropdown,
    Segmented,
    _example_phrases,
    _example_speech,
    _guarded,
    _pct_value,
    fmt_decimal_fr,
    fmt_int_fr,
)

log = logging.getLogger("treeaicoach.ui")   # same logger as before the split


def danger_mode(cfg: Any) -> str:
    """The "Annonce d'un danger" choice from (beep_on_danger, danger_voice): "bip_voix" | "bip" | "voix"."""
    if not bool(getattr(cfg, "beep_on_danger", True)):
        return "voix"
    return "bip" if getattr(cfg, "danger_voice", "bip_voix") == "bip" else "bip_voix"


def danger_mode_fields(mode: str) -> dict[str, Any]:
    """(beep_on_danger, danger_voice) of an "Annonce d'un danger" choice."""
    if mode == "voix":
        return {"beep_on_danger": False, "danger_voice": "bip_voix"}
    return {"beep_on_danger": True, "danger_voice": "bip" if mode == "bip" else "bip_voix"}


class AlertsPageMixin:
    """Réglages > Voix: what the coach says out loud and with which voice."""

    # ------------------------------------------------------------------ Voix tab
    def _build_voice_tab(self, body: Any, row: int) -> int:
        ex = _example_phrases()
        self._examples = _example_speech()

        s = self._section(body, row, "Ce que le coach dit", "Seulement ce qui ne peut pas attendre : le reste est "
                                                            "écrit dans le panneau.")
        self._section_button(s, "Tester la voix", self.test_voice, icon="voice",
                             tip="Fait dire une alerte d'exemple au coach.")
        self._choice_row(s, "voice_level", "Quantité", "Minimal : ganks, « Recule », objectifs à 60 s. Normal : "
                         "en plus, les gros appels après un combat gagné. Bavard : tout est lu.", VOICE_LEVELS,
                         segmented=True)
        self._danger_row(s)
        self._switch_row(s, "stance_voice", "Annoncer la posture", "« Prudent » ou « Attaque » quand elle change.")
        if hasattr(self.cfg, "caster_style"):
            from treeaicoach.hype import STYLE_LABELS  # noqa: PLC0415

            self._choice_row(s, "caster_style", "Mode annonceur", "Sobre : rien n'est lu. Coach : la probabilité "
                             "de victoire sur les gros retournements. Caster : en plus, des annonces de "
                             "commentateur.", STYLE_LABELS, segmented=True)

        s = self._section(body, row + 1, "Alertes de gank", "Quand un ennemi menace ta position. ▶ fait entendre "
                                                            "un exemple.")
        for field, title, key in (("alert_jungler_approach", "Jungler ennemi qui approche", "jungler_approach"),
                                  ("gank_pre_alert", "Alerte immédiate", None),
                                  ("alert_roam", "Roam d'un autre ennemi", "roam_approach"),
                                  ("alert_collapse", "Plusieurs ennemis convergent", "collapse"),
                                  ("alert_jungler_spotted", "Jungler ennemi aperçu", "jungler_spotted"),
                                  ("alert_laner_mia", "Adversaire de voie disparu", "laner_mia")):
            desc = ex[key] if key else "« Lee Sin ! » dès que le jungler sort du brouillard près de toi."
            self._switch_row(s, field, title, desc)
            if key:
                self._example_button(key)
        self.radius_lbl: Any = None
        self._slider_row(s, "sensitivity", "Sensibilité", self._radius_text(), 0.6, 1.6, 0.05,
                         lambda v: f"× {fmt_decimal_fr(v, 2)}", float, on_change=lambda _v: self._refresh_radius_text())
        self.radius_lbl = self._last_slot.desc_label

        s = self._section(body, row + 2, "Rappels", "Écrits dans le panneau ; lus à voix haute en quantité "
                                                    "« Bavard » (l'objectif à 60 s est toujours lu).")
        self._switch_row(s, "objective_timers", "Annonce des objectifs", ex["objective_soon"])
        self._example_button("objective_soon")
        self._switch_row(s, "recall_reminder", "Rappel pour dépenser ton or", ex["recall_gold"])
        self._example_button("recall_gold")
        self._slider_row(s, "recall_gold_threshold", "Seuil d'or du rappel", "Or à partir duquel le coach "
                         "te conseille de rentrer.", 300, 5000, 50, lambda v: f"{fmt_int_fr(v)} PO", int)
        self._switch_row(s, "control_ward_reminder", "Balise de contrôle", ex["control_ward"])
        self._example_button("control_ward")
        self._switch_row(s, "death_recap", "Récap de mort", ex["death_recap"])
        self._example_button("death_recap")
        self._switch_row(s, "item_advice_speak", "Lire les conseils d'achat",
                         "Désactivé par défaut : le conseil reste écrit.")

        s = self._section(body, row + 3, "Voix", "La voix neurale (en ligne) est la plus naturelle ; les voix "
                                                 "Windows servent de secours hors ligne.")
        self._choice_row(s, "voice_engine", "Moteur de voix", "« Automatique » utilise la voix neurale si "
                         "Internet répond, sinon une voix Windows.", self._engine_choices(), width=260,
                         on_change=lambda _v: self._refresh_voice_rows())
        self._choice_row(s, "neural_voice", "Voix neurale", "Voix Microsoft en ligne (française).",
                         self._neural_choices(), width=260)
        self._neural_row = self._last_row
        if hasattr(self.cfg, "neural_rate"):
            self._slider_row(s, "neural_rate", "Vitesse de la voix neurale", "Défaut : +15 %.", -50, 100, 5,
                             lambda v: str(v).replace("%", " %") if isinstance(v, str) else f"{int(v):+d} %",
                             lambda v: f"{int(round(v)):+d}%", to_float=_pct_value)
            self._neural_rate_row = self._last_row
        _row, slot = self._row(s, "Voix Windows", "« Automatique » choisit la meilleure voix française installée.")
        self._windows_voice_row = _row
        self.voice_menu = Dropdown(self, slot, [AUTO_VOICE], self.cb(self._on_voice_choice), width=320)
        self.voice_menu.grid(row=0, column=0)
        self._fill_voice_menu()
        self._widgets_by_field["voice_name"] = lambda: self.voice_menu.set(self.cfg.voice_name or AUTO_VOICE)
        self._slider_row(s, "voice_rate", "Vitesse de la voix Windows", "De -10 (lent) à 10 (rapide). Défaut : 2.",
                         -10, 10, 1, lambda v: f"{int(v):+d}" if int(v) else "0", int)
        self._slider_row(s, "voice_volume", "Volume", None, 0, 100, 1, lambda v: f"{int(v)} %", int)
        self._refresh_voice_rows()
        return row + 4

    def _danger_row(self, body: Any) -> None:
        """One choice for two settings: is a danger a beep, a beep and a sentence, or a sentence only."""
        _row, slot = self._row(body, "Annonce d'un danger", "Bip + voix : le bip part tout de suite, la phrase "
                               "suit si elle est prête. Bip seul : le plus rapide, rien à écouter.")
        labels = [f"  {lbl}  " for _v, lbl in DANGER_MODES]
        to_value = {f"  {lbl}  ": v for v, lbl in DANGER_MODES}
        to_label = {v: f"  {lbl}  " for v, lbl in DANGER_MODES}

        def changed(label: str) -> None:
            mode = to_value.get(label)
            if mode is None:
                return
            upd = {k: v for k, v in danger_mode_fields(mode).items() if hasattr(self.cfg, k)}
            new = dataclasses.replace(self.cfg, **upd).validated()
            self._replace_config(new, changed=set(upd))

        seg = Segmented(self, slot, labels, self.cb(changed))
        seg.grid(row=0, column=0)
        self._danger_seg = seg

        def refresh() -> None:
            seg.set(to_label.get(danger_mode(self.cfg), labels[0]))
        refresh()
        self._widgets_by_field["beep_on_danger"] = refresh
        self._widgets_by_field["danger_voice"] = refresh

    def _example_button(self, key: str) -> None:
        """A small "▶" button in the last row: speaks an example of this alert."""
        slot = self._last_slot
        b = self.ctk.CTkButton(slot, text="", width=BTN_H_SMALL, height=BTN_H_SMALL - 2, corner_radius=RADIUS,
                               fg_color="transparent", hover_color=PANEL_HI, border_width=1, border_color=BORDER_GOLD,
                               image=self._icon("play", 11, GOLD), command=self.cb(lambda: self.play_example(key)))
        for w in slot.grid_slaves(row=0):
            w.grid_configure(column=int(w.grid_info().get("column", 0)) + 1)
        b.grid(row=0, column=0, padx=(0, 14))
        self._tip(b, "Entendre un exemple")

    @_guarded
    def play_example(self, key: str) -> None:
        """Speak the example sentence of an alert type (always audible, even when muted by settings)."""
        if self.voice is None:
            self.show_error("La synthèse vocale n'est pas disponible.")
            return
        text, level = self._examples.get(key, ("Attention, Lee Sin approche !", 1))
        self.voice.say(text, level)
        if getattr(self.voice, "backend", "") == "print":
            self.show_toast("Voix indisponible sur ce système : le message est écrit dans le journal.", "warning")

    def _voice_api(self) -> Any:
        """The voice object (or the VoiceEngine class before it exists) for the list_* selectors."""
        if self.voice is not None:
            return self.voice
        try:
            from treeaicoach.voice import VoiceEngine  # noqa: PLC0415

            return VoiceEngine
        except Exception:
            return None

    def _engine_choices(self) -> list[tuple[str, str]]:
        labels = dict(ENGINE_LABELS)
        values: list[str] = []
        fn = getattr(self._voice_api(), "list_engines", None)
        try:
            got = fn() if callable(fn) else None
            for item in got or []:
                v = item[0] if isinstance(item, (tuple, list)) else item
                if isinstance(item, (tuple, list)) and len(item) > 1 and isinstance(item[1], str):
                    labels.setdefault(str(v), item[1])
                values.append(str(v))
        except Exception:
            log.debug("list_engines failed", exc_info=True)
        if not values:
            values = [v for v, _l in ENGINE_LABELS]
        cur = getattr(self.cfg, "voice_engine", "auto")
        if cur not in values:
            values.append(cur)
        return [(v, labels.get(v, v)) for v in values]

    def _neural_choices(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        fn = getattr(self._voice_api(), "list_neural_voices", None)
        try:
            got = fn() if callable(fn) else None
            for item in got or []:
                if isinstance(item, (tuple, list)) and item:
                    out.append((str(item[0]), str(item[1]) if len(item) > 1 else str(item[0])))
                elif isinstance(item, str):
                    out.append((item, item))
        except Exception:
            log.debug("list_neural_voices failed", exc_info=True)
        if not out:
            try:
                from treeaicoach.tts_neural import NEURAL_VOICES  # noqa: PLC0415

                out = [(v, lbl) for v, lbl in NEURAL_VOICES]
            except Exception:
                out = [("fr-FR-DeniseNeural", "Denise (femme, France)")]
        cur = getattr(self.cfg, "neural_voice", "")
        if cur and cur not in dict(out):
            out.append((cur, cur))
        return out

    def _refresh_voice_rows(self) -> None:
        """Neural voice rows only for auto / neural; Windows voice row only for auto / onecore / sapi."""
        eng = getattr(self.cfg, "voice_engine", "auto")
        for row, show in ((getattr(self, "_neural_row", None), eng in ("auto", "neural")),
                          (getattr(self, "_neural_rate_row", None), eng in ("auto", "neural")),
                          (getattr(self, "_windows_voice_row", None), eng != "neural")):
            if row is None:
                continue
            try:
                lbl = row.grid_slaves(row=0, column=0)[0].grid_slaves(row=0, column=0)[0]
                lbl.configure(text_color=TEXT if show else DIM)
                for w in row.grid_slaves(row=0, column=1)[0].winfo_children():
                    w = getattr(w, "_dropdown", None) or getattr(w, "_toggle", None) or w
                    try:
                        w.configure(state="normal" if show else "disabled")
                    except Exception:
                        pass
            except Exception:
                log.debug("voice rows refresh failed", exc_info=True)

    def _radius_text(self) -> str:
        try:
            from treeaicoach.geometry import to_game_units  # noqa: PLC0415

            warn = to_game_units(self.cfg.effective_warn_radius())
            danger = to_game_units(self.cfg.effective_danger_radius())
        except Exception:
            warn = self.cfg.effective_warn_radius() * 14870.0
            danger = self.cfg.effective_danger_radius() * 14870.0
        return (f"Plus haut : alertes plus tôt (et plus souvent). Rayon ≈ {fmt_int_fr(round(warn, -2))} unités, "
                f"danger ≈ {fmt_int_fr(round(danger, -2))}.")

    def _refresh_radius_text(self) -> None:
        if getattr(self, "radius_lbl", None) is not None:
            try:
                self.radius_lbl.configure(text=self._radius_text())
            except Exception:
                pass

    def _on_voice_choice(self, label: str) -> None:
        self.set_option("voice_name", "" if label == AUTO_VOICE else label)

    def _fill_voice_menu(self) -> None:
        menu = getattr(self, "voice_menu", None)
        if menu is None:
            return
        values = [AUTO_VOICE] + list(self._voices)
        if self.cfg.voice_name and self.cfg.voice_name not in values:
            values.append(self.cfg.voice_name)
        menu.configure(values=values)
        menu.set(self.cfg.voice_name or AUTO_VOICE)

    def _load_voices(self) -> None:
        voice = self.voice
        if voice is None:
            return

        def job() -> list[str]:
            return list(voice.list_voices() or [])

        def done(voices: list[str]) -> None:
            self._voices = [v for v in voices if isinstance(v, str) and v][:60]
            self._fill_voice_menu()

        self._dispatcher.run(job, done, name="TreeAI-ui-voices")

