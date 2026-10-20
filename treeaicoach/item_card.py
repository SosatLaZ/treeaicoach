"""The in-game item highlight: the next item of my build, with its icon, OP.GG style.

When I can shop (in my fountain zone, or dead) the compact HUD card becomes this item card: the
item's icon (Data Dragon, :mod:`treeaicoach.item_icons`), its name, its price, "objet 2 du build
(55 % de victoires)" or "il manque 450 PO", and a green plate border when the gold buys it now.
It stays :data:`LINGER_S` after I leave the base, then the card goes back to the coaching line.
Out of base it only shows when my gold buys the whole item and the card has nothing else to say
(:func:`treeaicoach.overlay_render.compact_content` decides; danger always wins).

No new window and no new zone over the game: it is drawn in the HUD card's own slot, sized for
it (``docs/LESSONS.md`` 18), and it is a frozen value so the overlay re-uploads only when it
changes (LESSONS 21). Pure; never raises.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

#: seconds the card stays after leaving the base (the shop closes, the card follows)
LINGER_S = 4.0


@dataclass(frozen=True)
class ItemCard:
    item_id: int
    name: str
    price: int                 # gold still to pay for the whole item (owned components deducted)
    gold: int                  # my current gold
    affordable: bool           # the whole item can be bought now (green border)
    sub: str                   # second line after the price ("objet 2 du build · 55 % de victoires")
    shop: bool                 # I can shop now (in base / dead, or lingering after the base)
    meta: bool = False         # the item comes from the live meta build
    sub_short: str = ""        # the same, shorter ("objet 2 du build (55 %)"), when ``sub`` does not fit

    @property
    def missing(self) -> int:
        return max(0, int(self.price) - int(self.gold))


def fmt_gold(v: Any) -> str:
    try:
        return f"{int(v):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "?"


def _why(rec: Any) -> tuple[str, str]:
    """("objet 2 du build · 55 % de victoires", "objet 2 du build (55 %)") / the counter reason."""
    reason = str(getattr(rec, "reason", "") or "").strip()
    if getattr(rec, "meta", False) and reason.startswith("objet "):
        # "objet 2 du build du moment (55.4 % de victoires)" -> "objet 2 du build · 55 % de victoires"
        head = reason.split(" (", 1)[0].replace(" du moment", "")
        wr = float(getattr(rec, "win_rate", 0.0) or 0.0)
        return (f"{head} · {wr:.0f} % de victoires", f"{head} ({wr:.0f} %)") if wr > 0 else (head, head)
    if reason:
        reason = reason.split(" (", 1)[0]
        reason = reason[0].lower() + reason[1:] if not reason[:2].istitle() or reason.startswith("L'") else reason
        return reason, reason
    return "", ""


def build_card(rec: Any, gold: float, owned: Any = (), shop: bool = False,
               items: dict | None = None) -> ItemCard | None:
    """The item card of a :class:`treeaicoach.itemization.Recommendation` (None without one)."""
    try:
        if rec is None or not getattr(rec, "item_id", 0):
            return None
        from treeaicoach import itemization as iz

        items = items if items is not None else iz.load_items()
        iid = int(rec.item_id)
        own = [int(i) for i in owned or ()]
        price = int(iz.remaining_cost(iid, own, items)) if iid in items else 0
        gold_i = int(max(0.0, float(gold or 0.0)))
        affordable = bool(price > 0 and gold_i >= price)
        why, why_short = _why(rec)
        if affordable:
            sub, short = (why, why_short) if why else ("tu as l'or : achète-le", "tu as l'or")
        elif getattr(rec, "buy_now_names", ()) and not getattr(rec, "completes", False):
            names = list(rec.buy_now_names)
            sub, short = "pour l'instant : " + " + ".join(names[:2]), "pour l'instant : " + names[0]
        else:
            miss = f"il manque {fmt_gold(max(0, price - gold_i))} PO"
            sub, short = (f"{miss} · {why_short}" if why.startswith("objet") else miss), miss
        return ItemCard(iid, str(rec.item_name), price, gold_i, affordable, sub, bool(shop),
                        bool(getattr(rec, "meta", False)), short)
    except Exception:
        log.debug("item card failed", exc_info=True)
        return None


class ItemCardState:
    """When the card shows: in base / dead, then :data:`LINGER_S` after leaving; out of base, when
    the gold buys the whole item (``shop=False``: shown only when the card is otherwise empty)."""

    def __init__(self) -> None:
        self._last_shop: float | None = None

    def update(self, now: float, rec: Any, gold: float, owned: Any, in_base: bool, dead: bool,
               items: dict | None = None) -> ItemCard | None:
        try:
            if in_base or dead:
                self._last_shop = float(now)
            shop = self._last_shop is not None and 0.0 <= float(now) - self._last_shop <= LINGER_S
            if rec is not None and int(getattr(rec, "item_id", 0) or 0) in {int(i) for i in owned or ()}:
                return None              # just bought: the advice of the next item is not computed yet
            card = build_card(rec, gold, owned, shop=shop, items=items)
            if card is None:
                return None
            if shop or card.affordable:
                return card
            return None
        except Exception:
            log.debug("item card state failed", exc_info=True)
            return None


__all__ = ["ItemCard", "ItemCardState", "build_card", "LINGER_S", "fmt_gold"]
