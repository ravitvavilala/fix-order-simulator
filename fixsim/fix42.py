"""FIX 4.2 dialect. The router works in FIX 4.4 terms; this module translates at the session edge.

Differences handled (FIX 4.2 dictionary):
- HandlInst (21) is required on NewOrderSingle and OrderCancelReplaceRequest.
- ExecutionReport requires ExecTransType (20); this router only sends 0 (New).
- Fills are ExecType 1 (Partial fill) or 2 (Fill); FIX 4.4 merged them into F (Trade).
- OrdStatus 5 (Replaced) exists. By 4.2 precedence (Partially filled > Replaced > New), a replace
  confirmation carries 39=5 unless the order already has fills, in which case it stays 39=1.
- LastLiquidityInd (851) does not exist, so it is left off.
- SessionRejectReason 13 (tag appears more than once) does not exist, so that Reject omits 373.
- OrdRejReason (103) ends at 8 and CxlRejReason (102) at 3. The router's 4.4 codes 13/99 and 6/99
  go out as 103=0 / 102=2 (Broker option); Text (58) keeps the reason.
- Message types are checked against the 4.2 list (single characters up to m, plus U-prefixed).
- OrdStatus 5 is sent on the replace confirmation only; later reports carry the order's 4.4 status.
"""

from __future__ import annotations

from fixsim.fix import Message, MsgType, RejectReason, Tag
from fixsim.model import ExecType, OrdStatus

BEGIN_STRING = "FIX.4.2"
HANDL_INST_VALUES = ("1", "2", "3")
REPLACED = "5"
PARTIAL_FILL = "1"
FILL = "2"
EXEC_TRANS_NEW = "0"
NOT_IN_42 = {Tag.LAST_LIQUIDITY_IND}
DEFINED_REASONS = {Tag.ORD_REJ_REASON: range(9), Tag.CXL_REJ_REASON: range(4)}
BROKER_OPTION = {Tag.ORD_REJ_REASON: 0, Tag.CXL_REJ_REASON: 2}
MSG_TYPES = frozenset("0123456789ABCDEFGHJKLMNPQRSTVWXYZabcdefghijklm")


def defines(msg_type: str) -> bool:
    """FIX 4.2 message types; values starting with U are privately defined."""
    return msg_type in MSG_TYPES or msg_type.startswith("U")


def inbound_problem(msg: Message) -> tuple[int, int, str] | None:
    """Returns (RefTagID, SessionRejectReason, Text) when a 4.2 order message breaks a 4.2-only rule."""
    if msg.msg_type not in (MsgType.NEW_ORDER_SINGLE, MsgType.ORDER_CANCEL_REPLACE_REQUEST):
        return None
    value = msg.get(Tag.HANDL_INST)
    if value is None:
        return Tag.HANDL_INST, RejectReason.REQUIRED_TAG_MISSING, "HandlInst (21) is required in FIX 4.2"
    if value not in HANDL_INST_VALUES:
        return Tag.HANDL_INST, RejectReason.VALUE_INCORRECT, "HandlInst (21) must be 1, 2 or 3"
    return None


def outbound(msg_type: str, fields: list[tuple[int, object]]) -> list[tuple[int, object]]:
    """Rewrites one outbound message body from FIX 4.4 semantics to FIX 4.2."""
    fields = [(t, BROKER_OPTION[t] if t in DEFINED_REASONS and int(v) not in DEFINED_REASONS[t] else v)
              for t, v in fields]
    if msg_type == MsgType.REJECT:
        return [(t, v) for t, v in fields
                if not (t == Tag.SESSION_REJECT_REASON and v == RejectReason.TAG_APPEARS_MORE_THAN_ONCE)]
    if msg_type != MsgType.EXECUTION_REPORT:
        return fields
    values = dict(fields)
    status, exec_type = values[Tag.ORD_STATUS], values[Tag.EXEC_TYPE]
    if exec_type == ExecType.TRADE:
        exec_type = FILL if status == OrdStatus.FILLED else PARTIAL_FILL
    if exec_type == ExecType.REPLACED and status == OrdStatus.NEW:
        status = REPLACED
    out: list[tuple[int, object]] = []
    for tag, value in fields:
        if tag in NOT_IN_42:
            continue
        out.append((tag, exec_type if tag == Tag.EXEC_TYPE else status if tag == Tag.ORD_STATUS else value))
        if tag == Tag.EXEC_ID:
            out.append((Tag.EXEC_TRANS_TYPE, EXEC_TRANS_NEW))
    return out
