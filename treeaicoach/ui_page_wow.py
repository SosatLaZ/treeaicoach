"""WoW Forever: the World of Warcraft helper made visible. Is WoW running, where it is installed, the
TreeAI addon (install / update), the combat log (/combatlog), the character seen by the addon
(SavedVariables) and the WoW switches.

All file work (finding WoW, reading SavedVariables, copying the addon) runs on a worker thread
(``app.run_job``); the page polls every ``POLL_MS`` while shown. Nothing here touches the game process:
only its process name and its own files (Blizzard rules: no memory, no screen, no keys)."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

POLL_MS = 3000
RULES_TEXT = ("TreeAI ne lit ni la mémoire ni l'écran de WoW et n'appuie sur aucune touche. Il lit seulement "
              "les fichiers du jeu : le journal de combat (/combatlog) et les sauvegardes de l'addon "
              "(écrites à /reload ou à la déconnexion). Les macros respectent les règles de Blizzard : "
              "un sort par touche.")
CLASS_FR = {"WARRIOR": "Guerrier", "PALADIN": "Paladin", "HUNTER": "Chasseur", "ROGUE": "Voleur",
            "PRIEST": "Prêtre", "SHAMAN": "Chaman", "MAGE": "Mage", "WARLOCK": "Démoniste", "DRUID": "Druide"}


def _money(copper: Any) -> str:
    try:
        c = int(copper)
    except (TypeError, ValueError):
        return ""
    g, s, cu = c // 10000, (c // 100) % 100, c % 100
    return f"{g} po {s} pa {cu} pc" if g else (f"{s} pa {cu} pc" if s else f"{cu} pc")


def _pick_char(db: dict) -> dict:
    """The character block of the addon's database: the most recently saved one when there are several."""
    if not isinstance(db, dict):
        return {}
    for key in ("char", "player", "me"):
        if isinstance(db.get(key), dict):
            return db[key]
    chars = db.get("chars") or db.get("characters")
    if isinstance(chars, dict) and chars:
        # addon schema v1 (wow_addon/README.md): chars["Nom-Royaume"].summary = {class, level, zone, ...}
        blocks = [v.get("summary") if isinstance(v.get("summary"), dict) else v
                  for v in chars.values() if isinstance(v, dict)]

        def stamp(b: dict) -> float:
            v = b.get("updated") or b.get("savedAt") or b.get("time") or b.get("t") or 0
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0

        if blocks:
            return max(blocks, key=stamp)
    return db if "level" in db else {}


def basic_summary(db: dict) -> list[tuple[str, str]]:
    """[(title, value)] from TreeAIDB without the leveling module (class, level, zone, gold, bags). Pure."""
    ch = _pick_char(db)
    if not ch:
        return []
    rows: list[tuple[str, str]] = []
    cls = str(ch.get("class") or ch.get("classFile") or "")
    lvl = ch.get("level")
    if cls or lvl:
        rows.append(("Personnage", " ".join(x for x in (str(ch.get("className") or "")
                                                        or CLASS_FR.get(cls.upper(), cls.title()),
                                                        f"niveau {lvl}" if lvl else "") if x)))
    zone = ch.get("zone") or ch.get("zoneText")
    if zone:
        sub = ch.get("subzone") or ch.get("subZone")
        rows.append(("Zone", f"{zone}{(' · ' + str(sub)) if sub else ''}"))
    if ch.get("money") is not None:
        rows.append(("Or", _money(ch.get("money"))))
    bags = ch.get("bags")
    if isinstance(bags, dict) and bags.get("total"):
        rows.append(("Sacs", f"{bags.get('free', '?')} places libres sur {bags.get('total')}"))
    elif ch.get("bagsTotal"):
        rows.append(("Sacs", f"{ch.get('bagsFree', '?')} places libres sur {ch.get('bagsTotal')}"))
    if ch.get("xpMax"):
        try:
            pct = int(100 * float(ch.get("xp") or 0) / float(ch["xpMax"]))
            rested = " · reposé" if ch.get("rested") else ""
            rows.append(("Expérience", f"{pct} %{rested}"))
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    return rows


