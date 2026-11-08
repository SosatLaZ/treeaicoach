"""Macro linter: WoW limits (255 bytes body, 16 chars name) + Classic-family syntax checks.

``lint(name, body)`` returns a list of French problem strings (empty = OK). Pure, never raises.
"""
from __future__ import annotations

import re

MAX_BODY = 255   # WoW counts bytes (UTF-8 accents count double): be strict
MAX_NAME = 16

COMMANDS = {
    "/cast", "/use", "/castsequence", "/castrandom", "/stopcasting", "/stopattack", "/startattack",
    "/stopmacro", "/petattack", "/petfollow", "/petpassive", "/petdefensive", "/petaggressive",
    "/petstay", "/petautocaston", "/petautocastoff", "/equip", "/equipslot", "/target",
    "/targetenemy", "/targetfriend", "/targetlasttarget", "/assist", "/cleartarget",
    "/cancelaura", "/cancelform", "/dismount", "/click", "/run", "/script", "/say", "/p",
    "/party", "/ra", "/raid", "/s", "/y", "/e", "/emote", "/focus", "/console", "/userandom",
}
# commands whose argument list uses [conditions] clauses separated by ';'
CONDITIONAL = {
    "/cast", "/use", "/castsequence", "/castrandom", "/stopmacro", "/petattack", "/petfollow",
    "/petpassive", "/petdefensive", "/petaggressive", "/petstay", "/equip", "/target",
    "/targetenemy", "/assist", "/startattack", "/stopattack", "/cancelaura", "/cancelform",
    "/dismount", "/userandom", "/equipslot", "/stopcasting", "/click",
}
CONDS = {
    "mod", "modifier", "harm", "help", "dead", "exists", "combat", "stance", "form", "stealth",
    "pet", "swimming", "outdoors", "indoors", "mounted", "flying", "flyable", "channeling",
    "group", "equipped", "worn", "button", "btn", "actionbar", "bar", "party", "raid", "known",
    "talent", "spec", "resting", "unithasvehicleui", "vehicleui", "cursor",
}
UNITS = {"player", "target", "mouseover", "pet", "pettarget", "targettarget", "none", "cursor",
         "party1", "party2", "party3", "party4", "party1target", "party2target", "party3target",
         "party4target", "mouseovertarget", "focus"}
MODS = {"shift", "ctrl", "alt"}
RESET = {"combat", "target", "shift", "alt", "ctrl"}
# conditions that cannot both hold in one [group]
OPPOSITE = (("harm", "help"), ("dead", "nodead"), ("combat", "nocombat"), ("exists", "noexists"),
            ("stealth", "nostealth"), ("mounted", "nomounted"), ("pet", "nopet"))

_TOKEN_LEFT = re.compile(r"\{[^}]*\}")


def _check_conditions(group: str) -> list[str]:
    errs = []
    parts = [c.strip().lower() for c in group.split(",") if c.strip()]
    for a, b in OPPOSITE:
        if a in parts and b in parts:
            errs.append(f"conditions contradictoires [{a},{b}]")
    if ("@mouseover" in parts or "target=mouseover" in parts) and not (
            {"harm", "help", "exists", "dead", "nodead"} & set(parts)):
        errs.append("@mouseover sans harm/help/exists : vise le vide quand rien n'est survolé")
    for raw in group.split(","):
        c = raw.strip()
        if not c:
            continue
        if c.startswith("@") or c.startswith("target="):
            unit = c[1:] if c.startswith("@") else c[7:]
            if unit == "focus":
                errs.append("@focus : pas de focus fiable en Classic, utiliser @mouseover")
            elif unit not in UNITS:
                errs.append(f"unité inconnue « {unit} »")
            continue
        key, _, val = c.partition(":")
        key = key.lower()
        base = key[2:] if key.startswith("no") and key[2:] in CONDS else key
        if base not in CONDS:
            errs.append(f"condition inconnue « {c} »")
            continue
        if base in ("mod", "modifier") and val:
            for m in val.split("/"):
                if m.lower() not in MODS:
                    errs.append(f"modificateur inconnu « {m} »")
        if base in ("stance", "form") and val:
            if not all(p.isdigit() for p in val.split("/")):
                errs.append(f"posture invalide « {c} »")
    return errs


