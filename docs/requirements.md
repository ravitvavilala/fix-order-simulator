# Requirements: FIX Order Routing Simulator

## 1. Purpose

A buy-side client sends equity and option orders over FIX 4.2 or FIX 4.4. The router must accept them over a
FIX session, route each order across several venues for the best price at the lowest cost, report
every state change back to the client, and support cancel, replace and a time-sliced (TWAP) strategy.

## 2. Stakeholders

| Stakeholder | Interest |
|---|---|
| Buy-side trader (client) | Fast, complete fills at the best price; accurate, timely execution reports |
| Trading desk / routing operations | Predictable routing, clear reasons for every reject and cancel |
| Compliance | An audit trail of every order state and fill, with the venue it came from |
| Development | Unambiguous rules, message formats and edge cases |
| QA | Testable acceptance criteria traced to scenarios |

## 3. Business requirements

| ID | Requirement |
|---|---|
| BR-1 | Clients connect and trade over a standard FIX 4.2 or FIX 4.4 session over TCP/IP. |
| BR-2 | Orders are routed for best price first and lowest execution cost second. |
| BR-3 | Clients see every change in an order's state, with quantities that always reconcile. |
| BR-4 | Clients can cancel or amend working orders, and are told clearly when they cannot. |
| BR-5 | Both U.S. equities and listed options can be traded. |
| BR-6 | Large orders can be worked over time to reduce market impact (TWAP). |

## 4. Functional requirements

