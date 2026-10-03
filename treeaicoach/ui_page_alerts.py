"""Alertes et voix: what the coach says out loud, which alerts exist, reminders, and the voice."""

from __future__ import annotations

import logging
from typing import Any

from treeaicoach.ui_common import AUTO_VOICE, DANGER_MODES, ENGINE_LABELS, VOICE_LEVELS, _pct_value, fmt_decimal_fr, fmt_int_fr

log = logging.getLogger(__name__)

CASTER_STYLES = (("sobre", "Sobre"), ("coach", "Coach"), ("caster", "Annonceur"))


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


def build(app: Any) -> AlertsPage:
    return AlertsPage(app)
