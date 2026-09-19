import asyncio

import pytest

from fixsim.client import FixClient
from fixsim.fix import MsgType, Tag, encode
from fixsim.server import start
from fixsim.session import utc_timestamp


def run(coro):
    return asyncio.run(coro)


async def connected():
    server, engine = await start(port=0, tick_seconds=0.05)
    port = server.sockets[0].getsockname()[1]
    client = await FixClient.connect("127.0.0.1", port)
    return server, engine, client


async def teardown(server, client):
    client.close()
    server.ticker.cancel()
    server.close()
    await server.wait_closed()


def test_logon_then_order_lifecycle_over_tcp():
    async def scenario():
        server, _, client = await connected()
        logon = await client.logon()
        assert logon.msg_type == MsgType.LOGON and logon.get(Tag.MSG_SEQ_NUM) == "1"
        await client.send(MsgType.NEW_ORDER_SINGLE, [
            (Tag.CL_ORD_ID, "A1"), (Tag.SYMBOL, "AAPL"), (Tag.SIDE, "1"), (Tag.ORDER_QTY, 250),
            (Tag.ORD_TYPE, "2"), (Tag.PRICE, 190.00), (Tag.TIME_IN_FORCE, "0")])
        got = await client.receive_until(lambda m: m.get(Tag.ORD_STATUS) == "2")
        await teardown(server, client)
        return got
    got = run(scenario())
    assert [m.get(Tag.EXEC_TYPE) for m in got] == ["0", "F", "F"]
    assert [int(m.get(Tag.MSG_SEQ_NUM)) for m in got] == [2, 3, 4]


def test_test_request_is_answered_with_heartbeat_echoing_the_id():
    async def scenario():
        server, _, client = await connected()
        await client.logon()
        await client.send(MsgType.TEST_REQUEST, [(Tag.TEST_REQ_ID, "PING-7")])
        reply = await client.receive()
        await teardown(server, client)
        return reply
    reply = run(scenario())
    assert reply.msg_type == MsgType.HEARTBEAT and reply.get(Tag.TEST_REQ_ID) == "PING-7"


def test_sequence_number_too_low_ends_the_session_with_logout():
    async def scenario():
        server, _, client = await connected()
        await client.logon()
        await client.send(MsgType.HEARTBEAT, [], seq=1)
        reply = await client.receive()
        await teardown(server, client)
        return reply
    reply = run(scenario())
    assert reply.msg_type == MsgType.LOGOUT and "too low" in reply.get(Tag.TEXT)


def test_sequence_gap_triggers_resend_request():
    async def scenario():
        server, _, client = await connected()
        await client.logon()
        await client.send(MsgType.HEARTBEAT, [], seq=5)
        reply = await client.receive()
        await teardown(server, client)
        return reply
    reply = run(scenario())
    assert reply.msg_type == MsgType.RESEND_REQUEST and reply.get(Tag.BEGIN_SEQ_NO) == "2"


def test_twap_slices_arrive_over_tcp_from_the_scheduler():
    async def scenario():
        server, engine, client = await connected()
        await client.logon()
        await client.send(MsgType.NEW_ORDER_SINGLE, [
            (Tag.CL_ORD_ID, "T1"), (Tag.SYMBOL, "MSFT"), (Tag.SIDE, "1"), (Tag.ORDER_QTY, 150),
            (Tag.ORD_TYPE, "2"), (Tag.PRICE, 410.15), (Tag.TARGET_STRATEGY, 1000),
            (Tag.TARGET_STRATEGY_PARAMETERS, "slices=3;interval=0.1")])
        got = await client.receive_until(lambda m: m.get(Tag.ORD_STATUS) == "2", timeout=2.0)
        await teardown(server, client)
        return got
    got = run(scenario())
    assert got[-1].get(Tag.CUM_QTY) == "150"
    assert sum(int(m.get(Tag.LAST_QTY)) for m in got if m.get(Tag.EXEC_TYPE) == "F") == 150


def resent(fields):
    return [(Tag.POSS_DUP_FLAG, "Y"), (Tag.ORIG_SENDING_TIME, utc_timestamp()), *fields]


def aapl_buy(cl, qty=10, tif="0"):
    return [(Tag.CL_ORD_ID, cl), (Tag.SYMBOL, "AAPL"), (Tag.SIDE, "1"), (Tag.ORDER_QTY, qty),
            (Tag.ORD_TYPE, "2"), (Tag.PRICE, 190.00), (Tag.TIME_IN_FORCE, tif)]