def lint(name: str, body: str) -> list[str]:
    """Problems of a fully rendered macro (French messages)."""
    try:
        return _lint(name, body)
    except Exception as e:  # pragma: no cover - never raise
        return [f"erreur interne du linter : {e}"]


def _lint(name: str, body: str) -> list[str]:
    errs: list[str] = []
    if not name or len(name) > MAX_NAME:
        errs.append(f"nom « {name} » : 1 à {MAX_NAME} caractères")
    size = len(body.encode("utf-8"))
    if size > MAX_BODY:
        errs.append(f"{name} : {size} octets > {MAX_BODY}")
    if not body.strip():
        errs.append(f"{name} : macro vide")
    if _TOKEN_LEFT.search(body):
        errs.append(f"{name} : jeton non résolu {_TOKEN_LEFT.search(body).group(0)}")
    for i, line in enumerate(body.split("\n"), 1):
        where = f"{name} l.{i}"
        if not line.strip():
            errs.append(f"{where} : ligne vide")
            continue
        if line.startswith("#"):
            if not (line.startswith("#showtooltip") or line.startswith("#show")):
                errs.append(f"{where} : directive inconnue")
            if i != 1:
                errs.append(f"{where} : #showtooltip doit être en 1re ligne")
            continue
        if not line.startswith("/"):
            errs.append(f"{where} : doit commencer par /")
            continue
        cmd, _, rest = line.partition(" ")
        cmd = cmd.lower()
        if cmd not in COMMANDS:
            errs.append(f"{where} : commande inconnue {cmd}")
            continue
        if cmd in ("/run", "/script"):
            if rest.count("(") != rest.count(")") or rest.count('"') % 2:
                errs.append(f"{where} : parenthèses/guillemets déséquilibrés")
            continue
        if rest.count("[") != rest.count("]"):
            errs.append(f"{where} : crochets déséquilibrés")
            continue
        if cmd not in CONDITIONAL:
            continue
        clauses = rest.split(";")
        for ci, clause in enumerate(clauses):
            groups = re.findall(r"\[([^\]]*)\]", clause)
            for group in groups:
                errs.extend(f"{where} : {e}" for e in _check_conditions(group))
            if re.search(r"\]\s*[^\s\[!].*\[", clause):
                errs.append(f"{where} : conditions après le nom du sort")
            tail = re.sub(r"\[[^\]]*\]", "", clause).strip()
            # a clause that always matches (no condition, or an empty [] group) hides the clauses after it
            if ci < len(clauses) - 1 and cmd in ("/cast", "/use", "/castsequence") and (
                    not groups or any(not g.strip() for g in groups)):
                errs.append(f"{where} : clause {ci + 1} toujours vraie, la suite n'est jamais lue")
            if "!" in tail and not re.match(r"^(reset=\S+\s+)?!\S", tail):
                errs.append(f"{where} : « ! » doit précéder le nom du sort")
            if cmd == "/castsequence" and tail:
                if tail.startswith("reset=") and " " not in tail:
                    errs.append(f"{where} : /castsequence sans sorts")
                m = re.match(r"^reset=(\S+)", tail)
                if m:
                    for part in m.group(1).split("/"):
                        if not (part.isdigit() or part.lower() in RESET):
                            errs.append(f"{where} : reset={m.group(1)} invalide ({part})")
        if cmd in ("/cast", "/use", "/castsequence"):
            # every clause needs a target spell/item
            for clause in rest.split(";"):
                tail = re.sub(r"\[[^\]]*\]", "", clause).strip()
                if not tail:
                    errs.append(f"{where} : clause sans sort/objet")
    return errs
