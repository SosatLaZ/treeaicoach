"""WoW Forever: the World of Warcraft helper made visible. Is WoW running, where it is installed, the
TreeAI addon (install / update), the combat log (/combatlog), the character seen by the addon
(SavedVariables) and the WoW switches, plus the live tabs « Direct · Quêtes · Groupe · HV · Session »
fed by :mod:`treeaicoach.wow.live` (file watchers on TreeAIDB, Auctionator's prices, the chat and combat
logs; the banner always says how old the last /reload is).

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


def plan_rows(plan: Any) -> list[tuple[str, str]]:
    """(title, description) per route of a TreeAIPlan dict, in plain words: the route's name (« Le plus rapide »,
    « Tout finir ici », « Avec mes potes »…) + its time; the description says only what is wrong, if anything."""
    rows: list[tuple[str, str]] = []
    try:
        verdicts = {"ok": "OK", "galere": "On va galérer", "long": "Trop long", "impossible": "Pas faisable",
                    "zone": "Changez de zone"}
        routes = plan.get("routes") if isinstance(plan, dict) else None
        routes = list(routes.values()) if isinstance(routes, dict) else (routes or [])
        for r in routes:
            if not isinstance(r, dict):
                continue
            from treeaicoach.wow.planner import routes as R  # noqa: PLC0415

            head = f"{R.route_name(r)} (≈ {max(1, int(r.get('minutes') or 0))} min)"
            rs = r.get("reasons")
            rs = list(rs.values()) if isinstance(rs, dict) else (rs or [])
            v = str(r.get("verdict") or "ok")
            desc = "" if v == "ok" else verdicts.get(v, "?") + (f" : {rs[0]}" if rs else "")
            rows.append((head, desc))
    except Exception:
        log.debug("plan rows failed", exc_info=True)
    return rows


def read_state(cfg: Any, hub: Any = None) -> dict:
    """Everything the page shows (worker thread, files only). Never raises.

    With a :class:`treeaicoach.wow.live.LiveHub` the game files are watched (mtime) and re-parsed only
    when they change; ``out["live"]`` is its snapshot and ``out["events"]`` the changes of this tick."""
    from treeaicoach.wow import optional  # noqa: PLC0415

    out: dict = {"wow_exe": None, "lol": False, "install": None, "src_version": "", "inst_version": "",
                 "needs_update": False, "log": ("", ""), "rows": [], "sv_age": None, "mode": None,
                 "live": {}, "events": []}
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
            if inst is not None:
                try:
                    from treeaicoach.wow.planner import writer  # noqa: PLC0415

                    out["plan"] = writer.read_plan(inst.addons_dir)
                except Exception:
                    log.debug("plan read failed", exc_info=True)
            if hub is not None:
                hub.attach_install(inst)
                out["events"] = hub.poll()
                snap = hub.snapshot()
                out["live"] = snap
                out["rows"] = summary_rows(snap.get("db") or {}) if snap.get("db") else []
                out["sv_age"] = snap.get("sv_age")
                return out
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



# ---------------------------------------------------------------------------------------- live tabs
TABS: tuple[tuple[str, str], ...] = (("direct", "Direct"), ("quetes", "Quêtes"), ("groupe", "Groupe"),
                                     ("hv", "HV"), ("session", "Session"))
_QT: dict = {}


def _qt_classes() -> dict:
    """The page's own small painted widgets (built once, Qt imported lazily): ``Sparkline`` and ``Meter``."""
    if _QT:
        return _QT
    from PySide6 import QtCore, QtGui, QtWidgets  # noqa: PLC0415

    from treeaicoach import ui_widgets as W  # noqa: PLC0415

    def color_of(name: str) -> str:
        t = W.theme()
        return str(getattr(t, name, name))

    class Sparkline(QtWidgets.QWidget):
        """QPainter sparkline: one or more series ([y…] or [(x, y)…], colour = theme name or #hex) on a
        shared scale, last point marked."""

        def __init__(self, series: list, width: int = 168, height: int = 40, tip: str = "") -> None:
            super().__init__()
            self.series = [(list(pts), color) for pts, color in series if pts]
            self.setFixedSize(width, height)
            if tip:
                self.setToolTip(tip)

        @staticmethod
        def xy(pts: list) -> list[tuple[float, float]]:
            out = []
            for i, p in enumerate(pts):
                if isinstance(p, (list, tuple)) and len(p) >= 2:
                    out.append((float(p[0]), float(p[1])))
                else:
                    out.append((float(i), float(p)))
            return out

        def paintEvent(self, _e: Any) -> None:  # noqa: N802
            all_pts = [self.xy(pts) for pts, _c in self.series]
            flat = [pt for pts in all_pts for pt in pts]
            if not flat:
                return
            qp = QtGui.QPainter(self)
            qp.setRenderHint(QtGui.QPainter.Antialiasing)
            x0, x1 = min(p[0] for p in flat), max(p[0] for p in flat)
            y0, y1 = min(p[1] for p in flat), max(p[1] for p in flat)
            if y1 - y0 < 1e-9:
                y0, y1 = y0 - 1, y1 + 1
            if x1 - x0 < 1e-9:
                x0, x1 = x0 - 1, x1 + 1
            pad = 4.0
            w, h = self.width() - 2 * pad, self.height() - 2 * pad
            qp.setPen(QtGui.QPen(QtGui.QColor(color_of("separator")), 1))
            qp.drawLine(QtCore.QPointF(pad, pad + h), QtCore.QPointF(pad + w, pad + h))
            for pts, (_p, color) in zip(all_pts, self.series):
                mapped = [QtCore.QPointF(pad + (x - x0) / (x1 - x0) * w, pad + h - (y - y0) / (y1 - y0) * h)
                          for x, y in pts]
                c = QtGui.QColor(color_of(color))
                path = QtGui.QPainterPath()
                path.moveTo(mapped[0])
                for m in mapped[1:]:
                    path.lineTo(m)
                qp.setPen(QtGui.QPen(c, 1.6))
                qp.setBrush(QtCore.Qt.NoBrush)
                qp.drawPath(path)
                qp.setPen(QtCore.Qt.NoPen)
                qp.setBrush(c)
                qp.drawEllipse(mapped[-1], 2.6, 2.6)
            qp.end()

    class Meter(QtWidgets.QWidget):
        """Thin rounded progress bar (0..100) with the percentage on its right."""

        def __init__(self, pct: Any, width: int = 132) -> None:
            super().__init__()
            try:
                self.pct = max(0, min(100, int(pct or 0)))
            except (TypeError, ValueError):
                self.pct = 0
            self.setFixedSize(width, 18)

        def paintEvent(self, _e: Any) -> None:  # noqa: N802
            qp = QtGui.QPainter(self)
            qp.setRenderHint(QtGui.QPainter.Antialiasing)
            bar_w = self.width() - 44
            qp.setPen(QtCore.Qt.NoPen)
            qp.setBrush(QtGui.QColor(color_of("track_off")))
            qp.drawRoundedRect(QtCore.QRectF(0, 6, bar_w, 6), 3, 3)
            qp.setBrush(QtGui.QColor(color_of("ok" if self.pct >= 100 else "accent")))
            qp.drawRoundedRect(QtCore.QRectF(0, 6, bar_w * self.pct / 100, 6), 3, 3)
            qp.setPen(QtGui.QColor(color_of("secondary")))
            qp.drawText(QtCore.QRectF(bar_w + 4, 0, 40, 18), int(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter),
                        f"{self.pct} %")
            qp.end()

    _QT.update(Sparkline=Sparkline, Meter=Meter)
    return _QT