def summary_rows(db: dict) -> list[tuple[str, str]]:
    """The leveling module's summary when present (any of dict / list of pairs / text), else basic."""
    from treeaicoach.wow import call  # noqa: PLC0415

    res = call("leveling", "summary", db)
    rows: list[tuple[str, str]] = []
    if isinstance(res, dict):
        rows = [(str(k), str(v)) for k, v in res.items() if v not in (None, "")]
    elif isinstance(res, (list, tuple)):
        for item in res:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                rows.append((str(item[0]), str(item[1])))
            elif item:
                rows.append((str(item), ""))
    elif isinstance(res, str) and res:
        rows = [("Résumé", res)]
    return rows or basic_summary(db)


def read_state(cfg: Any) -> dict:
    """Everything the page shows (worker thread, files only). Never raises."""
    from treeaicoach.wow import optional  # noqa: PLC0415

    out: dict = {"wow_exe": None, "lol": False, "install": None, "src_version": "", "inst_version": "",
                 "needs_update": False, "log": ("", ""), "rows": [], "sv_age": None, "mode": None}
    try:
        process, install, sv = optional("process"), optional("install"), optional("savedvars")
        if process is not None:
            out["wow_exe"] = process.wow_process()
            out["lol"] = process.is_lol_running()
        mode_mod = optional("mode")
        m = mode_mod.current() if mode_mod is not None else None
        out["mode"] = m.snapshot() if m is not None else None
        if install is not None:
            inst = install.find_wow(str(getattr(cfg, "wow_path", "") or ""))
            out["install"] = inst
            src = install.addon_source_dir()
            out["src_version"] = install.addon_version(src) if src is not None else ""
            out["has_src"] = src is not None
            out["inst_version"] = install.installed_version(inst)
            out["installed"] = bool(inst is not None and inst.addon_dir.is_dir())
            out["needs_update"] = install.needs_update(inst, src)
            out["log"] = install.combat_log_status(inst)
            files = install.savedvars_files(inst)
            if files and sv is not None:
                out["rows"] = summary_rows(sv.read_db(files[0]))
                out["sv_age"] = install.file_status(files[0]).get("age_s")
    except Exception:
        log.debug("wow page state failed", exc_info=True)
    return out


def _age_text(age: Any) -> str:
    if not isinstance(age, (int, float)):
        return ""
    if age < 90:
        return "à l'instant"
    if age < 3600:
        return f"il y a {int(age // 60)} min"
    if age < 86400:
        return f"il y a {int(age // 3600)} h"
    return f"il y a {int(age // 86400)} j"


