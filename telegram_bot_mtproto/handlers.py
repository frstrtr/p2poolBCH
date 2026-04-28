"""
Conversation handler for the MTProto bot variant.

Telethon doesn't ship a ConversationHandler equivalent, so per-chat state
is kept in two module-level dicts keyed by ``chat_id``.  Behaviour mirrors
the PTB variant in telegram_bot/handlers.py exactly: same /start/help
commands, same address-input flow, same toggle/unsubscribe buttons, same
"address inactive — save anyway?" confirmation.
"""
from __future__ import annotations

import logging
import re

import aiohttp
from telethon import events

# Reuse the PTB variant's subscription store and config knobs so a single
# user list is the source of truth across both impls.
from telegram_bot import subscriptions

from .config import P2POOL_API_URL, ONE_SUB_PER_ADDRESS
from .keyboards import build_main_menu, build_unsub_confirm, build_inactive_confirm

logger = logging.getLogger(__name__)

# Per-chat state (replaces PTB's ConversationHandler).  Cleared on /start
# and on every terminal action (save, unsubscribe, cancel).
_AWAIT_ADDR = "await_addr"
_CONFIRM_INACTIVE = "confirm_inactive"
_state: dict = {}            # chat_id -> state string above
_pending_addr: dict = {}     # chat_id -> address string during inactive-confirm

_ADDR_RE = re.compile(
    r'^(bitcoincash:[a-z0-9]{42,}|[13][a-zA-Z0-9]{25,34})$'
)


_HELP_TEXT = (
    "📖 <b>P2Pool BCH Notification Bot — quick guide</b>\n\n"
    "Send /start to open the menu.  Tap <b>📝 Set mining address</b> "
    "and paste your BCH payout address (cashaddr or legacy).  Toggle "
    "alert categories with the buttons:\n\n"
    "  🟢 <b>Connect</b> — first connect, recover-from-silent, stable\n"
    "  🔴 <b>Disconnect</b> — last disconnect, silent worker, flapping\n"
    "  📦 <b>Share</b> — every accepted/dead share\n"
    "  🏆 <b>Block</b> — block-found announcements with explorer link\n\n"
    "/cancel exits the address-input flow.  /help shows this text."
)


