"""Discord: the bot made visible. Live state, the setup checklist (token, servers, the player found in
voice, permissions, voice test), where the bot is (server / voice channels, join / leave) and what it
did lately.

The bot runs on its own asyncio thread (discord_bot.DiscordCaller); this page only reads its
thread-safe :meth:`snapshot` (a copy under a lock, no network) once a second while the page is
shown, and sends non-blocking commands (``join`` / ``leave`` / ``say_test``)."""

from __future__ import annotations

import logging
from typing import Any, Callable

log = logging.getLogger(__name__)

POLL_MS = 1000
DISCORD_HELP = ("Une seule fois : discord.com/developers/applications > « New Application » > Bot > « Reset "
                "Token », colle-le ici, puis « Inviter ». Ton identifiant : Discord > Paramètres > Avancés > Mode "
                "développeur, clic droit sur ton nom > « Copier l'identifiant ». Le bot n'est là que quand TreeAI "
                "est ouvert.")
STATE_TITLES = {"off": "Bot désactivé", "no_token": "Token manquant", "connecting": "Connexion…",
                "bad_token": "Token refusé", "error": "Erreur de connexion", "unavailable": "Indisponible"}
OK_MARK, BAD_MARK, WAIT_MARK = "✓", "✗", "·"


def _people(n: int) -> str:
    return "vide" if n <= 0 else ("1 personne" if n == 1 else f"{n} personnes")


def offline_snapshot(cfg: Any) -> dict:
    """What the page shows before the bot exists (never started in this session)."""
    from treeaicoach import discord_bot  # noqa: PLC0415  (light: discord.py is imported by the bot thread)

    on = bool(getattr(cfg, "discord_enabled", False))
    tok = discord_bot.clean_token(getattr(cfg, "discord_token", ""))
    state = "off" if not on else ("no_token" if not tok else "connecting")
    status = {"off": "Active le bot pour le connecter.", "no_token": "Colle le token du bot.",
              "connecting": "Connexion à Discord..."}[state]
    return {"state": state, "status": status, "ok": False, "error": "", "bot_name": "", "guilds": [],
            "user": {"id": discord_bot.clean_user_id(getattr(cfg, "discord_user_id", "")), "found": False},
            "channel": None, "latency_ms": None, "events": [], "in_game": False,
            "user_id": discord_bot.clean_user_id(getattr(cfg, "discord_user_id", "")),
            "voice_source": "", "voice_checked": False, "muted": False, "voice_map": [], "roster": []}


SOURCE_TEXT = {"toi": "C'est toi : tes ganks passent déjà par le coach.",
               "auto": "Reconnu par son pseudo. Le bot annonce ses ganks.",
               "manuel": "Choisi à la main. Le bot annonce ses ganks."}
ENEMY_TEXT = "Il joue dans l'équipe d'en face : pas d'annonce de gank pour lui. Il peut dire « Hey coach »."
NOT_PLAYING_TEXT = "Ne joue pas : pas d'annonce de gank pour lui. Il peut quand même dire « Hey coach »."


def listen_text(view: dict | None) -> str:
    """« Écoute : ▮▮▮▯▯▯▯▯ · dernier mot entendu « hé coach » » for one member ("" when not heard yet)."""
    if not view:
        return ""
    heard = str(view.get("heard") or "")
    bits = [f"Écoute : niveau {view.get('bar') or ''}".strip()]
    stage = str(view.get("stage") or "")
    if stage and stage != "écoute":
        bits.append(stage)
    bits.append(f"dernier mot entendu « {heard} »" if heard else "aucun mot reconnu")
    return " · ".join(bits)


LISTEN_TEST_DESC = "30 s, même hors partie : chacun dit « Hey coach » puis une question."


def listen_chain_text(listen: dict | None) -> str:
    """Every stage of the voice receive chain in one line, so « ça ne marche pas » can be located:
    « Audio reçu : 1 234 paquets, 1 200 décodés · 30 chiffrés (DAVE) illisibles ». "" before the bot
    listens. Pure (no Qt)."""
    st = dict((listen or {}).get("stats") or {})
    if not st.get("attached"):
        return ""
    n, dec = int(st.get("packets") or 0), int(st.get("decoded") or 0)
    if n == 0:
        return "Audio reçu : aucun paquet pour l'instant (personne n'a parlé, ou le bot n'entend pas le salon)."
    def fr(x: int) -> str:
        return f"{x:,}".replace(",", "\u202f")
    bits = [f"Audio reçu : {fr(n)} paquets, {fr(dec)} décodés"]
    for key, label in (("undecryptable", "chiffrés (DAVE) illisibles"), ("unmapped", "sans membre associé"),
                       ("opus_errors", "erreurs de décodage"), ("bad_rtp", "illisibles (transport)")):
        k = int(st.get(key) or 0)
        if k:
            bits.append(f"{fr(k)} {label}")
    status = str((listen or {}).get("status") or "")
    engine = str((listen or {}).get("engine") or "")
    if engine:
        bits.append(f"transcription : {engine}")
    return " · ".join(bits) + (f"\n{status}" if status else "")


