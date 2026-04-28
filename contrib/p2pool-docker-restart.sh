#!/bin/sh
# p2pool-docker-restart.sh — pull latest image and recreate the container.
#
# Assumes BCHN runs on the SAME host (uses --network host and
# RPC_HOST=127.0.0.1).  Edit the SETTINGS block below or override any
# variable from the calling environment, e.g.:
#
#     RPC_PASS=hunter2 PAYOUT_ADDRESS=bitcoincash:qp... \
#         ./contrib/p2pool-docker-restart.sh
#
# Or copy this script to /usr/local/bin/p2pool-restart and edit the
# inline defaults — the share-chain volume p2pool-data is preserved
# across restarts so the 'Loading shares' phase only runs once.
#
# To switch to the MTProto / Telethon bot variant (supports MTProto
# Telegram-app proxies), set BOT_IMPL=mtproto plus MTPROTO_API_ID,
# MTPROTO_API_HASH, and either MTPROXY_HOST/PORT/SECRET (MTProxy) or
# BOT_PROXY (HTTP/SOCKS5).  See TELEGRAM_BOT.md for the walkthrough.

set -e

# ── SETTINGS — edit these or override via environment ───────────────────────
: "${IMAGE:=ghcr.io/frstrtr/p2poolbch:latest}"
: "${CONTAINER:=p2pool-bch}"
: "${RPC_HOST:=127.0.0.1}"
: "${RPC_USER:=bitcoinrpc}"
: "${RPC_PASS:=changeme}"
: "${PAYOUT_ADDRESS:=dynamic}"
: "${BOT_TOKEN:=}"             # leave empty to start without the bot
: "${BOT_IMPL:=}"              # blank = ptb (default); set "mtproto" to use Telethon
: "${P2POOL_EXTRA_ARGS:=--give-author 0}"

# ── Stop, remove, pull, run ────────────────────────────────────────────────
docker stop "${CONTAINER}" 2>/dev/null || true
docker rm   "${CONTAINER}" 2>/dev/null || true
docker pull "${IMAGE}"

ARGS="-d --restart unless-stopped --network host --name ${CONTAINER}"
ARGS="${ARGS} -e RPC_HOST=${RPC_HOST}"
ARGS="${ARGS} -e RPC_USER=${RPC_USER}"
ARGS="${ARGS} -e RPC_PASS=${RPC_PASS}"
ARGS="${ARGS} -e PAYOUT_ADDRESS=${PAYOUT_ADDRESS}"
ARGS="${ARGS} -e P2POOL_EXTRA_ARGS=${P2POOL_EXTRA_ARGS}"
ARGS="${ARGS} -v p2pool-data:/p2pool/data"

# Bot env passthrough — only set what the user provided so empty values
# don't override defaults inside the container.
[ -n "${BOT_TOKEN}" ]            && ARGS="${ARGS} -e BOT_TOKEN=${BOT_TOKEN}"
[ -n "${BOT_IMPL}" ]             && ARGS="${ARGS} -e BOT_IMPL=${BOT_IMPL}"
[ -n "${MTPROTO_API_ID:-}" ]     && ARGS="${ARGS} -e MTPROTO_API_ID=${MTPROTO_API_ID}"
[ -n "${MTPROTO_API_HASH:-}" ]   && ARGS="${ARGS} -e MTPROTO_API_HASH=${MTPROTO_API_HASH}"
[ -n "${MTPROXY_HOST:-}" ]       && ARGS="${ARGS} -e MTPROXY_HOST=${MTPROXY_HOST}"
[ -n "${MTPROXY_PORT:-}" ]       && ARGS="${ARGS} -e MTPROXY_PORT=${MTPROXY_PORT}"
[ -n "${MTPROXY_SECRET:-}" ]     && ARGS="${ARGS} -e MTPROXY_SECRET=${MTPROXY_SECRET}"
[ -n "${BOT_PROXY:-}" ]          && ARGS="${ARGS} -e BOT_PROXY=${BOT_PROXY}"
[ -n "${BROADCAST_CHANNEL_ID:-}" ] && ARGS="${ARGS} -e BROADCAST_CHANNEL_ID=${BROADCAST_CHANNEL_ID}"

# shellcheck disable=SC2086
docker run ${ARGS} "${IMAGE}"

echo "---"
docker logs --tail=20 "${CONTAINER}" 2>&1 | tail -20
echo "---"
echo "Watch logs: docker logs -f ${CONTAINER}"
