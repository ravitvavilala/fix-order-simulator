"""FIX 4.4 acceptor session over TCP: logon, sequence numbers, heartbeats, and dispatch to the order manager."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fixsim.engine import OrderManager, Outbound
from fixsim.fix import FixError, Message, MsgType, Tag, decode, encode, split_frames

log = logging.getLogger("fixsim.session")

APPLICATION = {MsgType.NEW_ORDER_SINGLE, MsgType.ORDER_CANCEL_REQUEST, MsgType.ORDER_CANCEL_REPLACE_REQUEST}


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H:%M:%S.%f")[:-3]


class FixSession:
    def __init__(self, engine: OrderManager, sender_comp_id: str = "SIMROUTER"):
        self.engine = engine
        self.sender_comp_id = sender_comp_id
        self.target_comp_id: str | None = None
        self.next_out = 1
        self.next_in = 1
        self.logged_on = False
        self._writer: asyncio.StreamWriter | None = None

    async def run(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writer = writer
        buffer = b""
        try:
            while not writer.is_closing():
                chunk = await reader.read(4096)
                if not chunk:
                    break
                frames, buffer = split_frames(buffer + chunk)
                for raw in frames:
                    if not await self.on_raw(raw):
                        return
        finally:
            writer.close()

    async def on_raw(self, raw: bytes) -> bool:
        """Process one framed message; returns False when the session must end."""
        try:
            msg = decode(raw)
        except FixError as exc:
            log.warning("ignoring garbled message: %s", exc)
            return True
        seq = int(msg.get(Tag.MSG_SEQ_NUM, "0"))
        if not self.logged_on:
            if msg.msg_type != MsgType.LOGON:
                log.warning("first message was %s, not Logon; disconnecting", msg.msg_type)
                return False
            self.target_comp_id = msg.get(Tag.SENDER_COMP_ID)
            self.logged_on = True
            self.next_in = seq + 1
            await self.send(MsgType.LOGON, [(Tag.ENCRYPT_METHOD, 0), (Tag.HEART_BT_INT, msg.get(Tag.HEART_BT_INT, "30"))])
            return True
        if seq < self.next_in:
            await self.send(MsgType.LOGOUT, [(Tag.TEXT, f"MsgSeqNum too low, expecting {self.next_in} but received {seq}")])
            return False
        if seq > self.next_in:
            await self.send(MsgType.RESEND_REQUEST, [(Tag.BEGIN_SEQ_NO, self.next_in), (Tag.END_SEQ_NO, 0)])
        self.next_in = seq + 1
        return await self.dispatch(msg)

    async def dispatch(self, msg: Message) -> bool:
        kind = msg.msg_type
        if kind == MsgType.HEARTBEAT:
            return True
        if kind == MsgType.TEST_REQUEST:
            await self.send(MsgType.HEARTBEAT, [(Tag.TEST_REQ_ID, msg.get(Tag.TEST_REQ_ID, ""))])
            return True
        if kind == MsgType.LOGOUT:
            await self.send(MsgType.LOGOUT, [])
            return False
        if kind in APPLICATION:
            for out in self.engine.handle(msg):
                await self.send(out.msg_type, out.fields)
            return True
        await self.send(MsgType.REJECT, [
            (Tag.REF_SEQ_NUM, msg.get(Tag.MSG_SEQ_NUM, "0")),
            (Tag.SESSION_REJECT_REASON, 11),
            (Tag.TEXT, f"Unsupported MsgType {kind}"),
        ])
        return True

    async def send(self, msg_type: str, fields: list[tuple[int, object]]) -> None:
        body = [
            (Tag.MSG_TYPE, msg_type),
            (Tag.SENDER_COMP_ID, self.sender_comp_id),
            (Tag.TARGET_COMP_ID, self.target_comp_id or "CLIENT"),
            (Tag.MSG_SEQ_NUM, self.next_out),
            (Tag.SENDING_TIME, utc_timestamp()),
            *fields,
        ]
        self.next_out += 1
        self._writer.write(encode(body))
        await self._writer.drain()

    async def push(self, outbound: list[Outbound]) -> None:
        for out in outbound:
            await self.send(out.msg_type, out.fields)
