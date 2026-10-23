"""Avancé: detection of the minimap, analysis rate, performance, maintenance and support.
Rarely touched: the defaults suit almost every PC (docs/DESIGN.md, "Navigation")."""

from __future__ import annotations

from typing import Any

from treeaicoach.ui_common import CAPTURE_BACKENDS, DETECTORS, MINIMAP_MODES, MINIMAP_SIDES, PERF_MODES, fmt_decimal_fr


class AdvancedPage:
    def __init__(self, app: Any) -> None:
        self.app = app
        W = app.W
        self.page = W.Page("Avancé", "Les valeurs par défaut conviennent à presque tous les PC. Utile si les "
                                     "marques tombent à côté ou si le jeu ralentit.")

        det = self.page.section("Minimap", "Trouvée toute seule au début de la partie. Calibre-la à la main si les "
                                           "marques tombent à côté.", W.button("Calibrer", app.calibrate))
        app.choice_row(det, "minimap_mode", "Localisation", None, MINIMAP_MODES, segmented=True)
        app.choice_row(det, "minimap_side", "Côté de la minimap", "Position de la minimap dans les options du jeu.",
                       MINIMAP_SIDES, segmented=True)
        self.rect_row = det.add(W.Row("Calibration manuelle", self._rect_text()))

        ana = self.page.section("Analyse")
        app.slider_row(ana, "target_fps", "Images par seconde", "Plus c'est haut, plus les alertes sont réactives "
                       "(et plus le processeur travaille).", 2, 20, 1, lambda v: f"{v:.0f}", to_value=float)
        app.choice_row(ana, "detector_backend", "Détecteur", "Le réseau de neurones est plus précis ; le classique "
                       "dépanne.", DETECTORS)
        app.switch_row(ana, "download_skin_icons", "Télécharger les icônes de skins", "Icônes publiques "
                       "(Data Dragon), pour reconnaître les skins.")

        perf = self.page.section("Performance")
        app.choice_row(perf, "perf_mode", "Mode", "« Auto » mesure ton PC ; « PC modeste » analyse moins souvent.",
                       PERF_MODES, segmented=True)
        app.switch_row(perf, "adaptive_rate", "Cadence adaptative", "Analyse plus lente quand tout est calme.")
        app.slider_row(perf, "overlay_fps", "Fluidité de l'overlay", "Images par seconde des marques sur la minimap.",
                       10, 60, 5, lambda v: f"{v:.0f} img/s", to_value=float)
        app.switch_row(perf, "pause_when_unfocused", "Pause hors du jeu", "Overlay caché et analyse ralentie quand "
                       "le jeu n'est pas devant.")
        app.switch_row(perf, "low_priority", "Priorité basse", "Le jeu passe toujours en premier.")
        app.switch_row(perf, "eco_qos_v2", "Économie d'énergie de Windows 11", "Peut saccader le suivi : "
                       "désactivé par défaut.")
        app.choice_row(perf, "capture_backend", "Capture d'écran", "« Compatible » si la capture reste noire.",
                       CAPTURE_BACKENDS, segmented=True)
        app.switch_row(perf, "capture_wgc_border", "Capture de la fenêtre du jeu (cadre jaune Windows 10)",
                       "Windows 10 entoure alors le jeu d'un cadre jaune ; nos repères ne gênent plus la détection.")

        mnt = self.page.section("Maintenance et support")
        self.diag_btn = W.button("Lancer", app.start_diagnostic)
        hk = str(app.cfg.hotkey_diag or "").replace("+", " + ")
        self.diag_row = mnt.add(W.Row("Diagnostic complet", f"Une minute de partie enregistrée pour signaler un "
                                      f"problème ({hk} en jeu)." if hk else "Une minute de partie enregistrée.",
                                      self.diag_btn))
        mnt.add(W.Row("Copier le diagnostic", "Un résumé texte à coller dans ton message (Ctrl+D).",
                      W.button("Copier", app.copy_diagnostic)))
        mnt.add(W.Row("Journaux", "Fichiers de journal de TreeAI.", W.button("Ouvrir", app.open_logs)))
        mnt.add(W.Row("Tout envoyer en un fichier", "Un seul .zip avec tes captures de minimap, tes parties et "
                      "leur vérité, prêt à envoyer.", W.button("Créer le zip", app.export_for_treeai)))
        app.switch_row(mnt, "selfcheck_auto_diag", "Diagnostic automatique", "Un diagnostic est enregistré tout seul "
                       "quand TreeAI détecte un problème.")
        app.switch_row(mnt, "collect_samples", "Collecter des captures de minimap", "Pour améliorer la détection "
                       "(reste sur ton PC).")
        app.slider_row(mnt, "collect_interval_s", "Intervalle de collecte", None, 0.5, 10, 0.5,
                       lambda v: f"{fmt_decimal_fr(v, 1)} s", to_value=float)

    def _rect_text(self) -> str:
        r = self.app.cfg.manual_minimap_rect
        if not r:
            return "Aucune calibration manuelle enregistrée."
        return f"{r.get('w')} × {r.get('h')} px à ({r.get('x')}, {r.get('y')}), écran {r.get('screen_w')} × {r.get('screen_h')}."

    def on_config(self, diff: set[str]) -> None:
        if "manual_minimap_rect" in diff:
            self.rect_row.set_desc(self._rect_text())

    def set_diag_text(self, msg: str, running: bool) -> None:
        self.diag_row.set_desc(msg)
        self.diag_btn.setEnabled(not running)



def build(app: Any) -> AdvancedPage:
    return AdvancedPage(app)
