"""Write the plan as a data addon: ``Interface/AddOns/TreeAI_Plan/{TreeAI_Plan.toc, Plan.lua}``.

The game never writes this folder (unlike SavedVariables), so the app may write it while WoW runs. The
player types /reload to load it (or it loads at the next login). The write is atomic: the files are
written into a sibling temp folder, then swapped in, as the addon installer does. ``Plan.lua`` holds a
single global assignment ``TreeAIPlan = {...}``, which is pure data (SCHEMA.md §3).
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PLAN_ADDON = "TreeAI_Plan"
TOC = """## Interface: 11507, 16001
## Title: TreeAI Plan
## Notes: Trajet calculé par TreeAI Coach (fichier généré, ne pas modifier)
## Author: TreeAI Coach
## Version: {made}
Plan.lua
"""
_IDENT = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789")
_LUA_KEYWORDS = {"and", "break", "do", "else", "elseif", "end", "false", "for", "function", "if", "in", "local",
                 "nil", "not", "or", "repeat", "return", "then", "true", "until", "while", "goto"}
MAX_DEPTH = 12


def lua_str(s: str) -> str:
    out = ['"']
    for ch in str(s):
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif o < 32 or o == 127:
            out.append(f"\\{o:03d}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _key(k: Any) -> str:
    if isinstance(k, str) and k and k[0] not in "0123456789" and set(k) <= _IDENT and k not in _LUA_KEYWORDS:
        return k
    if isinstance(k, bool):
        return f"[{'true' if k else 'false'}]"
    if isinstance(k, (int, float)):
        return f"[{_num(k)}]"
    return f"[{lua_str(str(k))}]"


def _num(v: float) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if not math.isfinite(v):
        return "0"
    if float(v).is_integer():
        return str(int(v))
    return repr(round(float(v), 3))


def to_lua(v: Any, indent: int = 0, depth: int = 0) -> str:
    """Python value → Lua literal (dict → table with keys, list → array). Unknown types → nil."""
    if depth > MAX_DEPTH:
        return "nil"
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return _num(v)
    if isinstance(v, str):
        return lua_str(v)
    pad = "  " * (indent + 1)
    if isinstance(v, (list, tuple)):
        if not v:
            return "{}"
        flat = all(not isinstance(x, (dict, list, tuple)) for x in v)
        if flat:
            return "{ " + ", ".join(to_lua(x, 0, depth + 1) for x in v) + " }"
        return "{\n" + "".join(f"{pad}{to_lua(x, indent + 1, depth + 1)},\n" for x in v) + "  " * indent + "}"
    if isinstance(v, dict):
        items = [(k, x) for k, x in v.items() if x is not None]
        if not items:
            return "{}"
        flat = all(not isinstance(x, (dict, list, tuple)) for _, x in items)
        if flat and len(items) <= 12:
            return "{ " + ", ".join(f"{_key(k)} = {to_lua(x, 0, depth + 1)}" for k, x in items) + " }"
        return ("{\n" + "".join(f"{pad}{_key(k)} = {to_lua(x, indent + 1, depth + 1)},\n" for k, x in items)
                + "  " * indent + "}")
    return "nil"


def plan_lua(plan: dict) -> str:
    return ("-- GÉNÉRÉ par TreeAI Coach (planificateur de trajets) — ne pas modifier.\n"
            "TreeAIPlan = " + to_lua(plan) + "\n")


def write_plan(addons_dir: str | Path, plan: dict) -> tuple[bool, str]:
    """Write ``TreeAI_Plan`` into ``addons_dir`` atomically. (ok, French message). Never raises."""
    try:
        base = Path(addons_dir)
        base.mkdir(parents=True, exist_ok=True)
        dest = base / PLAN_ADDON
        stamp = f"{os.getpid()}_{int(time.time() * 1000)}"
        tmp = base / f".{PLAN_ADDON}.new_{stamp}"
        old = base / f".{PLAN_ADDON}.old_{stamp}"
        tmp.mkdir()
        made = int(plan.get("made") or time.time())
        (tmp / f"{PLAN_ADDON}.toc").write_text(TOC.format(made=made), encoding="utf-8", newline="\n")
        (tmp / "Plan.lua").write_text(plan_lua(plan), encoding="utf-8", newline="\n")
        existed = dest.is_dir()
        if existed:
            os.replace(dest, old)
        try:
            os.replace(tmp, dest)
        except OSError:
            if existed:
                os.replace(old, dest)
            raise
        if existed:
            shutil.rmtree(old, ignore_errors=True)
        return True, "Trajet prêt. Tape /reload en jeu pour le charger."
    except Exception as exc:
        log.debug("plan write failed", exc_info=True)
        try:
            for leftover in Path(addons_dir).glob(f".{PLAN_ADDON}.new_*"):
                shutil.rmtree(leftover, ignore_errors=True)
        except Exception:
            pass
        hint = " Lance TreeAI en administrateur." if isinstance(exc, PermissionError) else ""
        return False, f"Écriture du trajet impossible : {type(exc).__name__}.{hint}"


def read_plan(addons_dir: str | Path | None) -> dict | None:
    """The plan currently written (parsed back from Plan.lua), None when absent. Never raises."""
    if not addons_dir:
        return None
    try:
        from treeaicoach.wow import savedvars  # noqa: PLC0415

        p = Path(addons_dir) / PLAN_ADDON / "Plan.lua"
        v = savedvars.load(p).get("TreeAIPlan")
        return v if isinstance(v, dict) else None
    except Exception:
        log.debug("plan read failed", exc_info=True)
        return None
