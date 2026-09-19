"""Order manager: validates FIX application messages, drives the order state machine, emits reports."""

from __future__ import annotations

import itertools
import math
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal

from fixsim.fix import Message, MsgType, Tag
from fixsim.model import (
    ExecType,
    Instrument,
    Order,
    OrdStatus,
    OrdType,
    Side,
    TERMINAL,
    TimeInForce,
)
from fixsim.router import SmartOrderRouter

TWAP = "1000"
DECIMAL = re.compile(r"-?(\d+\.?\d*|\.\d+)")

REQUIRED_NEW = (Tag.CL_ORD_ID, Tag.SYMBOL, Tag.SIDE, Tag.TRANSACT_TIME, Tag.ORDER_QTY, Tag.ORD_TYPE)
REQUIRED_CANCEL = (Tag.CL_ORD_ID, Tag.ORIG_CL_ORD_ID, Tag.SIDE, Tag.SYMBOL, Tag.TRANSACT_TIME)
REQUIRED_REPLACE = REQUIRED_CANCEL + (Tag.ORDER_QTY, Tag.ORD_TYPE)


class OrdRejReason:
    UNKNOWN_SYMBOL = 1
    DUPLICATE_ORDER = 6
    INCORRECT_QUANTITY = 13
    OTHER = 99


class CxlRejReason:
    TOO_LATE = 0
    UNKNOWN_ORDER = 1
    DUPLICATE_CL_ORD_ID = 6
    OTHER = 99


@dataclass
class Outbound:
    msg_type: str
    fields: list[tuple[int, object]]
    owner: str | None = None

    def get(self, tag: int):
        return next((v for t, v in self.fields if t == tag), None)


@dataclass
class TwapSchedule:
    order_id: str
    slices: int
    interval: float
    next_at: float
    released: int = 0


