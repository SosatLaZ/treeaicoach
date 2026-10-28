"""Avancé: detection of the minimap, analysis rate, performance, maintenance and support.
Rarely touched: the defaults suit almost every PC (docs/DESIGN.md, "Navigation")."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from treeaicoach.ui_common import CAPTURE_BACKENDS, DETECTORS, MINIMAP_MODES, MINIMAP_SIDES, PERF_MODES


class AdvancedPage:
    """Since v3.4 built into the « Avancé » fold of the Réglages page (``host``); alone it is its own page."""

    def __init__(self, app: Any, host: Any = None) -> None:
        self.app = app
        W = app.W
        self.page = host if host is not None else W.Page("Avancé", "Les valeurs par défaut conviennent à presque "
                                                                   "tous les PC. Utile si les marques tombent à "
                                                                   "côté ou si le jeu ralentit.")

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
        # call evaluation (call_eval.py): the kinds of advice this player ignores come less often
        self.calls_row = ana.add(W.Row("Conseils espacés", "…",
                                       W.button("Réinitialiser", self._reset_calls)))
        # call_eval imports numpy (~0.5 s): read on a worker, never while the page is built
        app.run_job(self._calls_text, lambda t: self.calls_row.set_desc(t), None, name="TreeAI-ui-calls")

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
        mnt.add(W.Row("Rapport d'analyse", "Les 60 dernières secondes de partie (minimap, détection, voix, "
                      f"conseils) dans un petit zip à m'envoyer{f' ({hk} en jeu)' if hk else ''}.",
                      W.button("Créer", lambda: make_analysis_report(app))))
        mnt.add(W.Row("Copier le diagnostic", "Un résumé texte à coller dans ton message (Ctrl+D).",
                      W.button("Copier", app.copy_diagnostic)))
        mnt.add(W.Row("Journaux", "Fichiers de journal de TreeAI.", W.button("Ouvrir", app.open_logs)))
        mnt.add(W.Row("Envoyer à TreeAI", "Un seul petit .zip (~10 Mo) : tes 3 dernières parties et leur vérité, "
                      "les captures utiles, les rapports, les journaux filtrés et la config du PC. Sans pseudo ni clé.",
                      W.button("Créer le zip", lambda: make_export(app))))
        app.switch_row(mnt, "selfcheck_auto_diag", "Diagnostic automatique", "Un diagnostic est enregistré tout seul "
                       "quand TreeAI détecte un problème.")
        app.switch_row(mnt, "collect_samples", "Collecter des captures de minimap", "Seulement les moments utiles "
                       "pour améliorer la détection (doutes, combats, champions empilés), 25 par partie au plus. "
                       "Reste sur ton PC.")
        app.depends(app.slider_row(mnt, "collect_interval_s", "Au plus une capture toutes les", None, 3, 10, 1,
                                   lambda v: f"{v:.0f} min", to_value=lambda m: float(m) * 60.0,
                                   from_value=lambda s: min(10.0, max(3.0, float(s or 0) / 60.0))),
                    "collect_samples")

    @staticmethod
    def _calls_text() -> str:
        try:
            from treeaicoach import call_eval  # noqa: PLC0415

            q = call_eval.quieted()
        except Exception:
            q = []
        if not q:
            return ("Après chaque partie, TreeAI vérifie si tu as suivi ses conseils. Ceux que tu ignores presque "
                    "toujours reviennent moins souvent (jamais coupés pour les ganks). Aucun pour l'instant.")
        names = ", ".join(f"{call_eval.kind_label(k)} (×{v:.1f})" for k, v, _w in q[:4])
        return f"Moins fréquents car rarement suivis : {names}. Réinitialiser remet tout à la normale."

    def _reset_calls(self) -> None:
        try:
            from treeaicoach import call_eval  # noqa: PLC0415

            ok = call_eval.reset_stats()
        except Exception:
            ok = False
        self.calls_row.set_desc(self._calls_text())
        try:
            (self.app.show_toast if ok else self.app.show_error)(
                "Conseils remis à la normale." if ok else "Impossible de réinitialiser les conseils.")
        except Exception:
            pass

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


def make_export(app: Any) -> None:
    """Button « Envoyer à TreeAI »: export.build_export on a worker (with the running engine: settings,
    game running, perf), then the zip is shown in the file manager. Never raises."""
    try:
        from treeaicoach import export  # noqa: PLC0415

        eng = getattr(app, "engine", None)

        def done(res: Any) -> None:
            path, st = res
            if path is None:
                app.show_error(f"Zip impossible : {st.get('error') or 'rien à envoyer'}.")
                return
            app.show_toast(f"Zip prêt : {st['games']} parties, {st['captures']} captures, {st['reports']} rapports "
                           f"({export.size_label(int(st['bytes']))}). Glisse-le dans ton message.")
            try:
                from treeaicoach.ui_common import open_path  # noqa: PLC0415

                open_path(path, select=True)
            except Exception:
                pass

        app.show_toast("Création du zip en cours…")
        app.run_job(lambda: export.build_export(engine=eng), done,
                    lambda e: app.show_error(f"Zip impossible : {e}"), name="TreeAI-ui-export")
    except Exception:
        pass


def make_analysis_report(app: Any) -> None:
    """Button « Rapport d'analyse »: the last 60 s (analysis_report.py) zipped into <données>/reports,
    then the folder is opened. Never raises."""
    try:
        from treeaicoach import analysis_report  # noqa: PLC0415

        eng = getattr(app, "engine", None)

        def done(path: Any) -> None:
            if not path:
                app.show_toast("Rapport d'analyse impossible pour l'instant.", "warning")
                return
            app.show_toast(f"Rapport d'analyse créé : {Path(path).name}")
            try:
                from treeaicoach.ui_common import open_path  # noqa: PLC0415

                open_path(Path(path).parent)
            except Exception:
                pass

        app.run_job(lambda: analysis_report.request(eng, block=True), done,
                    lambda e: app.show_toast(f"Rapport d'analyse impossible : {e}", "warning"),
                    name="TreeAI-ui-report")
    except Exception:
        pass
