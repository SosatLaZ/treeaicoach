"""Eval set for the WoW « Hey coach » answers: fake live data + 30 representative questions → expected key facts.

The player: level-28 Horde hunter (WoW: Forever, frFR) in Orneval, group of 4 (priest 27, warrior 28, mage 27).
Scenarios:
  * ``full``  — /reload 6 min ago + live chat log (46 min of XP), safe AH deal, errors, app plan;
  * ``stale`` — same character, last /reload 2 h ago, no chat log: volatile data must disappear, the rest is labelled;
  * ``empty`` — no addon data at all: the coach must say it has no data (never a number);
  * ``noah``  — like ``full`` but the AH found nothing safe (reasons given);
  * ``duo``   — like ``full`` but only 2 players (hunter + priest).

Each case: ``q`` (the question), ``scenario``, ``facts`` (substrings that MUST be in the JSON state sent to the
model), ``absent`` (substrings that must NOT be there), ``local`` (substrings expected in the answer said without
AI). Player names (``NAMES``) must never appear anywhere. Used by tests/wow/test_ai_coach.py and by
tools/wow_ai_eval.py (the same questions against the real model)."""

from __future__ import annotations

import copy
from typing import Any

NOW = 1_794_200_000.0                     # fixed « now » (epoch seconds)
ME, REALM, GUILD = "Fakehunt", "Fakerealm", "Fakeguild"
NAMES = (ME, REALM, GUILD, "Fakepriest", "Fakewar", "Fakemage")
KEY = f"{ME}-{REALM}"