| ID | Requirement | Acceptance criteria | Traces to |
|---|---|---|---|
| FR-01 | The first message must be a valid Logon (35=A): SenderCompID (49) present, TargetCompID (56) = SIMROUTER, HeartBtInt (108) a whole number. Anything else, including a garbled or malformed first message, closes the connection with no reply. Sessions are not persisted, so Logon must carry MsgSeqNum 1, else Logout. A second Logon for a CompID that already has a live session is closed with no reply. | A Heartbeat before Logon, a Logon to the wrong TargetCompID or without 108, and a duplicate Logon are each closed with no reply; Logon at 34=5 gets Logout. | BR-1 |
| FR-02 | Every inbound message has BodyLength (9) and CheckSum (10) validated, and messages are framed from the TCP stream however the bytes arrive. A garbled message (bad framing, length, checksum or non-ASCII) is ignored. An intact message with an invalid field (bad tag number, empty value, repeated tag) still passes the sequence and CompID checks, then gets a session Reject (373=0, 4 or 13) and uses up its MsgSeqNum. After Logon, a different or unsupported BeginString, or a missing/invalid MsgSeqNum, ends the session with Logout; before Logon they close the connection with no reply. A repeated tag the router reads (for example 55 or 21) is a 373=13 Reject. | A tampered message produces no response; an empty 58= gets 373=4 and the session continues; messages separated by newlines or split at any byte are all framed. | BR-1 |
| FR-03 | MsgSeqNum (34) is enforced. Too low ends the session with Logout unless PossDupFlag (43=Y) marks it a duplicate, which is ignored. A message above the expected number is discarded and one ResendRequest (35=2, 7=expected, 16=0) is sent; the client then gap-fills its session messages and resends its application messages with 43=Y and OrigSendingTime (122). A resent message without 122 is rejected (371=122). An out-of-order ResendRequest is answered at once and an out-of-order Logout is acknowledged at once. SequenceReset (35=4) is honored in GapFill (123=Y, NewSeqNo above its own MsgSeqNum) and Reset (123=N or absent, never backwards) modes; a Reset also ends any pending resend. A client ResendRequest is answered with SequenceReset-GapFill (43=Y, 122) for the range asked (EndSeqNo 16 honored), because the acceptor keeps no message store; a range it has not sent gets Reject 373=5, a non-numeric one 373=6. SenderCompID/TargetCompID must match the logged-on pair, else Reject 373=9 naming the wrong tag, and Logout. | Gap at 3 gets ResendRequest 7=2; the order sent at 4 is discarded; after GapFill 34=2 36=4 and the order resent at 34=4 with 43=Y, it executes. | BR-1 |
| FR-04 | A TestRequest (35=1) is answered with a Heartbeat (35=0) echoing TestReqID (112); a TestRequest without 112 gets Reject 371=112. | Heartbeat carries the same 112. | BR-1 |
| FR-05 | A missing required tag, including TransactTime (60) on D, F and G, is a session Reject (35=3) naming the tag in RefTagID (371). | NewOrderSingle without Side returns 35=3, 371=54; without 60 returns 371=60. | BR-3 |
| FR-06 | Invalid orders are rejected with ExecutionReport 150=8/39=8 and an OrdRejReason (103). | Unknown symbol 103=1, duplicate ClOrdID 103=6, bad quantity 103=13, other 103=99 with Text. | BR-3 |
| FR-07 | An accepted order is acknowledged with 150=0/39=0 before any fill is reported. | The first report is New. | BR-3 |
| FR-08 | The router takes liquidity best price first; at the same price, the venue with the lower take fee first. | 250 AAPL @ 190.00 fills 100 on SIMB (0.0010) before 150 on SIMA (0.0030). | BR-2 |
| FR-09 | Each fill is reported as 150=F with LastQty (32), LastPx (31), LastMkt (30) and LastLiquidityInd (851). | 851=2 when the order took liquidity, 1 when a resting child was hit. | BR-3 |
| FR-10 | CumQty (14), LeavesQty (151) and AvgPx (6) reconcile on every report: Leaves = OrderQty − Cum while working, 0 when terminal. | The invariant holds on every report in every engine-level test. | BR-3 |
| FR-11 | A DAY limit remainder rests on the venue paying the highest maker rebate. | The 100-share remainder rests on SIMC (0.0032) and fills later with 851=1. | BR-2 |
| FR-12 | IOC and market orders cancel any unfilled remainder (150=4). FOK is canceled with no fills if it cannot fill in full; a market FOK checks all liquidity, ignoring any Price. | IOC cancels the rest with Text; a market order for 2,000 fills the 1,450 available and cancels the rest; FOK for 10,000 reports New then Canceled with CumQty 0; market FOK for 450 fills. | BR-2, BR-3 |
| FR-13 | Cancel (35=F) of a working order reports Pending Cancel (150=6) then Canceled (150=4), pulls resting child orders, and keeps CumQty. | Resting quantity at venues is 0 after the cancel. | BR-4 |
| FR-14 | Cancel or replace of an unknown or finished order returns OrderCancelReject (35=9) with CxlRejReason (102) and CxlRejResponseTo (434). OrigClOrdID (41) must be the order's current ClOrdID, not an earlier one. ClOrdIDs belong to the client (SenderCompID) that sent them, so one client cannot reach another's order. An unknown-order reject (102=1) carries OrderID NONE and OrdStatus 8. | Filled order: 102=0 (too late); unknown or stale 41, or another client's ClOrdID: 102=1. | BR-4 |
| FR-15 | Replace (35=G) reports Pending Replace (150=E) then Replaced (150=5) with OrdStatus showing the current state, then re-routes the new leaves. Side, Symbol and OrdType cannot change; OrderQty must exceed CumQty. | Replace to 500 @ 190.01 after 300 filled: Replaced with 39=1, then filled. | BR-4 |
| FR-16 | Option orders (167=OPT) require MaturityMonthYear (200), PutOrCall (201) and a positive StrikePrice (202), and are echoed on every report. The strike must match a listed series exactly (compared as a decimal), and an equity symbol never matches an option series. | An option without a strike is rejected; strikes 450.001 and 450.0000001 are unknown symbols; equity symbol "SPY 202612 C 450" is unknown; a valid one reports 201 and 202. | BR-5 |
| FR-17 | TargetStrategy 847=1000 runs a TWAP: OrderQty is split into N slices released every interval (848 `slices=N;interval=S`); unfilled slice quantity rolls into later slices; any remainder after the last slice is canceled. A TWAP must be a Day order, and the schedule keeps running if the client disconnects. | 300 in 3 slices at 60s fills 100 at t=0, 60 and 120; a TWAP with 59=3 is rejected. | BR-6 |
| FR-18 | Orders from the same client (SenderCompID) never trade with each other (self-trade prevention); orders from different clients can. | A client's sell skips its own resting buy; another client's sell fills it. | BR-2 |
| FR-19 | Prices must be plain FIX decimals (digits, optional point and sign; no exponent, spaces, inf or nan), and strategy parameters finite numbers. | Price inf, nan, 1e3, 190_00 or " 190", or interval=nan, is rejected. | BR-3 |
| FR-20 | A valid message type the router does not support gets BusinessMessageReject (35=j, 380=3, 372=MsgType). A MsgType the session's FIX version does not define gets session Reject 373=11. An inbound 35=j is not answered. | 35=V gets 35=j with 372=V; 35=ZZ gets 35=3 with 373=11. | BR-3 |
| FR-21 | Several clients can be connected at once, one live session per SenderCompID. Every execution report goes only to the client that owns the order, including fills on a resting order caused by another client's trade and TWAP slices released later. | CLIENT2's sell fills CLIENT1's resting buy: CLIENT2 sees only its own order, CLIENT1 gets its fill with 851=1; a connection that never logs on cannot take over another client's reports. | BR-1, BR-3 |
| FR-22 | A client can log on with BeginString FIX.4.2 or FIX.4.4; the session keeps that version, and a message in another version ends it with Logout. On a FIX 4.2 session: HandlInst (21) is required on D and G (values 1-3, else Reject 371=21); every ExecutionReport carries ExecTransType 20=0; fills are ExecType 1 (partial) or 2 (fill); a replace confirmation carries OrdStatus 5 unless the order has fills (4.2 precedence: Partially filled > Replaced > New); LastLiquidityInd (851) and SessionRejectReason 13, which 4.2 does not define, are not sent; OrdRejReason 13/99 and CxlRejReason 6/99 go out as 103=0 / 102=2 (Broker option) with the reason in Text; message types are checked against the 4.2 list. | Over 4.2, 250 AAPL reports 150=0/1/2 with 20=0 and no 851; replacing an unfilled order reports 150=5, 39=5; a D without 21 gets 35=3, 371=21; a 4.4 message on a 4.2 session gets Logout; OrderQty 0 gets 103=0; 35=AE gets 373=11; a 4.2 client and a 4.4 client trading with each other each get reports in their own version. | BR-1, BR-3 |

## 5. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-1 | Deterministic: routing ties break on fee then venue name, and the TWAP clock is injectable for tests. |
| NFR-2 | No third-party runtime dependencies (Python standard library only). |
| NFR-3 | The full test suite runs in under two seconds. |

## 6. Out of scope and simplifications

- **No message store:** when the client asks for a resend, the acceptor answers with SequenceReset-GapFill instead of replaying its messages, and each connection starts at MsgSeqNum 1. Reports for a client that is not logged on (for example TWAP slices released after it disconnects) are dropped, not queued.
- **One ResendRequest at a time:** if a message inside the client's resend is itself lost, the acceptor does not ask again; the client recovers by reconnecting.
- **No outbound heartbeat timer** (the acceptor answers TestRequest only), and SendingTime (52) and OrigSendingTime (122) accuracy is not checked, only presence of 122.
- **No market-data feed or NBBO:** venue books are seeded at start-up and move only through our orders and simulated external trades.
- **Quantity only as OrderQty (38):** FIX 4.2 allows CashOrderQty (152) instead; it is not supported, so an order without 38 gets Reject 371=38.
- **Fees are per share or contract** and influence routing only; they are not reported back (no Commission tag).
- **No Reg NMS order-protection or locked/crossed-market checks.**
