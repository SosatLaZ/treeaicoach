"""Macro generator: templates -> tokenized Lua data, French renderings, level-band simulation, lint.

In the Lua data, spells are ``{ID}`` tokens (resolved in game from the spell ID, French client name as
fallback), items ``{item:cat}`` tokens (best item of the bags) and trinkets ``{slot:13}``. ``?`` markers
are kept: the in-game manager drops optional lines/clauses whose spell, item or trinket is missing.
``simulate`` is a faithful Python port of ``Modules/Macros.lua`` renderVariant, used to lint every macro
as the game would build it for every class × spec × level band. ``personalize`` is the port of the in-game
per-macro settings (Maj/Ctrl/Alt variants, mouseover, pet commands, aspect guard, tank) applied to the
tokenized body before rendering: every combination is linted too.
"""
from __future__ import annotations

import functools
import logging
import re

from . import consumables
from .linter import CONDITIONAL, MAX_BODY, lint
from .spells import (CLASSES, COMMON, LEVEL_OVERRIDE, LEVELING_SPEC, SPECS, SPELLS, TALENTS, is_talent,
                     lookup, spec_ids)
from .templates import Macro, macros_for

log = logging.getLogger(__name__)

_KEY = re.compile(r"\{([a-z_0-9:]+)\}")
_ID = re.compile(r"\{(\d+)\}")
_ITEM = re.compile(r"\{item:([a-z_]+)\}")
_SLOT = re.compile(r"\{slot:(1[34])\}")
_MARK = re.compile(r"\?(?=[\[{!])")
_CASTS = ("/cast", "/castsequence", "/castrandom")
ITEM_SAMPLE = "item:99999"   # worst-case length of a resolved item token
LEVELS = (1, 5, 9, 10, 14, 19, 20, 24, 29, 30, 34, 39, 40, 44, 49, 50, 55, 60)
BANDS = ((1, 9), (10, 19), (20, 29), (30, 39), (40, 49), (50, 60))
MAX_DEFAULT = 18             # character macro slots (MAX_CHARACTER_MACROS)


class TemplateError(ValueError):
    pass


def tokenize(cls: str, template: str) -> str:
    """Template keys -> Lua tokens ({key} -> {ID}); items/slots untouched. Raises on unknown key."""
    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key.startswith("item:"):
            if key[5:] not in consumables.CATEGORIES:
                raise TemplateError(f"catégorie d'objet inconnue {key}")
            return m.group(0)
        if key.startswith("slot:"):
            if key[5:] not in ("13", "14"):
                raise TemplateError(f"emplacement inconnu {key}")
            return m.group(0)
        hit = lookup(cls, key)
        if hit is None:
            raise TemplateError(f"{cls}: sort inconnu {{{key}}}")
        return "{%d}" % hit[0]
    return _KEY.sub(sub, template)


@functools.lru_cache(maxsize=None)
def _table(cls: str) -> dict[int, tuple[str, str, str]]:
    """spell id -> (key, French name, flags) for a class (+ racials)."""
    out = {}
    for table in (COMMON, SPELLS.get(cls, {})):
        for key, (sid, name, flags) in table.items():
            out[sid] = (key, name, flags)
    return out


def _names(cls: str) -> dict[int, str]:
    return {sid: v[1] for sid, v in _table(cls).items()}


def _pet_ids(cls: str) -> set[int]:
    return {sid for sid, v in _table(cls).items() if "p" in v[2]}


def _split_clauses(line: str) -> tuple[str, list[str]] | None:
    if not line.startswith("/"):
        return None
    cmd, sep, rest = line.partition(" ")
    if not sep:
        return None
    return cmd, rest.split(";")


def required(cls: str, body: str) -> tuple[list[int], list[str]]:
    """(required spell IDs, required item categories) of a tokenized body, outside optional parts."""
    pets = _pet_ids(cls)
    spells: list[int] = []
    items: list[str] = []
    for line in body.split("\n"):
        if line.startswith("?"):
            continue
        parts = _split_clauses(line)
        chunks = [line] if parts is None else [c for c in parts[1] if not c.strip().startswith("?")]
        for chunk in chunks:
            for sid in map(int, _ID.findall(chunk)):
                if sid not in pets and sid not in spells:
                    spells.append(sid)
            for cat in _ITEM.findall(chunk):
                if cat not in items:
                    items.append(cat)
    return spells, items


