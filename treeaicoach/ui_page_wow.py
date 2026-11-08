"""WoW Forever (4.0, « compagnon »): the page to keep on a second screen or an alt-tab away while playing.

Top to bottom: the « Maintenant » card (the next step with its distance, time and why, the measured XP/h and the
time to the next level with how sure it is, at most two danger facts, how old all of it is), then the tabs
« Trajet · Groupe · HV · Session · Donjon (in an instance) · Erreurs », then the setup (« État », moved to the
top only when something needs doing), the coach switches and « Avancé ».

Zero freeze, by construction:

* every file read, parse and computation runs on a worker thread (:func:`read_state` → :func:`page_vm`): the
  whole page is turned into plain data (strings, numbers) there, each section with its signature;
* the UI thread only compares signatures and updates the widgets that changed, in place (pooled rows: a
  refresh never rebuilds a list), at most once per ``RENDER_MIN_MS``; long lists are virtualised
  (:class:`treeaicoach.ui_widgets.VirtualList`); sparklines are painted once into a pixmap, so repaints run
  no Python; hidden tabs are brought up to date one per idle turn, so switching tabs only flips visibility.

Blizzard rules: only the game's own files (SavedVariables, chat / combat logs), never memory, screen or keys."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

POLL_MS = 3000
RENDER_MIN_MS = 500          # at most one render per 500 ms
WARM_MS = 120                # hidden tabs are updated one per idle turn after a render
ERR_EMPTY = "Pas encore de fichier : en jeu, tape /tai erreurs puis /reload."
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
    """Everything the page shows (WORKER thread, files only). Never raises.

    With a :class:`treeaicoach.wow.live.LiveHub` the game files are watched (mtime) and re-parsed only when they
    change; ``out["live"]`` is its snapshot, ``out["events"]`` the changes of this tick and ``out["vm"]`` the
    whole page as plain data (:func:`page_vm`): the UI thread computes nothing."""
    from treeaicoach.wow import optional  # noqa: PLC0415

    out: dict = {"wow_exe": None, "lol": False, "install": None, "src_version": "", "inst_version": "",
                 "needs_update": False, "log": ("", ""), "rows": [], "sv_age": None, "mode": None,
                 "live": {}, "events": []}
    try:
        process, install = optional("process"), optional("install")
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
            if inst is not None and hub is not None:
                try:            # the app's plan file, parsed once per change (it was re-parsed every 3 s)
                    from treeaicoach.wow import live  # noqa: PLC0415
                    from treeaicoach.wow.planner import writer  # noqa: PLC0415

                    plan = live.read_db_cached(Path(inst.addons_dir) / writer.PLAN_ADDON / "Plan.lua", "TreeAIPlan")
                    out["plan"] = plan or None
                except Exception:
                    log.debug("plan read failed", exc_info=True)
            if hub is not None:
                hub.attach_install(inst)
                out["events"] = hub.poll()
                snap = hub.snapshot()
                out["live"] = snap
                out["rows"] = summary_rows(snap.get("db") or {}) if snap.get("db") else []
                out["sv_age"] = snap.get("sv_age")
            else:
                files = install.savedvars_files(inst)
                live = optional("live")
                if files and live is not None:
                    out["rows"] = summary_rows(live.read_db_cached(files[0]))
                    out["sv_age"] = install.file_status(files[0]).get("age_s")
    except Exception:
        log.debug("wow page state failed", exc_info=True)
    out["errfile"] = _errfile_info()
    if hub is not None:
        try:
            out["vm"] = page_vm(out)
        except Exception:
            log.debug("wow page view failed", exc_info=True)
    return out


def _errfile_info() -> dict | None:
    """Where TreeAI-erreurs.txt is and how old (worker thread). Never raises."""
    try:
        from treeaicoach.wow import optional  # noqa: PLC0415

        errlog = optional("errlog")
        p = errlog.report_path() if errlog is not None else None
        if p is None or not p.is_file():
            return None
        return {"path": str(p), "age": max(0.0, time.time() - p.stat().st_mtime)}
    except Exception:
        log.debug("errfile info failed", exc_info=True)
        return None


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
    log; ``button`` / ``can_install``: the install button; ``ready``: nothing to do (the card goes down)."""
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
            "can_install": bool(inst is not None and has_src), "found": inst is not None,
            "ready": not st or tone in ("ok", "") or headline.startswith("Presque")}


# ---------------------------------------------------------------------------------------- view model (pure)
TABS: tuple[tuple[str, str], ...] = (("trajet", "Trajet"), ("groupe", "Groupe"), ("hv", "HV"),
                                     ("session", "Session"), ("donjon", "Donjon"), ("erreurs", "Erreurs"))
TAB_KEYS = tuple(k for k, _t in TABS)
#: tabs of 3.9 (« Direct », « Quêtes ») now live in the « Maintenant » card and « Trajet »
TAB_ALIASES = {"direct": "trajet", "quetes": "trajet"}
#: what a tab shows while it has nothing (title, how to fill it)
EMPTY_TEXT = {
    "trajet": ("Pas encore de trajet", "Installe l'addon, connecte-toi puis tape /reload : tes quêtes et ton trajet "
                                       "arrivent ici."),
    "groupe": ("Tu joues seul", "Les cartes de tes amis apparaissent en groupe, quand ils ont TreeAI."),
    "hv": ("Pas encore de données d'hôtel des ventes", "Ouvre l'HV, clique « Scanner », puis /reload. "
                                                       "Les prix d'Auctionator sont lus aussi."),
    "session": ("Pas encore de session", "Tape /chatlog en jeu : l'XP, l'or, les morts et le butin sont suivis "
                                         "en direct. Sinon, chaque /reload ajoute un point."),
    "donjon": ("Pas de donjon en cours", "L'onglet s'affiche dans une instance ou quand un donjon est prévu."),
    "erreurs": ("Aucune erreur", "En jeu : /tai erreurs puis /reload pour le rapport complet."),
}
SECTION_ROWS_MAX = 16
EMPTY_GLYPH = {"trajet": "wow", "session": "clock", "donjon": "wow", "erreurs": "about"}


def _row(title: Any, desc: Any = "", value: Any = "", **kw: Any) -> dict:
    """One row of a section: title (+ description) on the left; optional number bubble (``num``), badge,
    progress (``meter`` 0..100), sparkline (``spark``) and value on the right. Plain data."""
    r = {"title": str(title or ""), "desc": str(desc or ""), "value": str(value or "")}
    for k, v in kw.items():
        if v is not None and v != "" and v != [] and v != ():
            r[k] = v
    return r


def _sec(out: list, sid: str, title: str, rows: list, foot: str = "", kind: str = "rows", **kw: Any) -> None:
    """Append a section {id, title, foot, kind, rows} (kind: rows / list / members / chart) when it has rows."""
    if rows or kw.get("always"):
        sec = {"id": sid, "title": title, "foot": " ".join(str(foot or "").split()), "kind": kind,
               "rows": list(rows)[:SECTION_ROWS_MAX] if kind in ("rows", "members") else list(rows)}
        sec.update({k: v for k, v in kw.items() if k != "always"})
        out.append(sec)


