"""ARAM (map 12, also ARAM Mayhem "KIWI") analysis from the recorded Live Client data only (no minimap).

``analyze(record)`` -> dict (never raises): KDA, kill participation (damage proxy), death timing (first two
minutes, isolated deaths, deaths before the team, streaks), items vs the enemy comp (Blessures graves, MR /
armor), team comps (engage / poke / sustain) and 3 concrete tips. Pure function of the record.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

EARLY_S = 120.0            # a death in the first two minutes of the game
FIGHT_WINDOW_S = 12.0      # kills closer than this belong to the same fight
STREAK_WINDOW_S = 60.0     # 3 deaths within this: "mort en boucle"
ANTIHEAL = frozenset({3033, 3123, 6609, 3165, 3916, 3075, 3076})
MR_ITEMS = frozenset({3156, 3139, 3102, 3111, 3194, 3190, 4401, 2504, 3065, 8001, 3814, 6035, 3001})
ARMOR_ITEMS = frozenset({3157, 3143, 3075, 3110, 3026, 3047, 3071, 6333, 3742, 3068, 3109, 3076, 3082, 3036, 3153,
                         3193, 3050, 3190})
HEAL_MIN_ENEMIES = 2       # healers needed in the enemy team to ask for anti-heal


def _f(x: Any, d: float = 0.0) -> float:
    try:
        v = float(x)
        return v if v == v else d
    except (TypeError, ValueError):
        return d


def _short(name: Any) -> str:
    return str(name or "").split("#", 1)[0].strip().lower()


def _item_name(iid: int) -> str:
    try:
        from treeaicoach import itemization

        it = itemization.load_items().get(int(iid))
        return it.name if it else f"objet {iid}"
    except Exception:
        return f"objet {iid}"


def _comp(aliases: list[str]) -> dict[str, Any]:
    """engage / poke / sustain counts of a team (alias list) + a readable label list."""
    from treeaicoach import itemization as it

    engage = poke = sustain = 0
    for a in aliases:
        tags = it.champion_tags(a)
        cls = it.champion_class(a)
        if cls in ("tank", "support_tank") or (a in it.HEAVY_CC and "Tank" in tags):
            engage += 1
        if cls == "mage" or "Mage" in tags[:1] or a in ("Ezreal", "Jayce", "Varus", "Caitlyn", "Ashe"):
            poke += 1
        if a in it.HEALERS or a in it.SHIELDS or cls == "enchanter":
            sustain += 1
    return {"engage": engage, "poke": poke, "sustain": sustain, "size": len(aliases)}


def _deaths_info(events: list[dict], me: str, my_team_names: set[str], duration: float) -> dict[str, Any]:
    kills = [e for e in events if e.get("EventName") == "ChampionKill"]
    mine = [e for e in kills if _short(e.get("VictimName")) == me]
    ally_deaths = [e for e in kills if _short(e.get("VictimName")) in my_team_names]
    times = [_f(e.get("EventTime")) for e in mine]
    early = [t for t in times if t <= EARLY_S + 1.0]
    first_in_fight = isolated = 0
    for e in mine:
        t = _f(e.get("EventTime"))
        near = [a for a in ally_deaths if a is not e and abs(_f(a.get("EventTime")) - t) <= FIGHT_WINDOW_S]
        if not near:
            isolated += 1                                  # nobody of the team died near: picked off alone
            continue
        if all(_f(a.get("EventTime")) >= t for a in near):
            first_in_fight += 1                            # the first of the team to fall in a fight
    streak = 0
    for i in range(len(times)):
        n = sum(1 for t in times[i:] if t - times[i] <= STREAK_WINDOW_S)
        streak = max(streak, n)
    return {"count": len(times), "times": [round(t) for t in times], "early": len(early), "isolated": isolated,
            "first_in_fight": first_in_fight, "streak_60s": streak}


def analyze(record: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"available": False}
    try:
        return _analyze(record, out)
    except Exception:
        log.exception("aram analysis failed")
        return out


def _analyze(record: Any, out: dict[str, Any]) -> dict[str, Any]:
    r = record if isinstance(record, dict) else {}
    meta = r.get("meta") if isinstance(r.get("meta"), dict) else {}
    summ = r.get("summary") if isinstance(r.get("summary"), dict) else {}
    roster = [p for p in (r.get("roster") or []) if isinstance(p, dict)]
    events = [e for e in (r.get("events") or []) if isinstance(e, dict)]
    snaps = [s for s in (r.get("snapshots") or []) if isinstance(s, dict)]
    duration = _f(r.get("duration") or summ.get("duration"))
    k, d, a = (int(_f(summ.get(x))) for x in ("kills", "deaths", "assists"))
    me_p = next((p for p in roster if p.get("is_me")), {})
    me = _short(meta.get("riot_id") or meta.get("summoner_name") or me_p.get("riot_id"))
    team = str(meta.get("team") or me_p.get("team") or "").upper()
    allies = [p for p in roster if str(p.get("team") or "").upper() == team]
    enemies = [p for p in roster if p.get("team") and str(p.get("team")).upper() != team]
    ally_names = {_short(p.get("riot_id") or p.get("summoner_name")) for p in allies}
    enemy_names = {_short(p.get("riot_id") or p.get("summoner_name")) for p in enemies}
    ally_names.discard("")
    enemy_names.discard("")
    minutes = max(1.0, duration / 60.0)
    if not roster and not summ:
        return out

    team_kills = sum(1 for e in events if e.get("EventName") == "ChampionKill"
                     and _short(e.get("KillerName")) in ally_names)
    if team_kills <= 0:
        team_kills = max(k, 0)
    kp = min(100, round(100.0 * (k + a) / team_kills)) if team_kills else None
    deaths = _deaths_info(events, me, ally_names, duration)
    deaths["count"] = max(deaths["count"], d) if not events else deaths["count"]

    ally_alias = [str(p.get("alias") or "") for p in allies if p.get("alias")]
    enemy_alias = [str(p.get("alias") or "") for p in enemies if p.get("alias")]
    comp_a, comp_e = _comp(ally_alias), _comp(enemy_alias)

    from treeaicoach import itemization as it

    items = [int(i) for i in ((snaps[-1].get("items") if snaps else None) or []) if isinstance(i, int)]
    healers = [x for x in enemy_alias if x in it.HEALERS]
    strong = [x for x in enemy_alias if x in it.STRONG_HEALERS]
    ph = pm = 0.0
    for x in enemy_alias:
        sp, sm, st = it.damage_split(x)
        ph += sp + st
        pm += sm
    tot = max(1e-6, ph + pm)
    mag_share = pm / tot
    have = set(items)
    need: list[dict[str, Any]] = []
    my_alias = str(me_p.get("alias") or meta.get("champion") or "")
    cls = it.champion_class(my_alias) if my_alias else "fighter"
    if (len(healers) >= HEAL_MIN_ENEMIES or strong) and not have & ANTIHEAL:
        pick = next((i for i in it.NEED_ITEMS["antiheal"].get(cls, ()) if i not in have), 3033)
        need.append({"need": "antiheal", "item": pick, "name": _item_name(pick),
                     "why": f"{', '.join((strong or healers)[:3])} se soignent (Blessures graves)"})
    if mag_share >= 0.55 and cls not in ("mage", "assassin_ap") and not have & MR_ITEMS and len(items) >= 3:
        pick = next((i for i in it.NEED_ITEMS["magic"].get(cls, ()) if i not in have), 3156)
        need.append({"need": "magic", "item": pick, "name": _item_name(pick),
                     "why": f"{round(100 * mag_share)} % de dégâts magiques en face"})
    elif mag_share <= 0.35 and not have & ARMOR_ITEMS and len(items) >= 3:
        pick = next((i for i in it.NEED_ITEMS["physical"].get(cls, ()) if i not in have), 3143)
        need.append({"need": "physical", "item": pick, "name": _item_name(pick),
                     "why": f"{round(100 * (1 - mag_share))} % de dégâts physiques en face"})

    tips: list[str] = []
    if deaths["early"] >= 1 and d >= 2:
        tips.append(f"{deaths['early']} mort{'s' if deaths['early'] > 1 else ''} dans les 2 premières minutes : "
                    "au début, reste derrière tes sbires et laisse l'ennemi venir à toi.")
    if deaths["first_in_fight"] >= 2:
        tips.append(f"Tu es tombé en premier de ton équipe {deaths['first_in_fight']} fois : en combat, "
                    "reste derrière ta ligne de front et laisse un coéquipier engager avant toi.")
    if deaths["isolated"] >= 2:
        tips.append(f"{deaths['isolated']} morts seul, sans coéquipier près de toi : en ARAM la voie est étroite, "
                    "rejoins ton équipe dès que tu as vu partir les ennemis.")
    if need:
        n0 = need[0]
        tips.append(f"Objet à penser : {n0['name']} ({n0['why']}).")
    if comp_a["engage"] == 0 and comp_a["size"] >= 4:
        tips.append("Ton équipe n'a aucun engage : joue les échanges de portée et attends leur erreur plutôt que de rusher.")
    elif comp_e["poke"] >= 3 and comp_a["sustain"] == 0:
        tips.append("Les ennemis te harcèlent de loin : prends un objet de soin ou de vie et recule en soignant "
                    "(relique de soin) au lieu de rester sous leurs sorts.")
    if kp is not None and d >= 8 and kp < 45:
        tips.append(f"Tu participes à {kp} % des éliminations de ton équipe pour {d} morts : arrive avec l'équipe, "
                    "pas avant, et ne meurs plus avant le combat.")
    if not tips and d / minutes >= 0.8:
        tips.append(f"{d} morts en {int(minutes)} min : reste derrière ta ligne de front et recule dès qu'elle tombe.")
    if not tips:
        tips.append("Partie propre : continue de rester avec ton équipe et d'acheter selon la composition adverse.")
    while len(tips) < 3:                   # always three concrete reminders
        extra = ("Achète à chaque mort : l'ARAM te donne de l'or régulier, ne le garde pas.",
                 "Tue d'abord les sbires pour monter niveau, pas de combat à 3 contre 5 avant le niveau 6.",
                 "Après chaque combat gagné, pousse la vague puis recule : la tour est à portée des deux camps.")
        for e in extra:
            if e not in tips:
                tips.append(e)
                break
    out.update({
        "available": True, "champion": my_alias, "class": cls, "result": r.get("result"),
        "minutes": round(minutes, 1), "kills": k, "deaths_total": d, "assists": a,
        "kda": round((k + a) / max(1, d), 2), "team_kills": team_kills, "kill_participation": kp,
        "deaths": deaths, "items": items, "item_needs": need,
        "enemy_healers": healers, "enemy_magic_share": round(mag_share, 2),
        "comp_ally": comp_a, "comp_enemy": comp_e, "tips": tips[:3],
        "meta_note": "ARAM : règles de mode (pas de build U.GG ARAM).",
    })
    return out


# ---------------------------------------------------------------------------------- live (Live Client only)
class AramCoach:
    """Light in-game ARAM coaching from the Live Client data only (no minimap, no positions).

    ``update(gt, game)`` returns a short French sentence or None: buy advice when I die (anti-heal / MR /
    armor against the enemy comp), « rejoins ton équipe » when I respawn, « recule » when two allies fall
    within seconds while I am alive. Spaced by :data:`MIN_GAP_S`; nothing in the first minute. Never raises."""

    MIN_GAP_S = 40.0
    FIGHT_S = 10.0

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._was_dead = False
        self._last = -1e9
        self._seen = 0
        self._kind_t: dict[str, float] = {}

    def _gap(self, kind: str, gt: float, gap: float | None = None) -> bool:
        if gt - self._last < (self.MIN_GAP_S if gap is None else gap):
            return False
        if gt - self._kind_t.get(kind, -1e9) < 120.0:
            return False
        return True

    def _say(self, kind: str, gt: float, text: str) -> str:
        self._last = gt
        self._kind_t[kind] = gt
        return text

    def update(self, gt: float, game: Any) -> str | None:
        try:
            return self._update(float(gt), game)
        except Exception:
            log.debug("AramCoach failed", exc_info=True)
            return None

    def _update(self, gt: float, game: Any) -> str | None:
        me = getattr(game, "me", None)
        if me is None or gt < 60.0:
            return None
        dead = bool(me.is_dead)
        was, self._was_dead = self._was_dead, dead
        if dead and not was:
            buy = self._buy_advice(game, me)
            if buy and self._gap("buy", gt, 20.0):
                return self._say("buy", gt, buy)
            return None
        if was and not dead and self._gap("rejoin", gt, 15.0):
            return self._say("rejoin", gt, "Tu es de retour : rejoins ton équipe, ne passe pas devant seul.")
        if not dead:
            names = {_short(p.riot_id or p.summoner_name) for p in getattr(game, "allies", []) or []}
            names.discard("")
            recent = [e for e in (getattr(game, "events", None) or []) if isinstance(e, dict)
                      and e.get("EventName") == "ChampionKill" and _short(e.get("VictimName")) in names
                      and 0.0 <= gt - _f(e.get("EventTime"), -1e9) <= self.FIGHT_S]
            if len(recent) >= 2 and self._gap("retreat", gt):
                return self._say("retreat", gt, "Ton équipe tombe : recule derrière ta tour, ne reste pas seul.")
        return None

    @staticmethod
    def _buy_advice(game: Any, me: Any) -> str | None:
        from treeaicoach import itemization as it

        enemies = [str(p.champion_alias or "") for p in getattr(game, "enemies", []) or [] if p.champion_alias]
        have = {int(i) for i in (me.items or []) if isinstance(i, int)}
        cls = it.champion_class(str(getattr(me, "champion_alias", "") or ""))
        heal = [x for x in enemies if x in it.HEALERS]
        if (len(heal) >= HEAL_MIN_ENEMIES or any(x in it.STRONG_HEALERS for x in enemies)) and not have & ANTIHEAL:
            pick = next((i for i in it.NEED_ITEMS["antiheal"].get(cls, ()) if i not in have), 3033)
            return f"Tu es mort : achète {_item_name(pick)}, leur équipe se soigne beaucoup."
        pm = ph = 0.0
        for x in enemies:
            sp, sm, st = it.damage_split(x)
            ph += sp + st
            pm += sm
        share = pm / max(1e-6, ph + pm)
        if len(have) >= 3:
            if share >= 0.55 and cls not in ("mage", "assassin_ap") and not have & MR_ITEMS:
                pick = next((i for i in it.NEED_ITEMS["magic"].get(cls, ()) if i not in have), 3156)
                return f"Tu es mort : achète {_item_name(pick)}, ils font surtout de la magie."
            if share <= 0.35 and not have & ARMOR_ITEMS:
                pick = next((i for i in it.NEED_ITEMS["physical"].get(cls, ()) if i not in have), 3143)
                return f"Tu es mort : achète {_item_name(pick)}, ils font surtout du physique."
        if _f(getattr(me, "current_gold", 0)) >= 1000:
            return "Tu es mort : dépense ton or à la fontaine avant de repartir."
        return None
