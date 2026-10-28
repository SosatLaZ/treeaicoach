"""CLAIMS AUDIT: is every fact the coach SAID true? Rechecks the texts of a game record.

    python tools/claims_audit.py <record.json | games dir> [--truth DIR] [--show wrong|all] [--rule R]

Reads the record's alerts + macro calls (what reached the voice / HUD), extracts the factual claims
with regexes and checks each one against the record itself (Live Client events, my snapshots, the
minimap sightings) and, when a LCU truth file is there (``truth/<record>.truth.json`` next to the
record, or ``--truth``), against the true per-minute frames / kills / monsters.

One verdict per claim: ``ok`` / ``wrong`` / ``unverifiable`` (the data cannot say). Rules:

* ``level_named``  "Jhin est niveau 6 (pas toi / avant toi)": truth band of his level between the two
  frames around t; "pas toi" / "avant toi" wrong when MY level (snapshots, exact) is already >= L.
* ``level_vs``     "niveau 6 contre 5" / "tu es niveau 3, pas lui": mine from snapshots, his from truth.
* ``level_diff``   "X a 2 niveaux d'avance sur toi" / "Tu as 2 niveaux d'avance sur X" (truth).
* ``dead``         "X est mort", "X et Y sont morts", "leur jungler est mort" (events / truth kills +
  respawn timer); ``alive`` a named champion "arrive / vient / disparu" while dead.
* ``taken``        "ils ont le Baron", "ta tour du bas est tombée", "inhibiteur tombé" (events).
* ``timer``        "Dragon dans 55 s / une minute / 20 secondes" (spawn schedule rebuilt from the
  kills, objective_truth.instances): wrong when off by > 10 s or the objective is already up.
* ``score``        "11 kills à 16" (ChampionKill events, ±1).
* ``numbers``      "combat 5v4" = alive counts (±0, deaths + respawn timers).
* ``unseen``       "Nocturne caché depuis 75 s" vs the record's last sighting (±15 s).
* ``name``         a champion name that is not in this game, or the same name twice ("X et X").
* ``side``         Baron / Héraut / larves "en bas", dragon "en haut".
* ``pronoun``      "il" for a champion whose French name is feminine (Ahri ... il devient plus fort).
* ``gold``         "+12,3k or" vs the true total-gold gap of the alive players (sign only, truth).

Development tool; never imported by the application. Records are untrusted data: run with ``python -I``
from the repo root (``PYTHONPATH=.``) if the record folder is not trusted.
"""

from __future__ import annotations

import argparse
import bisect
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from treeaicoach import phase as PH  # noqa: E402

BRW = [10, 10, 12, 12, 14, 16, 20, 25, 28, 32.5, 35, 37.5, 40, 42.5, 45, 47.5, 50, 52.5]
XP_TOTAL = (0, 0, 280, 660, 1140, 1720, 2400, 3180, 4060, 5040, 6120, 7300, 8580, 9960, 11440, 13020,
            14700, 16480, 18360)
ME = "__me__"
OBJ_SIDE = {"baron": "haut", "herald": "haut", "grubs": "haut", "dragon": "bas", "elder": "bas"}
OBJ_WORD = (("dragon ancestral", "elder"), ("ancien", "elder"), ("dragon", "dragon"), ("baron", "baron"),
            ("héraut", "herald"), ("larves", "grubs"))


def respawn_s(level: int, gt: float, mode: str = "CLASSIC") -> float:
    """Death timer (s). Fitted on the recorded player's own deaths (snapshots ``is_dead``, 40 real
    games): CLASSIC = the base table + ~0.7 %/min after 15:00; SWIFTPLAY ~ 2 x level + 4."""
    lv = max(1, min(18, int(level or 1)))
    if str(mode).upper() == "SWIFTPLAY":
        return max(10.0, 2.0 * lv + 4.0)
    b = BRW[lv - 1]
    if gt > 900:
        b *= 1.0 + min(0.5, (gt / 60.0 - 15.0) * 0.007)
    return b


