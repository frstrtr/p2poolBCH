"""
Inline keyboard builders for the MTProto bot variant.

Telethon represents inline keyboards as nested lists of ``Button`` objects.
Layout matches the PTB variant exactly so the user sees the same UI.
"""
from __future__ import annotations

from telethon import Button

# Match the FLAGS tuple in telegram_bot/subscriptions.py without importing
# from there at module load (avoids circular paths in some lazy-loader
# setups).  These four flags are the user-visible toggles.
FLAGS = ("connect", "disconnect", "share", "block")


def _flag_label(flag: str, value: bool) -> str:
    icons = {
        "connect":    ("🟢 Connect ON",    "⚫ Connect OFF"),
        "disconnect": ("🔴 Disconnect ON", "⚫ Disconnect OFF"),
        "share":      ("📦 Share ON",      "⚫ Share OFF"),
        "block":      ("🏆 Block ON",      "⚫ Block OFF"),
    }
    return icons[flag][0] if value else icons[flag][1]


def build_main_menu(sub):
    if sub is None or sub.get("addr") is None:
        return [[Button.inline("📝 Set mining address", b"set_addr")]]

    addr = sub["addr"]
    addr_short = (addr[:14] + "…" + addr[-6:]) if len(addr) > 24 else addr
    rows = [[Button.inline(f"📝 Address: {addr_short}", b"set_addr")]]
    flag_buttons = [
        Button.inline(_flag_label(f, sub.get(f, False)), f"toggle_{f}".encode())
        for f in FLAGS
    ]
    rows.append(flag_buttons[:2])
    rows.append(flag_buttons[2:])
    rows.append([Button.inline("❌ Unsubscribe", b"unsub_confirm")])
    return rows


def build_unsub_confirm():
    return [[
        Button.inline("✅ Yes, unsubscribe", b"unsub_do"),
        Button.inline("↩ Cancel", b"menu"),
    ]]


def build_inactive_confirm():
    return [[
        Button.inline("💾 Save anyway", b"save_addr_anyway"),
        Button.inline("✏️ Different address", b"change_addr"),
    ]]