def _trajet(snap: dict, ctx: dict) -> list[dict]:
    from treeaicoach.wow import live  # noqa: PLC0415

    out: list[dict] = []
    s = snap.get("summary") or {}
    t = live.trajet_view(snap, ctx.get("plan"))
    rows = []
    if t["warn"]:
        rows.append(_row(t["warn"], "", tone="warning"))
    for st in t["stops"]:
        cur = bool(st["current"])
        tag = st.get("tag", "") if st.get("tag") in ("Prête à rendre", "À plusieurs", "Rapide") else ""
        rows.append(_row(st["title"], st["desc"], num=str(st["n"]), num_tone="accent" if cur else "",
                         badge="En cours" if cur else tag, badge_tone="accent" if cur else
                         ("ok" if tag == "Prête à rendre" else "")))
    foot = [t["head"] + "." if t["head"] and not t["head"].endswith(".") else t["head"]]
    if t["minutes"]:
        foot.append(f"Reste ≈ {live.duration_text(t['minutes'] * 60)} selon le planificateur.")
    if t["dropped"]:
        foot.append(f"{t['dropped']} étape{'s' if t['dropped'] > 1 else ''} masquée{'s' if t['dropped'] > 1 else ''} : "
                    "quête plus dans ton journal.")
    if t["stops"]:
        _sec(out, "trajet.stops", "Ton trajet", rows, " ".join(x for x in foot if x))
    elif t["warn"]:
        _sec(out, "trajet.stops", "Ton trajet", rows)
    routes = plan_rows(ctx.get("plan"))
    rrows = [_row(a, b) for a, b in routes] or [
        _row("Pas encore de trajet calculé", "Après un /reload en jeu, clique « Calculer » : trois trajets au choix "
                                            "(le plus rapide, tout finir ici, avec tes potes).")]
    _sec(out, "trajet.routes", "Trajets calculés par TreeAI", rrows,
         "Choisis-en un en jeu : /tai plan." if routes else "", action="plan", always=True)
    ab = live.abandon_advice(s)
    _sec(out, "trajet.abandon", "À retirer de ton journal",
         [_row(f"Retire « {a['title']} »", a["why"]) for a in ab],
         "Tu la retires toi-même : journal de quêtes (L) → Abandonner.")
    tips = []
    tr = s.get("tracker") if isinstance(s.get("tracker"), dict) else {}
    logq = live.log_quests(s) or {}
    pk = tr.get("pickup")
    if isinstance(pk, dict) and pk.get("title") and live._i(pk.get("id")) not in logq:
        tips.append(_row(f"Prends « {pk['title']} »", f"chez {pk['giver']}" if pk.get("giver") else "En chemin."))
    for e in live._list(tr.get("easyNearby"))[:3]:
        if isinstance(e, dict) and e.get("title") and live._i(e.get("id")) not in logq:
            tips.append(_row(f"« {e['title']} »", "Facile, juste à côté."))
    _sec(out, "trajet.tips", "En passant", tips)
    return out


def _groupe(snap: dict, ctx: dict) -> list[dict]:
    from treeaicoach.wow import live  # noqa: PLC0415

    out: list[dict] = []
    members = live.members_view(snap)
    g = live.group_view(snap)
    if len(members) >= 2:
        rows = [{"title": m["name"] + (" (toi)" if m["me"] else ""), "desc": m["line"],
                 "badges": [list(b) for b in m["badges"]]} for m in members]
        foot = f"{g['no_addon']} membre(s) sans TreeAI." if g["no_addon"] else ""
        _sec(out, "groupe.members", "Le groupe", rows, foot, kind="members")
    if g["next"]:
        _sec(out, "groupe.next", "Prochaine mission", [_row(g["next"])])
    _sec(out, "groupe.board", "Quêtes en commun",
         [_row(e["title"], e["state"], meter=e["pct"]) for e in g["board"]])
    _sec(out, "groupe.split", "Qui fait quoi", [_row(x) for x in g["assign"] + g["claims"]])
    _sec(out, "groupe.notes", "À savoir", [_row(x) for x in g["travel"] + g["crafts"]])
    return out


def _hv(snap: dict, ctx: dict) -> list[dict]:
    from treeaicoach.wow import live  # noqa: PLC0415

    out: list[dict] = []
    v = live.ah_view(snap)
    if v["empty"]:
        return out
    m = live.money
    rows = []
    for d in v["deals"]:
        label = f"{d['name']} x{d['count']}" if d["count"] > 1 else d["name"]
        if d["kind"] == "vendor":
            desc = f"acheter {m(d['buy'])}, revendre au marchand {m(d['sell'])} : gain sûr"
        else:
            desc = (f"acheter {m(d['buy'])} → revendre {m(d['sell'])} · commission {m(d['cut'])} · "
                    f"dépôt {m(d['deposit'])} (perdu si invendu)")
            if d.get("sold") is not None:
                desc += f" · se vend ~{d['sold']:.1f}/jour".replace(".", ",")
            if d.get("stop"):
                desc += f" · stop-loss {m(d['stop'])}/u"
            if d["atr"]:
                desc += f" · Auctionator {m(d['atr'] * d['count'])}"
        series = []
        if len(d["ours"]) > 1:
            series.append([[list(p) for p in d["ours"][-30:]], "accent"])
        if len(d["atr_series"]) > 1:
            series.append([[list(p) for p in d["atr_series"][-30:]], "warning"])
        rows.append(_row(label, desc, f"+{m(d['net'])} net", spark=series or None,
                         value_tone="ok" if d["net"] > 0 else ""))
    foot = []
    if rows:
        foot.append("Net = vente − commission − achat. Courbes : prix TreeAI (bleu), Auctionator (jaune).")
    elif v["none"]:
        rows = [_row(v["none"][0], " · ".join(v["none"][1:]), tone="warning" if v.get("stale_deals") else "")]
        foot.append("Seuls les objets qui se vendent vraiment sont proposés (ventes mesurées sur plusieurs jours).")
    if v.get("rules"):
        foot.append(v["rules"])
    if v["thin"]:
        foot.append("Prudence, marché mince : " + ", ".join(v["thin"]) + ".")
    if v["info"]:
        foot.append(v["info"][:1].upper() + v["info"][1:] + ".")
    _sec(out, "hv.deals", "Achat-revente", rows, " ".join(foot))
    mine = [_row(x["name"] + (f" x{x['count']}" if x["count"] > 1 else ""),
                 (f"plus bas {m(x['lowest'])} · " if x["lowest"] else "") + x["advice"],
                 f"{m(x['unit'])}/u", tone="warning" if x["undercut"] else "") for x in v["mine"]]
    foot2 = ""
    if not mine and v["recent"]:
        mine = [_row(n, day, f"+{m(c)}") for n, c, day in v["recent"]]
        foot2 = "Ventes réalisées récentes (courrier de l'HV). Ouvre l'HV puis /reload pour tes enchères en cours."
    _sec(out, "hv.mine", "Mes ventes", mine, foot2)
    r = v["realized"]
    if r["total"] or r["today"] or r["week"] or r["series"]:
        rr = [_row("Profit réalisé", f"aujourd'hui {m(r['today'])} · 7 jours {m(r['week'])}", m(r["total"]),
                   spark=[[[float(x) for x in r["series"]], "ok"]] if len(r["series"]) > 1 else None)]
        rr += [_row(n, "", f"+{m(p)}") for n, p in r["top"][:3]]
        _sec(out, "hv.profit", "Bilan", rr, v["budget"])
    return out


