import pytest

from fixsim.fix import MsgType, Tag
from fixsim.market import AAPL, SPY_CALL
from fixsim.model import ExecType, OrdStatus

from conftest import cancel, new_order, replace


def reports(out):
    return [o for o in out if o.msg_type == MsgType.EXECUTION_REPORT]


def statuses(out):
    return [(o.get(Tag.EXEC_TYPE), o.get(Tag.ORD_STATUS)) for o in reports(out)]


def assert_quantities_consistent(out):
    for er in reports(out):
        qty, cum, leaves = er.get(Tag.ORDER_QTY), er.get(Tag.CUM_QTY), er.get(Tag.LEAVES_QTY)
        terminal = er.get(Tag.ORD_STATUS) in (OrdStatus.FILLED, OrdStatus.CANCELED, OrdStatus.REJECTED)
        assert leaves == (0 if terminal else qty - cum)


def test_limit_order_sweeps_best_price_then_lowest_fee_venue(engine):
    out = engine.handle(new_order(order_qty=250, price=190.00))
    assert statuses(out) == [(ExecType.NEW, OrdStatus.NEW), (ExecType.TRADE, OrdStatus.PARTIALLY_FILLED),
                             (ExecType.TRADE, OrdStatus.FILLED)]
    fills = [(er.get(Tag.LAST_MKT), er.get(Tag.LAST_PX), er.get(Tag.LAST_QTY)) for er in reports(out)[1:]]
    assert fills == [("SIMB", 190.00, 100), ("SIMA", 190.00, 150)]
    assert_quantities_consistent(out)


def test_marketable_order_fills_completely_at_volume_weighted_average(engine):
    out = engine.handle(new_order(order_qty=450, price=190.01))
    final = reports(out)[-1]
    assert final.get(Tag.ORD_STATUS) == OrdStatus.FILLED and final.get(Tag.LEAVES_QTY) == 0
    assert final.get(Tag.AVG_PX) == pytest.approx((300 * 190.00 + 150 * 190.01) / 450)
    assert_quantities_consistent(out)


def test_day_limit_remainder_rests_on_best_rebate_venue_and_fills_later(engine):
    engine.handle(new_order(order_qty=400, price=190.00))
    order = engine.by_cl_ord_id["C1"]
    assert order.cum_qty == 300 and engine.router.resting_qty(order) == 100
    out = engine.on_external_trade("SIMC", AAPL, taker_is_buy=False, qty=100, price=190.00)
    er = reports(out)[0]
    assert (er.get(Tag.LAST_MKT), er.get(Tag.LAST_LIQUIDITY_IND)) == ("SIMC", 1)
    assert er.get(Tag.ORD_STATUS) == OrdStatus.FILLED


def test_cancel_goes_through_pending_cancel_and_keeps_cum_qty(engine):
    engine.handle(new_order(order_qty=400, price=190.00))
    out = engine.handle(cancel("C1", "C2"))
    assert statuses(out) == [(ExecType.PENDING_CANCEL, OrdStatus.PENDING_CANCEL), (ExecType.CANCELED, OrdStatus.CANCELED)]
    final = reports(out)[-1]
    assert (final.get(Tag.CL_ORD_ID), final.get(Tag.ORIG_CL_ORD_ID)) == ("C2", "C1")
    assert final.get(Tag.CUM_QTY) == 300 and final.get(Tag.LEAVES_QTY) == 0
    assert engine.router.resting_qty(engine.by_cl_ord_id["C2"]) == 0


@pytest.mark.parametrize("setup, orig, reason", [
    ("filled", "C1", 0),
    ("none", "NOPE", 1),
])
def test_cancel_is_refused_when_too_late_or_unknown(engine, setup, orig, reason):
    if setup == "filled":
        engine.handle(new_order(order_qty=100, price=190.00))
    out = engine.handle(cancel(orig, "C9"))
    assert [o.msg_type for o in out] == [MsgType.ORDER_CANCEL_REJECT]
    assert out[0].get(Tag.CXL_REJ_REASON) == reason and out[0].get(Tag.CXL_REJ_RESPONSE_TO) == "1"