def exchange(steps):
    """Log on, run steps(client) against a live acceptor, and return what it collected."""
    async def scenario():
        server, engine, client = await connected()
        await client.logon()
        try:
            return await steps(client, engine)
        finally:
            await teardown(server, client)
    return run(scenario())


def test_a_message_before_logon_closes_the_connection_without_reply():
    async def scenario():
        server, _, client = await connected()
        await client.send(MsgType.HEARTBEAT, [])
        reply = await client.receive(timeout=0.5)
        await teardown(server, client)
        return reply, client.eof
    assert run(scenario()) == (None, True)


@pytest.mark.parametrize("target, extra", [
    ("SOMEONE_ELSE", [(Tag.HEART_BT_INT, 30)]),
    ("SIMROUTER", []),
])
def test_logon_with_wrong_target_or_no_heartbeat_interval_is_closed_without_reply(target, extra):
    async def scenario():
        server, _, client = await connected()
        client.writer.write(encode([(Tag.MSG_TYPE, MsgType.LOGON), (Tag.SENDER_COMP_ID, "CLIENT1"),
                                    (Tag.TARGET_COMP_ID, target), (Tag.MSG_SEQ_NUM, 1),
                                    (Tag.SENDING_TIME, utc_timestamp()), (Tag.ENCRYPT_METHOD, 0), *extra]))
        reply = await client.receive(timeout=0.5)
        await teardown(server, client)
        return reply, client.eof
    assert run(scenario()) == (None, True)


def test_logon_must_start_at_sequence_one():
    async def scenario():
        server, _, client = await connected()
        await client.send(MsgType.LOGON, [(Tag.ENCRYPT_METHOD, 0), (Tag.HEART_BT_INT, 30)], seq=5)
        reply = await client.receive()
        await teardown(server, client)
        return reply
    reply = run(scenario())
    assert reply.msg_type == MsgType.LOGOUT and "Expected MsgSeqNum 1" in reply.get(Tag.TEXT)


def test_gap_is_recovered_by_gap_fill_and_resend_and_the_discarded_order_then_executes():
    async def steps(client, engine):
        await client.send(MsgType.HEARTBEAT, [], seq=3)
        resend = await client.receive()
        await client.send(MsgType.NEW_ORDER_SINGLE, aapl_buy("G1"), seq=4)
        held = await client.receive(timeout=0.3)
        orders_during_gap = len(engine.orders)
        await client.send(MsgType.SEQUENCE_RESET, resent([(Tag.GAP_FILL_FLAG, "Y"), (Tag.NEW_SEQ_NO, 4)]), seq=2)
        await client.send(MsgType.NEW_ORDER_SINGLE, resent(aapl_buy("G1")), seq=4)
        client.seq = 5
        await client.send(MsgType.TEST_REQUEST, [(Tag.TEST_REQ_ID, "AFTER-GAP")])
        after = await client.receive_until(lambda m: m.msg_type == MsgType.HEARTBEAT)
        return resend, held, orders_during_gap, after
    resend, held, orders_during_gap, after = exchange(steps)
    assert (resend.msg_type, resend.get(Tag.BEGIN_SEQ_NO), resend.get(Tag.END_SEQ_NO)) == (MsgType.RESEND_REQUEST, "2", "0")
    assert held is None and orders_during_gap == 0
    assert [(m.msg_type, m.get(Tag.EXEC_TYPE)) for m in after] == [("8", "0"), ("8", "F"), ("0", None)]
    assert after[-1].get(Tag.TEST_REQ_ID) == "AFTER-GAP"


def test_sequence_reset_in_reset_mode_clears_a_pending_resend():
    async def steps(client, engine):
        await client.send(MsgType.HEARTBEAT, [], seq=5)
        first = await client.receive()
        await client.send(MsgType.SEQUENCE_RESET, [(Tag.NEW_SEQ_NO, 10)], seq=6)
        await client.send(MsgType.HEARTBEAT, [], seq=12)
        return first, await client.receive()
    first, second = exchange(steps)
    assert first.get(Tag.BEGIN_SEQ_NO) == "2"
    assert second.msg_type == MsgType.RESEND_REQUEST and second.get(Tag.BEGIN_SEQ_NO) == "10"