def render(cls: str, body: str, minimal: bool = False) -> str:
    """French rendering of a tokenized body. ``minimal`` drops every optional part."""
    names = _names(cls)
    lines = []
    for line in body.split("\n"):
        if line.startswith("?"):
            if minimal:
                continue
            line = line[1:]
        parts = _split_clauses(line)
        if parts is not None and minimal:
            kept = [c for c in parts[1] if not c.strip().startswith("?")]
            if not kept:
                continue
            line = parts[0] + " " + ";".join(kept).strip()
        line = _MARK.sub("", line)
        line = _ID.sub(lambda m: names.get(int(m.group(1)), m.group(0)), line)
        line = _ITEM.sub(ITEM_SAMPLE, line)
        line = _SLOT.sub(lambda m: m.group(1), line)
        lines.append(line)
    return "\n".join(lines)


# ------------------------------------------------------------------ personalisation (port of Modules/Macros.lua)
GUARD_ID = 13165                       # Aspect du faucon (rang 1) : la « garde » d'aspect des macros de tir
GUARD_LINE = "/cast !{%d}" % GUARD_ID  # ligne entière (marqueur « ? » en plus) : retirée quand la garde est coupée
_MODC = re.compile(r"^(?:mod|modifier)(?::|$)", re.I)
_NOMODC = re.compile(r"^no(?:mod|modifier)(?::|$)", re.I)
_CASTLIKE = ("/cast", "/use", "/castsequence", "/castrandom", "/equip", "/userandom")


def lead_groups(clause: str) -> tuple[list[str], str]:
    """Leading [conditions] groups of a clause and what follows them (spell, item, reset=…)."""
    groups: list[str] = []
    rest = clause.lstrip()
    while rest.startswith("["):
        j = rest.find("]")
        if j < 0:
            break
        groups.append(rest[1:j])
        rest = rest[j + 1:].lstrip()
    return groups, rest


def personalize(body: str, mods: bool = True, mo: bool = True, pet: bool = True, guard: bool = True,
                tank: int = 1) -> str:
    """Tokenized body with the player's settings applied (same algorithm as Macros.lua ``personalize``):
    mods=False drops the Maj/Ctrl/Alt alternatives ([mod:x] groups) and the [nomod] conditions, mo=False the
    @mouseover groups, pet=False the /pet… lines, guard=False the aspect guard line, tank=N retargets
    @party1. A clause left without any group is dropped; a lone empty group « [] » means « always »."""
    if mods and mo and pet and guard and tank == 1:
        return body
    out: list[str] = []
    for raw in body.split("\n"):
        opt = raw.startswith("?")
        line = raw[1:] if opt else raw
        if not line.startswith("/"):
            out.append(raw)
            continue
        cmd, sep, rest = line.partition(" ")
        lc = cmd.lower()
        if not pet and lc.startswith("/pet"):
            continue
        if not guard and line == GUARD_LINE:
            continue
        if not sep or lc in ("/run", "/script"):
            out.append(raw)
            continue
        new = rest
        if tank != 1 and lc == "/assist":
            new = new.replace("@party1", "@party%d" % tank)
        if (not mods or not mo) and lc in CONDITIONAL:
            clauses: list[str] = []
            for clause in new.split(";"):
                c = clause.strip()
                q = "?" if c.startswith("?") else ""
                if q:
                    c = c[1:]
                groups, tail = lead_groups(c)
                if not groups:
                    clauses.append(q + c)
                    continue
                kept: list[list[str]] = []
                for g in groups:
                    conds = [x.strip() for x in g.split(",") if x.strip()]
                    if not mods:
                        if any(_MODC.match(x) for x in conds):
                            continue
                        conds = [x for x in conds if not _NOMODC.match(x)]
                    if not mo and any(x.lower() in ("@mouseover", "target=mouseover") for x in conds):
                        continue
                    kept.append(conds)
                if not kept:
                    continue
                if len(kept) == 1 and not kept[0]:
                    kept = []
                gtxt = "".join("[" + ",".join(cs) + "]" for cs in kept)
                clauses.append(q + gtxt + (" " if gtxt and tail else "") + tail)
            if not clauses:
                continue
            rebuilt = "; ".join(clauses)
            if rebuilt.replace(" ", "") != new.replace(" ", ""):
                new = rebuilt
        out.append(raw if new == rest else ("?" if opt else "") + cmd + " " + new)
    if not any(ln.lstrip("?").split(" ", 1)[0].lower() in _CASTLIKE for ln in out):
        out = [ln for ln in out if not ln.lstrip("?").startswith("#")]
    return "\n".join(out)


