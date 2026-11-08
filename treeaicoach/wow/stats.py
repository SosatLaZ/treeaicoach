"""Measured play stats exported by the addon's ``Stats`` module, for TreeAI's WoW page (Session tab).

The addon (``Modules/Stats.lua``, « Plus → Stats → Exporter pour TreeAI », also at every /reload and logout)
writes a compact snapshot::

    TreeAIDB.chars["Nom-Royaume"].summary.stats = {
        v = 1, t = <time()>, played = <minutes measured>,
        sheet = {str, agi, sta, int, spi, ap, rap, crit, rcrit, scrit, hit, haste, armor, dodge, parry, block,
                 res = {fire, nature, frost, shadow, arcane}, speed, dmg = {lo, hi, speed}, rdmg = {...},
                 mp5, mp5c, hp, mp, ilvl, t},
        kill = {["-2"] = s, ["0"] = s, ["2"] = s, ...}, killElite = s, fights = n, dmgPct = % HP per fight,
        downSec = rest s per fight, downShare = 0..1, deaths = n, deathsH = per hour, deathTop = "name",
        xph = {q, g, d, all, recent}, goldH = copper/h, questMin, questA2C, questC2T, quests = n,
        speed = {w, m}  (yd/s walking / mounted), trans = {{"A>B", s}}, perH = {ammo, food, water, repair},
        ah = {rate, hours, n}, dropMobs = n,
    }

Pure functions; never raise (``log.debug`` on errors); French strings; never show a number we don't have.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def _d(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def _money(copper: float) -> str:
    c = int(round(abs(copper)))
    g, s = c // 10000, (c // 100) % 100
    sign = "-" if copper < 0 else ""
    if g:
        return f"{sign}{g} po {s:02d} pa" if s else f"{sign}{g} po"
    return f"{sign}{s} pa" if s else f"{sign}{c % 100} pc"


def _k(v: float) -> str:
    return f"{round(v / 1000)}k" if v >= 10000 else f"{round(v / 1000, 1)}k".replace(".", ",")


def _dec(v: float) -> str:
    return f"{round(v, 1)}".replace(".", ",").removesuffix(",0")


def read_stats(summary: Any) -> dict:
    """The ``summary.stats`` table of one character (``{}`` if absent or from an unknown schema)."""
    st = _d(_d(summary).get("stats"))
    return st if st.get("v") == 1 else {}


def stats_rows(summary: Any) -> list[tuple[str, str]]:
    """(title, value) rows for the Session tab, only for what was really measured."""
    rows: list[tuple[str, str]] = []
    try:
        st = read_stats(summary)
        if not st:
            return rows
        sh = _d(st.get("sheet"))
        parts = [f"{lab} {int(sh[k])}" for k, lab in (("str", "For"), ("agi", "Agi"), ("sta", "End"),
                                                       ("int", "Int"), ("spi", "Esp")) if _f(sh.get(k)) is not None]
        if _f(sh.get("ilvl")):
            parts.append(f"niv. obj. {round(_f(sh['ilvl']))}")
        if parts:
            rows.append(("Fiche", " · ".join(parts)))
        att = []
        ap, rap = _f(sh.get("ap")), _f(sh.get("rap"))
        if rap and (not ap or rap > ap):
            att.append(f"PA dist. {int(rap)}")
        elif ap:
            att.append(f"PA {int(ap)}")
        crit = _f(sh.get("rcrit")) if rap and (not ap or rap > ap) else _f(sh.get("crit"))
        if crit is not None:
            att.append(f"crit {_dec(crit)} %")
        if _f(sh.get("armor")):
            att.append(f"armure {int(_f(sh['armor']))}")
        if att:
            rows.append(("Attaque", " · ".join(att)))
        kill = _d(st.get("kill"))
        fight = []
        if _f(kill.get("0")):
            fight.append(f"~{round(_f(kill['0']))} s même niveau")
        if _f(kill.get("2")):
            fight.append(f"~{round(_f(kill['2']))} s à +2")
        if _f(st.get("dmgPct")) is not None:
            fight.append(f"{round(_f(st['dmgPct']))} % PV perdus / combat")
        if fight:
            rows.append(("Temps de kill", " · ".join(fight)))
        xph = _d(st.get("xph"))
        xp = [f"{lab} {_k(_f(xph[k]))}" for k, lab in (("q", "quêtes"), ("g", "farm"), ("d", "donjon"))
              if _f(xph.get(k))]
        if xp:
            rows.append(("XP/h mesurée", " · ".join(xp)))
        if _f(st.get("goldH")) is not None:
            rows.append(("Or / heure", _money(_f(st["goldH"]))))
        rest = []
        if _f(st.get("downSec")) is not None:
            rest.append(f"repos {round(_f(st['downSec']))} s / combat")
        if _f(st.get("deathsH")) is not None:
            rest.append(f"morts {_dec(_f(st['deathsH']))}/h")
        if st.get("deathTop"):
            rest.append(f"souvent : {str(st['deathTop'])[:24]}")
        if rest:
            rows.append(("Repos et morts", " · ".join(rest)))
        sp = _d(st.get("speed"))
        mv = []
        if _f(sp.get("w")):
            mv.append(f"{_dec(_f(sp['w']))} m/s à pied")
        if _f(sp.get("m")):
            mv.append(f"{_dec(_f(sp['m']))} m/s monté")
        if _f(st.get("questMin")):
            mv.append(f"quête ~{round(_f(st['questMin']))} min")
        if mv:
            rows.append(("Trajets", " · ".join(mv)))
        ph = _d(st.get("perH"))
        use = []
        if (_f(ph.get("ammo")) or 0) > 0:
            use.append(f"{round(_f(ph['ammo']))} munitions")
        if (_f(ph.get("food")) or 0) > 0:
            use.append(f"{_dec(_f(ph['food']))} nourriture")
        if (_f(ph.get("water")) or 0) > 0:
            use.append(f"{_dec(_f(ph['water']))} boisson")
        if (_f(ph.get("repair")) or 0) > 0:
            use.append(f"réparations {_money(_f(ph['repair']))}")
        if use:
            rows.append(("Par heure", " · ".join(use)))
        ah = _d(st.get("ah"))
        if _f(ah.get("rate")) is not None:
            hrs = f" en ~{round(_f(ah['hours']))} h" if _f(ah.get("hours")) else ""
            rows.append(("Hôtel des ventes", f"{round(_f(ah['rate']) * 100)} % vendu{hrs}"))
    except Exception as exc:  # never raise in the app path
        log.debug("wow stats rows: %s", exc)
    return rows
