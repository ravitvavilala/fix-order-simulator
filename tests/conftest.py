import pytest

from fixsim.engine import OrderManager
from fixsim.fix import Message, MsgType, Tag
from fixsim.market import default_venues
from fixsim.model import OrdStatus
from fixsim.router import SmartOrderRouter

TERMINAL = (OrdStatus.FILLED, OrdStatus.CANCELED, OrdStatus.REJECTED)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class ReportAuditor:
    """Checks FR-10 on every execution report the engine emits in any test."""

    def __init__(self):
        self.filled: dict[str, int] = {}
        self.notional: dict[str, float] = {}

    def check(self, outbound):
        for er in outbound:
            if er.msg_type != MsgType.EXECUTION_REPORT:
                continue
            oid = er.get(Tag.ORDER_ID)
            qty, cum, leaves = er.get(Tag.ORDER_QTY), er.get(Tag.CUM_QTY), er.get(Tag.LEAVES_QTY)
            if er.get(Tag.EXEC_TYPE) == "F":
                self.filled[oid] = self.filled.get(oid, 0) + er.get(Tag.LAST_QTY)
                self.notional[oid] = self.notional.get(oid, 0.0) + er.get(Tag.LAST_QTY) * er.get(Tag.LAST_PX)
            assert cum == self.filled.get(oid, 0), f"CumQty {cum} != sum of LastQty on {oid}"
            assert leaves == (0 if er.get(Tag.ORD_STATUS) in TERMINAL else qty - cum), f"LeavesQty wrong on {oid}"
            expected_avg = self.notional[oid] / cum if cum else 0.0
            assert er.get(Tag.AVG_PX) == pytest.approx(expected_avg), f"AvgPx wrong on {oid}"
        return outbound


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def engine(clock):
    eng = OrderManager(SmartOrderRouter(default_venues()), clock=clock)
    auditor = ReportAuditor()
    for name in ("handle", "tick", "on_external_trade"):
        method = getattr(eng, name)
        setattr(eng, name, lambda *a, _m=method, **k: auditor.check(_m(*a, **k)))
    return eng


def fix_msg(msg_type: str, **fields) -> Message:
    names = {name.lower(): value for name, value in vars(Tag).items() if not name.startswith("_")}
    pairs = [(Tag.MSG_TYPE, msg_type), (Tag.MSG_SEQ_NUM, "1"), (Tag.SENDER_COMP_ID, "CLIENT1")]
    fields.setdefault("transact_time", "20260919-12:00:00.000")
    for name, value in fields.items():
        if value is not None:
            pairs.append((names[name], str(value)))
    return Message(pairs)


def new_order(**fields) -> Message:
    base = dict(cl_ord_id="C1", symbol="AAPL", side="1", order_qty=100, ord_type="2", price=190.00, time_in_force="0")
    base.update(fields)
    return fix_msg(MsgType.NEW_ORDER_SINGLE, **base)


def cancel(orig, cl, symbol="AAPL", side="1") -> Message:
    return fix_msg(MsgType.ORDER_CANCEL_REQUEST, cl_ord_id=cl, orig_cl_ord_id=orig, symbol=symbol, side=side)


def replace(orig, cl, qty, price=None, symbol="AAPL", side="1", ord_type="2") -> Message:
    return fix_msg(MsgType.ORDER_CANCEL_REPLACE_REQUEST, cl_ord_id=cl, orig_cl_ord_id=orig, symbol=symbol,
                   side=side, order_qty=qty, ord_type=ord_type, price=price)