def options_of(cls: str, m: Macro) -> str:
    """Settings the page offers for a macro: m = Maj/Ctrl/Alt, o = survol, p = ordres au familier,
    g = garde d'aspect, t = tank (groupe 1-4)."""
    bodies = []
    for v in m.variants:
        try:
            bodies.append(tokenize(cls, v))
        except TemplateError:
            continue
    lines = [ln.lstrip("?") for b in bodies for ln in b.split("\n")]
    out = ""
    conds = [c.strip() for b in bodies for g in re.findall(r"\[([^\]]*)\]", b) for c in g.split(",")]
    if any(_MODC.match(c) or _NOMODC.match(c) for c in conds):
        out += "m"
    if any("@mouseover" in c for c in conds):
        out += "o"
    pets = [ln for ln in lines if ln.lower().startswith("/pet")]
    other = [ln for ln in lines if ln.startswith("/") and not ln.lower().startswith("/pet")]
    if pets and other and not m.petctl:
        out += "p"
    if GUARD_LINE in lines:
        out += "g"
    if any("@party1" in ln for ln in lines):
        out += "t"
    return out


def option_combos(letters: str) -> list[dict]:
    """Every combination of the settings a macro offers (off/on), as personalize() keyword sets."""
    keys = [k for k, letter in (("mods", "m"), ("mo", "o"), ("pet", "p"), ("guard", "g")) if letter in letters]
    combos = []
    for mask in range(1 << len(keys)):
        combos.append({k: not (mask >> i) & 1 for i, k in enumerate(keys)})
    return combos


# ------------------------------------------------------------------ game simulation
@functools.lru_cache(maxsize=None)
def learn_levels(cls: str) -> dict[str, int]:
    """key -> level the class learns it from a trainer (talent-only spells excluded)."""
    try:
        from .. import gamedata
        rows = gamedata.class_spells(cls)
    except Exception:  # pragma: no cover - data missing
        log.debug("gamedata unavailable", exc_info=True)
        rows = []
    first: dict[int, tuple[int, bool]] = {}
    for r in rows:
        if r["id"] not in first:
            first[r["id"]] = (r["level"], r["talent"])
    out = {}
    for key, (sid, _name, flags) in SPELLS.get(cls, {}).items():
        if "p" in flags or is_talent(cls, key):
            continue
        if key in LEVEL_OVERRIDE.get(cls, {}):
            out[key] = LEVEL_OVERRIDE[cls][key]
        elif sid in first and not first[sid][1]:
            out[key] = first[sid][0]
    return out


def known_ids(cls: str, spec: str, level: int, racials: bool = True) -> set[int]:
    """Spell IDs a ``spec`` character of ``level`` knows (worst case: every racial)."""
    table = SPELLS.get(cls, {})
    ids = {table[k][0] for k, lvl in learn_levels(cls).items() if lvl <= level}
    for key, lvl in TALENTS.get(cls, {}).get(spec, {}).items():
        if lvl <= level:
            ids.add(table[key][0])
    if racials:
        ids.update(sid for sid, _n, _f in COMMON.values())
    return ids


def _sub_tokens(cls: str, s: str, known: set[int], items: set[str]) -> tuple[str, bool]:
    names, pets = _names(cls), _pet_ids(cls)
    ok = True

    def item(m):
        nonlocal ok
        if m.group(1) not in items:
            ok = False
            return ""
        return ITEM_SAMPLE

    def slot(m):
        nonlocal ok
        if "trinket" not in items:
            ok = False
            return ""
        return m.group(1)

    def spell(m):
        nonlocal ok
        sid = int(m.group(1))
        if sid not in known and sid not in pets:
            ok = False
        return names.get(sid, "")
    s = _ITEM.sub(item, s)
    s = _SLOT.sub(slot, s)
    s = _ID.sub(spell, s)
    return s, ok