def _summary(updated: float) -> dict[str, Any]:
    return {
        "class": "HUNTER", "className": "Chasseur", "race": "Orc", "level": 28,
        "xp": 23000, "xpMax": 41400, "rested": 4140, "zone": "Orneval", "subzone": "Poste de Bois-Brisé",
        "money": 352075, "bagsFree": 6, "bagsTotal": 56, "guild": GUILD, "inGroup": True, "groupSize": 4,
        "updated": updated,
        "leveling": {"faction": "Horde", "hint": "Mille pointes (Librevent) et Orneval (Bois-Brisé)", "name": ME,
                     "realm": REALM, "trainPending": 30},
        "quests": {"count": 14, "complete": 2, "list": [
            {"id": 6441, "title": "Le cristal de Satyr", "level": 26, "complete": True, "obj": []},
            {"id": 6462, "title": "Totems de la tribu", "level": 25, "complete": True, "obj": []},
            {"id": 6503, "title": "Les Écorchemousses", "level": 27, "obj": ["Écorchemousse tué : 6/12"]},
            {"id": 1110, "title": "Les rocs de la course", "level": 30, "obj": ["Roc de course : 0/8"]},
            {"id": 1111, "title": "Le carburant des gobelins", "level": 31, "obj": ["Pillard tué : 0/10"]},
            {"id": 6621, "title": "Le roi des Furbolgs", "level": 32, "obj": ["Roi tué : 0/1"], "hard": True},
            {"id": 9001, "title": "Livraison oubliée", "level": 20, "grey": True, "obj": ["Paquet : 0/1"]},
            {"id": 1140, "title": "Le kraal de Tranchebauge", "level": 29, "obj": ["Défense : 0/1"], "dungeon": True},
        ]},
        "tracker": {"totalMin": 95, "route": [
            {"id": 6441, "title": "Le cristal de Satyr", "ready": True, "sec": 240,
             "task": "Rends « Le cristal de Satyr » (≈ 4 min)"},
            {"id": 6462, "title": "Totems de la tribu", "ready": True, "sec": 300},
            {"id": 6503, "title": "Les Écorchemousses", "sec": 720,
             "task": "Tue 6 Écorchemousses au nord du lac (≈ 12 min)"},
        ]},
        "todo": {"now": {"text": "Rends « Le cristal de Satyr » au Poste de Bois-Brisé (≈ 4 min)",
                         "why": "2 quêtes finies à rendre au même endroit", "eta": 240}},
        "plan": {"v": 1, "src": "auto", "route": "auto", "title": "Trajet TreeAI", "step": 1, "steps": 9,
                 "label": "Rends « Le cristal de Satyr »", "kind": "turn", "verdict": "ok", "xph": 19800,
                 "minutes": 52, "left": 4, "updated": updated},
        "abandon": {"count": 1, "list": [{"title": "Livraison oubliée", "why": "trop facile maintenant, presque pas d'XP",
                                          "detail": "157 XP/min (autres 326)"}]},
        "hunter": {"t": updated, "level": 28,
                   "pet": {"family": "Loup", "level": 27, "happiness": 2, "loyalty": "Loyauté (4)", "tpFree": 3,
                           "diet": ["meat"], "food": 2672, "dead": False},
                   "ammo": {"kind": "arrow", "total": 0, "equipped": 1240, "quiver": True, "mismatch": False}},
        "stats": {"xph": {"recent": 20500, "recentMin": 52, "all": 19900, "min": 640}, "perH": {"ammo": 600}},
        "group": {"v": 2, "updated": updated, "size": 4, "members": [
            {"name": ME, "class": "Chasseur", "level": 28, "me": True, "zone": "Orneval", "hp": 100},
            {"name": "Fakepriest", "class": "Prêtre", "level": 27, "zone": "Orneval", "hp": 80, "mana": 20, "bags": 8},
            {"name": "Fakewar", "class": "Guerrier", "level": 28, "zone": "Orneval", "hp": 0, "dead": True, "bags": 1},
            {"name": "Fakemage", "class": "Mage", "level": 27, "zone": "Orgrimmar", "hp": 100, "mana": 100,
             "leaveMin": 20},
        ], "advices": [{"cat": "pull", "prio": 2, "text": "Fakepriest à 20 % de mana : pause boisson"}],
            "common": [{"id": 6503, "title": "Les Écorchemousses", "holders": 3, "done": 1}]},
        "team": {"v": 1, "members": [{"class": "PRIEST", "level": 27, "role": "soigneur"},
                                     {"class": "WARRIOR", "level": 28, "role": "tank", "dead": True},
                                     {"class": "MAGE", "level": 27, "role": "dégâts", "otherZone": True}]},
        "missions": {"v": 1, "size": 4, "members": [{"name": ME, "class": "Chasseur", "level": 28},
                                                    {"name": "Fakepriest", "class": "Prêtre", "level": 27},
                                                    {"name": "Fakewar", "class": "Guerrier", "level": 28},
                                                    {"name": "Fakemage", "class": "Mage", "level": 27}],
                     "board": [{"title": "Les Écorchemousses", "done": 1, "holders": 3,
                                "status": "Fakewar : 4 Écorchemousses restants"}],
                     "next": "Prochaine mission : « Les Écorchemousses » avec Fakepriest et Fakewar",
                     "assign": ["Fakemage revient d'Orgrimmar"],
                     "session": {"leaderboard": [{"name": "Fakewar", "xp": 30000}]}},
        "auction": {"updated": updated, "scanAge": 1500, "neutral": False,
                    "deals": [{"name": "Étoffe de laine", "id": 2592, "kind": "flip", "count": 20, "buy": 1400,
                               "sell": 2200, "profit": 650, "roi": 0.46, "conf": 0.82, "sold": 35}],
                    "sell": [{"name": "Cuir moyen", "count": 12, "unit": 85, "net": 970, "vendor": 15},
                             {"name": "Griffe tranchante", "count": 3, "better": "vendor", "vendor": 120}],
                    "gold": {"total": 352075, "reserve": 100000, "invested": 0}},
        "inventory": {"greyCount": 7, "greyValue": 11850, "durMin": 62, "repairCost": 4200,
                      "mats": {"Cuir moyen": 12}},
        "dungeon": {"runs": 2, "meter": {"total": 1000, "top": "Fakemage"}},
        "talents": {"unspent": 1, "points": "18/0/0"},
        "gear": {"priority": "Agilité > DPS de l'arme à distance > Toucher / Critique",
                 "slots": [{"slot": "Arme à distance", "next": {"name": "Arc long renforcé", "gain": 18,
                                                                "where": "maître d'armes d'Orgrimmar", "cost": 42000}}]},
        "errands": {"trainer": "Orgrimmar, maître des chasseurs"},
    }


