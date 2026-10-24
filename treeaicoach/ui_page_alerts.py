"""Alertes et voix: what the coach says out loud, which alerts exist, reminders, and the voice."""

from __future__ import annotations

import logging
from typing import Any

from treeaicoach.ui_common import AUTO_VOICE, DANGER_MODES, ENGINE_LABELS, VOICE_LEVELS, _pct_value, fmt_decimal_fr, fmt_int_fr
from treeaicoach.ui_page_settings import hotkey_choices

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
        return [("fr-CA-SylvieNeural", "Sylvie (Québec)")]


def _mics() -> list[tuple[str, str]]:
    try:
        from treeaicoach.voice_assistant import list_mics  # noqa: PLC0415

        return list_mics()
    except Exception:
        return [("", "Micro par défaut de Windows")]


def listen_line(snap: dict | None) -> str:
    """The « Écoute » row: « modèle prêt · niveau ▮▮▮▯▯▯▯▯ · dernier mot entendu « hé coach » » (+ the
    status when something is wrong). Pure (no Qt)."""
    if not snap:
        return "Pas encore lancée : active l'assistant ou appuie sur « Tester »."
    bits = ["test micro" if snap.get("mic_checking") else str(snap.get("stage_label") or "")]
    if snap.get("listening"):
        bits.append(f"niveau {snap.get('bar') or ''}")
    heard = str(snap.get("heard") or "")
    bits.append(f"dernier mot entendu « {heard} »" if heard else "rien entendu pour l'instant")
    if snap.get("listening") and snap.get("mic_label"):
        bits.append(f"micro « {snap['mic_label']} »")
    bits.append("Whisper local" if snap.get("whisper") else
                (f"Vosk ({snap['whisper_status']})" if snap.get("whisper_status") else ""))
    line = " · ".join(b for b in bits if b)
    if snap.get("stage") == "error" or snap.get("silent_mic"):
        line += f"\n{snap.get('status') or ''}"
        if snap.get("engine_error"):
            line += f" ({snap['engine_error']})"
    return line


