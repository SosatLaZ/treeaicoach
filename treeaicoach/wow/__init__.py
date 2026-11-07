"""World of Warcraft: Forever helper for TreeAI Coach.

Blizzard rules first: TreeAI never sends keys, never reads the game's memory, network or screen.
It only reads the game's own files, the way Warcraft Logs or a SavedVariables viewer does:

* ``Logs/WoWCombatLog.txt`` (``/combatlog`` in game) -> :mod:`.combatlog`, :mod:`.coach`, :mod:`.report`
* ``WTF/Account/*/SavedVariables/TreeAI.lua`` (written by the TreeAI addon on /reload or logout)
  -> :mod:`.savedvars`, :mod:`.leveling`, :mod:`.ai_context`

and it installs / updates the in-game addon (``wow_addon/TreeAI``) in ``Interface/AddOns`` (:mod:`.install`).
:mod:`.process` tells whether WoW / LoL run, :mod:`.mode` switches TreeAI to "WoW mode" when WoW runs
and LoL does not.

Every submodule is optional for the others: import them through :func:`optional` (``None`` when a
module is missing or broken), never at import time of this package.
"""

from __future__ import annotations

import importlib
import logging
from types import ModuleType

log = logging.getLogger(__name__)

#: Submodules of the WoW helper (some are written separately and may be absent in a given build).
SUBMODULES: tuple[str, ...] = ("install", "process", "savedvars", "mode", "combatlog", "coach", "report",
                               "leveling", "ai_context", "macros", "classes", "live", "auction")
ADDON_NAME = "TreeAI"
SAVEDVARS_NAME = "TreeAIDB"


def optional(name: str) -> ModuleType | None:
    """``treeaicoach.wow.<name>`` or None when it is missing / fails to import. Never raises."""
    try:
        return importlib.import_module(f"{__name__}.{name}")
    except Exception:
        log.debug("wow.%s unavailable", name, exc_info=True)
        return None


def wow_mode_active() -> bool:
    """True while the engine's WoW mode is on (WoW runs, no League game). Never raises."""
    mod = optional("mode")
    try:
        m = mod.current() if mod is not None else None
        return bool(m is not None and m.active)
    except Exception:
        return False


def call(module: str, func: str, *args: object, default: object = None, **kwargs: object) -> object:
    """``treeaicoach.wow.<module>.<func>(*args, **kwargs)``, or ``default`` when the module / function
    is missing or raises. Never raises."""
    mod = optional(module)
    fn = getattr(mod, func, None) if mod is not None else None
    if not callable(fn):
        return default
    try:
        return fn(*args, **kwargs)
    except Exception:
        log.debug("wow.%s.%s failed", module, func, exc_info=True)
        return default