def _export(made: float) -> dict[str, Any]:
    return {
        "v": 1, "made": made, "char": KEY, "src": "QuestieLoader",
        "player": {"class": "HUNTER", "level": 28, "faction": "Horde", "map": 1440, "x": 73.3, "y": 61.7,
                   "speed": 7.0, "mount": 0, "logMax": 20, "logCount": 14, "group": 4,
                   "taxi": [22, 23, 25, 29, 30, 61, 77]},
        "maps": [],
        "quests": [
            {"id": 6441, "t": "Le cristal de Satyr", "lv": 26, "xp": 2050, "st": "log", "done": 1, "zone": 1440},
            {"id": 6462, "t": "Totems de la tribu", "lv": 25, "xp": 1900, "st": "log", "done": 1, "zone": 1440},
            {"id": 6503, "t": "Les Écorchemousses", "lv": 27, "xp": 2300, "st": "log", "done": 0, "zone": 1440},
            {"id": 1110, "t": "Les rocs de la course", "lv": 30, "xp": 2400, "st": "log", "done": 0, "zone": 1441,
             "o": [{"k": "item", "id": 5100, "n": 8, "have": 0, "p": 0.6, "ml": 30, "map": 1441, "x": 78.0, "y": 74.0}],
             "f": {"map": 1441, "x": 80.0, "y": 76.0, "k": "npc"}},
            {"id": 1111, "t": "Le carburant des gobelins", "lv": 31, "xp": 2700, "st": "log", "done": 0, "zone": 1441,
             "o": [{"k": "kill", "id": 4100, "n": 10, "have": 0, "ml": 30, "map": 1441, "x": 76.0, "y": 79.0}],
             "f": {"map": 1441, "x": 80.0, "y": 76.0, "k": "npc"}},
            {"id": 6621, "t": "Le roi des Furbolgs", "lv": 32, "xp": 3100, "st": "log", "done": 0, "zone": 1440,
             "elite": 1},
            {"id": 1140, "t": "Le kraal de Tranchebauge", "lv": 29, "xp": 2600, "st": "log", "done": 0,
             "zone": 1413, "dg": 1},
        ],
        "squad": [{"key": f"Fakepriest-{REALM}", "class": "PRIEST", "level": 27},
                  {"key": f"Fakewar-{REALM}", "class": "WARRIOR", "level": 28}],
    }


def _diag() -> dict[str, Any]:
    return {"errors": [{"module": "Compass", "n": 3, "t": NOW - 3000,
                        "msg": "Compass.lua:812: attempt to compare nil with number"}],
            "blockedTop": {"CastSpellByName|Quick": {"func": "CastSpellByName", "ctx": "Quick",
                                                     "kind": "ADDON_ACTION_BLOCKED", "n": 2, "last": NOW - 2000}},
            "blockedTotal": 2, "selftest": {"ok": 16, "warn": 1, "ko": 0, "t": NOW - 7200}}


def _db(updated: float) -> dict[str, Any]:
    return {"schema": 3, "version": "3.9.9",
            "chars": {KEY: {"summary": _summary(updated), "learn": {"v": 1, "xph": {"s": 5 * 18800, "n": 5}},
                            "plan": {"route": "xph", "step": 1}},
                      f"Fakealt-{REALM}": {"summary": {"class": "MAGE", "level": 5, "updated": updated - 86400}}},
            "export": _export(updated), "learn": {"v": 1}, "diag": _diag(),
            "group": {"peers": {f"Fakemage-{REALM}": {"class": "MAGE", "level": 27}}}}


