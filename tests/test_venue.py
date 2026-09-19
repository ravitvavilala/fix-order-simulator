from fixsim.venue import Venue


def book_with_resting_sell(owner: str) -> Venue:
    venue = Venue("SIMX", take_fee=0.001, make_rebate=0.002)
    venue.seed("AAPL", is_buy=True, price=189.00, qty=1)
    venue.submit("P1-C1", "P1", owner, "AAPL", is_buy=False, qty=100, limit=190.00, rest_remainder=True)
    return venue


def test_orders_from_the_same_client_never_trade_with_each_other():
    venue = book_with_resting_sell(owner="CLIENT1")
    fills = venue.submit("P2-C1", "P2", "CLIENT1", "AAPL", is_buy=True, qty=100, limit=190.00, rest_remainder=False)
    assert fills == []
    assert venue.resting_qty("P1-C1") == 100


def test_orders_from_different_clients_cross_and_both_see_the_fill():
    venue = book_with_resting_sell(owner="CLIENT1")
    fills = venue.submit("P2-C1", "P2", "CLIENT2", "AAPL", is_buy=True, qty=60, limit=190.00, rest_remainder=False)
    assert {(f.parent_id, f.qty, f.added_liquidity) for f in fills} == {("P2", 60, False), ("P1", 60, True)}
    assert venue.resting_qty("P1-C1") == 40


def test_price_time_priority_fills_the_earlier_order_first():
    venue = Venue("SIMX", take_fee=0.001, make_rebate=0.002)
    venue.seed("AAPL", is_buy=True, price=189.00, qty=1)
    venue.submit("P1-C1", "P1", "A", "AAPL", is_buy=False, qty=50, limit=190.00, rest_remainder=True)
    venue.submit("P2-C1", "P2", "B", "AAPL", is_buy=False, qty=50, limit=190.00, rest_remainder=True)
    fills = venue.submit("P3-C1", "P3", "C", "AAPL", is_buy=True, qty=50, limit=190.00, rest_remainder=False)
    assert [f.parent_id for f in fills if f.added_liquidity] == ["P1"]


def test_a_venue_never_creates_a_book_for_an_instrument_it_does_not_list():
    venue = Venue("SIMX", take_fee=0.001, make_rebate=0.002)
    venue.submit("EXT", None, None, "ZZZZ", is_buy=True, qty=10, limit=5.0, rest_remainder=True)
    assert not venue.lists("ZZZZ")
