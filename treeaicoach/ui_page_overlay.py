"""Overlay: what is drawn over the game, with a live preview (rendered on a worker thread)."""

from __future__ import annotations

import logging
from typing import Any

from treeaicoach.ui_common import FOG_MODES, HUD_POSITIONS, OVERLAY_MODES, PLAYS_POSITIONS, RADAR_POSITIONS, fmt_decimal_fr

log = logging.getLogger(__name__)

PREVIEW_FIELDS = frozenset({"overlay_enabled", "overlay_mode", "hud_enabled", "hud_position", "hud_detailed",
                            "radar_position", "radar_scale", "fog_mode", "overlay_timers", "jungle_paths",
                            "plays_enabled", "plays_position", "danger_flash", "text_tips", "item_advice",
                            "win_prob_hud", "skill_level"})


def position_choices(cfg: Any, which: str) -> list[tuple[str, str]]:
    base = RADAR_POSITIONS if which == "radar" else HUD_POSITIONS
    xy = cfg.radar_xy if which == "radar" else cfg.hud_xy
    return [(v, lbl) for v, lbl in base if v != "custom" or xy is not None]


class OverlayPage:
    def __init__(self, app: Any) -> None:
        self.app = app
        W, QtWidgets, Qt = app.W, app.QtWidgets, app.QtCore.Qt
        self.page = W.Page("Overlay", "Fenêtres transparentes posées sur le jeu (mode Sans bordure ou Fenêtré). "
                                      "Elles ne touchent jamais au jeu et se cachent hors de la partie.")
        self._preview_busy = False
        self._preview_again = False

        # preview
        self.move_btn = W.button("Déplacer", app.toggle_move_mode,
                                 tip="Fais glisser le radar et le panneau à la souris.")
        pv = self.page.section("Aperçu", "Exemple sur une partie fictive, avec tes réglages.", self.move_btn)
        holder = QtWidgets.QWidget()
        hl = QtWidgets.QVBoxLayout(holder)
        hl.setContentsMargins(16, 14, 16, 14)
        self.preview = QtWidgets.QLabel("Aperçu en préparation…")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(240)
        self.preview.setProperty("role", "secondary")
        hl.addWidget(self.preview)
        pv.add(holder)

        main = self.page.section("Général")
        app.switch_row(main, "overlay_enabled", "Afficher l'overlay",
                       f"Touche en jeu : {self._key('hotkey_overlay')}.")
        app.choice_row(main, "overlay_mode", "Où dessiner", "Sur la minimap : marques posées sur la vraie "
                       "minimap. Radar : une copie agrandie à côté.", OVERLAY_MODES)
        app.switch_row(main, "overlay_hide_from_capture", "Cacher des captures et du stream",
                       "Discord, OBS et les captures d'écran ne voient pas l'overlay.")

        mm = self.page.section("Sur la minimap")
        app.switch_row(mm, "overlay_timers", "Minuteurs", "Buff Baron / Ancien restant, ennemis morts, objectifs.")
        app.choice_row(mm, "fog_mode", "Zone des ennemis cachés", "Cercle qui grandit là où un ennemi caché peut "
                       "être.", FOG_MODES, segmented=True)
        self.fog_row = app.slider_row(mm, "fog_max_s", "Durée de la zone", "Au-delà, la zone est trop grande : "
                                      "elle s'efface.", 10, 180, 5, lambda v: f"{v:.0f} s", to_value=float)
        app.switch_row(mm, "jungle_paths", "Trajets probables du jungler", "Un ou deux chemins en pointillés.")
        app.switch_row(mm, "ward_guide", "Guide de balise", f"Où poser une balise ({self._key('hotkey_ward')} en jeu).")
        app.switch_row(mm, "ward_world", "Repère dans le jeu", "Marque au sol ou flèche au bord de l'écran.")
        app.switch_row(mm, "ward_sound", "Son du guide de balise", "Un son court quand un guide apparaît.")

        hud = self.page.section("Panneau en jeu", "Une consigne à la fois, en rouge en cas de danger.")
        app.switch_row(hud, "hud_enabled", "Afficher le panneau", "Jauge de menace, consigne du moment, "
                       "prochain objectif.")
        self.hud_pos = app.choice_row(hud, "hud_position", "Position du panneau", None,
                                      position_choices(app.cfg, "hud"))
        app.switch_row(hud, "hud_detailed", "Toujours en mode détaillé",
                       f"Sinon, maintiens {self._key('hotkey_details')} en jeu pour le voir.")
        app.switch_row(hud, "text_tips", "Conseils écrits", "Un conseil court dans le panneau, jamais lu.")
        app.switch_row(hud, "item_advice", "Prochain objet", "L'objet à acheter au prochain retour.")
        app.switch_row(hud, "win_prob_hud", "Probabilité de victoire", "Dans le panneau et sur l'Accueil.")

        scr = self.page.section("À l'écran")
        app.switch_row(scr, "danger_flash", "Flash de danger", "Cadre rouge sur les bords de l'écran en cas de gank.")
        app.switch_row(scr, "tip_toasts", "Conseils en bandeau", "Les conseils écrits aussi en petit bandeau.")
        app.switch_row(scr, "item_advice_toasts", "Conseil d'achat en bandeau", "Quand tu as l'or pour un objet.")
        app.switch_row(scr, "plays_enabled", "Coups notés", "Badge « coup de maître », « erreur »… après une action.")
        app.choice_row(scr, "plays_position", "Position du badge", None, PLAYS_POSITIONS, segmented=True)
        app.switch_row(scr, "plays_sound", "Son des bons coups", "Un son court.")
        app.switch_row(scr, "plays_sound_negative", "Son aussi pour les erreurs", "Désactivé par défaut.")

        self.radar = self.page.section("Radar", "Copie agrandie de la minimap, à côté d'elle.")
        self.radar_pos = app.choice_row(self.radar, "radar_position", "Position du radar", None,
                                        position_choices(app.cfg, "radar"))
        app.slider_row(self.radar, "radar_scale", "Taille du radar", "1,0 = même taille que la minimap.",
                       0.5, 2.0, 0.05, lambda v: fmt_decimal_fr(v, 2), to_value=float)
        self._sync_visibility()

    def _key(self, field: str) -> str:
        return str(getattr(self.app.cfg, field, "") or "").replace("+", " + ") or "touche désactivée"

    def _sync_visibility(self) -> None:
        cfg = self.app.cfg
        self.radar.setVisible(cfg.overlay_mode == "radar")
        self.fog_row.setVisible(cfg.fog_mode != "off")
        self.fog_row.parentWidget().sync_separators()

    def on_config(self, diff: set[str]) -> None:
        cfg = self.app.cfg
        if diff & {"overlay_mode", "fog_mode"}:
            self._sync_visibility()
        if diff & {"hud_xy", "hud_position", "radar_xy", "radar_position"}:
            self.hud_pos.ctl.set_choices(position_choices(cfg, "hud"), cfg.hud_position)
            self.radar_pos.ctl.set_choices(position_choices(cfg, "radar"), cfg.radar_position)
        if diff & PREVIEW_FIELDS and self.app._current_page == "overlay":
            self.render_preview()

    def on_move_mode(self, on: bool) -> None:
        self.move_btn.setText("Terminer" if on else "Déplacer")
        self.app.W.set_prop(self.move_btn, "kind", "primary" if on else None)

    def on_show(self) -> None:
        if self.preview.pixmap() is None or self.preview.pixmap().isNull():
            self.render_preview()

    def render_preview(self) -> None:
        """Compose the sample screen on a worker thread; only the latest request is drawn."""
        if self._preview_busy:
            self._preview_again = True
            return
        self._preview_busy = True
        cfg = self.app.cfg
        width = max(360, min(680, self.page.column.width() - 32))

        def job() -> Any:
            from treeaicoach import ui_preview  # noqa: PLC0415

            comp = ui_preview.compose(cfg)
            return ui_preview.preview_images(comp, screen_w=width).get("screen")

        def done(img: Any) -> None:
            self._preview_busy = False
            if img is not None:
                QtGui = self.app.QtGui
                rgb = img.convert("RGB")
                data = rgb.tobytes("raw", "RGB")
                qimg = QtGui.QImage(data, rgb.width, rgb.height, 3 * rgb.width, QtGui.QImage.Format_RGB888).copy()
                pm = QtGui.QPixmap.fromImage(qimg)
                self.preview.setPixmap(_rounded(QtGui, self.app.QtCore, pm, 8))
            if self._preview_again:
                self._preview_again = False
                self.render_preview()

        def failed(_e: BaseException) -> None:
            self._preview_busy = False
            self.preview.setText("Aperçu indisponible.")

        self.app.run_job(job, done, failed, name="TreeAI-ui-preview")


def _rounded(QtGui: Any, QtCore: Any, pm: Any, r: int) -> Any:
    out = QtGui.QPixmap(pm.size())
    out.fill(QtCore.Qt.transparent)
    qp = QtGui.QPainter(out)
    qp.setRenderHint(QtGui.QPainter.Antialiasing)
    path = QtGui.QPainterPath()
    path.addRoundedRect(QtCore.QRectF(0, 0, pm.width(), pm.height()), r, r)
    qp.setClipPath(path)
    qp.drawPixmap(0, 0, pm)
    qp.end()
    return out


def build(app: Any) -> OverlayPage:
    return OverlayPage(app)
