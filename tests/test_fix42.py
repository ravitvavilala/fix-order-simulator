import asyncio

import pytest

from fixsim import fix42
from fixsim.client import FixClient
from fixsim.engine import Outbound
from fixsim.fix import MsgType, RejectReason, Tag, encode
from fixsim.server import start
from fixsim.session import utc_timestamp

FIX42 = "FIX.4.2"


def exchange(steps, begin_string=FIX42):
    async def scenario():
        server, engine = await start(port=0, tick_seconds=0.05)
        port = server.sockets[0].getsockname()[1]
        client = await FixClient.connect("127.0.0.1", port, begin_string=begin_string)
        logon = await client.logon()
        try:
            return logon, await steps(client, engine)
        finally:
            client.close()
            server.ticker.cancel()
            server.close()
            await server.wait_closed()
    return asyncio.run(scenario())


def buy(cl, qty, price, tif="0"):
    return [(Tag.CL_ORD_ID, cl), (Tag.SYMBOL, "AAPL"), (Tag.SIDE, "1"), (Tag.ORDER_QTY, qty),
            (Tag.ORD_TYPE, "2"), (Tag.PRICE, price), (Tag.TIME_IN_FORCE, tif)]


def raw_order(client, fields):
    client.writer.write(encode([(Tag.MSG_TYPE, MsgType.NEW_ORDER_SINGLE), (Tag.SENDER_COMP_ID, client.sender),
                                (Tag.TARGET_COMP_ID, "SIMROUTER"), (Tag.MSG_SEQ_NUM, client.seq),
                                (Tag.SENDING_TIME, utc_timestamp()), (Tag.TRANSACT_TIME, utc_timestamp()), *fields],
                               FIX42))
    client.seq += 1


def test_logon_with_fix42_is_answered_in_fix42():
    logon, _ = exchange(lambda client, engine: asyncio.sleep(0))
    assert (logon.msg_type, logon.get(Tag.BEGIN_STRING)) == (MsgType.LOGON, FIX42)


def test_fills_are_reported_with_fix42_exec_types_and_exec_trans_type():
    async def steps(client, engine):
        await client.send(MsgType.NEW_ORDER_SINGLE, buy("A1", 250, 190.00))
        return await client.receive_until(lambda m: m.get(Tag.ORD_STATUS) == "2")
    _, reports = exchange(steps)
    assert [(m.get(Tag.EXEC_TYPE), m.get(Tag.ORD_STATUS)) for m in reports] == [("0", "0"), ("1", "1"), ("2", "2")]
    assert all(m.get(Tag.BEGIN_STRING) == FIX42 and m.get(Tag.EXEC_TRANS_TYPE) == "0" for m in reports)
    assert not any(m.has(Tag.LAST_LIQUIDITY_IND) for m in reports)


def test_replace_of_an_unfilled_order_reports_ord_status_replaced():
    async def steps(client, engine):
        await client.send(MsgType.NEW_ORDER_SINGLE, buy("R1", 100, 150.00))
        await client.receive_until(lambda m: m.get(Tag.EXEC_TYPE) == "0")
        await client.send(MsgType.ORDER_CANCEL_REPLACE_REQUEST, [(Tag.ORIG_CL_ORD_ID, "R1"), *buy("R2", 200, 150.00)])
        return await client.receive_until(lambda m: m.get(Tag.EXEC_TYPE) == "5")
    _, reports = exchange(steps)
    assert [(m.get(Tag.EXEC_TYPE), m.get(Tag.ORD_STATUS)) for m in reports] == [("E", "E"), ("5", "5")]


def test_replace_of_a_partly_filled_order_keeps_ord_status_partially_filled():
    async def steps(client, engine):
        await client.send(MsgType.NEW_ORDER_SINGLE, buy("P1", 400, 190.00))
        await client.receive_until(lambda m: m.get(Tag.CUM_QTY) == "300")
        await client.send(MsgType.ORDER_CANCEL_REPLACE_REQUEST, [(Tag.ORIG_CL_ORD_ID, "P1"), *buy("P2", 450, 190.00)])
        return await client.receive_until(lambda m: m.get(Tag.EXEC_TYPE) == "5")
    _, reports = exchange(steps)
    assert (reports[-1].get(Tag.EXEC_TYPE), reports[-1].get(Tag.ORD_STATUS)) == ("5", "1")