def _session(snap: dict, ctx: dict) -> list[dict]:
    from treeaicoach.wow import live  # noqa: PLC0415
    from treeaicoach.wow import stats as wstats  # noqa: PLC0415

    out: list[dict] = []
    s = snap.get("summary") or {}
    chat = snap.get("chat") or {}
    fresh = live.freshness(snap)
    rate = live.xp_rate(snap)
    v = live.session_view(snap)
    # the XP curve: the live chat log (5-min steps), else one point per /reload
    if fresh["live"] and len(chat.get("xp_series") or []) > 1:
        pts = [float(x) for x in chat["xp_series"]][-48:]
        cap = f"{live.fmt_int(chat.get('xp'))} XP en {live.duration_text(chat.get('duration')) or '< 1 min'} (en direct)"
        _sec(out, "session.curve", "Courbe d'XP", [{"spark": [[pts, "accent"]], "title": cap,
                                                    "desc": "Par tranche de 5 min, journal de discussion."}],
             kind="chart")
    elif len(snap.get("xp_points") or []) > 1:
        pts = [[float(a), float(b)] for a, b in snap["xp_points"]][-48:]
        tot, mins = pts[-1][1], pts[-1][0]
        cap = f"{live.fmt_int(tot)} XP en {live.duration_text(mins * 60) or '< 1 min'} (entre tes /reload)"
        _sec(out, "session.curve", "Courbe d'XP", [{"spark": [[pts, "accent"]], "title": cap,
                                                    "desc": "Un point par /reload. /chatlog pour la courbe en direct."}],
             kind="chart")
    recap = []
    if rate:
        recap.append(_row("XP par heure", f"{rate['src']}, sur {live.duration_text(rate['minutes'] * 60)} · {rate['conf']}",
                          f"{live.fmt_int(rate['xp_h'])} XP/h"))
    recap += [_row(t, "", val) if len(str(val)) < 34 else _row(t, val) for t, val in v["rows"]]
    recap += _deaths(snap, fresh["live"])
    _sec(out, "session.recap", "Récap", recap)
    goals = [_row(g["text"], meter=g["pct"]) for g in v["goals"] if g.get("pct") is not None]
    goals += [_row(g["text"]) for g in v["goals"] if g.get("pct") is None]
    _sec(out, "session.goals", "Objectifs", goals, "Objectif du groupe en jeu : /tai objectif niveau 30.")
    _sec(out, "session.board", "XP du groupe cette session",
         [_row(n, "", f"+{live.fmt_int(x)} XP" if x else "") for n, x in v["leaderboard"]])
    loot = [(f"{n} x{c}" if c > 1 else str(n), "", "") for n, c in (chat.get("loot") or []) if fresh["live"]]
    _sec(out, "session.loot", "Butin (en direct)", loot, kind="list")
    _sec(out, "session.stats", "Mes stats mesurées", [_row(t, "", val) if len(str(val)) < 34 else _row(t, val)
                                                      for t, val in wstats.stats_rows(s)],
         "Mesurées en jouant par l'addon (Plus → Stats → « Exporter pour TreeAI », puis /reload).")
    report = str(ctx.get("report") or "").strip() or str((snap.get("fights") or {}).get("last_text") or "")
    if report:
        _sec(out, "session.fight", "Dernier combat", [_row("Journal de combat", report[:600])],
             "Lu dans Logs/WoWCombatLog.txt (/combatlog). WoW Forever ne l'écrit pas toujours.")
    _sec(out, "session.events", "Évènements du groupe", [(e, "", "") for e in v["events"]], kind="list")
    return out


def _deaths(snap: dict, live_chat: bool) -> list[dict]:
    """Session rows about MY deaths: the count (live chat log, else the addon's session) and the last cause."""
    from treeaicoach.wow import live  # noqa: PLC0415

    s = snap.get("summary") or {}
    chat = snap.get("chat") or {}
    rows = []
    n, src = None, ""
    if live_chat and chat.get("events"):
        n, src = live._i(chat.get("deaths")), "en direct"
    elif isinstance(s.get("session"), dict) and s["session"].get("deaths") is not None:
        n, src = live._i(s["session"]["deaths"]), "au /reload"
    if n is not None:
        rows.append(_row("Morts", src, str(n) if n else "Aucune", value_tone="warning" if n >= 2 else ""))
    ld = (s.get("fight") or {}).get("lastDeath") if isinstance(s.get("fight"), dict) else None
    if isinstance(ld, dict) and ld and not live.freshness(snap)["stale"]:
        rows.append(_row("Dernière mort", live._why(ld.get("advice")), live.last_death_text(ld)))
    return rows


def _donjon(snap: dict, ctx: dict) -> list[dict]:
    from treeaicoach.wow import live  # noqa: PLC0415

    out: list[dict] = []
    d = live.dungeon_view(snap)
    if not d["active"]:
        return out
    rows = [_row(d["name"] or "Donjon", d["why"])]
    if d["boss"]:
        rows.append(_row("Boss en cours", "", d["boss"], value_tone="warning"))
    if d["next"]:
        rows.append(_row("Prochain boss", "", d["next"]))
    _sec(out, "donjon.now", "En ce moment", rows, "Minuteurs et conseils de boss : dans le jeu (onglet Donjon).")
    _sec(out, "donjon.run", "Ce run", [_row(t, "", val) for t, val in d["run"]])
    _sec(out, "donjon.last", "Run précédent", [_row(t, "", val) for t, val in d["last"]])
    return out


def _erreurs(snap: dict, ctx: dict) -> list[dict]:
    from treeaicoach.wow import errlog  # noqa: PLC0415

    out: list[dict] = []
    lists = errlog.diag_lists(snap.get("db") or {})
    info = ctx.get("errfile")
    c = lists["counts"]
    if isinstance(info, dict) and info.get("path"):
        when = _age_text(info.get("age")) or "à l'instant"
        file_row = _row("TreeAI-erreurs.txt", f"{info['path']} · mis à jour {when}")
    else:
        file_row = _row("TreeAI-erreurs.txt", ERR_EMPTY)
    counts = []
    if c["errors"]:
        counts.append(f"{c['errors']} erreur{'s' if c['errors'] > 1 else ''} Lua différente{'s' if c['errors'] > 1 else ''}"
                      f" ({c['errors_total']} au total)")
    if c["blocked"]:
        counts.append(f"{c['blocked']} action{'s' if c['blocked'] > 1 else ''} bloquée{'s' if c['blocked'] > 1 else ''}")
    rows = [file_row]
    if snap.get("db"):
        rows.append(_row(" · ".join(counts) if counts else "Aucune erreur ni action bloquée",
                         "Dans la dernière sauvegarde de l'addon.", tone="warning" if counts else "ok"))
    _sec(out, "erreurs.file", "Fichier d'erreurs", rows, "Envoie ce fichier pour une correction.",
         action="errors", always=True)
    _sec(out, "erreurs.blocked", "Actions bloquées (les plus fréquentes)",
         [(t, tip, "") for t, tip, _n in lists["blocked"]], "Survole une ligne pour le détail.", kind="list")
    _sec(out, "erreurs.errors", "Erreurs Lua (les plus fréquentes)",
         [(t, tip, "") for t, tip, _n in lists["errors"]], "Survole une ligne pour le message complet.",
         kind="list")
    _sec(out, "erreurs.tests", "Autotest : à revoir", [_row(t, "", tone=tone) for t, tone in lists["tests"]])
    return out