def simulate(cls: str, body: str, known: set[int], items: set[str] | None = None,
             consumable: bool = True) -> str | None:
    """Python port of Modules/Macros.lua renderVariant (without the size cut, linted instead)."""
    items = set(consumables.CATEGORIES) | {"trinket"} if items is None else items
    req, cats = required(cls, body)
    if any(s not in known for s in req) or any(c not in items for c in cats):
        return None
    out = []
    for line in body.split("\n"):
        optional = line.startswith("?")
        if optional:
            line = line[1:]
        m = re.match(r"^(/\S+)\s+(.+)$", line)
        keep = True
        if m and m.group(1) not in ("/run", "/script") and "?" in m.group(2):
            kept = []
            for clause in m.group(2).split(";"):
                c = clause.strip()
                if c.startswith("?"):
                    r, ok = _sub_tokens(cls, c[1:], known, items)
                    if ok:
                        kept.append(c[1:])
                elif c:
                    kept.append(c)
            if not kept:
                keep = False
            else:
                line = m.group(1) + " " + "; ".join(kept)
        if keep:
            r, ok = _sub_tokens(cls, line, known, items)
            if ok:
                out.append(r)
            elif not optional:
                return None
    text = "\n".join(out)
    if not text or text == "#showtooltip":
        return None
    if consumable and "{item:" in body and "item:" not in text:
        return None
    return text


def simulate_macro(cls: str, m: Macro, spec: str, level: int, items: set[str] | None = None,
                   opts: dict | None = None) -> str | None:
    known = known_ids(cls, spec, level)
    for v in m.variants:
        try:
            tok = tokenize(cls, v)
        except TemplateError:
            return None
        if opts:
            tok = personalize(tok, **opts)
        body = simulate(cls, tok, known, items, m.kind == "consumable")
        if body is not None:
            return body
    return None


def applies(m: Macro, spec: str) -> bool:
    return not m.specs or spec in m.specs


def macro_level(cls: str, m: Macro) -> int:
    """First level at which the macro can be built for one of its specs (61 = never)."""
    specs = m.specs or spec_ids(cls)
    for lvl in range(1, 61):
        if any(simulate_macro(cls, m, s, lvl) is not None for s in specs):
            return lvl
    return 61


# ------------------------------------------------------------------ semantic lint (Blizzard rules)
def _flags(cls: str, sid: int) -> str:
    hit = _table(cls).get(sid)
    return hit[2] if hit else ""


def _first_unmodded(lines: list[str], cast_lines: list[int]) -> int | None:
    """First /cast-like line usable without a modifier key (what a bare #showtooltip shows)."""
    for i in cast_lines:
        clauses = lines[i].split(" ", 1)[1].split(";") if " " in lines[i] else [""]
        for c in clauses:
            conds = re.findall(r"\[([^\]]*)\]", c)
            if not conds or any("mod:" not in g or "nomod" in g for g in conds):
                return i
    return None


# Spells that switch OFF when cast while active: always « ! » (or a guard such as [nostealth] / [noform]).
TOGGLES = {"auto_shot", "wand", "aspect_monkey", "aspect_hawk", "aspect_cheetah", "aspect_pack",
           "trueshot_aura", "bear", "dire_bear", "cat", "moonkin", "travel", "aquatic", "shadowform",
           "stealth", "prowl"}
_GUARDS = ("noform", "nostealth", "nostance")
_TAIL_OK = re.compile(r"^(?:!?\{\d+\}|\{item:[a-z_]+\}|\{slot:1[34]\}|item:\d+|\d+)$")


def _key_of(cls: str, sid: int) -> str:
    hit = _table(cls).get(sid)
    return hit[0] if hit else ""


