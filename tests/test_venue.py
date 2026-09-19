from fixsim.venue import Venue


def venue_with_our_resting_sell(parent_id: str) -> Venue:
    venue = Venue("SIMX", take_fee=0.001, make_rebate=0.002)
    venue.submit("P1-C1", parent_id, "AAPL", is_buy=False, qty=100, limit=190.00, rest_remainder=True)
    return venue


def test_an_order_never_trades_against_its_own_resting_child():
    venue = venue_with_our_resting_sell("P1")
    fills = venue.submit("P1-C2", "P1", "AAPL", is_buy=True, qty=100, limit=190.00, rest_remainder=False)
    assert fills == []
    assert venue.resting_qty("P1-C1") == 100


def test_two_different_orders_cross_and_both_see_the_fill():
    venue = venue_with_our_resting_sell("P1")
    fills = venue.submit("P2-C1", "P2", "AAPL", is_buy=True, qty=60, limit=190.00, rest_remainder=False)
    assert {(f.parent_id, f.qty, f.added_liquidity) for f in fills} == {("P2", 60, False), ("P1", 60, True)}
    assert venue.resting_qty("P1-C1") == 40


def test_price_time_priority_fills_the_earlier_order_first():
    venue = Venue("SIMX", take_fee=0.001, make_rebate=0.002)
    venue.submit("P1-C1", "P1", "AAPL", is_buy=False, qty=50, limit=190.00, rest_remainder=True)
    venue.submit("P2-C1", "P2", "AAPL", is_buy=False, qty=50, limit=190.00, rest_remainder=True)
    fills = venue.submit("P3-C1", "P3", "AAPL", is_buy=True, qty=50, limit=190.00, rest_remainder=False)
    assert [f.parent_id for f in fills if f.added_liquidity] == ["P1"]
