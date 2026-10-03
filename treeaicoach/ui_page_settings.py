"""Réglages: start-up and window, detection, AI advice, in-game keys, performance, maintenance.
One scrolling page of grouped lists (System Settings style); each setting lives in one place."""

from __future__ import annotations

import logging
from typing import Any

from treeaicoach.ui_common import (
    CAPTURE_BACKENDS,
    DETECTORS,
    MINIMAP_MODES,
    MINIMAP_SIDES,
    PERF_MODES,
    UI_SCALINGS,
    autostart_support,
    fmt_decimal_fr,
    get_windows_autostart,
    set_windows_autostart,
)

log = logging.getLogger(__name__)

HOTKEYS: tuple[tuple[str, str, str], ...] = (
    ("hotkey_jungler", "Où est le jungler ?", "Le coach dit où le jungler ennemi a été vu."),
    ("hotkey_mute", "Couper la voix", "Coupe ou rétablit la voix du coach."),
    ("hotkey_overlay", "Afficher / masquer l'overlay", None),
    ("hotkey_details", "Overlay détaillé (maintenir)", "Le panneau complet tant que la touche est enfoncée."),
    ("hotkey_ward", "Où poser une balise ?", "Les meilleurs endroits maintenant."),
    ("hotkey_ai", "Demander à l'IA", "Un conseil de l'IA sur la situation (si l'IA est activée)."),
    ("hotkey_diag", "Diagnostic complet", "Enregistre une minute de partie pour signaler un problème."),
)


def hotkey_choices(current: str) -> list[tuple[str, str]]:
    keys = [f"F{i}" for i in range(1, 13)] + ["Ctrl+F8"]
    out = [("", "Désactivée")] + [(k, k.replace("+", " + ")) for k in keys]
    if current and current not in keys:
        out.append((current, current.replace("+", " + ")))
    return out


def ai_providers() -> list[tuple[str, str]]:
    try:
        from treeaicoach.ai_advisor import PROVIDER_CHOICES  # noqa: PLC0415

        return list(PROVIDER_CHOICES)
    except Exception:
        return [("off", "Désactivé")]


class SettingsPage:
    def __init__(self, app: Any) -> None:
        self.app = app
        W = app.W
        self.page = W.Page("Réglages")

        gen = self.page.section("Démarrage")
        app.switch_row(gen, "autostart", "Démarrer l'analyse au lancement", "Sinon, clique sur « Démarrer » à "
                       "l'Accueil.")
        ok, why = autostart_support()
        if ok:
            sw = W.Switch(False)
            gen.add(W.Row("Ouvrir TreeAI avec Windows", "Au démarrage de la session.", sw))
            app.run_job(get_windows_autostart, lambda on: sw.set_quiet(bool(on)), None, name="TreeAI-ui-autostart")
            sw.toggled.connect(lambda on: app.run_job(lambda: set_windows_autostart(bool(on)), None, None,
                                                      name="TreeAI-ui-autostart-set"))

        after = self.page.section("Après la partie")
        app.switch_row(after, "post_game_report", "Rapport d'après-partie", "Morts, ganks, jungler adverse et "
                       "conseils, dans le navigateur.")
        app.switch_row(after, "open_report_automatically", "Ouvrir le rapport tout seul", "À la fin de chaque partie.")
        app.switch_row(after, "lcu_enabled", "Lire le client League of Legends", "Tes vraies stats après la partie "
                       "(lecture seule).")

        win = self.page.section("Fenêtre")
        app.choice_row(win, "ui_scaling", "Taille de l'interface", "Appliquée au prochain lancement.", UI_SCALINGS)
        app.switch_row(win, "ui_confirm_quit", "Confirmer avant de quitter en partie", None)

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

        ai = self.page.section("Conseils IA (facultatif)", "Gemini, Groq et OpenRouter ont une offre gratuite. La clé "
                                                          "reste sur ton PC.")
        app.choice_row(ai, "ai_provider", "Fournisseur", None, ai_providers())
        app.entry_row(ai, "ai_api_key", "Clé API", None, secret=True, placeholder="Colle ta clé ici")
        app.entry_row(ai, "ai_model", "Modèle", "Vide = modèle conseillé du fournisseur.", placeholder="Par défaut")
        self.ai_test = ai.add(W.Row("Tester la clé", "Une toute petite requête au fournisseur.",
                                    W.button("Tester", app.test_ai_key)))

        keys = self.page.section("Touches en jeu", "Touches globales (comme Discord ou OBS) : elles marchent pendant "
                                                   "la partie, sans quitter le jeu.")
        for field, title, desc in HOTKEYS:
            app.choice_row(keys, field, title, desc, hotkey_choices(str(getattr(app.cfg, field) or "")))

        perf = self.page.section("Performance", "Les valeurs par défaut conviennent à presque tous les PC.")
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

        mnt = self.page.section("Maintenance et support")
        self.diag_btn = W.button("Lancer", app.start_diagnostic)
        hk = str(app.cfg.hotkey_diag or "").replace("+", " + ")
        self.diag_row = mnt.add(W.Row("Diagnostic complet", f"Une minute de partie enregistrée pour signaler un "
                                      f"problème ({hk} en jeu)." if hk else "Une minute de partie enregistrée.",
                                      self.diag_btn))
        mnt.add(W.Row("Copier le diagnostic", "Un résumé texte à coller dans ton message (Ctrl+D).",
                      W.button("Copier", app.copy_diagnostic)))
        mnt.add(W.Row("Journaux", "Fichiers de journal de TreeAI.", W.button("Ouvrir", app.open_logs)))
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

    def on_ai_test(self, ok: bool, short: str) -> None:
        self.ai_test.set_desc(("Clé valide : " if ok else "Échec : ") + short)


def build(app: Any) -> SettingsPage:
    return SettingsPage(app)
