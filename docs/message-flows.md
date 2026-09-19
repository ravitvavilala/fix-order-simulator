# Message flows

Participants: the **Client** (buy-side OMS), the **Router** (FIX acceptor + order manager + smart order
router), and three simulated venues with different fee models. Prices go on the wire without trailing zeros
(190.00 is sent as 44=190).

| Venue | Take fee | Maker rebate | Role in routing |
|---|---|---|---|
| SIMA | 0.0030 | 0.0020 | Maker-taker |
| SIMB | 0.0010 | 0.0000 | Cheapest to take |
| SIMC | 0.0028 | 0.0032 | Best rebate, so remainders rest here |

## 1. Session start

```mermaid
sequenceDiagram
    participant C as Client
    participant R as Router
    C->>R: Logon 35=A, 34=1, 98=0, 108=30
    R->>C: Logon 35=A, 34=1
    C->>R: TestRequest 35=1, 112=PING-1
    R->>C: Heartbeat 35=0, 112=PING-1
```

## 2. New order: sweep the best price, rest the remainder (Buy 400 AAPL @ 190.00 DAY)

```mermaid
sequenceDiagram
    participant C as Client
    participant R as Router
    participant B as SIMB
    participant A as SIMA
    participant V as SIMC
    C->>R: NewOrderSingle 35=D, 11=A1, 55=AAPL, 54=1, 38=400, 40=2, 44=190, 59=0, 60=<time>
    R->>C: ExecutionReport 150=0 New, 39=0, 151=400
    Note over R: Plan: 190.00 on SIMB (fee 0.0010) then SIMA (0.0030)
    R->>B: IOC child buy 100 @ 190.00
    B-->>R: fill 100 @ 190.00
    R->>A: IOC child buy 200 @ 190.00
    A-->>R: fill 200 @ 190.00
    R->>V: DAY child buy 100 @ 190.00 (rests for the 0.0032 rebate)
    Note over R: Routing finishes first, then one report per fill
    R->>C: ExecutionReport 150=F, 39=1, 32=100, 31=190, 30=SIMB, 851=2, 14=100, 151=300
    R->>C: ExecutionReport 150=F, 39=1, 32=200, 30=SIMA, 14=300, 151=100
```

## 3. Resting child is hit later

```mermaid
sequenceDiagram
    participant X as Other participant
    participant V as SIMC
    participant R as Router
    participant C as Client
    X->>V: Sell 100 @ 190.00
    V-->>R: our resting child filled 100 @ 190.00 (we added liquidity)
    R->>C: ExecutionReport 150=F, 39=2 Filled, 30=SIMC, 851=1, 14=400, 151=0
```

## 4. Cancel

```mermaid
sequenceDiagram
    participant C as Client
    participant R as Router
    participant V as SIMC
    C->>R: OrderCancelRequest 35=F, 11=A2, 41=A1
    R->>C: ExecutionReport 150=6 Pending Cancel, 39=6, 11=A2, 41=A1
    R->>V: cancel resting child
    R->>C: ExecutionReport 150=4 Canceled, 39=4, 14=300 (kept), 151=0
```

## 5. Cancel/replace

```mermaid
sequenceDiagram
    participant C as Client
    participant R as Router
    participant V as SIMC
    participant B as SIMB
    C->>R: OrderCancelReplaceRequest 35=G, 11=A2, 41=A1, 38=500, 44=190.01
    R->>C: ExecutionReport 150=E Pending Replace, 39=E
    R->>V: cancel resting child (100)
    R->>C: ExecutionReport 150=5 Replaced, 39=1 (status now, not "Replaced"), 38=500, 151=200
    R->>B: IOC child buy 200 @ 190.01
    B-->>R: fill 200 @ 190.01
    R->>C: ExecutionReport 150=F, 39=2 Filled, 14=500, 6=190.004
```

## 6. Three kinds of "no"

```mermaid
sequenceDiagram
    participant C as Client
    participant R as Router
    C->>R: NewOrderSingle without 54 (Side)
    R->>C: Reject 35=3, 371=54, 373=1 (session level: message unusable)
    C->>R: NewOrderSingle 55=ZZZZ
    R->>C: ExecutionReport 150=8, 39=8, 103=1 Unknown symbol (business level: order refused)
    C->>R: OrderCancelRequest 41=<filled order>
    R->>C: OrderCancelReject 35=9, 434=1, 102=0 Too late to cancel (the order is unchanged)
```

## 7. Gap recovery

```mermaid
sequenceDiagram
    participant C as Client
    participant R as Router
    C->>R: Heartbeat 34=3 (2 was lost)
    R->>C: ResendRequest 35=2, 7=2, 16=0
    C->>R: NewOrderSingle 11=G1, 34=4
    Note over R: Above the expected number: discarded, no second ResendRequest
    C->>R: SequenceReset 35=4, 34=2, 43=Y, 122=<orig time>, 123=Y, 36=4 (GapFill over 2 and the Heartbeat at 3)
    C->>R: NewOrderSingle 11=G1, 34=4, 43=Y, 122=<orig time> (resent)
    R->>C: ExecutionReport 150=0, 11=G1
    C->>R: TestRequest 34=5, 112=AFTER-GAP
    R->>C: Heartbeat 112=AFTER-GAP
```

Session messages (Heartbeat, TestRequest, ResendRequest, Logout, Logon) are gap-filled, never resent;
application messages are resent with PossDupFlag and OrigSendingTime. A ResendRequest or Logout that
arrives above the expected number is acted on at once.

A client's own ResendRequest is answered with SequenceReset 35=4, 43=Y, 122, 123=Y, 36=the number after
the requested range (the next outbound number when 16=0), because the acceptor keeps no message store.

## 8. TWAP (847=1000, 848=slices=3;interval=60)

```mermaid
sequenceDiagram
    participant C as Client
    participant R as Router
    participant A as Venues
    C->>R: NewOrderSingle 38=300, 847=1000, 848=slices=3;interval=60
    R->>C: ExecutionReport 150=0 New
    R->>A: t=0s slice 1: IOC 100
    R->>C: ExecutionReport 150=F, 14=100
    R->>A: t=60s slice 2: IOC ceil(leaves / 2)
    R->>C: ExecutionReport 150=F, 14=200
    R->>A: t=120s slice 3: IOC the rest
    R->>C: ExecutionReport 150=F, 39=2 (or 150=4 Canceled for any unfilled remainder)
```
