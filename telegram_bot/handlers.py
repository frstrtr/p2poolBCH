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

from . import subscriptions
from .keyboards import build_main_menu, build_unsub_confirm

IDLE = 0
AWAIT_ADDR = 1

# Very permissive BCH address regex: accept cashaddr (bitcoincash:q...) and
# legacy (1... / 3...) — we just store whatever the user sends.
_ADDR_RE = re.compile(
    r'^(bitcoincash:[a-z0-9]{42,}|[13][a-zA-Z0-9]{25,34})$'
)


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

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
            "❌ That doesn't look like a valid BCH address. Please try again or /cancel."
        )
        return AWAIT_ADDR

    chat_id = update.effective_chat.id
    subscriptions.upsert(chat_id, {"addr": addr})
    await update.message.reply_text(
        f"✅ Address saved: <code>{addr}</code>",
        parse_mode="HTML",
    )
    return await _show_menu(update, context)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _show_menu(update, context)


# ------------------------------------------------------------------ #
# ConversationHandler factory                                         #
# ------------------------------------------------------------------ #

def build_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_start),
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
        },
        fallbacks=[CommandHandler("start", cmd_start)],
        allow_reentry=True,
        per_chat=True,
        per_user=False,  # group-safe: one sub per chat, not per user
    )
