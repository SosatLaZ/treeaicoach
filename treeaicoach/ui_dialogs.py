"""Help page, toasts and dialogs (changelog, about, onboarding), diagnostics, shortcuts.

Mixin of :class:`treeaicoach.ui.CoachApp` (split out of ``ui.py`` without any behaviour
change): the methods use the app state (``self.cfg``, ``self.ctk``, widgets ...) created in
``CoachApp.__init__`` and run on the Tk thread only. Not meant to be used on its own.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import logging
import threading
from pathlib import Path
from typing import Any, Callable

from treeaicoach import APP_NAME, __version__, paths, ui_kit
from treeaicoach.ui_common import (
    ACCENT,
    ACCENT_DIM,
    BG,
    BTN_H_SMALL,
    CARD_PAD,
    DANGER,
    DIM,
    GOLD,
    GOLD_HOVER,
    HOVER,
    LINE,
    LINE_STRONG,
    MUTED,
    PANEL_HI,
    PANEL_LO,
    RADIUS,
    RAISED,
    SAFE,
    SURFACE,
    TEAL,
    TEXT,
    TOAST_MS,
    WARNING,
    _game_json_path,
    _guarded,
    _report_function,
    game_datetime,
    open_path,
    state_key,
    ui_text,
)

log = logging.getLogger("treeaicoach.ui")   # same logger as before the split


class DialogsMixin:
    """Help page, toasts and dialogs (changelog, about, onboarding), diagnostics, shortcuts."""

    # ------------------------------------------------------------------ help page
    def _build_help_page(self) -> Any:
        ctk = self.ctk
        page, right, body = self._page("Aide", "Bien démarrer, touches, sécurité et dépannage", icon="help")
        b = self._button(right, "Mode guidé", lambda: self.show_onboarding(0), "secondary", icon="star",
                         height=BTN_H_SMALL)
        b.grid(row=0, column=0, padx=(0, 8))
        self._tip(b, "Relancer le mode guidé (niveau, Sans bordure, test de l'overlay)")
        self._button(right, "Nouveautés", self.show_changelog, "ghost", icon="star", height=BTN_H_SMALL).grid(
            row=0, column=1)
        steps = (
            ("Passe le jeu en mode « Sans bordure »",
             "Options du jeu → Vidéo → Mode d'affichage : Sans bordure (ou Fenêtré). En plein écran exclusif, "
             "la capture est noire et l'overlay invisible."),
            ("Lance TreeAI Coach",
             "Double-clique sur TreeAICoach.exe. L'analyse démarre toute seule et attend une partie : rien à "
             "installer, rien à configurer."),
            ("Joue ta partie",
             "Dès le chargement terminé, le coach trouve la minimap et suit les ennemis. Écoute les annonces et "
             "regarde les marques posées sur ta minimap."),
            ("Réagis aux alertes",
             "Un bip puis « Gank ! … recule ! » : recule tout de suite vers ta tour. « Attention » : un ennemi se "
             "rapproche, reste prudent." + (f" {self._key_label('hotkey_jungler')} : où est le jungler ?"
                                            if getattr(self.cfg, "hotkey_jungler", "") else "")),
            ("Consulte ton analyse",
             "À la fin de la partie, un rapport s'ouvre : morts, ganks subis, habitudes du jungler ennemi et "
             "conseils. Retrouve-le avec le replay sur « En jeu » et dans Analyses."),
        )
        s = self._section(body, 0, "Mode d'emploi en 5 étapes", icon="play")
        for i, (title, text) in enumerate(steps):
            r = self._frame(s)
            r.grid(row=i, column=0, sticky="ew", pady=10)
            r.grid_columnconfigure(1, weight=1)
            badge = self._number_badge(r, i + 1)
            badge.grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, 14), pady=(2, 0))
            self._label(r, title, self.fonts.h3, TEXT, anchor="w").grid(row=0, column=1, sticky="w")
            lbl = self._label(r, text, self.fonts.small, MUTED, anchor="w", justify="left", wraplength=560)
            lbl.grid(row=1, column=1, sticky="w")
            self._wrap_labels.append((lbl, 2 * CARD_PAD + 48))
        s = self._section(body, 1, "Sécurité et règles de Riot",
                          "TreeAI Coach fonctionne comme un logiciel de streaming (OBS, Discord) :", icon="shield")
        for i, text in enumerate((
                "Il lit uniquement l'écran (la minimap déjà visible) et l'API officielle « Live Client Data » "
                "fournie par le jeu.",
                "Aucune lecture ni écriture de la mémoire du jeu, aucune injection, aucune touche ni clic simulé.",
                "Aucun suivi des sorts ni des ultimes ennemis, aucune prédiction cachée : seulement ce que tu "
                "pourrais voir toi-même.",
                "L'overlay est une fenêtre séparée et transparente posée au-dessus du jeu, jamais dessinée dans "
                "le jeu.",
                "Besoin d'encore plus de prudence ? Active le « Mode sûr » (barre de gauche ou Ctrl+Maj+S).")):
            r = self._frame(s)
            r.grid(row=i, column=0, sticky="ew", pady=6)
            r.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(r, text="", image=self._icon("check", 14, TEAL), fg_color="transparent", width=16).grid(
                row=0, column=0, sticky="n", padx=(2, 12), pady=(3, 0))
            lbl = self._label(r, text, self.fonts.small, TEXT, anchor="w", justify="left", wraplength=580)
            lbl.grid(row=0, column=1, sticky="w")
            self._wrap_labels.append((lbl, 2 * CARD_PAD + 36))
        s = self._section(body, 2, "Touches", "En jeu : touches globales, à changer dans Réglages > Avancé. "
                                              "Dans cette fenêtre : raccourcis de l'application.", icon="keyboard")
        self._help_keys = s
        self._fill_help_keys()
        s = self._section(body, 3, "Dépannage", icon="target")
        for i, (q, a) in enumerate((
                ("« Capture noire »", "Le jeu est en plein écran exclusif : passe en « Sans bordure »."),
                ("La minimap n'est pas trouvée", "Le bouton « Calibrer » apparaît sur « En jeu » : trace un carré "
                 "autour de la minimap. Aussi dans Réglages > Détection."),
                ("Aucune voix", "Clique sur « Tester la voix ». Vérifie le volume de Windows ; sans Internet, "
                 "choisis une voix Windows dans Réglages > Voix."),
                ("L'overlay n'apparaît pas", "Mode Sans bordure obligatoire ; vérifie l'interrupteur « Overlay » de "
                 f"la barre de gauche (ou {self._key_label('hotkey_overlay')} en jeu), puis « Tester l'overlay »."),
                ("Trop d'annonces, ou pas assez", "Choisis ton niveau dans la barre de gauche, puis ajuste la "
                 "quantité de voix et la sensibilité dans Réglages > Voix."),
                ("Un autre problème", ("Pendant la partie, appuie sur " + self._key_label("hotkey_diag") +
                                       " (diagnostic complet de 60 s)" if getattr(self.cfg, "hotkey_diag", "") else
                                       "Pendant la partie, clique sur « Diagnostic complet » (page « En jeu »)") +
                 ", puis copie le diagnostic ci-dessous et colle-le dans ton message."))):
            r = self._frame(s)
            r.grid(row=i, column=0, sticky="ew", pady=5)
            r.grid_columnconfigure(0, weight=1)
            self._label(r, q, self.fonts.h3, GOLD_HOVER, anchor="w").grid(row=0, column=0, sticky="w")
            lbl = self._label(r, a, self.fonts.small, MUTED, anchor="w", justify="left", wraplength=600)
            lbl.grid(row=1, column=0, sticky="w")
            self._wrap_labels.append((lbl, 2 * CARD_PAD + 4))
        bar = self._frame(s)
        bar.grid(row=10, column=0, sticky="w", pady=(10, 4))
        self._button(bar, "Copier le diagnostic", self.copy_diagnostic, "secondary", icon="copy").grid(
            row=0, column=0, padx=(0, 8))
        self._button(bar, "Ouvrir les journaux", self.open_logs, "secondary", icon="folder").grid(row=0, column=1)
        s = self._section(body, 4, "À propos et mentions légales", f"{APP_NAME} {__version__}", icon="info")
        about = self._label(s, ui_kit.ABOUT_TEXT, self.fonts.small, MUTED, anchor="w", justify="left", wraplength=620)
        about.grid(row=0, column=0, sticky="w", pady=(12, 12))
        self._wrap_labels.append((about, 2 * CARD_PAD + 4))
        return page

    def _key_label(self, field: str) -> str:
        """Current binding of an in-game key ("F9", "Ctrl + F8"), or a plain-French "sans touche"."""
        key = str(getattr(self.cfg, field, "") or "").strip()
        return key.replace("+", " + ") if key else "sans touche"

    def _fill_help_keys(self) -> None:
        """Aide > Touches: the in-game keys with their CURRENT binding, then the window shortcuts."""
        s = getattr(self, "_help_keys", None)
        if s is None:
            return
        for w in s.winfo_children():
            w.destroy()
        rows = [(k, w, True) for k, w in ui_kit.game_keys(self.cfg)] + [(k, w, False) for k, w in ui_kit.SHORTCUTS]
        for i, (keys, what, in_game) in enumerate(rows):
            r = self._frame(s)
            r.grid(row=i, column=0, sticky="ew", pady=3)
            r.grid_columnconfigure(1, weight=1)
            self.ctk.CTkLabel(r, text=keys, font=self.fonts.tiny_bold, text_color=GOLD if in_game else MUTED,
                              fg_color=PANEL_LO, corner_radius=RADIUS, width=130, height=24).grid(
                row=0, column=0, sticky="w", padx=(0, 14))
            self._label(r, what + ("" if in_game else " (fenêtre)"), self.fonts.small, TEXT, anchor="w").grid(
                row=0, column=1, sticky="w")
        tail = self._frame(s)
        tail.grid(row=len(rows), column=0, sticky="ew", pady=(6, 10))

    def _number_badge(self, parent: Any, n: int) -> Any:
        """Step number: the display face in the accent colour (no badge, no circle)."""
        return self._label(parent, str(n), self.fonts.stat, ACCENT, anchor="n", width=26)

    def _error_page(self, key: str) -> Any:
        page = self.ctk.CTkFrame(self.content, fg_color=BG, corner_radius=0)
        page.is_error_page = True  # type: ignore[attr-defined]
        self._label(page, "Cette page n'a pas pu être affichée (voir les journaux).", self.fonts.h3,
                    DANGER).pack(pady=60)
        return page

    # ------------------------------------------------------------------ toasts & dialogs
    def show_toast(self, text: str, level: str = "info") -> None:
        """Small message at the bottom-right of the window for a few seconds (Tk thread)."""
        if self._closing:
            return
        try:
            if self._toast_frame is not None:
                self._toast_frame.destroy()
            if self._toast_job is not None:
                self.root.after_cancel(self._toast_job)
        except Exception:
            pass
        col = {"error": DANGER, "info": TEAL, "warning": WARNING}.get(level, TEAL)
        fr = self.ctk.CTkFrame(self.root, fg_color=PANEL_HI, corner_radius=0, border_width=1, border_color=LINE_STRONG)
        bar = self.ctk.CTkFrame(fr, width=3, height=22, corner_radius=0, fg_color=col)
        bar.grid(row=0, column=0, sticky="ns", padx=(1, 10), pady=1)
        self._label(fr, ui_text(text), self.fonts.small, TEXT, justify="left", wraplength=380).grid(
            row=0, column=1, padx=(0, 14), pady=9)
        fr.place(relx=1.0, rely=1.0, anchor="se", x=-16, y=-16)
        fr.lift()
        self._toast_frame = fr
        self._toast_job = self.root.after(TOAST_MS + (2000 if level == "error" else 0), self._hide_toast)

    def show_error(self, text: str) -> None:
        """French error toast (thread-safe: re-posted to the Tk thread if needed)."""
        if threading.current_thread() is not threading.main_thread():
            self._dispatcher.post(lambda: self.show_toast(text, "error"))
            return
        self.show_toast(text, "error")

    def _hide_toast(self) -> None:
        self._toast_job = None
        try:
            if self._toast_frame is not None:
                self._toast_frame.destroy()
        except Exception:
            pass
        self._toast_frame = None

    def _dialog(self, title: str, subtitle: str | None = None, icon: str | None = None,
                width: int = 460) -> tuple[Any, Any, Any, Callable[[], None]]:
        """Themed modal-less dialog: (toplevel, body frame, button bar, close function)."""
        ctk = self.ctk
        top = ctk.CTkToplevel(self.root)
        top.title(title)
        top.resizable(False, False)
        top.transient(self.root)
        top.configure(fg_color=SURFACE)
        self._set_window_icon(top)
        top.grid_columnconfigure(0, weight=1)
        card = self._frame(top)
        card.grid(row=0, column=0, sticky="nsew")
        card.grid_columnconfigure(0, weight=1)
        head = self._frame(card)
        head.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 0))
        head.grid_columnconfigure(1, weight=1)
        self._label(head, ui_text(title), self.fonts.state, TEXT, anchor="w").grid(row=0, column=1, sticky="w")
        self._hline(card, LINE_STRONG).grid(row=1, column=0, sticky="ew", padx=20, pady=(8, 0))
        if subtitle:
            self._label(card, ui_text(subtitle), self.fonts.small, MUTED, anchor="w", justify="left",
                        wraplength=width - 60).grid(row=2, column=0, sticky="w", padx=20, pady=(8, 0))
        body = self._frame(card)
        body.grid(row=3, column=0, sticky="nsew", padx=20, pady=(10, 0))
        body.grid_columnconfigure(0, weight=1)
        bar = self._frame(card)
        bar.grid(row=4, column=0, sticky="e", padx=20, pady=(14, 16))

        def close() -> None:
            try:
                top.grab_release()
            except Exception:
                pass
            try:
                top.destroy()
            except Exception:
                pass
            if getattr(self, "_open_dialog", None) is top:
                self._open_dialog = None

        top.protocol("WM_DELETE_WINDOW", close)
        top.bind("<Escape>", lambda _e: close(), add="+")
        old = getattr(self, "_open_dialog", None)
        if old is not None:
            try:
                old.destroy()
            except Exception:
                pass
        self._open_dialog = top
        top._close = close  # type: ignore[attr-defined]
        return top, body, bar, close

    def _place_dialog(self, top: Any, grab: bool = True) -> None:
        try:
            top.update_idletasks()
            x = self.root.winfo_rootx() + (self.root.winfo_width() - top.winfo_width()) // 2
            y = self.root.winfo_rooty() + (self.root.winfo_height() - top.winfo_height()) // 3
            top.geometry(f"+{max(0, x)}+{max(0, y)}")
            top.lift()
            top.focus_force()
            if grab:
                top.grab_set()
        except Exception:
            pass

    def _confirm(self, title: str, text: str, yes: str, on_yes: Callable[[], None],
                 kind: str = "danger") -> None:
        top, body, bar, close = self._dialog(title, icon="info")
        self._label(body, text, self.fonts.small, TEXT, anchor="w", justify="left", wraplength=400).grid(
            row=0, column=0, sticky="w")

        def ok() -> None:
            close()
            on_yes()

        self._button(bar, "Annuler", close, "secondary", width=110).grid(row=0, column=0, padx=(0, 8))
        self._button(bar, yes, ok, kind, width=130).grid(row=0, column=1)
        self._place_dialog(top)

    # ------------------------------------------------------------------ dialogs: changelog, about, onboarding
    def _first_run_dialogs(self) -> None:
        """Onboarding on the very first launch, else "Nouveautés" once per version."""
        if self._closing:
            return
        try:
            if not getattr(self.cfg, "ui_onboarding_done", True):
                self.show_onboarding()
            elif getattr(self.cfg, "ui_seen_changelog", ui_kit.CHANGELOG_VERSION) != ui_kit.CHANGELOG_VERSION:
                self.show_changelog()
        except Exception:
            log.exception("First-run dialog failed")

    def _mark(self, **fields: Any) -> None:
        """Store UI bookkeeping fields (onboarding / changelog seen) without applying anything live."""
        upd = {k: v for k, v in fields.items() if hasattr(self.cfg, k)}
        if upd:
            self.cfg = dataclasses.replace(self.cfg, **upd).validated()
            self._schedule_save()

    @_guarded
    def show_changelog(self) -> None:
        top, body, bar, close = self._dialog(f"Nouveautés v{ui_kit.CHANGELOG_VERSION}",
                                             "Ce qui change dans cette version de TreeAI Coach.", "sparkle", 500)
        for i, (title, text) in enumerate(ui_kit.CHANGELOG):
            r = self._frame(body)
            r.grid(row=i, column=0, sticky="ew", pady=5)
            r.grid_columnconfigure(1, weight=1)
            self.ctk.CTkLabel(r, text="", image=self._icon("check", 14, TEAL), fg_color="transparent",
                              width=16).grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, 10), pady=(3, 0))
            self._label(r, title, self.fonts.h3, TEXT, anchor="w").grid(row=0, column=1, sticky="w")
            self._label(r, text, self.fonts.small, MUTED, anchor="w", justify="left", wraplength=400).grid(
                row=1, column=1, sticky="w", pady=(2, 0))

        def ok() -> None:
            close()
            self._mark(ui_seen_changelog=ui_kit.CHANGELOG_VERSION)

        top.protocol("WM_DELETE_WINDOW", ok)
        self._button(bar, "OK", ok, "primary", width=80).grid(row=0, column=0)
        self._place_dialog(top, grab=False)

    @_guarded
    def show_about(self) -> None:
        top, body, bar, close = self._dialog("À propos de TreeAI Coach", f"Version {__version__}", "info", 520)
        self._label(body, ui_kit.ABOUT_TEXT, self.fonts.small, TEXT, anchor="w", justify="left",
                    wraplength=440).grid(row=0, column=0, sticky="w")
        self._button(bar, "Nouveautés", lambda: (close(), self.show_changelog()), "ghost", icon="star",
                     width=130).grid(row=0, column=0, padx=(0, 8))
        self._button(bar, "Fermer", close, "primary", width=110).grid(row=0, column=1)
        self._place_dialog(top, grab=False)

    @_guarded
    def show_onboarding(self, step: int = 0) -> None:
        """Guided first run ("Mode guidé", 3 steps): player level -> borderless check -> overlay + voice test."""
        steps = ui_kit.onboarding_steps()
        step = min(max(int(step), 0), len(steps) - 1)
        title, text = steps[step]
        top, body, bar, close = self._dialog("Mode guidé", f"Étape {step + 1} sur {len(steps)}", None, 520)
        self._onboarding_step = step
        dots = self._frame(body)
        dots.grid(row=0, column=0, sticky="w", pady=(0, 12))
        import tkinter as tk  # noqa: PLC0415

        for i in range(len(steps)):
            tk.Frame(dots, width=self._scaled(40), height=max(2, self._scaled(3)), bd=0, highlightthickness=0,
                     bg=ACCENT if i <= step else LINE_STRONG).grid(row=0, column=i, padx=(0, self._scaled(4)))
        r = self._frame(body)
        r.grid(row=1, column=0, sticky="ew")
        r.grid_columnconfigure(1, weight=1)
        self._number_badge(r, step + 1).grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, 14))
        self._label(r, title, self.fonts.h3, TEXT, anchor="w").grid(row=0, column=1, sticky="w")
        self._label(r, text, self.fonts.small, MUTED, anchor="w", justify="left", wraplength=400).grid(
            row=1, column=1, sticky="w", pady=(3, 0))
        extra = self._frame(body)
        extra.grid(row=2, column=0, sticky="ew", pady=(14, 0), padx=(40, 0))
        if step == 0:
            from treeaicoach import skill as _skill  # noqa: PLC0415

            cur = _skill.normalize(getattr(self.cfg, "skill_level", "intermediaire"))
            for i, (key, label) in enumerate(_skill.SKILL_LEVELS):
                b = self._button(extra, label, lambda k=key: (self.apply_skill_level(k), self._sync_skill_seg(),
                                                              close(), self.show_onboarding(0)),
                                 "primary" if key == cur else "secondary", width=100, height=28)
                b.grid(row=0, column=i, padx=(0, 6))
            self._label(extra, _skill.SKILL_HELP.get(cur, ""), self.fonts.tiny, MUTED, anchor="w", justify="left",
                        wraplength=420).grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))
        elif step == 1:
            status = self._label(extra, "Lecture des réglages du jeu…", self.fonts.small, MUTED, anchor="w")
            status.grid(row=0, column=0, sticky="w", padx=(0, 12))

            def check() -> None:
                status.configure(text="Lecture des réglages du jeu…", text_color=MUTED)

                def job() -> tuple[int, str]:
                    from treeaicoach import game_settings  # noqa: PLC0415

                    gs = game_settings.load_game_settings()
                    return ui_kit.window_mode_status(getattr(gs, "window_mode", None))

                def done(res: tuple[int, str]) -> None:
                    level, msg = res
                    try:
                        status.configure(text=msg, text_color={0: SAFE, 2: DANGER}.get(level, WARNING))
                    except Exception:
                        pass       # dialog closed meanwhile

                self._dispatcher.run(job, done, None, name="TreeAI-ui-window-mode")

            self._button(extra, "Vérifier", check, "secondary", icon="refresh", width=100, height=28).grid(
                row=0, column=1)
            self._label(extra, "Lu dans les fichiers de réglages du jeu (lecture seule). Change-le en jeu si besoin, "
                        "puis clique sur Vérifier.", self.fonts.tiny, DIM, anchor="w", justify="left",
                        wraplength=420).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
            check()
        else:
            self._button(extra, "Tester l'overlay", self.test_overlay, "secondary", icon="overlay", width=150,
                         height=28).grid(row=0, column=0, padx=(0, 8))
            self._button(extra, "Écouter", self.test_voice, "secondary", icon="voice", width=110, height=28).grid(
                row=0, column=1)
            self._label(extra, "Le test de l'overlay ne marche qu'en dehors d'une partie.", self.fonts.tiny, DIM,
                        anchor="w").grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

        def finish() -> None:
            close()
            self._mark(ui_onboarding_done=True, ui_seen_changelog=ui_kit.CHANGELOG_VERSION)
            self.show_toast("C'est prêt : lance une partie, l'analyse démarre toute seule.")

        top.protocol("WM_DELETE_WINDOW", finish)
        self._button(bar, "Passer", finish, "ghost", width=90).grid(row=0, column=0, padx=(0, 8))
        if step > 0:
            self._button(bar, "Précédent", lambda: (close(), self.show_onboarding(step - 1)), "secondary",
                         width=110).grid(row=0, column=1, padx=(0, 8))
        if step < len(steps) - 1:
            self._button(bar, "Suivant", lambda: (close(), self.show_onboarding(step + 1)), "primary",
                         width=110).grid(row=0, column=2)
        else:
            self._button(bar, "Terminer", finish, "primary", width=110).grid(row=0, column=2)
        self._place_dialog(top, grab=False)

    def _sync_skill_seg(self) -> None:
        """Sidebar level selector <- configuration (selected level: accent border, bold text)."""
        btns = getattr(self, "_skill_btns", None)
        if not btns:
            return
        try:
            from treeaicoach import skill as _skill  # noqa: PLC0415

            cur = _skill.normalize(getattr(self.cfg, "skill_level", "intermediaire"))
            for k, b in btns.items():
                on = k == cur
                b.configure(fg_color=ACCENT_DIM if on else RAISED, border_color=ACCENT if on else LINE,
                            text_color=TEXT if on else MUTED, hover_color=ACCENT_DIM if on else HOVER)
        except Exception:
            log.debug("skill selector sync failed", exc_info=True)

    # ------------------------------------------------------------------ widgets refresh, diagnostics
    def _refresh_all_widgets(self) -> None:
        for refresh in list(self._widgets_by_field.values()):
            try:
                refresh()
            except Exception:
                log.debug("Widget refresh failed", exc_info=True)
        self._refresh_radius_text()
        self._refresh_position_menus()
        self._sync_quick()
        self._sync_skill_seg()
        self._fill_help_keys()
        try:
            if "settings" in self._built:
                self._refresh_radar_rows()
                self._refresh_voice_rows()
        except Exception:
            log.debug("settings rows refresh failed", exc_info=True)

    def diagnostic(self) -> str:
        """Plain-text diagnostic report (no secret)."""
        log_file = None
        try:
            logs = paths.logs_dir()
            files = sorted(Path(logs).glob("*.log"), key=lambda f: f.stat().st_mtime)
            log_file = files[-1] if files else None
        except Exception:
            pass
        cpu = getattr(getattr(self, "_cpu", None), "value", None)
        return ui_kit.diagnostic_text(version=__version__, cfg=self.cfg, status=self._get_status(),
                                      engine=self.engine, overlay=self.overlay, detector=self._detector,
                                      voice=self.voice, log_file=log_file, data_dir=paths.user_data_dir(),
                                      cpu=cpu, demo=self.demo)

    @_guarded
    def copy_diagnostic(self) -> None:
        text = self.diagnostic()
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.show_toast("Diagnostic copié : colle-le (Ctrl+V) dans ton message.")

    @_guarded
    def open_logs(self) -> None:
        if not open_path(paths.logs_dir()):
            self.show_error("Impossible d'ouvrir le dossier des journaux.")

    @_guarded
    def open_last_report(self) -> None:
        """Open the most recent game report (generated first when missing)."""
        def job() -> list[dict]:
            fn = _report_function("list_games")
            return [g for g in (fn(5) or []) if isinstance(g, dict)] if fn is not None else []

        def done(games: list[dict]) -> None:
            if not games:
                self.show_toast("Aucun rapport pour l'instant : joue une partie avec l'analyse active.")
                return
            best = max(games, key=lambda g: game_datetime(g) or _dt.datetime.min)
            self.open_report(best)

        self._dispatcher.run(job, done, self.cb(lambda e: self.show_error(f"Rapport impossible : {e}")),
                             name="TreeAI-ui-last-report")

    @_guarded
    def copy_share_summary(self) -> None:
        """Copy a shareable summary of the most recent game (hype.share_summary) to the clipboard."""
        eng = self.engine
        stats = {}
        try:
            fn = getattr(eng, "hype_stats", None)
            stats = fn() if callable(fn) else {}
        except Exception:
            stats = {}

        def job() -> str | None:
            fn = _report_function("list_games")
            games = [g for g in (fn(5) or []) if isinstance(g, dict)] if fn is not None else []
            if not games:
                return None
            best = max(games, key=lambda g: game_datetime(g) or _dt.datetime.min)
            src = _game_json_path(best)
            if src is None or not Path(src).is_file():
                return None
            import json  # noqa: PLC0415

            from treeaicoach.analysis import analyze_game  # noqa: PLC0415
            from treeaicoach.hype import share_summary  # noqa: PLC0415

            return share_summary(analyze_game(json.loads(Path(src).read_text(encoding="utf-8"))), stats)

        def done(text: str | None) -> None:
            if not text:
                self.show_toast("Aucune partie enregistrée pour l'instant.")
                return
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.show_toast("Résumé copié : colle-le (Ctrl+V) où tu veux.")

        self._dispatcher.run(job, done, self.cb(lambda e: self.show_error(f"Résumé impossible : {e}")),
                             name="TreeAI-ui-share")

    @_guarded
    def clear_journal(self) -> None:
        self._journal_hidden.update(self._journal)
        self._journal.clear()
        self._render_journal()

    def _in_game(self) -> bool:
        st = self._get_status()
        return (st is not None and self._engine_running() and state_key(getattr(st, "state", None)) == "RUNNING"
                and getattr(st, "game_time", None) is not None)

    def request_close(self) -> None:
        """Window close button: confirm first while a game is being analysed."""
        if self._closing:
            return
        try:
            if getattr(self.cfg, "ui_confirm_quit", True) and self._in_game():
                self._confirm("Quitter pendant la partie ?",
                              "Une partie est en cours d'analyse : en quittant, tu n'auras plus d'alertes ni de "
                              "rapport pour cette partie. Tu peux plutôt réduire la fenêtre.", "Quitter",
                              self.close)
                return
        except Exception:
            log.exception("Close confirmation failed")
        self.close()
