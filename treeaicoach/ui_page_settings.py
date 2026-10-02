"""Réglages page: everything the player can set, in ONE page with tabs named after what they change.

Tabs (``ui_common.SETTINGS_TABS``): Général (start-up, after the game, window) · Affichage (what you
see in game: ui_page_overlay.py) · Voix (what you hear: ui_page_alerts.py) · Détection (minimap,
analysis) · IA (optional AI advice) · Mises à jour · Avancé (in-game keys, performance, maintenance).

Mixin of :class:`treeaicoach.ui.CoachApp`: the methods use the app state (``self.cfg``, ``self.ctk``,
widgets ...) created in ``CoachApp.__init__`` and run on the Tk thread only. Not meant to be used on
its own.
"""

from __future__ import annotations

import dataclasses
import inspect
import logging
import time
import webbrowser
from typing import Any

from treeaicoach import __version__, paths, ui_kit
from treeaicoach.config import Config
from treeaicoach.ui_common import (
    CAPTURE_BACKENDS,
    CARD_PAD,
    CTL_GAP,
    CTL_H,
    DANGER,
    DETECTORS,
    GOLD,
    HOTKEY_CHOICES,
    MINIMAP_MODES,
    MINIMAP_SIDES,
    MUTED,
    PERF_MODES,
    SAFE,
    SETTINGS_TABS,
    TEAL,
    TEXT,
    UI_SCALINGS,
    _guarded,
    autostart_support,
    fmt_decimal_fr,
    get_windows_autostart,
    open_path,
    set_windows_autostart,
    ui_text,
)

log = logging.getLogger("treeaicoach.ui")   # same logger as before the split


