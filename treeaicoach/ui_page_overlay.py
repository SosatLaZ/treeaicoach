"""Overlay page ("Overlay"): layers, positions, live preview, play-rating badges.

Mixin of :class:`treeaicoach.ui.CoachApp` (split out of ``ui.py`` without any behaviour
change): the methods use the app state (``self.cfg``, ``self.ctk``, widgets ...) created in
``CoachApp.__init__`` and run on the Tk thread only. Not meant to be used on its own.
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
    RADAR_POSITIONS,
    SUNKEN,
    TEXT,
    WARNING,
    _guarded,
    fmt_decimal_fr,
)

log = logging.getLogger("treeaicoach.ui")   # same logger as before the split


class OverlayPageMixin:
    """Overlay page ("Overlay"): layers, positions, live preview, play-rating badges."""

    # ------------------------------------------------------------------ overlay page
    def _build_overlay_page(self) -> Any:
        page, right, body = self._page("Overlay", "Ce qui est dessiné sur ta minimap")
        self.btn_move = self._button(right, "Déplacer les fenêtres", self.toggle_move_mode, "secondary", icon="move",
                                     height=26)
        self.btn_move.grid(row=0, column=0)
        self._tip(self.btn_move, "Fais glisser le radar et le HUD à la souris (Windows).")
        prev = lambda _v=None: self._schedule_overlay_preview()  # noqa: E731
        self._build_overlay_preview(body, 0)
        s = self._section(body, 1, "Affichage sur la minimap",
                          "Fenêtres transparentes traversées par la souris (jeu en Sans bordure ou Fenêtré). "
                          "Rien n'est injecté dans le jeu.", icon="map")
        self._switch_row(s, "overlay_enabled", "Activer l'overlay", "Interrupteur général (F11 en jeu).",
                         on_change=lambda v: (prev(), self._sync_quick()))
        if hasattr(self.cfg, "overlay_mode"):
            self._choice_row(s, "overlay_mode", "Où dessiner", "Sur la minimap : marques posées sur la vraie "
                             "minimap. Radar : copie agrandie à côté. Aucun : seulement le HUD et le flash.",
                             OVERLAY_MODES, segmented=True, on_change=lambda _v: (prev(), self._refresh_radar_rows()))
        if hasattr(self.cfg, "overlay_hide_from_capture"):
            self._switch_row(s, "overlay_hide_from_capture", "Masquer des captures et du stream",
                             "Les marques sur la minimap n'apparaissent pas sur tes captures d'écran ni sur OBS "
                             "(Windows 10 2004 ou plus récent).")
        if self._overlay_supports("overlay_opacity"):
            self._slider_row(s, "overlay_opacity", "Opacité", "Transparence des marques et du HUD.", 0.3, 1.0, 0.05,
                             lambda v: f"{int(round(float(v) * 100))} %", float, on_change=prev)
        if self._overlay_supports("overlay_scale"):
            self._slider_row(s, "overlay_scale", "Taille des marques", None, 0.6, 1.6, 0.1,
                             lambda v: f"× {fmt_decimal_fr(v, 1)}", float, on_change=prev)

        s = self._section(body, 2, "HUD et flash", "Panneau compact (jauge de menace, jungler, objectifs) et "
                                                    "alerte visuelle en cas de gank.", icon="eye")
        self._switch_row(s, "hud_enabled", "Panneau HUD", "Jauge de menace, jungler, 5 ennemis, objectifs.",
                         on_change=prev)
        self._position_menus: dict[str, Any] = {}
        self._position_menus["hud"] = self._choice_row(
            s, "hud_position", "Position du HUD", None, HUD_POSITIONS, width=230, on_change=prev)
        self._switch_row(s, "danger_flash", "Flash de danger", "Cadre rouge sur les bords de l'écran en cas de gank.",
                         on_change=prev)
        if hasattr(self.cfg, "ward_guide"):
            hk_ward = getattr(self.cfg, "hotkey_ward", "") or "sans raccourci"
            self._switch_row(s, "ward_guide", "Guide de balise",
                             f"Où poser ta balise : repère sur la minimap (après un retour, avant un objectif, "
                             f"ou {hk_ward}). Disparaît dès que la balise est posée.")
        if hasattr(self.cfg, "ward_world"):
            self._switch_row(s, "ward_world", "Repère dans le jeu",
                             "« Ward ici » au sol sur le buisson conseillé, ou flèche au bord de l'écran.")

        s = self._section(body, 3, "Position possible dans le brouillard",
                          "Zone qui grandit là où un ennemi caché peut se trouver (dernière position vue "
                          "+ vitesse de déplacement). Aucune prédiction.", icon="clock")
        self._choice_row(s, "fog_mode", "Cercle de position", "Pour le jungler seulement, tous les ennemis, "
                         "ou désactivé.", FOG_MODES, segmented=True, on_change=prev)
        self._slider_row(s, "fog_max_s", "Durée maximale", "Au-delà, la zone est trop grande : elle s'efface.",
                         10, 180, 5, lambda v: f"{int(v)} s", float)

        s = self._section(body, 4, "Radar à côté de la minimap", "Utilisé seulement en mode « Radar ».",
                          icon="target")
        self._radar_section = s
        self._position_menus["radar"] = self._choice_row(
            s, "radar_position", "Position du radar", None, RADAR_POSITIONS, width=230, on_change=prev)
        self._slider_row(s, "radar_scale", "Taille du radar", "1,0 = même taille que la minimap.", 0.5, 2.0, 0.1,
                         lambda v: f"× {fmt_decimal_fr(v, 1)}", float, on_change=prev)
        self._refresh_position_menus()
        self._refresh_radar_rows()
        try:
            self._build_plays_section(body, 10)
        except Exception:
            log.exception("Cannot build the rated plays section")
        return page

    def _build_plays_section(self, body: Any, row: int) -> None:
        """Overlay page: rated plays (plays.py / fx_overlay.py, chess.com style badges)."""
        if not hasattr(self.cfg, "plays_enabled"):
            return
        s = self._section(body, row, "Coups notés", "Après un moment clé, un badge note ton coup : coup de "
                          "maître, excellent, erreur, gaffe… Jamais pendant un combat.")
        prev = lambda _v=None: self._schedule_overlay_preview()  # noqa: E731
        self._switch_row(s, "plays_enabled", "Afficher les coups notés", "Badge à l'écran et précision dans le "
                         "rapport d'après-partie.", on_change=prev)
        if hasattr(self.cfg, "plays_position"):
            self._choice_row(s, "plays_position", "Position du badge", "En haut au centre de l'écran, ou près de "
                             "la minimap.", (("top_center", "Haut, au centre"), ("minimap", "Près de la minimap")),
                             segmented=True, on_change=prev)
        if hasattr(self.cfg, "plays_sound"):
            self._switch_row(s, "plays_sound", "Son pour les bons coups", "Petit son court.")
        if hasattr(self.cfg, "plays_sound_negative"):
            self._switch_row(s, "plays_sound_negative", "Son aussi pour les erreurs", "Désactivé par défaut.")

    def _overlay_supports(self, field: str) -> bool:
        """Whether the overlay module reads an optional look setting (``SUPPORTED_SETTINGS``)."""
        if not hasattr(self.cfg, field):
            return False
        try:
            import importlib  # noqa: PLC0415

            mod = importlib.import_module("treeaicoach.overlay")
            return field in tuple(getattr(mod, "SUPPORTED_SETTINGS", ()) or ())
        except Exception:
            return False

    def _refresh_radar_rows(self) -> None:
        """Dim the radar section when the map mode is not "radar"."""
        s = getattr(self, "_radar_section", None)
        if s is None:
            return
        on = getattr(self.cfg, "overlay_mode", "radar") == "radar"
        try:
            stack = list(s.winfo_children())
            while stack:
                w = stack.pop()
                stack.extend(w.winfo_children())
                if isinstance(w, (self.ctk.CTkLabel, self._PLabel)):
                    try:
                        if str(w.cget("text_color")).upper() in (TEXT, DIM):
                            w.configure(text_color=TEXT if on else DIM)
                    except Exception:
                        pass
        except Exception:
            log.debug("radar rows refresh failed", exc_info=True)

    def _position_choices(self, which: str) -> list[tuple[str, str]]:
        base = RADAR_POSITIONS if which == "radar" else HUD_POSITIONS
        xy = self.cfg.radar_xy if which == "radar" else self.cfg.hud_xy
        return [(v, lbl) for v, lbl in base if v != "custom" or xy is not None]

    def _schedule_overlay_preview(self) -> None:
        if self._overlay_preview_job is not None:
            try:
                self.root.after_cancel(self._overlay_preview_job)
            except Exception:
                pass
        self._overlay_preview_job = self.root.after(300, self._render_overlay_preview)

    def _build_overlay_preview(self, body: Any, row: int) -> None:
        """Top of the Overlay page: the real overlay layers for the current settings (ui_preview.py)."""
        ctk = self.ctk
        card = self._frame(body)
        card.grid(row=row, column=0, sticky="ew", pady=(0, 20))
        card.grid_columnconfigure(0, weight=1)
        head = self._frame(card)
        head.grid(row=0, column=0, sticky="ew")
        head.grid_columnconfigure(0, weight=1)
        self._label(head, "Aperçu", self.fonts.h2, TEXT, anchor="w").grid(row=0, column=0, sticky="w")
        self.overlay_preview_tag = self._label(head, "", self.fonts.caps, DIM, anchor="e")
        self.overlay_preview_tag.grid(row=0, column=1, sticky="e")
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
        tile(right, "hud", "HUD", 0, 0)
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
        if self._current_page != "overlay" or self._closing or getattr(self, "_overlay_preview_busy", False):
            return
        if self._iconic():          # minimised: no preview rendering, look again in 2 s
            self._overlay_preview_job = self.root.after(2000, self._render_overlay_preview)
            return
        cfg = self.cfg
        scale = max(0.5, self._scaled(100) / 100)
        live_state = self._overlay_state() if self._in_game() else None

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
                ci = self.ctk.CTkImage(light_image=im, dark_image=im,
                                       size=(int(im.width / scale), int(im.height / scale)))
                self._images[f"overlay-preview-{key}"] = ci
                lbl.configure(image=ci, text="", fg_color="transparent", width=0, height=0)
                frame.grid()
            tag = getattr(self, "overlay_preview_tag", None)
            if tag is not None:
                off = not bool(getattr(cfg, "overlay_enabled", True))
                tag.configure(text="OVERLAY DÉSACTIVÉ" if off else ("EN DIRECT · TA PARTIE" if live else
                                                                    "EXEMPLE · GANK EN COURS · 1920 × 1080"),
                              text_color=WARNING if off else (ACCENT if live else DIM))
            self._overlay_preview_live = live
            if live and self._current_page == "overlay" and self._overlay_preview_job is None:
                self._overlay_preview_job = self.root.after(1500, self._render_overlay_preview)

        def failed(exc: BaseException) -> None:
            self._overlay_preview_busy = False
            log.debug("overlay preview failed: %s", exc)

        self._overlay_preview_busy = True
        self._dispatcher.run(job, done, self.cb(failed), name="TreeAI-ui-overlay-preview")