def lint_spells(cls: str, name: str, body: str) -> list[str]:
    """Spell-level rules: no spell typed in clear (only base tokens), toggles protected by « ! »,
    spam-safe /castsequence (reset rule, no Feign Death + reset=combat)."""
    errs: list[str] = []
    for i, raw in enumerate(body.split("\n"), 1):
        line = raw[1:] if raw.startswith("?") else raw
        cmd, _, rest = line.partition(" ")
        cmd = cmd.lower()
        if cmd not in _CASTS and cmd not in ("/use", "/equip"):
            continue
        where = f"{name} l.{i}"
        for clause in rest.split(";"):
            c = clause.strip().lstrip("?")
            conds = " ".join(re.findall(r"\[([^\]]*)\]", c))
            tail = re.sub(r"\[[^\]]*\]", "", c).strip()
            if cmd == "/castsequence":
                reset = re.match(r"^reset=(\S+)\s+", tail)
                steps = [t.strip() for t in tail[reset.end():].split(",")] if reset else [
                    t.strip() for t in tail.split(",")]
                ids = [int(x) for x in _ID.findall(tail)]
                if not reset and len(steps) > 1:
                    errs.append(f"{where} : /castsequence sans reset= : bloquée si un sort échoue")
                if reset and "combat" in reset.group(1).split("/") and any(
                        _key_of(cls, x) == "feign_death" for x in ids):
                    errs.append(f"{where} : Feindre la mort fait sortir du combat : reset=combat relance "
                                "la séquence au début")
            else:
                steps = [tail]
            for si, st in enumerate(steps):
                if not st:
                    continue
                if cmd == "/castsequence" and st.lower() == "null":
                    # astuce « une fois par cible » : la séquence reste sur « null » jusqu'au reset
                    if si != len(steps) - 1:
                        errs.append(f"{where} : « null » seulement en dernier pas de la séquence")
                    elif not reset:
                        errs.append(f"{where} : séquence « … , null » sans reset= : ne repart jamais")
                    continue
                if not _TAIL_OK.match(st):
                    errs.append(f"{where} : « {st} » n'est pas un sort / objet de la base (jeton attendu)")
                    continue
                m = _ID.search(st)
                if m and _key_of(cls, int(m.group(1))) in TOGGLES and cmd == "/cast" and not st.startswith("!") \
                        and not any(g in conds for g in _GUARDS):
                    errs.append(f"{where} : {_key_of(cls, int(m.group(1)))} se coupe si on rappuie : « ! » requis")
    return errs


def is_guard(line: str) -> bool:
    """Aspect guard: « /cast !Aspect du faucon » alone (does nothing while it is active, never toggles it off)."""
    return line.lstrip("?") == GUARD_LINE


def is_once(line: str) -> bool:
    """« /castsequence reset=… X, null » : X once per target / fight, then nothing until the reset."""
    ln = line.lstrip("?")
    if not ln.lower().startswith("/castsequence "):
        return False
    _g, tail = lead_groups(ln.split(" ", 1)[1])
    return tail.replace(" ", "").lower().endswith(",null")


def _mod_req(line: str) -> set[str]:
    """Modifier requirement shared by every clause of a line: {"+shift"}, {"-shift"}, {"-any"} (= [nomod])…"""
    rest = line.lstrip("?").split(" ", 1)
    if len(rest) < 2:
        return set()
    req: set[str] | None = None
    for clause in rest[1].split(";"):
        groups, _tail = lead_groups(clause.strip().lstrip("?"))
        if not groups:
            return set()
        creq: set[str] | None = None
        for g in groups:
            r = set()
            for x in (c.strip().lower() for c in g.split(",")):
                mm = re.match(r"^(no)?(?:mod|modifier)(?::(\w+))?$", x)
                if mm:
                    r.add(("-" if mm.group(1) else "+") + (mm.group(2) or "any"))
            creq = r if creq is None else creq & r
        req = creq if req is None else req & creq
    return req or set()


def _exclusive(a: set[str], b: set[str]) -> bool:
    """Two lines can never both act on the same press (one needs a modifier the other forbids)."""
    def clash(pos, neg):
        return any(p[0] == "+" and n[0] == "-" and (n[1:] == "any" or n[1:] == p[1:]) for p in pos for n in neg)
    return clash(a, b) or clash(b, a)


