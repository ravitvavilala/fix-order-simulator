# FIX tag mapping

R = required, C = conditional, O = optional. "Model" is where the value lives in `fixsim`.

## Inbound: NewOrderSingle (35=D)

| Tag | Name | Req | Model | Rule |
|---|---|---|---|---|
| 11 | ClOrdID | R | `Order.cl_ord_id` | Must be unique, else 103=6 |
| 55 | Symbol | R | `Instrument.symbol` | Must be listed on a venue, else 103=1 |
| 167 | SecurityType | O | `Instrument.security_type` | `CS` (default) or `OPT` |
| 200 | MaturityMonthYear | C | `Instrument.maturity` | Required when 167=OPT |
| 201 | PutOrCall | C | `Instrument.put_or_call` | 0 = put, 1 = call; required when 167=OPT |
| 202 | StrikePrice | C | `Instrument.strike` | Required when 167=OPT |
| 54 | Side | R | `Order.side` | 1 = buy, 2 = sell, 5 = sell short |
| 38 | OrderQty | R | `Order.qty` | Positive whole number, else 103=13 |
| 40 | OrdType | R | `Order.ord_type` | 1 = market, 2 = limit |
| 44 | Price | C | `Order.price` | Required and > 0 when 40=2 |
| 59 | TimeInForce | O | `Order.tif` | 0 = Day (default), 3 = IOC, 4 = FOK |
| 847 | TargetStrategy | O | `Order.strategy` | 1000 = TWAP (FIX reserves 1000+ for user-defined strategies) |
| 848 | TargetStrategyParameters | C | `TwapSchedule` | `slices=<1-100>;interval=<seconds>`; defaults 5 and 60 |

## Inbound: OrderCancelRequest (35=F) and OrderCancelReplaceRequest (35=G)

| Tag | Name | F | G | Rule |
|---|---|---|---|---|
| 11 | ClOrdID | R | R | New and unused, else 102=6 |
| 41 | OrigClOrdID | R | R | Must match a ClOrdID the order has had, else 102=1 |
| 55 | Symbol | R | R | G: must equal the order's |
| 54 | Side | R | R | G: must equal the order's |
| 38 | OrderQty | | R | G: must exceed CumQty |
| 40 | OrdType | | R | G: must equal the order's |
| 44 | Price | | C | G: required when 40=2 |

## Outbound: ExecutionReport (35=8)

| Tag | Name | Source |
|---|---|---|
| 37 | OrderID | `Order.order_id` |
| 11 | ClOrdID | Current ClOrdID, or the request's on cancel/replace reports |
| 41 | OrigClOrdID | On cancel/replace reports |
| 17 | ExecID | Sequential, unique |
| 150 | ExecType | The event (see order-lifecycle.md) |
| 39 | OrdStatus | `Order.status` after the event |
| 55, 167, 200, 201, 202 | Instrument | `Order.instrument` (option tags only when 167=OPT) |
| 54, 38, 40, 44, 59 | Order terms | `Order` |
| 14 | CumQty | `Order.cum_qty` |
| 151 | LeavesQty | `Order.leaves_qty` |
| 6 | AvgPx | `Order.avg_px` |
| 31, 32, 30 | LastPx, LastQty, LastMkt | `Fill` (150=F only) |
| 851 | LastLiquidityInd | 1 = added (resting child hit), 2 = removed (took liquidity) |
| 103 | OrdRejReason | 150=8 only: 1 unknown symbol, 6 duplicate, 13 quantity, 99 other |
| 58 | Text | Human-readable reason on rejects and cancels |

## Outbound: OrderCancelReject (35=9) and Reject (35=3)

| Message | Tags |
|---|---|
| 35=9 | 37 (or `NONE`), 11, 41, 39, 434 (1 = cancel, 2 = replace), 102 (0 too late, 1 unknown order, 6 duplicate ClOrdID, 99 other), 58 |
| 35=3 | 45 RefSeqNum, 371 RefTagID, 373 SessionRejectReason (1 = required tag missing, 11 = invalid MsgType), 58 |
