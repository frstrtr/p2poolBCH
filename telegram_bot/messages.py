"""
Shared alert-message formatter for both bot variants.

Both ``telegram_bot`` (PTB / Bot API over HTTPS) and
``telegram_bot_mtproto`` (Telethon / MTProto direct) call ``build_message``
to convert a p2pool ``/event`` payload into the user-facing HTML message
plus its subscription-flag bucket (``connect`` / ``disconnect`` /
``share`` / ``block``), so the wording is identical regardless of the
transport library.
"""
from __future__ import annotations

import re


def fmt_addr(addr: str | None) -> str:
    """Render a BCH address as truncated <code>...</code>, or <unknown>."""
    if not addr:
        return "<unknown>"
    return f"<code>{addr[:14]}…{addr[-6:]}</code>"


def extract_worker(username: str, address: str) -> str:
    """Return the worker-name suffix from a Stratum username.

    Stratum authorize strings have the form ``address[._]workername`` and may
    carry difficulty hints after ``+`` or ``/`` (e.g. ``addr.rig1+1000``).
    When no suffix is present an empty string is returned so callers can omit
    the Worker line entirely.
    """
    base = re.split(r'[+/]', username, 1)[0]
    if address and base.startswith(address):
        suffix = base[len(address):]
        if suffix and suffix[0] in ("_", "."):
            return suffix[1:]
        return ""
    for sep in (".", "_"):
        if sep in base:
            return base.split(sep, 1)[1]
    return ""


def worker_line(worker: str) -> str:
    """Return a formatted ``Worker:`` line, or empty string when not set."""
    if worker:
        return f"\nWorker: <code>{worker}</code>"
    return ""


def format_idle(seconds: float) -> str:
    """Render a seconds value as a compact human duration (s / m / h+m / d+h)."""
    s = max(0.0, float(seconds or 0))
    if s < 60:
        return f"{int(s)}s"
    if s < 3600:
        return f"{int(s/60)}m"
    if s < 86400:
        return f"{int(s/3600)}h {int((s%3600)/60)}m"
    return f"{int(s/86400)}d {int((s%86400)/3600)}h"


# 8 event types -> 8 branches, each with locals for the type's own fields.
# A dispatch table is more invasive than the readability gain warrants here.
# pylint: disable=too-many-locals,too-many-return-statements,too-many-branches
def build_message(event: dict) -> tuple[str, str]:
    """Map a p2pool /event payload to (flag_name, html_message_text).

    flag_name is one of "connect", "disconnect", "share", "block", or ""
    when the event type is unrecognised.  The good-news/bad-news mapping
    keeps the four user-facing toggles unchanged across all event types
    introduced after the original four (silent, active_again, flapping,
    stable), so existing subscribers receive the new alerts without
    re-opting in.
    """
    t = event.get("type", "")
    node = event.get("node", "?")
    username = event.get("username", "?")
    address = event.get("address") or ""
    addr_html = fmt_addr(address)
    worker = extract_worker(username, address)

    if t == "worker_connected":
        ip = event.get("ip", "?")
        latency_ms = event.get("latency_ms")
        latency_str = ""
        if isinstance(latency_ms, (int, float)) and latency_ms > 0:
            # The notifier defers the alert by ~2 s so the first stratum
            # ping has time to land; if a value is present, render it on
            # its own line.  Absent value means the ping hadn't completed
            # yet (or the miner doesn't respond to client.get_version).
            latency_str = f"\nLatency: <b>{latency_ms:.1f} ms</b>"
        return "connect", (
            f"🟢 <b>Worker connected</b>\n"
            f"Node: {node}"
            f"{worker_line(worker)}\n"
            f"Address: {addr_html}\n"
            f"IP: <code>{ip}</code>"
            f"{latency_str}"
        )
    if t == "worker_disconnected":
        return "disconnect", (
            f"🔴 <b>Worker disconnected</b>\n"
            f"Node: {node}"
            f"{worker_line(worker)}\n"
            f"Address: {addr_html}"
        )
    if t == "worker_silent":
        idle = event.get("idle_seconds", 0)
        return "disconnect", (
            f"🟡 <b>Worker silent (no shares)</b>\n"
            f"Node: {node}"
            f"{worker_line(worker)}\n"
            f"Address: {addr_html}\n"
            f"Idle: <b>{format_idle(idle)}</b>"
        )
    if t == "worker_active_again":
        return "connect", (
            f"✅ <b>Worker active again</b>\n"
            f"Node: {node}"
            f"{worker_line(worker)}\n"
            f"Address: {addr_html}"
        )
    if t == "worker_flapping":
        count = event.get("flap_count", 0)
        win = int(event.get("window_seconds", 3600) // 60)
        return "disconnect", (
            f"⚠️ <b>Worker flapping</b>\n"
            f"Node: {node}"
            f"{worker_line(worker)}\n"
            f"Address: {addr_html}\n"
            f"Flaps: <b>{count}</b> in last {win}m"
        )
    if t == "worker_stable":
        return "connect", (
            f"✅ <b>Worker stable</b>\n"
            f"Node: {node}"
            f"{worker_line(worker)}\n"
            f"Address: {addr_html}"
        )
    if t == "share_found":
        dead = event.get("dead", False)
        h = event.get("hash") or "?"
        status = "💀 DEAD" if dead else "✅ accepted"
        return "share", (
            f"📦 <b>Share {status}</b>\n"
            f"Node: {node}"
            f"{worker_line(worker)}\n"
            f"Address: {addr_html}\n"
            f"Hash: <code>{h[:16]}…</code>"
        )
    if t == "block_found":
        h = event.get("hash") or "?"
        reward_sat = event.get("reward_sat") or 0
        symbol = event.get("symbol") or "BCH"
        explorer_url = event.get("explorer_url") or ""
        reward_str = (
            f"\nReward: <b>{reward_sat / 1e8:.8f} {symbol}</b>" if reward_sat else ""
        )
        if explorer_url:
            hash_str = f'\nBlock: <a href="{explorer_url}">{h[:16]}…</a>'
        else:
            hash_str = f"\nHash: <code>{h[:16]}…</code>"
        return "block", (
            f"🏆 <b>BLOCK FOUND!</b>\n"
            f"Node: {node}"
            f"{worker_line(worker)}\n"
            f"Address: {addr_html}"
            f"{reward_str}"
            f"{hash_str}"
        )
    return "", f"[unknown event type: {t}]"