def lint_rules(cls: str, name: str, body: str, guards: bool = True) -> list[str]:
    """One GCD action per press, correct #showtooltip, trinkets optional, /stopcasting placement.
    Exempt from the GCD count: the aspect guard (« !Aspect du faucon » : acts only when no combat aspect is
    on, the main action then waits one press) and the « once » sequences (« …, null »)."""
    errs: list[str] = lint_spells(cls, name, body)
    lines = body.split("\n")
    first = lines[0]
    gcd_lines, cast_lines = [], []
    for i, raw in enumerate(lines):
        line = raw[1:] if raw.startswith("?") else raw
        cmd = line.split(" ", 1)[0].lower()
        if _SLOT.search(line) and not raw.startswith("?"):
            errs.append(f"{name} l.{i + 1} : bijou sur une ligne non optionnelle")
        if cmd in _CASTS or cmd in ("/use", "/equip"):
            cast_lines.append(i)
        if cmd in _CASTS and not (guards and is_guard(line)) and not is_once(line):
            ids = [int(x) for x in _ID.findall(line)]
            if any(not ({"o", "p"} & set(_flags(cls, sid))) for sid in ids):
                gcd_lines.append(i)
        if cmd == "/stopmacro":
            # GCD lines gated by the same condition that stops the macro are exclusive with the rest
            cond = re.findall(r"\[([^\]]*)\]", line)
            for j in list(gcd_lines):
                clauses = re.findall(r"\[([^\]]*)\]", lines[j])
                if cond and clauses and all(c.startswith(cond[0]) for c in clauses):
                    gcd_lines.remove(j)
        if cmd == "/stopcasting" and i == len(lines) - 1:
            errs.append(f"{name} : /stopcasting en dernière ligne ne sert à rien")
    reqs = {j: _mod_req(lines[j]) for j in gcd_lines}
    clash = [(a, b) for k, a in enumerate(gcd_lines) for b in gcd_lines[k + 1:] if not _exclusive(reqs[a], reqs[b])]
    if clash:
        errs.append(f"{name} : {len(gcd_lines)} lignes sur le temps de recharge global (1 seule par appui)")
    if first.startswith("#showtooltip"):
        arg = first[len("#showtooltip"):].strip()
        if not cast_lines:
            errs.append(f"{name} : #showtooltip sans /cast ni /use")
        elif not arg and guards and is_guard(lines[cast_lines[0]]):
            errs.append(f"{name} : la garde d'aspect est la 1re ligne /cast : #showtooltip <sort principal>")
        elif not arg and gcd_lines and _first_unmodded(lines, cast_lines) not in (gcd_lines[0], None):
            errs.append(f"{name} : la 1re ligne /cast n'est pas l'action principale : #showtooltip <sort>")
        elif arg and not _ID.fullmatch(arg):
            errs.append(f"{name} : #showtooltip doit nommer un sort de la base")
    return errs


def _spec_overlap(a: Macro, b: Macro, cls: str) -> bool:
    sa = set(a.specs or spec_ids(cls))
    sb = set(b.specs or spec_ids(cls))
    return bool(sa & sb)


def lint_class(cls: str) -> list[str]:
    errs: list[str] = []
    ms = macros_for(cls)
    for i, m in enumerate(ms):
        for other in ms[:i]:
            if other.name == m.name and _spec_overlap(m, other, cls):
                errs.append(f"{cls}: nom en double {m.name} pour une même spécialisation")
        for s in m.specs:
            if s not in spec_ids(cls):
                errs.append(f"{cls}: {m.name} spécialisation inconnue {s}")
        if m.ctx not in ("", "solo", "groupe"):
            errs.append(f"{cls}: {m.name} contexte inconnu {m.ctx}")
        if not m.why:
            errs.append(f"{cls}: {m.name} sans « pourquoi »")
        if m.labels and len(m.labels) != len(m.variants):
            errs.append(f"{cls}: {m.name} : {len(m.labels)} libellés pour {len(m.variants)} variantes")
        letters = options_of(cls, m)
        for v in m.variants:
            try:
                tok = tokenize(cls, v)
            except TemplateError as e:
                errs.append(str(e))
                continue
            seen_b: set[str] = set()
            for opts in option_combos(letters):
                body = personalize(tok, **opts)
                if body in seen_b:
                    continue
                seen_b.add(body)
                tag = "" if all(opts.values()) else " (" + ", ".join(k for k, on in opts.items() if not on) + " coupé)"
                errs.extend(f"{cls}: {e}{tag}" for e in lint_rules(cls, m.name, body, "g" in letters))
                for minimal in (False, True):
                    errs.extend(f"{cls}: {e}{tag}" for e in lint(m.name, render(cls, body, minimal)))
        if m.kind != "consumable" and macro_level(cls, m) > 60:
            errs.append(f"{cls}: {m.name} jamais disponible (sorts introuvables) pour {m.specs or 'toutes'}")
    errs.extend(lint_bands(cls))
    return errs


