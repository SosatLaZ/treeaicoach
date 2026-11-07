"""Safe reader of WoW SavedVariables files (``WTF/Account/*/SavedVariables/TreeAI.lua``).

WoW writes them as plain Lua assignments of table constructors::

    TreeAIDB = {
        ["version"] = 1,
        ["chars"] = { ["Realm-Name"] = { ["level"] = 26, ... }, },
        [1] = "x", -- [1]
    }

This is a small tokenizer + recursive-descent parser for exactly that subset (literals, strings with
escapes, long brackets, tables, comments, ``nil``, ``-``/hex/exponent numbers, ``NAME = value``
statements). No ``eval``, no Lua runtime: anything else is a :class:`SavedVarsError`. Tables with only the
keys 1..n become lists, other tables dicts (integer keys stay ints). Bounded size and depth.
"""

from __future__ import annotations

import logging
import math
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MAX_BYTES = 32_000_000
MAX_DEPTH = 100

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f", "v": "\v",
            "\\": "\\", '"': '"', "'": "'", "\n": "\n"}
_NUM_RE = re.compile(r"0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LONG_OPEN = re.compile(r"\[(=*)\[")


class SavedVarsError(ValueError):
    """The text is not a SavedVariables file this parser accepts."""


class _Parser:
    def __init__(self, text: str) -> None:
        self.s = text
        self.i = 0
        self.n = len(text)

    # -------------------------------------------------------------- lexing helpers
    def err(self, what: str) -> SavedVarsError:
        line = self.s.count("\n", 0, self.i) + 1
        return SavedVarsError(f"{what} (ligne {line})")

    def skip(self) -> None:
        s, n = self.s, self.n
        while self.i < n:
            c = s[self.i]
            if c in " \t\r\n\f\v":
                self.i += 1
            elif s.startswith("--", self.i):
                m = _LONG_OPEN.match(s, self.i + 2)
                if m:
                    close = "]" + m.group(1) + "]"
                    end = s.find(close, m.end())
                    if end < 0:
                        raise self.err("commentaire non fermé")
                    self.i = end + len(close)
                else:
                    end = s.find("\n", self.i)
                    self.i = n if end < 0 else end + 1
            else:
                break

    def peek(self) -> str:
        self.skip()
        return self.s[self.i] if self.i < self.n else ""

    def expect(self, ch: str) -> None:
        if self.peek() != ch:
            raise self.err(f"« {ch} » attendu")
        self.i += 1

    # -------------------------------------------------------------- values
    def string(self) -> str:
        q = self.s[self.i]
        self.i += 1
        out: list[str] = []
        s, n = self.s, self.n
        while True:
            if self.i >= n:
                raise self.err("chaîne non fermée")
            c = s[self.i]
            if c == q:
                self.i += 1
                return "".join(out)
            if c == "\n":
                raise self.err("retour à la ligne dans une chaîne")
            if c == "\\":
                self.i += 1
                if self.i >= n:
                    raise self.err("échappement incomplet")
                e = s[self.i]
                if e in _ESCAPES:
                    out.append(_ESCAPES[e])
                    self.i += 1
                elif e.isdigit():
                    j = self.i
                    while j < n and j - self.i < 3 and s[j].isdigit():
                        j += 1
                    code = int(s[self.i:j])
                    if code > 255:
                        raise self.err("échappement trop grand")
                    out.append(chr(code))
                    self.i = j
                else:
                    out.append(e)                    # Lua 5.1 keeps unknown escapes' character
                    self.i += 1
            else:
                out.append(c)
                self.i += 1

    def long_string(self, m: re.Match[str]) -> str:
        close = "]" + m.group(1) + "]"
        end = self.s.find(close, m.end())
        if end < 0:
            raise self.err("chaîne longue non fermée")
        body = self.s[m.end():end]
        self.i = end + len(close)
        if body.startswith("\r\n"):
            body = body[2:]
        elif body.startswith("\n"):
            body = body[1:]
        return body

    def number(self, neg: bool) -> int | float:
        m = _NUM_RE.match(self.s, self.i)
        if not m:
            raise self.err("nombre attendu")
        tok = m.group(0)
        self.i = m.end()
        if tok[:2].lower() == "0x":
            v: int | float = int(tok, 16)
        elif any(c in tok for c in ".eE"):
            v = float(tok)
        else:
            v = int(tok)
        return -v if neg else v

    def value(self, depth: int) -> Any:
        if depth > MAX_DEPTH:
            raise self.err("tables trop imbriquées")
        c = self.peek()
        if not c:
            raise self.err("fin de fichier inattendue")
        if c == "{":
            return self.table(depth + 1)
        if c in "\"'":
            return self.string()
        if c == "[":
            m = _LONG_OPEN.match(self.s, self.i)
            if m:
                return self.long_string(m)
            raise self.err("valeur attendue")
        if c == "-":
            self.i += 1
            self.skip()
            nxt = self.s[self.i:self.i + 4]
            if nxt.startswith("inf") or nxt.startswith("nan"):    # some writers emit -inf / -nan
                self.i += 3
                return -math.inf if nxt.startswith("inf") else math.nan
            return self.number(True)
        if c.isdigit() or c == ".":
            return self.number(False)
        m = _NAME_RE.match(self.s, self.i)
        if m:
            word = m.group(0)
            self.i = m.end()
            if word == "true":
                return True
            if word == "false":
                return False
            if word == "nil":
                return None
            if word in ("inf", "nan"):
                return math.inf if word == "inf" else math.nan
            raise self.err(f"identifiant inattendu « {word} »")
        raise self.err("valeur attendue")

    def table(self, depth: int) -> Any:
        self.expect("{")
        d: dict[Any, Any] = {}
        auto = 1
        while True:
            c = self.peek()
            if c == "}":
                self.i += 1
                break
            if c == "[" and not _LONG_OPEN.match(self.s, self.i):
                self.i += 1
                key = self.value(depth)
                self.expect("]")
                self.expect("=")
                val = self.value(depth)
                if isinstance(key, float) and key.is_integer():
                    key = int(key)
                if key is not None and val is not None:
                    d[key] = val
            else:
                m = _NAME_RE.match(self.s, self.i)
                save = self.i
                if m and m.group(0) not in ("true", "false", "nil"):
                    self.i = m.end()
                    if self.peek() == "=":
                        self.i += 1
                        val = self.value(depth)
                        if val is not None:
                            d[m.group(0)] = val
                    else:
                        self.i = save
                        raise self.err(f"identifiant inattendu « {m.group(0)} »")
                else:
                    val = self.value(depth)
                    if val is not None:
                        d[auto] = val
                    auto += 1
            c = self.peek()
            if c in (",", ";"):
                self.i += 1
            elif c != "}":
                raise self.err("« , » ou « } » attendu")
        if d and all(isinstance(k, int) and not isinstance(k, bool) for k in d) \
                and sorted(d) == list(range(1, len(d) + 1)):
            return [d[k] for k in range(1, len(d) + 1)]
        return d

    def chunk(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        while True:
            c = self.peek()
            if not c:
                return out
            if c == ";":
                self.i += 1
                continue
            m = _NAME_RE.match(self.s, self.i)
            if not m:
                raise self.err("nom de variable attendu")
            self.i = m.end()
            self.expect("=")
            out[m.group(0)] = self.value(0)


def parse(text: str) -> dict[str, Any]:
    """{variable name: value} of a SavedVariables file. Raises :class:`SavedVarsError`."""
    if not isinstance(text, str):
        raise SavedVarsError("texte attendu")
    if len(text) > MAX_BYTES:
        raise SavedVarsError("fichier trop gros")
    if text.startswith("﻿"):
        text = text[1:]
    try:
        return _Parser(text).chunk()
    except RecursionError as exc:
        raise SavedVarsError("tables trop imbriquées") from exc


def load(path: str | Path) -> dict[str, Any]:
    """Parsed file, {} when missing / too big / invalid. Never raises."""
    try:
        p = Path(path)
        if not p.is_file() or p.stat().st_size > MAX_BYTES:
            return {}
        return parse(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        log.debug("cannot read SavedVariables %s", path, exc_info=True)
        return {}


def read_db(path: str | Path | None, name: str = "TreeAIDB") -> dict[str, Any]:
    """The addon's database table (a dict; {} when absent). Never raises."""
    if path is None:
        return {}
    db = load(path).get(name)
    if isinstance(db, list):
        return {i + 1: v for i, v in enumerate(db)}
    return db if isinstance(db, dict) else {}


def load_treeai_db() -> dict | None:
    """TreeAIDB of the WoW install TreeAI uses (the WoW mode's, else found from the saved config), newest
    SavedVariables file; None when not found. Never raises."""
    try:
        from treeaicoach.wow import optional  # noqa: PLC0415

        mode, install = optional("mode"), optional("install")
        m = mode.current() if mode is not None else None
        if m is not None and m.db is not None:
            return m.db
        inst = getattr(m, "install", None) if m is not None else None
        if inst is None and install is not None:
            path = ""
            try:
                from treeaicoach.config import load_config  # noqa: PLC0415

                path = str(getattr(load_config(), "wow_path", "") or "")
            except Exception:
                pass
            inst = install.find_wow(path)
        files = install.savedvars_files(inst) if install is not None and inst is not None else []
        return (read_db(files[0]) or None) if files else None
    except Exception:
        log.debug("load_treeai_db failed", exc_info=True)
        return None
