"""FIX 4.4 tag=value codec: encoding, validation and stream framing."""

from __future__ import annotations

from dataclasses import dataclass, field

SOH = "\x01"
BEGIN_STRING = "FIX.4.4"


class Tag:
    BEGIN_STRING = 8
    BODY_LENGTH = 9
    MSG_TYPE = 35
    SENDER_COMP_ID = 49
    TARGET_COMP_ID = 56
    MSG_SEQ_NUM = 34
    SENDING_TIME = 52
    CHECKSUM = 10
    AVG_PX = 6
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
    LOGOUT = "5"
    EXECUTION_REPORT = "8"
    ORDER_CANCEL_REJECT = "9"
    LOGON = "A"
    NEW_ORDER_SINGLE = "D"
    ORDER_CANCEL_REQUEST = "F"
    ORDER_CANCEL_REPLACE_REQUEST = "G"


class FixError(ValueError):
    pass


@dataclass
class Message:
    fields: list[tuple[int, str]] = field(default_factory=list)

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
        return f"{value:.4f}".rstrip("0").rstrip(".") or "0"
    return str(value)


def encode(body: list[tuple[int, object]]) -> bytes:
    """Wrap body fields (starting with 35=MsgType) with BeginString, BodyLength and CheckSum."""
    if not body or body[0][0] != Tag.MSG_TYPE:
        raise FixError("body must start with tag 35 (MsgType)")
    body_str = "".join(f"{t}={_fmt(v)}{SOH}" for t, v in body)
    head = f"{Tag.BEGIN_STRING}={BEGIN_STRING}{SOH}{Tag.BODY_LENGTH}={len(body_str.encode())}{SOH}"
    raw = (head + body_str).encode()
    return raw + f"{Tag.CHECKSUM}={sum(raw) % 256:03d}{SOH}".encode()


def decode(raw: bytes) -> Message:
    """Parse and validate one complete message; raises FixError on any framing or integrity fault."""
    text = raw.decode("ascii", errors="strict")
    if not text.endswith(SOH):
        raise FixError("message must end with SOH")
    pairs = []
    for part in text[:-1].split(SOH):
        tag, sep, value = part.partition("=")
        if not sep or not tag.isdigit() or value == "":
            raise FixError(f"malformed field {part!r}")
        pairs.append((int(tag), value))
    tags = [t for t, _ in pairs]
    if tags[:3] != [Tag.BEGIN_STRING, Tag.BODY_LENGTH, Tag.MSG_TYPE] or tags[-1] != Tag.CHECKSUM:
        raise FixError("header must be 8, 9, 35 and trailer must be 10")
    if pairs[0][1] != BEGIN_STRING:
        raise FixError(f"unsupported BeginString {pairs[0][1]}")
    checksum_at = text.rfind(f"{SOH}{Tag.CHECKSUM}=") + 1
    body_start = text.index(SOH, text.index(SOH) + 1) + 1
    body_len = len(text[body_start:checksum_at].encode())
    if body_len != int(pairs[1][1]):
        raise FixError(f"BodyLength {pairs[1][1]} does not match actual {body_len}")
    expected = sum(text[:checksum_at].encode()) % 256
    if f"{expected:03d}" != pairs[-1][1]:
        raise FixError(f"CheckSum {pairs[-1][1]} does not match computed {expected:03d}")
    return Message(pairs)


def split_frames(buffer: bytes) -> tuple[list[bytes], bytes]:
    """Cut complete messages off a TCP byte stream; returns (messages, unconsumed remainder)."""
    frames = []
    marker = f"{SOH}{Tag.CHECKSUM}=".encode()
    while True:
        start = buffer.find(f"{Tag.BEGIN_STRING}=".encode())
        if start < 0:
            return frames, b""
        buffer = buffer[start:]
        trailer = buffer.find(marker)
        if trailer < 0:
            return frames, buffer
        end = buffer.find(SOH.encode(), trailer + len(marker))
        if end < 0:
            return frames, buffer
        frames.append(buffer[: end + 1])
        buffer = buffer[end + 1 :]


def pretty(raw: bytes) -> str:
    return raw.decode("ascii", errors="replace").replace(SOH, "|")
