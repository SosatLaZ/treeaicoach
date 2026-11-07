"""WoW Forever smart macros: knowledge base (9 classes), generator and 255-char linter.

``tools/wow_build_macros.py`` writes ``wow_addon/TreeAI/Data/Macros.lua`` from this package;
the in-game manager is ``wow_addon/TreeAI/Modules/Macros.lua``.
"""
from .generator import build_class, export_lua, lint_all, render, required, tokenize  # noqa: F401
from .linter import lint  # noqa: F401
