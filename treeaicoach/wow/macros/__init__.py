"""WoW Forever smart macros: knowledge base (9 classes × specs × level bands), generator, linter.

``tools/wow_build_macros.py`` writes ``wow_addon/TreeAI/Data/Macros.lua`` from this package;
the in-game manager is ``wow_addon/TreeAI/Modules/Macros.lua``.
"""
from .generator import (band_report, build_class, export_lua, lint_all, lint_rules, render,  # noqa: F401
                        required, simulate, simulate_macro, tokenize)
from .linter import lint  # noqa: F401
