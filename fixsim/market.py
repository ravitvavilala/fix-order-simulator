"""The default simulated market: three venues with different fee models and seeded quotes."""

from __future__ import annotations

from fixsim.model import Instrument
from fixsim.venue import Venue

AAPL = Instrument("AAPL")
MSFT = Instrument("MSFT")
SPY_CALL = Instrument("SPY", "OPT", maturity="202612", put_or_call="1", strike=450.0)

BUY, SELL = True, False


def default_venues() -> list[Venue]:
    maker_taker = Venue("SIMA", take_fee=0.0030, make_rebate=0.0020)
    low_fee = Venue("SIMB", take_fee=0.0010, make_rebate=0.0000)
    rebate = Venue("SIMC", take_fee=0.0028, make_rebate=0.0032)
    quotes = {
        maker_taker: [(AAPL, SELL, 190.00, 200), (AAPL, SELL, 190.02, 300), (AAPL, BUY, 189.98, 400),
                      (MSFT, SELL, 410.10, 150), (MSFT, BUY, 410.00, 200),
                      (SPY_CALL, SELL, 12.40, 20), (SPY_CALL, BUY, 12.30, 25)],
        low_fee: [(AAPL, SELL, 190.00, 100), (AAPL, SELL, 190.01, 200), (AAPL, BUY, 189.97, 300),
                  (MSFT, SELL, 410.12, 250), (MSFT, BUY, 409.99, 100),
                  (SPY_CALL, SELL, 12.45, 30), (SPY_CALL, BUY, 12.25, 10)],
        rebate: [(AAPL, SELL, 190.01, 150), (AAPL, SELL, 190.03, 500), (AAPL, BUY, 189.99, 250),
                 (MSFT, SELL, 410.15, 300), (MSFT, BUY, 410.01, 150),
                 (SPY_CALL, SELL, 12.40, 15), (SPY_CALL, BUY, 12.35, 20)],
    }
    for venue, levels in quotes.items():
        for instrument, is_buy, price, qty in levels:
            venue.seed(instrument.key, is_buy, price, qty)
    return [maker_taker, low_fee, rebate]