def _row(title: str, desc: str = "", value: str = "", spark: Any = None, meter: Any = None) -> dict:
    return {"title": str(title), "desc": str(desc or ""), "value": str(value or ""), "spark": spark, "meter": meter}


def _pairs(rows: list) -> list[dict]:
    """(title, value) → a row with the value on the right when short, under the title when long."""
    return [_row(t, "", v) if len(str(v)) < 34 else _row(t, v) for t, v in rows]


def tab_blocks(key: str, live_snap: dict) -> list[dict]:
    """What one tab shows: blocks {title, foot, rows=[{title, desc, value, spark, meter}]} (pure, tests).
    An empty list means « nothing yet » (the page shows :data:`EMPTY_TEXT`)."""
    from treeaicoach.wow import live  # noqa: PLC0415

    snap = live_snap or {}
    blocks: list[dict] = []

    def block(title: str, rows: list, foot: str = "") -> None:
        if rows:
            blocks.append({"title": title, "foot": foot, "rows": rows})

    if key == "direct":
        v = live.direct_view(snap)
        if v["empty"] and not (snap.get("chat") or {}).get("events"):
            return blocks
        rows = [_row(v["title"], v["zone"])]
        if v["xp_series"]:
            rows.append(_row("Courbe de la session", "XP (bleu) et or (vert), par tranche de 5 min",
                             spark=[(v["xp_series"], "accent"), (v["gold_series"], "ok")]))
        block("En direct", rows + _pairs(v["rows"]), v["live_text"])
    elif key == "quetes":
        v = live.quests_view(snap)
        if v["now"]:
            block("À faire maintenant", [_row(v["now"], f"Pourquoi ? {v['why']}" if v["why"] else "")])
        block("Tes quêtes (les meilleures en haut)", [_row(f"{t} — {tag}", why) for t, tag, why in v["route"]], v["head"])
        block("Ton trajet", [_row(("Maintenant" if i == 1 else "Ensuite"), p) for i, p in enumerate(v["plan"], 1)])
        block("En passant", [_row(t) for t in v["tips"]])
        block("À retirer", [_row(f"Retire « {a['title']} »", a["why"]) for a in v["abandon"]],
              "Tu la retires toi-même : journal de quêtes (L) → Abandonner.")
    elif key == "groupe":
        v = live.group_view(snap)
        if v["members"]:
            block("Le groupe", [_row(", ".join(v["members"]),
                                     f"Prochaine mission : {v['next']}" if v["next"] else "")],
                  f"{v['no_addon']} membre(s) sans TreeAI." if v["no_addon"] else "")
        block("Tableau des missions", [_row(e["title"], e["state"], meter=e["pct"]) for e in v["board"]])
        block("Répartition", [_row(t) for t in v["assign"] + v["claims"]])
        block("Voyage", [_row(t) for t in v["travel"]])
        block("Artisans", [_row(t) for t in v["crafts"]])
    elif key == "hv":
        v = live.ah_view(snap)
        rows = []
        for d in v["deals"]:
            label = f"{d['name']} x{d['count']}" if d["count"] > 1 else d["name"]
            if d["kind"] == "vendor":
                desc = f"acheter {live.money(d['buy'])}, revendre au marchand {live.money(d['sell'])} : gain sûr"
            else:
                desc = (f"acheter {live.money(d['buy'])} → revendre {live.money(d['sell'])} · commission "
                        f"{live.money(d['cut'])} · dépôt {live.money(d['deposit'])} (perdu si invendu)")
                if d["atr"]:
                    desc += f" · Auctionator {live.money(d['atr'] * d['count'])}"
            series = []
            if d["ours"]:
                series.append((list(d["ours"]), "accent"))
            if d["atr_series"]:
                series.append((list(d["atr_series"]), "warning"))
            rows.append(_row(label, desc, f"+{live.money(d['net'])} net", spark=series or None))
        block("Meilleures affaires", rows,
              "Net = vente − commission − achat. Courbes : prix TreeAI (bleu), Auctionator (jaune).")
        mine = [_row(m["name"] + (f" x{m['count']}" if m["count"] > 1 else ""),
                     (f"plus bas {live.money(m['lowest'])} · " if m["lowest"] else "") + m["advice"],
                     f"{live.money(m['unit'])}/u") for m in v["mine"]]
        foot = ""
        if not mine and v["recent"]:
            mine = [_row(n, day, f"+{live.money(c)}") for n, c, day in v["recent"]]
            foot = "Ventes réalisées récentes (courrier de l'HV). Ouvre l'HV puis /reload pour tes enchères en cours."
        block("Mes ventes", mine, foot)
        r = v["realized"]
        if r["total"] or r["today"] or r["week"] or r["series"]:
            rr = [_row("Profit réalisé", f"aujourd'hui {live.money(r['today'])} · 7 jours {live.money(r['week'])}",
                       live.money(r["total"]), spark=[(r["series"], "ok")] if len(r["series"]) > 1 else None)]
            rr += [_row(n, "", f"+{live.money(p)}") for n, p in r["top"][:3]]
            block("Bilan", rr, v["budget"])
        block("Marchés minces (prudence)", [_row(t) for t in v["thin"]])
        if blocks and v["info"]:
            first = blocks[0]
            first["foot"] = " ".join(x for x in (first["foot"], v["info"][:1].upper() + v["info"][1:] + ".") if x)
    elif key == "session":
        v = live.session_view(snap)
        block("Récap", _pairs(v["rows"]))
        block("Objectifs", [_row(g["text"], meter=g["pct"]) for g in v["goals"]],
              "Objectif du groupe en jeu : /tai mission objectif niveau 30 (ou quetes 15).")
        block("Classement XP", [_row(n, "", f"{x} XP/h" if x else "") for n, x in v["leaderboard"]])
        block("Évènements", [_row(t) for t in v["events"]])
    return blocks


