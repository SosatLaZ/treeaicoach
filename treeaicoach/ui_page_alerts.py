"""Alerts & voice page ("Alertes et voix"): alert switches, voice engine / level, coach extras.

Mixin of :class:`treeaicoach.ui.CoachApp` (split out of ``ui.py`` without any behaviour
change): the methods use the app state (``self.cfg``, ``self.ctk``, widgets ...) created in
``CoachApp.__init__`` and run on the Tk thread only. Not meant to be used on its own.
"""

from __future__ import annotations

import logging
from typing import Any

from treeaicoach import ui_kit
from treeaicoach.ui_common import (
    AUTO_VOICE,
    BORDER_GOLD,
    DIM,
    ENGINE_LABELS,
    GOLD,
    HOTKEY_CHOICES,
    MUTED,
    PANEL_HI,
    RADIUS,
    TEXT,
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


class AlertsPageMixin:
    """Alerts & voice page ("Alertes et voix"): alert switches, voice engine / level, coach extras."""

    # ------------------------------------------------------------------ alerts & voice page
    def _build_alerts_page(self) -> Any:
        page, right, body = self._page("Alertes", "Ce que le coach annonce et comment il parle")
        self._button(right, "Tester la voix", self.test_voice, "secondary", icon="voice", height=26).grid(
            row=0, column=0)
        ex = _example_phrases()
        self._examples = _example_speech()

        # --- presets -------------------------------------------------------------------
        s = self._section(body, 0, "Préréglage", "Un clic pour tout régler (alertes et overlay). Tu peux ensuite "
                                                 "ajuster chaque option.", icon="sliders")
        _row, slot = self._row(s, "Style du coach", None)
        labels = [f"  {lbl}  " for _k, lbl in ui_kit.PRESET_LABELS]
        to_key = {f"  {lbl}  ": k for k, lbl in ui_kit.PRESET_LABELS}
        self.preset_seg = Segmented(self, slot, labels,
                                    self.cb(lambda lbl: self.apply_preset(to_key.get(lbl, ""))))
        self.preset_seg.grid(row=0, column=0)
        self.preset_lbl = self._row_desc(slot)
        self._refresh_preset_label()

        s = self._section(body, 1, "Alertes de gank", "Annonces vocales quand un ennemi menace ta position. "
                                                      "▶ fait entendre un exemple.", icon="swords")
        for field, title, key in (("alert_jungler_approach", "Jungler ennemi qui approche", "jungler_approach"),
                                  ("alert_roam", "Roam d'un autre ennemi", "roam_approach"),
                                  ("alert_collapse", "Plusieurs ennemis convergent", "collapse"),
                                  ("alert_jungler_spotted", "Jungler ennemi aperçu", "jungler_spotted"),
                                  ("alert_laner_mia", "Adversaire de voie disparu", "laner_mia")):
            self._switch_row(s, field, title, ex[key])
            self._example_button(key)

        s = self._section(body, 2, "Sensibilité",
                          "Plus la sensibilité est haute, plus les alertes arrivent tôt (et plus souvent).",
                          icon="target")
        self.radius_lbl: Any = None
        self._slider_row(s, "sensitivity", "Sensibilité des alertes", self._radius_text(), 0.6, 1.6, 0.05,
                         lambda v: f"× {fmt_decimal_fr(v, 2)}", float,
                         on_change=lambda _v: (self._refresh_radius_text(), self._refresh_preset_label()))
        self.radius_lbl = self._last_slot.desc_label

        s = self._section(body, 3, "Aides de jeu", "Rappels basés uniquement sur l'API officielle de Riot.",
                          icon="clock")
        self._switch_row(s, "objective_timers", "Minuteurs des objectifs", ex["objective_soon"])
        self._example_button("objective_soon")
        self._switch_row(s, "recall_reminder", "Rappel pour dépenser ton or", ex["recall_gold"])
        self._example_button("recall_gold")
        self._slider_row(s, "recall_gold_threshold", "Seuil d'or du rappel", "Or à partir duquel le coach "
                         "te conseille de rentrer.", 300, 5000, 50, lambda v: f"{fmt_int_fr(v)} PO", int)
        self._switch_row(s, "control_ward_reminder", "Balise de contrôle", ex["control_ward"])
        self._example_button("control_ward")
        self._switch_row(s, "death_recap", "Récap de mort", ex["death_recap"])
        self._example_button("death_recap")
        self._switch_row(s, "break_reminder", "Conseil de pause",
                         "Après 3 défaites d'affilée : « une pause de 10 minutes aide à rester concentré ».")

        s = self._section(body, 4, "Voix", "La voix neurale (en ligne) est la plus naturelle ; les voix Windows "
                                           "servent de secours hors ligne.", icon="voice")
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
        self._slider_row(s, "voice_rate", "Vitesse", "De -10 (lent) à 10 (rapide). Défaut : 2.", -10, 10, 1,
                         lambda v: f"{int(v):+d}" if int(v) else "0", int)
        self._slider_row(s, "voice_volume", "Volume", None, 0, 100, 1, lambda v: f"{int(v)} %", int)
        self._switch_row(s, "beep_on_danger", "Bip avant un danger", "Deux bips courts avant « Gank ! ».")
        self._refresh_voice_rows()

        s = self._section(body, 5, "Raccourcis clavier",
                          "Touches globales (RegisterHotKey, comme Discord ou OBS) : rien n'est envoyé au jeu.",
                          icon="keyboard")
        for field, title, desc in (
                ("hotkey_jungler", "Où est le jungler ?", "Annonce la dernière position connue du jungler ennemi."),
                ("hotkey_mute", "Couper / rétablir la voix", None),
                ("hotkey_overlay", "Afficher / masquer l'overlay", None),
                ("hotkey_ai", "Demander à l'IA", "Conseil d'achat et de macro immédiat (si un fournisseur "
                 "d'IA est configuré dans Réglages > IA).")):
            if not hasattr(self.cfg, field):
                continue
            cur = getattr(self.cfg, field) or "Désactivé"
            values = list(HOTKEY_CHOICES) + ([cur] if cur not in HOTKEY_CHOICES else [])
            self._choice_row(s, field, title, desc, [("" if v == "Désactivé" else v, v) for v in values],
                             width=150)
        try:
            self._build_coach_extras(body, 6)
        except Exception:
            log.exception("Cannot build the build-advice / caster sections")
        self._tabs(page, body, (("Alertes", ("Préréglage", "Alertes de gank", "Sensibilité")),
                                ("Voix", ("Voix", "Mode annonceur")),
                                ("Aides", ("Aides de jeu", "Conseils d'achat")),
                                ("Touches", ("Raccourcis clavier",))))
        return page

    def _build_coach_extras(self, body: Any, row: int) -> None:
        """Alertes page: build advice switches + "mode annonceur" (hype.py)."""
        if hasattr(self.cfg, "item_advice"):
            s = self._section(body, row, "Conseils d'achat", "Le prochain objet adapté à la partie (soins "
                              "adverses, ennemi très fort, dégâts magiques…), d'après l'API officielle.", icon="star")
            self._switch_row(s, "item_advice", "Conseils d'achat", "Ligne « Prochain objet » dans le HUD.")
            self._switch_row(s, "item_advice_toasts", "Bandeau à l'écran",
                             "Affiche le conseil en bandeau au retour en base, à la mort, aux niveaux 6/11/16.")
            self._switch_row(s, "item_advice_speak", "Lire les conseils d'achat à voix haute",
                             "Désactivé par défaut : le conseil reste écrit.")
        if hasattr(self.cfg, "caster_style"):
            from treeaicoach.hype import STYLE_LABELS  # noqa: PLC0415

            s = self._section(body, row + 1, "Mode annonceur",
                              "Probabilité de victoire en direct et, en style « Caster esport », des annonces "
                              "enflammées pour les grands moments (multikill, shutdown, ace, vol de Baron).",
                              icon="star")
            self._choice_row(s, "caster_style", "Style", "Sobre : rien n'est lu · Coach : la probabilité de "
                             "victoire est lue sur les gros retournements (+/-15 points, 3 min max) · Caster : "
                             "en plus, des annonces de commentateur.", STYLE_LABELS, segmented=True)
            if hasattr(self.cfg, "win_prob_hud"):
                self._switch_row(s, "win_prob_hud", "Afficher la probabilité de victoire",
                                 "Dans le HUD et le tableau de bord (modèle sur l'or, kills, tours, dragons, "
                                 "Baron et Elder).")

    def _row_desc(self, slot: Any) -> Any:
        """The description label of the row owning ``slot`` (created empty if the row had none)."""
        lbl = getattr(slot, "desc_label", None)
        if lbl is None:
            left = slot.master.grid_slaves(row=0, column=0)[0]
            lbl = self._label(left, " ", self.fonts.tiny, MUTED, anchor="w", justify="left", wraplength=430)
            lbl.grid(row=1, column=0, sticky="w", pady=(3, 0))
            slot.desc_label = lbl
        return lbl

    def _example_button(self, key: str) -> None:
        """A small "▶" button in the last row: speaks an example of this alert."""
        slot = self._last_slot
        b = self.ctk.CTkButton(slot, text="", width=30, height=28, corner_radius=RADIUS, fg_color="transparent",
                               hover_color=PANEL_HI, border_width=1, border_color=BORDER_GOLD,
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
        """Neural voice row only for auto / neural; Windows voice row only for auto / onecore / sapi."""
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
        return (f"Rayon d'alerte ≈ {fmt_int_fr(round(warn, -2))} unités · "
                f"danger ≈ {fmt_int_fr(round(danger, -2))} unités")

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
