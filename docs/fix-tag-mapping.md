# FIX tag mapping

R = required, C = conditional, O = optional. "Model" is where the value lives in `fixsim`.

## Inbound: header and session messages

| Tag | Name | Req | Model | Rule |
|---|---|---|---|---|
| 8 | BeginString | R | `FixSession.begin_string` | FIX.4.2 or FIX.4.4, fixed at Logon; another value at Logon closes the connection with no reply; a different version later in the session ends it with Logout |
| 9, 10 | BodyLength, CheckSum | R | (checked) | Wrong: message ignored as garbled |
| 35 | MsgType | R | `Message.msg_type` | Not defined in the session's FIX version: Reject 373=11; defined but unsupported: 35=j 380=3 |
| 49 | SenderCompID | R | `FixSession.target_comp_id`, `Order.owner` | Fixed at Logon; must match on every message, else Reject 373=9 and Logout. Keys self-trade prevention and report routing |
| 56 | TargetCompID | R | `FixSession.sender_comp_id` | Must be SIMROUTER, else Reject 373=9 and Logout (at Logon: close with no reply) |
| 34 | MsgSeqNum | R | `FixSession.next_in` | Missing or non-numeric: Logout; too low: Logout unless 43=Y; gap: ResendRequest |
| 43 | PossDupFlag | O | (checked) | Y on a resent message; below the expected number it is ignored |
| 122 | OrigSendingTime | C | (presence only) | Required when 43=Y, else Reject 371=122 |
| 52 | SendingTime | R | (not checked) | |
| 98, 108 | EncryptMethod, HeartBtInt | R on Logon | (echoed) | 108 must be a whole number, else the Logon is closed with no reply |
| 112 | TestReqID | R on 35=1 | (echoed on 35=0) | Missing: Reject 371=112 |
| 7, 16 | BeginSeqNo, EndSeqNo | R on 35=2 | `FixSession.answer_resend` | Non-numeric: Reject 373=6; outside what was sent: 373=5 |
| 123, 36 | GapFillFlag, NewSeqNo | R on 35=4 | `FixSession.next_in` | 123 Y or N; 36 above the message's own MsgSeqNum (GapFill) or not below the expected number (Reset) |

## Inbound: NewOrderSingle (35=D)

| Tag | Name | Req | Model | Rule |
|---|---|---|---|---|
| 11 | ClOrdID | R | `Order.cl_ord_id` | Must be unique for this client (SenderCompID), else 103=6 |
| 55 | Symbol | R | `Instrument.symbol` | Must be listed on a venue, else 103=1 |
| 60 | TransactTime | R | (validated only) | Missing: session Reject 371=60 |
| 167 | SecurityType | O | `Instrument.security_type` | `CS` (default) or `OPT` |
| 200 | MaturityMonthYear | C | `Instrument.maturity` | Required when 167=OPT |
| 201 | PutOrCall | C | `Instrument.put_or_call` | 0 = put, 1 = call; required when 167=OPT |
| 202 | StrikePrice | C | `Instrument.strike` | Required and positive when 167=OPT; kept as a Decimal and matched exactly |
| 54 | Side | R | `Order.side` | 1 = buy, 2 = sell, 5 = sell short |
| 38 | OrderQty | R | `Order.qty` | Positive whole number, else 103=13 |
| 40 | OrdType | R | `Order.ord_type` | 1 = market, 2 = limit |
| 44 | Price | C | `Order.price` | Required when 40=2: a plain decimal > 0 (no exponent, inf or nan) |
| 59 | TimeInForce | O | `Order.tif` | 0 = Day (default), 3 = IOC, 4 = FOK |
| 847 | TargetStrategy | O | `Order.strategy` | 1000 = TWAP (FIX reserves 1000+ for user-defined strategies) |
| 848 | TargetStrategyParameters | C | `TwapSchedule` | `slices=<1-100>;interval=<seconds>`; defaults 5 and 60 |

## Inbound: OrderCancelRequest (35=F) and OrderCancelReplaceRequest (35=G)

| Tag | Name | F | G | Rule |
|---|---|---|---|---|
| 11 | ClOrdID | R | R | New and unused, else 102=6 |
| 41 | OrigClOrdID | R | R | Must be the current ClOrdID of one of this client's orders, else 102=1 |
| 60 | TransactTime | R | R | Missing: session Reject 371=60 |
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
| 58 | Text | Human-readable reason on rejects and on cancels the router initiates (IOC, FOK, market, TWAP end) |

## Outbound: OrderCancelReject (35=9) and Reject (35=3)

| Message | Tags |
|---|---|
| 35=9 | 37 (`NONE` when 102=1 Unknown order, with 39=8), 11, 41, 39, 434 (1 = cancel, 2 = replace), 102 (0 too late, 1 unknown order, 6 duplicate ClOrdID, 99 other), 58 |
| 35=3 | 45 RefSeqNum, 371 RefTagID, 373 SessionRejectReason (0 invalid tag number, 1 required tag missing, 4 tag without value, 5 incorrect value, 6 incorrect data format, 9 CompID problem, 11 invalid MsgType, 13 tag appears more than once), 58 |
| 35=j | 45 RefSeqNum, 372 RefMsgType, 380 BusinessRejectReason (3 = unsupported message type), 58 |
| 35=4 | SequenceReset: 43 PossDupFlag=Y, 122 OrigSendingTime, 123 GapFillFlag=Y, 36 NewSeqNo (answer to a client ResendRequest) |

## FIX 4.2 sessions

The router works in FIX 4.4 terms; `fixsim/fix42.py` translates at the session edge. Differences, from the
FIX 4.2 dictionary:

| Item | FIX 4.4 | FIX 4.2 (this router) |
|---|---|---|
| HandlInst (21) on D and G | Optional | Required: 1, 2 or 3; missing is Reject 371=21, 373=1; other values 373=5 |
| ExecTransType (20) on 35=8 | Not used | Required; always 0 (New) |
| Fill report ExecType (150) | F Trade | 1 Partial fill, or 2 Fill when OrdStatus is 2 |
| Replace confirmation (150=5) OrdStatus | Current state, 0 or 1 | 5 Replaced when nothing has filled, else 1 (precedence: Partially filled > Replaced > New) |
| LastLiquidityInd (851) | Sent on fills | Not defined in 4.2, not sent |
| SessionRejectReason 13 (repeated tag) | Sent | Not defined in 4.2 (values end at 11); the Reject is sent without 373 |
| TargetStrategy (847/848) | Standard tags | Not in 4.2; accepted on 4.2 sessions the same way |
| OrdRejReason (103) 13, 99 and CxlRejReason (102) 6, 99 | Sent | Not in 4.2 (103 ends at 8, 102 at 3): sent as 103=0 / 102=2 Broker option, reason kept in Text (58) |
| MsgType check | FIX 4.4 list | FIX 4.2 list: single characters up to m, plus U-prefixed private types; others get Reject 373=11 |
| OrdStatus 5 Replaced | Not used | On the replace confirmation only; later reports for the order carry its current status (0 or 1) |
| OrderQty (38) | Required | 4.2 allows CashOrderQty (152) instead; not supported, so a D or G without 38 still gets Reject 371=38, 373=1 |
