"""Smart order router: sweep the best-priced liquidity across venues, then rest any remainder for rebate."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from fixsim.model import Order
from fixsim.venue import Fill, Venue


@dataclass
class ChildOrder:
    ref: str
    venue: str
    price: float | None
    qty: int
    posted: bool


@dataclass
class RouteResult:
    fills: list[Fill] = field(default_factory=list)
    children: list[ChildOrder] = field(default_factory=list)


class SmartOrderRouter:
    def __init__(self, venues: list[Venue]):
        self.venues = {v.mic: v for v in venues}
        self._child_ids = itertools.count(1)
        self._resting: dict[str, list[tuple[str, str]]] = {}

    def lists(self, key: str) -> bool:
        return any(v.lists(key) for v in self.venues.values())

    def plan(self, order: Order, qty: int, limit: float | None) -> list[tuple[Venue, float, int]]:
        """Allocate qty across venue price levels: best price, then lowest take fee, then venue name."""
        candidates = []
        for v in self.venues.values():
            for price, available in v.levels(order.instrument.key, order.is_buy, limit, order.order_id):
                rank = price if order.is_buy else -price
                candidates.append((rank, v.take_fee, v.mic, price, available, v))
        candidates.sort(key=lambda c: c[:3])
        allocation, remaining = [], qty
        for _, _, _, price, available, venue in candidates:
            if remaining == 0:
                break
            take = min(remaining, available)
            allocation.append((venue, price, take))
            remaining -= take
        return allocation

    def available(self, order: Order, limit: float | None) -> int:
        return sum(q for _, _, q in self.plan(order, order.leaves_qty, limit))

    def route(self, order: Order, qty: int, limit: float | None, rest_remainder: bool) -> RouteResult:
        result = RouteResult()
        remaining = qty
        for venue, price, take in self.plan(order, qty, limit):
            ref = self._new_ref(order)
            fills = venue.submit(ref, order.order_id, order.instrument.key, order.is_buy, take, price, rest_remainder=False)
            done = sum(f.qty for f in fills if f.ref == ref)
            remaining -= done
            result.children.append(ChildOrder(ref, venue.mic, price, take, posted=False))
            result.fills.extend(f for f in fills if f.parent_id is not None)
        if remaining and rest_remainder and limit is not None:
            venue = min(self.venues.values(), key=lambda v: (-v.make_rebate, v.mic))
            ref = self._new_ref(order)
            fills = venue.submit(ref, order.order_id, order.instrument.key, order.is_buy, remaining, limit, rest_remainder=True)
            result.fills.extend(f for f in fills if f.parent_id is not None)
            if venue.resting_qty(ref):
                self._resting.setdefault(order.order_id, []).append((venue.mic, ref))
                result.children.append(ChildOrder(ref, venue.mic, limit, remaining, posted=True))
        return result

    def cancel_all(self, order: Order) -> int:
        return sum(self.venues[mic].cancel(ref) for mic, ref in self._resting.pop(order.order_id, []))

    def resting_qty(self, order: Order) -> int:
        return sum(self.venues[mic].resting_qty(ref) for mic, ref in self._resting.get(order.order_id, []))

    def _new_ref(self, order: Order) -> str:
        return f"{order.order_id}-C{next(self._child_ids)}"
