"""Réglages: what a player sets once (start-up, after the game, window, AI advice, in-game keys).
The technical settings (detection, performance, maintenance) are on the Avancé page."""

from __future__ import annotations

import logging
from typing import Any

from treeaicoach.ui_common import (
    UI_SCALINGS,
    autostart_support,
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
        self.page = W.Page("Réglages", "Ce que tu règles une fois. La détection et la performance sont dans Avancé.")

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

        ai = self.page.section("Conseils IA (facultatif)", "Gemini, Groq et OpenRouter ont une offre gratuite. La clé "
                                                          "reste sur ton PC.")
        prov = app.choice_row(ai, "ai_provider", "Fournisseur", None,
                              [(app.cfg.ai_provider, "Désactivé" if app.cfg.ai_provider == "off" else
                                str(app.cfg.ai_provider).capitalize())])
        app.run_job(ai_providers, lambda ps: prov.ctl.set_choices(ps, app.cfg.ai_provider), None,
                    name="TreeAI-ui-ai-providers")   # ai_advisor is a large module: imported on a worker
        app.entry_row(ai, "ai_api_key", "Clé API", None, secret=True, placeholder="Colle ta clé ici")
        app.entry_row(ai, "ai_model", "Modèle", "Vide = modèle conseillé du fournisseur.", placeholder="Par défaut")
        self.ai_test = ai.add(W.Row("Tester la clé", "Une toute petite requête au fournisseur.",
                                    W.button("Tester", app.test_ai_key)))

        keys = self.page.section("Touches en jeu", "Touches globales (comme Discord ou OBS) : elles marchent pendant "
                                                   "la partie, sans quitter le jeu.")
        for field, title, desc in HOTKEYS:
            app.choice_row(keys, field, title, desc, hotkey_choices(str(getattr(app.cfg, field) or "")))


    def on_ai_test(self, ok: bool, short: str) -> None:
        self.ai_test.set_desc(("Clé valide : " if ok else "Échec : ") + short)


def build(app: Any) -> SettingsPage:
    return SettingsPage(app)
