"""
PTB ConversationHandler + callback handlers.

States
------
IDLE         — user interacts via inline keyboard buttons
AWAIT_ADDR   — user is typing a BCH address

Entry points
------------
/start  (or any text when not in a conversation) → show main menu

Callbacks
---------
set_addr        → ask user to type address  (→ AWAIT_ADDR)
toggle_<flag>   → flip that flag            (→ IDLE, edit menu)
unsub_confirm   → ask for confirmation      (→ IDLE, edit message)
unsub_do        → delete sub               (→ IDLE)
menu            → show main menu            (→ IDLE)
"""
from __future__ import annotations

import re

from telegram import Update, InlineKeyboardMarkup
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import aiohttp

from . import subscriptions
from .keyboards import build_main_menu, build_unsub_confirm, build_inactive_confirm

IDLE = 0
AWAIT_ADDR = 1
CONFIRM_INACTIVE = 2

# Very permissive BCH address regex: accept cashaddr (bitcoincash:q...) and
# legacy (1... / 3...) — we just store whatever the user sends.
_ADDR_RE = re.compile(
    r'^(bitcoincash:[a-z0-9]{42,}|[13][a-zA-Z0-9]{25,34})$'
)


async def _check_addr_active(addr: str) -> bool:
    """Query the local p2pool API. Returns True if active, True on any error (fail open)."""
    from .config import P2POOL_API_URL
    try:
        url = f"{P2POOL_API_URL}/miner_stats"
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, params={"address": addr}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return bool(data.get("active", False))
    except Exception:
        pass
    return True  # fail open: don't block save when API is unreachable


async def _show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> int:
    chat_id = update.effective_chat.id
    sub = subscriptions.get(chat_id)
    markup = build_main_menu(sub)

    if sub is None or sub.get("addr") is None:
        text = (
            "👋 <b>P2Pool BCH Notifications</b>\n\n"
            "Set your mining address to start receiving alerts."
        )
    else:
        flags_on = [f for f in ("connect", "disconnect", "share", "block") if sub.get(f)]
        text = (
            "⚙️ <b>Your notification settings</b>\n\n"
            f"Address: <code>{sub['addr']}</code>\n"
            f"Active: {', '.join(flags_on) or 'none'}"
        )

    if edit and update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=markup, parse_mode="HTML"
        )
    else:
        if update.callback_query:
            await update.callback_query.answer()
        await update.effective_chat.send_message(
            text, reply_markup=markup, parse_mode="HTML"
        )
    return IDLE


# ------------------------------------------------------------------ #
# Entry points                                                        #
# ------------------------------------------------------------------ #

_HELP_TEXT = (
    "📖 <b>P2Pool BCH Notification Bot — complete guide</b>\n\n"

    "━━━━━━━━━━━━━━━━━━━━━\n"
    "🔧 <b>Setup</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━\n"
    "Tap <b>📝 Set mining address</b> (or the address row if already set) "
    "and send your BCH address:\n"
    "  • Cashaddr: <code>bitcoincash:qp3w…</code>\n"
    "  • Legacy: <code>1A1zP…</code> or <code>3J98t…</code>\n\n"
    "If the address isn't currently mining on this node, the bot will warn "
    "you and offer <b>💾 Save anyway</b> — alerts will start as soon as the "
    "miner connects.\n\n"
    "Each Telegram account tracks <b>one address</b> at a time. "
    "To switch, tap the address row and enter a new one.\n\n"

    "━━━━━━━━━━━━━━━━━━━━━\n"
    "🔔 <b>Notification toggles</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━\n"
    "After setting an address, four toggle buttons appear. "
    "Tap any button to flip it on/off (all are <b>ON</b> by default):\n\n"
    "  🟢 <b>Connect ON / ⚫ Connect OFF</b>\n"
    "     Fired when your miner establishes a stratum connection.\n\n"
    "  🔴 <b>Disconnect ON / ⚫ Disconnect OFF</b>\n"
    "     Fired when your miner drops its stratum connection.\n\n"
    "  📦 <b>Share ON / ⚫ Share OFF</b>\n"
    "     Fired each time your miner submits a valid share.\n\n"
    "  🏆 <b>Block ON / ⚫ Block OFF</b>\n"
    "     Fired when the pool finds a BCH block.\n\n"

    "━━━━━━━━━━━━━━━━━━━━━\n"
    "❌ <b>Unsubscribing</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━\n"
    "Tap <b>❌ Unsubscribe</b> in the menu, then confirm with "
    "<b>✅ Yes, unsubscribe</b>. This deletes your address and all settings. "
    "You can re-subscribe at any time with /start.\n\n"

    "━━━━━━━━━━━━━━━━━━━━━\n"
    "💬 <b>Commands</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━\n"
    "  /start — show this guide and open the menu\n"
    "  /cancel — abort address input and return to the menu\n\n"
    "All other interaction is via the inline keyboard below. ⬇️"
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.effective_chat.send_message(_HELP_TEXT, parse_mode="HTML")
    return await _show_menu(update, context)


# ------------------------------------------------------------------ #
# Callback query handlers                                             #
# ------------------------------------------------------------------ #

async def cb_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    return await _show_menu(update, context, edit=True)


async def cb_set_addr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "📝 Please send your BCH mining address (cashaddr preferred).\n"
        "Example: <code>bitcoincash:qp3w…xyz</code>\n\n"
        "Or /cancel to go back.",
        parse_mode="HTML",
    )
    return AWAIT_ADDR


