"""Settings page ("Réglages"): general settings, optional AI advice, updates.

Mixin of :class:`treeaicoach.ui.CoachApp` (split out of ``ui.py`` without any behaviour
change): the methods use the app state (``self.cfg``, ``self.ctk``, widgets ...) created in
``CoachApp.__init__`` and run on the Tk thread only. Not meant to be used on its own.
"""

from __future__ import annotations

import dataclasses
import logging
import time
import webbrowser
from typing import Any

from treeaicoach import __version__, paths, ui_kit
from treeaicoach.config import Config
from treeaicoach.ui_common import (
    CARD_PAD,
    CTL_H,
    DANGER,
    DETECTORS,
    GOLD,
    MINIMAP_MODES,
    MINIMAP_SIDES,
    MUTED,
    SAFE,
    TEAL,
    TEXT,
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
    """Settings page ("Réglages"): general settings, optional AI advice, updates."""

    # ------------------------------------------------------------------ settings page
    def _build_settings_page(self) -> Any:
        page, right, body = self._page("Réglages", "Démarrage, minimap, IA, maintenance")
        b = self._button(right, "Diagnostic", self.copy_diagnostic, "ghost", icon="copy", height=26)
        b.grid(row=0, column=0)
        self._tip(b, "Copie un rapport technique (sans donnée personnelle) à coller dans ton message. (Ctrl+D)")
        s = self._section(body, 0, "Minimap", "Par défaut, la minimap est trouvée automatiquement. "
                                              "Calibre-la à la main si la détection échoue.", icon="map")
        self._choice_row(s, "minimap_mode", "Localisation", self._manual_rect_text(), MINIMAP_MODES, segmented=True,
                         on_change=self._on_minimap_mode)
        self._rect_desc = self._last_slot.desc_label
        _row, slot = self._row(s, "Calibration manuelle", "Trace un carré autour de la minimap sur une capture.")
        self._button(slot, "Calibrer la minimap", self.calibrate, "ghost", icon="target").grid(row=0, column=0)
        self._choice_row(s, "minimap_side", "Côté de la minimap", "Position de la minimap dans les options du jeu.",
                         MINIMAP_SIDES, segmented=True)
        _row, slot = self._row(s, "Relocaliser", "Recherche à nouveau la minimap (après un changement de "
                                                 "résolution ou d'échelle de l'interface).")
        self._button(slot, "Relocaliser maintenant", self.relocate, "secondary", icon="refresh").grid(row=0, column=0)

        s = self._section(body, 1, "Détection", icon="cpu")
        self._slider_row(s, "target_fps", "Images par seconde", "Plus c'est haut, plus c'est réactif (et plus "
                         "le processeur travaille). Défaut : 8.", 2, 20, 1, lambda v: f"{int(v)} i/s", float)
        self._choice_row(s, "detector_backend", "Détecteur", "Le réseau de neurones est plus précis ; le "
                         "détecteur classique sert de secours.", DETECTORS, width=240)
        self._switch_row(s, "download_skin_icons", "Télécharger les icônes de skins",
                         "Améliore la reconnaissance des champions avec un skin (CommunityDragon).")
        self._switch_row(s, "collect_samples", "Collecter des captures de minimap",
                         "Enregistre des minimaps pour améliorer le modèle (dossier « collect »).")
        self._slider_row(s, "collect_interval_s", "Intervalle de collecte", None, 0.5, 30, 0.5,
                         lambda v: f"{fmt_decimal_fr(v, 1)} s", float)

        s = self._section(body, 2, "Démarrage & rapports", icon="play")
        self._switch_row(s, "autostart", "Démarrer l'analyse au lancement",
                         "L'analyse attend une partie en arrière-plan (processeur ≈ 0 hors partie).")
        ok, reason = autostart_support()
        _row, slot = self._row(s, "Lancer avec Windows",
                               "Ouvre TreeAI Coach à l'ouverture de ta session." if ok else reason)
        self.win_autostart_var = self.ctk.BooleanVar(value=get_windows_autostart() if ok else False)
        sw = self._toggle(slot, self.win_autostart_var, self._on_windows_autostart)
        sw.grid(row=0, column=0)
        if not ok:
            sw.configure(state="disabled")
        self._switch_row(s, "post_game_report", "Rapport d'après-partie",
                         "Analyse chaque partie (morts, ganks subis, jungler ennemi) et écrit un rapport HTML.")
        self._switch_row(s, "open_report_automatically", "Ouvrir le rapport automatiquement",
                         "À la fin de la partie, dans ton navigateur.")

        s = self._section(body, 3, "Interface", icon="sliders")
        if hasattr(self.cfg, "safe_mode"):
            self._switch_row(s, "safe_mode", "Mode sûr", "Aucune alerte de gank ni suivi du jungler, aucune zone dans "
                             "le brouillard. Minuteurs et rappels restent actifs.",
                             on_change=lambda _v: self._sync_quick())
        if hasattr(self.cfg, "ui_remember_page"):
            self._switch_row(s, "ui_remember_page", "Rouvrir la dernière page",
                             "Au lancement, revient sur la page que tu regardais.")
        if hasattr(self.cfg, "ui_confirm_quit"):
            self._switch_row(s, "ui_confirm_quit", "Confirmer avant de quitter en partie",
                             "Évite de fermer le coach par erreur pendant une partie.")
        if hasattr(self.cfg, "ui_scaling"):
            self._choice_row(s, "ui_scaling", "Taille de l'interface",
                             "En plus de l'échelle d'affichage de Windows. Appliquée au prochain lancement.",
                             (("auto", "Auto"), ("90", "90 %"), ("100", "100 %"), ("110", "110 %"), ("125", "125 %")),
                             segmented=True,
                             on_change=lambda _v: self.show_toast("Nouvelle taille appliquée au prochain lancement."))
        _row, slot = self._row(s, "Mode guidé", "3 étapes : ton niveau, jeu en Sans bordure, test de l'overlay.")
        self._button(slot, "Relancer", lambda: self.show_onboarding(0), "secondary").grid(row=0, column=0)

        s = self._section(body, 4, "Maintenance et support", icon="info")
        _row, slot = self._row(s, "Diagnostic", "Version, moteur, détecteur, voix et dernières erreurs, à joindre "
                                                "à un signalement.")
        self._button(slot, "Copier le diagnostic", self.copy_diagnostic, "secondary", icon="copy").grid(
            row=0, column=0)
        _row, slot = self._row(s, "Diagnostic complet", "Enregistre 60 s d'analyse en partie (minimap, détections, "
                                                        "temps de calcul, réglages) dans un zip à joindre à un "
                                                        "signalement.")
        self._button(slot, "Enregistrer", self.start_diagnostic, "secondary", icon="report").grid(row=0, column=0)
        self._diag_desc = slot.desc_label
        _row, slot = self._row(s, "Journaux", "Utile pour signaler un problème.")
        self._button(slot, "Ouvrir les journaux", self.open_logs, "secondary",
                     icon="folder").grid(row=0, column=0)
        _row, slot = self._row(s, "Rapports de parties", "Rapports HTML d'après-partie.")
        self._button(slot, "Dernier rapport", self.open_last_report, "secondary", icon="report").grid(
            row=0, column=0, padx=(0, 8))
        self._button(slot, "Dossier", self.open_games_dir, "secondary", icon="folder").grid(row=0, column=1)
        _row, slot = self._row(s, "Données", str(paths.user_data_dir()))
        self._button(slot, "Ouvrir le dossier", lambda: open_path(paths.user_data_dir()), "secondary",
                     icon="folder").grid(row=0, column=0)
        _row, slot = self._row(s, "Réinitialiser", "Remet tous les réglages par défaut.")
        self._button(slot, "Réinitialiser", self.ask_reset, "danger").grid(row=0, column=0)
        try:
            self._build_ai_section(body, 5)
        except Exception:
            log.exception("Cannot build the AI section")
        try:
            self._build_updates_section(body, 6)
        except Exception:
            log.exception("Cannot build the updates section")
        self._tabs(page, body, (("Général", ("Démarrage & rapports", "Interface", "Mises à jour")),
                                ("Minimap", ("Minimap", "Détection")),
                                ("IA", ("IA (facultatif)",)),
                                ("Avancé", ("Maintenance et support",))))
        return page

    # ------------------------------------------------------------------ optional AI advice (ai_advisor.py)
    def _build_ai_section(self, body: Any, row: int) -> None:
        if not hasattr(self.cfg, "ai_provider"):
            return
        from treeaicoach import ai_advisor  # noqa: PLC0415

        s = self._section(body, row, "IA (facultatif)", "Un conseil d'achat et de macro écrit par une IA aux "
                          "moments clés (retour en base, mort, niveaux 6/11/16, 60 s avant dragon / Baron). "
                          "Désactivé par défaut ; ta clé reste sur ce PC et aucun pseudo n'est envoyé.",
                          icon="star")
        self._choice_row(s, "ai_provider", "Fournisseur", "Gemini, Groq et OpenRouter ont une offre gratuite ; "
                         "Ollama tourne sur ton PC.", ai_advisor.PROVIDER_CHOICES, width=260)
        _row, slot = self._row(s, "Clé API", "Collée ici, enregistrée localement (jamais exportée).")
        key_entry = self.ctk.CTkEntry(slot, width=300, height=CTL_H, font=self.fonts.small, show="•",
                                      placeholder_text="Clé du fournisseur")
        if self.cfg.ai_api_key:
            key_entry.insert(0, self.cfg.ai_api_key)
        key_entry.grid(row=0, column=0)
        save_key = self.cb(lambda _e=None: self.set_option("ai_api_key", key_entry.get().strip()))
        key_entry.bind("<FocusOut>", save_key, add="+")
        key_entry.bind("<Return>", save_key, add="+")
        self._ai_key_entry = key_entry
        defaults = ", ".join(f"{ui_kit.AI_SHORT.get(k, k)} : {p.default_model}"
                             for k, p in getattr(ai_advisor, "PROVIDERS", {}).items())
        _row, slot = self._row(s, "Modèle", f"Vide = modèle par défaut ({defaults}).")
        model_entry = self.ctk.CTkEntry(slot, width=300, height=CTL_H, font=self.fonts.small,
                                        placeholder_text="par défaut")
        if self.cfg.ai_model:
            model_entry.insert(0, self.cfg.ai_model)
        model_entry.grid(row=0, column=0)
        save_model = self.cb(lambda _e=None: self.set_option("ai_model", model_entry.get().strip()))
        model_entry.bind("<FocusOut>", save_model, add="+")
        model_entry.bind("<Return>", save_model, add="+")
        self._ai_model_entry = model_entry
        hk = getattr(self.cfg, "hotkey_ai", "") or "sans raccourci"
        _row, slot = self._row(s, "Tester la connexion", "Envoie une petite question de test au fournisseur. "
                               f"En partie : « Demander à l'IA » ({hk}) donne un conseil immédiat ; une revue "
                               "IA est ajoutée au rapport d'après-partie.")
        self._ai_test_btn = self._button(slot, "Tester", self.test_ai, "secondary", icon="check")
        self._ai_test_btn.grid(row=0, column=0, padx=(0, 8))
        self._button(slot, "Demander à l'IA", self.ask_ai, "ghost", icon="star").grid(row=0, column=1)
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
                         "s'affiche en bandeau et dans le HUD.")
        links = self._frame(s)
        links.grid(row=2 * s._rows, column=0, sticky="ew", pady=(4, 6))
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

    # ------------------------------------------------------------------ updates (updater.py)
    def _build_updates_section(self, body: Any, row: int) -> None:
        s = self._section(body, row, "Mises à jour", "Les nouvelles versions sont publiées sur GitHub ; "
                                                     "le fichier est vérifié (SHA-256) avant d'être installé.",
                          icon="download")
        _row, slot = self._row(s, f"Version installée : {__version__}", None)
        self._update_check_btn = self._button(slot, "Vérifier les mises à jour", self.check_updates,
                                              "secondary", icon="refresh")
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
        _row, slot = self._row(s, "Installer", "Télécharge la nouvelle version, la vérifie puis redémarre "
                                               "TreeAI Coach.")
        self._update_btn = self._button(slot, "Mettre à jour", self.install_update, "primary",
                                        state="disabled")
        self._update_btn.grid(row=0, column=0)
        self._update_manual_btn = self._button(slot, "Télécharger manuellement", self.open_manual_download,
                                               "secondary", icon="download")
        self._update_manual_btn.grid(row=0, column=1, padx=(6, 0))
        self._tip(self._update_manual_btn, "Ouvre le lien direct du dernier TreeAICoach.exe dans ton navigateur. "
                                           "Ferme TreeAI Coach, puis remplace l'ancien fichier par le nouveau.")
        _row, slot = self._row(s, "Jeton GitHub (dépôt privé)", "Facultatif : jeton d'accès personnel avec "
                               "lecture du dépôt, nécessaire tant que le dépôt est privé.")
        entry = self.ctk.CTkEntry(slot, width=300, height=CTL_H, font=self.fonts.small, show="•",
                                  placeholder_text="ghp_… ou github_pat_…")
        if self.cfg.github_token:
            entry.insert(0, self.cfg.github_token)
        entry.grid(row=0, column=0)
        save_token = self.cb(lambda _e=None: self.set_option("github_token", entry.get().strip()))
        entry.bind("<FocusOut>", save_token, add="+")
        entry.bind("<Return>", save_token, add="+")
        self._update_token_entry = entry
        self._switch_row(s, "check_updates_on_start", "Vérifier au démarrage",
                         "Cherche une nouvelle version en arrière-plan à chaque lancement.")
        info = self._update_info              # found by the silent check before this page was built
        if info is not None:
            self._btn_state(self._update_btn, True)
            self._set_update_status(f"Nouvelle version {getattr(info, 'version', '')} disponible.", GOLD)

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
            self._set_update_status(rep.message, DANGER)
            top, body, bar, close = self._dialog("Mise à jour non appliquée", None, width=520)
            self._label(body, ui_text(rep.message.split(" Tu peux aussi")[0]), self.fonts.small, TEXT, anchor="w",
                        justify="left", wraplength=470).grid(row=0, column=0, sticky="w")
            self._label(body, updater.MANUAL_DOWNLOAD_URL, self.fonts.tiny, MUTED, anchor="w", justify="left",
                        wraplength=470).grid(row=1, column=0, sticky="w", pady=(8, 0))
            self._button(bar, "Fermer", close, "ghost").grid(row=0, column=0, padx=(0, 6))
            self._button(bar, "Réessayer", lambda: (close(), self.show_page("settings"), self.check_updates()),
                         "secondary").grid(row=0, column=1, padx=(0, 6))
            self._button(bar, "Télécharger manuellement", lambda: (close(), self.open_manual_download()),
                         "primary", icon="download").grid(row=0, column=2)
            self._place_dialog(top, grab=False)

        self._dispatcher.run(updater.startup_report, done, None, name="TreeAI-update-report")

    def _set_update_status(self, text: str, color: str = MUTED) -> None:
        lbl = getattr(self, "_update_status", None)
        if lbl is not None:
            try:
                lbl.configure(text=text, text_color=color)
                box = getattr(self, "_update_status_box", None)
                if box is not None:
                    (box.grid if text else box.grid_remove)()
            except Exception:
                pass

    def _startup_update_check(self) -> None:
        """Silent background check at launch (frozen exe only): toast if a new version exists."""
        if self._closing or not self.cfg.check_updates_on_start:
            return
        self.check_updates(quiet=True)

    def check_updates(self, quiet: bool = False) -> None:
        """Check GitHub for a new version (background thread); ``quiet`` = toast only if available."""
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
            color = GOLD if res.available else (DANGER if res.status == updater.ERROR else TEAL)
            self._set_update_status(updater.manual_hint(res.message) if res.status == updater.ERROR
                                    else res.message, color)
            btn = getattr(self, "_update_btn", None)
            if btn is not None:
                self._btn_state(btn, bool(res.available and res.can_install))
            if res.available and quiet:
                self.show_toast(f"Nouvelle version {res.info.version} disponible : Réglages → Mises à jour.")
            elif not quiet:
                self.show_toast(res.message, "error" if res.status == updater.ERROR else "info")

        def failed(exc: BaseException) -> None:
            self._update_busy = False
            if not quiet:
                self._set_update_status(f"Vérification impossible : {exc}", DANGER)

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
            msg = updater.manual_hint(res.message)
            self._set_update_status(msg, DANGER)
            self.show_error(res.message + " Lien direct : Réglages > Mises à jour > Télécharger manuellement.")

        def failed(exc: BaseException) -> None:
            done(updater.ApplyResult(False, f"Mise à jour impossible : {exc}"))

        self._dispatcher.run(job, done, failed, name="TreeAI-update-install")

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
                      "Tous les réglages reviennent à leur valeur par défaut (la calibration de la minimap "
                      "et la position de la fenêtre sont conservées).", "Réinitialiser", self.reset_settings)

    @_guarded
    def reset_settings(self) -> None:
        keep = {"manual_minimap_rect": self.cfg.manual_minimap_rect, "ui_geometry": self.cfg.ui_geometry}
        for k in ("ui_onboarding_done", "ui_seen_changelog", "ui_last_page", "github_token", "ai_api_key",
                  "icon_scale_by_res"):
            if hasattr(self.cfg, k):
                keep[k] = getattr(self.cfg, k)
        new = dataclasses.replace(Config(), **keep).validated()
        self._replace_config(new, changed=set(f.name for f in dataclasses.fields(Config)))
        self._refresh_all_widgets()
        self.show_toast("Réglages réinitialisés.")