_BUILDERS = {"trajet": _trajet, "groupe": _groupe, "hv": _hv, "session": _session, "donjon": _donjon,
             "erreurs": _erreurs}


def _sign(x: Any) -> int:
    return hash(repr(x))


def tab_sections(key: str, snap: dict, ctx: dict | None = None) -> list[dict]:
    """What one tab shows: ordered sections {id, title, foot, kind, rows, sig} (pure, worker thread). An empty
    list means « nothing yet » (the page shows :data:`EMPTY_TEXT`)."""
    key = TAB_ALIASES.get(str(key), str(key))
    fn = _BUILDERS.get(key)
    if fn is None:
        return []
    try:
        secs = fn(snap or {}, ctx or {})
    except Exception:
        log.debug("wow tab %s failed", key, exc_info=True)
        secs = []
    for sec in secs:
        sec["sig"] = _sign(sec)
    return secs


def tab_blocks(key: str, snap: dict) -> list[dict]:
    """(3.9 name) :func:`tab_sections` without context: blocks with ``title``, ``foot`` and ``rows`` of
    {title, desc, value…}."""
    return tab_sections(key, snap)


def _ctx(st: dict) -> dict:
    mode = st.get("mode") or {}
    return {"plan": st.get("plan"), "errfile": st.get("errfile"), "report": mode.get("report") if isinstance(mode, dict) else ""}


def fresh_texts(snap: dict, tabs: dict) -> dict:
    """The line under the tab bar, per tab: how old the data is (and the AH scan's age for « HV »)."""
    from treeaicoach.wow import live  # noqa: PLC0415

    from treeaicoach.wow import auction  # noqa: PLC0415

    f = live.freshness(snap)
    out = {k: f["text"] for k in TAB_KEYS}
    a = (snap.get("summary") or {}).get("auction")
    if isinstance(a, dict):
        scan = auction.scan_age(a, live.data_age(snap), snap.get("db"), snap.get("now"))
        if scan is not None:
            out["hv"] = f"{f['text']} Dernier scan de l'HV {live.age_text(scan)}."
    out["erreurs"] = ("Le fichier se réécrit quand les erreurs changent, à chaque /reload. "
                      "En jeu : /tai erreurs puis /reload pour le rapport complet.")
    return out


def page_vm(st: dict) -> dict:
    """The whole page as plain data (WORKER thread): the « Maintenant » card, every tab's sections with their
    signatures, which tabs exist, the freshness line of each tab."""
    from treeaicoach.wow import live  # noqa: PLC0415

    st = st or {}
    snap = st.get("live") or {}
    ctx = _ctx(st)
    hero = live.hero_view(snap)
    tabs = {k: tab_sections(k, snap, ctx) for k in TAB_KEYS}
    return {"hero": hero, "hero_sig": _sign(hero), "tabs": tabs,
            "tab_sig": {k: tuple(s["sig"] for s in v) for k, v in tabs.items()},
            "visible": {"donjon": bool(tabs.get("donjon"))}, "fresh": fresh_texts(snap, tabs)}


def live_toasts(events: list) -> list[str]:
    """Toasts worth showing for the changes of one tick (level up). Pure."""
    out: list[str] = []
    for ev in events or []:
        kind = getattr(ev, "kind", "")
        if kind == "chat":
            for e in getattr(ev, "data", None) or []:
                if e.get("kind") == "level":
                    out.append(f"Niveau {e.get('level')} ! Bravo.")
    return out[-2:]


# ---------------------------------------------------------------------------------------- Qt (UI thread)
_QT: dict = {}


