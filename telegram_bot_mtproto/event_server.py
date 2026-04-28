"""
aiohttp ``POST /event`` receiver for the MTProto bot variant.

Reuses ``telegram_bot.messages.build_message`` so alert text is identical
to the PTB variant; reuses ``telegram_bot.subscriptions`` so a single
user list applies regardless of which impl is running.
"""
from __future__ import annotations

import json
import logging

from aiohttp import web

from telegram_bot import subscriptions
from telegram_bot.messages import build_message

from .config import BROADCAST_CHANNEL_ID

logger = logging.getLogger(__name__)


async def _send(client, chat_id: int | str, text: str) -> None:
    """Send a Telethon HTML message; tolerate transient API hiccups."""
    try:
        await client.send_message(int(chat_id), text, parse_mode="html")
    except Exception as exc:
        logger.error("send_message to %s failed: %s", chat_id, exc)


def make_event_handler(client):
    async def handle_event(request: web.Request) -> web.Response:
        try:
            body = await request.read()
            event = json.loads(body)
        except Exception as exc:
            logger.warning("Bad event payload: %s", exc)
            return web.Response(status=400, text="bad json")

        flag_name, message = build_message(event)
        event_address = (event.get("address") or "").lower()

        # Broadcast to channel regardless of per-user subscriptions.
        if BROADCAST_CHANNEL_ID:
            try:
                await _send(client, int(BROADCAST_CHANNEL_ID), message)
            except Exception as exc:
                logger.error("Broadcast failed: %s", exc)

        if not flag_name or not event_address:
            return web.Response(text="ok")

        for chat_id, sub in subscriptions.all_subscriptions():
            sub_addr = (sub.get("addr") or "").lower()
            if sub_addr != event_address:
                continue
            if not sub.get(flag_name):
                continue
            await _send(client, chat_id, message)

        return web.Response(text="ok")

    return handle_event


def build_app(client) -> web.Application:
    app = web.Application()
    app.router.add_post("/event", make_event_handler(client))
    return app
