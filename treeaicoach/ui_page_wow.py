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


OK_MARK, BAD_MARK, WAIT_MARK = "✓", "✗", "·"
STEP_TITLES = {"folder": "Dossier de WoW", "addon": "Addon TreeAI", "log": "Journal de combat"}


def overview(st: dict, enabled: bool = True) -> dict:
    """What the « État » card shows, from :func:`read_state` (pure, tests).

    ``headline`` / ``tone``: the verdict (« Tout est prêt », « Addon à installer »…); ``detail``: is WoW
    running; ``steps``: [(key, done True / False / None, text)] for the folder, the addon and the combat
    log; ``button`` / ``can_install``: the install button."""
    st = st or {}
    exe, mode = st.get("wow_exe"), st.get("mode") or {}
    if not st:
        detail = "Recherche de WoW…"
    elif not enabled:
        detail = "Désactivé : TreeAI ignore WoW."
    elif st.get("lol"):
        detail = "Partie de League en cours : le coach LoL a la priorité."
    elif exe and mode.get("active"):
        detail = "WoW lancé : coach actif."
    elif exe:
        detail = "WoW lancé. Le coach démarre avec l'analyse."
    else:
        detail = "WoW n'est pas lancé."

    inst = st.get("install")
    has_src = st.get("has_src", True)
    installed = bool(st.get("installed"))
    src_v, inst_v = st.get("src_version") or "", st.get("inst_version") or ""
    steps: list[tuple[str, Any, str]] = []
    if not st:
        steps = [(k, None, "") for k in STEP_TITLES]
    else:
        if inst is None:
            steps.append(("folder", False, "Introuvable. Choisis le dossier « World of Warcraft »."))
        else:
            steps.append(("folder", True, f"{inst.flavor_dir} ({inst.source})"))
        if not has_src:
            steps.append(("addon", False, "Absent de cette version de TreeAI."))
        elif not installed:
            steps.append(("addon", False if inst is not None else None, "Pas encore installé."))
        elif st.get("needs_update"):
            steps.append(("addon", None, f"Mise à jour disponible : {inst_v or '?'} → {src_v or '?'}."))
        else:
            steps.append(("addon", True, f"Version {inst_v}, à jour." if inst_v else "Installé et à jour."))
        log_tone, log_text = st.get("log") or ("", "")
        steps.append(("log", True if log_tone == "ok" else None,
                      log_text or "Tape /combatlog en jeu (fichier Logs/WoWCombatLog.txt)."))

    if not st:
        headline, tone = "WoW Forever", ""
    elif not enabled:
        headline, tone = "Mode WoW désactivé", ""
    elif inst is None:
        headline, tone = "WoW introuvable", "danger"
    elif not has_src:
        headline, tone = "Addon manquant", "danger"
    elif not installed:
        headline, tone = "Addon à installer", "warning"
    elif st.get("needs_update"):
        headline, tone = "Mise à jour de l'addon", "warning"
    elif steps[-1][1] is not True and exe:
        headline, tone = "Presque prêt : active /combatlog", "warning"
    else:
        headline, tone = "Tout est prêt", "ok"
    button = ("Mettre à jour l'addon" if st.get("needs_update") else "Réinstaller l'addon") if installed \
        else "Installer l'addon"
    return {"headline": headline, "tone": tone, "detail": detail, "steps": steps, "button": button,
            "can_install": bool(inst is not None and has_src), "found": inst is not None}


