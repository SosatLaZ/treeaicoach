"""Overlay: what is drawn over the game, with a live preview (rendered on a worker thread)."""

from __future__ import annotations

import logging
from typing import Any

from treeaicoach.ui_common import FOG_MODES, HUD_POSITIONS, OVERLAY_MODES, PLAYS_POSITIONS, RADAR_POSITIONS

log = logging.getLogger(__name__)

PREVIEW_FIELDS = frozenset({"overlay_enabled", "overlay_mode", "hud_enabled", "hud_position", "hud_detailed",
                            "radar_position", "radar_scale", "fog_mode", "overlay_timers", "jungle_paths",
                            "plays_enabled", "plays_position", "danger_flash", "text_tips", "item_advice",
                            "win_prob_hud", "skill_level", "essentials_strip"})


def position_choices(cfg: Any, which: str) -> list[tuple[str, str]]:
    base = RADAR_POSITIONS if which == "radar" else HUD_POSITIONS
    xy = cfg.radar_xy if which == "radar" else cfg.hud_xy
    return [(v, lbl) for v, lbl in base if v != "custom" or xy is not None]


class OverlayPage:
    def __init__(self, app: Any) -> None:
        self.app = app
        W, QtWidgets, Qt = app.W, app.QtWidgets, app.QtCore.Qt
        self.page = W.Page("Overlay", "Ce qui s'affiche par-dessus le jeu (mode Sans bordure ou Fenêtré). "
                                      "Rien ne touche au jeu.")
        self._preview_busy = False
        self._preview_again = False

        # preview
        self.move_btn = W.button("Déplacer", app.toggle_move_mode,
                                 tip="Fais glisser le panneau à la souris.")
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
                       f"Tout ce qui suit. Touche en jeu : {self._key('hotkey_overlay')}.")
        app.switch_row(main, "overlay_hide_from_capture", "Invisible en stream et en capture",
                       "Discord, OBS et les captures d'écran ne voient pas l'overlay.")

        mm = self.page.section("Minimap")
        app.choice_row(mm, "overlay_mode", "Marques sur la minimap", "Ennemis suivis, zones et minuteurs.",
                       OVERLAY_MODES, segmented=True)
        app.switch_row(mm, "overlay_timers", "Minuteurs", "Objectifs, buffs Baron et Ancien, ennemis morts.")
        app.choice_row(mm, "fog_mode", "Zone des ennemis cachés", "Cercle qui grandit là où un ennemi caché peut "
                       "être.", FOG_MODES, segmented=True)
        self.fog_row = app.slider_row(mm, "fog_max_s", "Durée de la zone", "Ensuite elle s'efface (trop grande "
                                      "pour servir).", 10, 180, 5, lambda v: f"{v:.0f} s", to_value=float)
        app.switch_row(mm, "jungle_paths", "Trajets probables du jungler", "Un ou deux chemins en pointillés.")
        app.switch_row(mm, "ward_guide", "Où poser une balise", f"Sur la minimap, quand tu appuies sur "
                       f"{self._key('hotkey_ward')}.")
        app.depends(app.switch_row(mm, "ward_world", "Repère dans le jeu",
                                   "Marque au sol, ou flèche au bord de l'écran."), "ward_guide")
        app.depends(app.switch_row(mm, "ward_sound", "Son du repère", "Un son court quand il apparaît."),
                    "ward_guide")

        hud = self.page.section("Panneau de consignes", "Une consigne à la fois, en rouge en cas de danger.")
        app.switch_row(hud, "hud_enabled", "Afficher le panneau", "Menace, consigne du moment, prochain objectif.")
        self.hud_pos = app.depends(app.choice_row(hud, "hud_position", "Position", None,
                                                  position_choices(app.cfg, "hud")), "hud_enabled")
        app.depends(app.switch_row(hud, "hud_detailed", "Toujours détaillé",
                                   f"Sinon, maintiens {self._key('hotkey_details')} en jeu."), "hud_enabled")
        app.depends(app.switch_row(hud, "win_prob_hud", "Chances de victoire", "Un pourcentage dans le panneau."),
                    "hud_enabled")
        app.switch_row(hud, "text_tips", "Conseils écrits", "Un conseil court, jamais lu à voix haute.")
        app.depends(app.switch_row(hud, "tip_toasts", "Aussi en bandeau", "Le conseil s'affiche aussi en haut."),
                    "text_tips")

        buy = self.page.section("Achats")
        app.switch_row(buy, "item_advice", "Conseils d'achat", "L'objet à acheter au prochain retour.")
        app.depends(app.switch_row(buy, "hud_item_card", "Carte d'achat", "En base, mort ou avec l'or : l'objet, "
                                   "son icône et son prix."), "item_advice")
        app.depends(app.switch_row(buy, "item_advice_toasts", "Bandeau quand tu as l'or", None), "item_advice")
        app.switch_row(buy, "hud_build_strip", "Barre de build", f"Les 6 objets du build, en base ou en "
                       f"maintenant {self._key('hotkey_details')}.")

        scr = self.page.section("Écran")
        app.switch_row(scr, "essentials_strip", "Barre de l'essentiel", "En haut de l'écran : or contre ton "
                       "adversaire, CS par minute, prochain objectif, jungler ennemi, prochain objet.")
        app.switch_row(scr, "danger_flash", "Bords rouges en cas de gank", None)
        app.switch_row(scr, "plays_enabled", "Coups notés", "Un badge après un bon coup ou une erreur.")
        app.depends(app.choice_row(scr, "plays_position", "Position du badge", None, PLAYS_POSITIONS,
                                   segmented=True), "plays_enabled")
        app.depends(app.switch_row(scr, "plays_sound", "Son des bons coups", None), "plays_enabled")
        app.depends(app.switch_row(scr, "plays_sound_negative", "Son des erreurs", None), "plays_enabled",
                    "plays_sound")
        self._sync_visibility()

    def _key(self, field: str) -> str:
        return str(getattr(self.app.cfg, field, "") or "").replace("+", " + ") or "touche désactivée"

    def _sync_visibility(self) -> None:
        cfg = self.app.cfg
        self.fog_row.setVisible(cfg.fog_mode != "off")
        self.fog_row.parentWidget().sync_separators()

    def on_config(self, diff: set[str]) -> None:
        cfg = self.app.cfg
        if diff & {"overlay_mode", "fog_mode"}:
            self._sync_visibility()
        if diff & {"hud_xy", "hud_position", "radar_xy", "radar_position"}:
            self.hud_pos.ctl.set_choices(position_choices(cfg, "hud"), cfg.hud_position)
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
        try:            # sharp on a HiDPI screen: the 1080p composition scaled to the real pixels
            dpr = max(1.0, min(3.0, float(self.preview.devicePixelRatioF())))
        except Exception:
            dpr = 1.0

        def job() -> Any:
            from treeaicoach import ui_preview  # noqa: PLC0415

            comp = ui_preview.compose(cfg)
            return ui_preview.preview_images(comp, screen_w=int(round(width * dpr))).get("screen")

        def done(img: Any) -> None:
            self._preview_busy = False
            if img is not None:
                QtGui = self.app.QtGui
                rgb = img.convert("RGB")
                data = rgb.tobytes("raw", "RGB")
                qimg = QtGui.QImage(data, rgb.width, rgb.height, 3 * rgb.width, QtGui.QImage.Format_RGB888).copy()
                pm = _rounded(QtGui, self.app.QtCore, QtGui.QPixmap.fromImage(qimg), int(round(8 * dpr)))
                pm.setDevicePixelRatio(dpr)
                self.preview.setPixmap(pm)
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
