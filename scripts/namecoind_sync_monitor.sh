#!/usr/bin/env bash
# namecoind_sync_monitor.sh — watch Namecoin Core sync progress and tell
# you the moment it's ready for merged mining.
#
# Until initial block download (IBD) is complete, getauxblock returns
# error -10 ("Namecoin is downloading blocks...") and p2pool's --merged
# flag will keep retrying without producing aux work.  This script polls
# every INTERVAL seconds and prints a one-line status: PID alive, peer
# count, header/block heights, % verified, and a probe of getauxblock.
# When getauxblock starts returning a real JSON object instead of -10,
# the script prints a banner saying you can now wire p2pool to it.
#
# Usage:
#     ./namecoind_sync_monitor.sh                    # default 10s interval
#     ./namecoind_sync_monitor.sh -i 30              # 30s interval
#     ./namecoind_sync_monitor.sh -w p2pool          # use specific wallet
#     ./namecoind_sync_monitor.sh -c /path/to/conf   # custom conf file
#
# Exits with status 0 once the node is ready (getauxblock works), or
# stays running forever if you pass --watch (in which case it just keeps
# tailing status).
#
# Requires: namecoin-cli on PATH, jq (apt install jq) for nicer parsing.

set -u

INTERVAL=10
WALLET="p2pool"
CONF=""
KEEP_WATCHING=0
NO_COLOR=0

while [ $# -gt 0 ]; do
    case "$1" in
        -i|--interval) INTERVAL="$2"; shift 2 ;;
        -w|--wallet)   WALLET="$2"; shift 2 ;;
        -c|--conf)     CONF="-conf=$2"; shift 2 ;;
        --watch)       KEEP_WATCHING=1; shift ;;
        --no-color)    NO_COLOR=1; shift ;;
        -h|--help)
            sed -n '2,/^set -u/p' "$0" | sed 's/^# \?//;$d'
            exit 0
            ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

if [ "$NO_COLOR" = "1" ] || [ ! -t 1 ]; then
    R=""; G=""; Y=""; B=""; D=""; N=""
else
    R=$'\e[31m'; G=$'\e[32m'; Y=$'\e[33m'; B=$'\e[34m'; D=$'\e[2m'; N=$'\e[0m'
fi

if ! command -v namecoin-cli >/dev/null 2>&1; then
    echo "${R}namecoin-cli not found on PATH${N}" >&2
    exit 1
fi
HAVE_JQ=0
command -v jq >/dev/null 2>&1 && HAVE_JQ=1

cli() { namecoin-cli ${CONF} "$@" 2>&1; }
cli_w() { namecoin-cli ${CONF} -rpcwallet="$WALLET" "$@" 2>&1; }

extract() {
    # extract <key> <json> — pulls "key": value out of a JSON blob,
    # trying jq first then falling back to grep/sed.  numbers and bools
    # come back unquoted; strings keep no quotes.
    local key="$1"; local json="$2"
    if [ "$HAVE_JQ" = "1" ]; then
        echo "$json" | jq -r ".${key} // empty" 2>/dev/null
    else
        echo "$json" | grep -oE "\"${key}\"[[:space:]]*:[[:space:]]*[^,}]*" \
            | head -1 | sed -E "s/\"${key}\"[[:space:]]*:[[:space:]]*//;s/^[\"]//;s/[\"]$//"
    fi
}

print_banner_ready() {
    echo ""
    echo "${G}╔════════════════════════════════════════════════════════════════════╗${N}"
    echo "${G}║  NAMECOIND READY FOR MERGED MINING                                 ║${N}"
    echo "${G}║                                                                    ║${N}"
    echo "${G}║  getauxblock now returns work.  You can wire p2pool to it:         ║${N}"
    echo "${G}║                                                                    ║${N}"
    echo "${G}║    --merged http://USER:PASS@127.0.0.1:8336/                       ║${N}"
    echo "${G}║                                                                    ║${N}"
    echo "${G}║  Add the flag to P2POOL_EXTRA_ARGS, restart the docker container,  ║${N}"
    echo "${G}║  watch the log for: 'Got new merged mining work!'                  ║${N}"
    echo "${G}╚════════════════════════════════════════════════════════════════════╝${N}"
    echo ""
}

ts() { date '+%H:%M:%S'; }

WAS_READY=0

while true; do
    # 1. Daemon alive?
    if ! pidof namecoind >/dev/null 2>&1; then
        echo "[$(ts)] ${R}DEAD${N}  namecoind is not running"
        if [ "$KEEP_WATCHING" = "1" ]; then sleep "$INTERVAL"; continue; fi
        sleep "$INTERVAL"; continue
    fi

    # 2. RPC reachable?
    chain_info=$(cli getblockchaininfo)
    if echo "$chain_info" | head -1 | grep -qiE 'error|could not'; then
        echo "[$(ts)] ${Y}STARTING${N} RPC not ready yet (${D}${chain_info%%$'\n'*}${N})"
        sleep "$INTERVAL"; continue
    fi

    blocks=$(extract blocks "$chain_info")
    headers=$(extract headers "$chain_info")
    progress=$(extract verificationprogress "$chain_info")
    ibd=$(extract initialblockdownload "$chain_info")
    chain=$(extract chain "$chain_info")
    size_mb=$(extract size_on_disk "$chain_info")

    # progress as integer percent
    if [ -n "$progress" ]; then
        pct=$(awk -v p="$progress" 'BEGIN{printf "%.2f", p*100}')
    else
        pct="?"
    fi

    # 3. peer count
    peers=$(cli getconnectioncount 2>/dev/null | tr -d '[:space:]')
    [ -z "$peers" ] && peers="?"

    # 4. probe getauxblock
    aux=$(cli_w getauxblock 2>&1)
    if echo "$aux" | grep -q '"hash"'; then
        ready=1
        aux_state="${G}OK${N}"
    elif echo "$aux" | grep -qi 'downloading blocks'; then
        ready=0
        aux_state="${Y}IBD${N}"
    elif echo "$aux" | grep -qi 'wallet'; then
        ready=0
        aux_state="${R}NOWALLET${N}"
    elif echo "$aux" | grep -qi 'loading'; then
        ready=0
        aux_state="${Y}LOADING${N}"
    else
        ready=0
        aux_state="${R}ERR${N}"
    fi

    size_str=""
    if [ -n "$size_mb" ] && [ "$size_mb" != "" ]; then
        size_str=$(awk -v s="$size_mb" 'BEGIN{printf "%.0fMB", s/1048576}')
    fi

    printf '[%s] %sALIVE%s chain=%s blocks=%s/%s pct=%s%% peers=%s aux=%s %s\n' \
        "$(ts)" "$G" "$N" "${chain:-?}" "${blocks:-?}" "${headers:-?}" \
        "$pct" "$peers" "$aux_state" "${D}${size_str}${N}"

    if [ "$ready" = "1" ] && [ "$WAS_READY" = "0" ]; then
        print_banner_ready
        WAS_READY=1
        if [ "$KEEP_WATCHING" != "1" ]; then exit 0; fi
    fi

    sleep "$INTERVAL"
done