def friend_rows(snap: dict, links: dict | None = None) -> list[dict]:
    """The "Dans le vocal" rows: ``{id, title, desc, listen, tone, choices, value, editable}`` per voice
    member: who plays (either team: « joue Lux (support) », « joue en face : Darius (top) ») and who does
    not. ``listen``: his live « Écoute » line (level, last words) while the bot listens. ``choices``:
    (Riot ID / "" auto / "-" absent, label) for the hand-made fix (only during a game). Pure (no Qt)."""
    from treeaicoach import discord_friends as df  # noqa: PLC0415

    links = dict(links or {})
    roster = [p for p in snap.get("roster") or [] if isinstance(p, dict)]
    in_game = bool(roster)
    others = [p for p in roster if not p.get("me") and p.get("riot")]
    views = dict((snap.get("listen") or {}).get("members") or {})
    rows = []
    for e in snap.get("voice_map") or []:
        p = e.get("player")
        src = str(e.get("source") or "")
        name = str(e.get("name") or "?")
        title = f"{name} : {df.describe(e, in_game)}"
        if p:
            desc = ENEMY_TEXT if p.get("enemy") else SOURCE_TEXT.get(src, "")
        else:
            desc = ("Pseudo pas reconnu parmi les 10 joueurs. Il peut quand même dire « Hey coach » ; s'il joue, "
                    "choisis son champion." if in_game and src != "absent"
                    else (NOT_PLAYING_TEXT if in_game else "Associé à son champion au début de la partie."))
        editable = in_game and src != "toi"
        choices = [("", "Auto"), (df.NOT_PLAYING, "Ne joue pas")] + \
            [(str(x["riot"]), df.player_label(x) + (", en face" if x.get("enemy") else "")) for x in others]
        value = links.get(str(e.get("id")), "")
        if value not in (c[0] for c in choices):
            value = ""
        rows.append({"id": str(e.get("id")), "title": title, "desc": desc,
                     "listen": listen_text(views.get(str(e.get("id")))),
                     "tone": "accent" if p and not p.get("enemy") and src in ("auto", "manuel") else "",
                     "choices": choices, "value": value, "editable": editable})
    return rows


VOICE_LABELS = {"edge": "naturelle", "custom": "naturelle", "onecore": "Windows", "sapi": "Windows",
                "clip": "enregistrée"}


def voice_line(snap: dict) -> tuple[str, bool | None]:
    """("Voix : naturelle" / "Windows" / "enregistrée" / "aucune...", ok) of the bot; ok None = not
    checked yet (the clips are rendered right after the connection)."""
    src = str(snap.get("voice_source") or "")
    if src in VOICE_LABELS:
        return f"Voix : {VOICE_LABELS[src]}", True
    if snap.get("voice_checked"):
        return "Voix : aucune, le bot ne peut pas parler", False
    return "Voix : vérifiée à la connexion", None


def headline(snap: dict) -> tuple[str, str, str]:
    """(big status, detail line, tone) of the state card."""
    st = snap.get("state", "off")
    if st == "connected":
        ch = snap.get("channel")
        lat = snap.get("latency_ms")
        bits = [f"Dans #{ch['name']} ({ch['guild']})" if ch else "Pas dans un salon vocal"]
        if lat is not None:
            bits.append(f"latence {lat} ms")
        voice, ok = voice_line(snap)
        if ok is not None:
            bits.append(voice)
        if snap.get("muted"):
            bits.append("bot coupé (raccourci)")
        return f"Connecté en tant que {snap.get('bot_name') or 'bot'}", " · ".join(bits), "ok" if ok is not False else "warning"
    detail = str(snap.get("status") or "")
    if st in ("error", "unavailable") and snap.get("error") and snap["error"] not in detail:
        detail = f"{detail} Raison : {snap['error']}".strip()
    tone = {"bad_token": "danger", "error": "danger", "unavailable": "danger", "connecting": "warning"}.get(st, "")
    return STATE_TITLES.get(st, "Hors ligne"), detail, tone