def _champ_meta() -> dict:
    try:
        with open(os.path.join(ROOT, "treeaicoach", "assets", "champion_meta.json"), encoding="utf-8") as f:
            return json.load(f).get("champions") or {}
    except Exception:
        return {}


def _norm(s: Any) -> str:
    return "".join(ch for ch in str(s or "").lower() if ch.isalnum())


class Game:
    """Indexed facts of one record (+ optional truth)."""

    def __init__(self, rec: dict, truth: dict | None, meta: dict) -> None:
        self.rec, self.truth = rec, truth
        roster = rec.get("roster") or []
        self.me = ME
        self.my_team = next((r.get("team") for r in roster if r.get("is_me")), (rec.get("meta") or {}).get("team"))
        # keyed by alias, except me = ME (mirror games: "Yone" in a line is the OTHER Yone)
        my_tm = next((r.get("team") for r in roster if r.get("is_me")), None)
        dup = Counter(r.get("alias") for r in roster)

        def key(r: dict) -> str:
            if r.get("is_me"):
                return ME
            a = str(r.get("alias"))
            return a if dup[a] == 1 or r.get("team") != my_tm else a + "@ally"

        self.team = {key(r): r.get("team") for r in roster}
        self.role = {key(r): r.get("position") for r in roster}
        # display name -> alias (French name, alias, both normalized)
        self.by_name: dict[str, str] = {}
        for r in sorted(roster, key=lambda r: (bool(r.get("is_me")), r.get("team") == my_tm)):  # a mirror name = the enemy
            for n in (r.get("name"), r.get("alias")):
                if n and str(n) not in self.by_name:
                    self.by_name[str(n)] = key(r)
        self.aliases = {r.get("alias") for r in roster}
        self.names_sorted = sorted(self.by_name, key=len, reverse=True)
        self.all_names = {}
        for alias, c in meta.items():
            if c.get("name"):
                self.all_names[str(c["name"])] = alias
        self.gender = {alias: c.get("g") for alias, c in meta.items()}
        # who is who in the events (riot id / summoner name -> alias)
        self.who: dict[str, str] = {}
        for r in roster:
            for k in (r.get("riot_id"), r.get("summoner_name")):
                if k:
                    self.who[str(k)] = key(r)
                    self.who[str(k).split("#", 1)[0]] = key(r)
        snaps = rec.get("snapshots") or []
        self.snap_t = [float(s.get("game_time") or 0.0) for s in snaps]
        self.snap = snaps
        self.events = [e for e in rec.get("events") or [] if isinstance(e, dict)]
        self.sight = {k: sorted(float(p[0]) for p in v if isinstance(p, list) and p)
                      for k, v in (rec.get("sightings") or {}).items()}
        # deaths: (alias, t, until)
        self.deaths: list[tuple[str, float, float, float]] = []      # (who, t, earliest / latest respawn)
        self.mode = str((rec.get("meta") or {}).get("game_mode") or "CLASSIC")
        self.tr = None
        if truth:
            self.tr = truth
            parts = {str(p["id"]): p for p in truth.get("participants") or []}
            me_pid = str(truth.get("me"))
            self.pid = {ME: me_pid}
            for pid, p in parts.items():
                if pid != me_pid:
                    a = str(p.get("alias"))
                    self.pid[a if p.get("team") != self.my_team or dup[a] == 1 else a + "@ally"] = pid
            for k in truth.get("kills") or []:
                t, victim = float(k[0]), str(k[2])
                p = parts.get(victim)
                if p is None:
                    continue
                a = next((k for k, v in self.pid.items() if v == victim), p.get("alias"))
                lv = self.truth_level(a, t)[0] or 1
                u = t + respawn_s(lv, t, self.mode)
                self.deaths.append((a, t, u, u))
        else:
            for e in self.events:
                if e.get("EventName") == "ChampionKill":
                    a = self.who.get(str(e.get("VictimName") or ""))
                    if a:
                        t = float(e.get("EventTime") or 0.0)
                                # no levels of the others in a record: [mine, mine + 3] (a support is the lowest)
                        lv = self.my_level(t) or 1
                        hi = min(18, lv + (0 if a == ME else 3))
                        self.deaths.append((a, t, t + respawn_s(lv, t, self.mode), t + respawn_s(hi, t, self.mode)))

    # ---- levels
    def my_level(self, t: float) -> int | None:
        i = bisect.bisect_right(self.snap_t, t) - 1
        return int(self.snap[i].get("level") or 0) if i >= 0 else None

    def truth_level(self, alias: Any, t: float) -> tuple[int | None, int | None]:
        """(level at the frame before t, level at the frame after t)."""
        if not self.tr:
            return None, None
        pid = getattr(self, "pid", {}).get(alias)
        if pid is None:
            return None, None
        lo = hi = None
        frames = self.tr.get("frames") or []
        for i, (ft, row) in enumerate(frames):
            if ft <= t and pid in row:
                lo = int(row[pid][5])
                nxt = frames[i + 1][1] if i + 1 < len(frames) else row
                hi = int(nxt.get(pid, row[pid])[5])
        return lo, hi

    # ---- deaths
    def dead(self, alias: str, t: float, slack: float = 3.0) -> bool | None:
        if not self.deaths and not self.tr:
            return None
        return any(a == alias and k - slack <= t < u + slack for a, k, _l, u in self.deaths)

    def dead_strict(self, alias: str, t: float) -> bool:
        return any(a == alias and k + 2.0 <= t < u - 6.0 for a, k, u, _h in self.deaths)

    def remaining(self, alias: str, t: float) -> float | None:
        r = [u - t for a, k, u, _h in self.deaths if a == alias and k <= t < u]
        return max(r) if r else None

    def alive_count(self, team: str, t: float) -> int:
        return sum(1 for a, tm in self.team.items() if tm == team and not self.dead_strict(a, t))

    def score(self, t: float) -> tuple[int, int] | None:
        """(our kills, their kills) at t: truth kills, else the record's events when they are complete
        (recording from the start, every victim known)."""
        if self.tr:
            parts = {str(p["id"]): p.get("team") for p in self.tr.get("participants") or []}
            v = [parts.get(str(k[2])) for k in self.tr.get("kills") or [] if float(k[0]) <= t]
            return sum(1 for x in v if x and x != self.my_team), sum(1 for x in v if x == self.my_team)
        if float((self.rec.get("meta") or {}).get("start_game_time") or 0.0) > 5.0:
            return None
        ours = theirs = 0
        for e in self.events:
            if e.get("EventName") == "ChampionKill" and float(e.get("EventTime") or 0) <= t:
                v = self.team.get(self.who.get(str(e.get("VictimName") or "")))
                if v is None:
                    return None
                ours += v != self.my_team
                theirs += v == self.my_team
        return ours, theirs

    # ---- names in a text
    def names_in(self, text: str) -> list[str]:
        out, s = [], text
        for n in self.names_sorted:
            if re.search(r"(?<![\w'])" + re.escape(n) + r"(?![\w'])", s):
                out.append(self.by_name[n])
                s = s.replace(n, " ")
        return out

    def foreign_names(self, text: str) -> list[str]:
        out = []
        for n, alias in self.all_names.items():
            if alias in self.aliases or len(n) < 4:
                continue
            if re.search(r"(?<![\w'])" + re.escape(n) + r"(?![\w'])", text):
                if not any(n in m and n != m for m in self.by_name):      # "Vi" inside "Viktor"...
                    out.append(n)
        return out

    def monsters(self) -> list[list]:
        if self.tr:
            return [m for m in self.tr.get("monsters") or [] if isinstance(m, list) and len(m) >= 5]
        out = []
        for e in self.events:
            n = e.get("EventName")
            typ = {"DragonKill": "DRAGON", "BaronKill": "BARON_NASHOR", "HordeKill": "HORDE",
                   "HeraldKill": "RIFTHERALD"}.get(n)
            if typ is None:
                continue
            killer = self.who.get(str(e.get("KillerName") or ""))
            team = self.team.get(killer, "")
            sub = str(e.get("DragonType") or "")
            if typ == "DRAGON" and sub.lower() == "elder":
                typ = "ELDER_DRAGON"
            out.append([float(e.get("EventTime") or 0.0), 0, team, typ, sub.upper()])
        return out


