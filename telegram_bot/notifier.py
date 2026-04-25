"""
Alert delivery helpers.
"""
from __future__ import annotations

from telegram import Bot
from telegram.error import TelegramError

from .config import BROADCAST_CHANNEL_ID


async def send_alert(bot: Bot, chat_id: int | str, text: str) -> None:
    try:
        await bot.send_message(
            chat_id=int(chat_id),
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except TelegramError as exc:
        print(f"[notifier] send_alert({chat_id}) failed: {exc}")


async def broadcast_to_channel(bot: Bot, text: str) -> None:
    if not BROADCAST_CHANNEL_ID:
        return
    try:
        await bot.send_message(
            chat_id=BROADCAST_CHANNEL_ID,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except TelegramError as exc:
        print(f"[notifier] broadcast_to_channel failed: {exc}")
