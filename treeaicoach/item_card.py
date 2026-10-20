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
LINGER_S = 6.0
#: after a respawn I stand in my fountain, but the tracker needs a few seconds to see me there
RESPAWN_LINGER_S = 8.0
#: after the card is gone, a "Achète X : tu as l'or" line of the shop (tips.py, 12 s) is stale: hidden
TAIL_S = 12.0
#: items that appear in the inventory OUT of the shop (support quest upgrades, Muramana, Séraphin,
#: Fimbulwinter): not a proof that I am shopping
_NOT_SHOP = frozenset(range(3865, 3878)) | {3040, 3042, 3121}
#: no item card in the first minutes (the start in the fountain belongs to the team plan)
MIN_GT = 150.0


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
    buy_ids: tuple[int, ...] = ()   # components the gold buys now (the whole item is not affordable)
    buy_names: tuple[str, ...] = ()
    buy_gold: int = 0               # their cost
    ward: bool = False              # the gold left also buys a control ward (itemization extras)

    @property
    def shown_price(self) -> int:
        """The price printed: the components bought now, else the whole item."""
        return int(self.buy_gold) if self.buy_ids and not self.affordable else int(self.price)

    @property
    def buy_now(self) -> bool:
        """Something is bought now: the whole item, or its components (green border)."""
        return self.affordable or bool(self.buy_ids)

    @property
    def title(self) -> str:
        """Verb first: "Achète Estropieur" / "Achète Phage" (component now) / "Vise Estropieur"."""
        if self.affordable:
            return f"Achète {self.name}"
        if self.buy_names:      # the first component only: the title does not flicker as the gold grows
            return f"Achète {self.buy_names[0]}"
        return f"Vise {self.name}"

    @property
    def icon_id(self) -> int:
        """The icon shown: what the click buys now (the component), else the item itself."""
        return int(self.buy_ids[0]) if self.buy_ids and not self.affordable else int(self.item_id)

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
        buy_ids: tuple[int, ...] = ()
        buy_names: tuple[str, ...] = ()
        buy_gold = 0
        if affordable:
            sub, short = (why, why_short) if why else ("tu as l'or : achète-le", "tu as l'or")
        elif getattr(rec, "buy_now", ()) and not getattr(rec, "completes", False):
            # a component now, on the way to the item: "Achète Phage" · "vers Estropieur (objet 2 du build)"
            buy_ids = tuple(int(i) for i in rec.buy_now)[:2]
            buy_names = tuple(str(n) for n in getattr(rec, "buy_now_names", ()) or ())[:2]
            pool = list(own)
            for b in buy_ids:
                buy_gold += int(iz.remaining_cost(b, pool, items)) if b in items else 0
                pool.append(b)
            head = f"vers {rec.item_name}"
            if len(buy_names) > 1:
                head = f"puis {buy_names[1]}, " + head
            sub, short = (f"{head} ({why_short})" if why.startswith("objet") else head), f"vers {rec.item_name}"
        else:
            miss = f"il manque {fmt_gold(max(0, price - gold_i))} PO"
            sub, short = (f"{miss} · {why_short}" if why.startswith("objet") else miss), miss
        ward = 2055 in tuple(getattr(rec, "extras", ()) or ())
        if ward and (affordable or buy_ids):    # the gold left buys a control ward too (2nd line)
            sub, short = f"+ balise · {sub}", "+ balise de contrôle"
        return ItemCard(iid, str(rec.item_name), price, gold_i, affordable, sub, bool(shop),
                        bool(getattr(rec, "meta", False)), short, buy_ids, buy_names, buy_gold, ward)
    except Exception:
        log.debug("item card failed", exc_info=True)
        return None


class ItemCardState:
    """When the card shows: in base / dead, then :data:`LINGER_S` after leaving; out of base, when
    the gold buys the whole item (``shop=False``: shown only when the card is otherwise empty)."""

    def __init__(self) -> None:
        self._last_shop: float | None = None
        self._last_dead: float | None = None
        self._owned: frozenset[int] | None = None

    def tail(self, now: float) -> bool:
        """The shop card ended less than :data:`TAIL_S` ago (buy lines of the shop are stale then)."""
        last = self._last_shop if self._last_dead is None else max(self._last_shop or 0.0, self._last_dead)
        return last is not None and LINGER_S < float(now) - last <= LINGER_S + TAIL_S

    def update(self, now: float, rec: Any, gold: float, owned: Any, in_base: bool, dead: bool,
               items: dict | None = None, game_time: float | None = None) -> ItemCard | None:
        try:
            if game_time is not None and game_time < MIN_GT and not dead:
                return None              # the start in the fountain: the starting items are the client's
            own = frozenset(int(i) for i in owned or () if int(i or 0) > 0)
            if self._owned is not None and any(i not in _NOT_SHOP for i in own - self._owned):
                in_base = True           # a new item in my inventory (Live Client): I am at the shop,
                #                          even when the minimap tracker does not see me in my fountain
            self._owned = own
            if in_base or dead:
                self._last_shop = float(now)
            if dead:
                self._last_dead = float(now)
            shop = (self._last_shop is not None and 0.0 <= float(now) - self._last_shop <= LINGER_S) or \
                (self._last_dead is not None and 0.0 <= float(now) - self._last_dead <= RESPAWN_LINGER_S)
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