class SettingsPageMixin:
    """Réglages page: tabs Général / Affichage / Voix / Détection / IA / Mises à jour / Avancé."""

    # ------------------------------------------------------------------ page
    def _build_settings_page(self) -> Any:
        page, _right, body = self._page("Réglages", "Ce que le coach montre, dit et analyse", tabs=SETTINGS_TABS)
        self._settings_page = page
        builders = {"Général": self._build_general_tab, "Affichage": self._build_display_tab,
                    "Voix": self._build_voice_tab, "Détection": self._build_detection_tab,
                    "IA": self._build_ai_section, "Mises à jour": self._build_updates_section,
                    "Avancé": self._build_advanced_tab}
        # one panel per tab (body.tab_bodies), all built with the page at start-up (CoachApp.__init__)
        lazy = {tab: (lambda b=builders[tab], t=tab: self._build_settings_tab(b, body.tab_bodies[t], 0))
                for tab in SETTINGS_TABS}
        self._tabs(page, body, tuple((t, ()) for t in SETTINGS_TABS), on_select=self._on_settings_tab, lazy=lazy)
        return page

    def _build_settings_tab(self, build: Any, body: Any, row: int) -> Any:
        """Generator: the tab's sections one by one (see ``_tabs(lazy=...)``), then the row wrapping."""
        res = build(body, row)
        if inspect.isgenerator(res):
            yield from res
        try:     # the new rows wrap to the current width at once
            scale = max(0.5, self._scaled(100) / 100)
            self.root.after_idle(lambda: self._wrap_rows(int(self.content.winfo_width() / scale), force=True))
        except Exception:
            pass

    def _on_settings_tab(self, tab: str) -> None:
        self._settings_tab = tab
        if tab == "Affichage" and self._current_page == "settings":
            self._schedule_overlay_preview()

    # ------------------------------------------------------------------ Général
    def _build_general_tab(self, body: Any, row: int) -> int:
        s = self._section(body, row, "Démarrage")
        self._switch_row(s, "autostart", "Démarrer l'analyse au lancement",
                         "L'analyse attend ta partie en arrière-plan (processeur ≈ 0 hors partie).")
        ok, reason = autostart_support()
        _row, slot = self._row(s, "Lancer avec Windows",
                               "Ouvre TreeAI Coach à l'ouverture de ta session." if ok else reason)
        self.win_autostart_var = self.ctk.BooleanVar(value=get_windows_autostart() if ok else False)
        sw = self._toggle(slot, self.win_autostart_var, self._on_windows_autostart)
        sw.grid(row=0, column=0)
        if not ok:
            sw.configure(state="disabled")
        _row, slot = self._row(s, "Mode guidé", "3 étapes : ton niveau, jeu en Sans bordure, test de l'overlay "
                                                "et de la voix.")
        self._button(slot, "Relancer", lambda: self.show_onboarding(0), "secondary").grid(row=0, column=0)

        yield                                   # one section per idle slot (prebuild)
        s = self._section(body, row + 1, "Après la partie")
        self._switch_row(s, "post_game_report", "Rapport d'après-partie",
                         "Morts, ganks subis, jungler ennemi et coups notés, dans un rapport à ouvrir dans ton "
                         "navigateur.")
        self._switch_row(s, "open_report_automatically", "Ouvrir le rapport tout seul",
                         "À la fin de la partie, dans ton navigateur.")
        self._switch_row(s, "lcu_enabled", "Lire le client League of Legends",
                         "Après la partie, positions et or exacts pour le rapport (lecture seule, le client doit "
                         "rester ouvert).")
        self._switch_row(s, "break_reminder", "Conseil de pause",
                         "Après 3 défaites d'affilée : « une pause de 10 minutes aide à rester concentré ».")

        yield                                   # one section per idle slot (prebuild)
        s = self._section(body, row + 2, "Fenêtre")
        self._choice_row(s, "ui_scaling", "Taille de l'interface",
                         "En plus de l'échelle d'affichage de Windows. Appliquée au prochain lancement.",
                         UI_SCALINGS, segmented=True,
                         on_change=lambda _v: self.show_toast("Nouvelle taille appliquée au prochain lancement."))
        self._switch_row(s, "ui_confirm_quit", "Confirmer avant de quitter en partie",
                         "Évite de fermer le coach par erreur pendant une partie.")
        return row + 3

    # ------------------------------------------------------------------ Détection
    def _build_detection_tab(self, body: Any, row: int) -> int:
        s = self._section(body, row, "Minimap", "Trouvée toute seule au début de la partie. Calibre-la à la main "
                                                "seulement si elle n'est pas trouvée.")
        self._choice_row(s, "minimap_mode", "Localisation", self._manual_rect_text(), MINIMAP_MODES, segmented=True,
                         on_change=self._on_minimap_mode)
        self._rect_desc = self._last_slot.desc_label
        _row, slot = self._row(s, "Calibration manuelle", "Trace un carré autour de la minimap sur une capture.")
        self._button(slot, "Calibrer", self.calibrate, "secondary", icon="target").grid(row=0, column=0)
        self._choice_row(s, "minimap_side", "Côté de la minimap", "Position de la minimap dans les options du jeu.",
                         MINIMAP_SIDES, segmented=True)
        _row, slot = self._row(s, "Chercher à nouveau", "Après un changement de résolution ou d'échelle de "
                                                        "l'interface du jeu.")
        self._button(slot, "Chercher", self.relocate, "secondary", icon="refresh").grid(row=0, column=0)

        yield                                   # one section per idle slot (prebuild)
        s = self._section(body, row + 1, "Analyse")
        self._slider_row(s, "target_fps", "Images par seconde", "Plus c'est haut, plus les alertes sont réactives "
                         "(et plus le processeur travaille). Défaut : 12.", 2, 20, 1, lambda v: f"{int(v)} i/s",
                         float)
        self._choice_row(s, "detector_backend", "Détecteur", "Le réseau de neurones est plus précis ; le "
                         "détecteur classique sert de secours.", DETECTORS, width=240)
        self._switch_row(s, "download_skin_icons", "Télécharger les icônes de skins",
                         "Reconnaît mieux les champions avec un skin (CommunityDragon).")
        return row + 2

    # ------------------------------------------------------------------ Avancé
    def _build_advanced_tab(self, body: Any, row: int) -> int:
        s = self._section(body, row, "Touches en jeu", "Touches globales (comme Discord ou OBS) : rien n'est "
                                                        "envoyé au jeu.")
        for field, title, desc in (
                ("hotkey_jungler", "Où est le jungler ?", "Le coach dit la dernière position connue du jungler."),
                ("hotkey_mute", "Couper / rétablir la voix", None),
                ("hotkey_overlay", "Afficher / masquer l'overlay", None),
                ("hotkey_details", "Overlay détaillé", "À maintenir : jungler, 5 ennemis et alliés dans le panneau."),
                ("hotkey_ward", "Où poser une balise ?", "Montre les meilleurs emplacements tout de suite."),
                ("hotkey_ai", "Demander à l'IA", "Conseil d'achat et de macro immédiat (Réglages > IA)."),
                ("hotkey_diag", "Diagnostic complet", "Enregistre 60 s d'analyse pour un signalement. Pris en "
                 "compte au prochain démarrage de l'analyse.")):
            if not hasattr(self.cfg, field):
                continue
            cur = getattr(self.cfg, field) or "Désactivé"
            values = list(HOTKEY_CHOICES) + ([cur] if cur not in HOTKEY_CHOICES else [])
            self._choice_row(s, field, title, desc, [("" if v == "Désactivé" else v, v) for v in values], width=150)

        yield                                   # one section per idle slot (prebuild)
        s = self._section(body, row + 1, "Performance", "Les valeurs par défaut conviennent à presque tous les PC.")
        self._choice_row(s, "perf_mode", "Mode", "« Auto » mesure ton PC ; « PC modeste » analyse moins souvent "
                         "quand rien ne se passe.", PERF_MODES, segmented=True)
        self._switch_row(s, "adaptive_rate", "Cadence adaptative",
                         "4 à 6 images par seconde au calme, la cadence maximale dès qu'un ennemi approche.")
        self._slider_row(s, "overlay_fps", "Fluidité de l'overlay", "Images par seconde des marques sur la "
                         "minimap. Défaut : 30.", 10, 60, 5, lambda v: f"{int(v)} i/s", float)
        self._switch_row(s, "pause_when_unfocused", "Pause hors du jeu",
                         "Overlay masqué et analyse ralentie quand le jeu n'est pas la fenêtre active.")
        self._switch_row(s, "low_priority", "Priorité basse",
                         "Le jeu passe toujours avant TreeAI. Appliqué au prochain lancement.")
        self._switch_row(s, "eco_qos_v2", "Économie d'énergie de Windows 11",
                         "Peut faire saccader le suivi des ennemis : désactivé par défaut. Appliqué au prochain "
                         "lancement.")
        self._choice_row(s, "capture_backend", "Capture d'écran", "« Compatible » si la capture reste noire "
                         "ou figée avec « Auto ».", CAPTURE_BACKENDS, segmented=True)

        yield                                   # one section per idle slot (prebuild)
        s = self._section(body, row + 2, "Maintenance et support")
        _row, slot = self._row(s, "Diagnostic", "Version, moteur, détecteur, voix et dernières erreurs, à coller "
                                                "dans ton message (Ctrl+D).")
        self._button(slot, "Copier", self.copy_diagnostic, "secondary", icon="copy").grid(row=0, column=0)
        hk = str(getattr(self.cfg, "hotkey_diag", "") or "").replace("+", " + ")
        _row, slot = self._row(s, "Diagnostic complet", "Enregistre 60 s d'analyse en partie (minimap, détections, "
                               "temps de calcul, réglages) dans un zip à joindre à un signalement." +
                               (f" En jeu : {hk}." if hk else ""))
        self._button(slot, "Enregistrer", self.start_diagnostic, "secondary", icon="report").grid(row=0, column=0)
        self._diag_desc = slot.desc_label
        _row, slot = self._row(s, "Journaux", "Fichiers techniques, utiles pour signaler un problème.")
        self._button(slot, "Ouvrir", self.open_logs, "secondary", icon="folder").grid(row=0, column=0)
        _row, slot = self._row(s, "Données", str(paths.user_data_dir()))
        self._button(slot, "Ouvrir", lambda: open_path(paths.user_data_dir()), "secondary",
                     icon="folder").grid(row=0, column=0)
        if hasattr(self.cfg, "selfcheck_auto_diag"):
            self._switch_row(s, "selfcheck_auto_diag", "Diagnostic automatique",
                             "Si la détection est faible, un diagnostic de 60 s est enregistré (une fois par "
                             "partie) pour le signalement.")
        self._switch_row(s, "collect_samples", "Collecter des captures de minimap",
                         "Pour améliorer le modèle (dossier « collect »). Désactivé par défaut.")
        self._slider_row(s, "collect_interval_s", "Intervalle de collecte", None, 0.5, 30, 0.5,
                         lambda v: f"{fmt_decimal_fr(v, 1)} s", float)
        _row, slot = self._row(s, "Réinitialiser", "Remet tous les réglages par défaut (calibration, clés et "
                                                   "jeton conservés).")
        self._button(slot, "Réinitialiser", self.ask_reset, "danger").grid(row=0, column=0)
        return row + 3

    # ------------------------------------------------------------------ IA (ai_advisor.py)
    def _build_ai_section(self, body: Any, row: int) -> int:
        if not hasattr(self.cfg, "ai_provider"):
            return row
        from treeaicoach import ai_advisor  # noqa: PLC0415

        s = self._section(body, row, "Conseils IA (facultatif)", "Un conseil d'achat et de macro écrit par une IA "
                          "aux moments clés (retour en base, mort, niveaux 6/11/16, 60 s avant dragon / Baron). "
                          "Ta clé reste sur ce PC et aucun pseudo n'est envoyé.")
        self._choice_row(s, "ai_provider", "Fournisseur", "Gemini, Groq et OpenRouter ont une offre gratuite ; "
                         "Ollama tourne sur ton PC.", ai_advisor.PROVIDER_CHOICES, width=260)
        _row, slot = self._row(s, "Clé API", "Collée ici, enregistrée sur ce PC (jamais exportée).")
        self._ai_key_entry = self._entry_row(slot, "ai_api_key", "Clé du fournisseur", secret=True)
        defaults = ", ".join(f"{ui_kit.AI_SHORT.get(k, k)} : {p.default_model}"
                             for k, p in getattr(ai_advisor, "PROVIDERS", {}).items())
        _row, slot = self._row(s, "Modèle", f"Vide = modèle par défaut ({defaults}).")
        self._ai_model_entry = self._entry_row(slot, "ai_model", "par défaut")
        hk = getattr(self.cfg, "hotkey_ai", "") or "sans raccourci"
        _row, slot = self._row(s, "Tester la connexion", "Envoie une petite question de test au fournisseur. "
                               f"En partie : « Demander à l'IA » ({hk}) donne un conseil immédiat ; une revue "
                               "IA est ajoutée au rapport d'après-partie.")
        self._ai_test_btn = self._button(slot, "Tester", self.test_ai, "secondary", icon="check")
        self._ai_test_btn.grid(row=0, column=0, padx=(0, CTL_GAP))
        self._button(slot, "Demander", self.ask_ai, "ghost", icon="star").grid(row=0, column=1)
        box = self._frame(s)
        box.grid(row=2 * s._rows, column=0, sticky="ew", pady=(0, 6))
        box.grid_columnconfigure(0, weight=1)
        s._rows += 1
        self._ai_status = self._label(box, "", self.fonts.small, MUTED, anchor="w", justify="left",
                                      wraplength=620)
        self._ai_status.grid(row=0, column=0, sticky="w")
        self._wrap_labels.append((self._ai_status, 2 * CARD_PAD + 4))
        self._ai_status_box = box
        box.grid_remove()               # shown with the first test result
        self._switch_row(s, "ai_speak", "Lire le conseil IA à voix haute", "Désactivé par défaut : le conseil "
                         "s'affiche en bandeau et dans le panneau.")
        links = self._frame(s)
        links.grid(row=2 * s._rows, column=0, sticky="ew", pady=(4, 10))
        s._rows += 1
        self._label(links, "Obtenir une clé gratuite :", self.fonts.tiny, MUTED, anchor="w").grid(
            row=0, column=0, sticky="w", padx=(0, 8))
        for i, (label, url) in enumerate((("Gemini", "https://aistudio.google.com/apikey"),
                                          ("Groq", "https://console.groq.com/keys"),
                                          ("OpenRouter", "https://openrouter.ai/keys"),
                                          ("Ollama (local)", "https://ollama.com"))):
            lnk = self._label(links, label, self.fonts.tiny, TEAL, anchor="w", cursor="hand2")
            lnk.grid(row=0, column=i + 1, sticky="w", padx=(0, 10))
            lnk.bind("<Button-1>", self.cb(lambda _e=None, u=url: webbrowser.open(u)), add="+")
            self._tip(lnk, url)
        return row + 1

    def _entry_row(self, slot: Any, field: str, placeholder: str, secret: bool = False) -> Any:
        """A text field saved on Entrée / when it loses the focus (and refreshed by a reset)."""
        entry = self.ctk.CTkEntry(slot, width=300, height=CTL_H, font=self.fonts.small, show="•" if secret else "",
                                  placeholder_text=placeholder)
        if getattr(self.cfg, field, ""):
            entry.insert(0, getattr(self.cfg, field))
        entry.grid(row=0, column=0)
        save = self.cb(lambda _e=None: self.set_option(field, entry.get().strip()))
        entry.bind("<FocusOut>", save, add="+")
        entry.bind("<Return>", save, add="+")

        def refresh() -> None:
            try:            # being typed (not saved yet): keep what the player is writing
                if entry.focus_get() is getattr(entry, "_entry", entry):
                    return
            except Exception:
                pass
            if entry.get().strip() != getattr(self.cfg, field, ""):
                entry.delete(0, "end")
                if getattr(self.cfg, field, ""):
                    entry.insert(0, getattr(self.cfg, field))
        self._widgets_by_field[field] = refresh
        return entry

    def _set_ai_status(self, text: str, color: str = MUTED) -> None:
        lbl = getattr(self, "_ai_status", None)
        if lbl is not None:
            try:
                lbl.configure(text=text, text_color=color)
                box = getattr(self, "_ai_status_box", None)
                if box is not None:
                    (box.grid if text else box.grid_remove)()
            except Exception:
                pass

    @_guarded
    def test_ai(self) -> None:
        """"Tester" button: one request to the chosen provider (background thread)."""
        for field, entry in (("ai_api_key", getattr(self, "_ai_key_entry", None)),
                             ("ai_model", getattr(self, "_ai_model_entry", None))):
            if entry is not None and entry.get().strip() != getattr(self.cfg, field, ""):
                self.set_option(field, entry.get().strip())
        from treeaicoach import ai_advisor  # noqa: PLC0415

        cfg = self.cfg
        self._set_ai_status("Test en cours…")

        def done(res: Any) -> None:
            ok, msg = res
            self._ai_test = (bool(ok), "clé OK" if ok else "erreur")
            self._set_ai_status(msg, SAFE if ok else DANGER)

        self._dispatcher.run(lambda: ai_advisor.check_connection(cfg), done,
                             self.cb(lambda e: self._set_ai_status(f"Test impossible : {e}", DANGER)),
                             name="TreeAI-ui-ai-test")

    def _ai_budget(self) -> str:
        """"IA 3/5" while a game runs (engine.ai_budget_text), "" otherwise."""
        eng = self.engine
        fn = getattr(eng, "ai_budget_text", None) if eng is not None else None
        if not callable(fn) or not self._in_game():
            return ""
        try:
            return str(fn() or "")
        except Exception:
            return ""

    @_guarded
    def test_ai_key(self) -> None:
        """Dashboard "Tester la clé": one tiny request on a worker thread, result in French (row + toast)."""
        if self._ai_test_busy:
            return
        self._ai_test_busy = True
        cfg = self.cfg

        def done(res: Any) -> None:
            self._ai_test_busy = False
            ok, short, msg = res
            self._ai_test = (bool(ok), str(short))
            self._set_ai_status(msg, SAFE if ok else DANGER)
            self.show_toast(msg, "info" if ok else "error")

        def failed(exc: BaseException) -> None:
            self._ai_test_busy = False
            self._ai_test = (False, "erreur")
            self.show_error(f"Test de la clé impossible : {exc}")

        self._dispatcher.run(lambda: ui_kit.test_ai_key(cfg), done, self.cb(failed), name="TreeAI-ui-ai-key")

    # ------------------------------------------------------------------ Mises à jour (updater.py)
    def _build_updates_section(self, body: Any, row: int) -> int:
        from treeaicoach import updater  # noqa: PLC0415

        s = self._section(body, row, "Mises à jour", "Les nouvelles versions sont publiées sur GitHub ; le fichier "
                                                     "est vérifié (SHA-256) avant d'être installé.")
        _row, slot = self._row(s, f"Version installée : {__version__}", None)
        self._update_check_btn = self._button(slot, "Vérifier", self.check_updates, "secondary", icon="refresh")
        self._update_check_btn.grid(row=0, column=0)
        box = self._frame(s)
        box.grid(row=2 * s._rows, column=0, sticky="ew", pady=(0, 6))
        box.grid_columnconfigure(0, weight=1)
        s._rows += 1
        self._update_status = self._label(box, "", self.fonts.small, MUTED, anchor="w", justify="left",
                                          wraplength=620)
        self._update_status.grid(row=0, column=0, sticky="w")
        self._wrap_labels.append((self._update_status, 2 * CARD_PAD + 4))
        self._update_status_box = box
        box.grid_remove()               # shown with the first message
        self._update_bar = self.ctk.CTkProgressBar(box, height=8)
        self._update_bar.set(0)
        self._update_bar.grid(row=1, column=0, sticky="ew", pady=(6, 2))
        self._update_bar.grid_remove()
        # the direct link, shown after an error: always a way out, even when the in-app update fails
        self._update_manual = self._label(box, updater.MANUAL_DOWNLOAD_URL, self.fonts.tiny, TEAL, anchor="w",
                                          justify="left", wraplength=620, cursor="hand2")
        self._update_manual.grid(row=2, column=0, sticky="w", pady=(4, 0))
        self._update_manual.bind("<Button-1>", self.cb(lambda _e=None: self.open_manual_download()), add="+")
        self._tip(self._update_manual, "Ouvrir le lien dans le navigateur")
        self._update_manual.grid_remove()
        _row, slot = self._row(s, "Installer", "Télécharge la nouvelle version, la vérifie puis redémarre "
                                               "TreeAI Coach.")
        self._update_btn = self._button(slot, "Mettre à jour", self.install_update, "primary", icon="download",
                                        state="disabled")
        self._update_btn.grid(row=0, column=0)
        self._update_manual_btn = self._button(slot, "Télécharger", self.open_manual_download, "secondary")
        self._update_manual_btn.grid(row=0, column=1, padx=(CTL_GAP, 0))
        self._tip(self._update_manual_btn, "Ouvre le lien direct du dernier TreeAICoach.exe dans ton navigateur. "
                                           "Ferme TreeAI Coach, puis remplace l'ancien fichier par le nouveau.")
        self._switch_row(s, "check_updates_on_start", "Vérifier au démarrage",
                         "Cherche une nouvelle version en arrière-plan à chaque lancement.")
        _row, slot = self._row(s, "Jeton GitHub (dépôt privé)", "Facultatif : jeton d'accès personnel avec "
                               "lecture du dépôt, nécessaire tant que le dépôt est privé.")
        self._update_token_entry = self._entry_row(slot, "github_token", "ghp_… ou github_pat_…", secret=True)
        info = self._update_info              # found by the silent check before this page was built
        if info is not None:
            self._btn_state(self._update_btn, True)
            self._set_update_status(f"Nouvelle version {getattr(info, 'version', '')} disponible.", GOLD)
        return row + 1

    @_guarded
    def open_manual_download(self) -> None:
        """Fallback when the in-app update fails: the direct link to the published exe."""
        from treeaicoach import updater  # noqa: PLC0415

        try:
            webbrowser.open(updater.MANUAL_DOWNLOAD_URL)
            self.show_toast("Lien de téléchargement ouvert dans le navigateur.")
        except Exception:
            self._copy_text(updater.MANUAL_DOWNLOAD_URL)
            self.show_toast("Lien copié : colle-le dans ton navigateur.")

    def _copy_text(self, text: str) -> None:
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except Exception:
            log.debug("clipboard failed", exc_info=True)

    def _check_last_update(self) -> None:
        """At launch: did the last in-app update really apply? (updater.startup_report)."""
        from treeaicoach import updater  # noqa: PLC0415

        def done(rep: Any) -> None:
            if rep is None or self._closing:
                return
            if rep.ok:
                self.show_toast(rep.message)
                return
            self._set_update_status(rep.message, DANGER, manual=True)
            top, body, bar, close = self._dialog("Mise à jour non appliquée", None, width=520)
            self._label(body, ui_text(rep.message.split(" Tu peux aussi")[0]), self.fonts.small, TEXT, anchor="w",
                        justify="left", wraplength=470).grid(row=0, column=0, sticky="w")
            self._label(body, updater.MANUAL_DOWNLOAD_URL, self.fonts.tiny, MUTED, anchor="w", justify="left",
                        wraplength=470).grid(row=1, column=0, sticky="w", pady=(8, 0))
            self._button(bar, "Fermer", close, "ghost").grid(row=0, column=0, padx=(0, 6))
            self._button(bar, "Réessayer", lambda: (close(), self.open_settings("Mises à jour"), self.check_updates()),
                         "secondary").grid(row=0, column=1, padx=(0, 6))
            self._button(bar, "Télécharger", lambda: (close(), self.open_manual_download()),
                         "primary", icon="download").grid(row=0, column=2)
            self._place_dialog(top, grab=False)

        self._dispatcher.run(updater.startup_report, done, None, name="TreeAI-update-report")

    def _set_update_status(self, text: str, color: str = MUTED, manual: bool = False) -> None:
        """Status line of the Mises à jour tab (+ the direct download link after an error)."""
        lbl = getattr(self, "_update_status", None)
        if lbl is not None:
            try:
                lbl.configure(text=text, text_color=color)
                box = getattr(self, "_update_status_box", None)
                if box is not None:
                    (box.grid if text else box.grid_remove)()
                link = getattr(self, "_update_manual", None)
                if link is not None:
                    (link.grid if manual else link.grid_remove)()
            except Exception:
                pass

    def _show_update_available(self, info: Any) -> None:
        """A new version exists: the sidebar shows it (one click to this tab) until it is installed."""
        btn = getattr(self, "_update_side", None)
        if btn is None:
            return
        try:
            if info is None:
                btn.grid_remove()
                return
            btn.configure(text=f"Nouvelle version {getattr(info, 'version', '')}".strip())
            btn.grid()
        except Exception:
            log.debug("update button failed", exc_info=True)

    def _startup_update_check(self) -> None:
        """Silent background check at launch (frozen exe only): sidebar button if a new version exists."""
        if self._closing or not self.cfg.check_updates_on_start:
            return
        self.check_updates(quiet=True)

    def check_updates(self, quiet: bool = False) -> None:
        """Check GitHub for a new version (background thread); ``quiet`` = say something only if available."""
        if getattr(self, "_update_busy", False):
            return
        self._update_busy = True
        from treeaicoach import updater
        entry = getattr(self, "_update_token_entry", None)
        if entry is not None and entry.get().strip() != self.cfg.github_token:
            self.set_option("github_token", entry.get().strip())
        cfg = self.cfg
        if not quiet:
            self._set_update_status("Recherche d'une nouvelle version…")

        def done(res: Any) -> None:
            self._update_busy = False
            self._update_info = res.info if res.available else None
            self._show_update_available(self._update_info)
            err = res.status == updater.ERROR
            color = GOLD if res.available else (DANGER if err else TEAL)
            self._set_update_status(updater.manual_hint(res.message) if err else res.message, color, manual=err)
            btn = getattr(self, "_update_btn", None)
            if btn is not None:
                self._btn_state(btn, bool(res.available and res.can_install))
            if res.available and quiet:
                self.show_toast(f"Nouvelle version {res.info.version} disponible : bouton en bas à gauche.")
            elif not quiet:
                self.show_toast(res.message, "error" if err else "info")

        def failed(exc: BaseException) -> None:
            self._update_busy = False
            if not quiet:
                self._set_update_status(f"Vérification impossible : {exc}", DANGER, manual=True)

        self._dispatcher.run(lambda: updater.check_for_update(cfg), done, failed, name="TreeAI-update-check")

    def install_update(self) -> None:
        """Download + verify + swap the exe, then close the app (the batch relaunches it)."""
        info = getattr(self, "_update_info", None)
        if info is None or self._update_busy:
            return
        from treeaicoach import updater
        self._update_busy = True
        self._btn_state(self._update_btn, False)
        self._update_check_btn.configure(state="disabled")
        self._update_bar.set(0)
        self._update_bar.grid()
        self._set_update_status(f"Téléchargement de la version {info.version}…")
        cfg = self.cfg
        last = [0.0]

        def progress(done_b: int, total: int) -> None:     # worker thread: throttled post
            now = time.monotonic()
            if now - last[0] < 0.15 and done_b < total:
                return
            last[0] = now
            frac = done_b / total if total else 0.0
            text = (f"Téléchargement de la version {info.version}… "
                    f"{fmt_decimal_fr(done_b / 1e6, 1)} / {fmt_decimal_fr(total / 1e6, 1)} Mo")
            self._dispatcher.post(lambda: (self._update_bar.set(frac), self._set_update_status(text)))

        def job() -> Any:
            dl = updater.download_update(info, cfg, progress=progress)
            if not dl.ok:
                return dl
            return updater.apply_update(dl.path, info)

        def done(res: Any) -> None:
            self._update_busy = False
            self._update_check_btn.configure(state="normal")
            if res.ok and isinstance(res, updater.ApplyResult):
                self._set_update_status(res.message, TEAL)
                self.show_toast(res.message)
                self.root.after(800, self.close)
                return
            self._update_bar.grid_remove()
            self._btn_state(self._update_btn, True)
            self._set_update_status(updater.manual_hint(res.message), DANGER, manual=True)
            self.show_error(res.message + " Lien direct : Réglages > Mises à jour.")

        def failed(exc: BaseException) -> None:
            done(updater.ApplyResult(False, f"Mise à jour impossible : {exc}"))

        self._dispatcher.run(job, done, failed, name="TreeAI-update-install")

    # ------------------------------------------------------------------ helpers
    def _manual_rect_text(self) -> str:
        r = self.cfg.manual_minimap_rect
        if not r:
            return "Aucune calibration manuelle enregistrée."
        return (f"Calibration : {r['w']} × {r['h']} px en ({r['x']}, {r['y']}) "
                f"pour un écran {r['screen_w']} × {r['screen_h']}.")

    def _on_minimap_mode(self, value: str) -> None:
        if value == "manual" and not self.cfg.manual_minimap_rect:
            self.show_toast("Calibre d'abord la minimap pour utiliser le mode manuel.")
            self.calibrate()
        else:
            self.relocate(quiet=True)

    @_guarded
    def _on_windows_autostart(self) -> None:
        want = bool(self.win_autostart_var.get())
        if set_windows_autostart(want):
            self.show_toast("TreeAI Coach se lancera avec Windows." if want else
                            "Lancement avec Windows désactivé.")
        else:
            self.win_autostart_var.set(get_windows_autostart())
            self.show_error("Impossible de modifier le lancement avec Windows.")

    @_guarded
    def ask_reset(self) -> None:
        self._confirm("Réinitialiser les réglages ?",
                      "Tous les réglages reviennent à leur valeur par défaut (la calibration de la minimap, "
                      "tes clés et la position de la fenêtre sont conservées).", "Réinitialiser",
                      self.reset_settings)

    @_guarded
    def reset_settings(self) -> None:
        keep = {"manual_minimap_rect": self.cfg.manual_minimap_rect, "ui_geometry": self.cfg.ui_geometry}
        for k in ("ui_onboarding_done", "ui_seen_changelog", "github_token", "ai_api_key", "icon_scale_by_res"):
            if hasattr(self.cfg, k):
                keep[k] = getattr(self.cfg, k)
        new = dataclasses.replace(Config(), **keep).validated()
        self._replace_config(new, changed=set(f.name for f in dataclasses.fields(Config)))
        self._refresh_all_widgets()
        self.show_toast("Réglages réinitialisés.")