EMPTY_TEXT = {
    "direct": ("Rien pour l'instant", "Installe l'addon, connecte-toi puis tape /reload (et /chatlog pour le direct)."),
    "quetes": ("Pas encore de quêtes", "Le suivi de l'addon s'enregistre au /reload."),
    "groupe": ("Pas de groupe", "Le tableau des missions apparaît en groupe avec des amis qui ont TreeAI."),
    "hv": ("Pas encore de données d'hôtel des ventes", "Ouvre l'HV, clique « Scanner », puis /reload. "
                                                      "Les prix d'Auctionator sont lus aussi."),
    "session": ("Pas encore de session", "Tape /chatlog en jeu : l'XP, l'or et le butin sont suivis en direct."),
}


def live_toasts(events: list) -> list[str]:
    """Toasts worth showing for the changes of one tick (level up, death, fresh /reload). Pure."""
    out: list[str] = []
    for ev in events or []:
        kind = getattr(ev, "kind", "")
        if kind == "chat":
            for e in getattr(ev, "data", None) or []:
                if e.get("kind") == "level":
                    out.append(f"Niveau {e.get('level')} ! Bravo.")
    return out[-2:]


class WowPage:
    def __init__(self, app: Any) -> None:
        self.app = app
        W = self.W = app.W
        QtCore, QtWidgets = app.QtCore, app.QtWidgets
        Qt = QtCore.Qt
        self.state: dict = {}
        self._snap: dict = {}
        self._busy = False
        self._first_poll = True
        from treeaicoach.wow import optional  # noqa: PLC0415

        live_mod = optional("live")
        self.hub = live_mod.LiveHub() if live_mod is not None else None
        self.page = W.Page("WoW Forever", "Addon en jeu, données en direct, macros et coach. "
                                          "S'active seul quand WoW est lancé.")

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

        # -- live tabs: Direct / Quêtes / Groupe / HV / Session (one segmented bar + a stack)
        bar = QtWidgets.QWidget()
        bl = QtWidgets.QVBoxLayout(bar)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(8)
        self.tabs = W.Segmented(TABS, "direct")
        self.tabs.changed.connect(self._show_tab)
        bl.addWidget(self.tabs, 0, Qt.AlignLeft)
        self.fresh = W.label(live_mod.freshness_text(None) if live_mod else "", "footnote", wrap=True)
        bl.addWidget(self.fresh)
        self.page.add(bar)
        # the tabs live in one column, only the current one shown (a hidden tab takes no height)
        self.stack = QtWidgets.QWidget()
        sl = QtWidgets.QVBoxLayout(self.stack)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)
        self.page.add(self.stack)
        self.tab_pages: dict[str, Any] = {}
        self.tab_layouts: dict[str, Any] = {}
        self._tab_holder: dict[str, Any] = {}
        self._tab_sig: dict[str, Any] = {}
        self._current = "direct"
        self._stale: set[str] = set()
        for key, _title in TABS:
            w = QtWidgets.QWidget()
            lay = QtWidgets.QVBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(24)
            sl.addWidget(w)
            self.tab_pages[key] = w
            self.tab_layouts[key] = lay
            self._tab_holder[key] = None

        # « Direct » also keeps the character card (SavedVariables) and the combat-log report
        direct = self.tab_layouts["direct"]
        self.char_section = W.Section("Personnage", "Lu dans les sauvegardes de l'addon : à jour après "
                                                    "/reload ou déconnexion.")
        self._char_holder = QtWidgets.QWidget()
        self._char_layout = QtWidgets.QVBoxLayout(self._char_holder)
        self._char_layout.setContentsMargins(0, 0, 0, 0)
        self.char_section.add(self._char_holder)
        self._char_group: Any = None
        self._char_sig: Any = None
        direct.addWidget(self.char_section)
        plan = W.Section("Plan de quêtes", "Trois trajets au choix : le plus rapide, tout finir ici, avec tes potes. "
                                            "Choisis-en un en jeu : /tai plan.")
        self._plan_holder = QtWidgets.QWidget()
        self._plan_layout = QtWidgets.QVBoxLayout(self._plan_holder)
        self._plan_layout.setContentsMargins(0, 0, 0, 0)
        plan.add(self._plan_holder)
        self._plan_group: Any = None
        self._plan_sig: Any = None
        pact = QtWidgets.QWidget()
        pl = QtWidgets.QHBoxLayout(pact)
        pl.setContentsMargins(16, 12, 16, 14)
        pl.setSpacing(12)
        self.plan_btn = W.button("Calculer le trajet", self._plan, kind="primary")
        self.plan_btn.setMinimumHeight(36)
        pl.addWidget(self.plan_btn, 0)
        self.plan_hint = W.label("Après /reload en jeu (l'addon exporte tes quêtes).", "secondary")
        self.plan_hint.setWordWrap(True)
        pl.addWidget(self.plan_hint, 1, Qt.AlignVCenter)
        plan.add(pact)
        self._plan_last: Any = None
        direct.addWidget(plan)
        fight = W.Section("Journal de combat", "Lu dans Logs/WoWCombatLog.txt (/combatlog). WoW Forever ne "
                                               "l'écrit pas toujours : l'addon garde alors ses récaps de mort.")
        self.report_row = fight.add(W.Row("Rapport", "Pas encore de combat lu."))
        self.report_row.desc.setTextInteractionFlags(Qt.TextSelectableByMouse)
        direct.addWidget(fight)
        for lay in self.tab_layouts.values():
            lay.addStretch(1)
        self._show_tab("direct")

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

    # ------------------------------------------------------------------ tabs
    def _show_tab(self, key: str) -> None:
        """Show one tab (the others are hidden, so the column takes the current tab's height only)."""
        key = str(key) if str(key) in self.tab_pages else "direct"
        self._current = key
        for k, w in self.tab_pages.items():
            w.setVisible(k == key)
        self.tabs.set_value(key)
        if hasattr(self, "fresh"):                      # built when shown: wrapped text gets its real size
            if key in self._stale:
                self._tab_sig.pop(key, None)
            self._render_tab(key, self._snap)

    def current_tab(self) -> str:
        return self._current

    def _render_tab(self, key: str, snap: dict) -> None:
        try:
            blocks = tab_blocks(key, snap)
        except Exception:
            log.debug("wow tab %s failed", key, exc_info=True)
            blocks = []
        sig = repr(blocks)
        if sig == self._tab_sig.get(key):
            return
        self._tab_sig[key] = sig
        (self._stale.add if self.tab_pages[key].isHidden() else self._stale.discard)(key)
        W, QtWidgets = self.W, self.app.QtWidgets
        Qt = self.app.QtCore.Qt
        qt = _qt_classes()
        old = self._tab_holder.get(key)
        if old is not None:
            old.setParent(None)
            old.deleteLater()
        holder = QtWidgets.QWidget()
        hl = QtWidgets.QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(24)
        if not blocks:
            sec = W.Section("")
            t, d = EMPTY_TEXT.get(key, ("Rien pour l'instant", ""))
            sec.add(W.Row(t, d))
            hl.addWidget(sec)
        for b in blocks:
            sec = W.Section(b["title"], b.get("foot") or "")
            for r in b["rows"][:16]:
                control = None
                if r.get("spark"):
                    control = qt["Sparkline"](r["spark"], tip="Historique")
                elif r.get("meter") is not None:
                    control = qt["Meter"](r["meter"])
                if r.get("value"):
                    val = W.label(r["value"], "secondary")
                    if control is not None:
                        box = QtWidgets.QWidget()
                        bx = QtWidgets.QHBoxLayout(box)
                        bx.setContentsMargins(0, 0, 0, 0)
                        bx.setSpacing(12)
                        bx.addWidget(control)
                        bx.addWidget(val, 0, Qt.AlignVCenter)
                        control = box
                    else:
                        control = val
                row = W.Row(r["title"], r.get("desc") or None, control)
                sec.add(row)
            hl.addWidget(sec)
        self.tab_layouts[key].insertWidget(0, holder)
        self._tab_holder[key] = holder

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
        if getattr(self, "_installing", False):
            return
        self._installing = True                        # the 3 s refresh must not re-enable the button meanwhile
        self.install_btn.setEnabled(False)
        path = str(getattr(self.app.cfg, "wow_path", "") or "")

        def job() -> Any:
            return install.install_addon(install.find_wow(path), force=True)

        def done(res: Any) -> None:
            self._installing = False
            self.install_btn.setEnabled(True)
            self.app.show_toast(str(getattr(res, "message", "")), "info" if getattr(res, "ok", False) else "error")
            self.refresh()

        def failed(_exc: BaseException) -> None:
            self._installing = False
            self.install_btn.setEnabled(True)

        self.app.run_job(job, done, failed, name="TreeAI-wow-install")

    def _plan(self) -> None:
        from treeaicoach.wow import optional  # noqa: PLC0415

        install = optional("install")
        if install is None:
            return
        self.plan_btn.setEnabled(False)
        self.plan_hint.setText("Calcul en cours (~20 s)…")
        path = str(getattr(self.app.cfg, "wow_path", "") or "")

        def job() -> Any:
            from treeaicoach.wow import planner  # noqa: PLC0415

            return planner.compute_and_write(install.find_wow(path), seconds=16.0)

        def done(res: Any) -> None:
            self.plan_btn.setEnabled(True)
            res = res if isinstance(res, dict) else {}
            ok = bool(res.get("ok"))
            if res.get("plan"):
                self._plan_last = res["plan"]
                self._render_plan(res["plan"])
            self.plan_hint.setText("Tape /reload en jeu pour charger le trajet." if ok
                                   else str(res.get("message") or "Calcul impossible."))
            self.app.show_toast(str(res.get("message") or ""), "info" if ok else "error")

        def failed(_exc: BaseException) -> None:
            self.plan_btn.setEnabled(True)
            self.plan_hint.setText("Calcul impossible.")

        self.app.run_job(job, done, failed, name="TreeAI-wow-plan")

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
        cfg, hub = self.app.cfg, self.hub

        def done(state: dict) -> None:
            self._busy = False
            self.state = state or {}
            if not self._first_poll:
                for text in live_toasts(self.state.get("events") or []):
                    self.app.show_toast(text, "info")
            self._first_poll = False
            self._render()

        def failed(_exc: BaseException) -> None:
            self._busy = False

        self.app.run_job(lambda: read_state(cfg, hub), done, failed, name="TreeAI-wow-state")

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
        self.install_btn.setEnabled(ov["can_install"] and not getattr(self, "_installing", False))
        self.install_hint.setText("Puis /reload en jeu." if ov["found"] else "Indique d'abord le dossier de WoW.")
        mode = st.get("mode") or {}
        snap = st.get("live") or {}
        report = str(mode.get("report") or "").strip() or str((snap.get("fights") or {}).get("last_text") or "")
        self.report_row.set_desc(report[:1200] if report else "Pas encore de combat lu.")
        self._render_char(st.get("rows") or [], st.get("sv_age"))
        self._render_plan(st.get("plan") or self._plan_last)
        from treeaicoach.wow import optional  # noqa: PLC0415

        live_mod = optional("live")
        if live_mod is not None:
            self.fresh.setText(live_mod.freshness_text(st.get("sv_age")))
        self._snap = snap
        self._render_tab(self._current, snap)          # the others are built when they are shown
        if self._current != "direct":
            self._render_tab("direct", snap)
        # the live card already shows the character: the SavedVariables card only when it is alone
        self.char_section.setVisible(not self._tab_sig.get("direct", "[]").startswith("[{"))

    def _render_char(self, rows: list, age: Any) -> None:
        # ANNOY: the age (« il y a 3 min ») changes every minute; only that label is updated then, the card
        # is rebuilt only when the character data changes (it used to blink once a minute)
        age_txt = _age_text(age)
        sig = (tuple(rows), bool(age_txt))
        if sig == self._char_sig:
            lbl = getattr(self, "_age_lbl", None)
            if lbl is not None:
                try:
                    lbl.setText(age_txt)
                except Exception:
                    log.debug("age label update failed", exc_info=True)
            return
        self._char_sig = sig
        self._age_lbl = None
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
        elif age_txt:
            self._age_lbl = W.label(age_txt, "secondary")
            grp.add(W.Row("Dernière sauvegarde", None, self._age_lbl))
        self._char_layout.addWidget(grp)
        self._char_group = grp

    def _render_plan(self, plan: Any) -> None:
        rows = plan_rows(plan)
        sig = tuple(rows)
        if sig == self._plan_sig:
            return
        self._plan_sig = sig
        W = self.W
        if self._plan_group is not None:
            self._plan_group.setParent(None)
            self._plan_group.deleteLater()
        grp = W.Group()
        for title, desc in rows:
            grp.add(W.Row(title, desc or None))
        if not rows:
            grp.add(W.Row("Pas encore de trajet", "Clique sur « Calculer le trajet »."))
        self._plan_layout.addWidget(grp)
        self._plan_group = grp

    def on_close(self) -> None:
        self.timer.stop()


def build(app: Any) -> WowPage:
    return WowPage(app)
