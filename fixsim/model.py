"""Order domain model and the FIX 4.4 order state machine."""

from __future__ import annotations

from dataclasses import dataclass, field


class OrdStatus:
    NEW = "0"
    PARTIALLY_FILLED = "1"
    FILLED = "2"
    CANCELED = "4"
    PENDING_CANCEL = "6"
    REJECTED = "8"
    PENDING_REPLACE = "E"


class ExecType:
    NEW = "0"
    CANCELED = "4"
    REPLACED = "5"
    PENDING_CANCEL = "6"
    REJECTED = "8"
    PENDING_REPLACE = "E"
    TRADE = "F"


class Side:
    BUY = "1"
    SELL = "2"
    SELL_SHORT = "5"


class OrdType:
    MARKET = "1"
    LIMIT = "2"


class TimeInForce:
    DAY = "0"
    IOC = "3"
    FOK = "4"


TERMINAL = {OrdStatus.FILLED, OrdStatus.CANCELED, OrdStatus.REJECTED}

WORKING = {OrdStatus.NEW, OrdStatus.PARTIALLY_FILLED}

ALLOWED_TRANSITIONS: dict[str | None, set[str]] = {
    None: {OrdStatus.NEW, OrdStatus.REJECTED},
    OrdStatus.NEW: {
        OrdStatus.PARTIALLY_FILLED,
        OrdStatus.FILLED,
        OrdStatus.PENDING_CANCEL,
        OrdStatus.PENDING_REPLACE,
        OrdStatus.CANCELED,
    },
    OrdStatus.PARTIALLY_FILLED: {
        OrdStatus.PARTIALLY_FILLED,
        OrdStatus.FILLED,
        OrdStatus.PENDING_CANCEL,
        OrdStatus.PENDING_REPLACE,
        OrdStatus.CANCELED,
    },
    OrdStatus.PENDING_CANCEL: {OrdStatus.CANCELED},
    OrdStatus.PENDING_REPLACE: {OrdStatus.NEW, OrdStatus.PARTIALLY_FILLED, OrdStatus.FILLED},
    OrdStatus.FILLED: set(),
    OrdStatus.CANCELED: set(),
    OrdStatus.REJECTED: set(),
}


class InvalidTransition(RuntimeError):
    pass


@dataclass(frozen=True)
class Instrument:
    symbol: str
    security_type: str = "CS"
    maturity: str | None = None
    put_or_call: str | None = None
    strike: float | None = None

    @property
    def is_option(self) -> bool:
        return self.security_type == "OPT"

    @property
    def key(self) -> str:
        if not self.is_option:
            return self.symbol
        right = "C" if self.put_or_call == "1" else "P"
        return f"{self.symbol} {self.maturity} {right} {self.strike:.2f}"

    @property
    def multiplier(self) -> int:
        return 100 if self.is_option else 1


@dataclass
class Order:
    order_id: str
    cl_ord_id: str
    instrument: Instrument
    side: str
    qty: int
    ord_type: str
    price: float | None
    tif: str
    status: str | None = None
    cum_qty: int = 0
    notional: float = 0.0
    strategy: str | None = None
    cl_ord_id_history: list[str] = field(default_factory=list)

    @property
    def is_buy(self) -> bool:
        return self.side == Side.BUY

    @property
    def leaves_qty(self) -> int:
        return 0 if self.status in TERMINAL else self.qty - self.cum_qty

    @property
    def avg_px(self) -> float:
        return round(self.notional / self.cum_qty, 6) if self.cum_qty else 0.0

    def transition(self, new_status: str) -> None:
        if new_status not in ALLOWED_TRANSITIONS[self.status]:
            raise InvalidTransition(f"{self.cl_ord_id}: {self.status} -> {new_status} not allowed")
        self.status = new_status

    def apply_fill(self, qty: int, price: float) -> None:
        if qty <= 0 or qty > self.leaves_qty:
            raise InvalidTransition(f"{self.cl_ord_id}: fill {qty} exceeds leaves {self.leaves_qty}")
        self.cum_qty += qty
        self.notional += qty * price
        self.transition(OrdStatus.FILLED if self.cum_qty == self.qty else OrdStatus.PARTIALLY_FILLED)
