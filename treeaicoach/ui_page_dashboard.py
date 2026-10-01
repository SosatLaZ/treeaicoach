"""Dashboard page ("Tableau de bord"): status hero, live tiles, coach gauge, journal.

Mixin of :class:`treeaicoach.ui.CoachApp` (split out of ``ui.py`` without any behaviour
change): the methods use the app state (``self.cfg``, ``self.ctk``, widgets ...) created in
``CoachApp.__init__`` and run on the Tk thread only. Not meant to be used on its own.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from PIL import Image

from treeaicoach import ui_kit
from treeaicoach.ui_common import (
    ACCENT,
    ACCENT_DIM,
    ACCENT_HOVER,
    ALLY_RING,
    BG,
    BORDER,
    DIM,
    ENEMY_RING,
    LEVEL_COLORS,
    LINE_STRONG,
    MUTED,
    ON_ACCENT,
    PANEL,
    PANEL_HI,
    PANEL_LO,
    RADAR_PX,
    RADIUS,
    RADIUS_DIALOG,
    SUNKEN,
    SURFACE,
    SWITCH_OFF,
    TEAL,
    TEXT,
    WARNING,
    WARNING_BG,
    WIDE_MAX,
    HeroBanner,
    circle_icon,
    flat_placeholder,
    radar_placeholder,
)

log = logging.getLogger("treeaicoach.ui")   # same logger as before the split


class DashboardPageMixin:
    """Dashboard page ("Tableau de bord"): status hero, live tiles, coach gauge, journal."""

    # ------------------------------------------------------------------ dashboard
    def _build_dashboard(self) -> Any:
        ctk = self.ctk
        page, right, body = self._page("En jeu", "Minimap et alertes en direct", max_width=WIDE_MAX)
        self.dash_safe_var = ctk.BooleanVar(value=bool(getattr(self.cfg, "safe_mode", False)))
        self.dash_safe = self._toggle(right, self.dash_safe_var,
                                      lambda: self.set_safe_mode(bool(self.dash_safe_var.get())), color=WARNING,
                                      small=True, text="Mode sûr")
        self.dash_safe.grid(row=0, column=0, padx=(0, 18))
        self._tip(self.dash_safe.lbl, "Mode sûr : aucune alerte de gank ni suivi du jungler, aucune zone dans le "
                                  "brouillard. Minuteurs et rappels restent actifs. (Ctrl+Maj+S)")
        self.btn_test_voice = self._button(right, "Tester la voix", self.test_voice, "ghost", icon="voice", width=0,
                                           height=26)
        self.btn_test_voice.grid(row=0, column=1, padx=(0, 8))
        self._tip(self.btn_test_voice, "Fait dire une alerte d'exemple au coach.")
        self.btn_demo = self._button(right, "Mode démo", self.toggle_demo, "ghost", icon="demo", width=0, height=26)
        self.btn_demo.grid(row=0, column=2, padx=(0, 8))
        self._tip(self.btn_demo, "Partie simulée : le jungler ennemi vient te ganker vers 40 s.")
        self.btn_calib = self._button(right, "Calibrer la minimap", self.calibrate, "ghost", icon="target", width=0,
                                      height=26)
        self.btn_calib.grid(row=0, column=3)
        self._tip(self.btn_calib, "Trace un carré autour de la minimap si elle n'est pas trouvée toute seule.")

        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0)
        body.grid_rowconfigure(3, weight=1)

        # --- banner (break reminder...) -------------------------------------------------
        self.banner = ctk.CTkFrame(body, fg_color=WARNING_BG, corner_radius=RADIUS, border_width=0)
        self.banner.grid_columnconfigure(1, weight=1)
        ctk.CTkFrame(self.banner, width=3, height=20, corner_radius=0, fg_color=WARNING).grid(
            row=0, column=0, sticky="ns", padx=(0, 10))
        self.banner_lbl = self._label(self.banner, "", self.fonts.small, TEXT, anchor="w", justify="left")
        self.banner_lbl.grid(row=0, column=1, sticky="w", pady=8)
        self._button(self.banner, "Compris", self._dismiss_banner, "ghost", width=90, height=28).grid(
            row=0, column=2, padx=10)
        self._banner_dismissed: str | None = None

        # --- hero: state + pulse, clock, threat gauge, start button ----------------------
        self._gauge_frac = 0.0
        self._gauge_target = 0.0
        self._gauge_color = DIM
        hero = HeroBanner(self, body)
        self.hero = hero
        hero.canvas.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        self.status_card = hero.canvas
        self.state_title = hero.title
        self.state_msg = hero.msg
        self.clock_lbl = hero.clock
        self.threat_lbl = hero.threat
        self.threat_detail = hero.detail
        self.demo_badge = hero.badge
        self.btn_start = ctk.CTkButton(hero.canvas, text="Démarrer l'analyse", width=150, height=34,
                                       corner_radius=RADIUS, font=self.fonts.button, fg_color=ACCENT,
                                       hover_color=ACCENT_HOVER, text_color=ON_ACCENT, text_color_disabled=DIM,
                                       bg_color=hero.right_bg, image=self._icon("play", 12, ON_ACCENT),
                                       compound="left", command=self.cb(self.toggle_engine))
        hero.attach_button(self.btn_start)

        # --- champion select: pre-game card (champ_select.py, League Client, read-only) ----
        self.cs_card = ctk.CTkFrame(body, fg_color=SURFACE, corner_radius=RADIUS_DIALOG, border_width=1,
                                    border_color=ACCENT_DIM)
        self.cs_card.grid_columnconfigure(0, weight=1)
        self._cs_sig: Any = None
        self._cs_busy = False
        self._cs_polled = 0.0

        # --- left column: teams + journal --------------------------------------------
        left = self._frame(body)
        left.grid(row=3, column=0, sticky="nsew", padx=(0, 16))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(2, weight=1)

        # --- live coach strip: my role (+ lane swap), "jouer plus fort ou non", top tip, AI counter
        co = self._frame(left)
        self.coach_card = co
        co.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        co.grid_columnconfigure(1, weight=1)
        self.coach_gauge_lbl = self._label(co, "-", self.fonts.num, DIM, anchor="w")
        self.coach_gauge_lbl.grid(row=0, column=0, sticky="w", padx=(0, 12), pady=(0, 0))
        self._tip(self.coach_gauge_lbl, "Jouer plus fort ou non : ATTAQUE ▲▲, PLUS FORT ▲, NORMAL, "
                                        "PRUDENT ▼, SAFE ▼▼ (selon ton avance, les combats et ton face-à-face).")
        self.coach_role_lbl = self._label(co, "Rôle : -", self.fonts.small, MUTED, anchor="w")
        self.coach_role_lbl.grid(row=0, column=1, sticky="w")
        self._tip(self.coach_role_lbl, "Rôle détecté d'après la partie (et les échanges de voie).")
        self.coach_ai_lbl = self._label(co, "", self.fonts.tiny_bold, TEAL, anchor="e")
        self.coach_ai_lbl.grid(row=0, column=2, sticky="e", padx=(8, 0))
        self._tip(self.coach_ai_lbl, "Conseils IA utilisés dans cette partie : 5 automatiques max "
                                     "+ 1 en urgence (F8 à part).")
        self.coach_tip_lbl = self._label(co, "Le conseil du moment s'affichera ici pendant la partie.",
                                         self.fonts.small, DIM, anchor="w", justify="left", wraplength=520)
        self.coach_tip_lbl.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(2, 8))
        self._hline(co).grid(row=2, column=0, columnspan=3, sticky="ew")
        self._coach_sig: tuple = ()

        en = self._frame(left)
        self.enemies_card = en
        en.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        en.grid_columnconfigure(0, weight=1)
        head = self._frame(en)
        head.grid(row=0, column=0, sticky="ew")
        head.grid_columnconfigure(1, weight=1)
        self._caption(head, "Ennemis", ENEMY_RING, anchor="w").grid(row=0, column=0, sticky="w")
        self.visible_lbl = self._label(head, "", self.fonts.tiny, MUTED, anchor="e")
        self.visible_lbl.grid(row=0, column=2, sticky="e")
        self.jungler_lbl = self._label(en, "Jungler : en attente d'une partie", self.fonts.small, MUTED,
                                       anchor="w", justify="left", wraplength=480)
        self.jungler_lbl.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        slots = self._frame(en)
        slots.grid(row=2, column=0, sticky="ew", pady=(6, 8))
        self.enemy_slots: list[dict[str, Any]] = []
        for i in range(5):
            slots.grid_columnconfigure(i, weight=1, uniform="enemy")
            box = ctk.CTkFrame(slots, fg_color=PANEL, corner_radius=RADIUS, border_width=1, border_color=PANEL)
            box.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 2, 0))
            box.grid_columnconfigure(0, weight=1)
            icon = ctk.CTkLabel(box, text="", image=self._enemy_image(None, None, "empty"), fg_color="transparent")
            icon.grid(row=0, column=0, pady=(9, 0))
            name = self._label(box, "-", self.fonts.tiny_bold, MUTED)
            name.grid(row=1, column=0, padx=4, pady=(3, 0))
            status = self._label(box, " ", self.fonts.tiny, DIM)
            status.grid(row=2, column=0, pady=(1, 8), padx=4)
            slot = {"box": box, "icon": icon, "name": name, "status": status, "sig": None, "tip": ""}
            self._hoverable(box, PANEL, LINE_STRONG, slot)
            self._tip(box, lambda sl=slot: sl.get("tip") or "")
            self.enemy_slots.append(slot)
        # allies + lane match-up
        self._hline(en).grid(row=3, column=0, sticky="ew")
        team = self._frame(en)
        team.grid(row=4, column=0, sticky="ew", pady=(8, 8))
        team.grid_columnconfigure(1, weight=1)
        al = self._frame(team)
        al.grid(row=0, column=0, sticky="w")
        self._label(al, "ALLIÉS", self.fonts.caps, DIM, anchor="w").grid(row=0, column=0, columnspan=4, sticky="w")
        self.ally_slots: list[dict[str, Any]] = []
        for i in range(4):
            cell = self._frame(al)
            cell.grid(row=1, column=i, padx=(0, 8), pady=(4, 0))
            ic = ctk.CTkLabel(cell, text="", image=self._ally_image(None, None, None), fg_color="transparent")
            ic.grid(row=0, column=0)
            nm = self._label(cell, "-", self.fonts.tiny, DIM)
            nm.grid(row=1, column=0, pady=(2, 0))
            slot = {"icon": ic, "name": nm, "sig": None, "tip": ""}
            self._tip(ic, lambda sl=slot: sl.get("tip") or "")
            self.ally_slots.append(slot)
        # lane match-up lives in the status strip (the "hero"): me VS my lane opponent
        mu = ctk.CTkFrame(hero.canvas, fg_color=PANEL, corner_radius=0)
        self.mu_me = ctk.CTkLabel(mu, text="", image=self._ally_image(None, None, None), fg_color="transparent")
        self.mu_me.grid(row=0, column=0, rowspan=2)
        self._label(mu, "VS", self.fonts.tiny_bold, DIM).grid(row=0, column=1, rowspan=2, padx=6)
        self.mu_opp = ctk.CTkLabel(mu, text="", image=self._ally_image(None, None, None, ring=ENEMY_RING),
                                   fg_color="transparent")
        self.mu_opp.grid(row=0, column=2, rowspan=2)
        self._caption(mu, "Face-à-face", DIM, anchor="w").grid(row=0, column=3, sticky="sw", padx=(10, 0))
        self.matchup_lbl = self._label(mu, "En attente", self.fonts.tiny, DIM, anchor="w")
        self.matchup_lbl.grid(row=1, column=3, sticky="nw", padx=(10, 0))
        self._matchup_sig: tuple = ()
        hero.attach_matchup(mu)

        jr = self._frame(left)
        jr.grid(row=2, column=0, sticky="nsew")
        jr.grid_columnconfigure(0, weight=1)
        jr.grid_rowconfigure(1, weight=1)
        jh = self._frame(jr)
        jh.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        jh.grid_columnconfigure(1, weight=1)
        self.journal_cap = self._caption(jh, "Journal", MUTED, anchor="w")
        self.journal_cap.grid(row=0, column=0, sticky="w")
        clr = ctk.CTkButton(jh, text="", width=26, height=24, corner_radius=RADIUS, fg_color="transparent",
                            hover_color=PANEL_HI, image=self._icon("close", 12, DIM),
                            command=self.cb(self.clear_journal))
        clr.grid(row=0, column=2)
        self._tip(clr, "Effacer le journal")
        self.journal_clear_btn = clr
        # empty journal -> "avant la partie": last game + précision, goal, point to work on (or a checklist)
        self.pregame = self._frame(jr)
        self.pregame.grid_columnconfigure(0, weight=1)
        self.journal = ctk.CTkTextbox(jr, fg_color=SUNKEN, text_color=TEXT, font=self.fonts.small,
                                      wrap="word", activate_scrollbars=True, border_width=0,
                                      scrollbar_button_color=SWITCH_OFF,
                                      scrollbar_button_hover_color=LINE_STRONG, height=190)
        self.journal.grid(row=1, column=0, sticky="nsew")
        for lvl, col in LEVEL_COLORS.items():
            self.journal.tag_config(f"lvl{lvl}", foreground=col)
        self.journal.tag_config("time", foreground=DIM)
        self.journal.tag_config("line", spacing1=3, spacing3=3)
        self.journal.tag_config("empty", foreground=DIM)
        self._render_journal()

        # --- right column: radar + tech -------------------------------------------------
        rc = self._frame(body, width=RADAR_PX + 8)
        rc.grid(row=3, column=1, sticky="n")
        rc.grid_columnconfigure(0, weight=1)
        rh = self._frame(rc)
        rh.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        rh.grid_columnconfigure(1, weight=1)
        self._caption(rh, "Radar", MUTED, anchor="w").grid(row=0, column=0, sticky="w")
        for i, (icon, tip, fn) in enumerate((
                ("refresh", "Rechercher la minimap maintenant", lambda: self.relocate()),
                ("report", "Ouvrir le dernier rapport", lambda: self.open_last_report()),
                ("folder", "Ouvrir le dossier des rapports", lambda: self.open_games_dir()),
                ("copy", "Copier le diagnostic (Ctrl+D)", lambda: self.copy_diagnostic()))):
            b = ctk.CTkButton(rh, text="", width=28, height=26, corner_radius=RADIUS, fg_color="transparent",
                              hover_color=PANEL_HI, image=self._icon(icon, 15, MUTED), command=self.cb(fn))
            b.grid(row=0, column=i + 2, padx=(2, 0))
            self._tip(b, tip)
        import tkinter as tk  # noqa: PLC0415

        from PIL import ImageTk  # noqa: PLC0415

        self._radar_size = self._scaled(RADAR_PX)
        self._radar_placeholder = flat_placeholder(self._radar_size)
        self._radar_photo = ImageTk.PhotoImage(self._radar_placeholder, master=self.root)

        def textured(img: Image.Image) -> None:      # minimap texture rendered off the Tk thread (cv2)
            self._radar_placeholder = img
            if not self._radar_live:
                self._radar_photo.paste(img)
        self._dispatcher.run(lambda: radar_placeholder(self._radar_size), textured, None,
                             name="TreeAI-ui-radar-placeholder")
        holder = tk.Frame(rc, bg=BG, width=self._radar_size, height=self._radar_size)
        holder.grid(row=1, column=0)
        holder.grid_propagate(False)
        self.radar_lbl = tk.Label(holder, image=self._radar_photo, bg=BG, bd=0, highlightthickness=0)
        self.radar_lbl.place(x=0, y=0, relwidth=1, relheight=1)
        self.radar_msg = tk.Label(holder, text="En attente d'une partie…", bg=PANEL_LO, fg=MUTED,
                                  font=(self.fonts.family, self._font_px(12)), padx=12, pady=6)
        self.radar_msg.place(relx=0.5, rely=0.5, anchor="center")
        self.radar_badge = ctk.CTkLabel(holder, text=" HORS LIGNE ", font=self.fonts.caps, text_color=MUTED,
                                        fg_color=PANEL_HI, corner_radius=RADIUS, height=18, bg_color=PANEL_LO)
        self.radar_badge.place(x=self._scaled(8), y=self._scaled(8))
        # --- launcher: status of each subsystem with a one-click fix ----------------------
        sysf = self._frame(rc)
        sysf.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        sysf.grid_columnconfigure(2, weight=1)
        self._caption(sysf, "Système", MUTED, anchor="w").grid(row=0, column=0, columnspan=4, sticky="w")
        self._hline(sysf, LINE_STRONG).grid(row=1, column=0, columnspan=4, sticky="ew", pady=(4, 2))
        self.sys_rows: dict[str, dict[str, Any]] = {}
        sys_tips = {"ia": "Modèle qui reconnaît les champions sur la minimap.",
                    "ai": "Conseils écrits par une IA en ligne (facultatif) : fournisseur, clé, conseils utilisés "
                          "dans la partie (5 + 1 en urgence)."}
        for i, (key, label) in enumerate((("game", "Jeu"), ("minimap", "Minimap"), ("lcu", "Client LoL"),
                                          ("ia", "Détection"), ("ai", "IA conseil"), ("voice", "Voix"))):
            r = 2 + i
            dot = ctk.CTkFrame(sysf, width=6, height=6, corner_radius=0, fg_color=DIM)
            dot.grid(row=r, column=0, padx=(0, 8))
            name = self._label(sysf, label, self.fonts.small, TEXT, anchor="w")
            name.grid(row=r, column=1, sticky="w", padx=(0, 8), pady=1)
            val = self._label(sysf, "-", self.fonts.tiny, MUTED, anchor="w")
            val.grid(row=r, column=2, sticky="w")
            if key in sys_tips:
                self._tip(name, sys_tips[key])
            self._tip(val, lambda v=val: v.cget("text"))
            btn = ctk.CTkButton(sysf, text="", width=10, height=18, corner_radius=RADIUS, fg_color="transparent",
                                hover_color=PANEL_HI, text_color=ACCENT, font=self.fonts.tiny_bold,
                                command=self.cb(lambda k=key: self._system_fix(k)))
            btn.grid(row=r, column=3, sticky="e")
            btn.grid_remove()
            self.sys_rows[key] = {"dot": dot, "val": val, "btn": btn, "sig": None, "action": ""}
        self.btn_test_overlay = self._button(rc, "Tester l'overlay", self.test_overlay, "secondary", icon="overlay",
                                             height=26)
        self.btn_test_overlay.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self._tip(self.btn_test_overlay, "Affiche l'overlay sur une partie d'exemple pendant 10 s "
                                         "(sûr, attention, danger), hors partie.")
        self._overlay_test: tuple[float, list] | None = None

        tech = self._frame(rc)
        tech.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        self.tech: dict[str, Any] = {}
        for i, (key, label, tip) in enumerate((
                ("fps", "FPS", "Images de minimap analysées par seconde"),
                ("cpu", "CPU", "Processeur utilisé par TreeAI Coach (en % de la machine)"),
                ("detector", "MODÈLE", "Détecteur de champions utilisé"),
                ("voice", "VOIX", "Moteur de synthèse vocale utilisé"))):
            tech.grid_columnconfigure(i, weight=1, uniform="tech")
            tile = self._frame(tech)
            tile.grid(row=0, column=i, sticky="ew")
            tile.grid_columnconfigure(0, weight=1)
            self._label(tile, label, self.fonts.caps, DIM, anchor="w").grid(row=0, column=0, sticky="w")
            val = self._label(tile, "-", self.fonts.tiny_bold, TEXT, anchor="w")
            val.grid(row=1, column=0, sticky="w")
            self.tech[key] = val
            self._tip(tile, tip)
        # live health (engine status.health, in game): capture, detection timings, overlay, champions
        self.health_lbl = self._label(rc, "", self.fonts.tiny, MUTED, anchor="w", justify="left",
                                      wraplength=RADAR_PX + 20)
        self.health_lbl.grid(row=5, column=0, sticky="w", pady=(8, 0))
        self._tip(self.health_lbl, "Santé de l'analyse : capture, temps de détection (médiane / pire 5 %), "
                                   "overlay, champions vus sur la minimap, processeur.")
        self._health_sig: Any = None
        self.btn_diag = self._button(rc, "Diagnostic complet", self.start_diagnostic, "ghost", icon="report",
                                     height=30)
        self.btn_diag.grid(row=6, column=0, sticky="w", pady=(6, 0))
        self._tip(self.btn_diag, "Enregistre 60 s d'analyse (minimap, détections, temps de calcul, réglages) "
                                 "dans un zip à joindre à un signalement. Joue normalement pendant ce temps.")
        self._cpu = ui_kit.CpuMeter()
        return page

    def _scaled(self, px: int) -> int:
        try:
            return int(round(px * float(self.ctk.ScalingTracker.get_widget_scaling(self.root))))
        except Exception:
            return px

    def _font_px(self, size: int) -> int:
        return -abs(self._scaled(size))

    def _enemy_image(self, icon: np.ndarray | None, alias: str | None, mode: str, role: str | None = None,
                     mia: float | None = None, jungler: bool = False) -> Any:
        """CTkImage of an enemy card portrait: team ring, role badge, MIA arc, jungler star (cached)."""
        bucket = None if mia is None else min(20, int(mia // 3))
        key = (alias, mode, icon is not None, role, bucket, jungler)
        img = self._enemy_cache.get(key)
        if img is None:
            ring = {"visible": ENEMY_RING, "approach": WARNING, "mia": DIM, "empty": BORDER}.get(mode, DIM)
            pil = circle_icon(icon, 104, ring, grey=(mode == "mia"), bg=PANEL)
            if mode != "empty":
                frac = None if bucket is None else min(1.0, bucket * 3 / 60.0)
                pil = ui_kit.decorate_portrait(pil, role, frac, arc_color=WARNING, badge_bg=PANEL, star=jungler)
            img = self.ctk.CTkImage(light_image=pil, dark_image=pil, size=(52, 52))
            if len(self._enemy_cache) > 160:
                self._enemy_cache.clear()
            self._enemy_cache[key] = img
        return img

    def _ally_image(self, icon: np.ndarray | None, alias: str | None, role: str | None,
                    ring: str = ALLY_RING) -> Any:
        """Small round portrait of an ally (or of me / my lane opponent) with its role badge."""
        key = ("ally", alias, icon is not None, role, ring)
        img = self._enemy_cache.get(key)
        if img is None:
            pil = circle_icon(icon, 72, ring if alias else BORDER, bg=PANEL)
            if alias:
                pil = ui_kit.decorate_portrait(pil, role, badge_bg=PANEL)
            img = self.ctk.CTkImage(light_image=pil, dark_image=pil, size=(36, 36))
            self._enemy_cache[key] = img
        return img

    def _draw_gauge(self) -> None:
        hero = getattr(self, "hero", None)
        if hero is not None:
            hero.draw_gauge(self._gauge_frac, self._gauge_color)