def test_replace_reprices_and_reroutes_the_remaining_quantity(engine):
    engine.handle(new_order(order_qty=400, price=190.00))
    out = engine.handle(replace("C1", "C2", qty=500, price=190.01))
    assert statuses(out)[:2] == [(ExecType.PENDING_REPLACE, OrdStatus.PENDING_REPLACE),
                                 (ExecType.REPLACED, OrdStatus.PARTIALLY_FILLED)]
    order = engine.by_cl_ord_id["C2"]
    assert order.qty == 500 and order.price == 190.01 and order.cum_qty == 500
    assert order.cl_ord_id_history == ["C1"]
    assert_quantities_consistent(out)


def test_replace_below_filled_quantity_is_refused(engine):
    engine.handle(new_order(order_qty=400, price=190.00))
    out = engine.handle(replace("C1", "C2", qty=200, price=190.00))
    assert out[0].msg_type == MsgType.ORDER_CANCEL_REJECT and out[0].get(Tag.CXL_REJ_RESPONSE_TO) == "2"


@pytest.mark.parametrize("fields, reason", [
    (dict(symbol="ZZZZ"), 1),
    (dict(order_qty=0), 13),
    (dict(price=None), 99),
    (dict(security_type="OPT", symbol="SPY", maturity_month_year="202612", put_or_call="1"), 99),
    (dict(side="7"), 99),
])
def test_invalid_orders_are_rejected_with_a_reason(engine, fields, reason):
    out = engine.handle(new_order(**fields))
    assert statuses(out) == [(ExecType.REJECTED, OrdStatus.REJECTED)]
    assert out[0].get(Tag.ORD_REJ_REASON) == reason


def test_duplicate_cl_ord_id_is_rejected(engine):
    engine.handle(new_order(order_qty=50))
    out = engine.handle(new_order(order_qty=50))
    assert out[0].get(Tag.ORD_REJ_REASON) == 6


def test_missing_required_tag_is_a_session_reject(engine):
    msg = new_order()
    msg.fields = [(t, v) for t, v in msg.fields if t != Tag.SIDE]
    out = engine.handle(msg)
    assert out[0].msg_type == MsgType.REJECT and out[0].get(Tag.REF_TAG_ID) == Tag.SIDE


def test_ioc_cancels_the_unfilled_remainder(engine):
    out = engine.handle(new_order(order_qty=400, price=190.00, time_in_force="3"))
    assert statuses(out)[-1] == (ExecType.CANCELED, OrdStatus.CANCELED)
    assert reports(out)[-1].get(Tag.CUM_QTY) == 300


def test_fok_without_enough_liquidity_is_canceled_with_no_fills(engine):
    out = engine.handle(new_order(order_qty=10_000, price=190.00, time_in_force="4"))
    assert statuses(out) == [(ExecType.NEW, OrdStatus.NEW), (ExecType.CANCELED, OrdStatus.CANCELED)]
    assert engine.by_cl_ord_id["C1"].cum_qty == 0


def test_market_order_sweeps_the_book(engine):
    out = engine.handle(new_order(order_qty=700, ord_type="1", price=None))
    assert statuses(out)[-1] == (ExecType.TRADE, OrdStatus.FILLED)
    assert {er.get(Tag.LAST_MKT) for er in reports(out)[1:]} == {"SIMA", "SIMB", "SIMC"}


def test_option_order_routes_and_reports_contract_fields(engine):
    out = engine.handle(new_order(symbol="SPY", security_type="OPT", maturity_month_year="202612",
                                  put_or_call="1", strike_price=450, order_qty=30, price=12.40))
    final = reports(out)[-1]
    assert final.get(Tag.ORD_STATUS) == OrdStatus.FILLED
    assert (final.get(Tag.PUT_OR_CALL), final.get(Tag.STRIKE_PRICE)) == ("1", 450.0)
    assert engine.by_cl_ord_id["C1"].instrument == SPY_CALL


def test_twap_releases_slices_over_time_and_completes(engine, clock):
    out = engine.handle(new_order(order_qty=300, price=190.02, target_strategy=1000,
                                  target_strategy_parameters="slices=3;interval=60"))
    order = engine.by_cl_ord_id["C1"]
    assert order.cum_qty == 100
    clock.now = 59
    assert engine.tick() == []
    clock.now = 60
    engine.tick()
    assert order.cum_qty == 200
    clock.now = 120
    engine.tick()
    assert order.status == OrdStatus.FILLED and not engine.twaps
    assert_quantities_consistent(out)