def _qt_classes() -> dict:
    """The page's widgets (built once, Qt imported lazily). All of them update IN PLACE from plain data."""
    if _QT:
        return _QT
    from PySide6 import QtCore, QtGui, QtWidgets  # noqa: PLC0415

    from treeaicoach import ui_widgets as W  # noqa: PLC0415

    Qt = QtCore.Qt

    def color_of(name: str) -> str:
        return str(getattr(W.theme(), name, name))

    def spark_pixmap(series: list, w: int, h: int, dpr: float) -> Any:
        pm = QtGui.QPixmap(max(1, int(w * dpr)), max(1, int(h * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        pts_all = []
        for pts, _c in series:
            xy = []
            for i, p in enumerate(pts or []):
                try:
                    xy.append((float(p[0]), float(p[1])) if isinstance(p, (list, tuple)) else (float(i), float(p)))
                except (TypeError, ValueError, IndexError):
                    continue
            pts_all.append(xy)
        flat = [p for xy in pts_all for p in xy]
        if not flat:
            return pm
        x0, x1 = min(p[0] for p in flat), max(p[0] for p in flat)
        y0, y1 = min(p[1] for p in flat), max(p[1] for p in flat)
        if y1 - y0 < 1e-9:
            y0, y1 = y0 - 1, y1 + 1
        if x1 - x0 < 1e-9:
            x0, x1 = x0 - 1, x1 + 1
        pad = 4.0
        iw, ih = w - 2 * pad, h - 2 * pad
        qp = QtGui.QPainter(pm)
        qp.setRenderHint(QtGui.QPainter.Antialiasing)
        qp.setPen(QtGui.QPen(QtGui.QColor(color_of("separator")), 1))
        qp.drawLine(QtCore.QPointF(pad, pad + ih), QtCore.QPointF(pad + iw, pad + ih))
        for xy, (_p, color) in zip(pts_all, series):
            if not xy:
                continue
            mapped = [QtCore.QPointF(pad + (x - x0) / (x1 - x0) * iw, pad + ih - (y - y0) / (y1 - y0) * ih)
                      for x, y in xy]
            c = QtGui.QColor(color_of(color))
            qp.setPen(QtGui.QPen(c, 1.6))
            qp.setBrush(Qt.NoBrush)
            qp.drawPolyline(mapped)
            qp.setPen(Qt.NoPen)
            qp.setBrush(c)
            qp.drawEllipse(mapped[-1], 2.6, 2.6)
        qp.end()
        return pm

    def screen_dpr() -> float:
        try:
            scr = QtGui.QGuiApplication.primaryScreen()
            return float(scr.devicePixelRatio()) if scr is not None else 1.0
        except Exception:
            return 1.0

    class Sparkline(QtWidgets.QLabel):
        """A sparkline painted ONCE into a pixmap when its data changes: a repaint is plain C++, no Python
        (it used to run a Python paintEvent on every repaint). ``stretch``: follows the width (charts)."""

        def __init__(self, series: list | None = None, width: int = 168, height: int = 40, tip: str = "",
                     stretch: bool = False) -> None:
            super().__init__()
            self._w, self._h, self._stretch = width, height, stretch
            self.series: list = []
            if stretch:
                self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
                self.setMinimumWidth(80)
                self.setFixedHeight(height)
            else:
                self.setFixedSize(width, height)
            if tip:
                self.setToolTip(tip)
            if series:
                self.set_series(series)

        def set_series(self, series: Any) -> None:
            series = [(list(pts), str(color)) for pts, color in (series or []) if pts]
            if series == self.series:
                return
            self.series = series
            self.redraw()

        def redraw(self) -> None:
            w = max(80, self.width()) if self._stretch else self._w
            self._drawn_w = w
            dpr = float(self.devicePixelRatioF() or 0) or screen_dpr()
            self.setPixmap(spark_pixmap(self.series, w, self._h, dpr))

        def resizeEvent(self, e: Any) -> None:  # noqa: N802
            super().resizeEvent(e)
            if self._stretch and abs(self.width() - getattr(self, "_drawn_w", 0)) > 8:
                self.redraw()

    def set_tone(w: Any, tone: str) -> None:
        W.set_prop(w, "tone", tone or "")

    def set_text(lbl: Any, text: str) -> None:
        if lbl.text() != text:
            lbl.setText(text)
        vis = bool(text)
        if lbl.isHidden() == vis:
            lbl.setVisible(vis)

    class RowView(QtWidgets.QWidget):
        """One pooled row: number bubble, title + description, then badge, progress, sparkline, value. Every
        part is created the first time it is needed and updated in place afterwards."""

        def __init__(self) -> None:
            super().__init__()
            self.setMinimumHeight(W.ROW_MIN_H)
            g = self._g = QtWidgets.QGridLayout(self)
            g.setContentsMargins(W.ROW_PAD_X, W.ROW_PAD_Y, W.ROW_PAD_X, W.ROW_PAD_Y)
            g.setHorizontalSpacing(W.SP_3)
            g.setVerticalSpacing(2)
            self.title = W.label("", wrap=True)
            self.title.setTextInteractionFlags(Qt.TextSelectableByMouse)
            g.addWidget(self.title, 0, 1)
            self.desc = W.label("", "secondary", wrap=True)
            g.addWidget(self.desc, 1, 1)
            self.desc.hide()
            g.setColumnStretch(1, 1)
            self.right = QtWidgets.QWidget()
            rl = self._rl = QtWidgets.QHBoxLayout(self.right)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(W.SP_3)
            g.addWidget(self.right, 0, 2, 2, 1, Qt.AlignRight | Qt.AlignVCenter)
            self.right.hide()
            self.num: Any = None
            self.badge: Any = None
            self.meter: Any = None
            self.spark: Any = None
            self.value: Any = None
            self.r: dict = {}

        def _part(self, name: str) -> Any:
            w = getattr(self, name)
            if w is not None:
                return w
            if name == "num":
                w = W.label("", "num")
                w.setAlignment(Qt.AlignCenter)
                w.setFixedWidth(26)
                self._g.addWidget(w, 0, 0, 2, 1, Qt.AlignTop)
            elif name == "badge":
                w = W.badge("")
                self._rl.insertWidget(0, w)
            elif name == "meter":
                w = QtWidgets.QProgressBar()
                w.setRange(0, 100)
                w.setTextVisible(False)
                w.setFixedWidth(120)
                self._rl.insertWidget(min(1, self._rl.count()), w)
            elif name == "spark":
                w = Sparkline(None, tip="Historique")
                self._rl.insertWidget(max(0, self._rl.count() - (1 if self.value is not None else 0)), w)
            elif name == "value":
                w = W.label("", "secondary")
                self._rl.addWidget(w, 0, Qt.AlignVCenter)
            setattr(self, name, w)
            return w

        def set(self, r: dict) -> None:
            if r == self.r:
                return
            old, self.r = self.r, dict(r)
            set_text(self.title, str(r.get("title") or ""))
            if old.get("tone", "") != r.get("tone", ""):
                set_tone(self.title, str(r.get("tone") or ""))
            set_text(self.desc, str(r.get("desc") or ""))
            if r.get("num") or self.num is not None:
                n = self._part("num")
                set_text(n, str(r.get("num") or ""))
                set_tone(n, str(r.get("num_tone") or ""))
            if r.get("badge") or self.badge is not None:
                W.set_badge(self._part("badge"), str(r.get("badge") or ""), str(r.get("badge_tone") or ""))
            if r.get("meter") is not None or self.meter is not None:
                m = self._part("meter")
                pct = r.get("meter")
                if pct is None:
                    m.hide()
                else:
                    pct = max(0, min(100, int(pct)))
                    if m.value() != pct:
                        m.setValue(pct)
                    set_tone(m, "ok" if pct >= 100 else "")
                    m.show()
            if r.get("spark") or self.spark is not None:
                sp = self._part("spark")
                sp.set_series(r.get("spark") or [])
                sp.setVisible(bool(r.get("spark")))
            if r.get("value") or self.value is not None:
                v = self._part("value")
                set_text(v, str(r.get("value") or ""))
                set_tone(v, str(r.get("value_tone") or ""))
            show_right = bool(r.get("badge") or r.get("meter") is not None or r.get("spark") or r.get("value"))
            if self.right.isHidden() == show_right:
                self.right.setVisible(show_right)

    class MemberCard(QtWidgets.QFrame):
        """A friend's card (name, class, level, zone, up to three status badges)."""

        def __init__(self) -> None:
            super().__init__()
            self.setObjectName("Group")
            lay = QtWidgets.QVBoxLayout(self)
            lay.setContentsMargins(W.SP_4, W.SP_3, W.SP_4, W.SP_3)
            lay.setSpacing(W.SP_1)
            self.name = W.label("", "cardtitle")
            lay.addWidget(self.name)
            self.line = W.label("", "secondary", wrap=True)
            lay.addWidget(self.line)
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(W.SP_2)
            self.badges = [W.badge("") for _ in range(3)]
            for b in self.badges:
                row.addWidget(b)
                b.hide()
            row.addStretch(1)
            lay.addLayout(row)
            lay.addStretch(1)                       # content stays at the top of a taller neighbour's row
            self.r: dict = {}

        def set(self, r: dict) -> None:
            if r == self.r:
                return
            self.r = dict(r)
            set_text(self.name, str(r.get("title") or ""))
            set_text(self.line, str(r.get("desc") or ""))
            bs = list(r.get("badges") or [])
            for i, b in enumerate(self.badges):
                text, tone = (bs[i] + ["", ""])[:2] if i < len(bs) else ("", "")
                W.set_badge(b, str(text), str(tone))

    class SectionView(QtWidgets.QWidget):
        """Header (title + optional action) + body (pooled rows, a virtual list, member cards or a chart) +
        footnote. :meth:`apply` does nothing when the section's signature did not change."""

        def __init__(self, kind: str, action: Any = None) -> None:
            super().__init__()
            self.kind = kind
            self.sig: Any = None
            lay = QtWidgets.QVBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(W.SP_2)
            head = QtWidgets.QHBoxLayout()
            head.setContentsMargins(4, 0, 4, 4)
            head.setSpacing(W.SP_4)
            self.header = W.label("", "section")
            head.addWidget(self.header, 0)
            head.addStretch(1)
            if action is not None:
                head.addWidget(action, 0, Qt.AlignRight | Qt.AlignVCenter)
            lay.addLayout(head)
            self.rows: list = []
            if kind == "list":
                self.body = W.VirtualList(max_visible=8)
            elif kind == "members":
                self.body = QtWidgets.QWidget()
                self._grid = QtWidgets.QGridLayout(self.body)
                self._grid.setContentsMargins(0, 0, 0, 0)
                self._grid.setSpacing(W.SP_3)
            elif kind == "chart":
                self.body = W.Card(pad=W.SP_4, spacing=W.SP_1)
                self.chart = Sparkline(None, width=480, height=72, stretch=True)
                self.body.add(self.chart)
                self.caption = W.label("", "cardtitle", wrap=True)
                self.body.add(self.caption)
                self.sub = W.label("", "secondary", wrap=True)
                self.body.add(self.sub)
            else:
                self.body = W.Group()
            lay.addWidget(self.body)
            self.footnote = W.label("", "footnote", wrap=True)
            self.footnote.setContentsMargins(W.ROW_PAD_X, 0, W.ROW_PAD_X, 0)
            lay.addWidget(self.footnote)
            self.footnote.hide()

        def apply(self, sec: dict) -> bool:
            sig = sec["sig"] if "sig" in sec else repr(sec)      # (signatures come from the worker)
            if sig == self.sig:
                return False
            self.sig = sig
            set_text(self.header, str(sec.get("title") or ""))
            set_text(self.footnote, str(sec.get("foot") or ""))
            rows = list(sec.get("rows") or [])
            if self.kind == "list":
                self.body.set_rows(rows)
            elif self.kind == "chart":
                r = rows[0] if rows else {}
                self.chart.set_series(r.get("spark") or [])
                set_text(self.caption, str(r.get("title") or ""))
                set_text(self.sub, str(r.get("desc") or ""))
            elif self.kind == "members":
                for i, r in enumerate(rows):
                    if i >= len(self.rows):
                        card = MemberCard()
                        self._grid.addWidget(card, i // 2, i % 2)
                        self.rows.append(card)
                    self.rows[i].set(r)
                    if self.rows[i].isHidden():
                        self.rows[i].show()
                for card in self.rows[len(rows):]:
                    card.hide()
            else:
                for i, r in enumerate(rows):
                    if i >= len(self.rows):
                        self.rows.append(self.body.add(RowView()))
                    self.rows[i].set(r)
                    if self.rows[i].isHidden():
                        self.rows[i].show()
                for row in self.rows[len(rows):]:
                    if not row.isHidden():
                        row.hide()
                self.body.sync_separators()
            return True

        def recolor(self) -> None:
            if self.kind == "list":
                self.body.recolor()
            elif self.kind == "chart":
                self.chart.redraw()
            else:
                for r in self.rows:
                    if isinstance(r, RowView) and r.spark is not None:
                        r.spark.redraw()

    class TabView(QtWidgets.QWidget):
        """One tab: its sections in order (created once, updated in place, hidden when absent) or the empty
        state. ``actions``: section id → header widget (buttons that must survive every update)."""

        def __init__(self, key: str, actions: dict) -> None:
            super().__init__()
            self.key = key
            self.actions = actions
            self.views: dict[str, SectionView] = {}
            self.sig: Any = None
            self.lay = QtWidgets.QVBoxLayout(self)
            self.lay.setContentsMargins(0, 0, 0, 0)
            self.lay.setSpacing(W.SECTION_GAP)
            title, text = EMPTY_TEXT.get(key, ("Rien pour l'instant", ""))
            self.empty = W.EmptyState(EMPTY_GLYPH.get(key, "tray"), title, text)
            self.lay.addWidget(self.empty)
            self.lay.addStretch(1)

        def apply(self, sections: list, sig: Any = None) -> bool:
            sig = sig if sig is not None else tuple(s.get("sig") for s in sections)
            if sig == self.sig:
                return False
            self.sig = sig
            ids = []
            for idx, sec in enumerate(sections, start=1):
                sid = str(sec.get("id"))
                ids.append(sid)
                v = self.views.get(sid)
                if v is None:
                    act = self.actions.get(sec.get("action")) if sec.get("action") else None
                    v = self.views[sid] = SectionView(str(sec.get("kind") or "rows"), act)
                    self.lay.insertWidget(idx, v)
                elif self.lay.indexOf(v) != idx:
                    self.lay.removeWidget(v)
                    self.lay.insertWidget(idx, v)
                v.apply(sec)
                if v.isHidden():
                    v.show()
            for sid, v in self.views.items():
                if sid not in ids and not v.isHidden():
                    v.hide()
            if self.empty.isHidden() == (not sections):
                self.empty.setVisible(not sections)
            return True

        def section_titles(self) -> list[str]:
            """Titles of the sections on screen, in order (tests: which content a tab shows)."""
            out = []
            for i in range(self.lay.count()):
                w = self.lay.itemAt(i).widget()
                if isinstance(w, SectionView) and not w.isHidden():
                    out.append(w.header.text())
            return out

    class HeroCard(W.Card):
        """« Maintenant »: next step (distance, time, why), XP/h, next level, danger facts, freshness."""

        def __init__(self) -> None:
            super().__init__(pad=W.CARD_PAD, spacing=W.SP_2)
            self.sig: Any = None
            head = QtWidgets.QHBoxLayout()
            head.setSpacing(W.SP_3)
            self.kicker = W.label("Maintenant", "kicker")
            head.addWidget(self.kicker, 0, Qt.AlignVCenter)
            head.addStretch(1)
            self.badge = W.badge("")
            head.addWidget(self.badge, 0, Qt.AlignVCenter)
            self.add(head)
            self.title = W.label("", "headline", wrap=True)
            self.title.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.add(self.title)
            self.sub = W.label("", "secondary", wrap=True)
            self.add(self.sub)
            self.sep = QtWidgets.QFrame()
            self.sep.setObjectName("Separator")
            self.sep.setFixedHeight(1)
            self.add(self.sep)
            tiles = QtWidgets.QHBoxLayout()
            tiles.setSpacing(W.SP_5)
            self.tiles: dict[str, dict] = {}
            for key, kick in (("xp", "XP par heure"), ("lvl", "Niveau suivant"), ("risk", "À surveiller")):
                box = QtWidgets.QWidget()
                bl = QtWidgets.QVBoxLayout(box)
                bl.setContentsMargins(0, 0, 0, 0)
                bl.setSpacing(2)
                k = W.label(kick, "kicker")
                bl.addWidget(k)
                v = W.label("", "metric", wrap=True)
                bl.addWidget(v)
                bar = None
                if key == "lvl":
                    bar = QtWidgets.QProgressBar()
                    bar.setRange(0, 100)
                    bar.setTextVisible(False)
                    bl.addWidget(bar)
                c = W.label("", "secondary", wrap=True)
                bl.addWidget(c)
                c2 = W.label("", "secondary", wrap=True)
                bl.addWidget(c2)
                bl.addStretch(1)
                tiles.addWidget(box, 1)
                self.tiles[key] = {"box": box, "kicker": k, "value": v, "cap": c, "cap2": c2, "bar": bar}
            self.add(tiles)
            self.foot = W.label("", "footnote", wrap=True)
            self.add(self.foot)

        def apply(self, h: dict, sig: Any = None) -> bool:
            sig = sig if sig is not None else repr(h)
            if sig == self.sig:
                return False
            self.sig = sig
            set_text(self.kicker, str(h.get("kicker") or "Maintenant"))
            W.set_badge(self.badge, str(h.get("badge") or ""), str(h.get("badge_tone") or ""))
            step = h.get("step") or {}
            if h.get("empty"):
                set_text(self.title, "Pas encore de données de l'addon")
                set_text(self.sub, "Installe l'addon, connecte-toi puis tape /reload en jeu. Pour l'XP et l'or en "
                                   "direct : /chatlog.")
            elif step.get("title"):
                set_text(self.title, str(step["title"]))
                set_text(self.sub, " · ".join(x for x in (step.get("dist"), step.get("eta"), step.get("why")) if x))
            else:
                set_text(self.title, "Rien de prévu pour l'instant")
                set_text(self.sub, "Prends des quêtes ou calcule un trajet (onglet Trajet).")
            xp, lvl, risk = h.get("xp") or {}, h.get("level") or {}, list(h.get("danger") or [])
            t = self.tiles["xp"]
            set_text(t["value"], f"{xp['xp_h']:,} XP/h".replace(",", " ") if xp.get("xp_h") else "")
            if xp.get("xp_h"):
                mins = int(round(float(xp.get("minutes") or 0)))
                set_text(t["cap"], f"{xp.get('src')}, sur {mins} min")
                set_text(t["cap2"], f"Mesure {xp.get('conf')}")
            else:
                set_text(t["cap"], "Pas encore mesurée")
                set_text(t["cap2"], "/chatlog en jeu, ou 10 min entre deux /reload")
            t = self.tiles["lvl"]
            t["kicker"].setText(f"Niveau {lvl.get('next')}" if lvl.get("next") else "Niveau suivant")
            set_text(t["value"], (str(lvl.get("eta") or "")) if lvl.get("pct") is not None else str(lvl.get("text") or ""))
            if lvl.get("pct") is not None:
                t["bar"].setValue(int(lvl["pct"]))
                t["bar"].show()
                set_text(t["cap"], f"{lvl['pct']} % du niveau {lvl.get('level')}")
                set_text(t["cap2"], f"Estimation {lvl['conf']}" if lvl.get("conf") else "Sans mesure d'XP/h")
            else:
                t["bar"].hide()
                set_text(t["cap"], "")
                set_text(t["cap2"], "")
            t = self.tiles["risk"]
            set_text(t["value"], "")
            for lbl, item in ((t["cap"], risk[0] if risk else None), (t["cap2"], risk[1] if len(risk) > 1 else None)):
                set_text(lbl, str(item[0]) if item else "")
                set_tone(lbl, str(item[1]) if item else "")
            show = {"xp": not h.get("empty"), "lvl": bool(lvl), "risk": bool(risk)}
            for k, on in show.items():
                if self.tiles[k]["box"].isHidden() == on:
                    self.tiles[k]["box"].setVisible(on)
            any_tile = any(show.values())
            if self.sep.isHidden() == any_tile:
                self.sep.setVisible(any_tile)
            set_text(self.foot, " · ".join(x for x in (h.get("who"), h.get("zone")) if x))
            return True

    _QT.update(Sparkline=Sparkline, RowView=RowView, MemberCard=MemberCard, SectionView=SectionView,
               TabView=TabView, HeroCard=HeroCard)
    return _QT


class WowPage:
    def __init__(self, app: Any) -> None:
        self.app = app
        W = self.W = app.W
        QtCore, QtWidgets = app.QtCore, app.QtWidgets
        Qt = QtCore.Qt
        qt = _qt_classes()
        self.state: dict = {}
        self._snap: dict = {}
        self._busy = False
        self._first_poll = True
        self._last_render = 0.0
        self.render_ms: list[float] = []            # UI-thread CPU time of the last renders (tests, /diag)
        self._dirty: set[str] = set()
        self._vm: dict = {}
        from treeaicoach.wow import optional  # noqa: PLC0415

        live_mod = optional("live")
        self.hub = live_mod.LiveHub() if live_mod is not None else None
        self.page = W.Page("WoW Forever", "Ton compagnon de jeu : l'étape, l'XP, le groupe, l'HV. "
                                          "Suit chaque /reload, et le direct avec /chatlog.")

        # -- « Maintenant »
        self.hero = qt["HeroCard"]()
        self.page.add(self.hero)

        # -- tabs (one bar; one TabView per tab, only the current one visible)
        bar = QtWidgets.QWidget()
        bl = QtWidgets.QVBoxLayout(bar)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(W.SP_2)
        self.tabs = W.Segmented(TABS, "trajet")
        self.tabs.set_choice_visible("donjon", False)
        self.tabs.changed.connect(self._show_tab)
        bl.addWidget(self.tabs, 0, Qt.AlignLeft)
        self.fresh = W.label(live_mod.freshness_text(None) if live_mod else "", "footnote", wrap=True)
        bl.addWidget(self.fresh)
        self.page.add(bar)
        self.plan_btn = W.button("Calculer", self._plan, kind="primary",
                                 tip="Calcule trois trajets avec tes quêtes (après un /reload en jeu).")
        eact = QtWidgets.QWidget()
        el = QtWidgets.QHBoxLayout(eact)
        el.setContentsMargins(0, 0, 0, 0)
        el.setSpacing(W.SP_2)
        self.err_open_btn = W.button("Ouvrir", self._open_errors,
                                     tip="Ouvre TreeAI-erreurs.txt (à envoyer pour une correction).")
        el.addWidget(self.err_open_btn)
        self.err_copy_btn = W.button("Copier", self._copy_errors, tip="Copie le contenu du fichier.")
        el.addWidget(self.err_copy_btn)
        self._actions = {"plan": self.plan_btn, "errors": eact}
        self.stack = QtWidgets.QWidget()
        sl = QtWidgets.QVBoxLayout(self.stack)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)
        self.tab_views: dict[str, Any] = {}
        for key, _title in TABS:
            tv = qt["TabView"](key, self._actions)
            sl.addWidget(tv)
            tv.hide()
            self.tab_views[key] = tv
        self.page.add(self.stack)
        self._plan_last: Any = None
        self._current = "trajet"

        # -- état : verdict + is WoW running (+ the master switch), the three checks, the install button
        self.state_section = top = self.page.section("État", "L'addon s'installe dans Interface/AddOns/TreeAI. "
                                                             "En jeu : /tai.")
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
        self._state_top: Any = None

        # -- coach switches
        self.coach_section = coach = self.page.section("Coach", "La voix répond quand tu dis « Hey coach ».")
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
        self._render_timer = QtCore.QTimer(self.page)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._render)
        self._warm_timer = QtCore.QTimer(self.page)
        self._warm_timer.setSingleShot(True)
        self._warm_timer.timeout.connect(self._warm)
        self._show_tab("trajet")
        self._render()

    # ------------------------------------------------------------------ tabs
    def _show_tab(self, key: str) -> None:
        """Show one tab: only a visibility flip (its content is already up to date, or brought up to date now
        by a diff). The segmented bar and the visible content always name the same tab."""
        key = TAB_ALIASES.get(str(key), str(key))
        if key not in self.tab_views or not self.tabs.choice_visible(key):
            key = "trajet"
        self._current = key
        for k, w in self.tab_views.items():
            on = k == key
            if w.isHidden() == on:
                w.setVisible(on)
        self.tabs.set_value(key)
        fresh = (self._vm.get("fresh") or {}).get(key)
        if fresh and self.fresh.text() != fresh:
            self.fresh.setText(fresh)
        if key in self._dirty and self._vm:
            self._apply_tab(key)

    def current_tab(self) -> str:
        return self._current

    def visible_tab(self) -> str:
        """The tab whose content is on screen (tests: it must always be :meth:`current_tab`)."""
        shown = [k for k, w in self.tab_views.items() if not w.isHidden()]
        return shown[0] if len(shown) == 1 else ""

    def _apply_tab(self, key: str) -> None:
        tabs = self._vm.get("tabs") or {}
        sigs = self._vm.get("tab_sig") or {}
        self.tab_views[key].apply(tabs.get(key) or [], sigs.get(key))
        self._dirty.discard(key)

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
        self.plan_btn.setText("Calcul…")
        path = str(getattr(self.app.cfg, "wow_path", "") or "")

        def job() -> Any:
            from treeaicoach.wow import planner  # noqa: PLC0415

            return planner.compute_and_write(install.find_wow(path), seconds=16.0)

        def done(res: Any) -> None:
            self.plan_btn.setEnabled(True)
            self.plan_btn.setText("Calculer")
            res = res if isinstance(res, dict) else {}
            ok = bool(res.get("ok"))
            if res.get("plan"):
                self._plan_last = res["plan"]
            self.app.show_toast(("Trajet calculé : tape /reload en jeu pour le charger." if ok
                                 else str(res.get("message") or "Calcul impossible.")), "info" if ok else "error")
            self.refresh()

        def failed(_exc: BaseException) -> None:
            self.plan_btn.setEnabled(True)
            self.plan_btn.setText("Calculer")

        self.app.run_job(job, done, failed, name="TreeAI-wow-plan")

    # ------------------------------------------------------------------ refresh
    def on_show(self) -> None:
        self.refresh()
        if not self.timer.isActive():
            self.timer.start(POLL_MS)

    def on_theme(self) -> None:
        """Colours changed (Réglages > Thème): repaint the pixmaps and the coloured lines."""
        try:
            for tv in self.tab_views.values():
                for v in tv.views.values():
                    v.recolor()
        except Exception:
            log.debug("wow page recolor failed", exc_info=True)

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
            self._on_state(state)

        def failed(_exc: BaseException) -> None:
            self._busy = False

        self.app.run_job(lambda: read_state(cfg, hub), done, failed, name="TreeAI-wow-state")

    def _on_state(self, state: dict) -> None:
        """A new state from the worker (UI thread): store it, toasts, and ONE render within ``RENDER_MIN_MS``."""
        self.state = state or {}
        if not self._first_poll:
            for text in live_toasts(self.state.get("events") or []):
                self.app.show_toast(text, "info")
        self._first_poll = False
        self._request_render()

    def _request_render(self) -> None:
        if self._render_timer.isActive():
            return
        wait = RENDER_MIN_MS - 1000 * (time.monotonic() - self._last_render)
        self._render_timer.start(max(0, int(wait)))

    def _render(self) -> None:
        """Apply the current state to the widgets: only what changed (signatures computed by the worker)."""
        t0 = time.thread_time()
        self._last_render = time.monotonic()
        try:
            self._render_state()
        finally:
            self.render_ms.append(1000 * (time.thread_time() - t0))
            del self.render_ms[:-50]

    def _render_state(self) -> None:
        W, st = self.W, self.state
        ov = overview(st, bool(getattr(self.app.cfg, "wow_enabled", True)))
        if self.state_row.title.text() != ov["headline"]:
            self.state_row.title.setText(ov["headline"])
        W.set_prop(self.state_row.title, "tone", ov["tone"])
        self.state_row.set_desc(ov["detail"])
        for key, done, text in ov["steps"]:
            row = self.steps[key]
            mark = OK_MARK if done else (BAD_MARK if done is False else WAIT_MARK)
            title = f"{mark}  {STEP_TITLES[key]}"
            if row.title.text() != title:
                row.title.setText(title)
            W.set_prop(row.title, "tone", "ok" if done else ("danger" if done is False else ""))
            row.set_desc(text)
        self.pick_btn.setVisible(bool(st) and not ov["found"])
        if self.install_btn.text() != ov["button"]:
            self.install_btn.setText(ov["button"])
        self.install_btn.setEnabled(ov["can_install"] and not getattr(self, "_installing", False))
        self.install_hint.setText("Puis /reload en jeu." if ov["found"] else "Indique d'abord le dossier de WoW.")
        self._place_state(not ov["ready"])
        self._snap = st.get("live") or {}
        vm = st.get("vm")
        if not isinstance(vm, dict):                    # tests / no hub: the same pure computation
            try:
                vm = page_vm({**st, "plan": st.get("plan") or self._plan_last})
            except Exception:
                log.debug("wow page view failed", exc_info=True)
                vm = {}
        self._vm = vm
        self.hero.apply(vm.get("hero") or {}, vm.get("hero_sig"))
        donjon = bool((vm.get("visible") or {}).get("donjon"))
        self.tabs.set_choice_visible("donjon", donjon)
        if self._current == "donjon" and not donjon:
            self._show_tab("trajet")
        self._dirty = set(TAB_KEYS)
        self._apply_tab(self._current)
        fresh = (vm.get("fresh") or {}).get(self._current) or ""
        if self.fresh.text() != fresh:
            self.fresh.setText(fresh)
        if self._dirty and not self._warm_timer.isActive():
            self._warm_timer.start(WARM_MS)

    def _warm(self) -> None:
        """Bring ONE hidden tab up to date per idle turn: a later switch is only a visibility flip."""
        for key in TAB_KEYS:
            if key in self._dirty and key != self._current:
                self._apply_tab(key)
                break
        self._dirty.discard(self._current)
        if self._dirty:
            self._warm_timer.start(WARM_MS)

    def _place_state(self, top: bool) -> None:
        """« État » at the top while something needs doing (install, folder…), else down with the settings."""
        if self._state_top is top:
            return
        self._state_top = top
        lay = self.page.lay
        lay.removeWidget(self.state_section)
        if top:
            lay.insertWidget(1, self.state_section)
        else:
            lay.insertWidget(lay.indexOf(self.coach_section), self.state_section)

    # ------------------------------------------------------------------ erreurs WoW
    def _errors_text(self) -> tuple[Any, str]:
        """(path, text) of TreeAI-erreurs.txt, written now from the last addon save when missing (a small file,
        on a click)."""
        from treeaicoach.wow import optional  # noqa: PLC0415

        errlog = optional("errlog")
        if errlog is None:
            return None, ""
        path = errlog.report_path()
        text = errlog.read_report()
        if not text:
            db = (self._snap or {}).get("db") or {}
            if errlog.write_report(db, force=True) is not None:
                text = errlog.read_report()
        return (path if text else None), text

    def _open_errors(self) -> None:
        from treeaicoach.wow import optional  # noqa: PLC0415

        path, _text = self._errors_text()
        errlog = optional("errlog")
        if path is None or errlog is None:
            self.app.show_toast(ERR_EMPTY, "info")
        elif not errlog.open_file(path):
            self.app.copy_text(str(path))
            self.app.show_toast(f"Fichier : {path} (chemin copié)", "info")

    def _copy_errors(self) -> None:
        _path, text = self._errors_text()
        if not text:
            self.app.show_toast(ERR_EMPTY, "info")
            return
        self.app.copy_text(text)
        self.app.show_toast("Erreurs WoW copiées.", "info")

    def on_close(self) -> None:
        self.timer.stop()
        self._render_timer.stop()
        self._warm_timer.stop()


def build(app: Any) -> WowPage:
    return WowPage(app)
