import pytest

from fixsim.engine import OrderManager
from fixsim.fix import Message, MsgType, Tag
from fixsim.market import default_venues
from fixsim.router import SmartOrderRouter


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def engine(clock):
    return OrderManager(SmartOrderRouter(default_venues()), clock=clock)


def fix_msg(msg_type: str, **fields) -> Message:
    names = {name.lower(): value for name, value in vars(Tag).items() if not name.startswith("_")}
    pairs = [(Tag.MSG_TYPE, msg_type), (Tag.MSG_SEQ_NUM, "1")]
    for name, value in fields.items():
        pairs.append((names[name], str(value)))
    return Message(pairs)


def new_order(**fields) -> Message:
    base = dict(cl_ord_id="C1", symbol="AAPL", side="1", order_qty=100, ord_type="2", price=190.00, time_in_force="0")
    base.update(fields)
    return fix_msg(MsgType.NEW_ORDER_SINGLE, **{k: v for k, v in base.items() if v is not None})


def cancel(orig, cl, symbol="AAPL", side="1") -> Message:
    return fix_msg(MsgType.ORDER_CANCEL_REQUEST, cl_ord_id=cl, orig_cl_ord_id=orig, symbol=symbol, side=side)


def replace(orig, cl, qty, price=None, symbol="AAPL", side="1", ord_type="2") -> Message:
    fields = dict(cl_ord_id=cl, orig_cl_ord_id=orig, symbol=symbol, side=side, order_qty=qty, ord_type=ord_type)
    if price is not None:
        fields["price"] = price
    return fix_msg(MsgType.ORDER_CANCEL_REPLACE_REQUEST, **fields)