def texts(rec: dict) -> list[tuple[float, str, str]]:
    """(t, kind, text) said / shown, deduplicated (a macro call is also its macro_tip)."""
    out, seen = [], set()
    for a in rec.get("alerts") or []:
        try:
            t, kind = float(a[0]), str(a[1])
            text = a[3] if isinstance(a[2], int) else a[2]
        except (TypeError, ValueError, IndexError):
            continue
        out.append((t, kind, str(text or "")))
    for c in rec.get("calls") or []:
        try:
            out.append((float(c[0]), "call:" + str(c[1]), str(c[2] or "")))
        except (TypeError, ValueError, IndexError):
            continue
    res = []
    for t, k, x in sorted(out):
        key = (round(t), x.rstrip(" .!"))
        if key in seen:
            continue
        seen.add(key)
        res.append((t, k, x))
    return res


def _spawns(g: Game) -> list[dict]:
    try:
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        from objective_truth import instances

        mode = str((g.rec.get("meta") or {}).get("game_mode") or "CLASSIC")
        return instances(g.monsters(), float(g.rec.get("duration") or 3600.0) + 600.0, mode)
    except Exception:
        return []


def audit(rec: dict, truth: dict | None = None, meta: dict | None = None) -> list[dict]:
    """One dict per claim: {t, kind, text, rule, verdict, detail}."""
    g = Game(rec, truth, meta if meta is not None else _champ_meta())
    spawns = _spawns(g)
    rows: list[dict] = []
    enemy_team = "CHAOS" if g.my_team == "ORDER" else "ORDER"

    def add(t, kind, text, rule, verdict, detail=""):
        rows.append({"t": round(t, 1), "kind": kind, "text": text, "rule": rule, "verdict": verdict,
                     "detail": detail})

    for t, kind, text in texts(rec):
        names = g.names_in(text)
        # ---------------------------------------------------------------- names
        foreign = g.foreign_names(text)
        if foreign:
            add(t, kind, text, "name", "wrong", f"pas dans la partie: {foreign}")
        m = re.search(r"([A-ZÀ-Ý][\w' .]+?) et ([A-ZÀ-Ý][\w' .]+?) (se|sont|ont|font|arrivent)", text)
        if m and m.group(1).strip() == m.group(2).strip():
            add(t, kind, text, "name", "wrong", "même nom deux fois")
        # ---------------------------------------------------------------- pronoun
        if re.search(r"\bil devient plus fort\b|\bil a\b|\bÉvite-le\b", text) and names:
            a = names[0]
            if g.gender.get(a) == "f":
                add(t, kind, text, "pronoun", "wrong", f"{a} est féminin")
        # ---------------------------------------------------------------- levels
        m = re.search(r"(.+?) est niveau (\d+)", text)
        if m:
            who = g.names_in(m.group(1))
            if not who and "jungler" in m.group(1).lower():
                who = [a for a, r in g.role.items() if r == "JUNGLE" and g.team.get(a) == enemy_team][:1]
            L = int(m.group(2))
            mine = g.my_level(t)
            bad = []
            if re.search(r"pas toi|avant toi", text) and mine is not None and mine >= L:
                bad.append(f"moi déjà niveau {mine}")
            if who:
                lo, hi = g.truth_level(who[-1], t)
                if lo is not None and not (lo <= L <= hi):
                    bad.append(f"vrai niveau {lo}..{hi}")
                elif lo is None and not bad:
                    add(t, kind, text, "level_named", "unverifiable", "pas de vérité")
                    continue
                if "avant le vôtre" in text:
                    ally_jg = [a for a, r in g.role.items() if r == "JUNGLE" and g.team.get(a) == g.my_team]
                    alo, _ahi = g.truth_level(ally_jg[0], t) if ally_jg else (None, None)
                    if alo is not None and alo >= L:
                        bad.append(f"ton jungler déjà niveau {alo}")
            elif not bad:
                add(t, kind, text, "level_named", "unverifiable", "champion non reconnu")
                continue
            add(t, kind, text, "level_named", "wrong" if bad else "ok", "; ".join(bad))
        m = re.search(r"niveau (\d+) contre (\d+)", text) or re.search(r"tu es niveau (\d+), pas lui", text)
        if m:
            A = int(m.group(1))
            B = int(m.group(2)) if m.lastindex and m.lastindex >= 2 else None
            mine = g.my_level(t)
            bad = []
            if mine is not None and mine != A:
                bad.append(f"mon niveau {mine}")
            opp = names[0] if names else None
            lo, hi = g.truth_level(opp, t) if opp else (None, None)
            if lo is not None:
                if B is not None and not (lo <= B <= hi):
                    bad.append(f"vrai niveau de {opp} {lo}..{hi}")
                if B is None and lo >= A:
                    bad.append(f"{opp} déjà niveau {lo}")
            add(t, kind, text, "level_vs", "wrong" if bad else ("ok" if lo is not None else "unverifiable"),
                "; ".join(bad))
        m = re.search(r"(.+?) a (\d+) niveaux? d'avance sur toi", text) or \
            re.search(r"Tu as (\d+) niveaux? d'avance sur (.+?)\s*:", text)
        if m:
            if text.startswith("Tu as"):
                n, who, sign = int(m.group(1)), g.names_in(m.group(2)), -1
            else:
                n, who, sign = int(m.group(2)), g.names_in(m.group(1)), 1
            lo, hi = g.truth_level(who[-1], t) if who else (None, None)
            mlo, mhi = g.truth_level(g.me, t)
            if lo is None or mlo is None:
                add(t, kind, text, "level_diff", "unverifiable")
            else:
                diffs = {sign * (a - b) for a in (lo, hi) for b in (mlo, mhi)}
                ok = min(diffs) <= n <= max(diffs) or (min(diffs) <= n + 0 and max(diffs) >= n)
                add(t, kind, text, "level_diff", "ok" if ok else "wrong", f"écart vrai {sorted(diffs)}")
        # ---------------------------------------------------------------- dead / alive
        m = re.search(r"(?:^|: |, )([^:,]+?) (?:est mort|sont morts)", text)
        if m and "Mort face" not in text:
            who = g.names_in(m.group(1))
            if "leur jungler" in m.group(1).lower():
                who = [a for a, r in g.role.items() if r == "JUNGLE" and g.team.get(a) == enemy_team]
            if who:
                alive = [a for a in who if g.dead(a, t) is False]
                if g.dead(who[0], t) is None:
                    add(t, kind, text, "dead", "unverifiable")
                else:
                    add(t, kind, text, "dead", "wrong" if alive else "ok", f"vivants: {alive}" if alive else "")
                mt = re.search(r"\((\d+) s\)", text)
                if mt and not alive:
                    rem = min((g.remaining(a, t) or 0.0) for a in who)
                    add(t, kind, text, "dead_timer", "ok" if abs(rem - int(mt.group(1))) <= 12 else "wrong",
                        f"reste {rem:.0f} s")
        if re.search(r"\b(arrive|vient vers toi|a disparu|disparu)\b", text) and names and g.deaths:
            dead = [a for a in names if g.dead_strict(a, t)]
            add(t, kind, text, "alive", "wrong" if dead else "ok", f"mort: {dead}" if dead else "")
        # ---------------------------------------------------------------- taken objectives / structures
        if re.search(r"ils ont le Baron", text):
            got = [e for e in g.events if e.get("EventName") == "BaronKill"
                   and 0 <= t - float(e.get("EventTime") or 0) <= 185
                   and g.team.get(g.who.get(str(e.get("KillerName") or ""))) == enemy_team]
            add(t, kind, text, "taken", "ok" if got else "wrong", "" if got else "pas de Baron ennemi < 3 min")
        m = re.search(r"(ta|leur) tour du (bas|haut|milieu) est (tombée|prise)", text)
        if m:
            team = g.my_team if m.group(1) == "ta" else enemy_team
            lane = {"bas": "bot", "haut": "top", "milieu": "mid"}[m.group(2)]
            down = [e for e in g.events if e.get("EventName") == "TurretKilled"
                    and float(e.get("EventTime") or 0) <= t + 2
                    and (PH.parse_turret(e.get("TurretKilled")) or ("", "", 0))[:2] == (team, lane)]
            add(t, kind, text, "taken", "ok" if down else "wrong", "" if down else "aucune tour tombée là")
        if re.search(r"inhibiteur tombé", text):
            down = [e for e in g.events if e.get("EventName") == "InhibKilled" and float(e.get("EventTime") or 0) <= t + 2
                    and PH.structure_owner(e.get("InhibKilled")) == g.my_team]
            add(t, kind, text, "taken", "ok" if down else "wrong")
        # ---------------------------------------------------------------- timers
        m = re.search(r"(Dragon ancestral|Dragon|dragon|Baron|Héraut|larves)[^.:]*? (?:dans|apparaissent dans) "
                      r"(une minute|\d+ secondes|\d+ s)", text)
        if m and spawns:
            word = m.group(1).lower()
            key = next(k for w, k in OBJ_WORD if word.startswith(w))
            n = 60 if m.group(2) == "une minute" else int(re.match(r"\d+", m.group(2)).group(0))
            when = t + n
            cand = [s for s in spawns if s["key"] in ((key,) if key not in ("dragon", "elder") else ("dragon", "elder"))]
            up = [s for s in cand if s["spawn"] <= t - 5 and (s["take"] is None or s["take"] > t)]
            err = min((abs(s["spawn"] - when) for s in cand), default=None)
            if up:
                add(t, kind, text, "timer", "wrong", f"déjà là depuis {t - up[0]['spawn']:.0f} s")
            elif err is None:
                add(t, kind, text, "timer", "unverifiable")
            else:
                add(t, kind, text, "timer", "ok" if err <= 10 else "wrong", f"écart {err:.0f} s")
            side = re.search(r"\ben (haut|bas)\b", text)
            if side and OBJ_SIDE.get(key) and side.group(1) != OBJ_SIDE[key]:
                add(t, kind, text, "side", "wrong", f"{key} est {OBJ_SIDE[key]}")
        m = re.search(r"Va en (haut|bas) : (Baron|dragon|Dragon|Héraut|larves)", text)
        if m:
            key = next(k for w, k in OBJ_WORD if m.group(2).lower().startswith(w))
            add(t, kind, text, "side", "ok" if OBJ_SIDE[key] == m.group(1) else "wrong")
        # ---------------------------------------------------------------- score / numbers
        m = re.search(r"(\d+) kills à (\d+)", text)
        if m:
            sc = g.score(t)
            if sc is None:
                add(t, kind, text, "score", "unverifiable", "événements incomplets")
            else:
                ours, theirs = sc
                a, b = int(m.group(1)), int(m.group(2))
                ok = abs(a - ours) <= 1 and abs(b - theirs) <= 1
                add(t, kind, text, "score", "ok" if ok else "wrong", f"vrai {ours} à {theirs}")
        m = re.search(r"combat (\d)v(\d)", text)
        if m and g.tr:
            us, them = g.alive_count(g.my_team, t), g.alive_count(enemy_team, t)
            ok = (int(m.group(1)), int(m.group(2))) == (us, them)
            add(t, kind, text, "numbers", "ok" if ok else "wrong", f"vivants {us}v{them}")
        # ---------------------------------------------------------------- unseen
        m = re.search(r"(.+?) caché depuis (\d+) s", text)
        if m:
            who = g.names_in(m.group(1).split(":")[-1])
            if who and who[0] in g.sight:
                st = g.sight[who[0]]
                i = bisect.bisect_right(st, t) - 1
                real = t - st[i] if i >= 0 else t
                n = int(m.group(2))
                add(t, kind, text, "unseen", "ok" if abs(real - n) <= 15 or (real > n and n >= 120) else "wrong",
                    f"vu il y a {real:.0f} s")
            else:
                add(t, kind, text, "unseen", "unverifiable")
        # ---------------------------------------------------------------- gold
        m = re.search(r"([+−-])(\d+(?:,\d)?)k or", text)
        if m and g.tr:
            add(t, kind, text, "gold", "unverifiable", "or d'objets des vivants : pas d'objets dans la vérité")
    return rows


