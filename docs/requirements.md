# Requirements: FIX Order Routing Simulator

## 1. Purpose

A buy-side client sends equity and option orders over FIX 4.4. The router must accept them over a
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
| BR-1 | Clients connect and trade over a standard FIX 4.4 session over TCP/IP. |
| BR-2 | Orders are routed for best price first and lowest execution cost second. |
| BR-3 | Clients see every change in an order's state, with quantities that always reconcile. |
| BR-4 | Clients can cancel or amend working orders, and are told clearly when they cannot. |
| BR-5 | Both U.S. equities and listed options can be traded. |
| BR-6 | Large orders can be worked over time to reduce market impact (TWAP). |

## 4. Functional requirements

| ID | Requirement | Acceptance criteria | Traces to |
|---|---|---|---|
| FR-01 | The acceptor completes a Logon (35=A) before any other message and answers with its own Logon. | A message before Logon disconnects the session. | BR-1 |
| FR-02 | Every inbound message has BodyLength (9) and CheckSum (10) validated; a garbled message is ignored. | A tampered message produces no response. | BR-1 |
| FR-03 | MsgSeqNum (34) is enforced: too low ends the session with Logout (35=5); a gap is answered with ResendRequest (35=2). | Sequence 1 after Logon returns Logout "too low"; sequence 5 when 2 is expected returns ResendRequest 7=2. | BR-1 |
| FR-04 | A TestRequest (35=1) is answered with a Heartbeat (35=0) echoing TestReqID (112). | Heartbeat carries the same 112. | BR-1 |
| FR-05 | A missing required tag is a session Reject (35=3) naming the tag in RefTagID (371). | NewOrderSingle without Side returns 35=3, 371=54. | BR-3 |
| FR-06 | Invalid orders are rejected with ExecutionReport 150=8/39=8 and an OrdRejReason (103). | Unknown symbol 103=1, duplicate ClOrdID 103=6, bad quantity 103=13, other 103=99 with Text. | BR-3 |
| FR-07 | An accepted order is acknowledged with 150=0/39=0 before any fill is reported. | The first report is New. | BR-3 |
| FR-08 | The router takes liquidity best price first; at the same price, the venue with the lower take fee first. | 250 AAPL @ 190.00 fills 100 on SIMB (0.0010) before 150 on SIMA (0.0030). | BR-2 |
| FR-09 | Each fill is reported as 150=F with LastQty (32), LastPx (31), LastMkt (30) and LastLiquidityInd (851). | 851=2 when the order took liquidity, 1 when a resting child was hit. | BR-3 |
| FR-10 | CumQty (14), LeavesQty (151) and AvgPx (6) reconcile on every report: Leaves = OrderQty − Cum while working, 0 when terminal. | The invariant holds on every report in every test. | BR-3 |
| FR-11 | A DAY limit remainder rests on the venue paying the highest maker rebate. | The 100-share remainder rests on SIMC (0.0032) and fills later with 851=1. | BR-2 |
| FR-12 | IOC and market orders cancel any unfilled remainder (150=4). FOK is canceled with no fills if it cannot fill in full. | IOC cancels the rest with Text; FOK for 10,000 reports New then Canceled with CumQty 0. | BR-2, BR-3 |
| FR-13 | Cancel (35=F) of a working order reports Pending Cancel (150=6) then Canceled (150=4), pulls resting child orders, and keeps CumQty. | Resting quantity at venues is 0 after the cancel. | BR-4 |
| FR-14 | Cancel or replace of an unknown or finished order returns OrderCancelReject (35=9) with CxlRejReason (102) and CxlRejResponseTo (434). | Filled order: 102=0 (too late); unknown: 102=1. | BR-4 |
| FR-15 | Replace (35=G) reports Pending Replace (150=E) then Replaced (150=5) with OrdStatus showing the current state, then re-routes the new leaves. Side, Symbol and OrdType cannot change; OrderQty must exceed CumQty. | Replace to 500 @ 190.01 after 300 filled: Replaced with 39=1, then filled. | BR-4 |
| FR-16 | Option orders (167=OPT) require MaturityMonthYear (200), PutOrCall (201) and StrikePrice (202), and are echoed on every report. | An option without a strike is rejected; a valid one reports 201 and 202. | BR-5 |
| FR-17 | TargetStrategy 847=1000 runs a TWAP: OrderQty is split into N slices released every interval (848 `slices=N;interval=S`); unfilled slice quantity rolls into later slices; any remainder after the last slice is canceled. | 300 in 3 slices at 60s fills 100 at t=0, 60 and 120. | BR-6 |
| FR-18 | A parent order never trades against its own resting child order (self-trade prevention). | Venue matching skips resting orders with the same parent. | BR-2 |

## 5. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-1 | Deterministic: routing ties break on fee then venue name, and the TWAP clock is injectable for tests. |
| NFR-2 | No third-party runtime dependencies (Python standard library only). |
| NFR-3 | The full test suite runs in under a second. |

## 6. Out of scope and simplifications

- **No message store:** a sequence gap is answered with ResendRequest, but the acceptor does not replay or gap-fill its own messages.
- **One client session at a time;** no outbound heartbeat timer (the acceptor answers TestRequest only).
- **No market-data feed or NBBO:** venue books are seeded at start-up and move only through our orders and simulated external trades.
- **Fees are per share or contract** and influence routing only; they are not reported back (no Commission tag).
- **No Reg NMS order-protection or locked/crossed-market checks.**