async def _check_addr_active(addr: str) -> bool:
    """Query the local p2pool API; True = active, True on any error (fail open)."""
    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            url = f"{P2POOL_API_URL}/miner_stats"
            async with session.get(url, params={"address": addr}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return bool(data.get("active", False))
    except Exception:
        pass
    return True  # fail open


def _menu_text(sub: dict | None) -> str:
    if sub is None or sub.get("addr") is None:
        return (
            "👋 <b>P2Pool BCH Notifications</b>\n\n"
            "Set your mining address to start receiving alerts."
        )
    flags_on = [f for f in ("connect", "disconnect", "share", "block") if sub.get(f)]
    return (
        "⚙️ <b>Your notification settings</b>\n\n"
        f"Address: <code>{sub['addr']}</code>\n"
        f"Active: {', '.join(flags_on) or 'none'}"
    )


async def _show_menu(target, chat_id: int, edit: bool = False):
    """Send or edit the main menu message.

    target may be a NewMessage event, a CallbackQuery, or a Telethon
    client; whichever exposes ``respond`` / ``edit`` works.
    """
    sub = subscriptions.get(chat_id)
    text = _menu_text(sub)
    buttons = build_main_menu(sub)
    if edit and hasattr(target, "edit"):
        try:
            await target.edit(text, buttons=buttons, parse_mode="html")
            return
        except Exception:
            # Falls through to respond() — happens when the source message
            # is not editable (e.g. /start in a fresh chat).
            pass
    await target.respond(text, buttons=buttons, parse_mode="html")


def register_handlers(client):
    """Attach all events to a connected Telethon client."""

    @client.on(events.NewMessage(pattern=r"^/start(?:@\w+)?$"))
    async def _cmd_start(event):
        chat_id = event.chat_id
        _state.pop(chat_id, None)
        _pending_addr.pop(chat_id, None)
        await _show_menu(event, chat_id)

    @client.on(events.NewMessage(pattern=r"^/help(?:@\w+)?$"))
    async def _cmd_help(event):
        await event.respond(_HELP_TEXT, parse_mode="html")

    @client.on(events.NewMessage(pattern=r"^/cancel(?:@\w+)?$"))
    async def _cmd_cancel(event):
        chat_id = event.chat_id
        _state.pop(chat_id, None)
        _pending_addr.pop(chat_id, None)
        await _show_menu(event, chat_id)

    @client.on(events.CallbackQuery)
    async def _on_callback(q):
        chat_id = q.chat_id
        data = q.data.decode("utf-8", errors="replace")

        if data == "set_addr":
            _state[chat_id] = _AWAIT_ADDR
            await q.answer()
            await q.respond(
                "Send your BCH payout address (cashaddr <code>bitcoincash:q…</code> "
                "or legacy <code>1…</code> / <code>3…</code>).  /cancel to abort.",
                parse_mode="html",
            )
            return

        if data.startswith("toggle_"):
            flag = data[len("toggle_"):]
            sub = subscriptions.get(chat_id)
            if sub is not None:
                subscriptions.upsert(chat_id, {flag: not sub.get(flag, False)})
            await q.answer("Toggled")
            await _show_menu(q, chat_id, edit=True)
            return

        if data == "unsub_confirm":
            await q.answer()
            try:
                await q.edit(
                    "Unsubscribe from all alerts?",
                    buttons=build_unsub_confirm(),
                    parse_mode="html",
                )
            except Exception:
                await q.respond(
                    "Unsubscribe from all alerts?",
                    buttons=build_unsub_confirm(),
                    parse_mode="html",
                )
            return

        if data == "unsub_do":
            subscriptions.delete(chat_id)
            _state.pop(chat_id, None)
            _pending_addr.pop(chat_id, None)
            await q.answer("Unsubscribed")
            await _show_menu(q, chat_id, edit=True)
            return

        if data == "menu":
            await q.answer()
            await _show_menu(q, chat_id, edit=True)
            return

        if data == "save_addr_anyway":
            addr = _pending_addr.pop(chat_id, None)
            if addr:
                subscriptions.upsert(chat_id, {"addr": addr})
            _state.pop(chat_id, None)
            await q.answer("Address saved")
            await _show_menu(q, chat_id, edit=True)
            return

        if data == "change_addr":
            _pending_addr.pop(chat_id, None)
            _state[chat_id] = _AWAIT_ADDR
            await q.answer()
            try:
                await q.edit(
                    "Send a different BCH address.  /cancel to abort.",
                    parse_mode="html",
                )
            except Exception:
                await q.respond(
                    "Send a different BCH address.  /cancel to abort.",
                    parse_mode="html",
                )
            return

        await q.answer("Unknown action")

    @client.on(events.NewMessage(incoming=True))
    async def _on_text(event):
        # Skip command messages — they are dispatched by the patterns above.
        text = (event.raw_text or "").strip()
        if text.startswith("/"):
            return
        chat_id = event.chat_id
        if _state.get(chat_id) != _AWAIT_ADDR:
            return  # only react when we're waiting for an address

        addr = text
        if not _ADDR_RE.match(addr):
            await event.respond(
                "❌ <b>Invalid address format.</b>\n\n"
                "Please enter a valid BCH address:\n"
                "• cashaddr: <code>bitcoincash:qp3w…</code>\n"
                "• legacy: <code>1A1zP…</code> or <code>3J98t…</code>\n\n"
                "Or /cancel to go back.",
                parse_mode="html",
            )
            return

        if ONE_SUB_PER_ADDRESS:
            for other_id, other_sub in subscriptions.all_subscriptions():
                if (
                    str(other_id) != str(chat_id)
                    and (other_sub.get("addr") or "").lower() == addr.lower()
                ):
                    await event.respond(
                        "❌ That address is already registered by another subscriber."
                    )
                    return

        active = await _check_addr_active(addr)
        if not active:
            _pending_addr[chat_id] = addr
            _state[chat_id] = _CONFIRM_INACTIVE
            await event.respond(
                "⚠️ <b>Address not found on this node</b>\n\n"
                f"<code>{addr}</code>\n\n"
                "This address has no active hashrate on this p2pool node "
                "right now. You can still save it — you'll get alerts as "
                "soon as it connects.",
                parse_mode="html",
                buttons=build_inactive_confirm(),
            )
            return

        subscriptions.upsert(chat_id, {"addr": addr})
        _state.pop(chat_id, None)
        await event.respond(f"✅ Address saved: <code>{addr}</code>", parse_mode="html")
        await _show_menu(event, chat_id)