def _truth_for(path: str, truth_dir: str | None) -> dict | None:
    base = os.path.basename(path)[:-5]
    for d in [truth_dir] if truth_dir else [os.path.join(os.path.dirname(path), "truth")]:
        if not d:
            continue
        p = os.path.join(d, base + ".truth.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    return None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("path")
    ap.add_argument("--truth", default=None)
    ap.add_argument("--show", default="wrong", choices=("wrong", "all", "none"))
    ap.add_argument("--rule", default=None)
    ap.add_argument("--min-version", default=None, help="only records of this app version or newer")
    a = ap.parse_args(argv)
    files = sorted(glob.glob(os.path.join(a.path, "*.json"))) if os.path.isdir(a.path) else [a.path]
    meta = _champ_meta()
    total: Counter = Counter()
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                rec = json.load(fh)
        except Exception:
            continue
        if not isinstance(rec, dict) or "alerts" not in rec:
            continue
        ver = str((rec.get("meta") or {}).get("app_version") or "")
        if a.min_version and tuple(int(x) for x in re.findall(r"\d+", ver)[:3]) < \
                tuple(int(x) for x in re.findall(r"\d+", a.min_version)[:3]):
            continue
        truth = _truth_for(f, a.truth)
        rows = audit(rec, truth, meta)
        c: Counter = Counter((r["rule"], r["verdict"]) for r in rows)
        total.update(c)
        wrong = sum(v for (r, vd), v in c.items() if vd == "wrong")
        print(f"== {os.path.basename(f)} v{ver}{' +vérité' if truth else ''}: {len(rows)} faits, {wrong} faux")
        for r in rows:
            if a.rule and r["rule"] != a.rule:
                continue
            if a.show == "all" or (a.show == "wrong" and r["verdict"] == "wrong"):
                mm, ss = divmod(int(r["t"]), 60)
                print(f"  {mm:02d}:{ss:02d} {r['rule']:<12} {r['verdict']:<12} {r['text'][:70]!r} {r['detail']}")
    by = defaultdict(Counter)
    for (rule, vd), n in total.items():
        by[rule][vd] += n
    print("rule          ok  wrong  unverif")
    for rule in sorted(by):
        c = by[rule]
        print(f"{rule:<12} {c['ok']:>4} {c['wrong']:>6} {c['unverifiable']:>8}")
    print(f"TOTAL wrong {sum(c['wrong'] for c in by.values())} / {sum(sum(c.values()) for c in by.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
