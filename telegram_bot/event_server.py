"""
aiohttp server that receives POST /event from p2pool (PyPy2 side) and routes
alerts to subscribed Telegram users.

Event payload (JSON body):
    {"type": "worker_connected"|"worker_disconnected"|"share_found"|"block_found",
     "node": str, "username": str, "address": str, "ip": str (connect/disconnect),
     "hash": str (share/block), "dead": bool (share),
     "reward_sat": int (block), "symbol": str (block),
     "explorer_url": str (block, full URL or empty), "ts": float}
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


def _extract_worker(username: str, address: str) -> str:
    """Return the worker-name suffix from a Stratum username.

    Stratum authorize strings have the form ``address[._]workername`` and may
    carry difficulty hints after ``+`` or ``/`` (e.g. ``addr.rig1+1000``).
    When no suffix is present an empty string is returned so callers can omit
    the Worker line entirely.
    """
    import re as _re
    # Strip difficulty/share-target hints before extracting the worker name
    # e.g. "bitcoincash:qpabc.rig1+1000/2" → "bitcoincash:qpabc.rig1"
    base = _re.split(r'[+/]', username, 1)[0]

    if address and base.startswith(address):
        suffix = base[len(address):]
        if suffix and suffix[0] in ("_", "."):
            return suffix[1:]
        return ""  # username == address, no worker suffix
    # Fallback for unusual formats: split on first separator
    for sep in (".", "_"):
        if sep in base:
            return base.split(sep, 1)[1]
    return ""


def _worker_line(worker: str) -> str:
    """Return a formatted ``Worker:`` line, or empty string when not set."""
    if worker:
        return f"\nWorker: <code>{worker}</code>"
    return ""


def _build_message(event: dict) -> tuple[str, str]:
    """Return (flag_name, message_text)."""
    t = event.get("type", "")
    node = event.get("node", "?")
    username = event.get("username", "?")
    address = event.get("address") or ""
    addr_html = _fmt_addr(address)
    worker = _extract_worker(username, address)

    if t == "worker_connected":
        ip = event.get("ip", "?")
        return "connect", (
            f"🟢 <b>Worker connected</b>\n"
            f"Node: {node}"
            f"{_worker_line(worker)}\n"
            f"Address: {addr_html}\n"
            f"IP: <code>{ip}</code>"
        )
    elif t == "worker_disconnected":
        ip = event.get("ip", "?")
        return "disconnect", (
            f"🔴 <b>Worker disconnected</b>\n"
            f"Node: {node}"
            f"{_worker_line(worker)}\n"
            f"Address: {addr_html}"
        )
    elif t == "share_found":
        dead = event.get("dead", False)
        h = event.get("hash") or "?"
        status = "💀 DEAD" if dead else "✅ accepted"
        return "share", (
            f"📦 <b>Share {status}</b>\n"
            f"Node: {node}"
            f"{_worker_line(worker)}\n"
            f"Address: {addr_html}\n"
            f"Hash: <code>{h[:16]}…</code>"
        )
    elif t == "block_found":
        h = event.get("hash") or "?"
        reward_sat = event.get("reward_sat") or 0
        symbol = event.get("symbol") or "BCH"
        explorer_url = event.get("explorer_url") or ""
        reward_str = f"\nReward: <b>{reward_sat / 1e8:.8f} {symbol}</b>" if reward_sat else ""
        if explorer_url:
            hash_str = f"\nBlock: <a href=\"{explorer_url}\">{h[:16]}…</a>"
        else:
            hash_str = f"\nHash: <code>{h[:16]}…</code>"
        return "block", (
            f"🏆 <b>BLOCK FOUND!</b>\n"
            f"Node: {node}"
            f"{_worker_line(worker)}\n"
            f"Address: {addr_html}"
            f"{reward_str}"
            f"{hash_str}"
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
