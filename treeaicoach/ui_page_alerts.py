"""Alertes et voix: what the coach says out loud, which alerts exist, reminders, and the voice."""

from __future__ import annotations

import logging
from typing import Any

from treeaicoach.ui_common import AUTO_VOICE, DANGER_MODES, ENGINE_LABELS, VOICE_LEVELS, _pct_value, fmt_decimal_fr, fmt_int_fr

log = logging.getLogger(__name__)

CASTER_STYLES = (("sobre", "Sobre"), ("coach", "Coach"), ("caster", "Annonceur"))
DISCORD_HELP = ("Une seule fois : sur discord.com/developers/applications, « New Application », onglet Bot, "
                "« Reset Token » et colle-le ici. Ouvre le lien d'invitation et choisis ton serveur. Ton identifiant : "
                "Paramètres Discord > Avancés > Mode développeur, puis clic droit sur ton nom > « Copier l'identifiant ». "
                "Le bot tourne dans TreeAI : il n'est là que quand l'app est ouverte.")


def danger_mode(cfg: Any) -> str:
    """The "Annonce d'un danger" choice from (beep_on_danger, danger_voice): "bip_voix" | "bip" | "voix"."""
    if not bool(getattr(cfg, "beep_on_danger", True)):
        return "voix"
    return "bip" if getattr(cfg, "danger_voice", "bip_voix") == "bip" else "bip_voix"


def danger_mode_fields(mode: str) -> dict[str, Any]:
    """(beep_on_danger, danger_voice) of an "Annonce d'un danger" choice."""
    if mode == "voix":
        return {"beep_on_danger": False, "danger_voice": "bip_voix"}
    return {"beep_on_danger": True, "danger_voice": "bip" if mode == "bip" else "bip_voix"}


def neural_voices() -> list[tuple[str, str]]:
    try:
        from treeaicoach.tts_neural import NEURAL_VOICES  # noqa: PLC0415

        return [(v, lbl) for v, lbl in NEURAL_VOICES]
    except Exception:
        return [("fr-FR-DeniseNeural", "Denise (France)")]


