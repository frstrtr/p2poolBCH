#!/bin/sh
# docker-entrypoint.sh — translate environment variables into p2pool arguments.
#
# Required environment variables:
#   RPC_HOST       bitcoind hostname/IP (e.g. 192.168.86.110)
#   RPC_USER       bitcoind RPC username
#   RPC_PASS       bitcoind RPC password
#
# Optional environment variables:
#   NETWORK              p2pool network name (default: bitcoincash)
#   PAYOUT_ADDRESS       mining payout address (BCH address, e.g. bitcoincash:qp... or legacy 1...).
#                        Required when using BCHN — modern BCHN removed the legacy wallet RPC that
#                        p2pool falls back to when no address is supplied.  Set to "dynamic" to let
#                        each miner's login name be used as the payout address (default: dynamic).
#   NODE_NAME            human-readable node label shown in shares (default: hostname)
#   P2POOL_PORT          p2pool P2P listen port (default: network default 9349)
#   WORKER_PORT          Stratum+web listen port (default: 9348)
#   P2POOL_EXTRA_ARGS    additional raw flags appended verbatim to the command
#
# Bot environment variables (all optional; bot starts if BOT_TOKEN or /etc/p2pool-bot.env exists):
#   BOT_TOKEN              Telegram bot token (or put it in /etc/p2pool-bot.env)
#   BOT_IMPL               "ptb" (default — Bot API / HTTPS) or "mtproto"
#                          (Telethon — MTProto direct, supports MTProxy
#                          Telegram-app proxies)
#   MTPROTO_API_ID         (mtproto only) numeric API ID from my.telegram.org/apps
#   MTPROTO_API_HASH       (mtproto only) hex API hash from my.telegram.org/apps
#   MTPROXY_HOST           (mtproto only) MTProto proxy hostname (e.g. bella-cook.com)
#   MTPROXY_PORT           (mtproto only) MTProto proxy port (default 443)
#   MTPROXY_SECRET         (mtproto only) MTProxy secret hex (as shown in Telegram apps)
#   LOCAL_EVENT_PORT       port the bot event receiver listens on (default: 19349)
#   BOT_PROXY              outbound proxy URL for Telegram API
#                          (http://, https://, socks5://, socks5h://) — works
#                          for both impls; for mtproto it's only used when
#                          MTPROXY_HOST is unset.
#   BOT_PROXY_GET_UPDATES  (ptb only) separate proxy for long-poll getUpdates
#                          (defaults to BOT_PROXY)
#
# Mount /etc/p2pool-bot.env (chmod 600) with KEY=VALUE pairs to pass secrets,
# OR pass any of the above as docker -e VAR=value (they propagate to the bot
# subprocess automatically; an env-file entry overrides a docker -e value).

set -e

# Exec-passthrough: if the first argument looks like a command (starts with /
# or is a known binary on PATH), just exec it directly.  This lets CI smoke
# tests and interactive debugging work without --entrypoint overrides:
#   docker run IMAGE pypy -c "import twisted; print twisted.__version__"
#   docker run IMAGE bash
if [ $# -gt 0 ]; then
    case "$1" in
        /*|pypy|python*|bash|sh) exec "$@" ;;
    esac
fi

: "${RPC_HOST:?RPC_HOST is required (bitcoind hostname or IP)}"
: "${RPC_USER:?RPC_USER is required}"
: "${RPC_PASS:?RPC_PASS is required}"

NETWORK="${NETWORK:-bitcoincash}"
NODE_NAME="${NODE_NAME:-$(hostname)}"

ARGS="--net ${NETWORK} --node-name ${NODE_NAME}"

# Payout address
if [ -n "${PAYOUT_ADDRESS:-}" ] && [ "${PAYOUT_ADDRESS}" != "dynamic" ]; then
    ARGS="${ARGS} --address ${PAYOUT_ADDRESS}"
fi

# Custom ports
if [ -n "${P2POOL_PORT:-}" ]; then
    ARGS="${ARGS} --port ${P2POOL_PORT}"
fi
if [ -n "${WORKER_PORT:-}" ]; then
    ARGS="${ARGS} --worker-port ${WORKER_PORT}"
fi

# Telegram bot — start if either BOT_TOKEN env var is set or the env file exists
_start_bot=0
if [ -n "${BOT_TOKEN:-}" ]; then
    _start_bot=1
fi
if [ -f /etc/p2pool-bot.env ]; then
    _start_bot=1
fi

if [ "${_start_bot}" = "1" ]; then
    ARGS="${ARGS} --run-bot --bot-python /opt/bot-venv/bin/python3"
    if [ -f /etc/p2pool-bot.env ]; then
        ARGS="${ARGS} --bot-env-file /etc/p2pool-bot.env"
    fi
    # BOT_IMPL=ptb | mtproto — propagated from docker -e or the env file
    # (main.py itself also reads BOT_IMPL if --bot-impl is omitted, but
    # passing it on the CLI makes the choice visible in `ps`).
    if [ -n "${BOT_IMPL:-}" ]; then
        ARGS="${ARGS} --bot-impl ${BOT_IMPL}"
    fi
fi

# Extra flags passthrough
if [ -n "${P2POOL_EXTRA_ARGS:-}" ]; then
    # word-split intentional here
    # shellcheck disable=SC2086
    ARGS="${ARGS} ${P2POOL_EXTRA_ARGS}"
fi

# shellcheck disable=SC2086
exec pypy run_p2pool.py ${ARGS} --bitcoind-address "${RPC_HOST}" "${RPC_USER}" "${RPC_PASS}"
