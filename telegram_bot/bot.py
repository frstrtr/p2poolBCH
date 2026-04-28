"""
Entry point: starts the aiohttp event-receiver server and PTB polling loop
together in the same asyncio event loop.

Usage:
    python -m telegram_bot.bot

Or run directly:
    python telegram_bot/bot.py

Required environment variables (see config.py):
    BOT_TOKEN              Telegram bot token from @BotFather
    LOCAL_EVENT_PORT       Port the p2pool notifier POSTs to (default 19349)
    P2POOL_API_URL         Base URL of p2pool web API (default http://127.0.0.1:9348)
    SUBSCRIPTIONS_FILE     Path to JSON subscription store (optional)
    BROADCAST_CHANNEL_ID   Telegram channel ID for broadcast (optional)
"""
from __future__ import annotations

import asyncio
import logging
import signal

import os

from aiohttp import web
from telegram.ext import Application, PicklePersistence

from .config import (
    BOT_TOKEN,
    BOT_PROXY,
    BOT_PROXY_GET_UPDATES,
    LOCAL_EVENT_PORT,
    SUBSCRIPTIONS_FILE,
)
from .event_server import build_app
from .handlers import build_conversation_handler

logging.basicConfig(
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def _redact_proxy(url: str) -> str:
    """Return a proxy URL with the userinfo password redacted, safe to log."""
    try:
        from urllib.parse import urlsplit, urlunsplit
        s = urlsplit(url)
        if s.password:
            netloc = (s.username or "") + ":***@" + (s.hostname or "")
            if s.port:
                netloc += ":" + str(s.port)
            return urlunsplit((s.scheme, netloc, s.path, s.query, s.fragment))
    except Exception:
        pass
    return url


async def _run_aiohttp(app: web.Application, port: int) -> None:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    logger.info("Event receiver listening on http://127.0.0.1:%d/event", port)


async def main() -> None:
    # Persist conversation state across restarts (stored alongside subscriptions.json)
    _persistence_path = os.path.join(
        os.path.dirname(SUBSCRIPTIONS_FILE), "ptb_persistence"
    )
    persistence = PicklePersistence(filepath=_persistence_path)

    # Build PTB Application
    builder = Application.builder().token(BOT_TOKEN).persistence(persistence)
    if BOT_PROXY:
        # PTB v20.x: configure the proxy for both outbound API requests and
        # the long-poll getUpdates connection.  Accepts http://, https://,
        # socks5:// and socks5h:// URLs (SOCKS requires the [socks] extra).
        logger.info("Using outbound proxy for Telegram API: %s", _redact_proxy(BOT_PROXY))
        builder = builder.proxy_url(BOT_PROXY).get_updates_proxy_url(BOT_PROXY_GET_UPDATES or BOT_PROXY)
    ptb_app = builder.build()
    ptb_app.add_handler(build_conversation_handler())

    # Build aiohttp app (needs the bot object to send messages)
    http_app = build_app(ptb_app.bot)

    # Start event receiver
    await _run_aiohttp(http_app, LOCAL_EVENT_PORT)

    # Run PTB (initialise, start polling, idle until stopped)
    async with ptb_app:
        await ptb_app.start()
        await ptb_app.updater.start_polling(drop_pending_updates=True)

        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()

        def _signal_handler():
            logger.info("Shutdown signal received")
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)

        await stop_event.wait()
        await ptb_app.updater.stop()
        await ptb_app.stop()


if __name__ == "__main__":
    asyncio.run(main())
