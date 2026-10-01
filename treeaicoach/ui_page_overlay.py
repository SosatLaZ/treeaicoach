"""Réglages > Affichage ("ce que tu vois en jeu"): live preview, overlay, minimap marks, panel, screen.

Mixin of :class:`treeaicoach.ui.CoachApp`: the methods use the app state (``self.cfg``, ``self.ctk``,
widgets ...) created in ``CoachApp.__init__`` and run on the Tk thread only. Not meant to be used on
its own. (This was the "Overlay" page before the settings were grouped in one page.)
"""

from __future__ import annotations

import logging
from typing import Any

from PIL import Image

from treeaicoach.ui_common import (
    ACCENT,
    DIM,
    FOG_MODES,
    HUD_POSITIONS,
    LINE_STRONG,
    OVERLAY_MODES,
    PLAYS_POSITIONS,
    RADAR_POSITIONS,
    SUNKEN,
    TEXT,
    WARNING,
    _guarded,
    fmt_decimal_fr,
)

log = logging.getLogger("treeaicoach.ui")   # same logger as before the split

#: Largest size (logical px) of each preview tile: the screen and the badge on the left column,
#: the cut-outs (panel, minimap marks, radar) on the right one.
PREVIEW_TILE_MAX: dict[str, tuple[int, int]] = {"screen": (400, 260), "badge": (400, 120), "hud": (300, 200),
                                                "minimap": (260, 260), "radar": (260, 260)}