def checklist(snap: dict, has_token: bool, has_id: bool) -> list[tuple[str, bool | None, str]]:
    """The five setup steps: (key, done (None = cannot tell yet), description)."""
    st = snap.get("state")
    connected = st == "connected"
    guilds = list(snap.get("guilds") or [])
    user = dict(snap.get("user") or {})
    steps: list[tuple[str, bool | None, str]] = []
    # 1. token
    if not has_token:
        steps.append(("token", False, "Colle le token de ton bot (portail Discord > Bot > Reset Token)."))
    elif st == "bad_token":
        steps.append(("token", False, "Discord refuse ce token : recopie-le (Reset Token)."))
    elif connected:
        steps.append(("token", True, f"Accepté par Discord : le bot s'appelle {snap.get('bot_name') or 'bot'}."))
    elif st == "off":
        steps.append(("token", None, "Active le bot en haut de la page pour le vérifier."))
    else:
        steps.append(("token", None, str(snap.get("status") or "Vérification...")))
    # 2. servers
    if not connected:
        steps.append(("guilds", None, "Visible une fois le bot connecté."))
    elif not guilds:
        steps.append(("guilds", False, "Le bot n'est sur aucun serveur : invite-le."))
    else:
        names = " · ".join(f"{g['name']} ({g['members']} membres)" if g.get("members") else g["name"]
                           for g in guilds[:4])
        more = f" et {len(guilds) - 4} autres" if len(guilds) > 4 else ""
        steps.append(("guilds", True, f"Sur {len(guilds)} serveur{'s' if len(guilds) > 1 else ''} : {names}{more}."))
    # 3. me
    if not has_id:
        steps.append(("me", False, "Ton identifiant Discord, pour que le bot te suive dans ton salon."))
    elif not connected:
        steps.append(("me", None, "Visible une fois le bot connecté."))
    elif user.get("channel_id"):
        steps.append(("me", True, f"{user.get('name') or 'Toi'} : dans #{user['channel']} ({user['guild']})."))
    elif user.get("exists") is False:
        steps.append(("me", False, "Aucun compte Discord avec cet identifiant : recopie-le."))
    else:
        who = user.get("name") or "Trouvé"
        steps.append(("me", False, f"{who}, mais pas dans un salon vocal d'un serveur du bot. Rejoins-en un."))
    # 4. permissions in my channel (else in the bot's channel)
    ch = _find_channel(guilds, user.get("channel_id") or (snap.get("channel") or {}).get("id"))
    if ch is None:
        steps.append(("perms", None, "Vérifié dans ton salon vocal quand tu y es."))
    elif ch.get("can_speak"):
        steps.append(("perms", True, f"Le bot peut se connecter et parler dans #{ch['name']}."))
    else:
        steps.append(("perms", False, f"Dans #{ch['name']}, il manque le droit : {', '.join(ch.get('missing') or [])}. "
                                      "Donne-le au rôle du bot dans les réglages du salon."))
    # 5. voice test
    said = [e for e in snap.get("events") or [] if str(e.get("text", "")).startswith("A dit")]
    if said:
        steps.append(("test", True, f"Dernière annonce à {said[0]['t']} dans #{said[0].get('channel') or '?'}."))
    else:
        steps.append(("test", None, "Le bot rejoint ton salon et parle. Tes amis doivent l'entendre."))
    return steps


def _find_channel(guilds: list[dict], cid: Any) -> dict | None:
    if not cid:
        return None
    for g in guilds:
        for ch in g.get("voice_channels") or []:
            if ch.get("id") == str(cid):
                return ch
    return None


