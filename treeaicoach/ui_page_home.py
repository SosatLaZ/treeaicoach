"""Accueil: the ONE status line + Démarrer, champion select card, level, quick switches, last game,
"Système" rows. Updated by ``CoachApp._refresh_status`` (snapshots only, never blocking)."""

from __future__ import annotations

import logging
from typing import Any

from treeaicoach import ui_kit
from treeaicoach.ui_common import champion_name, fmt_clock, fmt_game_date, game_datetime, game_field, game_result, ui_text

log = logging.getLogger(__name__)


class HomePage:
    def __init__(self, app: Any) -> None:
        self.app = app
        W, QtWidgets, Qt = app.W, app.QtWidgets, app.QtCore.Qt
        self.page = W.Page("Accueil")
        self._sig: Any = None
        self._sys_sig: dict[str, Any] = {}
        self._fix_action = ""

        # --- status hero: title, message, start / stop + fix
        hero = W.card()
        hl = QtWidgets.QGridLayout(hero)
        hl.setContentsMargins(22, 20, 22, 20)
        hl.setHorizontalSpacing(20)
        hl.setVerticalSpacing(4)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(10)
        self.dot = W.Dot()
        row.addWidget(self.dot, 0, Qt.AlignVCenter)
        self.title = W.label("Démarrage…", "headline")
        row.addWidget(self.title, 1)
        hl.addLayout(row, 0, 0)
        self.message = W.label("", "secondary", wrap=True)
        hl.addWidget(self.message, 1, 0)
        self.live = W.label("", "footnote")
        self.live.hide()
        hl.addWidget(self.live, 2, 0)
        btns = QtWidgets.QHBoxLayout()
        btns.setSpacing(8)
        self.fix_btn = W.button("", self._run_fix)
        self.fix_btn.hide()
        btns.addWidget(self.fix_btn)
        self.start_btn = W.button("Démarrer", app.toggle_engine, "primary")
        self.start_btn.setProperty("kind", "primary")
        self.start_btn.setMinimumWidth(120)
        btns.addWidget(self.start_btn)
        hl.addLayout(btns, 0, 1, 2, 1, Qt.AlignRight | Qt.AlignVCenter)
        hl.setColumnStretch(0, 1)
        self.page.add(hero)

        # --- champion select (only while it happens)
        self.cs = W.Section("Sélection des champions")
        self.cs_title = W.label("", "headline")
        self.cs_lines = W.label("", "secondary", wrap=True)
        box = QtWidgets.QWidget()
        bl = QtWidgets.QVBoxLayout(box)
        bl.setContentsMargins(16, 12, 16, 14)
        bl.setSpacing(6)
        bl.addWidget(self.cs_title)
        # the build at a glance, OP.GG style: start items | core items in order | spells | keystone
        self.cs_icons = QtWidgets.QWidget()
        self.cs_icons_lay = QtWidgets.QHBoxLayout(self.cs_icons)
        self.cs_icons_lay.setContentsMargins(0, 4, 0, 4)
        self.cs_icons_lay.setSpacing(4)
        self.cs_icons.hide()
        bl.addWidget(self.cs_icons)
        bl.addWidget(self.cs_lines)
        self.cs.add(box)
        self.cs.hide()
        self.page.add(self.cs)

        # --- players of the game in progress (player_intel.py, off by default): by champion, no names
        self.pl = W.Section("Joueurs", "Infos publiques du client LoL, pour cette partie seulement.")
        pbox = QtWidgets.QWidget()
        pl = QtWidgets.QVBoxLayout(pbox)
        pl.setContentsMargins(16, 12, 16, 14)
        pl.setSpacing(4)
        self.pl_enemies = W.label("", "secondary", wrap=True)
        self.pl_allies = W.label("", "secondary", wrap=True)
        pl.addWidget(W.label("Ennemis", "headline"))
        pl.addWidget(self.pl_enemies)
        pl.addWidget(W.label("Alliés", "headline"))
        pl.addWidget(self.pl_allies)
        self.pl.add(pbox)
        self.pl.hide()
        self._pl_sig: Any = None
        self.page.add(self.pl)

        # --- level
        from treeaicoach.skill import SKILL_HELP, SKILL_LEVELS, normalize  # noqa: PLC0415

        self._skill_help, self._normalize = SKILL_HELP, normalize
        lvl = self.page.section("Ton niveau", "Plus tu es débutant, plus le coach explique, une chose à la fois.")
        self.level = W.Segmented(SKILL_LEVELS, normalize(app.cfg.skill_level))
        self.level.changed.connect(app.apply_skill_level)
        self.level_row = lvl.add(W.Row("Niveau d'aide", SKILL_HELP.get(normalize(app.cfg.skill_level), ""),
                                       self.level))
        app.bind("skill_level", self._sync_level)

        # --- quick switches
        quick = self.page.section("En jeu")
        self.voice_sw = W.Switch(True)
        self.voice_sw.toggled.connect(lambda on: app.set_muted(not on))
        quick.add(W.Row("Voix du coach", f"Coupe ou rétablis la voix ({self._key('hotkey_mute')} en jeu).",
                        self.voice_sw))
        app.switch_row(quick, "overlay_enabled", "Overlay", "Marques sur la minimap et panneau de consignes.")
        app.switch_row(quick, "safe_mode", "Mode sûr", "Plus d'alertes de gank ni de suivi du jungler "
                                                       "(Ctrl+Maj+S). Pour une partie tranquille.")
        app.switch_row(quick, "player_intel", "Infos des joueurs (rang, séries)",
                       "Rang, LP et séries des joueurs de ta partie, lus dans le client LoL. Gardé le temps "
                       "de la partie, jamais enregistré.")

        # --- try it
        tri = self.page.section("Essayer sans jouer")
        tri.add(W.Row("Tester la voix", "Le coach dit une phrase d'exemple.", W.button("Écouter", app.test_voice)))
        tri.add(W.Row("Tester l'overlay", "Un exemple de gank s'affiche 10 s sur ton écran.",
                      W.button("Afficher", app.test_overlay)))
        self.demo_btn = W.button("Lancer", app.toggle_demo)
        tri.add(W.Row("Partie de démonstration", "Partie simulée : le jungler ennemi vient te ganker vers 40 s.",
                      self.demo_btn))

        # --- last game
        last = self.page.section("Dernière partie")
        self.last_row = last.add(W.Row("Aucune partie enregistrée",
                                       "Joue une partie avec l'analyse active : le rapport apparaît ici.",
                                       W.button("Rapport", app.open_last_report)))
        self.last_row.control.setEnabled(False)

        # --- system
        self.sys = self.page.section("Système", "Chaque ligne dit ce qui marche ; un lien corrige ce qui ne marche pas.")
        self.sys_rows: dict[str, Any] = {}

    # ------------------------------------------------------------------ helpers
    def _key(self, field: str) -> str:
        return str(getattr(self.app.cfg, field, "") or "").replace("+", " + ") or "touche désactivée"

    def _sync_level(self) -> None:
        lv = self._normalize(self.app.cfg.skill_level)
        self.level.set_value(lv)
        self.level_row.set_desc(self._skill_help.get(lv, ""))

    def sync_quick(self) -> None:
        self.voice_sw.set_quiet(not self.app._is_muted())

    def _run_fix(self) -> None:
        run_fix(self.app, self._fix_action)

    def on_show(self) -> None:
        self.app._refresh_status()

    def on_games(self, games: list[dict]) -> None:
        r = self.last_row
        if not games:
            r.title.setText("Aucune partie enregistrée")
            r.set_desc("Joue une partie avec l'analyse active : le rapport apparaît ici.")
            r.control.setEnabled(False)
            return
        g = games[0]
        champ = champion_name(game_field(g, "champion", "alias", default="")) or "Partie"
        res = {"win": "Victoire", "lose": "Défaite"}.get(game_result(g) or "", "")
        r.title.setText(f"{champ}" + (f" · {res}" if res else ""))
        r.set_desc(fmt_game_date(game_datetime(g)))
        r.control.setEnabled(True)

    # ------------------------------------------------------------------ status
    def update_status(self, snap: dict[str, Any]) -> None:
        app, W = self.app, self.app.W
        key = snap["key"]
        level = {"RUNNING": 0, "WAITING_GAME": 0, "LOCATING": 0, "STARTING": -1, "STOPPED": -1,
                 "CAPTURE_BLACK": 1, "UNSUPPORTED_MODE": 1}.get(key, 2)
        running = snap["running"]
        st = snap["status"]
        live = ""
        if key == "RUNNING" and st is not None:
            gt = getattr(st, "game_time", None)
            parts = [f"Partie {fmt_clock(gt)}" if gt is not None else "",
                     f"{float(getattr(st, 'fps', 0) or 0):.0f} img/s",
                     f"{int(getattr(st, 'enemies_visible', 0) or 0)} ennemis visibles"]
            alert = str(getattr(st, "last_alert", "") or "")
            if alert:
                parts.append(f"Dernière alerte : {ui_text(alert)}")
            live = " · ".join(p for p in parts if p)
        start_text = ("Arrêt…" if running else "Démarrage…") if app._busy else ("Arrêter" if running else "Démarrer")
        sig = (snap["title"], snap["message"], snap["fix"], level, live, start_text, app.demo)
        if sig != self._sig:
            self._sig = sig
            self.dot.set_level(level)
            self.title.setText(ui_text(snap["title"]))
            self.message.setText(ui_text(snap["message"]))
            self.live.setText(live)
            self.live.setVisible(bool(live))
            self._fix_action = snap["action"]
            self.fix_btn.setText(snap["fix"])
            self.fix_btn.setVisible(bool(snap["fix"] and snap["action"]))
            self.start_btn.setText(start_text)
            self.start_btn.setEnabled(not app._busy)
            W.set_prop(self.start_btn, "kind", "secondary" if running else "primary")
            self.demo_btn.setText("Arrêter" if app.demo else "Lancer")
        self.sync_quick()
        self._update_players(snap)
        self._update_system(snap)

    def _update_players(self, snap: dict[str, Any]) -> None:
        """The "Joueurs" card during a game when "Infos des joueurs" is on (lock-only read, no I/O)."""
        lines: Any = None
        try:
            from treeaicoach import player_intel  # noqa: PLC0415

            if snap.get("key") == "RUNNING" and not self.app.demo and player_intel.enabled(self.app.cfg):
                players = player_intel.get_default_watcher().snapshot()
                lines = player_intel.card(players) if players else (("Lecture du client LoL…",), ())
        except Exception:
            log.debug("players card failed", exc_info=True)
        if lines == self._pl_sig:
            return
        self._pl_sig = lines
        if lines is None:
            self.pl.hide()
            return
        enemies, allies = lines
        self.pl_enemies.setText("\n".join(ui_text(x) for x in enemies) or "Aucune info")
        self.pl_allies.setText("\n".join(ui_text(x) for x in allies) or "Aucune info")
        self.pl.show()

    def _update_system(self, snap: dict[str, Any]) -> None:
        app, W = self.app, self.app.W
        st = snap["status"]
        import time  # noqa: PLC0415

        now = time.monotonic()
        if now - app._lcu_polled > 30.0 and app._current_page == "home":
            app._lcu_polled = now
            app.refresh_lcu(lambda _t: None)
        det = str(getattr(st, "detector", "") or getattr(app._detector, "name", "") or "")
        vb = str(getattr(app.voice, "backend", "") or getattr(st, "voice", "") or "")
        data = ui_kit.subsystem_rows(
            state=snap["key"], message=str(getattr(st, "message", "") or ""), running=snap["running"],
            demo=app.demo, minimap_found=getattr(st, "minimap_rect", None) is not None,
            minimap_method=getattr(st, "locate_method", None), detector=det, voice_backend=vb,
            muted=app._is_muted(), lcu_text=app._lcu_text, lcu_enabled=bool(getattr(app.cfg, "lcu_enabled", True)),
            engine_ok=app.engine is not None or app._busy, ai_provider=str(getattr(app.cfg, "ai_provider", "off")),
            ai_key_set=bool(str(getattr(app.cfg, "ai_api_key", "") or "").strip()), ai_budget=app._ai_budget(),
            ai_test=None if app._ai_test_busy else app._ai_test,
            capture=_capture_why(getattr(st, "health", None)))
        for k, label, level, text, fix, action in data:
            row = self.sys_rows.get(k)
            if row is None:
                row = self._add_sys_row(k, label)
            sig = (level, text, fix, action)
            if self._sys_sig.get(k) == sig:
                continue
            self._sys_sig[k] = sig
            row["dot"].set_level(level)
            row["val"].setText(ui_text(text))
            row["action"] = action
            row["btn"].setText(fix)
            row["btn"].setVisible(bool(fix and action))
            W.set_prop(row["val"], "tone", {1: "warning", 2: "danger"}.get(level, ""))

    def _add_sys_row(self, key: str, label: str) -> dict[str, Any]:
        app = self.app
        W, QtWidgets, Qt = app.W, app.QtWidgets, app.QtCore.Qt
        w = QtWidgets.QWidget()
        w.setMinimumHeight(44)
        lay = QtWidgets.QHBoxLayout(w)
        lay.setContentsMargins(16, 8, 16, 8)
        lay.setSpacing(10)
        dot = W.Dot()
        lay.addWidget(dot, 0, Qt.AlignVCenter)
        name = W.label(label)
        name.setMinimumWidth(130)
        lay.addWidget(name)
        val = W.label("", "value", wrap=True)
        lay.addWidget(val, 1)
        rec: dict[str, Any] = {"dot": dot, "val": val, "action": ""}
        btn = W.button("", lambda: run_fix(app, rec["action"]), "link")
        btn.hide()
        lay.addWidget(btn)
        rec["btn"] = btn
        self.sys.add(w)
        self.sys_rows[key] = rec
        return rec

    def show_champ_select(self, title: str, lines: tuple[str, ...] | None, card: Any = None) -> None:
        if lines is None:
            self.cs.hide()
            return
        self.cs_title.setText(ui_text(title))
        self.cs_lines.setText("\n".join(ui_text(x) for x in lines[:8]))
        self._fill_icons(card)
        self.cs.show()

    ICON_PX = 34

    def _fill_icons(self, card: Any) -> None:
        """Icon row of the champion select card: start items, core items (arrows between them), the
        two summoner spells, the keystone + secondary tree. Missing icons are skipped. Never raises."""
        lay = self.cs_icons_lay
        try:
            while lay.count():
                it = lay.takeAt(0)
                w = it.widget() if it is not None else None
                if w is not None:
                    w.hide()                 # deleteLater alone leaves it drawn until the event loop runs
                    w.setParent(None)
                    w.deleteLater()
            if card is None:
                self.cs_icons.hide()
                return
            groups = [[("item", i, n) for i, n, _g in getattr(card, "start_items", ()) or ()],
                      [("item", i, "") for i in getattr(card, "core_ids", ()) or ()],
                      [("spell", i, "") for i in getattr(card, "spell_ids", ()) or ()],
                      [("perk", i, "") for i in getattr(card, "rune_ids", ()) or ()]]
            n = 0
            for gi, group in enumerate(groups):
                shown = [w for w in (self._icon(k, i, tip) for k, i, tip in group) if w is not None]
                if not shown:
                    continue
                if n:
                    lay.addSpacing(14)
                for j, w in enumerate(shown):
                    if gi == 1 and j:
                        lay.addWidget(self.app.W.label("›", "secondary"))
                    lay.addWidget(w)
                    n += 1
            lay.addStretch(1)
            self.cs_icons.setVisible(n > 0)
        except Exception:
            log.debug("champion select icons failed", exc_info=True)
            self.cs_icons.hide()

    def _icon(self, kind: str, key: Any, tip: str = "") -> Any:
        from treeaicoach import item_icons  # noqa: PLC0415

        data = item_icons.png_bytes(kind, key)
        if not data:
            return None
        QtGui, QtWidgets, Qt = self.app.QtGui, self.app.QtWidgets, self.app.QtCore.Qt
        pm = QtGui.QPixmap()
        if not pm.loadFromData(data):
            return None
        px = self.ICON_PX if kind != "perk" else self.ICON_PX - 4
        lab = QtWidgets.QLabel()
        lab.setPixmap(pm.scaled(px, px, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        lab.setFixedSize(px, px)
        if not tip and kind == "item":
            try:
                from treeaicoach import game_data  # noqa: PLC0415

                tip = game_data.item_name(int(key), "")
            except Exception:
                tip = ""
        if tip:
            lab.setToolTip(tip)
        return lab


def _capture_why(health: Any) -> str:
    try:
        from treeaicoach.capture import capture_why

        return capture_why(health.get("capture")) if isinstance(health, dict) else ""
    except Exception:
        return ""


def run_fix(app: Any, action: str) -> None:
    """One-click fix of the status line / of a "Système" row, by action name."""
    if action == "start":
        app.toggle_engine()
    elif action == "calibrate":
        app.calibrate()
    elif action == "help_borderless":
        app.show_page("about")
        app.show_toast("Dans le jeu : Options > Vidéo > Mode d'affichage : Sans bordure.", "warning")
    elif action == "relocate":
        app.relocate()
    elif action == "diagnostic":
        app.copy_diagnostic()
    elif action == "settings_ia":
        app.show_page("settings")
    elif action == "settings_ai":
        app.show_page("settings")
    elif action == "test_ai":
        app.test_ai_key()
    elif action == "voice":
        app.test_voice()
    elif action == "unmute":
        app.toggle_mute()
    elif action == "voice_settings":
        app.show_page("alerts")
        app.show_toast("Aucune voix Windows trouvée : choisis la voix neurale (Internet) ou installe une voix "
                       "française (Paramètres Windows > Heure et langue > Voix).", "warning")
    elif action == "lcu_help":
        app.show_toast("Laisse le client League of Legends ouvert : après la partie, TreeAI y lit tes vraies "
                       "stats (lecture seule). Rien à régler.")


def build(app: Any) -> HomePage:
    return HomePage(app)