def test_rejected_gap_fill_still_uses_up_its_sequence_number():
    async def steps(client, engine):
        await client.send(MsgType.SEQUENCE_RESET, resent([(Tag.GAP_FILL_FLAG, "Y"), (Tag.NEW_SEQ_NO, 1)]), seq=2)
        reject = await client.receive()
        await client.send(MsgType.TEST_REQUEST, [(Tag.TEST_REQ_ID, "NEXT")], seq=3)
        return reject, await client.receive()
    reject, reply = exchange(steps)
    assert (reject.msg_type, reject.get(Tag.REF_TAG_ID), reject.get(Tag.SESSION_REJECT_REASON)) == ("3", "36", "5")
    assert reply.msg_type == MsgType.HEARTBEAT


def test_resent_message_without_orig_sending_time_is_rejected():
    async def steps(client, engine):
        await client.send(MsgType.HEARTBEAT, [(Tag.POSS_DUP_FLAG, "Y")])
        return await client.receive()
    reply = exchange(steps)
    assert (reply.msg_type, reply.get(Tag.REF_TAG_ID), reply.get(Tag.SESSION_REJECT_REASON)) == ("3", "122", "1")


def test_out_of_order_resend_request_is_answered_before_asking_for_the_gap():
    async def steps(client, engine):
        await client.send(MsgType.TEST_REQUEST, [(Tag.TEST_REQ_ID, "T")])
        await client.receive()
        await client.send(MsgType.RESEND_REQUEST, [(Tag.BEGIN_SEQ_NO, 2), (Tag.END_SEQ_NO, 0)], seq=4)
        return await client.receive(), await client.receive()
    gap_fill, resend = exchange(steps)
    assert (gap_fill.msg_type, gap_fill.get(Tag.MSG_SEQ_NUM), gap_fill.get(Tag.NEW_SEQ_NO)) == ("4", "2", "3")
    assert gap_fill.get(Tag.POSS_DUP_FLAG) == "Y" and gap_fill.has(Tag.ORIG_SENDING_TIME)
    assert (resend.msg_type, resend.get(Tag.MSG_SEQ_NUM), resend.get(Tag.BEGIN_SEQ_NO)) == ("2", "3", "3")


def test_resend_request_gap_fills_only_the_requested_range():
    async def steps(client, engine):
        for _ in range(2):
            await client.send(MsgType.TEST_REQUEST, [(Tag.TEST_REQ_ID, "T")])
            await client.receive()
        await client.send(MsgType.RESEND_REQUEST, [(Tag.BEGIN_SEQ_NO, 2), (Tag.END_SEQ_NO, 2)])
        return await client.receive()
    reply = exchange(steps)
    assert (reply.get(Tag.MSG_SEQ_NUM), reply.get(Tag.NEW_SEQ_NO)) == ("2", "3")


@pytest.mark.parametrize("begin, reason", [("abc", "6"), ("0", "5"), ("9", "5")])
def test_resend_request_with_a_bad_range_is_rejected_and_the_session_continues(begin, reason):
    async def steps(client, engine):
        await client.send(MsgType.RESEND_REQUEST, [(Tag.BEGIN_SEQ_NO, begin), (Tag.END_SEQ_NO, 0)])
        reject = await client.receive()
        await client.send(MsgType.TEST_REQUEST, [(Tag.TEST_REQ_ID, "UP")])
        return reject, await client.receive()
    reject, reply = exchange(steps)
    assert (reject.msg_type, reject.get(Tag.REF_TAG_ID), reject.get(Tag.SESSION_REJECT_REASON)) == ("3", "7", reason)
    assert reply.msg_type == MsgType.HEARTBEAT


def test_possible_duplicate_below_expected_sequence_is_ignored():
    async def steps(client, engine):
        await client.send(MsgType.HEARTBEAT, [(Tag.POSS_DUP_FLAG, "Y")], seq=1)
        await client.send(MsgType.TEST_REQUEST, [(Tag.TEST_REQ_ID, "STILL-UP")])
        return await client.receive()
    reply = exchange(steps)
    assert reply.msg_type == MsgType.HEARTBEAT and reply.get(Tag.TEST_REQ_ID) == "STILL-UP"


