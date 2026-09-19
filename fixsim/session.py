"""FIX 4.2 / 4.4 acceptor session over TCP: logon, sequence numbers and recovery, heartbeats, dispatch."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fixsim import fix42
from fixsim.engine import OrderManager, Outbound
from fixsim.fix import (
    BEGIN_STRING,
    BeginStringError,
    FixError,
    Message,
    MsgType,
    RejectReason,
    Tag,
    decode,
    encode,
    split_frames,
)

log = logging.getLogger("fixsim.session")

APPLICATION = {MsgType.NEW_ORDER_SINGLE, MsgType.ORDER_CANCEL_REQUEST, MsgType.ORDER_CANCEL_REPLACE_REQUEST}
NOT_REPLAYED = {MsgType.HEARTBEAT, MsgType.TEST_REQUEST, MsgType.RESEND_REQUEST, MsgType.LOGOUT, MsgType.LOGON}


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H:%M:%S.%f")[:-3]


async def deliver(sessions: dict[str, "FixSession"], outbound: list[Outbound],
                  requester: "FixSession | None" = None) -> None:
    """Send each report to the session of the client that owns the order, never to another client."""
    for out in outbound:
        target = sessions.get(out.owner) if out.owner else requester
        if target is None or not target.logged_on:
            log.warning("%s is not logged on; dropping %s (no message store)", out.owner, out.msg_type)
            continue
        try:
            await target.send(out.msg_type, out.fields)
        except (ConnectionError, RuntimeError) as exc:
            log.warning("could not deliver to %s: %s", target.target_comp_id, exc)


class FixSession:
    def __init__(self, engine: OrderManager, sessions: dict[str, "FixSession"] | None = None,
                 sender_comp_id: str = "SIMROUTER"):
        self.engine = engine
        self.sessions = {} if sessions is None else sessions
        self.sender_comp_id = sender_comp_id
        self.target_comp_id: str | None = None
        self.begin_string: str | None = None
        self.next_out = 1
        self.next_in = 1
        self.logged_on = False
        self.resend_through = 0
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
            self.logged_on = False
            if self.sessions.get(self.target_comp_id) is self:
                del self.sessions[self.target_comp_id]
            writer.close()

    async def on_raw(self, raw: bytes) -> bool:
        """Process one framed message; returns False when the session must end."""
        try:
            msg = decode(raw)
        except FixError as exc:
            if not self.logged_on:
                return False
            if isinstance(exc, BeginStringError):
                await self.logout(str(exc))
                return False
            log.warning("ignoring garbled message: %s", exc)
            return True
        if self.logged_on and msg.get(Tag.BEGIN_STRING) != self.begin_string:
            await self.logout(f"BeginString {msg.get(Tag.BEGIN_STRING)} does not match this session's {self.begin_string}")
            return False
        seq_text = msg.get(Tag.MSG_SEQ_NUM, "")
        if not seq_text.isdigit():
            if self.logged_on:
                await self.logout("MsgSeqNum (34) missing or invalid")
            return False
        seq = int(seq_text)
        if not self.logged_on:
            return await self.on_logon(msg, seq)
        wrong = next((tag for tag, want in ((Tag.SENDER_COMP_ID, self.target_comp_id),
                                            (Tag.TARGET_COMP_ID, self.sender_comp_id)) if msg.get(tag) != want), None)
        if wrong is not None:
            await self.reject(seq, wrong, RejectReason.COMP_ID_PROBLEM, "CompID does not match this session")
            await self.logout("CompID problem")
            return False
        kind = msg.msg_type
        if kind == MsgType.SEQUENCE_RESET and msg.get(Tag.GAP_FILL_FLAG, "N") == "N":
            return await self.sequence_reset(msg, seq, gap_fill=False)
        if seq < self.next_in:
            if msg.get(Tag.POSS_DUP_FLAG) == "Y":
                return True
            await self.logout(f"MsgSeqNum too low, expecting {self.next_in} but received {seq}")
            return False
        if seq > self.next_in:
            return await self.on_gap(msg, seq)
        self.next_in += 1
        if msg.error:
            await self.reject(seq, msg.error.ref_tag, msg.error.reason, str(msg.error))
            return True
        if msg.get(Tag.POSS_DUP_FLAG) == "Y":
            if not msg.has(Tag.ORIG_SENDING_TIME):
                await self.reject(seq, Tag.ORIG_SENDING_TIME, RejectReason.REQUIRED_TAG_MISSING,
                                  "OrigSendingTime (122) is required when PossDupFlag=Y")
                return True
            if kind in NOT_REPLAYED:
                return True
        if kind == MsgType.SEQUENCE_RESET:
            return await self.sequence_reset(msg, seq, gap_fill=True)
        return await self.dispatch(msg, seq)

    async def on_logon(self, msg: Message, seq: int) -> bool:
        sender, heartbeat = msg.get(Tag.SENDER_COMP_ID), msg.get(Tag.HEART_BT_INT, "")
        if (msg.msg_type != MsgType.LOGON or msg.error or not sender
                or msg.get(Tag.TARGET_COMP_ID) != self.sender_comp_id or not heartbeat.isdigit()):
            log.warning("first message was not a valid Logon; disconnecting")
            return False
        if sender in self.sessions:
            log.warning("%s already has a live session; disconnecting the second connection", sender)
            return False
        self.target_comp_id = sender
        self.begin_string = msg.get(Tag.BEGIN_STRING)
        if seq != 1:
            await self.logout(f"Expected MsgSeqNum 1 at Logon (sessions are not persisted), received {seq}")
            return False
        self.sessions[sender] = self
        self.logged_on = True
        self.next_in = 2
        await self.send(MsgType.LOGON, [(Tag.ENCRYPT_METHOD, 0), (Tag.HEART_BT_INT, heartbeat)])
        return True

    async def on_gap(self, msg: Message, seq: int) -> bool:
        """Messages above the expected number are discarded; the client resends them with PossDupFlag=Y."""
        if msg.msg_type == MsgType.RESEND_REQUEST:
            await self.answer_resend(msg, seq)
        if msg.msg_type == MsgType.LOGOUT:
            await self.send(MsgType.LOGOUT, [])
            return False
        if self.resend_through < self.next_in:
            self.resend_through = seq
            await self.send(MsgType.RESEND_REQUEST, [(Tag.BEGIN_SEQ_NO, self.next_in), (Tag.END_SEQ_NO, 0)])
        return True

    async def sequence_reset(self, msg: Message, seq: int, gap_fill: bool) -> bool:
        new_seq = msg.get(Tag.NEW_SEQ_NO)
        if msg.error:
            problem = (msg.error.ref_tag, msg.error.reason, str(msg.error))
        elif gap_fill and msg.get(Tag.GAP_FILL_FLAG) != "Y":
            problem = (Tag.GAP_FILL_FLAG, RejectReason.VALUE_INCORRECT, "GapFillFlag (123) must be Y or N")
        elif new_seq is None:
            problem = (Tag.NEW_SEQ_NO, RejectReason.REQUIRED_TAG_MISSING, "NewSeqNo (36) missing")
        elif not new_seq.isdigit():
            problem = (Tag.NEW_SEQ_NO, RejectReason.INCORRECT_DATA_FORMAT, "NewSeqNo (36) must be a whole number")
        elif (int(new_seq) <= seq) if gap_fill else (int(new_seq) < self.next_in):
            problem = (Tag.NEW_SEQ_NO, RejectReason.VALUE_INCORRECT, f"NewSeqNo {new_seq} would not move forward")
        else:
            self.next_in = int(new_seq)
            if not gap_fill:
                self.resend_through = 0
            return True
        await self.reject(seq, *problem)
        return True

    async def dispatch(self, msg: Message, seq: int) -> bool:
        kind = msg.msg_type
        if kind in (MsgType.HEARTBEAT, MsgType.REJECT, MsgType.LOGON, MsgType.BUSINESS_MESSAGE_REJECT):
            return True
        if kind == MsgType.TEST_REQUEST:
            if not msg.has(Tag.TEST_REQ_ID):
                await self.reject(seq, Tag.TEST_REQ_ID, RejectReason.REQUIRED_TAG_MISSING, "TestReqID (112) missing")
            else:
                await self.send(MsgType.HEARTBEAT, [(Tag.TEST_REQ_ID, msg.get(Tag.TEST_REQ_ID))])
            return True
        if kind == MsgType.RESEND_REQUEST:
            await self.answer_resend(msg, seq)
            return True
        if kind == MsgType.LOGOUT:
            await self.send(MsgType.LOGOUT, [])
            return False
        if kind in APPLICATION:
            problem = fix42.inbound_problem(msg) if self.begin_string == fix42.BEGIN_STRING else None
            if problem:
                await self.reject(seq, *problem)
                return True
            await deliver(self.sessions, self.engine.handle(msg), self)
            return True
        defined = fix42.defines(kind) if self.begin_string == fix42.BEGIN_STRING else MsgType.is_defined(kind)
        if not defined:
            await self.reject(seq, Tag.MSG_TYPE, RejectReason.INVALID_MSG_TYPE, f"Invalid MsgType {kind}")
            return True
        await self.send(MsgType.BUSINESS_MESSAGE_REJECT, [
            (Tag.REF_SEQ_NUM, seq),
            (Tag.REF_MSG_TYPE, kind),
            (Tag.BUSINESS_REJECT_REASON, 3),
            (Tag.TEXT, f"Unsupported message type {kind}"),
        ])
        return True

    async def answer_resend(self, msg: Message, seq: int) -> None:
        """No message store: answer a resend request by gap-filling the counterparty past the requested range."""
        values = {}
        for tag in (Tag.BEGIN_SEQ_NO, Tag.END_SEQ_NO):
            value = msg.get(tag)
            if value is None:
                return await self.reject(seq, tag, RejectReason.REQUIRED_TAG_MISSING, f"Tag {tag} missing")
            if not value.isdigit():
                return await self.reject(seq, tag, RejectReason.INCORRECT_DATA_FORMAT, f"Tag {tag} must be a whole number")
            values[tag] = int(value)
        begin, end = values[Tag.BEGIN_SEQ_NO], values[Tag.END_SEQ_NO]
        last_sent = self.next_out - 1
        if not 1 <= begin <= last_sent or (end and end < begin):
            return await self.reject(seq, Tag.BEGIN_SEQ_NO, RejectReason.VALUE_INCORRECT,
                                     f"Cannot resend {begin} to {end or 'end'}; last sent is {last_sent}")
        new_seq = self.next_out if end == 0 or end >= last_sent else end + 1
        await self.send(MsgType.SEQUENCE_RESET, [(Tag.GAP_FILL_FLAG, "Y"), (Tag.NEW_SEQ_NO, new_seq)],
                        seq=begin, poss_dup=True)

    async def reject(self, ref_seq: int, ref_tag: int | None, reason: int, text: str) -> None:
        fields = [(Tag.REF_SEQ_NUM, ref_seq)]
        if ref_tag is not None:
            fields.append((Tag.REF_TAG_ID, ref_tag))
        await self.send(MsgType.REJECT, fields + [(Tag.SESSION_REJECT_REASON, reason), (Tag.TEXT, text)])

    async def logout(self, text: str) -> None:
        await self.send(MsgType.LOGOUT, [(Tag.TEXT, text)])

    async def send(self, msg_type: str, fields: list[tuple[int, object]], seq: int | None = None,
                   poss_dup: bool = False) -> None:
        now = utc_timestamp()
        header = [
            (Tag.MSG_TYPE, msg_type),
            (Tag.SENDER_COMP_ID, self.sender_comp_id),
            (Tag.TARGET_COMP_ID, self.target_comp_id),
            (Tag.MSG_SEQ_NUM, seq if seq is not None else self.next_out),
        ]
        if poss_dup:
            header.append((Tag.POSS_DUP_FLAG, "Y"))
        header.append((Tag.SENDING_TIME, now))
        if poss_dup:
            header.append((Tag.ORIG_SENDING_TIME, now))
        if seq is None:
            self.next_out += 1
        if self.begin_string == fix42.BEGIN_STRING:
            fields = fix42.outbound(msg_type, fields)
        self._writer.write(encode(header + fields, self.begin_string or BEGIN_STRING))
        await self._writer.drain()