def test_order_without_handl_inst_is_rejected_on_fix42():
    async def steps(client, engine):
        raw_order(client, buy("H1", 10, 190.00))
        return await client.receive(), len(engine.orders)
    _, (reject, orders) = exchange(steps)
    assert (reject.msg_type, reject.get(Tag.REF_TAG_ID), reject.get(Tag.SESSION_REJECT_REASON)) == ("3", "21", "1")
    assert orders == 0


def test_handl_inst_outside_1_to_3_is_rejected_on_fix42():
    async def steps(client, engine):
        raw_order(client, [*buy("H2", 10, 190.00), (Tag.HANDL_INST, "9")])
        return await client.receive()
    _, reject = exchange(steps)
    assert (reject.get(Tag.REF_TAG_ID), reject.get(Tag.SESSION_REJECT_REASON)) == ("21", "5")


def test_fix44_session_does_not_require_handl_inst():
    async def steps(client, engine):
        await client.send(MsgType.NEW_ORDER_SINGLE, buy("N1", 10, 150.00))
        return await client.receive()
    _, report = exchange(steps, begin_string="FIX.4.4")
    assert (report.get(Tag.BEGIN_STRING), report.get(Tag.EXEC_TYPE)) == ("FIX.4.4", "0")
    assert not report.has(Tag.EXEC_TRANS_TYPE)


def test_a_message_in_another_fix_version_ends_the_session():
    async def steps(client, engine):
        client.begin_string = "FIX.4.4"
        await client.send(MsgType.HEARTBEAT, [])
        return await client.receive()
    _, logout = exchange(steps)
    assert logout.msg_type == MsgType.LOGOUT and "does not match" in logout.get(Tag.TEXT)


def test_fix42_translation_rules():
    report = Outbound(MsgType.EXECUTION_REPORT, [(Tag.ORDER_ID, "O1"), (Tag.EXEC_ID, "E1"), (Tag.EXEC_TYPE, "F"),
                                                 (Tag.ORD_STATUS, "1"), (Tag.LAST_LIQUIDITY_IND, 2)])
    assert fix42.outbound(report.msg_type, report.fields) == [
        (Tag.ORDER_ID, "O1"), (Tag.EXEC_ID, "E1"), (Tag.EXEC_TRANS_TYPE, "0"), (Tag.EXEC_TYPE, "1"), (Tag.ORD_STATUS, "1")]
    reject = [(Tag.REF_SEQ_NUM, 2), (Tag.REF_TAG_ID, 55), (Tag.SESSION_REJECT_REASON, RejectReason.TAG_APPEARS_MORE_THAN_ONCE)]
    assert fix42.outbound(MsgType.REJECT, reject) == reject[:2]


def test_reason_codes_fix42_does_not_define_go_out_as_broker_option():
    async def steps(client, engine):
        await client.send(MsgType.NEW_ORDER_SINGLE, buy("Q0", 0, 190.00))
        order_reject = await client.receive()
        await client.send(MsgType.NEW_ORDER_SINGLE, buy("L1", 10, 150.00))
        await client.receive()
        await client.send(MsgType.ORDER_CANCEL_REQUEST, [(Tag.CL_ORD_ID, "L1"), (Tag.ORIG_CL_ORD_ID, "L1"),
                                                          (Tag.SYMBOL, "AAPL"), (Tag.SIDE, "1")])
        return order_reject, await client.receive()
    _, (order_reject, cancel_reject) = exchange(steps)
    assert (order_reject.get(Tag.ORD_REJ_REASON), order_reject.get(Tag.TEXT)) == ("0", "OrderQty must be a positive whole number")
    assert (cancel_reject.msg_type, cancel_reject.get(Tag.CXL_REJ_REASON)) == (MsgType.ORDER_CANCEL_REJECT, "2")