def lint_bands(cls: str) -> list[str]:
    """Render every macro as the game would for each spec × level; lint + slot budget."""
    errs: list[str] = []
    seen: set[tuple[str, str]] = set()
    for spec in spec_ids(cls):
        for level in LEVELS:
            counts = {"solo": 0, "groupe": 0}
            for m in macros_for(cls):
                if not applies(m, spec):
                    continue
                body = simulate_macro(cls, m, spec, level)
                if body is None:
                    continue
                combos = option_combos(options_of(cls, m)) if cls == "HUNTER" else [{"mods": False, "mo": False}]
                for opts in [None] + combos:
                    b = body if opts is None else simulate_macro(cls, m, spec, level, opts=opts)
                    if b is not None and (m.name, b) not in seen:
                        seen.add((m.name, b))
                        errs.extend(f"{cls}/{spec}/niv.{level}: {e}" for e in lint(m.name, b))
                if m.default:
                    for ctx in counts:
                        if m.ctx in ("", ctx):
                            counts[ctx] += 1
            for ctx, n in counts.items():
                if n > MAX_DEFAULT:
                    errs.append(f"{cls}/{spec}/niv.{level}/{ctx}: {n} macros par défaut > {MAX_DEFAULT}")
    return errs


def lint_all() -> list[str]:
    """Every problem of every class macro (templates, renderings, every spec × level band)."""
    errs: list[str] = []
    for cls in CLASSES:
        errs.extend(lint_class(cls))
    return errs


def band_report(cls: str, spec: str) -> dict[tuple[int, int], list[str]]:
    """Macro names buildable at the top of each level band (for docs/tests)."""
    out = {}
    for lo, hi in BANDS:
        out[(lo, hi)] = [m.name for m in macros_for(cls)
                         if applies(m, spec) and simulate_macro(cls, m, spec, hi) is not None]
    return out


def build_class(cls: str) -> list[dict]:
    out = []
    for m in macros_for(cls):
        variants = []
        for v in m.variants:
            body = tokenize(cls, v)
            spells, items = required(cls, body)
            variants.append({"b": body, "r": spells, "it": items})
        out.append({"n": m.name, "d": m.desc, "k": m.key, "i": m.icon, "def": m.default,
                    "c": m.kind, "s": list(m.specs), "x": m.ctx, "w": m.why, "o": m.place,
                    "l": 1 if m.kind == "consumable" else macro_level(cls, m), "v": variants,
                    "sp": m.in_simple, "pr": m.in_pro, "pro": m.pro, "f": options_of(cls, m),
                    "t": m.trick, "vl": list(m.labels), "nf": m.needs})
    return out


# ------------------------------------------------------------------ Lua export
def _lua_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _lua_val(v, indent: str = "") -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return _lua_str(v)
    if isinstance(v, dict):
        inner = ", ".join(f"{k}={_lua_val(x)}" for k, x in v.items())
        return "{" + inner + "}"
    if isinstance(v, (list, tuple)):
        return "{" + ", ".join(_lua_val(x) for x in v) + "}"
    raise TypeError(type(v))


