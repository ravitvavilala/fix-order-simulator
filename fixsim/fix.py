"""FIX 4.2 / 4.4 tag=value codec: encoding, validation and stream framing."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SOH = "\x01"
BEGIN_STRING = "FIX.4.4"
SUPPORTED_BEGIN_STRINGS = ("FIX.4.2", "FIX.4.4")


class Tag:
    BEGIN_STRING = 8
    BODY_LENGTH = 9
    MSG_TYPE = 35
    SENDER_COMP_ID = 49
    TARGET_COMP_ID = 56
    MSG_SEQ_NUM = 34
    NEW_SEQ_NO = 36
    POSS_DUP_FLAG = 43
    SENDING_TIME = 52
    TRANSACT_TIME = 60
    ORIG_SENDING_TIME = 122
    GAP_FILL_FLAG = 123
    REF_MSG_TYPE = 372
    BUSINESS_REJECT_REASON = 380
    CHECKSUM = 10
    AVG_PX = 6
    EXEC_TRANS_TYPE = 20
    HANDL_INST = 21
    BEGIN_SEQ_NO = 7
    CL_ORD_ID = 11
    CUM_QTY = 14
    END_SEQ_NO = 16
    EXEC_ID = 17
    LAST_MKT = 30
    LAST_PX = 31
    LAST_QTY = 32
    ORDER_ID = 37
    ORDER_QTY = 38
    ORD_STATUS = 39
    ORD_TYPE = 40
    ORIG_CL_ORD_ID = 41
    PRICE = 44
    REF_SEQ_NUM = 45
    SIDE = 54
    SYMBOL = 55
    TEXT = 58
    TIME_IN_FORCE = 59
    ENCRYPT_METHOD = 98
    CXL_REJ_REASON = 102
    ORD_REJ_REASON = 103
    HEART_BT_INT = 108
    TEST_REQ_ID = 112
    EXEC_TYPE = 150
    LEAVES_QTY = 151
    SECURITY_TYPE = 167
    MATURITY_MONTH_YEAR = 200
    PUT_OR_CALL = 201
    STRIKE_PRICE = 202
    REF_TAG_ID = 371
    SESSION_REJECT_REASON = 373
    CXL_REJ_RESPONSE_TO = 434
    TARGET_STRATEGY = 847
    TARGET_STRATEGY_PARAMETERS = 848
    LAST_LIQUIDITY_IND = 851


class MsgType:
    HEARTBEAT = "0"
    TEST_REQUEST = "1"
    RESEND_REQUEST = "2"
    REJECT = "3"
    SEQUENCE_RESET = "4"
    LOGOUT = "5"
    EXECUTION_REPORT = "8"
    ORDER_CANCEL_REJECT = "9"
    LOGON = "A"
    NEW_ORDER_SINGLE = "D"
    ORDER_CANCEL_REQUEST = "F"
    ORDER_CANCEL_REPLACE_REQUEST = "G"
    BUSINESS_MESSAGE_REJECT = "j"

    DEFINED = (set("0123456789ABCDEFGHJKLMNPQRSTVWXYZabcdefghijklmnopqrstuvwxyz")
               | {f"A{c}" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"} | {f"B{c}" for c in "ABCDEFGH"})

    @classmethod
    def is_defined(cls, value: str) -> bool:
        """FIX 4.4 message types; values starting with U are reserved for user-defined messages."""
        return value in cls.DEFINED or value.startswith("U")


class RejectReason:
    INVALID_TAG_NUMBER = 0
    REQUIRED_TAG_MISSING = 1
    TAG_WITHOUT_VALUE = 4
    VALUE_INCORRECT = 5
    INCORRECT_DATA_FORMAT = 6
    COMP_ID_PROBLEM = 9
    INVALID_MSG_TYPE = 11
    TAG_APPEARS_MORE_THAN_ONCE = 13


_KNOWN_TAGS = {v for k, v in vars(Tag).items() if not k.startswith("_")}
_HEADER = re.compile(rb"8=FIX[^\x01]*\x019=\d+\x0135=")


class FixError(ValueError):
    """A garbled message: framing, BodyLength or CheckSum is wrong. The spec says ignore it."""


class BeginStringError(FixError):
    """Wrong protocol version. The spec says end the session."""


class FieldError(FixError):
    """The message is intact but a field is invalid. The spec says send a session Reject (35=3)."""

    def __init__(self, text: str, ref_tag: int | None, reason: int):
        super().__init__(text)
        self.ref_tag, self.reason = ref_tag, reason


@dataclass
class Message:
    fields: list[tuple[int, str]] = field(default_factory=list)
    error: FieldError | None = None

    @property
    def msg_type(self) -> str:
        return self.get(Tag.MSG_TYPE) or ""

    def get(self, tag: int, default: str | None = None) -> str | None:
        for t, v in self.fields:
            if t == tag:
                return v
        return default

    def has(self, tag: int) -> bool:
        return any(t == tag for t, _ in self.fields)

    def __str__(self) -> str:
        return "|".join(f"{t}={v}" for t, v in self.fields) + "|"


def _fmt(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.8f}".rstrip("0").rstrip(".") or "0"
    return str(value)


def encode(body: list[tuple[int, object]], begin_string: str = BEGIN_STRING) -> bytes:
    """Wrap body fields (starting with 35=MsgType) with BeginString, BodyLength and CheckSum."""
    if not body or body[0][0] != Tag.MSG_TYPE:
        raise FixError("body must start with tag 35 (MsgType)")
    empty = next((t for t, v in body if v is None or str(v) == ""), None)
    if empty is not None:
        raise FixError(f"tag {empty} has no value")
    body_str = "".join(f"{t}={_fmt(v)}{SOH}" for t, v in body)
    head = f"{Tag.BEGIN_STRING}={begin_string}{SOH}{Tag.BODY_LENGTH}={len(body_str.encode())}{SOH}"
    raw = (head + body_str).encode()
    return raw + f"{Tag.CHECKSUM}={sum(raw) % 256:03d}{SOH}".encode()


def decode(raw: bytes) -> Message:
    """Check integrity (garbled: FixError), then protocol version, then fields (first problem kept in .error)."""
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise FixError("message is not ASCII") from exc
    if not text.endswith(SOH):
        raise FixError("message must end with SOH")
    head = re.match(rf"8=([^{SOH}]*){SOH}9=(\d+){SOH}35=", text)
    trailer = re.search(rf"{SOH}10=(\d{{3}}){SOH}$", text)
    if not head or not trailer:
        raise FixError("header must start 8, 9, 35 and the trailer must be 10=nnn")
    checksum_at = trailer.start() + 1
    body_len = len(text[head.end(2) + 1:checksum_at].encode())
    if body_len != int(head.group(2)):
        raise FixError(f"BodyLength {head.group(2)} does not match actual {body_len}")
    expected = sum(text[:checksum_at].encode()) % 256
    if f"{expected:03d}" != trailer.group(1):
        raise FixError(f"CheckSum {trailer.group(1)} does not match computed {expected:03d}")
    if head.group(1) not in SUPPORTED_BEGIN_STRINGS:
        raise BeginStringError(f"unsupported BeginString {head.group(1)}")
    pairs: list[tuple[int, str]] = []
    error = None
    for part in text[:-1].split(SOH):
        tag, _, value = part.partition("=")
        if not tag.isdigit() or tag.startswith("0"):
            error = error or FieldError(f"invalid tag number {tag!r}", None, RejectReason.INVALID_TAG_NUMBER)
            continue
        number = int(tag)
        if number in _KNOWN_TAGS and any(t == number for t, _ in pairs):
            error = error or FieldError(f"tag {number} appears more than once", number,
                                        RejectReason.TAG_APPEARS_MORE_THAN_ONCE)
            continue
        if value == "":
            error = error or FieldError(f"tag {number} has no value", number, RejectReason.TAG_WITHOUT_VALUE)
        pairs.append((number, value))
    return Message(pairs, error)


def split_frames(buffer: bytes) -> tuple[list[bytes], bytes]:
    """Cut complete messages off a TCP byte stream; returns (messages, unconsumed remainder)."""
    frames = []
    marker = f"{SOH}{Tag.CHECKSUM}=".encode()
    while True:
        head = _HEADER.search(buffer)
        if head is None:
            partial = buffer.rfind(b"8=", max(0, len(buffer) - 64))
            return frames, buffer[partial:] if partial >= 0 else buffer[-1:] if buffer.endswith(b"8") else b""
        buffer = buffer[head.start():]
        trailer = buffer.find(marker)
        end = buffer.find(SOH.encode(), trailer + len(marker)) if trailer >= 0 else -1
        following = _HEADER.search(buffer, 1)
        if following and (end < 0 or following.start() < end):
            buffer = buffer[following.start():]
            continue
        if end < 0:
            return frames, buffer
        frames.append(buffer[: end + 1])
        buffer = buffer[end + 1 :]


def pretty(raw: bytes) -> str:
    return raw.decode("ascii", errors="replace").replace(SOH, "|")