def _chat(start: float, minutes: int, xp_each: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """A live chat-log session: one kill every minute for ``minutes`` min, ending now (via live.ChatSession)."""
    from treeaicoach.wow import live

    events: list[dict[str, Any]] = [{"t": start, "kind": "zone", "zone": "Orneval"}]
    events += [{"t": start + 60 * i, "kind": "xp", "xp": xp_each, "kill": True} for i in range(minutes + 1)]
    cs = live.ChatSession()
    cs.feed(events)
    return cs.to_dict(), events


def _app_plan() -> dict[str, Any]:
    return {"v": 1, "made": NOW - 1800, "char": KEY, "zone": 1440, "routes": [
        {"id": "xph", "title": "XP/h max", "xph": 21000, "minutes": 70, "verdict": "ok", "reasons": [],
         "steps": [{"k": "turn", "q": 6441, "label": "Rendre « Le cristal de Satyr »", "eta": 4},
                   {"k": "obj", "q": 6503, "o": 1, "label": "Tuer 6 Écorchemousses", "eta": 16}]},
        {"id": "clear", "title": "Tout finir ici", "xph": 17000, "minutes": 110, "verdict": "long",
         "reasons": ["Le roi des Furbolgs est élite"], "steps": []}]}


def scenario(kind: str) -> dict[str, Any]:
    """{db, live, plan, facts} for :func:`treeaicoach.wow.ai_context.question_context`."""
    if kind == "empty":
        return {"db": {}, "live": {}, "plan": None, "facts": []}
    if kind == "stale":
        db = _db(NOW - 2 * 3600)
        return {"db": db, "live": {"chat": {}, "chat_age": 3 * 3600.0, "events": []}, "plan": None, "facts": []}
    db = _db(NOW - 360)
    chat, events = _chat(NOW - 46 * 60, 46, 350)
    live = {"chat": chat, "chat_age": 30.0, "events": events}
    if kind == "noah":
        a = db["chars"][KEY]["summary"]["auction"]
        a["deals"] = []
        a["none"] = {"why": ["14 affaire(s) apparente(s) écartée(s) :", "historique trop court (9)",
                             "marge trop faible après commission et dépôt (5)"]}
    if kind == "duo":
        s = db["chars"][KEY]["summary"]
        s["groupSize"] = 2
        s["group"]["members"] = s["group"]["members"][:2]
        s["team"]["members"] = s["team"]["members"][:1]
    return {"db": db, "live": live, "plan": _app_plan(), "facts": ["Familier mort il y a 40 s"] if kind == "full" else []}


def huge_scenario() -> dict[str, Any]:
    """Everything oversized (60 long quests, 10 deals, 5 members…): the state must stay within budget."""
    sc = copy.deepcopy(scenario("full"))
    s = sc["db"]["chars"][KEY]["summary"]
    long_obj = ["Objectif très long numéro %d avec beaucoup de mots inutiles : 0/%d" % (i, i + 5) for i in range(4)]
    s["quests"]["list"] = [{"id": 20000 + i, "title": f"Quête au titre vraiment très long numéro {i} de la zone",
                            "level": 25 + i % 6, "obj": long_obj} for i in range(60)]
    s["quests"]["count"] = 60
    s["auction"]["deals"] = [dict(s["auction"]["deals"][0], name=f"Objet de commerce numéro {i}") for i in range(10)]
    s["auction"]["sell"] = [dict(s["auction"]["sell"][0], name=f"Composant numéro {i}") for i in range(5)]
    s["group"]["advices"] = [{"text": f"Conseil de groupe très détaillé numéro {i} pour Fakepriest"} for i in range(8)]
    sc["facts"] = [f"Fait de combat récent numéro {i} avec un texte assez long" for i in range(8)]
    return sc


#: The 30 eval cases. Numbers come from the fake data above (see the module docstring for the arithmetic).
CASES: list[dict[str, Any]] = [
    # --- route / next step
    {"id": "next", "q": "Hey coach, on fait quoi ?", "scenario": "full",
     "facts": ["Rends « Le cristal de Satyr »", "étape 1 sur 9"], "local": ["Rends « Le cristal de Satyr »"]},
    {"id": "where-next", "q": "Où je vais ensuite ?", "scenario": "full",
     "facts": ["maintenant", "Mille pointes (Librevent)", "d'après Classic"], "local": ["Cristal de Satyr"]},
    {"id": "next-quest", "q": "C'est quoi ma prochaine quête ?", "scenario": "full",
     "facts": ["Rends « Le cristal de Satyr »"], "local": ["Cristal de Satyr"]},
    {"id": "best-zone", "q": "C'est quoi la meilleure zone pour mon niveau ?", "scenario": "full",
     "facts": ["Mille pointes (Librevent) et Orneval (Bois-Brisé)", "d'après Classic"], "local": ["Mille pointes"]},
    {"id": "next-stale", "q": "On fait quoi ?", "scenario": "stale",
     "facts": ["il y a 2 h", "Rends « Le cristal de Satyr »"], "absent": ["mort", "en direct"],
     "local": ["Cristal de Satyr"]},
    # --- quest log
    {"id": "log-count", "q": "Il me reste combien de quêtes dans mon journal ?", "scenario": "full",
     "facts": ["\"journal\":\"14/20\""], "local": ["14 sur 20"]},
    {"id": "turn-in", "q": "J'ai des quêtes à rendre ?", "scenario": "full",
     "facts": ["Le cristal de Satyr [26]", "Totems de la tribu [25]"], "local": ["2 à rendre"]},
    {"id": "abandon", "q": "Quelle quête je devrais abandonner ?", "scenario": "full",
     "facts": ["Livraison oubliée : trop facile maintenant"], "absent": ["XP/min"], "local": ["Livraison oubliée"]},
    # --- XP/h and level ETA (confidence)
    {"id": "xph", "q": "Combien d'XP par heure je fais ?", "scenario": "full",
     "facts": ["\"xp_par_heure\":\"21 500\"", "mesurée en direct sur 46 min", "\"confiance\":\"bonne\""],
     "local": ["21 500", "fiable"]},
    {"id": "eta", "q": "Dans combien de temps je suis niveau 29 ?", "scenario": "full",
     "facts": ["\"niveau_29_dans\":\"≈ 45 min\"", "\"xp_restante\":\"16 300\""], "local": ["45 min"]},
    {"id": "eta-stale", "q": "Je monte quand niveau 29 ?", "scenario": "stale",
     "facts": ["moyenne de tes 5 dernières sessions", "il y a 2 h"], "absent": ["20 500", "21 500"],
     "local": ["18 800"]},
    {"id": "eta-empty", "q": "Dans combien de temps je suis niveau 29 ?", "scenario": "empty",
     "facts": ["/reload"], "absent": ["xp_par_heure"], "local": ["je n'ai pas encore la donnée"]},
    # --- « ça vaut le coup ? » (travel vs XP vs alternatives)
    {"id": "detour-salines", "q": "Ça vaut le coup d'aller aux Salines pour 2 quêtes ?", "scenario": "full",
     "facts": ["Les Salines (Mille pointes)", "Vol pour Poste de Librevent", "≈ 5 100 XP", "\"quetes\":2",
               "xp_par_heure_du_detour", "verdict"], "local": ["Les Salines", "5 100 XP"]},
    {"id": "detour-stale", "q": "C'est rentable d'aller aux Salines ?", "scenario": "stale",
     "facts": ["Les Salines (Mille pointes)", "≈ 5 100 XP", "moyenne de tes 5 dernières sessions"],
     "local": ["Les Salines"]},
    # --- dungeons
    {"id": "dungeon-duo", "q": "Quel donjon à 2 ?", "scenario": "duo",
     "facts": ["\"a_2\"", "Donjon d'Ombrecroc (18–26)", "d'après Classic"], "local": ["Ombrecroc", "Classic"]},
    {"id": "dungeon-4", "q": "On peut faire quel donjon à 4 ?", "scenario": "full",
     "facts": ["Kraal de Tranchebauge (24–32)", "Profondeurs de Brassenoire (22–30)", "1 tank, 1 soigneur"],
     "absent": ["Prison de Hurlevent"], "local": ["Kraal de Tranchebauge"]},
    {"id": "dungeon-ready", "q": "On est prêts pour Brassenoire ?", "scenario": "full",
     "facts": ["Profondeurs de Brassenoire (22–30) : niveau OK", "Le kraal de Tranchebauge"],
     "local": ["Brassenoire", "niveau OK"]},
    # --- auction house / money
    {"id": "sell-what", "q": "Je vends quoi ?", "scenario": "full",
     "facts": ["Cuir moyen x12", "objets_gris", "Griffe tranchante : au marchand"], "local": ["Cuir moyen"]},
    {"id": "ah-deal", "q": "Il y a une bonne affaire à l'HV ?", "scenario": "full",
     "facts": ["Étoffe de laine x20 : acheter 14 pa, revendre 22 pa → +6 pa 50 pc", "il y a 31 min"],
     "local": ["Étoffe de laine"]},
    {"id": "ah-nothing", "q": "Il y a une bonne affaire à l'HV ?", "scenario": "noah",
     "facts": ["Aucun achat-revente sûr pour l'instant", "historique trop court (9)"], "absent": ["affaires_sures"],
     "local": ["Rien de sûr", "historique trop court"]},
    {"id": "gold", "q": "Combien j'ai d'or ?", "scenario": "full",
     "facts": ["35 po 20 pa"], "local": ["35 po 20 pa"]},
    # --- group
    {"id": "group-state", "q": "Mes potes ils en sont où ?", "scenario": "full",
     "facts": ["Prêtre 27, mana 20 %", "Guerrier 28, mort", "Mage 27, Orgrimmar, part dans 20 min"],
     "local": ["Prêtre 27", "Guerrier 28, mort"]},
    {"id": "together", "q": "On fait quoi ensemble ?", "scenario": "full",
     "facts": ["Prochaine mission : « Les Écorchemousses » avec Prêtre 27 et Guerrier 28"],
     "local": ["Les Écorchemousses"]},
    {"id": "group-stale", "q": "Mes potes ils en sont où ?", "scenario": "stale",
     "facts": ["Prêtre 27", "trop vieux"], "absent": ["mort", "mana 20", "part dans"], "local": ["Prêtre 27"]},
    # --- pet / ammo / best pet
    {"id": "pet", "q": "Mon familier va bien ?", "scenario": "full",
     "facts": ["Loup, niveau 27 (toi 28), content", "points_de_dressage_libres"], "local": ["Loup", "content"]},
    {"id": "ammo", "q": "Il me reste combien de flèches ?", "scenario": "full",
     "facts": ["1 240 flèches", "≈ 2 h 04 de jeu"], "local": ["1 240 flèches"]},
    {"id": "best-pet", "q": "Quel est le meilleur familier pour moi ?", "scenario": "full",
     "facts": ["a_apprivoiser", "d'après Classic"], "local": ["Classic"]},
    # --- errors / blocked actions
    {"id": "errors", "q": "Pourquoi l'addon bug ?", "scenario": "full",
     "facts": ["1 erreur, vue 3 fois, la plus récente dans le module Compass", "derniere_erreur", "CastSpellByName pendant Quick"],
     "local": ["Compass", "CastSpellByName"]},
    # --- talents / gear
    {"id": "talents", "q": "Quels talents je prends ?", "scenario": "full",
     "facts": ["\"points_libres\":1", "18/0/0"], "local": []},
    {"id": "gear", "q": "Je dois changer d'arme ?", "scenario": "full",
     "facts": ["Arc long renforcé"], "local": ["Arc long renforcé"]},
]