def export_lua(version: str = "2") -> str:
    """Contents of wow_addon/TreeAI/Data/Macros.lua (deterministic)."""
    out = [
        "-- GENERATED by tools/wow_build_macros.py from treeaicoach/wow/macros/ - do not edit.",
        "-- Spell tokens {ID} -> client name (FR fallback below); {item:cat} -> best item in bags;",
        "-- {slot:13} -> trinket slot with a use effect; '?' = optional line/clause.",
        "-- Per macro: n name, d desc, k key, s specs, x ctx (solo/groupe), w why, o where (bar slot),",
        "-- l level, v variants, sp/pr in the Simple/Pro presets (def = Complet), pro = Pro macro, t its trick,",
        "-- f settings offered (m modifiers, o mouseover, p pet, g aspect guard, t tank), vl variant labels,",
        "-- nf needs (focus). D.forms: druid canonical form index -> spell IDs (remapped in game).",
        "-- D.guard: aspect guard spell (« /cast !{ID} » line, removed when the player turns the guard off).",
        "if type(TreeAI) ~= \"table\" then return end",
        "TreeAI.Data = TreeAI.Data or {}",
        "local D = {}",
        "TreeAI.Data.Macros = D",
        f"D.version = {_lua_str(version)}",
        "D.spells = {",
    ]
    names: dict[int, str] = {}
    pets: set[int] = set()
    for cls in CLASSES:
        names.update(_names(cls))
        pets |= _pet_ids(cls)
    for sid in sorted(names):
        out.append(f"  [{sid}] = {_lua_str(names[sid])},")
    out.append("}")
    out.append("D.pet = {" + ", ".join(f"[{s}]=true" for s in sorted(pets)) + "}")
    out.append("D.items = {")
    for cat, rows in consumables.CATEGORIES.items():
        if cat == "petfood":
            continue
        out.append(f"  {cat} = {{")
        for iid, req, val, tag in rows:
            out.append(f"    {{{iid}, {req}, {val}, {_lua_str(tag)}}},")
        out.append("  },")
    out.append("}")
    out.append("D.specs = {")
    for cls in CLASSES:
        rows = ", ".join("{id=%s, n=%s, role=%s}" % (_lua_str(i), _lua_str(n), _lua_str(r))
                         for i, n, r in SPECS[cls])
        out.append(f"  {cls} = {{{rows}}},")
    out.append("}")
    # [form:N] of the templates = Classic order (1 bear, 2 aquatic, 3 cat, 4 travel, 5 moonkin); a druid
    # who skipped a form has them shifted: the addon rewrites N from GetShapeshiftFormInfo at render time.
    dru = SPELLS["DRUID"]
    forms = [[dru["bear"][0], dru["dire_bear"][0]], [dru["aquatic"][0]], [dru["cat"][0]], [dru["travel"][0]],
             [dru["moonkin"][0]]]
    out.append("D.forms = {DRUID = " + _lua_val(forms) + "}")
    out.append("D.leveling = {" + ", ".join(f"{c}={_lua_str(LEVELING_SPEC[c])}" for c in CLASSES) + "}")
    out.append(f"D.guard = {GUARD_ID}")
    out.append("D.classes = {}")
    for cls in CLASSES:
        out.append(f"D.classes.{cls} = {{")
        for m in build_class(cls):
            out.append("  {n=%s, d=%s, k=%s, i=%s, def=%s, c=%s, s=%s, x=%s, l=%d," % (
                _lua_str(m["n"]), _lua_str(m["d"]), _lua_str(m["k"]), _lua_str(m["i"]),
                _lua_val(m["def"]), _lua_str(m["c"]), _lua_val(m["s"]), _lua_str(m["x"]), m["l"]))
            out.append("   w=%s," % _lua_str(m["w"]))
            extra = "   sp=%s, pr=%s, f=%s," % (_lua_val(m["sp"]), _lua_val(m["pr"]), _lua_str(m["f"]))
            if m["pro"]:
                extra += " pro=true,"
            if m["t"]:
                extra += " t=%s," % _lua_str(m["t"])
            if m["vl"]:
                extra += " vl=%s," % _lua_val(m["vl"])
            if m["nf"]:
                extra += " nf=%s," % _lua_str(m["nf"])
            out.append(extra)
            out.append("   o=%s, v={" % _lua_str(m["o"]))
            for v in m["v"]:
                out.append(f"    {{b={_lua_str(v['b'])}, r={_lua_val(v['r'])}, it={_lua_val(v['it'])}}},")
            out.append("  }},")
        out.append("}")
    return "\n".join(out) + "\n"


def all_macros() -> dict[str, list[Macro]]:
    return {cls: macros_for(cls) for cls in CLASSES}
