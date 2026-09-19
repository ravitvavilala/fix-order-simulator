"""A simulated exchange: one limit order book per instrument, price-time priority, maker-taker fees."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

_seq = itertools.count(1)


@dataclass
class Resting:
    ref: str
    is_buy: bool
    price: float
    qty: int
    parent_id: str | None = None
    seq: int = field(default_factory=lambda: next(_seq))


@dataclass
class Fill:
    venue: str
    ref: str
    parent_id: str | None
    price: float
    qty: int
    added_liquidity: bool


class Venue:
    def __init__(self, mic: str, take_fee: float, make_rebate: float):
        self.mic = mic
        self.take_fee = take_fee
        self.make_rebate = make_rebate
        self._books: dict[str, list[Resting]] = {}

    def lists(self, key: str) -> bool:
        return key in self._books

    def seed(self, key: str, is_buy: bool, price: float, qty: int) -> None:
        self._books.setdefault(key, []).append(Resting(f"EXT-{next(_seq)}", is_buy, price, qty))

    def _opposite(self, key: str, is_buy: bool, limit: float | None, parent_id: str | None) -> list[Resting]:
        book = self._books.get(key, [])
        side = [
            r
            for r in book
            if r.is_buy != is_buy and (parent_id is None or r.parent_id != parent_id)
        ]
        if limit is not None:
            side = [r for r in side if (r.price <= limit if is_buy else r.price >= limit)]
        return sorted(side, key=lambda r: (r.price if is_buy else -r.price, r.seq))

    def levels(self, key: str, is_buy: bool, limit: float | None, parent_id: str | None = None) -> list[tuple[float, int]]:
        """Aggregated opposite-side liquidity an order on `is_buy` side could take, best price first."""
        out: dict[float, int] = {}
        for r in self._opposite(key, is_buy, limit, parent_id):
            out[r.price] = out.get(r.price, 0) + r.qty
        return sorted(out.items(), key=lambda kv: kv[0] if is_buy else -kv[0])

    def submit(self, ref: str, parent_id: str | None, key: str, is_buy: bool, qty: int,
               limit: float | None, rest_remainder: bool) -> list[Fill]:
        fills: list[Fill] = []
        remaining = qty
        for resting in self._opposite(key, is_buy, limit, parent_id):
            if remaining == 0:
                break
            take = min(remaining, resting.qty)
            resting.qty -= take
            remaining -= take
            fills.append(Fill(self.mic, ref, parent_id, resting.price, take, added_liquidity=False))
            if resting.parent_id is not None:
                fills.append(Fill(self.mic, resting.ref, resting.parent_id, resting.price, take, added_liquidity=True))
        self._books[key] = [r for r in self._books.get(key, []) if r.qty > 0]
        if remaining and rest_remainder and limit is not None:
            self._books.setdefault(key, []).append(Resting(ref, is_buy, limit, remaining, parent_id))
        return fills

    def cancel(self, ref: str) -> int:
        canceled = 0
        for key, book in self._books.items():
            canceled += sum(r.qty for r in book if r.ref == ref)
            self._books[key] = [r for r in book if r.ref != ref]
        return canceled

    def resting_qty(self, ref: str) -> int:
        return sum(r.qty for book in self._books.values() for r in book if r.ref == ref)
