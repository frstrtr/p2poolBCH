"""
Entry point for the MTProto bot variant.

Boots a Telethon ``TelegramClient`` (logs in as a bot via the bot token,
no user account needed), registers conversation handlers, and runs an
aiohttp ``POST /event`` server in the same asyncio loop.

Run with:
    python -m telegram_bot_mtproto.bot

Required env vars: BOT_TOKEN, MTPROTO_API_ID, MTPROTO_API_HASH.
See telegram_bot_mtproto/config.py for the full reference.
"""
from __future__ import annotations

import asyncio
import logging
import signal

from aiohttp import web
from telethon import TelegramClient

from . import config
from .event_server import build_app
from .handlers import register_handlers

logging.basicConfig(
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def _run_aiohttp(app: web.Application, port: int) -> web.AppRunner:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    logger.info("Event receiver listening on http://127.0.0.1:%d/event", port)
    return runner


async def main() -> None:
    if not config.MTPROTO_API_ID or not config.MTPROTO_API_HASH:
        raise SystemExit(
            "MTProto bot requires MTPROTO_API_ID and MTPROTO_API_HASH "
            "in addition to BOT_TOKEN.  Get them at "
            "https://my.telegram.org/apps and add to your env file."
        )

    proxy = config.telethon_proxy()
    logger.info("Telethon proxy: %s", config.redact_proxy(proxy))

    client = TelegramClient(
        config.SESSION_FILE,
        config.MTPROTO_API_ID,
        config.MTPROTO_API_HASH,
        proxy=proxy,
        # Sensible defaults for a bot that may live behind a flaky proxy:
        connection_retries=5,
        retry_delay=2,
        request_retries=3,
        timeout=30,
    )

    # Telethon's bot login uses the bot token directly — no user account
    # involved; the session file mostly caches peer data after this.
    await client.start(bot_token=config.BOT_TOKEN)
    me = await client.get_me()
    logger.info("Logged in as @%s (bot_id=%s)", me.username, me.id)

    register_handlers(client)

    http_runner = await _run_aiohttp(build_app(client), config.LOCAL_EVENT_PORT)

    # Graceful shutdown: any of SIGINT/SIGTERM/client-disconnect ends main.
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _signal_handler():
        logger.info("Shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    client_task = asyncio.create_task(client.run_until_disconnected())
    stop_task = asyncio.create_task(stop_event.wait())
    done, pending = await asyncio.wait(
        {client_task, stop_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()

    await client.disconnect()
    await http_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