class DiscordPage:
    STEP_TITLES = {"token": "Token du bot", "guilds": "Bot invité sur un serveur", "me": "Ton identifiant Discord",
                   "perms": "Droit de parler dans ton salon", "test": "Test vocal"}

    def __init__(self, app: Any) -> None:
        self.app = app
        W = self.W = app.W
        QtCore, QtWidgets = app.QtCore, app.QtWidgets
        #: where the page reads the bot (tests / screenshots pass a fake)
        self.snapshot_fn: Callable[[], dict] | None = None
        self._guild_id: str | None = None
        self._sig_channels: Any = None
        self._sig_events: Any = None
        self.snap: dict = {}
        self.page = W.Page("Discord", "Le bot annonce les ganks et le matchup dans ton salon vocal, pour toi "
                                      "et tes amis.")

        # -- state card
        top = self.page.section("État")
        card = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(card)
        h.setContentsMargins(16, 14, 16, 14)
        h.setSpacing(14)
        self.avatar = QtWidgets.QLabel("?")
        self.avatar.setFixedSize(44, 44)
        self.avatar.setAlignment(QtCore.Qt.AlignCenter)
        h.addWidget(self.avatar, 0, QtCore.Qt.AlignVCenter)
        v = QtWidgets.QVBoxLayout()
        v.setSpacing(2)
        self.big = W.label("", "headline")
        self.detail = W.label("", "secondary", wrap=True, selectable=True)
        v.addWidget(self.big)
        v.addWidget(self.detail)
        h.addLayout(v, 1)
        self.enable = W.Switch(bool(app.cfg.discord_enabled))
        self.enable.toggled.connect(lambda on: app.set_option("discord_enabled", bool(on)))
        app.bind("discord_enabled", lambda: self.enable.set_quiet(bool(app.cfg.discord_enabled)))
        h.addWidget(self.enable, 0, QtCore.Qt.AlignVCenter)
        top.add(card)

        # -- setup checklist
        setup = self.page.section("Mise en place", DISCORD_HELP)
        self.steps: dict[str, Any] = {}
        tok = QtWidgets.QWidget()
        th = QtWidgets.QHBoxLayout(tok)
        th.setContentsMargins(0, 0, 0, 0)
        th.setSpacing(8)
        self.token = QtWidgets.QLineEdit(str(app.cfg.discord_token or ""))
        self.token.setEchoMode(QtWidgets.QLineEdit.Password)
        self.token.setPlaceholderText("Colle le token ici")
        self.token.setFixedWidth(220)
        self.token.editingFinished.connect(lambda: self._set_token(self.token.text()))
        th.addWidget(self.token)
        th.addWidget(W.button("Coller", self._paste_token))
        app.bind("discord_token", lambda: None if self.token.hasFocus() else self.token.setText(
            str(app.cfg.discord_token or "")))
        self.steps["token"] = setup.add(W.Row(self.STEP_TITLES["token"], "", tok))
        self.invite_btn = W.button("Inviter", self._open_invite, tip="Ouvre la page d'invitation du bot.")
        self.steps["guilds"] = setup.add(W.Row(self.STEP_TITLES["guilds"], "", self.invite_btn))
        self.user_id = QtWidgets.QLineEdit(str(app.cfg.discord_user_id or ""))
        self.user_id.setPlaceholderText("Ex. 123456789012345678")
        self.user_id.setFixedWidth(200)
        self.user_id.setToolTip("Discord > Paramètres > Avancés > Mode développeur, puis clic droit sur ton nom > "
                                "« Copier l'identifiant ».")
        self.user_id.editingFinished.connect(lambda: app.set_option("discord_user_id", self.user_id.text().strip()))
        app.bind("discord_user_id", lambda: None if self.user_id.hasFocus() else self.user_id.setText(
            str(app.cfg.discord_user_id or "")))
        self.steps["me"] = setup.add(W.Row(self.STEP_TITLES["me"], "", self.user_id))
        self.steps["perms"] = setup.add(W.Row(self.STEP_TITLES["perms"], ""))
        tests = QtWidgets.QWidget()
        bh = QtWidgets.QHBoxLayout(tests)
        bh.setContentsMargins(0, 0, 0, 0)
        bh.setSpacing(8)
        self.say_gank = W.button("Gank", lambda: self._say("gank"), tip="Le bot dit « Bot gank ».")
        self.say_matchup = W.button("Matchup", lambda: self._say("matchup"), tip="Le bot dit le matchup.")
        bh.addWidget(self.say_gank)
        bh.addWidget(self.say_matchup)
        self.steps["test"] = setup.add(W.Row(self.STEP_TITLES["test"], "", tests))

        # -- what the bot says, how loud
        on = ("discord_enabled",)
        says = self.page.section("Annonces du bot", "Par défaut : les ganks et le matchup. Le reste est en option.")
        self._says = says
        self.voice_row = says.add(W.Row("Voix du bot", ""))
        app.slider_row(says, "discord_volume", "Volume du bot", "Ce que tes amis entendent.", 0, 100, 5,
                       lambda v: f"{int(v)} %", to_value=lambda v: int(round(v)))
        app.choice_row(says, "discord_route", "Où passe le son de l'app", "Par Discord : ton PC reste muet tant "
                       "que le bot est dans le vocal.",
                       [("discord", "Par Discord"), ("pc", "Sur mon PC"), ("both", "Les deux")], segmented=True,
                       on_change=lambda v: app.set_options(discord_only=v == "discord", discord_route=v))
        app.switch_row(says, "discord_ally_ganks", "Ganks de tes amis", "Quand un ami du salon qui joue avec toi "
                       "se fait gank. Seul le salon l'entend.")
        app.depends(app.switch_row(says, "discord_say_name", "Dire le pseudo", "« Paul, bot gank »."),
                    "discord_ally_ganks")
        app.switch_row(says, "discord_friends_matchup", "Matchup de chaque ami", "Au début : « Paul, Darius contre "
                       "Garen : favorable jusqu'au 6 ».")
        app.switch_row(says, "discord_objectives", "Objectifs", "« Dragon dans 30 secondes », Baron, larves.")
        app.switch_row(says, "discord_team_calls", "Calls d'équipe", "« Ils sont 4 bot : Baron maintenant ».")
        app.switch_row(says, "discord_jungler_seen", "Jungler ennemi vu", "« Jungler ennemi vu top », jamais "
                       "pendant un gank.")
        app.switch_row(says, "discord_summary", "Résumé de fin de partie", "« Victoire en 32 minutes, 9 ganks "
                       "évités sur 10 ».")
        app.switch_row(says, "discord_team_recap", "Bilan d'équipe écrit", "Dans le chat du salon après la partie : "
                       "une force et une chose à corriger par ami. Aucun pseudo enregistré.")
        from treeaicoach.ui_page_settings import hotkey_choices  # noqa: PLC0415

        app.choice_row(says, "hotkey_discord_mute", "Touche pour couper le bot", "Il se tait tout de suite.",
                       hotkey_choices(str(app.cfg.hotkey_discord_mute or "")))

        # -- « Hey coach » heard in the voice channel
        ear = self.page.section("Écoute « Hey coach »", "Reconnaissance sur ton PC (Whisper, gratuit), "
                                "seulement en partie.")
        self._ear = ear
        app.switch_row(ear, "discord_listen", "Écouter le salon vocal", "Toi ou un ami dit « Hey coach » puis sa "
                       "question : le bot répond dans le salon.")
        self._listen_row = app.depends(ear.add(W.Row("Tester l'écoute", LISTEN_TEST_DESC,
                                                     W.button("Tester", self._listen_test))), "discord_listen")
        self._listen_chain = ""
        for sec in (says, ear):
            app.depends(sec, *on)

        # -- who is who in the voice channel
        self._sig_friends: Any = None
        friends = self.page.section("Dans le vocal", "Tes amis du salon, reconnus par leur pseudo. Corrige avec "
                                                     "le menu si besoin.")
        friends.group.hide()
        self._friends_holder = QtWidgets.QWidget()
        self._friends_lay = QtWidgets.QVBoxLayout(self._friends_holder)
        self._friends_lay.setContentsMargins(0, 0, 0, 0)
        self._friends_group: Any = None
        friends.layout().insertWidget(2, self._friends_holder)
        app.bind("discord_links", lambda: self._update_friends(self.snap or {}, force=True))

        # -- where the bot is
        self.leave_btn = W.button("Quitter", self._leave, tip="Le bot quitte le salon vocal.")
        where = self.page.section("Où est le bot", "Il te suit dans ton salon vocal. Tu peux aussi le placer à la "
                                                   "main.", action=self.leave_btn)
        self.server = W.Combo([], None)
        self.server.setMinimumWidth(220)
        self.server.activated.connect(lambda _i: self._pick_server(self.server.value()))
        self.server_row = where.add(W.Row("Serveur", "", self.server))
        self._chan_holder = QtWidgets.QWidget()
        self._chan_lay = QtWidgets.QVBoxLayout(self._chan_holder)
        self._chan_lay.setContentsMargins(0, 0, 0, 0)
        self._chan_group: Any = None
        where.layout().insertWidget(2, self._chan_holder)      # voice channels: under the server row

        # -- log
        logs = self.page.section("Journal du bot", "Les 10 dernières actions du bot.")
        logs.group.hide()
        self._log_holder = QtWidgets.QWidget()
        self._log_lay = QtWidgets.QVBoxLayout(self._log_holder)
        self._log_lay.setContentsMargins(0, 0, 0, 0)
        self._log_group: Any = None
        logs.layout().insertWidget(2, self._log_holder)

        self.timer = QtCore.QTimer(self.page)
        self.timer.timeout.connect(self.tick)
        self.update(self._read())

    # ------------------------------------------------------------------ data
    def _bot(self, create: bool = False) -> Any:
        from treeaicoach import discord_bot  # noqa: PLC0415

        return discord_bot.caller() if create else discord_bot.active()

    def _read(self) -> dict:
        try:
            if self.snapshot_fn is not None:
                return dict(self.snapshot_fn())
            bot = self._bot()
            return bot.snapshot() if bot is not None else offline_snapshot(self.app.cfg)
        except Exception:
            log.debug("discord snapshot failed", exc_info=True)
            return offline_snapshot(self.app.cfg)

    def _sync_bot(self) -> None:
        """Hand the settings to the bot (starts / stops it). Non-blocking."""
        if self.snapshot_fn is not None:
            return
        cfg = self.app.cfg
        try:
            if cfg.discord_enabled or self._bot() is not None:
                self._bot(create=True).configure(cfg)
        except Exception:
            log.debug("discord configure failed", exc_info=True)

    # ------------------------------------------------------------------ actions
    def _set_token(self, text: str) -> None:
        from treeaicoach import discord_bot  # noqa: PLC0415

        self.app.set_option("discord_token", discord_bot.clean_token(text))

    def _paste_token(self) -> None:
        text = self.app.QtGui.QGuiApplication.clipboard().text()
        if text.strip():
            self.token.setText(text.strip())
            self._set_token(text)
            if not self.app.cfg.discord_enabled:
                self.app.set_option("discord_enabled", True)

    def _open_invite(self) -> None:
        from treeaicoach import discord_bot  # noqa: PLC0415

        url = discord_bot.invite_url(discord_bot.application_id_from_token(self.app.cfg.discord_token))
        if url:
            self.app.QtGui.QDesktopServices.openUrl(self.app.QtCore.QUrl(url))

    def _command(self, fn: Callable[[Any], bool], msg: str) -> None:
        bot = self._bot()
        if bot is None or not fn(bot):
            self.app.show_toast("Active le bot et colle son token d'abord.", "warning")
            return
        self.app.show_toast(msg)
        self.app.later(300, self.tick)

    def _say(self, kind: str) -> None:
        self._command(lambda b: b.say_test(kind), "Le bot va parler dans ton salon vocal.")

    def _leave(self) -> None:
        self._command(lambda b: b.leave(), "Le bot quitte le salon vocal.")

    def _join(self, cid: str) -> None:
        self._command(lambda b: b.join(cid), "Le bot rejoint ce salon.")

    def _pick_server(self, gid: Any) -> None:
        self._guild_id = str(gid) if gid else None
        self._sig_channels = None
        self.update(self.snap or self._read())

    # ------------------------------------------------------------------ refresh
    def on_show(self) -> None:
        self._sync_bot()
        self.tick()
        if not self.timer.isActive():
            self.timer.start(POLL_MS)

    def tick(self) -> None:
        app = self.app
        if app._closing or app._current_page != "discord":
            self.timer.stop()
            return
        if app._iconic():
            return
        self.update(self._read())

    def update(self, snap: dict) -> None:
        self.snap = snap
        W = self.W
        title, detail, tone = headline(snap)
        self.big.setText(title)
        W.set_prop(self.big, "tone", tone)
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))
        self._paint_avatar(snap)
        cfg = self.app.cfg
        has_token = bool(str(cfg.discord_token or "").strip())
        from treeaicoach import discord_bot  # noqa: PLC0415

        has_id = bool(discord_bot.clean_user_id(cfg.discord_user_id))
        for key, done, desc in checklist(snap, has_token, has_id):
            row = self.steps[key]
            mark = OK_MARK if done else (BAD_MARK if done is False else WAIT_MARK)
            row.title.setText(f"{mark}  {self.STEP_TITLES[key]}")
            W.set_prop(row.title, "tone", "ok" if done else ("danger" if done is False else ""))
            row.set_desc(desc)
        self.invite_btn.setEnabled(bool(discord_bot.invite_url(discord_bot.application_id_from_token(cfg.discord_token))))
        connected = snap.get("state") == "connected"
        for b in (self.say_gank, self.say_matchup):
            b.setEnabled(connected)
        self.leave_btn.setEnabled(bool(snap.get("channel")))
        voice, ok = voice_line(snap)
        why = "; ".join(f"{k} : {v}" for k, v in dict(snap.get("voice_failures") or {}).items())
        if ok:
            desc = {"Voix : naturelle": "La voix naturelle du coach.",
                    "Voix : Windows": "La voix de Windows : la voix naturelle ne répond pas (Internet ?).",
                    "Voix : enregistrée": "Les annonces enregistrées dans TreeAI : aucune autre voix ne répond "
                                          "(Internet ?). Le matchup ne peut pas être dit."}.get(voice, "")
        elif ok is False:
            desc = f"Aucune voix ne marche sur ce PC{f' ({why})' if why else ''}."
        else:
            desc = "Testée dès que le bot est connecté."
        self.voice_row.title.setText(voice)
        W.set_prop(self.voice_row.title, "tone", "ok" if ok else ("danger" if ok is False else ""))
        self.voice_row.set_desc(desc)
        self._update_servers(snap)
        self._update_friends(snap)
        self._update_log(snap)

    def _paint_avatar(self, snap: dict) -> None:
        t = self.W.theme()
        name = str(snap.get("bot_name") or "")
        on = snap.get("state") == "connected"
        self.avatar.setText((name[:1] or "?").upper())
        bg, fg = (t.accent_soft, t.accent) if on else (t.control, t.secondary)
        css = f"background: {bg}; color: {fg}; border-radius: 22px; font-size: 20px; font-weight: 600;"
        if self.avatar.styleSheet() != css:
            self.avatar.setStyleSheet(css)

    def _update_servers(self, snap: dict) -> None:
        guilds = list(snap.get("guilds") or [])
        user = dict(snap.get("user") or {})
        bot_ch = snap.get("channel") or {}
        ids = [g["id"] for g in guilds]
        if self._guild_id not in ids:
            self._guild_id = user.get("guild_id") if user.get("guild_id") in ids else (
                bot_ch.get("guild_id") if bot_ch.get("guild_id") in ids else (ids[0] if ids else None))
        choices = [(g["id"], g["name"]) for g in guilds]
        if choices != [(self.server.itemData(i), self.server.itemText(i)) for i in range(self.server.count())]:
            self.server.set_choices(choices, self._guild_id)
        elif self.server.value() != self._guild_id and self._guild_id is not None:
            self.server.set_value(self._guild_id)
        self.server.setEnabled(bool(choices))
        g = next((x for x in guilds if x["id"] == self._guild_id), None)
        if g is None:
            self.server_row.set_desc("Le bot n'est sur aucun serveur pour l'instant." if snap.get("state") == "connected"
                                     else "Visible une fois le bot connecté.")
        else:
            here = f"Le bot est dans #{bot_ch['name']}." if bot_ch and bot_ch.get("guild_id") == g["id"] else \
                "Le bot n'est dans aucun salon de ce serveur."
            self.server_row.set_desc(f"{g['members']} membres · {here}" if g.get("members") else here)
        chans = list(g.get("voice_channels") or []) if g else []
        sig = tuple((c["id"], c["name"], c["members"], tuple(c.get("people") or ()), c["can_speak"], c["me_here"],
                     c["bot_here"]) for c in chans)
        if sig == self._sig_channels:
            return
        self._sig_channels = sig
        W = self.W
        grp = W.Group()
        for c in chans:
            bits = [_people(int(c["members"]))]
            if c.get("people"):
                bits[0] += " : " + ", ".join(c["people"][:4]) + ("…" if len(c["people"]) > 4 else "")
            if c["me_here"]:
                bits.append("tu es ici")
            if c["bot_here"]:
                bits.append("le bot est ici")
            if not c["can_speak"]:
                bits.append("il manque : " + ", ".join(c.get("missing") or []))
            btn = W.button("Rejoindre", lambda cid=c["id"]: self._join(cid),
                           "primary" if c["me_here"] and not c["bot_here"] else None)
            btn.setEnabled(bool(c["can_speak"]) and not c["bot_here"])
            row = grp.add(W.Row("#" + c["name"], " · ".join(bits), btn))
            if c["me_here"]:
                W.set_prop(row.title, "tone", "accent")
        if not chans:
            grp.add(W.Row("Aucun salon vocal", "Ce serveur n'a pas de salon vocal visible par le bot." if g else
                          "Connecte le bot pour voir ses serveurs."))
        self._swap(self._chan_lay, "_chan_group", grp)

    def _update_friends(self, snap: dict, force: bool = False) -> None:
        chain = listen_chain_text(snap.get("listen"))
        row = getattr(self, "_listen_row", None)
        if row is not None and chain != getattr(self, "_listen_chain", ""):
            self._listen_chain = chain
            row.set_desc(LISTEN_TEST_DESC + ("\n" + chain if chain else ""))
        links = dict(getattr(self.app.cfg, "discord_links", None) or {})
        rows = friend_rows(snap, links)
        sig = tuple((r["id"], r["title"], r["desc"], tuple(r["choices"]), r["value"], r["editable"]) for r in rows)
        if sig == self._sig_friends and not force:
            self._update_listen(rows)               # live levels: only the description changes
            return
        self._sig_friends = sig
        W = self.W
        grp = W.Group()
        self._friend_widgets: dict[str, Any] = {}
        for r in rows:
            ctl = None
            if r["editable"]:
                ctl = W.Combo(r["choices"], r["value"])
                ctl.setMinimumWidth(180)
                ctl.activated.connect(lambda _i, uid=r["id"], c=ctl: self._link(uid, c.value()))
            row = grp.add(W.Row(r["title"], r["desc"], ctl))
            self._friend_widgets[r["id"]] = (row, "")
            if r["tone"]:
                W.set_prop(row.title, "tone", r["tone"])
        self._update_listen(rows)
        if not rows:
            grp.add(W.Row("Personne", "Les membres de ton salon vocal s'affichent ici quand le bot est connecté."))
        self._swap(self._friends_lay, "_friends_group", grp)

    def _update_listen(self, rows: list[dict]) -> None:
        widgets = getattr(self, "_friend_widgets", {})
        for r in rows:
            row, shown = widgets.get(r["id"], (None, ""))
            if row is None or r["listen"] == shown:
                continue
            row.set_desc(r["desc"] + ("\n" + r["listen"] if r["listen"] else ""))
            widgets[r["id"]] = (row, r["listen"])

    def _listen_test(self) -> None:
        """« Tester : dis « Hey coach » » for the voice channel: 30 s, even outside a game."""
        bot = self._bot()
        if bot is None or not hasattr(bot, "listen_test"):
            self.app.show_toast("Active le bot et colle son token d'abord.", "warning")
            return
        ok, msg = bot.listen_test()
        self.app.show_toast(msg, "info" if ok else "warning")
        self.app.later(300, self.tick)

    def _link(self, uid: str, value: Any) -> None:
        """The hand-made fix of one voice member, kept by Discord id <-> Riot ID in the settings."""
        links = dict(getattr(self.app.cfg, "discord_links", None) or {})
        if value:
            links[str(uid)] = str(value)
        else:
            links.pop(str(uid), None)
        self.app.set_option("discord_links", links)
        self.app.later(300, self.tick)

    def _update_log(self, snap: dict) -> None:
        events = list(snap.get("events") or [])[:10]
        sig = tuple((e.get("t"), e.get("text"), e.get("channel"), e.get("ok")) for e in events)
        if sig == self._sig_events:
            return
        self._sig_events = sig
        W = self.W
        grp = W.Group()
        for e in events:
            where = f" · #{e['channel']}" if e.get("channel") else ""
            row = grp.add(W.Row(str(e.get("text", "")), f"{e.get('t', '')}{where}"))
            if not e.get("ok", True):
                W.set_prop(row.title, "tone", "danger")
        if not events:
            grp.add(W.Row("Rien pour l'instant", "Connexions, salons rejoints, annonces et erreurs s'afficheront ici."))
        self._swap(self._log_lay, "_log_group", grp)

    def _swap(self, lay: Any, attr: str, new: Any) -> None:
        old = getattr(self, attr)
        lay.addWidget(new)
        setattr(self, attr, new)
        if old is not None:
            lay.removeWidget(old)
            old.hide()
            old.deleteLater()

    # ------------------------------------------------------------------ config / close
    def on_config(self, diff: set[str]) -> None:
        if diff & {"discord_enabled", "discord_token", "discord_user_id", "discord_volume", "discord_only",
                   "discord_objectives", "discord_jungler_seen", "discord_summary", "hotkey_discord_mute",
                   "discord_ally_ganks", "discord_say_name", "discord_links", "discord_team_calls",
                   "discord_route", "discord_listen", "discord_friends_matchup", "discord_team_recap"}:
            self._sync_bot()
            self.update(self._read())

    def on_close(self) -> None:
        import sys  # noqa: PLC0415

        self.timer.stop()
        mod = sys.modules.get("treeaicoach.discord_bot")
        if mod is not None:
            mod.shutdown(1.5)                 # leave the voice channel now, not at Discord's timeout


def build(app: Any) -> DiscordPage:
    return DiscordPage(app)
