"""
aiohttp server that receives POST /event from p2pool (PyPy2 side) and routes
alerts to subscribed Telegram users.

Event payload (JSON body):
    {"type": "worker_connected"|"worker_disconnected"|"share_found"|"block_found",
     "node": str, "username": str, "address": str, "ip": str (connect/disconnect),
     "hash": str (share/block), "dead": bool (share), "ts": float}
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from aiohttp import web

from . import subscriptions
from .notifier import broadcast_to_channel, send_alert

if TYPE_CHECKING:
    from telegram import Bot

logger = logging.getLogger(__name__)


def _fmt_addr(addr: str | None) -> str:
    if not addr:
        return "<unknown>"
    return f"<code>{addr[:14]}…{addr[-6:]}</code>"


def _build_message(event: dict) -> tuple[str, str]:
    """Return (flag_name, message_text)."""
    t = event.get("type", "")
    node = event.get("node", "?")
    username = event.get("username", "?")
    address = event.get("address") or ""
    addr_html = _fmt_addr(address)

    if t == "worker_connected":
        ip = event.get("ip", "?")
        return "connect", (
            f"🟢 <b>Worker connected</b>\n"
            f"Node: {node}\n"
            f"User: <code>{username}</code>\n"
            f"Address: {addr_html}\n"
            f"IP: <code>{ip}</code>"
        )
    elif t == "worker_disconnected":
        ip = event.get("ip", "?")
        return "disconnect", (
            f"🔴 <b>Worker disconnected</b>\n"
            f"Node: {node}\n"
            f"User: <code>{username}</code>\n"
            f"Address: {addr_html}"
        )
    elif t == "share_found":
        dead = event.get("dead", False)
        h = event.get("hash", "?")
        status = "💀 DEAD" if dead else "✅ accepted"
        return "share", (
            f"📦 <b>Share {status}</b>\n"
            f"Node: {node}\n"
            f"User: <code>{username}</code>\n"
            f"Address: {addr_html}\n"
            f"Hash: <code>{h[:16]}…</code>"
        )
    elif t == "block_found":
        h = event.get("hash", "?")
        return "block", (
            f"🏆 <b>BLOCK FOUND!</b>\n"
            f"Node: {node}\n"
            f"User: <code>{username}</code>\n"
            f"Address: {addr_html}\n"
            f"Hash: <code>{h[:16]}…</code>"
        )
    else:
        return "", f"[unknown event type: {t}]"


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
            await send_alert(bot, chat_id, message)

        return web.Response(text="ok")

    return handle_event


def build_app(bot: "Bot") -> web.Application:
    app = web.Application()
    app.router.add_post("/event", make_event_handler(bot))
    return app