class OrderManager:
    def __init__(self, router: SmartOrderRouter, clock=time.monotonic):
        self.router = router
        self.clock = clock
        self.orders: dict[str, Order] = {}
        self.by_cl_ord_id: dict[tuple[str, str], Order] = {}
        self.twaps: dict[str, TwapSchedule] = {}
        self._order_ids = itertools.count(1)
        self._exec_ids = itertools.count(1)

    def handle(self, msg: Message) -> list[Outbound]:
        handlers = {
            MsgType.NEW_ORDER_SINGLE: (REQUIRED_NEW, self.on_new_order),
            MsgType.ORDER_CANCEL_REQUEST: (REQUIRED_CANCEL, self.on_cancel),
            MsgType.ORDER_CANCEL_REPLACE_REQUEST: (REQUIRED_REPLACE, self.on_replace),
        }
        required, handler = handlers[msg.msg_type]
        missing = next((t for t in required if not msg.has(t)), None)
        if missing is not None:
            return [Outbound(MsgType.REJECT, [
                (Tag.REF_SEQ_NUM, msg.get(Tag.MSG_SEQ_NUM, "0")),
                (Tag.REF_TAG_ID, missing),
                (Tag.SESSION_REJECT_REASON, 1),
                (Tag.TEXT, f"Required tag {missing} missing"),
            ])]
        return handler(msg)

    def on_new_order(self, m: Message) -> list[Outbound]:
        order = self._build_order(m)
        problem = self._validate_new(m, order)
        if problem:
            reason, text = problem
            order.transition(OrdStatus.REJECTED)
            return [self._report(order, ExecType.REJECTED, text=text, rej_reason=reason)]
        order.transition(OrdStatus.NEW)
        self.orders[order.order_id] = order
        self.by_cl_ord_id[order.owner, order.cl_ord_id] = order
        out = [self._report(order, ExecType.NEW)]
        if order.strategy == TWAP:
            slices, interval = _twap_params(m.get(Tag.TARGET_STRATEGY_PARAMETERS, ""))
            self.twaps[order.order_id] = TwapSchedule(order.order_id, slices, interval, self.clock())
            return out + self._release_due_slices()
        if order.tif == TimeInForce.FOK and self.router.available(order, self._limit(order)) < order.qty:
            order.transition(OrdStatus.CANCELED)
            return out + [self._report(order, ExecType.CANCELED, text="FOK: not enough liquidity to fill in full")]
        out += self._route(order, order.leaves_qty)
        return out + self._cancel_unrestable_remainder(order)

    def on_cancel(self, m: Message) -> list[Outbound]:
        cl, orig, owner = m.get(Tag.CL_ORD_ID), m.get(Tag.ORIG_CL_ORD_ID), _owner(m)
        order = self.by_cl_ord_id.get((owner, orig))
        refused = self._cancel_problem(order, owner, cl, orig)
        if refused:
            return [self._cancel_reject(order, cl, orig, "1", *refused)]
        order.transition(OrdStatus.PENDING_CANCEL)
        out = [self._report(order, ExecType.PENDING_CANCEL, cl=cl, orig=orig)]
        self.router.cancel_all(order)
        self.twaps.pop(order.order_id, None)
        order.transition(OrdStatus.CANCELED)
        self._adopt_cl_ord_id(order, cl)
        return out + [self._report(order, ExecType.CANCELED, cl=cl, orig=orig)]

    def on_replace(self, m: Message) -> list[Outbound]:
        cl, orig, owner = m.get(Tag.CL_ORD_ID), m.get(Tag.ORIG_CL_ORD_ID), _owner(m)
        order = self.by_cl_ord_id.get((owner, orig))
        refused = self._cancel_problem(order, owner, cl, orig) or self._replace_problem(order, m)
        if refused:
            return [self._cancel_reject(order, cl, orig, "2", *refused)]
        order.transition(OrdStatus.PENDING_REPLACE)
        out = [self._report(order, ExecType.PENDING_REPLACE, cl=cl, orig=orig)]
        self.router.cancel_all(order)
        order.qty = int(m.get(Tag.ORDER_QTY))
        if order.ord_type == OrdType.LIMIT:
            order.price = float(m.get(Tag.PRICE))
        order.transition(OrdStatus.PARTIALLY_FILLED if order.cum_qty else OrdStatus.NEW)
        self._adopt_cl_ord_id(order, cl)
        out.append(self._report(order, ExecType.REPLACED, cl=cl, orig=orig))
        if order.order_id in self.twaps:
            return out
        out += self._route(order, order.leaves_qty)
        return out + self._cancel_unrestable_remainder(order)

    def on_external_trade(self, mic: str, instrument: Instrument, taker_is_buy: bool, qty: int,
                          price: float) -> list[Outbound]:
        """Another market participant trades on a venue, possibly filling our resting child orders."""
        venue = self.router.venues[mic]
        fills = venue.submit(f"EXT-{mic}", None, None, instrument.key, taker_is_buy, qty, price, rest_remainder=False)
        return self._apply_fills([f for f in fills if f.parent_id is not None])

    def tick(self) -> list[Outbound]:
        """Release every TWAP slice that is due at the current clock time."""
        return self._release_due_slices()

    def _release_due_slices(self) -> list[Outbound]:
        out = []
        now = self.clock()
        for schedule in list(self.twaps.values()):
            order = self.orders[schedule.order_id]
            while schedule.released < schedule.slices and now >= schedule.next_at and order.status not in TERMINAL:
                remaining_slices = schedule.slices - schedule.released
                slice_qty = -(-order.leaves_qty // remaining_slices)
                schedule.released += 1
                schedule.next_at += schedule.interval
                out += self._route(order, slice_qty, rest=False)
            if schedule.released == schedule.slices or order.status in TERMINAL:
                self.twaps.pop(order.order_id, None)
                if order.status not in TERMINAL:
                    order.transition(OrdStatus.CANCELED)
                    out.append(self._report(order, ExecType.CANCELED, text="TWAP schedule complete; remainder canceled"))
        return out

    def _route(self, order: Order, qty: int, rest: bool | None = None) -> list[Outbound]:
        if qty <= 0:
            return []
        if rest is None:
            rest = order.ord_type == OrdType.LIMIT and order.tif == TimeInForce.DAY
        result = self.router.route(order, qty, self._limit(order), rest)
        return self._apply_fills(result.fills)

    @staticmethod
    def _limit(order: Order) -> float | None:
        return order.price if order.ord_type == OrdType.LIMIT else None

    def _apply_fills(self, fills) -> list[Outbound]:
        out = []
        for fill in fills:
            order = self.orders[fill.parent_id]
            order.apply_fill(fill.qty, fill.price)
            out.append(self._report(order, ExecType.TRADE, last=fill))
        return out

    def _cancel_unrestable_remainder(self, order: Order) -> list[Outbound]:
        rests = order.ord_type == OrdType.LIMIT and order.tif == TimeInForce.DAY
        if rests or order.status in TERMINAL or order.leaves_qty == 0:
            return []
        order.transition(OrdStatus.CANCELED)
        why = "Market order" if order.ord_type == OrdType.MARKET else "IOC"
        return [self._report(order, ExecType.CANCELED, text=f"{why}: unfilled quantity canceled")]

    def _build_order(self, m: Message) -> Order:
        strike = m.get(Tag.STRIKE_PRICE)
        instrument = Instrument(
            symbol=m.get(Tag.SYMBOL),
            security_type=m.get(Tag.SECURITY_TYPE, "CS"),
            maturity=m.get(Tag.MATURITY_MONTH_YEAR),
            put_or_call=m.get(Tag.PUT_OR_CALL),
            strike=Decimal(strike) if _is_number(strike) else None,
        )
        qty, price = m.get(Tag.ORDER_QTY), m.get(Tag.PRICE)
        return Order(
            order_id=f"ORD{next(self._order_ids):06d}",
            cl_ord_id=m.get(Tag.CL_ORD_ID),
            instrument=instrument,
            side=m.get(Tag.SIDE),
            qty=int(qty) if qty.isdigit() else 0,
            ord_type=m.get(Tag.ORD_TYPE),
            price=float(price) if _is_number(price) else None,
            tif=m.get(Tag.TIME_IN_FORCE, TimeInForce.DAY),
            owner=_owner(m),
            strategy=m.get(Tag.TARGET_STRATEGY),
        )

    def _validate_new(self, m: Message, order: Order) -> tuple[int, str] | None:
        if (order.owner, order.cl_ord_id) in self.by_cl_ord_id:
            return OrdRejReason.DUPLICATE_ORDER, f"Duplicate ClOrdID {order.cl_ord_id}"
        if order.side not in (Side.BUY, Side.SELL, Side.SELL_SHORT):
            return OrdRejReason.OTHER, f"Unsupported Side {order.side}"
        if order.qty <= 0:
            return OrdRejReason.INCORRECT_QUANTITY, "OrderQty must be a positive whole number"
        if order.ord_type not in (OrdType.MARKET, OrdType.LIMIT):
            return OrdRejReason.OTHER, f"Unsupported OrdType {order.ord_type}"
        if order.ord_type == OrdType.LIMIT and not (order.price and order.price > 0):
            return OrdRejReason.OTHER, "Limit order requires a positive Price (44)"
        if order.tif not in (TimeInForce.DAY, TimeInForce.IOC, TimeInForce.FOK):
            return OrdRejReason.OTHER, f"Unsupported TimeInForce {order.tif}"
        if order.instrument.security_type not in ("CS", "OPT"):
            return OrdRejReason.OTHER, f"Unsupported SecurityType {order.instrument.security_type}"
        inst = order.instrument
        if inst.is_option and not (inst.maturity and inst.put_or_call in ("0", "1") and inst.strike and inst.strike > 0):
            return OrdRejReason.OTHER, "Option order requires MaturityMonthYear (200), PutOrCall (201) and StrikePrice (202)"
        if order.strategy not in (None, TWAP):
            return OrdRejReason.OTHER, f"Unsupported TargetStrategy {order.strategy}"
        if order.strategy == TWAP and order.tif != TimeInForce.DAY:
            return OrdRejReason.OTHER, "TWAP requires TimeInForce Day (59=0)"
        if order.strategy == TWAP and _twap_params(m.get(Tag.TARGET_STRATEGY_PARAMETERS, "")) is None:
            return OrdRejReason.OTHER, "TargetStrategyParameters (848) must be slices=<1-100>;interval=<seconds>"
        if not self.router.lists(inst.key):
            return OrdRejReason.UNKNOWN_SYMBOL, f"Unknown symbol {inst.label}"
        return None

    def _cancel_problem(self, order: Order | None, owner: str, cl: str, orig: str) -> tuple[int, str] | None:
        if order is None:
            return CxlRejReason.UNKNOWN_ORDER, "Unknown order"
        if order.cl_ord_id != orig:
            return CxlRejReason.UNKNOWN_ORDER, f"OrigClOrdID {orig} is not the order's current ClOrdID ({order.cl_ord_id})"
        if (owner, cl) in self.by_cl_ord_id:
            return CxlRejReason.DUPLICATE_CL_ORD_ID, f"Duplicate ClOrdID {cl}"
        if order.status in TERMINAL:
            return CxlRejReason.TOO_LATE, f"Order already {_STATUS_NAME[order.status]}"
        return None

    def _replace_problem(self, order: Order, m: Message) -> tuple[int, str] | None:
        if m.get(Tag.SIDE) != order.side or m.get(Tag.SYMBOL) != order.instrument.symbol:
            return CxlRejReason.OTHER, "Side and Symbol cannot change on replace"
        if m.get(Tag.ORD_TYPE) != order.ord_type:
            return CxlRejReason.OTHER, "OrdType cannot change on replace"
        qty = m.get(Tag.ORDER_QTY)
        if not qty.isdigit() or int(qty) <= order.cum_qty:
            return CxlRejReason.OTHER, f"OrderQty must exceed CumQty ({order.cum_qty})"
        if order.ord_type == OrdType.LIMIT and not (_is_number(m.get(Tag.PRICE)) and float(m.get(Tag.PRICE)) > 0):
            return CxlRejReason.OTHER, "Limit order requires a positive Price (44)"
        return None

    def _adopt_cl_ord_id(self, order: Order, cl: str) -> None:
        order.cl_ord_id_history.append(order.cl_ord_id)
        order.cl_ord_id = cl
        self.by_cl_ord_id[order.owner, cl] = order

    def _report(self, order: Order, exec_type: str, *, cl: str | None = None, orig: str | None = None,
                last=None, text: str | None = None, rej_reason: int | None = None) -> Outbound:
        inst = order.instrument
        fields: list[tuple[int, object]] = [(Tag.ORDER_ID, order.order_id), (Tag.CL_ORD_ID, cl or order.cl_ord_id)]
        if orig:
            fields.append((Tag.ORIG_CL_ORD_ID, orig))
        fields += [
            (Tag.EXEC_ID, f"EX{next(self._exec_ids):07d}"),
            (Tag.EXEC_TYPE, exec_type),
            (Tag.ORD_STATUS, order.status),
            (Tag.SYMBOL, inst.symbol),
            (Tag.SECURITY_TYPE, inst.security_type),
        ]
        if inst.is_option:
            fields += [(tag, value) for tag, value in ((Tag.MATURITY_MONTH_YEAR, inst.maturity),
                       (Tag.PUT_OR_CALL, inst.put_or_call), (Tag.STRIKE_PRICE, inst.strike)) if value is not None]
        fields += [(Tag.SIDE, order.side), (Tag.ORDER_QTY, order.qty), (Tag.ORD_TYPE, order.ord_type)]
        if order.price is not None:
            fields.append((Tag.PRICE, order.price))
        fields += [
            (Tag.TIME_IN_FORCE, order.tif),
            (Tag.CUM_QTY, order.cum_qty),
            (Tag.LEAVES_QTY, order.leaves_qty),
            (Tag.AVG_PX, order.avg_px),
        ]
        if last is not None:
            fields += [(Tag.LAST_PX, last.price), (Tag.LAST_QTY, last.qty), (Tag.LAST_MKT, last.venue),
                       (Tag.LAST_LIQUIDITY_IND, 1 if last.added_liquidity else 2)]
        if rej_reason is not None:
            fields.append((Tag.ORD_REJ_REASON, rej_reason))
        if text:
            fields.append((Tag.TEXT, text))
        return Outbound(MsgType.EXECUTION_REPORT, fields, owner=order.owner)

    def _cancel_reject(self, order: Order | None, cl: str, orig: str, response_to: str,
                       reason: int, text: str) -> Outbound:
        known = order is not None and reason != CxlRejReason.UNKNOWN_ORDER
        return Outbound(MsgType.ORDER_CANCEL_REJECT, [
            (Tag.ORDER_ID, order.order_id if known else "NONE"),
            (Tag.CL_ORD_ID, cl),
            (Tag.ORIG_CL_ORD_ID, orig),
            (Tag.ORD_STATUS, order.status if known else OrdStatus.REJECTED),
            (Tag.CXL_REJ_RESPONSE_TO, response_to),
            (Tag.CXL_REJ_REASON, reason),
            (Tag.TEXT, text),
        ])


_STATUS_NAME = {OrdStatus.FILLED: "filled", OrdStatus.CANCELED: "canceled", OrdStatus.REJECTED: "rejected"}


def _is_number(value: str | None) -> bool:
    """FIX Price and float fields: plain decimal digits, no exponent, inf or nan."""
    return value is not None and DECIMAL.fullmatch(value) is not None and math.isfinite(float(value))


def _owner(m: Message) -> str:
    return m.get(Tag.SENDER_COMP_ID, "CLIENT")


def _twap_params(raw: str) -> tuple[int, float] | None:
    params = dict(p.split("=", 1) for p in raw.split(";") if "=" in p)
    try:
        slices = int(params.get("slices", 5))
        interval = float(params.get("interval", 60))
    except ValueError:
        return None
    if not 1 <= slices <= 100 or not math.isfinite(interval) or interval <= 0:
        return None
    return slices, interval
