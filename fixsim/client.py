"""A scripted FIX client that walks the order lifecycle over TCP: python -m fixsim.client [--port 9878]"""

from __future__ import annotations

import argparse
import asyncio

from fixsim.fix import Message, MsgType, Tag, decode, encode, split_frames
from fixsim.session import utc_timestamp

EXEC_TYPE = {"0": "New", "4": "Canceled", "5": "Replaced", "6": "PendingCancel", "8": "Rejected",
             "E": "PendingReplace", "F": "Trade"}
ORD_STATUS = {"0": "New", "1": "PartiallyFilled", "2": "Filled", "4": "Canceled", "6": "PendingCancel",
              "8": "Rejected", "E": "PendingReplace"}
MSG_NAME = {"0": "Heartbeat", "2": "ResendRequest", "3": "Reject", "5": "Logout", "8": "ExecutionReport",
            "9": "OrderCancelReject", "A": "Logon"}


class FixClient:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, sender: str = "CLIENT1"):
        self.reader, self.writer, self.sender = reader, writer, sender
        self.seq = 1
        self._buffer = b""
        self._pending: list[Message] = []

    @classmethod
    async def connect(cls, host: str, port: int, sender: str = "CLIENT1") -> "FixClient":
        reader, writer = await asyncio.open_connection(host, port)
        return cls(reader, writer, sender)

    async def send(self, msg_type: str, fields: list[tuple[int, object]], seq: int | None = None) -> None:
        body = [(Tag.MSG_TYPE, msg_type), (Tag.SENDER_COMP_ID, self.sender), (Tag.TARGET_COMP_ID, "SIMROUTER"),
                (Tag.MSG_SEQ_NUM, seq if seq is not None else self.seq), (Tag.SENDING_TIME, utc_timestamp()), *fields]
        if seq is None:
            self.seq += 1
        self.writer.write(encode(body))
        await self.writer.drain()

    async def receive(self, timeout: float = 2.0) -> Message | None:
        while not self._pending:
            try:
                chunk = await asyncio.wait_for(self.reader.read(4096), timeout)
            except asyncio.TimeoutError:
                return None
            if not chunk:
                return None
            frames, self._buffer = split_frames(self._buffer + chunk)
            self._pending += [decode(f) for f in frames]
        return self._pending.pop(0)

    async def receive_until(self, done, timeout: float = 2.0) -> list[Message]:
        got = []
        while True:
            msg = await self.receive(timeout)
            if msg is None:
                return got
            got.append(msg)
            if done(msg):
                return got

    async def logon(self) -> Message | None:
        await self.send(MsgType.LOGON, [(Tag.ENCRYPT_METHOD, 0), (Tag.HEART_BT_INT, 30)])
        return await self.receive()

    def close(self) -> None:
        self.writer.close()


def describe(msg: Message) -> str:
    kind = msg.msg_type
    if kind != MsgType.EXECUTION_REPORT:
        extra = msg.get(Tag.TEXT) or msg.get(Tag.TEST_REQ_ID) or ""
        return f"{MSG_NAME.get(kind, kind):<18} {extra}"
    last = f"{msg.get(Tag.LAST_QTY)} @ {msg.get(Tag.LAST_PX)} on {msg.get(Tag.LAST_MKT)}" if msg.has(Tag.LAST_PX) else ""
    return (f"ExecutionReport    {EXEC_TYPE[msg.get(Tag.EXEC_TYPE)]:<15} {ORD_STATUS[msg.get(Tag.ORD_STATUS)]:<16}"
            f" ClOrdID={msg.get(Tag.CL_ORD_ID):<4} Cum={msg.get(Tag.CUM_QTY):<4} Leaves={msg.get(Tag.LEAVES_QTY):<4}"
            f" AvgPx={msg.get(Tag.AVG_PX):<9} {last} {msg.get(Tag.TEXT) or ''}").rstrip()


def order(cl: str, symbol: str, side: str, qty: int, price: float | None, tif: str = "0",
          extra: tuple = ()) -> list:
    fields = [(Tag.CL_ORD_ID, cl), (Tag.SYMBOL, symbol), (Tag.SIDE, side), (Tag.ORDER_QTY, qty),
              (Tag.ORD_TYPE, "2" if price is not None else "1"), (Tag.TIME_IN_FORCE, tif)]
    if price is not None:
        fields.append((Tag.PRICE, price))
    return fields + list(extra)


def quiet(msg: Message) -> bool:
    return False


async def scenario(client: FixClient, title: str, msg_type: str, fields: list, wait: float = 0.3) -> None:
    print(f"\n== {title}")
    await client.send(msg_type, fields)
    for msg in await client.receive_until(quiet, timeout=wait):
        print("  <-", describe(msg))


async def main(host: str, port: int) -> None:
    client = await FixClient.connect(host, port)
    print("  <-", describe(await client.logon()))
    await scenario(client, "1. Buy 400 AAPL @ 190.00 DAY: sweep best price across venues, rest the remainder for rebate",
                   MsgType.NEW_ORDER_SINGLE, order("A1", "AAPL", "1", 400, 190.00))
    await scenario(client, "2. Replace A1 -> A2: raise to 500 @ 190.01, re-route the new leaves",
                   MsgType.ORDER_CANCEL_REPLACE_REQUEST,
                   [(Tag.ORIG_CL_ORD_ID, "A1")] + order("A2", "AAPL", "1", 500, 190.01))
    await scenario(client, "3. Cancel A2 after it is filled: refused, too late to cancel",
                   MsgType.ORDER_CANCEL_REQUEST,
                   [(Tag.CL_ORD_ID, "A3"), (Tag.ORIG_CL_ORD_ID, "A2"), (Tag.SYMBOL, "AAPL"), (Tag.SIDE, "1")])
    await scenario(client, "4. Sell 100 MSFT IOC @ 410.05: nothing marketable, remainder canceled",
                   MsgType.NEW_ORDER_SINGLE, order("M1", "MSFT", "2", 100, 410.05, tif="3"))
    await scenario(client, "5. Buy 30 SPY Dec-2026 450 calls @ 12.40: options order flow",
                   MsgType.NEW_ORDER_SINGLE,
                   order("O1", "SPY", "1", 30, 12.40, extra=((Tag.SECURITY_TYPE, "OPT"), (Tag.MATURITY_MONTH_YEAR, "202612"),
                                                             (Tag.PUT_OR_CALL, "1"), (Tag.STRIKE_PRICE, 450))))
    await scenario(client, "6. Unknown symbol: business reject",
                   MsgType.NEW_ORDER_SINGLE, order("Z1", "ZZZZ", "1", 10, 5.00))
    await scenario(client, "7. TWAP buy 150 MSFT @ 410.15 in 3 slices, 1s apart (TargetStrategy 847=1000)",
                   MsgType.NEW_ORDER_SINGLE,
                   order("T1", "MSFT", "1", 150, 410.15, extra=((Tag.TARGET_STRATEGY, 1000),
                                                                 (Tag.TARGET_STRATEGY_PARAMETERS, "slices=3;interval=1"))),
                   wait=3.0)
    await scenario(client, "8. TestRequest: the acceptor answers with a Heartbeat",
                   MsgType.TEST_REQUEST, [(Tag.TEST_REQ_ID, "PING-1")])
    await scenario(client, "9. Logout", MsgType.LOGOUT, [])
    client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9878)
    args = parser.parse_args()
    asyncio.run(main(args.host, args.port))
