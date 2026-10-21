"""The build strip: the meta build of my champion as one row of small item icons.

Next to the HUD card slot (layout.py, never over the game's UI), shown in base / while dead / while
the details key is held, hidden in fights. Bought items are dimmed with a check, the next one has a
green outline and the gold still missing under it. The card says WHAT to buy now; the strip shows
WHERE the build goes (the user asked for "un tout petit panneau pour montrer le meilleur build").

Pure data: a frozen value (compared by the overlay to re-upload only on change, LESSONS 21), built
from :mod:`treeaicoach.meta_builds` and the Live Client inventory. Never raises.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

#: icons in the strip (the meta core with its boots, then the best situational options)
MAX_ITEMS = 6
#: no strip before this game time (s): the start in the fountain belongs to the client's shop
MIN_GT = 150.0


@dataclass(frozen=True)
class StripItem:
    item_id: int
    status: str          # "owned" | "next" | "todo"
    missing: int = 0     # gold still missing for the next item (0 for the others)


@dataclass(frozen=True)
class BuildStrip:
    items: tuple[StripItem, ...]

    @property
    def ids(self) -> tuple[int, ...]:
        return tuple(i.item_id for i in self.items)

    @property
    def done(self) -> int:
        return sum(1 for i in self.items if i.status == "owned")


def strip_ids(build: Any, items: dict | None = None) -> tuple[int, ...]:
    """The icons of a meta build: core (boots at their place; starters / consumables left out),
    then the best situational options, at most :data:`MAX_ITEMS`."""
    core = [int(i) for i in getattr(build, "core", ()) or ()]
    if items is not None:
        core = [i for i in core if getattr(items.get(i), "kind", "legendary") not in ("starter", "consumable")]
    boots = getattr(build, "boots", None)
    if boots and int(boots) not in core:
        core.append(int(boots))
    out = list(dict.fromkeys(core))
    for iid, _wr, _n in getattr(build, "options", ()) or ():
        if len(out) >= MAX_ITEMS:
            break
        if int(iid) not in out and (items is None or items.get(int(iid)) is not None):
            out.append(int(iid))
    return tuple(out[:MAX_ITEMS])


def make_strip(build: Any, owned: Any, gold: float, items: dict | None = None,
               next_id: int | None = None) -> BuildStrip | None:
    """The strip of ``build`` for my inventory ``owned`` and ``gold``; ``next_id``: the item the
    advisor wants next (highlighted when it is in the strip, else the first one not owned)."""
    try:
        if build is None or not getattr(build, "has_items", False):
            return None
        ids = strip_ids(build, items)
        if len(ids) < 2:
            return None
        own = [int(i) for i in owned or () if int(i or 0) > 0]

        def have(iid: int) -> bool:
            return iid in own or (items is not None and any(
                o in items and iid in getattr(items[o], "parts", ()) for o in own))

        todo = [i for i in ids if not have(i)]
        nxt = next_id if next_id in todo else (todo[0] if todo else None)
        miss = 0
        if nxt is not None and items is not None and nxt in items:
            from treeaicoach import itemization as iz

            miss = max(0, int(iz.remaining_cost(nxt, own, items)) - int(max(0.0, float(gold or 0.0))))
        out = tuple(StripItem(i, "owned" if have(i) else "next" if i == nxt else "todo", miss if i == nxt else 0)
                    for i in ids)
        return BuildStrip(out)
    except Exception:
        log.debug("build strip failed", exc_info=True)
        return None


__all__ = ["BuildStrip", "StripItem", "make_strip", "strip_ids", "MAX_ITEMS", "MIN_GT"]