class AlertsPage:
    def __init__(self, app: Any) -> None:
        self.app = app
        W = app.W
        self.page = W.Page("Alertes et voix", "Le coach parle seulement pour ce qui ne peut pas attendre.")
        say = self.page.section("Ce que le coach dit", action=W.button("Tester", app.test_voice,
                                                                        tip="Le coach dit une phrase."))
        app.choice_row(say, "voice_level", "Quantité", "Minimal : ganks, « Recule », objectifs. Bavard : "
                       "presque tout.", VOICE_LEVELS, segmented=True)
        self.danger = W.Segmented(DANGER_MODES, danger_mode(app.cfg))
        self.danger.changed.connect(lambda m: app.set_options(**danger_mode_fields(m)))
        say.add(W.Row("Annonce d'un danger", "Le bip part tout de suite, la voix suit.", self.danger))
        app.bind("beep_on_danger", lambda: self.danger.set_value(danger_mode(app.cfg)))
        app.bind("danger_voice", lambda: self.danger.set_value(danger_mode(app.cfg)))
        app.switch_row(say, "stance_voice", "Annoncer la posture", "« Prudent » ou « Attaque » quand elle change.")
        app.choice_row(say, "caster_style", "Mode annonceur", "Coach : tes chances de victoire aux moments clés.",
                       CASTER_STYLES, segmented=True)
        app.switch_row(say, "item_advice_speak", "Lire les conseils d'achat", "Sinon ils restent écrits.")
        app.switch_row(say, "ai_auto", "Conseils IA automatiques", "Sinon l'IA répond seulement à tes questions.")
        app.switch_row(say, "ai_speak", "Lire le conseil IA", "Sinon il reste écrit.")

        from treeaicoach.voice_assistant import PC_MIC_ENABLED

        va = self.page.section("Assistant vocal", ("Reconnaissance sur ton PC (Whisper, gratuit, hors ligne). "
                               "Le micro est écouté seulement en partie.") if PC_MIC_ENABLED else
                               ("Micro du PC désactivé : le coach t'écoute par Discord (page Discord, "
                                "« Écoute « Hey coach » »)."),
                               action=W.button("Tester", self.test_assistant,
                                               tip="Dis « Hey coach » puis une question (20 s)."))
        va_row = app.switch_row(va, "voice_assistant", "Assistant « Hey coach » (micro du PC)", "Dis « Hey coach » puis ta question : "
                       "réponse à voix haute en une ou deux phrases.")
        if not PC_MIC_ENABLED and va_row is not None:
            va_row.setEnabled(False)
        self.mic = app.depends(app.choice_row(va, "voice_assistant_mic", "Micro", "Celui de ton casque.",
                                              [("", "Micro par défaut de Windows")]), "voice_assistant")
        app.run_job(_mics, lambda ms: self.mic.ctl.set_choices(ms, app.cfg.voice_assistant_mic), None,
                    name="TreeAI-ui-mics")
        app.depends(app.choice_row(va, "hotkey_voice_ask", "Touche « parler »", "Appuie, puis pose ta question.",
                                   hotkey_choices(str(getattr(app.cfg, "hotkey_voice_ask", "") or ""))),
                    "voice_assistant")
        self.check_row = app.depends(va.add(W.Row("Test micro", "5 s : micro utilisé, niveau et phrase comprise.",
                                                  W.button("Test micro", self.mic_check))), "voice_assistant")
        # live « Écoute » panel: every stage of the chain is visible (mic level, last words, state)
        self.listen_row = va.add(W.Row("Écoute", listen_line(None)))
        self._listen_shown = ""
        self._listen_timer = app.QtCore.QTimer(self.page)
        self._listen_timer.timeout.connect(self._tick_listen)
        self._listen_timer.start(400)

        gank = self.page.section("Alertes de gank", "Le mode sûr les coupe toutes.")
        app.switch_row(gank, "alert_jungler_approach", "Jungler ennemi qui approche", "L'alerte la plus utile.")
        app.switch_row(gank, "gank_pre_alert", "Nom du jungler qui sort du brouillard", "« Lee Sin ! » près de toi.")
        app.switch_row(gank, "alert_jungler_spotted", "Jungler repéré", "Où il vient d'être vu.")
        app.switch_row(gank, "alert_roam", "Ennemi en balade", "Un laner adverse quitte sa voie vers toi.")
        app.switch_row(gank, "alert_collapse", "Plusieurs ennemis arrivent", "Deux ennemis ou plus convergent.")
        app.switch_row(gank, "alert_laner_mia", "Adversaire de voie disparu", "« Manquant » quand tu ne le vois plus.")
        app.slider_row(gank, "sensitivity", "Sensibilité", "Plus haut : alertes plus tôt, de plus loin.",
                       0.6, 1.6, 0.05, lambda v: fmt_decimal_fr(v, 2), to_value=float)

        rem = self.page.section("Rappels", "Écrits dans le panneau, lus selon la quantité choisie.")
        app.switch_row(rem, "objective_timers", "Annonce des objectifs", "Dragon, Héraut, Baron avant leur apparition.")
        app.switch_row(rem, "recall_reminder", "Rappel pour dépenser ton or", "Quand un retour à la base vaut le coup.")
        app.depends(app.slider_row(rem, "recall_gold_threshold", "Seuil d'or du rappel", None, 500, 3000, 50,
                                   lambda v: f"{fmt_int_fr(v)} or", to_value=lambda v: int(v)), "recall_reminder")
        app.switch_row(rem, "control_ward_reminder", "Balise de contrôle", "Penser à en acheter une.")
        app.switch_row(rem, "death_recap", "Récap de mort", "Une phrase sur la cause de ta mort.")
        app.switch_row(rem, "break_reminder", "Conseil de pause", "Après plusieurs parties d'affilée.")

        v = self.page.section("Voix", "La voix neurale demande Internet ; les voix Windows marchent sans.")
        app.choice_row(v, "voice_engine", "Moteur de voix", "« Automatique » : la voix neurale si Internet répond.",
                       ENGINE_LABELS)
        cur = str(app.cfg.neural_voice or "fr-CA-SylvieNeural")
        self.neural = app.choice_row(v, "neural_voice", "Voix neurale", "Voix Microsoft en ligne.",
                                     [(cur, cur.replace("fr-FR-", "").replace("fr-CA-", "").replace("Neural", ""))])
        # the full list needs tts_neural (edge-tts, aiohttp: a slow import): read on a worker
        app.run_job(neural_voices, lambda vs: self.neural.ctl.set_choices(vs, app.cfg.neural_voice), None,
                    name="TreeAI-ui-neural-voices")
        # optional paid ElevenLabs voice with an African accent (tts_eleven.py): key first, then search, then pick
        self.eleven_key = app.entry_row(v, "eleven_api_key", "Clé ElevenLabs", "Option payante : voix française "
                                        "à accent africain.", secret=True, placeholder="Colle ta clé ici")
        self.eleven_find = v.add(W.Row("Voix à accent africain", "Puis choisis-la dans « Voix neurale ».",
                                       W.button("Chercher", self._find_eleven)))
        self.neural_rate = app.slider_row(v, "neural_rate", "Vitesse de la voix neurale", "Défaut : +15 %.",
                                          -50, 100, 5, lambda x: f"{x:+.0f} %", to_value=lambda x: f"{int(x):+d}%",
                                          from_value=_pct_value)
        self.win_voice = app.choice_row(v, "voice_name", "Voix Windows", None, self._win_voices())
        self.voice_rate = app.slider_row(v, "voice_rate", "Vitesse de la voix Windows", "Défaut : 2.", -10, 10, 1,
                                         lambda x: f"{x:+.0f}", to_value=lambda x: int(x))
        app.slider_row(v, "voice_volume", "Volume", None, 0, 100, 5, lambda x: f"{x:.0f} %",
                       to_value=lambda x: int(x))
        self._sync_engine_rows()

    def _tick_listen(self) -> None:
        """Refresh the « Écoute » row while the page is shown (cheap: a dict copy)."""
        try:
            if getattr(self.app, "_closing", False) or not self.page.isVisible():
                return
            import sys  # noqa: PLC0415

            mod = sys.modules.get("treeaicoach.voice_assistant")
            if mod is not None:
                self._keep_auto_mic(mod.assistant())
            text = listen_line(mod.assistant().snapshot() if mod is not None else None)
            if text != self._listen_shown:
                self._listen_shown = text
                self.listen_row.set_desc(text)
        except Exception:
            log.debug("listen panel refresh failed", exc_info=True)

    def _keep_auto_mic(self, a: Any) -> None:
        """The assistant replaced a silent mic during a game: the setting follows (saved, dropdown)."""
        name = a.take_auto_mic()
        if name and name != self.app.cfg.voice_assistant_mic:
            self.app.set_option("voice_assistant_mic", name)
            self.app.run_job(_mics, lambda ms: self.mic.ctl.set_choices(ms, name), None, name="TreeAI-ui-mics")
            self.app.show_toast(f"Micro « {name} » gardé : l'autre n'envoyait aucun son.", "info")

    def test_assistant(self) -> None:
        """"Tester : dis « Hey coach »": 20 s of listening, even outside a game and with the switch
        off; the « Écoute » row shows the level and what is heard, a toast says the result."""
        app = self.app
        try:
            from treeaicoach import voice_assistant as va  # noqa: PLC0415

            a = va.assistant()
            a.configure(app.cfg)
            a.test(lambda msg, ok: app.post(lambda: app.show_toast(msg, "info" if ok else "warning")))
            app.show_toast(a.status if not va.model_ready() else
                           "Dis « Hey coach » puis une question (20 s).", "info")
        except Exception:
            log.exception("voice assistant test failed")
            app.show_toast("Assistant vocal indisponible sur ce PC.", "warning")

    def mic_check(self) -> None:
        """« Test micro 5 s »: what the recognizer understood + the max level, in a toast (the « Écoute »
        row shows the level and the words live)."""
        app = self.app
        try:
            from treeaicoach import voice_assistant as va  # noqa: PLC0415

            a = va.assistant()
            a.configure(app.cfg)
            def _done(msg: str, ok: bool) -> None:
                def _ui() -> None:
                    best = a.suggested_mic
                    if best and best != app.cfg.voice_assistant_mic:
                        app.set_option("voice_assistant_mic", best)       # the input that HAS the voice
                        app.run_job(_mics, lambda ms: self.mic.ctl.set_choices(ms, best), None,
                                    name="TreeAI-ui-mics")
                        a.configure(app.cfg)
                    app.show_toast(msg, "info" if ok else "warning")
                app.post(_ui)

            if not a.mic_check(_done):
                app.show_toast("Test micro déjà en cours.", "info")
                return
            app.show_toast("Parle pendant 5 s…", "info")
        except Exception:
            log.exception("mic check failed")
            app.show_toast("Test micro impossible sur ce PC.", "warning")

    def _win_voices(self) -> list[tuple[str, str]]:
        return [("", AUTO_VOICE)] + [(n, n) for n in self.app._voices]

    def _find_eleven(self) -> None:
        app = self.app
        key = str(getattr(app.cfg, "eleven_api_key", "") or "")

        def done(vs: list[tuple[str, str]]) -> None:
            if not vs:
                app.show_error("Aucune voix trouvée : vérifie la clé ElevenLabs (et que l'offre est payante).")
                return
            cur = str(app.cfg.neural_voice)
            self.neural.ctl.set_choices(neural_voices() + vs, cur)

        from treeaicoach.tts_eleven import list_african_voices  # noqa: PLC0415
        app.run_job(lambda: list_african_voices(key), done, None, name="TreeAI-ui-eleven-voices")

    def on_voices(self) -> None:
        self.win_voice.ctl.set_choices(self._win_voices(), self.app.cfg.voice_name)

    def _sync_engine_rows(self) -> None:
        eng = str(self.app.cfg.voice_engine)
        neural = eng in ("auto", "neural")
        for row, vis in ((self.neural, neural), (self.neural_rate, neural), (self.eleven_key, neural), (self.eleven_find, neural), (self.win_voice, not neural or eng == "auto"),
                         (self.voice_rate, eng != "neural")):
            row.setVisible(vis)
        self.neural.parentWidget().sync_separators()

    def on_config(self, diff: set[str]) -> None:
        if "voice_engine" in diff:
            self._sync_engine_rows()


def build(app: Any) -> AlertsPage:
    return AlertsPage(app)
