"""Run the simulated router as a FIX 4.4 acceptor: python -m fixsim.server [--port 9878]"""

from __future__ import annotations

import argparse
import asyncio
import logging

from fixsim.engine import OrderManager
from fixsim.market import default_venues
from fixsim.router import SmartOrderRouter
from fixsim.session import FixSession


async def start(host: str = "127.0.0.1", port: int = 9878, tick_seconds: float = 0.5):
    """Start the acceptor; returns (server, engine). One client at a time, as in a single-desk simulator."""
    engine = OrderManager(SmartOrderRouter(default_venues()))
    current: dict[str, FixSession] = {}

    async def on_connect(reader, writer):
        session = FixSession(engine)
        current["session"] = session
        await session.run(reader, writer)

    async def ticker():
        while True:
            await asyncio.sleep(tick_seconds)
            session = current.get("session")
            outbound = engine.tick()
            if session and session.logged_on and outbound:
                await session.push(outbound)

    server = await asyncio.start_server(on_connect, host, port)
    server.ticker = asyncio.create_task(ticker())
    return server, engine


async def main(port: int) -> None:
    server, _ = await start(port=port)
    addr = server.sockets[0].getsockname()
    logging.info("FIX acceptor listening on %s:%s (SenderCompID=SIMROUTER)", *addr[:2])
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9878)
    asyncio.run(main(parser.parse_args().port))
