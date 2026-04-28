"""
aiohttp server that receives POST /event from p2pool (PyPy2 side) and routes
alerts to subscribed Telegram users.

Event payload (JSON body):
    {"type": "worker_connected"|"worker_disconnected"|
              "worker_silent"|"worker_active_again"|
              "worker_flapping"|"worker_stable"|
              "share_found"|"block_found",
     "node": str, "username": str, "address": str,
     "ip": str (connect/disconnect),
     "idle_seconds": float (worker_silent),
     "flap_count": int, "window_seconds": int (worker_flapping),
     "hash": str (share/block), "dead": bool (share),
     "reward_sat": int (block), "symbol": str (block),
     "explorer_url": str (block, full URL or empty), "ts": float}

Subscription flag mapping: silent / flapping / stable / active_again ride on
the existing 'connect' and 'disconnect' user-facing flags so existing
subscribers receive them without re-opting in.  bad-news goes to disconnect,
good-news goes to connect.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from aiohttp import web

from . import subscriptions
from .messages import build_message as _build_message  # shared with telegram_bot_mtproto
from .notifier import broadcast_to_channel, send_alert

if TYPE_CHECKING:
    from telegram import Bot

logger = logging.getLogger(__name__)


# Message-builder lives in telegram_bot.messages — imported above as
# _build_message so this file's call sites stay unchanged.


def make_event_handler(bot: "Bot"):
    async def handle_event(request: web.Request) -> web.Response:
        try:
            body = await request.read()
            event = json.loads(body)
        except Exception as exc:
            logger.warning("Bad event payload: %s", exc)
            return web.Response(status=400, text="bad json")

        flag_name, message = _build_message(event)
        event_address = (event.get("address") or "").lower()

        # Broadcast to channel regardless of per-user subs
        await broadcast_to_channel(bot, message)

        if not flag_name or not event_address:
            return web.Response(text="ok")

        # Per-user delivery
        for chat_id, sub in subscriptions.all_subscriptions():
            sub_addr = (sub.get("addr") or "").lower()
            if sub_addr != event_address:
                continue
            if not sub.get(flag_name):
                continue
            try:
                await send_alert(bot, chat_id, message)
            except Exception as exc:
                logger.error("Delivery to %s failed: %s", chat_id, exc)

        return web.Response(text="ok")

    return handle_event


def build_app(bot: "Bot") -> web.Application:
    app = web.Application()
    app.router.add_post("/event", make_event_handler(bot))
    return app