class WowPage:
    def __init__(self, app: Any) -> None:
        self.app = app
        W = self.W = app.W
        QtCore, QtWidgets = app.QtCore, app.QtWidgets
        self.state: dict = {}
        self._busy = False
        self.page = W.Page("WoW Forever", "Le coach pour World of Warcraft: Forever : addon en jeu, macros, "
                                          "annonces vocales. S'active seul quand WoW est lancé.")

        top = self.page.section("État")
        self.enable = W.Switch(bool(getattr(app.cfg, "wow_enabled", True)))
        self.enable.toggled.connect(lambda on: app.set_option("wow_enabled", bool(on)))
        app.bind("wow_enabled", lambda: self.enable.set_quiet(bool(app.cfg.wow_enabled)))
        self.state_row = top.add(W.Row("Mode WoW", "Recherche de WoW…", self.enable))

        addon = self.page.section("Addon TreeAI", "Installé dans Interface/AddOns/TreeAI. En jeu : /tai.")
        path_box = QtWidgets.QWidget()
        ph = QtWidgets.QHBoxLayout(path_box)
        ph.setContentsMargins(0, 0, 0, 0)
        ph.setSpacing(8)
        self.path = QtWidgets.QLineEdit(str(getattr(app.cfg, "wow_path", "") or ""))
        self.path.setPlaceholderText("Automatique")
        self.path.setFixedWidth(220)
        self.path.editingFinished.connect(lambda: self._set_path(self.path.text()))
        ph.addWidget(self.path)
        ph.addWidget(W.button("Parcourir", self._browse, tip="Choisis le dossier « World of Warcraft »."))
        app.bind("wow_path", lambda: None if self.path.hasFocus() else self.path.setText(str(app.cfg.wow_path or "")))
        self.folder_row = addon.add(W.Row("Dossier de WoW", "", path_box))
        self.install_btn = W.button("Installer", self._install, kind="primary")
        self.addon_row = addon.add(W.Row("Addon", "", self.install_btn))
        self.auto = W.Switch(bool(getattr(app.cfg, "wow_addon_auto_update", True)))
        self.auto.toggled.connect(lambda on: app.set_option("wow_addon_auto_update", bool(on)))
        app.bind("wow_addon_auto_update", lambda: self.auto.set_quiet(bool(app.cfg.wow_addon_auto_update)))
        addon.add(W.Row("Mise à jour automatique", "Quand WoW est détecté, l'addon est mis à jour.", self.auto))

        coach = self.page.section("Coach", "La voix répond seulement quand tu dis « Hey coach ».")
        self.clog = W.Switch(bool(getattr(app.cfg, "wow_combatlog", True)))
        self.clog.toggled.connect(lambda on: app.set_option("wow_combatlog", bool(on)))
        app.bind("wow_combatlog", lambda: self.clog.set_quiet(bool(app.cfg.wow_combatlog)))
        self.log_row = coach.add(W.Row("Journal de combat", "", self.clog))
        self.pvp = W.Switch(bool(getattr(app.cfg, "wow_pvp_help", False)))
        self.pvp.toggled.connect(lambda on: app.set_option("wow_pvp_help", bool(on)))
        app.bind("wow_pvp_help", lambda: self.pvp.set_quiet(bool(app.cfg.wow_pvp_help)))
        coach.add(W.Row("Aide JcJ", "Conseils JcJ dans les réponses du coach. En jeu : /tai pvp.", self.pvp))
        self.voice = W.Switch(bool(getattr(app.cfg, "wow_voice", False)))
        self.voice.toggled.connect(lambda on: app.set_option("wow_voice", bool(on)))
        app.bind("wow_voice", lambda: self.voice.set_quiet(bool(app.cfg.wow_voice)))
        coach.add(W.Row("Annonces vocales automatiques (désactivé)",
                        "Désactivé par défaut : la voix ne parle jamais d'elle-même en jeu.", self.voice))

        fight = self.page.section("Dernier combat", "Lu dans le journal de combat. Sert aussi au coach.")
        self.report_row = fight.add(W.Row("Rapport", "Pas encore de combat lu."))
        self.char_section = self.page.section("Personnage", "Lu dans les sauvegardes de l'addon (à jour après "
                                                            "/reload ou déconnexion).")
        self._char_holder = QtWidgets.QWidget()
        self._char_layout = QtWidgets.QVBoxLayout(self._char_holder)
        self._char_layout.setContentsMargins(0, 0, 0, 0)
        self.char_section.add(self._char_holder)
        self._char_group: Any = None
        self._char_sig: Any = None

        rules = self.page.section("Règles de Blizzard")
        rules.add(W.Row("Ce que TreeAI fait (et ne fait pas)", RULES_TEXT))

        self.timer = QtCore.QTimer(self.page)
        self.timer.timeout.connect(self.tick)
        self._render()

    # ------------------------------------------------------------------ actions
    def _set_path(self, text: str) -> None:
        self.app.set_option("wow_path", str(text or "").strip())
        self.refresh()

    def _browse(self) -> None:
        QtWidgets = self.app.QtWidgets
        try:
            start = self.path.text() or "C:\\"
            d = QtWidgets.QFileDialog.getExistingDirectory(self.page, "Dossier World of Warcraft", start)
        except Exception:
            log.debug("folder dialog failed", exc_info=True)
            return
        if d:
            self.path.setText(d)
            self._set_path(d)

    def _install(self) -> None:
        from treeaicoach.wow import optional  # noqa: PLC0415

        install = optional("install")
        if install is None:
            return
        self.install_btn.setEnabled(False)
        path = str(getattr(self.app.cfg, "wow_path", "") or "")

        def job() -> Any:
            return install.install_addon(install.find_wow(path), force=True)

        def done(res: Any) -> None:
            self.install_btn.setEnabled(True)
            self.app.show_toast(str(getattr(res, "message", "")), "info" if getattr(res, "ok", False) else "error")
            self.refresh()

        def failed(_exc: BaseException) -> None:
            self.install_btn.setEnabled(True)

        self.app.run_job(job, done, failed, name="TreeAI-wow-install")

    # ------------------------------------------------------------------ refresh
    def on_show(self) -> None:
        self.refresh()
        if not self.timer.isActive():
            self.timer.start(POLL_MS)

    def tick(self) -> None:
        app = self.app
        if app._closing or app._current_page != "wow":
            self.timer.stop()
            return
        if app._iconic():
            return
        self.refresh()

    def refresh(self) -> None:
        if self._busy:
            return
        self._busy = True
        cfg = self.app.cfg

        def done(state: dict) -> None:
            self._busy = False
            self.state = state or {}
            self._render()

        def failed(_exc: BaseException) -> None:
            self._busy = False

        self.app.run_job(lambda: read_state(cfg), done, failed, name="TreeAI-wow-state")

    def _render(self) -> None:
        W, st = self.W, self.state
        on = bool(getattr(self.app.cfg, "wow_enabled", True))
        exe, mode = st.get("wow_exe"), st.get("mode") or {}
        if not st:
            text, tone = "Recherche de WoW…", ""
        elif not on:
            text, tone = "Désactivé : TreeAI ignore WoW.", ""
        elif st.get("lol"):
            text, tone = "Partie de League en cours : le coach LoL a la priorité.", "warning"
        elif exe and mode.get("active"):
            text, tone = "WoW lancé : coach actif.", "ok"
        elif exe:
            text, tone = "WoW lancé. Le coach démarre avec l'analyse.", "ok"
        else:
            text, tone = "WoW n'est pas lancé.", ""
        self.state_row.set_desc(text)
        W.set_prop(self.state_row.title, "tone", tone)

        inst = st.get("install")
        if inst is None:
            self.folder_row.set_desc("Introuvable. Choisis le dossier « World of Warcraft »." if st else "")
            W.set_prop(self.folder_row.title, "tone", "danger" if st else "")
        else:
            self.folder_row.set_desc(f"{inst.flavor_dir} ({inst.source})")
            W.set_prop(self.folder_row.title, "tone", "ok")
        src_v, inst_v = st.get("src_version") or "", st.get("inst_version") or ""
        if not st.get("has_src", True):
            addon_text, addon_tone = "Addon absent de cette version de TreeAI.", "danger"
        elif not st.get("installed"):
            addon_text, addon_tone = "Pas encore installé.", "warning"
        elif st.get("needs_update"):
            addon_text, addon_tone = f"Mise à jour disponible ({inst_v or '?'} → {src_v or '?'}).", "warning"
        else:
            addon_text, addon_tone = f"Installé et à jour{(' (' + inst_v + ')') if inst_v else ''}.", "ok"
        if not st:
            addon_text, addon_tone = "", ""
        self.addon_row.set_desc(addon_text)
        W.set_prop(self.addon_row.title, "tone", addon_tone)
        self.install_btn.setText("Mettre à jour" if st.get("installed") else "Installer")
        self.install_btn.setEnabled(inst is not None and st.get("has_src", True))

        log_tone, log_text = st.get("log") or ("", "")
        self.log_row.set_desc(log_text or "Lit Logs/WoWCombatLog.txt (tape /combatlog en jeu).")
        W.set_prop(self.log_row.title, "tone", log_tone)
        report = str((mode or {}).get("report") or "").strip()
        self.report_row.set_desc(report[:1200] if report else "Pas encore de combat lu.")
        self._render_char(st.get("rows") or [], st.get("sv_age"))

    def _render_char(self, rows: list, age: Any) -> None:
        sig = (tuple(rows), _age_text(age))
        if sig == self._char_sig:
            return
        self._char_sig = sig
        W = self.W
        if self._char_group is not None:
            self._char_group.setParent(None)
            self._char_group.deleteLater()
        grp = W.Group()
        for title, value in rows[:12]:
            grp.add(W.Row(str(title), str(value)))
        if not rows:
            grp.add(W.Row("Rien pour l'instant", "Installe l'addon, connecte-toi puis tape /reload."))
        elif sig[1]:
            grp.add(W.Row("Dernière sauvegarde", sig[1]))
        self._char_layout.addWidget(grp)
        self._char_group = grp

    def on_close(self) -> None:
        self.timer.stop()


def build(app: Any) -> WowPage:
    return WowPage(app)