def test_message_types_added_after_fix42_get_a_session_reject_on_fix42():
    async def steps(client, engine):
        await client.send("AE", [])
        return await client.receive()
    _, reject = exchange(steps)
    assert (reject.msg_type, reject.get(Tag.REF_TAG_ID), reject.get(Tag.SESSION_REJECT_REASON)) == ("3", "35", "11")


@pytest.mark.parametrize("begin_string", ["FIX.4.1", "FIX.4.3", "FIXT.1.1"])
def test_logon_with_an_unsupported_begin_string_is_closed_without_reply(begin_string):
    async def scenario():
        server, _ = await start(port=0, tick_seconds=0.05)
        port = server.sockets[0].getsockname()[1]
        client = await FixClient.connect("127.0.0.1", port, begin_string=begin_string)
        await client.send(MsgType.LOGON, [(Tag.ENCRYPT_METHOD, 0), (Tag.HEART_BT_INT, 30)])
        reply = await client.receive(timeout=0.5)
        client.close()
        server.ticker.cancel()
        server.close()
        await server.wait_closed()
        return reply, client.eof
    assert asyncio.run(scenario()) == (None, True)


def test_each_client_gets_its_own_fix_version_when_they_trade_with_each_other():
    async def scenario():
        server, _ = await start(port=0, tick_seconds=0.05)
        port = server.sockets[0].getsockname()[1]
        old = await FixClient.connect("127.0.0.1", port, sender="CLIENT42", begin_string=FIX42)
        new = await FixClient.connect("127.0.0.1", port, sender="CLIENT44", begin_string="FIX.4.4")
        await old.logon()
        await new.logon()
        await old.send(MsgType.NEW_ORDER_SINGLE, buy("B1", 400, 190.00))
        await old.receive_until(lambda m: m.get(Tag.CUM_QTY) == "300")
        await new.send(MsgType.NEW_ORDER_SINGLE, [(Tag.CL_ORD_ID, "S1"), (Tag.SYMBOL, "AAPL"), (Tag.SIDE, "2"),
                                                  (Tag.ORDER_QTY, 100), (Tag.ORD_TYPE, "2"), (Tag.PRICE, 190.00),
                                                  (Tag.TIME_IN_FORCE, "3")])
        seller = await new.receive_until(lambda m: m.get(Tag.ORD_STATUS) == "2")
        buyer = await old.receive()
        for c in (old, new):
            c.close()
        server.ticker.cancel()
        server.close()
        await server.wait_closed()
        return seller[-1], buyer
    seller, buyer = asyncio.run(scenario())
    assert (seller.get(Tag.BEGIN_STRING), seller.get(Tag.EXEC_TYPE), seller.get(Tag.LAST_LIQUIDITY_IND)) == ("FIX.4.4", "F", "2")
    assert (buyer.get(Tag.BEGIN_STRING), buyer.get(Tag.EXEC_TYPE), buyer.get(Tag.EXEC_TRANS_TYPE)) == (FIX42, "2", "0")
    assert not buyer.has(Tag.LAST_LIQUIDITY_IND)


def test_twap_slices_reach_a_fix42_session_in_fix42():
    async def steps(client, engine):
        await client.send(MsgType.NEW_ORDER_SINGLE, [
            (Tag.CL_ORD_ID, "T1"), (Tag.SYMBOL, "MSFT"), (Tag.SIDE, "1"), (Tag.ORDER_QTY, 60), (Tag.ORD_TYPE, "2"),
            (Tag.PRICE, 410.15), (Tag.TARGET_STRATEGY, 1000), (Tag.TARGET_STRATEGY_PARAMETERS, "slices=3;interval=0.1")])
        return await client.receive_until(lambda m: m.get(Tag.ORD_STATUS) == "2")
    _, reports = exchange(steps)
    assert [m.get(Tag.EXEC_TYPE) for m in reports] == ["0", "1", "1", "2"]
    assert all(m.get(Tag.BEGIN_STRING) == FIX42 for m in reports)