def test_resend_request_from_client_is_answered_with_sequence_reset_gap_fill():
    async def steps(client, engine):
        await client.send(MsgType.RESEND_REQUEST, [(Tag.BEGIN_SEQ_NO, 1), (Tag.END_SEQ_NO, 0)])
        return await client.receive()
    reply = exchange(steps)
    assert reply.msg_type == MsgType.SEQUENCE_RESET and reply.has(Tag.ORIG_SENDING_TIME)
    assert (reply.get(Tag.MSG_SEQ_NUM), reply.get(Tag.POSS_DUP_FLAG), reply.get(Tag.GAP_FILL_FLAG), reply.get(Tag.NEW_SEQ_NO)) \
        == ("1", "Y", "Y", "2")


def test_test_request_without_id_is_rejected():
    async def steps(client, engine):
        await client.send(MsgType.TEST_REQUEST, [])
        return await client.receive()
    reply = exchange(steps)
    assert reply.msg_type == MsgType.REJECT and reply.get(Tag.REF_TAG_ID) == str(Tag.TEST_REQ_ID)


def test_unsupported_message_type_gets_business_message_reject():
    async def steps(client, engine):
        await client.send("V", [(262, "MD1")])
        return await client.receive()
    reply = exchange(steps)
    assert reply.msg_type == MsgType.BUSINESS_MESSAGE_REJECT
    assert (reply.get(Tag.REF_MSG_TYPE), reply.get(Tag.BUSINESS_REJECT_REASON)) == ("V", "3")


def test_undefined_message_type_is_a_session_reject_and_an_inbound_business_reject_is_not_answered():
    async def steps(client, engine):
        await client.send("ZZ", [])
        reject = await client.receive()
        await client.send(MsgType.BUSINESS_MESSAGE_REJECT, [(Tag.REF_SEQ_NUM, 2), (Tag.REF_MSG_TYPE, "8"),
                                                            (Tag.BUSINESS_REJECT_REASON, 3)])
        await client.send(MsgType.TEST_REQUEST, [(Tag.TEST_REQ_ID, "AFTER-J")])
        return reject, await client.receive()
    reject, reply = exchange(steps)
    assert (reject.msg_type, reject.get(Tag.REF_TAG_ID), reject.get(Tag.SESSION_REJECT_REASON)) == ("3", "35", "11")
    assert reply.msg_type == MsgType.HEARTBEAT and reply.get(Tag.TEST_REQ_ID) == "AFTER-J"


def test_garbled_message_is_ignored_and_empty_field_is_rejected_without_ending_the_session():
    from fixsim.fix import SOH
    async def steps(client, engine):
        client.writer.write(b"8=FIX.4.4\x019=5\x0135=0\x0158=caf\xc3\xa9\x0110=000\x01")
        body = f"35=0{SOH}49=CLIENT1{SOH}56=SIMROUTER{SOH}34={client.seq}{SOH}58={SOH}"
        raw = f"8=FIX.4.4{SOH}9={len(body)}{SOH}{body}".encode()
        client.writer.write(raw + f"10={sum(raw) % 256:03d}{SOH}".encode())
        client.seq += 1
        reject = await client.receive()
        await client.send(MsgType.TEST_REQUEST, [(Tag.TEST_REQ_ID, "OK")])
        return reject, await client.receive()
    reject, reply = exchange(steps)
    assert reject.msg_type == MsgType.REJECT
    assert (reject.get(Tag.REF_TAG_ID), reject.get(Tag.SESSION_REJECT_REASON)) == ("58", "4")
    assert reply.msg_type == MsgType.HEARTBEAT and reply.get(Tag.TEST_REQ_ID) == "OK"


def test_comp_id_mismatch_is_rejected_and_logged_out():
    async def steps(client, engine):
        client.sender = "IMPOSTER"
        await client.send(MsgType.HEARTBEAT, [])
        return await client.receive(), await client.receive()
    reject, logout = exchange(steps)
    assert reject.msg_type == MsgType.REJECT and reject.get(Tag.SESSION_REJECT_REASON) == "9"
    assert logout.msg_type == MsgType.LOGOUT