async def cb_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    flag = update.callback_query.data.split("_", 1)[1]  # e.g. "connect"
    if flag not in subscriptions.FLAGS:
        return IDLE
    chat_id = update.effective_chat.id
    sub = subscriptions.get_or_default(chat_id)
    sub = subscriptions.upsert(chat_id, {flag: not sub.get(flag, True)})
    markup = build_main_menu(sub)
    flags_on = [f for f in subscriptions.FLAGS if sub.get(f)]
    text = (
        "⚙️ <b>Your notification settings</b>\n\n"
        f"Address: <code>{sub.get('addr', '(not set)')}</code>\n"
        f"Active: {', '.join(flags_on) or 'none'}"
    )
    await update.callback_query.edit_message_text(
        text, reply_markup=markup, parse_mode="HTML"
    )
    return IDLE


async def cb_unsub_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "❓ Are you sure you want to unsubscribe and remove all your settings?",
        reply_markup=build_unsub_confirm(),
    )
    return IDLE


async def cb_unsub_do(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer("Unsubscribed.")
    subscriptions.delete(update.effective_chat.id)
    await update.callback_query.edit_message_text(
        "✅ You have been unsubscribed. Send /start to subscribe again.",
        reply_markup=InlineKeyboardMarkup([]),
    )
    return ConversationHandler.END


# ------------------------------------------------------------------ #
# AWAIT_ADDR state                                                    #
# ------------------------------------------------------------------ #

async def recv_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    addr = (update.message.text or "").strip()
    if not _ADDR_RE.match(addr):
        await update.message.reply_text(
            "❌ <b>Invalid address format.</b>\n\n"
            "Please enter a valid BCH address:\n"
            "• cashaddr: <code>bitcoincash:qp3w…</code>\n"
            "• legacy: <code>1A1zP…</code> or <code>3J98t…</code>\n\n"
            "Or /cancel to go back.",
            parse_mode="HTML",
        )
        return AWAIT_ADDR

    chat_id = update.effective_chat.id

    from .config import ONE_SUB_PER_ADDRESS
    if ONE_SUB_PER_ADDRESS:
        for other_id, other_sub in subscriptions.all_subscriptions():
            if str(other_id) != str(chat_id) and (other_sub.get("addr") or "").lower() == addr.lower():
                await update.message.reply_text(
                    "❌ That address is already registered by another subscriber.\n"
                    "Each address can only have one subscriber on this node.",
                    parse_mode="HTML",
                )
                return AWAIT_ADDR

    # Check whether the address is currently active on this node.
    active = await _check_addr_active(addr)
    if not active:
        context.user_data["pending_addr"] = addr
        await update.message.reply_text(
            "⚠️ <b>Address not found on this node</b>\n\n"
            f"<code>{addr}</code>\n\n"
            "This address has no active hashrate on this p2pool node right now. "
            "You can still save it — you'll get alerts as soon as it connects.",
            parse_mode="HTML",
            reply_markup=build_inactive_confirm(),
        )
        return CONFIRM_INACTIVE

    subscriptions.upsert(chat_id, {"addr": addr})
    await update.message.reply_text(
        f"✅ Address saved: <code>{addr}</code>",
        parse_mode="HTML",
    )
    return await _show_menu(update, context)


# ------------------------------------------------------------------ #
# CONFIRM_INACTIVE state                                             #
# ------------------------------------------------------------------ #

async def cb_save_anyway(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer("Address saved!")
    addr = context.user_data.pop("pending_addr", None)
    if addr:
        subscriptions.upsert(update.effective_chat.id, {"addr": addr})
    return await _show_menu(update, context, edit=True)


async def cb_change_addr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    context.user_data.pop("pending_addr", None)
    await update.callback_query.edit_message_text(
        "📝 Please send your BCH mining address (cashaddr preferred).\n"
        "Example: <code>bitcoincash:qp3w…</code>\n\n"
        "Or /cancel to go back.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([]),
    )
    return AWAIT_ADDR


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _show_menu(update, context)


# ------------------------------------------------------------------ #
# ConversationHandler factory                                         #
# ------------------------------------------------------------------ #

def build_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
        ],
        states={
            IDLE: [
                CallbackQueryHandler(cb_menu, pattern="^menu$"),
                CallbackQueryHandler(cb_set_addr, pattern="^set_addr$"),
                CallbackQueryHandler(cb_toggle, pattern=r"^toggle_"),
                CallbackQueryHandler(cb_unsub_confirm, pattern="^unsub_confirm$"),
                CallbackQueryHandler(cb_unsub_do, pattern="^unsub_do$"),
            ],
            AWAIT_ADDR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_address),
                CommandHandler("cancel", cmd_cancel),
            ],
            CONFIRM_INACTIVE: [
                CallbackQueryHandler(cb_save_anyway, pattern="^save_addr_anyway$"),
                CallbackQueryHandler(cb_change_addr, pattern="^change_addr$"),
                CommandHandler("cancel", cmd_cancel),
            ],
        },
        fallbacks=[CommandHandler("start", cmd_start)],
        allow_reentry=True,
        per_chat=True,
        per_user=False,   # group-safe: one sub per chat, not per user
        per_message=False,
    )
