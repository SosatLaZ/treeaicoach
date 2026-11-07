"""Macro generator: templates -> rendered French macros (lint) and the addon's Data/Macros.lua.

In the Lua data, spells are ``{ID}`` tokens (resolved in game by name from the spell ID, FR name
as fallback) and items ``{item:cat}`` tokens (best item in the bags). ``?`` markers are kept: the
in-game manager drops optional lines/clauses whose spell or item is missing.
"""
from __future__ import annotations

import logging
import re

from . import consumables
from .linter import lint
from .spells import CLASSES, COMMON, PET, SPELLS, lookup
from .templates import Macro, macros_for

log = logging.getLogger(__name__)

_KEY = re.compile(r"\{([a-z_0-9:]+)\}")
_ID = re.compile(r"\{(\d+)\}")
_ITEM = re.compile(r"\{item:([a-z_]+)\}")
_MARK = re.compile(r"\?(?=[\[{!])")
ITEM_SAMPLE = "item:99999"   # worst-case length of a resolved item token


class TemplateError(ValueError):
    pass


def tokenize(cls: str, template: str) -> str:
    """Template keys -> Lua tokens ({key} -> {ID}); items untouched. Raises on unknown key."""
    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key.startswith("item:"):
            if key[5:] not in consumables.CATEGORIES:
                raise TemplateError(f"catégorie d'objet inconnue {key}")
            return m.group(0)
        hit = lookup(cls, key)
        if hit is None:
            raise TemplateError(f"{cls}: sort inconnu {{{key}}}")
        return "{%d}" % hit[0]
    return _KEY.sub(sub, template)


def _names(cls: str) -> dict[int, str]:
    out = {}
    for table in (SPELLS.get(cls, {}), COMMON, PET.get(cls, {})):
        for sid, name in table.values():
            out[sid] = name
    return out


def _pet_ids(cls: str) -> set[int]:
    return {sid for sid, _ in PET.get(cls, {}).values()}


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
        lines.append(line)
    return "\n".join(lines)


def build_class(cls: str) -> list[dict]:
    out = []
    for m in macros_for(cls):
        variants = []
        for v in m.variants:
            body = tokenize(cls, v)
            spells, items = required(cls, body)
            variants.append({"b": body, "r": spells, "it": items})
        out.append({"n": m.name, "d": m.desc, "k": m.key, "i": m.icon, "def": m.default,
                    "c": m.kind, "v": variants})
    return out


def lint_all() -> list[str]:
    """Every problem of every class macro (full and minimal renderings)."""
    errs: list[str] = []
    for cls in CLASSES:
        seen = set()
        count = 0
        for m in macros_for(cls):
            if m.name in seen:
                errs.append(f"{cls}: nom en double {m.name}")
            seen.add(m.name)
            count += m.default
            for v in m.variants:
                try:
                    body = tokenize(cls, v)
                except TemplateError as e:
                    errs.append(str(e))
                    continue
                for minimal in (False, True):
                    errs.extend(f"{cls}: {e}" for e in lint(m.name, render(cls, body, minimal)))
        if count > 18:
            errs.append(f"{cls}: {count} macros par défaut > 18 emplacements perso")
    return errs


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


def export_lua(version: str = "1") -> str:
    """Contents of wow_addon/TreeAI/Data/Macros.lua (deterministic)."""
    out = [
        "-- GENERATED by tools/wow_build_macros.py from treeaicoach/wow/macros/ - do not edit.",
        "-- Spell tokens {ID} -> GetSpellInfo name (FR fallback below); {item:cat} -> best item in",
        "-- bags; '?' = optional line/clause. Rank-agnostic names (highest learned rank).",
        "if type(TreeAI) ~= \"table\" then return end",
        "TreeAI.Data = TreeAI.Data or {}",
        "local D = {}",
        "TreeAI.Data.Macros = D",
        f"D.version = {_lua_str(version)}",
        "D.spells = {",
    ]
    names: dict[int, str] = {}
    for cls in CLASSES:
        names.update(_names(cls))
    for sid in sorted(names):
        out.append(f"  [{sid}] = {_lua_str(names[sid])},")
    out.append("}")
    pets = sorted({sid for t in PET.values() for sid, _ in t.values()})
    out.append("D.pet = {" + ", ".join(f"[{s}]=true" for s in pets) + "}")
    out.append("D.items = {")
    for cat, rows in consumables.CATEGORIES.items():
        if cat == "petfood":
            continue
        out.append(f"  {cat} = {{")
        for iid, req, val, tag in rows:
            out.append(f"    {{{iid}, {req}, {val}, {_lua_str(tag)}}},")
        out.append("  },")
    out.append("}")
    out.append("D.classes = {}")
    for cls in CLASSES:
        out.append(f"D.classes.{cls} = {{")
        for m in build_class(cls):
            out.append("  {n=%s, d=%s, k=%s, i=%s, def=%s, c=%s, v={" % (
                _lua_str(m["n"]), _lua_str(m["d"]), _lua_str(m["k"]), _lua_str(m["i"]),
                _lua_val(m["def"]), _lua_str(m["c"])))
            for v in m["v"]:
                out.append(f"    {{b={_lua_str(v['b'])}, r={_lua_val(v['r'])}, it={_lua_val(v['it'])}}},")
            out.append("  }},")
        out.append("}")
    return "\n".join(out) + "\n"


def all_macros() -> dict[str, list[Macro]]:
    return {cls: macros_for(cls) for cls in CLASSES}
