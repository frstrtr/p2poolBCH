"""
Configuration loaded from environment variables.
Copy .env.example to .env and fill in values, or export them before running.
"""
import os

BOT_TOKEN: str = os.environ["BOT_TOKEN"]

# Optional outbound proxy for Telegram API access.  Useful when the bot
# host can't reach api.telegram.org directly (regional blocks, hostile
# networks, restricted egress).  Examples:
#   http://proxy.example.net:3128
#   https://user:pass@proxy.example.net:443
#   socks5://user:pass@127.0.0.1:1080         (SOCKS support requires
#   socks5h://user:pass@127.0.0.1:1080         python-telegram-bot[socks])
# When unset the bot connects directly.
BOT_PROXY: str = os.environ.get("BOT_PROXY", "").strip()
# Separate proxy for the long-poll getUpdates connection (defaults to
# BOT_PROXY).  Some setups use a different upstream for outbound vs
# long-poll — most users can leave this empty.
BOT_PROXY_GET_UPDATES: str = os.environ.get("BOT_PROXY_GET_UPDATES", BOT_PROXY).strip()

# Port that aiohttp listens on for p2pool event POSTs
LOCAL_EVENT_PORT: int = int(os.environ.get("LOCAL_EVENT_PORT", "19349"))

# Base URL of the running p2pool node's JSON-RPC web interface (for /api/*)
P2POOL_API_URL: str = os.environ.get("P2POOL_API_URL", "http://127.0.0.1:9348")

# Path to the subscriptions JSON file
SUBSCRIPTIONS_FILE: str = os.environ.get(
    "SUBSCRIPTIONS_FILE",
    os.path.join(os.path.dirname(__file__), "subscriptions.json"),
)

# Optional: a Telegram channel where every event is broadcast regardless of
# per-user subscriptions (leave empty to disable).
BROADCAST_CHANNEL_ID: str = os.environ.get("BROADCAST_CHANNEL_ID", "")

# When True, each BCH address may only be claimed by one subscriber.
# The second user to attempt claiming that address is rejected.
# Default: False (multiple subscribers per address are allowed).
ONE_SUB_PER_ADDRESS: bool = os.environ.get("ONE_SUB_PER_ADDRESS", "").lower() in ("1", "true", "yes")
