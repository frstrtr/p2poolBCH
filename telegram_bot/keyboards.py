"""
Inline keyboard builder helpers.
"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .subscriptions import FLAGS


def _flag_label(flag: str, value: bool) -> str:
    icons = {
        "connect":    ("🟢 Connect ON",    "⚫ Connect OFF"),
        "disconnect": ("🔴 Disconnect ON", "⚫ Disconnect OFF"),
        "share":      ("📦 Share ON",      "⚫ Share OFF"),
        "block":      ("🏆 Block ON",      "⚫ Block OFF"),
    }
    return icons[flag][0] if value else icons[flag][1]


def build_main_menu(sub: dict | None) -> InlineKeyboardMarkup:
    """
    Main menu keyboard.  If sub is None (not subscribed) show only
    'Set address' button.  Otherwise show address + all 4 toggle buttons +
    Unsubscribe.
    """
    if sub is None or sub.get("addr") is None:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 Set mining address", callback_data="set_addr")],
        ])

    addr_short = sub["addr"][:14] + "…" + sub["addr"][-6:]
    rows = [
        [InlineKeyboardButton(f"📝 Address: {addr_short}", callback_data="set_addr")],
    ]
    # Toggles in a 2-column grid
    flag_buttons = [
        InlineKeyboardButton(_flag_label(f, sub[f]), callback_data=f"toggle_{f}")
        for f in FLAGS
    ]
    rows.append(flag_buttons[:2])
    rows.append(flag_buttons[2:])
    rows.append([InlineKeyboardButton("❌ Unsubscribe", callback_data="unsub_confirm")])
    return InlineKeyboardMarkup(rows)


def build_unsub_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes, unsubscribe", callback_data="unsub_do"),
            InlineKeyboardButton("↩ Cancel", callback_data="menu"),
        ]
    ])
