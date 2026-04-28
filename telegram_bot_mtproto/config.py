"""
Configuration for the MTProto bot variant.

Reads BOT_TOKEN, MTPROTO_API_ID, MTPROTO_API_HASH and proxy settings from
the environment.  Subscription file path and broadcast channel are
inherited from the PTB variant's config so both impls share state.

Required:
    BOT_TOKEN              Telegram bot token from @BotFather
    MTPROTO_API_ID         Numeric API ID from https://my.telegram.org/apps
    MTPROTO_API_HASH       Hex string from https://my.telegram.org/apps

Optional proxy (precedence: MTPROXY_* > BOT_PROXY > direct):
    MTPROXY_HOST           MTProto proxy hostname (e.g. bella-cook.com)
    MTPROXY_PORT           MTProto proxy port (e.g. 443)
    MTPROXY_SECRET         Hex secret as displayed in the Telegram app
    BOT_PROXY              Fallback http/https/socks5/socks5h URL

Optional:
    MTPROTO_SESSION_FILE   Path to Telethon session sqlite (default
                           alongside subscriptions.json)
    LOCAL_EVENT_PORT       Port the aiohttp event receiver listens on
    P2POOL_API_URL         Base URL of p2pool web API
    SUBSCRIPTIONS_FILE     Shared with telegram_bot variant
    BROADCAST_CHANNEL_ID   Optional broadcast channel
    ONE_SUB_PER_ADDRESS    "1"/"true" to enforce one subscriber per addr
"""
from __future__ import annotations

import os

BOT_TOKEN: str = os.environ["BOT_TOKEN"]

# MTProto API credentials — free, but unique per developer.  Get from
# https://my.telegram.org/apps after logging in with the bot owner's
# personal Telegram account (the bot itself doesn't need to log in).
_api_id_raw = os.environ.get("MTPROTO_API_ID", "").strip()
MTPROTO_API_ID: int = int(_api_id_raw) if _api_id_raw.isdigit() else 0
MTPROTO_API_HASH: str = os.environ.get("MTPROTO_API_HASH", "").strip()

# Reuse the PTB variant's subscriptions store path so a single user list
# applies to both impls — switching impl does not lose subscribers.
_default_sub_file = os.path.join(
    os.path.dirname(__file__), "..", "telegram_bot", "subscriptions.json"
)
SUBSCRIPTIONS_FILE: str = os.environ.get("SUBSCRIPTIONS_FILE", _default_sub_file)

# Telethon stores per-bot session state in a SQLite file.  Place it next
# to subscriptions.json so the same volume mount captures both.
_default_session = os.path.join(
    os.path.dirname(SUBSCRIPTIONS_FILE), "mtproto_session"
)
SESSION_FILE: str = os.environ.get("MTPROTO_SESSION_FILE", _default_session)

LOCAL_EVENT_PORT: int = int(os.environ.get("LOCAL_EVENT_PORT", "19349"))
P2POOL_API_URL: str = os.environ.get("P2POOL_API_URL", "http://127.0.0.1:9348")
BROADCAST_CHANNEL_ID: str = os.environ.get("BROADCAST_CHANNEL_ID", "")
ONE_SUB_PER_ADDRESS: bool = os.environ.get("ONE_SUB_PER_ADDRESS", "").lower() in (
    "1", "true", "yes",
)

# Proxy settings — MTProxy form takes precedence when MTPROXY_HOST is set.
MTPROXY_HOST: str = os.environ.get("MTPROXY_HOST", "").strip()
MTPROXY_PORT: int = int(os.environ.get("MTPROXY_PORT", "443") or "443")
MTPROXY_SECRET: str = os.environ.get("MTPROXY_SECRET", "").strip()
BOT_PROXY: str = os.environ.get("BOT_PROXY", "").strip()


def telethon_proxy():
    """Return a Telethon-shaped proxy tuple, or None for direct connection.

    Telethon proxy formats:
      MTProxy:   ("mtproxy", host, port, secret_hex)
      SOCKS5:    (socks.SOCKS5, host, port, rdns:bool, user, password)
      HTTP:      (socks.HTTP,   host, port, rdns:bool, user, password)
    """
    if MTPROXY_HOST and MTPROXY_SECRET:
        return ("mtproxy", MTPROXY_HOST, MTPROXY_PORT, MTPROXY_SECRET)
    if BOT_PROXY:
        # urllib import is stdlib; deferred to avoid module-load cost when
        # no proxy is configured.  pylint: disable=import-outside-toplevel
        from urllib.parse import urlsplit  # noqa: PLC0415
        s = urlsplit(BOT_PROXY)
        host = s.hostname
        port = s.port
        if not host:
            return None
        # PySocks is only required when a SOCKS/HTTP proxy URL is
        # actually used; deferring the import means the mtproto bot
        # still works for MTProxy-only deployments where PySocks isn't
        # installed.  pylint: disable=import-outside-toplevel
        if s.scheme in ("socks5", "socks5h"):
            import socks  # noqa: PLC0415,F401  # PySocks
            rdns = s.scheme == "socks5h"
            return (socks.SOCKS5, host, port or 1080, rdns, s.username, s.password)
        if s.scheme in ("http", "https"):
            import socks  # noqa: PLC0415,F401
            return (socks.HTTP, host, port or 8080, True, s.username, s.password)
    return None


def redact_proxy(p) -> str:
    """Render a proxy tuple safely for logging (passwords masked)."""
    if not p:
        return "(none — direct connection)"
    if p[0] == "mtproxy":
        secret = p[3] or ""
        masked = (secret[:8] + "…") if len(secret) > 8 else "***"
        return f"mtproxy://{p[1]}:{p[2]} secret={masked}"
    # PySocks tuple: (proxy_type:int, host, port, rdns, user, password)
    # PySocks codes: SOCKS4=1, SOCKS5=2, HTTP=3.
    scheme = {1: "socks4", 2: "socks5", 3: "http"}.get(int(p[0]), "proxy")
    user = p[4] if len(p) >= 5 else None
    return f"{scheme}://{(user + ':***@') if user else ''}{p[1]}:{p[2]}"