class AlertsPage:
    def __init__(self, app: Any) -> None:
        self.app = app
        W = app.W
        self.page = W.Page("Alertes et voix", "Le coach ne parle que pour ce qui ne peut pas attendre ; le reste est "
                                              "écrit dans le panneau.")
        say = self.page.section("Ce que le coach dit", action=W.button("Tester la voix", app.test_voice))
        app.choice_row(say, "voice_level", "Quantité", "Minimal : ganks, « Recule », objectifs. Normal : en plus "
                       "les rappels. Bavard : presque tout.", VOICE_LEVELS, segmented=True)
        self.danger = W.Segmented(DANGER_MODES, danger_mode(app.cfg))
        self.danger.changed.connect(lambda m: app.set_options(**danger_mode_fields(m)))
        say.add(W.Row("Annonce d'un danger", "Le bip part tout de suite ; la voix suit si elle est prête.",
                      self.danger))
        app.bind("beep_on_danger", lambda: self.danger.set_value(danger_mode(app.cfg)))
        app.bind("danger_voice", lambda: self.danger.set_value(danger_mode(app.cfg)))
        app.switch_row(say, "stance_voice", "Annoncer la posture", "« Prudent » ou « Attaque » quand elle change.")
        app.choice_row(say, "caster_style", "Mode annonceur", "Sobre : rien n'est lu. Coach : la probabilité de "
                       "victoire aux moments clés. Annonceur : plus vivant.", CASTER_STYLES, segmented=True)
        app.switch_row(say, "item_advice_speak", "Lire les conseils d'achat", "Sinon ils restent écrits.")
        app.switch_row(say, "ai_speak", "Lire le conseil IA à voix haute", "Désactivé par défaut : le conseil IA "
                       "reste écrit.")

        gank = self.page.section("Alertes de gank", "Quand un ennemi menace ta position. Le mode sûr les coupe "
                                                    "toutes.")
        app.switch_row(gank, "alert_jungler_approach", "Jungler ennemi qui approche", "L'alerte la plus utile.")
        app.switch_row(gank, "gank_pre_alert", "Nom du jungler dès qu'il sort du brouillard",
                       "« Lee Sin ! » quand il apparaît près de toi.")
        app.switch_row(gank, "alert_jungler_spotted", "Jungler repéré", "Où il vient d'être vu.")
        app.switch_row(gank, "alert_roam", "Ennemi en balade", "Un laner adverse quitte sa voie vers toi.")
        app.switch_row(gank, "alert_collapse", "Plusieurs ennemis arrivent", "Deux ennemis ou plus convergent.")
        app.switch_row(gank, "alert_laner_mia", "Adversaire de voie disparu", "« Manquant » quand ton adversaire "
                       "n'est plus visible.")
        app.slider_row(gank, "sensitivity", "Sensibilité", "Plus haut : alertes plus tôt, de plus loin.",
                       0.6, 1.6, 0.05, lambda v: fmt_decimal_fr(v, 2), to_value=float)

        rem = self.page.section("Rappels", "Écrits dans le panneau ; lus à voix haute selon la quantité choisie.")
        app.switch_row(rem, "objective_timers", "Annonce des objectifs", "Dragon, Héraut, Baron avant leur apparition.")
        app.switch_row(rem, "recall_reminder", "Rappel pour dépenser ton or", "Quand un retour à la base vaut le coup.")
        app.slider_row(rem, "recall_gold_threshold", "Seuil d'or du rappel", "Or à partir duquel le coach propose "
                       "un retour.", 500, 3000, 50, lambda v: f"{fmt_int_fr(v)} or", to_value=lambda v: int(v))
        app.switch_row(rem, "control_ward_reminder", "Balise de contrôle", "Penser à en acheter une.")
        app.switch_row(rem, "death_recap", "Récap de mort", "Une phrase sur la cause de ta mort.")
        app.switch_row(rem, "break_reminder", "Conseil de pause", "Après plusieurs parties d'affilée.")

        self._build_discord(app, W)

        v = self.page.section("Voix", "La voix neurale (en ligne) est la plus naturelle ; les voix Windows marchent "
                                      "sans Internet.")
        app.choice_row(v, "voice_engine", "Moteur de voix", "« Automatique » utilise la voix neurale si "
                       "Internet répond.", ENGINE_LABELS)
        cur = str(app.cfg.neural_voice or "fr-FR-DeniseNeural")
        self.neural = app.choice_row(v, "neural_voice", "Voix neurale", "Voix Microsoft en ligne (française).",
                                     [(cur, cur.replace("fr-FR-", "").replace("Neural", ""))])
        # the full list needs tts_neural (edge-tts, aiohttp: a slow import): read on a worker
        app.run_job(neural_voices, lambda vs: self.neural.ctl.set_choices(vs, app.cfg.neural_voice), None,
                    name="TreeAI-ui-neural-voices")
        self.neural_rate = app.slider_row(v, "neural_rate", "Vitesse de la voix neurale", "Défaut : +15 %.",
                                          -50, 100, 5, lambda x: f"{x:+.0f} %", to_value=lambda x: f"{int(x):+d}%",
                                          from_value=_pct_value)
        self.win_voice = app.choice_row(v, "voice_name", "Voix Windows", None, self._win_voices())
        self.voice_rate = app.slider_row(v, "voice_rate", "Vitesse de la voix Windows", "De -10 (lent) à 10 "
                                         "(rapide). Défaut : 2.", -10, 10, 1, lambda x: f"{x:+.0f}",
                                         to_value=lambda x: int(x))
        app.slider_row(v, "voice_volume", "Volume", None, 0, 100, 5, lambda x: f"{x:.0f} %",
                       to_value=lambda x: int(x))
        self._sync_engine_rows()

    # ------------------------------------------------------------------ bot Discord
    def _build_discord(self, app: Any, W: Any) -> None:
        dc = self.page.section(
            "Bot Discord", DISCORD_HELP)
        app.switch_row(dc, "discord_enabled", "Bot Discord", "Dans ton salon vocal, il annonce seulement « Top gank », "
                       "« Mid gank », « Bot gank » et le matchup au début. Rien d'autre.")
        app.entry_row(dc, "discord_token", "Token du bot", "Portail Discord > ton application > Bot > Reset Token.",
                      secret=True, placeholder="Colle le token ici")
        app.entry_row(dc, "discord_user_id", "Ton identifiant Discord", "Le bot rejoint le salon vocal où tu es.",
                      placeholder="Ex. 123456789012345678", width=220)
        self.dc_invite = dc.add(W.Row("Inviter le bot", None, W.button("Ouvrir le lien", self._open_invite)))
        self.dc_test_btn = W.button("Faire parler le bot", self._test_discord)
        self.dc_test = dc.add(W.Row("Tester", None, self.dc_test_btn))
        self._sync_discord()

    def _sync_discord(self) -> None:
        from treeaicoach import discord_bot  # noqa: PLC0415  (light: discord.py is imported by the bot thread)

        cfg = self.app.cfg
        url = discord_bot.invite_url(discord_bot.application_id_from_token(getattr(cfg, "discord_token", "")))
        self.dc_invite.control.setEnabled(bool(url))
        self.dc_invite.set_desc("Ajoute-le à ton serveur (droits : voir le salon, se connecter, parler)." if url
                                else "Colle d'abord le token : le lien se crée tout seul.")
        bot = discord_bot.active()
        if bot is not None:
            self.dc_test.set_desc(bot.status()[0])
        elif not getattr(cfg, "discord_enabled", False):
            self.dc_test.set_desc("Bot désactivé.")
        else:
            self.dc_test.set_desc("Il se connecte au démarrage du coach.")

    def _open_invite(self) -> None:
        from treeaicoach import discord_bot  # noqa: PLC0415

        url = discord_bot.invite_url(discord_bot.application_id_from_token(getattr(self.app.cfg, "discord_token", "")))
        if url:
            self.app.QtGui.QDesktopServices.openUrl(self.app.QtCore.QUrl(url))

    def _test_discord(self) -> None:
        cfg = self.app.cfg
        self.dc_test_btn.setEnabled(False)
        self.dc_test.set_desc("Connexion à Discord...")

        def job() -> tuple[str, bool]:
            from treeaicoach import discord_bot  # noqa: PLC0415

            bot = discord_bot.caller()
            bot.configure(cfg)
            return bot.test_blocking(45.0)

        def done(res: tuple[str, bool]) -> None:
            self.dc_test_btn.setEnabled(True)
            self.dc_test.set_desc(res[0])

        def failed(_exc: BaseException) -> None:
            self.dc_test_btn.setEnabled(True)
            self.dc_test.set_desc("Le test a échoué (voir le journal).")
        self.app.run_job(job, done, failed, name="TreeAI-ui-discord-test")

    def on_close(self) -> None:
        import sys  # noqa: PLC0415

        mod = sys.modules.get("treeaicoach.discord_bot")
        if mod is not None:
            mod.shutdown(1.5)                 # leave the voice channel now, not at Discord's timeout

    def _win_voices(self) -> list[tuple[str, str]]:
        return [("", AUTO_VOICE)] + [(n, n) for n in self.app._voices]

    def on_voices(self) -> None:
        self.win_voice.ctl.set_choices(self._win_voices(), self.app.cfg.voice_name)

    def _sync_engine_rows(self) -> None:
        eng = str(self.app.cfg.voice_engine)
        neural = eng in ("auto", "neural")
        for row, vis in ((self.neural, neural), (self.neural_rate, neural), (self.win_voice, not neural or eng == "auto"),
                         (self.voice_rate, eng != "neural")):
            row.setVisible(vis)
        self.neural.parentWidget().sync_separators()

    def on_config(self, diff: set[str]) -> None:
        if "voice_engine" in diff:
            self._sync_engine_rows()
        if diff & {"discord_enabled", "discord_token", "discord_user_id"}:
            self._sync_discord()


def build(app: Any) -> AlertsPage:
    return AlertsPage(app)
