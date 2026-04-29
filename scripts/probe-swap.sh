#!/bin/bash
# probe-swap.sh — temporarily replace live p2pool stratum with the diagnostic
# probe on the same port (9348), so miners hit it without changing pool URL.
#
# Usage:
#   sudo ./scripts/probe-swap.sh <variant> [extra probe flags...]
#
#   variants:
#     baseline   — current production behaviour (flat subscribe, vroll on, ka 120s)
#     nicehash   — STRATUM_NICEHASH_COMPAT=1 (nested subscribe form)
#     noboost    — nicehash + STRATUM_DISABLE_ASICBOOST=1 (no version-rolling)
#     nopings    — noboost + STRATUM_DISABLE_LATENCY_PING=1 (keepalive off)
#
# The container `${CONTAINER:-p2pool-bch}` is stopped before the probe binds
# 9348 and restarted unconditionally on exit (EXIT trap, fires on Ctrl-C,
# crashes, kill, etc.).

set -u

CONTAINER="${CONTAINER:-p2pool-bch}"
PORT="${PORT:-9348}"
PROBE="$(dirname "$0")/stratum_probe_server.py"

if [ $# -lt 1 ]; then
    sed -n '/^# Usage/,/^$/p' "$0" | sed 's/^# \?//'
    exit 2
fi

VARIANT="$1"; shift

case "$VARIANT" in
    baseline) FLAGS=() ;;
    nicehash) FLAGS=(--subscribe-form=nested) ;;
    noboost)  FLAGS=(--subscribe-form=nested --no-version-rolling) ;;
    nopings)  FLAGS=(--subscribe-form=nested --no-version-rolling --keepalive-interval=0) ;;
    *) echo "unknown variant: $VARIANT (use baseline|nicehash|noboost|nopings)" >&2; exit 2 ;;
esac

CAPTURE="probe-${VARIANT}-$(date +%Y%m%d-%H%M%S).log"
echo "[probe-swap] variant=$VARIANT  capture=$CAPTURE"
echo "[probe-swap] flags: ${FLAGS[*]:-(none)}  extra: $*"

restore() {
    rc=$?
    echo "[probe-swap] restoring — restarting $CONTAINER"
    docker start "$CONTAINER" >/dev/null && echo "[probe-swap] $CONTAINER back up" \
        || echo "[probe-swap] WARNING: docker start $CONTAINER failed — fix manually"
    exit "$rc"
}
trap restore EXIT

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    echo "[probe-swap] $CONTAINER is not running — aborting (refusing to start probe blind)" >&2
    exit 1
fi

echo "[probe-swap] stopping $CONTAINER ..."
if ! docker stop "$CONTAINER" >/dev/null; then
    echo "[probe-swap] docker stop failed — aborting" >&2
    exit 1
fi

# Brief pause so the kernel releases :9348 before we try to bind.
for _ in 1 2 3 4 5; do
    if ! ss -tln "sport = :$PORT" 2>/dev/null | grep -q ":$PORT"; then break; fi
    sleep 1
done

echo "[probe-swap] launching probe on :$PORT — Ctrl-C to stop & restore"
python3 "$PROBE" --port "$PORT" --capture "$CAPTURE" "${FLAGS[@]}" "$@"
