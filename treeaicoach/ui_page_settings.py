"""Réglages: what a player sets once (start-up, League client, after the game, AI advice, in-game keys,
window).
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


def ai_count_text() -> str:
    """« 42 requêtes IA aujourd'hui · aucune limite » (the daily count, shown only)."""
    try:
        from treeaicoach.ai_advisor import DAILY_QUOTA

        n = int(DAILY_QUOTA.count)
    except Exception:
        n = 0
    return f"{n} requête{'s' if n > 1 else ''} IA aujourd'hui · aucune limite"


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
        self.page = W.Page("Réglages", "Ce que tu règles une fois.")

        gen = self.page.section("Démarrage")
        app.switch_row(gen, "autostart", "Démarrer le coach à l'ouverture", "Sinon, clique sur « Démarrer » à "
                       "l'Accueil.")
        ok, why = autostart_support()
        if ok:
            sw = W.Switch(False)
            gen.add(W.Row("Ouvrir TreeAI avec Windows", "Au démarrage de la session.", sw))
            app.run_job(get_windows_autostart, lambda on: sw.set_quiet(bool(on)), None, name="TreeAI-ui-autostart")
            sw.toggled.connect(lambda on: app.run_job(lambda: set_windows_autostart(bool(on)), None, None,
                                                      name="TreeAI-ui-autostart-set"))

        lol = self.page.section("Client League of Legends", "Lecture seule, sauf les runes, sorts et objets que "
                                                            "tu autorises ici.")
        app.switch_row(lol, "lcu_enabled", "Utiliser le client", "Tes vraies stats après la partie, la sélection "
                       "des champions et les joueurs.")
        dep = (lambda row: app.depends(row, "lcu_enabled"))
        dep(app.switch_row(lol, "cs_import_items", "Set d'objets dans la boutique", "Les objets du build, "
                           "dans l'onglet « Sets d'objets » de la boutique."))
        dep(app.switch_row(lol, "cs_auto_runes", "Runes automatiques", "Crée ou remplace la page « TreeAI », "
                           "jamais tes autres pages."))
        dep(app.switch_row(lol, "cs_auto_spells", "Sorts automatiques", "Saut éclair reste sur ta touche."))
        dep(app.switch_row(lol, "tilt_guard", "Pause après une série de défaites", "Un conseil à la sélection "
                           "des champions."))
        dep(app.switch_row(lol, "player_intel", "Rangs des joueurs", "Rang, LP et séries de chaque champion de ta "
                           "partie, gardés le temps de la partie."))
        app.depends(app.switch_row(lol, "discord_ranks", "Annoncer les rangs sur Discord", "Au début de la partie, "
                                   "le bot les écrit dans le salon et dit les plus utiles."), "lcu_enabled",
                    "player_intel")

        after = self.page.section("Après la partie")
        app.switch_row(after, "post_game_report", "Rapport d'après-partie", "Morts, ganks, jungler adverse et "
                       "conseils, dans le navigateur.")
        app.depends(app.switch_row(after, "open_report_automatically", "Ouvrir le rapport tout seul", None),
                    "post_game_report")

        ai = self.page.section("Conseils IA (facultatif)", "Gemini, Groq et OpenRouter ont une offre gratuite. La clé "
                                                          "reste sur ton PC.")
        self.ai_count = ai.add(W.Row("Requêtes aujourd'hui", ai_count_text()))
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

        win = self.page.section("Fenêtre")
        app.choice_row(win, "ui_scaling", "Taille de l'interface", "Appliquée au prochain lancement.", UI_SCALINGS)
        app.switch_row(win, "ui_confirm_quit", "Confirmer avant de quitter en partie", None)

        sto = self.page.section("Stockage", "Captures, parties, rapports et journaux gardés sur ton PC.")
        self.storage_row = sto.add(W.Row("Place utilisée", "Calcul…",
                                         W.button("Faire le ménage", self.clean_storage)))
        app.switch_row(sto, "storage_auto_clean", "Ménage automatique au démarrage", "Mêmes règles que le "
                       "bouton (réglages ci-dessous). Jamais les modèles de voix téléchargés.")
        app.slider_row(sto, "storage_keep_games", "Parties gardées complètes", "Les plus anciennes perdent leur "
                       "rapport HTML et leur vérité ; tes statistiques restent.", 3, 50, 1, lambda v: f"{v:.0f}",
                       to_value=int)
        app.slider_row(sto, "storage_keep_days", "Captures et rapports gardés", None, 1, 30, 1,
                       lambda v: f"{v:.0f} j", to_value=int)
        self.refresh_storage()
        app.run_job(self._auto_clean, lambda _r: self.refresh_storage(), None, name="TreeAI-ui-storage-auto")

    def _auto_clean(self) -> Any:
        from treeaicoach import storage  # noqa: PLC0415

        return storage.auto_cleanup(self.app.cfg)

    def refresh_storage(self) -> None:
        """« Place utilisée » row: sizes per kind, computed on a worker."""
        def job() -> str:
            from treeaicoach import storage  # noqa: PLC0415

            return storage.usage_text(storage.usage())
        try:
            self.app.run_job(job, lambda txt: self.storage_row.set_desc(txt), None, name="TreeAI-ui-storage")
        except Exception:
            log.debug("storage refresh failed", exc_info=True)

    def clean_storage(self) -> None:
        """« Faire le ménage »: plan on a worker, confirmation listing what goes and the space freed, then
        deletion on a worker. Never raises."""
        app = self.app
        from treeaicoach import storage  # noqa: PLC0415

        rules = storage.cfg_rules(app.cfg)

        def planned(plan: Any) -> None:
            from treeaicoach.ui_dialogs import message  # noqa: PLC0415

            text = storage.describe_plan(plan, rules["keep_games"])
            if not plan.items:
                message(app, "Faire le ménage", text, [("OK", "ok")])
                return
            if message(app, "Faire le ménage ?", text, [("Annuler", "no"), ("Supprimer", "yes")]) != "yes":
                return
            app.run_job(lambda: storage.run_cleanup(plan), cleaned, None, name="TreeAI-ui-storage-clean")

        def cleaned(res: Any) -> None:
            n, freed = res
            app.show_toast(f"Ménage fait : {n} fichier{'s' if n > 1 else ''} supprimé{'s' if n > 1 else ''}, "
                           f"{storage.size_label(freed)} libérés.")
            self.refresh_storage()

        app.run_job(lambda: storage.plan_cleanup(**rules), planned,
                    lambda e: app.show_error(f"Ménage impossible : {e}"), name="TreeAI-ui-storage-plan")

    def on_ai_test(self, ok: bool, short: str) -> None:
        self.ai_test.set_desc(("Clé valide : " if ok else "Échec : ") + short)


def build(app: Any) -> SettingsPage:
    return SettingsPage(app)