class WowPage:
    def __init__(self, app: Any) -> None:
        self.app = app
        W = self.W = app.W
        QtCore, QtWidgets = app.QtCore, app.QtWidgets
        Qt = QtCore.Qt
        self.state: dict = {}
        self._busy = False
        self.page = W.Page("WoW Forever", "Addon en jeu, macros et coach. S'active seul quand WoW est lancé.")

        # -- état : verdict + is WoW running (+ the master switch), the three checks, the install button
        top = self.page.section("État", "L'addon s'installe dans Interface/AddOns/TreeAI. En jeu : /tai.")
        self.state_row = app.switch_row(top, "wow_enabled", "WoW Forever", "Recherche de WoW…")
        self.enable = self.state_row.switch
        self.enable.setToolTip("Mode WoW : TreeAI suit WoW quand il est lancé (et que League ne l'est pas).")
        W.set_prop(self.state_row.title, "role", "headline")
        self.steps: dict[str, Any] = {}
        for key, title in STEP_TITLES.items():
            self.steps[key] = top.add(W.Row(f"{WAIT_MARK}  {title}", ""))
        self.pick_btn = W.button("Choisir", self._browse, tip="Choisis le dossier « World of Warcraft ».")
        self.steps["folder"]._grid.addWidget(self.pick_btn, 0, 1, 2, 1, Qt.AlignRight | Qt.AlignVCenter)
        self.pick_btn.hide()
        act = QtWidgets.QWidget()
        al = QtWidgets.QHBoxLayout(act)
        al.setContentsMargins(16, 12, 16, 14)
        al.setSpacing(12)
        self.install_btn = W.button("Installer l'addon", self._install, kind="primary")
        self.install_btn.setMinimumHeight(40)
        self.install_btn.setMinimumWidth(240)
        al.addWidget(self.install_btn, 0)
        self.install_hint = W.label("Puis /reload en jeu.", "secondary")
        al.addWidget(self.install_hint, 0, Qt.AlignVCenter)
        al.addStretch(1)
        top.add(act)

        # -- personnage (SavedVariables) and last fight (combat log)
        self.char_section = self.page.section("Personnage", "Lu dans les sauvegardes de l'addon : à jour après "
                                                            "/reload ou déconnexion.")
        self._char_holder = QtWidgets.QWidget()
        self._char_layout = QtWidgets.QVBoxLayout(self._char_holder)
        self._char_layout.setContentsMargins(0, 0, 0, 0)
        self.char_section.add(self._char_holder)
        self._char_group: Any = None
        self._char_sig: Any = None
        fight = self.page.section("Dernier combat", "Lu dans le journal de combat. Sert aussi au coach.")
        self.report_row = fight.add(W.Row("Rapport", "Pas encore de combat lu."))
        self.report_row.desc.setTextInteractionFlags(Qt.TextSelectableByMouse)

        # -- coach switches
        coach = self.page.section("Coach", "La voix répond quand tu dis « Hey coach ».")
        self.log_row = app.switch_row(coach, "wow_combatlog", "Lire le journal de combat",
                                      "Rapport de combat et contexte du coach.")
        self.clog = self.log_row.switch
        self.pvp = app.switch_row(coach, "wow_pvp_help", "Aide JcJ",
                                  "Conseils JcJ dans les réponses. En jeu : /tai pvp.").switch
        self.voice = app.switch_row(coach, "wow_voice", "Annonces vocales",
                                    "La voix parle d'elle-même en combat. Désactivé par défaut.").switch

        # -- avancé : the folder override, auto update, Blizzard rules
        more = self.page.fold("Avancé", "dossier, mise à jour, règles")
        inst = more.section("Installation")
        path_box = QtWidgets.QWidget()
        ph = QtWidgets.QHBoxLayout(path_box)
        ph.setContentsMargins(0, 0, 0, 0)
        ph.setSpacing(8)
        self.path = QtWidgets.QLineEdit(str(getattr(app.cfg, "wow_path", "") or ""))
        self.path.setPlaceholderText("Automatique")
        self.path.setMinimumWidth(160)
        self.path.setMaximumWidth(260)
        self.path.editingFinished.connect(lambda: self._set_path(self.path.text()))
        ph.addWidget(self.path, 1)
        ph.addWidget(W.button("Parcourir", self._browse, tip="Choisis le dossier « World of Warcraft »."))
        app.bind("wow_path", lambda: None if self.path.hasFocus() else self.path.setText(str(app.cfg.wow_path or "")))
        self.folder_row = inst.add(W.Row("Dossier de WoW", "Vide : trouvé tout seul.", path_box))
        self.auto = app.switch_row(inst, "wow_addon_auto_update", "Mise à jour automatique",
                                   "L'addon est mis à jour quand WoW est détecté.").switch
        rules = more.section("Règles de Blizzard")
        rules.add(W.Row("Ce que TreeAI fait (et ne fait pas)", RULES_TEXT))
        self.more = more

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
        ov = overview(st, bool(getattr(self.app.cfg, "wow_enabled", True)))
        self.state_row.title.setText(ov["headline"])
        W.set_prop(self.state_row.title, "tone", ov["tone"])
        self.state_row.set_desc(ov["detail"])
        for key, done, text in ov["steps"]:
            row = self.steps[key]
            mark = OK_MARK if done else (BAD_MARK if done is False else WAIT_MARK)
            row.title.setText(f"{mark}  {STEP_TITLES[key]}")
            W.set_prop(row.title, "tone", "ok" if done else ("danger" if done is False else ""))
            row.set_desc(text)
        self.pick_btn.setVisible(bool(st) and not ov["found"])
        self.install_btn.setText(ov["button"])
        self.install_btn.setEnabled(ov["can_install"])
        self.install_hint.setText("Puis /reload en jeu." if ov["found"] else "Indique d'abord le dossier de WoW.")
        mode = st.get("mode") or {}
        report = str(mode.get("report") or "").strip()
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
        rest = list(rows[:12])
        if rest and rest[0][0] == "Personnage":         # the character as the card's title
            name = str(rest.pop(0)[1])
            zone = next((str(v) for t, v in rest if t == "Zone"), "")
            rest = [(t, v) for t, v in rest if t != "Zone"]
            head = grp.add(W.Row(name, zone or None))
            W.set_prop(head.title, "role", "headline")
        for title, value in rest:
            if value:
                grp.add(W.Row(str(title), None, W.label(str(value), "secondary")))
            else:
                grp.add(W.Row(str(title)))
        if not rows:
            grp.add(W.Row("Rien pour l'instant", "Installe l'addon, connecte-toi puis tape /reload."))
        elif sig[1]:
            grp.add(W.Row("Dernière sauvegarde", None, W.label(sig[1], "secondary")))
        self._char_layout.addWidget(grp)
        self._char_group = grp

    def on_close(self) -> None:
        self.timer.stop()


def build(app: Any) -> WowPage:
    return WowPage(app)