class OverlayPageMixin:
    """Réglages > Affichage: what the player sees in game (overlay windows, minimap marks, panel)."""

    # ------------------------------------------------------------------ Affichage tab
    def _build_display_tab(self, body: Any, row: int) -> int:
        prev = lambda _v=None: self._schedule_overlay_preview()  # noqa: E731
        self._build_overlay_preview(body, row)
        hk = lambda f, d="": str(getattr(self.cfg, f, "") or d)  # noqa: E731

        yield                                   # one section per idle slot (prebuild)
        s = self._section(body, row + 1, "Overlay", "Fenêtres transparentes posées sur le jeu (Sans bordure ou "
                                                    "Fenêtré) : rien n'est injecté dans le jeu.")
        self._switch_row(s, "overlay_enabled", "Afficher l'overlay",
                         f"Interrupteur général ({hk('hotkey_overlay', 'F11')} en jeu).",
                         on_change=lambda v: (prev(), self._sync_quick()))
        self._choice_row(s, "overlay_mode", "Où dessiner", "Sur la minimap : marques posées sur la vraie minimap. "
                         "Radar : copie agrandie à côté. Aucun : seulement le panneau et le flash.",
                         OVERLAY_MODES, segmented=True, on_change=lambda _v: (prev(), self._refresh_radar_rows()))
        self._switch_row(s, "overlay_hide_from_capture", "Cacher des captures et du stream",
                         "Tes captures d'écran et OBS ne voient pas les marques (Windows 10 2004 ou plus récent).")

        yield                                   # one section per idle slot (prebuild)
        s = self._section(body, row + 2, "Sur la minimap")
        self._switch_row(s, "overlay_timers", "Minuteurs", "Buff Baron / Ancien restant, ennemis morts, "
                         "réapparition des objectifs qui te concernent.", on_change=prev)
        self._choice_row(s, "fog_mode", "Zone des ennemis cachés", "Cercle qui grandit là où un ennemi caché peut "
                         "être (dernière position vue + vitesse). Aucune prédiction.", FOG_MODES, segmented=True,
                         on_change=prev)
        self._slider_row(s, "fog_max_s", "Durée de la zone", "Au-delà, la zone est trop grande : elle s'efface.",
                         10, 180, 5, lambda v: f"{int(v)} s", float)
        self._switch_row(s, "jungle_paths", "Trajets probables du jungler (essai)",
                         "1 ou 2 chemins en pointillés d'après ses derniers camps vus. Désactivé par défaut.",
                         on_change=prev)
        self._switch_row(s, "ward_guide", "Guide de balise",
                         f"Où poser ta balise : repère sur la minimap après un retour, avant un objectif, ou avec "
                         f"{hk('hotkey_ward', 'sans raccourci')}. Disparaît dès que la balise est posée.")
        self._switch_row(s, "ward_world", "Repère dans le jeu",
                         "« Ward ici » au sol sur le buisson conseillé, ou une flèche au bord de l'écran.")
        self._switch_row(s, "ward_sound", "Son du guide de balise", "Un son court quand un guide apparaît.")

        yield                                   # one section per idle slot (prebuild)
        s = self._section(body, row + 3, "Panneau en jeu", "Une consigne à la fois, en rouge en cas de danger.")
        self._switch_row(s, "hud_enabled", "Afficher le panneau", "Jauge de menace, consigne du moment, prochain "
                         "objectif.", on_change=prev)
        self._position_menus: dict[str, Any] = {}
        self._position_menus["hud"] = self._choice_row(
            s, "hud_position", "Position du panneau", None, HUD_POSITIONS, width=230, on_change=prev)
        self._switch_row(s, "hud_detailed", "Toujours en mode détaillé",
                         f"Sinon, maintiens {hk('hotkey_details', 'la touche du mode détaillé')} en jeu pour voir "
                         "le détail : jungler, 5 ennemis, alliés.", on_change=prev)
        self._switch_row(s, "text_tips", "Conseils écrits",
                         "La consigne courte du moment dans le panneau (jamais lue à voix haute).")
        self._switch_row(s, "item_advice", "Prochain objet",
                         "Objet adapté à la partie (soins adverses, dégâts magiques…), d'après l'API officielle.")
        self._switch_row(s, "win_prob_hud", "Probabilité de victoire", "Dans le panneau et sur « En jeu » (or, "
                         "kills, tours, dragons, Baron et Ancien).")

        yield                                   # one section per idle slot (prebuild)
        s = self._section(body, row + 4, "À l'écran")
        self._switch_row(s, "danger_flash", "Flash de danger", "Cadre rouge sur les bords de l'écran en cas de gank.",
                         on_change=prev)
        self._switch_row(s, "tip_toasts", "Conseils en bandeau",
                         "La consigne du moment aussi en petit bandeau en haut de l'écran.")
        self._switch_row(s, "item_advice_toasts", "Conseil d'achat en bandeau",
                         "Au retour en base, à la mort et aux niveaux 6, 11 et 16.")
        self._switch_row(s, "plays_enabled", "Coups notés", "Badge « coup de maître », « erreur »… après un "
                         "moment clé, jamais pendant un combat ; précision dans le rapport.", on_change=prev)
        self._choice_row(s, "plays_position", "Position du badge", None, PLAYS_POSITIONS, segmented=True,
                         on_change=prev)
        self._switch_row(s, "plays_sound", "Son des bons coups", "Un son court.")
        self._switch_row(s, "plays_sound_negative", "Son aussi pour les erreurs", "Désactivé par défaut.")

        yield                                   # one section per idle slot (prebuild)
        s = self._section(body, row + 5, "Radar", "Copie agrandie de la minimap, à côté d'elle (mode « Radar »).")
        self._radar_section = s
        self._position_menus["radar"] = self._choice_row(
            s, "radar_position", "Position du radar", None, RADAR_POSITIONS, width=230, on_change=prev)
        self._slider_row(s, "radar_scale", "Taille du radar", "1,0 = même taille que la minimap.", 0.5, 2.0, 0.1,
                         lambda v: f"× {fmt_decimal_fr(v, 1)}", float, on_change=prev)
        self._refresh_position_menus()
        self._refresh_radar_rows()
        return row + 6

    def _refresh_radar_rows(self) -> None:
        """The "Radar" section only exists in the radar mode (hidden otherwise, not greyed out)."""
        s = getattr(self, "_radar_section", None)
        if s is not None:
            self._set_section_visible(s, getattr(self.cfg, "overlay_mode", "minimap") == "radar")

    def _position_choices(self, which: str) -> list[tuple[str, str]]:
        base = RADAR_POSITIONS if which == "radar" else HUD_POSITIONS
        xy = self.cfg.radar_xy if which == "radar" else self.cfg.hud_xy
        return [(v, lbl) for v, lbl in base if v != "custom" or xy is not None]

    # ------------------------------------------------------------------ preview (ui_preview.py)
    def _schedule_overlay_preview(self) -> None:
        if self._overlay_preview_job is not None:
            try:
                self.root.after_cancel(self._overlay_preview_job)
            except Exception:
                pass
        self._overlay_preview_job = self.root.after(300, self._render_overlay_preview)

    def _build_overlay_preview(self, body: Any, row: int) -> None:
        """Top of the Affichage tab: the real overlay layers for the current settings (ui_preview.py),
        with the two overlay actions (test on screen, move the windows)."""
        ctk = self.ctk
        card = self._frame(body)
        card.grid(row=row, column=0, sticky="ew", pady=(0, 20))
        card.grid_columnconfigure(0, weight=1)
        card.title = "Aperçu"  # type: ignore[attr-defined]
        try:
            body._sections.append(card)
        except AttributeError:
            body._sections = [card]
        head = self._frame(card)
        head.grid(row=0, column=0, sticky="ew")
        head.grid_columnconfigure(1, weight=1)
        self._label(head, "Aperçu", self.fonts.h2, TEXT, anchor="w").grid(row=0, column=0, sticky="w")
        self.overlay_preview_tag = self._label(head, "", self.fonts.caps, DIM, anchor="w")
        self.overlay_preview_tag.grid(row=0, column=1, sticky="w", padx=(12, 0))
        b = self._button(head, "Tester", self.test_overlay, "ghost", icon="overlay", height=30)
        b.grid(row=0, column=2, padx=(8, 0))
        self._tip(b, "Affiche l'overlay sur ton écran pendant 10 s (sûr, attention, danger), hors partie.")
        self.btn_move = self._button(head, "Déplacer", self.toggle_move_mode, "ghost", icon="move", height=30)
        self.btn_move.grid(row=0, column=3, padx=(8, 0))
        self._tip(self.btn_move, "Fais glisser le radar et le panneau à la souris (Windows), puis « Terminer ».")
        self._hline(card, LINE_STRONG).grid(row=1, column=0, sticky="ew", pady=(8, 12))
        grid = self._frame(card)
        grid.grid(row=2, column=0, sticky="w")
        self._overlay_tiles: dict[str, tuple[Any, Any]] = {}

        def tile(parent: Any, key: str, caption: str, r: int, c: int, **gkw: Any) -> None:
            f = self._frame(parent)
            f.grid(row=r, column=c, sticky="nw", **gkw)
            img = ctk.CTkLabel(f, text="", fg_color="transparent")
            img.grid(row=0, column=0, sticky="nw")
            cap = self._label(f, caption, self.fonts.tiny, DIM, anchor="w")
            cap.grid(row=1, column=0, sticky="w", pady=(3, 0))
            self._overlay_tiles[key] = (f, img)
            if key != "screen":
                f.grid_remove()          # shown once rendered

        left = self._frame(grid)
        left.grid(row=0, column=0, sticky="nw", padx=(0, 16))
        tile(left, "screen", "Écran entier : où chaque élément s'affiche", 0, 0)
        tile(left, "badge", "Coup noté (exemple)", 1, 0, pady=(10, 0))
        right = self._frame(grid)
        right.grid(row=0, column=1, sticky="nw")
        tile(right, "hud", "Panneau", 0, 0)
        tile(right, "minimap", "Marques sur la minimap", 1, 0, pady=(10, 0))
        tile(right, "radar", "Radar", 2, 0, pady=(10, 0))
        self.overlay_preview = self._overlay_tiles["screen"][1]
        self.overlay_preview.configure(text="Génération de l'aperçu…", text_color=DIM, font=self.fonts.small,
                                       width=400, height=225, fg_color=SUNKEN, corner_radius=0)
        self._overlay_preview_busy = False
        self._overlay_preview_live = False

    @_guarded
    def _render_overlay_preview(self) -> None:
        self._overlay_preview_job = None
        if not self._display_tab_live() or self._closing or getattr(self, "_overlay_preview_busy", False):
            if self._current_page == "settings" and self._settings_tab == "Affichage" and self._iconic():
                self._overlay_preview_job = self.root.after(2000, self._render_overlay_preview)
            return
        cfg = self.cfg
        scale = max(0.5, self._scaled(100) / 100)
        # the demo is not a real screen (its geometry would mislead): the 1080p example then
        live_state = self._overlay_state() if self._in_game() and not self.demo else None

        def job() -> tuple[dict[str, Image.Image], bool]:
            from treeaicoach import ui_preview  # noqa: PLC0415

            comp = ui_preview.compose(cfg, live_state)
            return ui_preview.preview_images(comp, screen_w=int(400 * scale), zoom=0.75 * scale), comp.live

        def done(res: tuple[dict[str, Image.Image], bool]) -> None:
            self._overlay_preview_busy = False
            imgs, live = res
            tiles = getattr(self, "_overlay_tiles", {})
            for key, (frame, lbl) in tiles.items():
                im = imgs.get(key)
                if im is None:
                    frame.grid_remove()
                    continue
                # never wider than its column (a 4K screen gives big cut-outs), shown at the UI scale
                max_w, max_h = PREVIEW_TILE_MAX.get(key, (400, 400))
                k = min(1.0, max_w * scale / max(1, im.width), max_h * scale / max(1, im.height))
                if k < 1.0:
                    im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.LANCZOS)
                ci = self.ctk.CTkImage(light_image=im, dark_image=im,
                                       size=(int(im.width / scale), int(im.height / scale)))
                self._images[f"overlay-preview-{key}"] = ci
                lbl.configure(image=ci, text="", fg_color="transparent", width=0, height=0)
                frame.grid()
            tag = getattr(self, "overlay_preview_tag", None)
            if tag is not None:
                off = not bool(getattr(cfg, "overlay_enabled", True))
                tag.configure(text="OVERLAY DÉSACTIVÉ" if off else ("EN DIRECT · TA PARTIE" if live else
                                                                    "EXEMPLE · GANK EN COURS"),
                              text_color=WARNING if off else (ACCENT if live else DIM))
            self._overlay_preview_live = live
            if live and self._display_tab_live() and self._overlay_preview_job is None:
                self._overlay_preview_job = self.root.after(1500, self._render_overlay_preview)

        def failed(exc: BaseException) -> None:
            self._overlay_preview_busy = False
            log.debug("overlay preview failed: %s", exc)

        self._overlay_preview_busy = True
        self._dispatcher.run(job, done, self.cb(failed), name="TreeAI-ui-overlay-preview")