def test_twap_scheduler_survives_a_client_disconnecting_mid_schedule():
    async def scenario():
        server, engine, first = await connected()
        port = server.sockets[0].getsockname()[1]
        await first.logon()
        await first.send(MsgType.NEW_ORDER_SINGLE, [
            (Tag.CL_ORD_ID, "T1"), (Tag.SYMBOL, "MSFT"), (Tag.SIDE, "1"), (Tag.ORDER_QTY, 60), (Tag.ORD_TYPE, "2"),
            (Tag.PRICE, 410.15), (Tag.TARGET_STRATEGY, 1000), (Tag.TARGET_STRATEGY_PARAMETERS, "slices=3;interval=0.1")])
        await first.receive()
        first.close()
        await asyncio.sleep(0.4)
        second = await FixClient.connect("127.0.0.1", port, sender="CLIENT2")
        await second.logon()
        await second.send(MsgType.NEW_ORDER_SINGLE, [
            (Tag.CL_ORD_ID, "T2"), (Tag.SYMBOL, "MSFT"), (Tag.SIDE, "1"), (Tag.ORDER_QTY, 30), (Tag.ORD_TYPE, "2"),
            (Tag.PRICE, 410.15), (Tag.TARGET_STRATEGY, 1000), (Tag.TARGET_STRATEGY_PARAMETERS, "slices=3;interval=0.1")])
        got = await second.receive_until(lambda m: m.get(Tag.ORD_STATUS) == "2", timeout=2.0)
        t1 = engine.by_cl_ord_id["CLIENT1", "T1"]
        await teardown(server, second)
        return got, t1
    got, t1 = run(scenario())
    assert got and got[-1].get(Tag.ORD_STATUS) == "2" and got[-1].get(Tag.CUM_QTY) == "30"
    assert (t1.status, t1.cum_qty) == ("2", 60)
    assert {m.get(Tag.CL_ORD_ID) for m in got} == {"T2"}


def test_reports_go_only_to_the_client_that_owns_the_order():
    async def scenario():
        server, _, first = await connected()
        port = server.sockets[0].getsockname()[1]
        await first.logon()
        await first.send(MsgType.NEW_ORDER_SINGLE, aapl_buy("B1", qty=400))
        await first.receive_until(lambda m: m.get(Tag.CUM_QTY) == "300")
        second = await FixClient.connect("127.0.0.1", port, sender="CLIENT2")
        await second.logon()
        await second.send(MsgType.NEW_ORDER_SINGLE, [
            (Tag.CL_ORD_ID, "S1"), (Tag.SYMBOL, "AAPL"), (Tag.SIDE, "2"), (Tag.ORDER_QTY, 100),
            (Tag.ORD_TYPE, "2"), (Tag.PRICE, 190.00), (Tag.TIME_IN_FORCE, "3")])
        theirs = await second.receive_until(lambda m: m.get(Tag.ORD_STATUS) in ("2", "4"))
        mine = await first.receive()
        second.close()
        await teardown(server, first)
        return theirs, mine
    theirs, mine = run(scenario())
    assert {m.get(Tag.CL_ORD_ID) for m in theirs} == {"S1"} and theirs[-1].get(Tag.ORD_STATUS) == "2"
    assert (mine.get(Tag.CL_ORD_ID), mine.get(Tag.ORD_STATUS), mine.get(Tag.LAST_LIQUIDITY_IND)) == ("B1", "2", "1")


def test_a_second_connection_cannot_take_over_a_live_session():
    async def scenario():
        server, _, first = await connected()
        port = server.sockets[0].getsockname()[1]
        await first.logon()
        _, probe = await asyncio.open_connection("127.0.0.1", port)
        twin = await FixClient.connect("127.0.0.1", port, sender="CLIENT1")
        twin_logon = await twin.logon()
        await first.send(MsgType.NEW_ORDER_SINGLE, [
            (Tag.CL_ORD_ID, "T1"), (Tag.SYMBOL, "MSFT"), (Tag.SIDE, "1"), (Tag.ORDER_QTY, 60), (Tag.ORD_TYPE, "2"),
            (Tag.PRICE, 410.15), (Tag.TARGET_STRATEGY, 1000), (Tag.TARGET_STRATEGY_PARAMETERS, "slices=3;interval=0.1")])
        got = await first.receive_until(lambda m: m.get(Tag.ORD_STATUS) == "2")
        probe.close()
        twin.close()
        await teardown(server, first)
        return twin_logon, twin.eof, got
    twin_logon, twin_closed, got = run(scenario())
    assert twin_logon is None and twin_closed
    assert got[-1].get(Tag.CUM_QTY) == "60"
