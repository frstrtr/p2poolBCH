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
#   PAYOUT_ADDRESS       mining payout address; "dynamic" uses miner login (default: dynamic)
#   NODE_NAME            human-readable node label shown in shares (default: hostname)
#   P2POOL_PORT          p2pool P2P listen port (default: network default 9349)
#   WORKER_PORT          Stratum+web listen port (default: 9348)
#   P2POOL_EXTRA_ARGS    additional raw flags appended verbatim to the command
#
# Bot environment variables (all optional; bot starts if BOT_TOKEN or /etc/p2pool-bot.env exists):
#   BOT_TOKEN            Telegram bot token (or put it in /etc/p2pool-bot.env)
#   LOCAL_EVENT_PORT     port the bot event receiver listens on (default: 19349)
#
# Mount /etc/p2pool-bot.env (chmod 600) with KEY=VALUE pairs to pass secrets.

set -e

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
fi

# Extra flags passthrough
if [ -n "${P2POOL_EXTRA_ARGS:-}" ]; then
    # word-split intentional here
    # shellcheck disable=SC2086
    ARGS="${ARGS} ${P2POOL_EXTRA_ARGS}"
fi

# shellcheck disable=SC2086
exec pypy run_p2pool.py ${ARGS} "${RPC_HOST}" "${RPC_USER}" "${RPC_PASS}"
