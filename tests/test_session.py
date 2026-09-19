import asyncio

from fixsim.client import FixClient
from fixsim.fix import MsgType, Tag
from fixsim.server import start


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
