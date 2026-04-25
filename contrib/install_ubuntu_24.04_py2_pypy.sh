#!/usr/bin/env bash
set -euo pipefail
# Interactive installer for p2pool-BCH on Ubuntu 24.04 — PyPy2 + local OpenSSL 1.1.
# Run as root (sudo). All options can be supplied as CLI args or entered interactively.
#
# Usage: sudo ./install_ubuntu_24.04_py2_pypy.sh [OPTIONS]
#
# Options:
#   --user USER          Linux user to install under          (default: $SUDO_USER or ubuntu)
#   --network NET        p2pool network name                  (default: bitcoincash)
#   --rpc-host HOST      bitcoind RPC host                    (default: 127.0.0.1)
#   --rpc-port PORT      bitcoind RPC port                    (default: 8332)
#   --rpc-user RPCUSER   bitcoind RPC username                (default: bitcoinrpc)
#   --rpc-pass RPCPASS   bitcoind RPC password                (no default)
#   --address ADDR       payout BCH address or "dynamic"      (default: dynamic)
#   --bot-token TOKEN    Telegram bot token; enables the bot  (default: skip bot setup)
#   --node-name NAME     label shown in bot alert messages    (default: system hostname)
#   --bootstrap NODE     extra p2pool peer ADDR[:PORT];
#                        may be repeated                      (default: none)
#   --yes / -y           non-interactive; accept all defaults
#   --help               show this help

# ── defaults ──────────────────────────────────────────────────────────────────
INSTALL_USER=""
NETWORK=""
RPC_HOST=""
RPC_PORT=""
RPC_USER=""
RPC_PASS=""
PAYOUT_ADDRESS=""
BOT_TOKEN=""
NODE_NAME=""
BOOTSTRAP_NODES=()
NON_INTERACTIVE=0

# ── helpers ───────────────────────────────────────────────────────────────────
function info { echo "[INFO] $*"; }
function die  { echo "[ERROR] $*" >&2; exit 1; }
function hr   { echo "────────────────────────────────────────────────────────────────────────"; }

# prompt VARNAME "Question" "default"
# Reads interactively if the variable is not already set; respects --yes.
function prompt {
    local -n _pref="$1"
    local msg="$2" default="$3"
    [[ -n "${_pref}" ]] && return
    if [[ "$NON_INTERACTIVE" -eq 1 ]]; then _pref="$default"; return; fi
    local _reply
    read -r -p "  ${msg} [${default}]: " _reply </dev/tty || { _pref="$default"; return; }
    _pref="${_reply:-$default}"
}

# prompt_secret VARNAME "Question"
# Reads without echo; skips if already set or --yes.
function prompt_secret {
    local -n _sref="$1"
    local msg="$2"
    [[ -n "${_sref}" ]] && return
    if [[ "$NON_INTERACTIVE" -eq 1 ]]; then return; fi
    local _reply
    read -r -s -p "  ${msg}: " _reply </dev/tty || true; echo
    _sref="${_reply}"
}

# ── argument parsing ──────────────────────────────────────────────────────────
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --user)         INSTALL_USER="$2"; shift 2;;
        --network)      NETWORK="$2"; shift 2;;
        --rpc-host)     RPC_HOST="$2"; shift 2;;
        --rpc-port)     RPC_PORT="$2"; shift 2;;
        --rpc-user)     RPC_USER="$2"; shift 2;;
        --rpc-pass)     RPC_PASS="$2"; shift 2;;
        --address)      PAYOUT_ADDRESS="$2"; shift 2;;
        --bot-token)    BOT_TOKEN="$2"; shift 2;;
        --node-name)    NODE_NAME="$2"; shift 2;;
        --bootstrap)    BOOTSTRAP_NODES+=("$2"); shift 2;;
        --yes|-y)       NON_INTERACTIVE=1; shift;;
        --help|-h)
            cat <<'HELP'
Interactive installer for p2pool-BCH on Ubuntu 24.04 — PyPy2 + local OpenSSL 1.1.
Run as root (sudo). All options can be supplied as CLI args or entered interactively.

Usage: sudo ./install_ubuntu_24.04_py2_pypy.sh [OPTIONS]

Options:
  --user USER          Linux user to install under          (default: $SUDO_USER or ubuntu)
  --network NET        p2pool network name                  (default: bitcoincash)
  --rpc-host HOST      bitcoind RPC host                    (default: 127.0.0.1)
  --rpc-port PORT      bitcoind RPC port                    (default: 8332)
  --rpc-user RPCUSER   bitcoind RPC username                (default: bitcoinrpc)
  --rpc-pass RPCPASS   bitcoind RPC password                (no default)
  --address ADDR       payout BCH address or "dynamic"      (default: dynamic)
  --bot-token TOKEN    Telegram bot token; enables the bot  (default: skip bot setup)
  --node-name NAME     label shown in bot alert messages    (default: system hostname)
  --bootstrap NODE     extra p2pool peer ADDR[:PORT];
                       may be repeated                      (default: none)
  --yes / -y           non-interactive; accept all defaults
  --help               show this help
HELP
            exit 0;;
        *) die "Unknown argument: $1";;
    esac
done

# ── root check ────────────────────────────────────────────────────────────────
if [[ "$(id -u)" -ne 0 ]]; then
    die "Run this script as root: sudo $0"
fi

# ── interactive prompts ───────────────────────────────────────────────────────
hr
echo "  p2pool-BCH installer — Ubuntu 24.04"
hr
prompt INSTALL_USER "Linux user to install under" "${SUDO_USER:-ubuntu}"
prompt NETWORK      "p2pool network (bitcoincash / bitcoincash_testnet / bitcoin)" "bitcoincash"
echo
echo "  bitcoind / BCHN connection:"
prompt     RPC_HOST "RPC host" "127.0.0.1"
prompt     RPC_PORT "RPC port" "8332"
prompt     RPC_USER "RPC username" "bitcoinrpc"
prompt_secret RPC_PASS "RPC password (input hidden, leave empty to configure later)"
echo
echo "  p2pool payout address (BCH address, or 'dynamic' to pull one from bitcoind):"
prompt PAYOUT_ADDRESS "Payout address" "dynamic"
echo
echo "  Telegram bot notifications (optional — press Enter to skip):"
prompt_secret BOT_TOKEN "Bot token from @BotFather (empty = disable)"
if [[ -n "$BOT_TOKEN" ]]; then
    _hn="$(hostname -s 2>/dev/null || echo p2pool)"
    prompt NODE_NAME "Node name shown in alerts" "$_hn"
fi
echo
echo "  Extra p2pool bootstrap/peer nodes (optional):"
echo "    (already supplied via --bootstrap flags: ${BOOTSTRAP_NODES[*]:-none})"
if [[ "$NON_INTERACTIVE" -eq 0 ]] && [[ "${#BOOTSTRAP_NODES[@]}" -eq 0 ]]; then
    _extra_node=""
    read -r -p "  Add a peer ADDR[:PORT] (or Enter to skip): " _extra_node </dev/tty || true
    [[ -n "$_extra_node" ]] && BOOTSTRAP_NODES+=("$_extra_node")
fi

# ── confirmation ──────────────────────────────────────────────────────────────
hr
echo "  Installation summary:"
echo "    User:            $INSTALL_USER"
echo "    Network:         $NETWORK"
echo "    bitcoind RPC:    ${RPC_USER}@${RPC_HOST}:${RPC_PORT}"
if [[ -n "$RPC_PASS" ]]; then
    echo "    RPC password:    (set)"
else
    echo "    RPC password:    NOT SET — service will need editing before first start"
fi
echo "    Payout address:  $PAYOUT_ADDRESS"
if [[ -n "$BOT_TOKEN" ]]; then
    echo "    Telegram bot:    enabled — /etc/p2pool-bot.env will be written"
    echo "    Node name:       ${NODE_NAME:-$(hostname -s 2>/dev/null || echo p2pool)}"
else
    echo "    Telegram bot:    disabled (no token provided)"
fi
[[ "${#BOOTSTRAP_NODES[@]}" -gt 0 ]] && echo "    Bootstrap nodes: ${BOOTSTRAP_NODES[*]}"
hr

if [[ "$NON_INTERACTIVE" -eq 0 ]]; then
    _confirm=""
    read -r -p "  Proceed with installation? [Y/n]: " _confirm </dev/tty || true
    case "${_confirm,,}" in
        n|no) echo "Aborted."; exit 0;;
    esac
fi

# ── derived paths (computed after collecting config) ──────────────────────────
USER="$INSTALL_USER"
BASE_HOME="/home/${USER}"
P2POOL_DIR="$BASE_HOME/Github/p2pool"
OPENSSL_PREFIX="$BASE_HOME/openssl-1.1"
PYPY_VERSION="pypy2.7-v7.3.20-linux64"
PYPY_BIN="$BASE_HOME/$PYPY_VERSION/bin/pypy"

if [[ ! -d "$BASE_HOME" ]]; then
    die "Home directory $BASE_HOME does not exist. Create user '$USER' first."
fi

info "Updating package lists"
apt-get update

info "Install build and runtime dependencies"
# We run Python package installs under the PyPy runtime (we use PyPy's pip).
# Avoid installing system Python3 packages; only install the required build
# toolchain and libraries needed to compile Python extensions and OpenSSL.
apt-get install -y --no-install-recommends \
  build-essential gcc make perl pkg-config ccache git wget ca-certificates \
  libbz2-dev libreadline-dev libncurses5-dev zlib1g-dev libgdbm-dev libffi-dev \
  libsqlite3-dev liblzma-dev libssl-dev curl chrony logrotate

info "Create directories"
mkdir -p "$BASE_HOME/Github"
chown -R "$USER":"$USER" "$BASE_HOME/Github" || true

info "Download and install PyPy2 runtime"
if [ ! -x "$PYPY_BIN" ]; then
  cd "$BASE_HOME"
  sudo -u "$USER" bash -lc "
    set -e
    if [ ! -f $PYPY_VERSION.tar.bz2 ]; then
      wget -qO $PYPY_VERSION.tar.bz2 https://downloads.python.org/pypy/$PYPY_VERSION.tar.bz2
    fi
    tar -xjf $PYPY_VERSION.tar.bz2
  "
fi

info "Build and install OpenSSL 1.1.1 locally (needed for PyPy cryptography ABI compatibility)"
if [ ! -d "$OPENSSL_PREFIX" ]; then
  TMPDIR=$(mktemp -d)
  cd "$TMPDIR"
  wget -qO openssl-1.1.1u.tar.gz https://www.openssl.org/source/openssl-1.1.1u.tar.gz
  tar xzf openssl-1.1.1u.tar.gz
  cd openssl-1.1.1u
  ./config --prefix="$OPENSSL_PREFIX" no-shared -Wl,-rpath,'$ORIGIN/../lib' && make -j"$(nproc)" && make install_sw
  rm -rf "$TMPDIR"
  chown -R "$USER":"$USER" "$OPENSSL_PREFIX"
fi

info "Clone p2pool repository"
if [ ! -d "$P2POOL_DIR" ]; then
  # ensure we run as the target user from a safe working directory
  sudo -u "$USER" bash -lc "cd \"$BASE_HOME\" || exit 1; git clone https://github.com/jtoomim/p2pool.git \"$P2POOL_DIR\""
fi

# ensure contrib directory exists (git clone may have created the repo, but make sure)
mkdir -p "$P2POOL_DIR/contrib"
chown -R "$USER":"$USER" "$P2POOL_DIR" || true

info "Install p2pool Python dependencies into PyPy site-packages"
export LD_LIBRARY_PATH="$OPENSSL_PREFIX/lib:${LD_LIBRARY_PATH:-}"
export OPENSSL_DIR="$OPENSSL_PREFIX"
export OPENSSL_LIBDIR="$OPENSSL_PREFIX/lib"

PIP_CMD=("$PYPY_BIN" -m pip)

info "Ensure pip is available for PyPy"
sudo -u "$USER" "$PYPY_BIN" -m ensurepip || true
sudo -u "$USER" "${PIP_CMD[@]}" install --upgrade pip setuptools wheel

info "Install cryptography and pyOpenSSL from source so they build against local OpenSSL"
sudo -u "$USER" env LD_LIBRARY_PATH="$OPENSSL_PREFIX/lib" OPENSSL_DIR="$OPENSSL_PREFIX" OPENSSL_LIBDIR="$OPENSSL_PREFIX/lib" "${PIP_CMD[@]}" install --no-binary :all: cryptography pyOpenSSL

info "Install remaining requirements from requirements.txt into PyPy"
if [ -f "$P2POOL_DIR/requirements.txt" ]; then
  # typing backport is required for some packages' setup.py under Python 2 (eg. Twisted)
  sudo -u "$USER" "${PIP_CMD[@]}" install typing || true
  sudo -u "$USER" "${PIP_CMD[@]}" install -r "$P2POOL_DIR/requirements.txt"
fi

info "Create Telegram bot Python 3 venv at $P2POOL_DIR/bot-venv"
# Ubuntu 24.04 enforces PEP 668: pip install to system Python 3 is blocked.
# We create a dedicated venv for the bot so its deps are isolated and the
# path can be passed to p2pool via --bot-python.
BOT_VENV="$P2POOL_DIR/bot-venv"
if [ ! -x "$BOT_VENV/bin/python3" ]; then
  apt-get install -y --no-install-recommends python3 python3-venv
  sudo -u "$USER" python3 -m venv "$BOT_VENV"
fi
sudo -u "$USER" "$BOT_VENV/bin/pip" install --upgrade pip
sudo -u "$USER" "$BOT_VENV/bin/pip" install -r "$P2POOL_DIR/telegram_bot/requirements.txt"
info "Bot venv ready: $BOT_VENV/bin/python3"

info "Create p2pool wrapper script"
cat > "$P2POOL_DIR/contrib/p2pool-run.sh" <<'BASH'
#!/usr/bin/env bash
set -euo pipefail
BASE_DIR="/home/USER_PLACEHOLDER"
OPENSSL_DIR="$BASE_DIR/openssl-1.1"
PYPY_BIN="$BASE_DIR/PYPY_PLACEHOLDER/bin/pypy"
P2POOL_DIR="$BASE_DIR/Github/p2pool"
LOGFILE="$BASE_DIR/p2pool.log"
BOT_VENV="$P2POOL_DIR/bot-venv"
BOT_ENV_FILE="/etc/p2pool-bot.env"
BOT_NODE_NAME="NODE_PLACEHOLDER"
export LD_LIBRARY_PATH="$OPENSSL_DIR/lib:${LD_LIBRARY_PATH:-}"
export OPENSSL_DIR="$OPENSSL_DIR"
export OPENSSL_LIBDIR="$OPENSSL_DIR/lib"
cd "$P2POOL_DIR"
BOT_ARGS=()
if [ -x "$BOT_VENV/bin/python3" ] && [ -f "$BOT_ENV_FILE" ]; then
  BOT_ARGS=(--run-bot --bot-python "$BOT_VENV/bin/python3" --bot-env-file "$BOT_ENV_FILE" --node-name "$BOT_NODE_NAME")
fi
exec "$PYPY_BIN" run_p2pool.py --logfile "$LOGFILE" "${BOT_ARGS[@]}" "$@"
BASH

sed -i "s|USER_PLACEHOLDER|$USER|g" "$P2POOL_DIR/contrib/p2pool-run.sh"
sed -i "s|PYPY_PLACEHOLDER|$PYPY_VERSION|g" "$P2POOL_DIR/contrib/p2pool-run.sh"
_effective_node_name="${NODE_NAME:-$(hostname -s 2>/dev/null || echo p2pool)}"
sed -i "s|NODE_PLACEHOLDER|${_effective_node_name}|g" "$P2POOL_DIR/contrib/p2pool-run.sh"
chmod +x "$P2POOL_DIR/contrib/p2pool-run.sh"

info "Create systemd service unit"
# Build ExecStart from collected configuration
_ADDR_ARGS="--address $PAYOUT_ADDRESS"
[[ "$PAYOUT_ADDRESS" == "dynamic" ]] && _ADDR_ARGS="$_ADDR_ARGS --numaddresses 2"
_RPC_ARGS=""
[[ -n "$RPC_PASS" ]] && _RPC_ARGS=" $RPC_USER $RPC_PASS --bitcoind-address $RPC_HOST --bitcoind-rpc-port $RPC_PORT"
_BOOTSTRAP_ARGS=""
for _bn in "${BOOTSTRAP_NODES[@]+${BOOTSTRAP_NODES[@]}}"; do _BOOTSTRAP_ARGS="$_BOOTSTRAP_ARGS -n $_bn"; done
_EXECSTART="$P2POOL_DIR/contrib/p2pool-run.sh --net $NETWORK $_ADDR_ARGS${_BOOTSTRAP_ARGS}${_RPC_ARGS}"

cat > "$P2POOL_DIR/contrib/p2pool.service" <<UNIT
[Unit]
Description=p2pool (local wrapper)
After=network.target bitcoind.service

[Service]
Type=simple
User=$USER
Group=$USER
WorkingDirectory=$P2POOL_DIR
Environment=LD_LIBRARY_PATH=$OPENSSL_PREFIX/lib
ExecStart=$_EXECSTART
Restart=on-failure
RestartSec=5
StandardOutput=append:$BASE_HOME/p2pool.out
StandardError=append:$BASE_HOME/p2pool.out

[Install]
WantedBy=multi-user.target
UNIT
chmod 644 "$P2POOL_DIR/contrib/p2pool.service"

info "Install and enable systemd service"
cp "$P2POOL_DIR/contrib/p2pool.service" /etc/systemd/system/p2pool.service
systemctl daemon-reload
systemctl enable p2pool.service || true

info "Enable time sync with chrony"
systemctl enable --now chrony || true

info "Installing logrotate config for p2pool logs"
cat > /etc/logrotate.d/p2pool <<'LR'
/home/USER_PLACEHOLDER/p2pool.out /home/USER_PLACEHOLDER/p2pool.log {
    su USER_PLACEHOLDER USER_PLACEHOLDER
    weekly
    rotate 12
    compress
    missingok
    notifempty
    copytruncate
}
LR
sed -i "s|USER_PLACEHOLDER|$USER|g" /etc/logrotate.d/p2pool

info "Installing disk-space alert service and timer"
cat > /usr/local/bin/p2pool-disk-alert.sh <<'SH'
#!/usr/bin/env bash
# simple disk usage alert for root filesystem; logs a warning to syslog when threshold exceeded
THRESHOLD=90
USAGE=$(df / --output=pcent | tail -1 | tr -dc '0-9')
if [ -n "$USAGE" ] && [ "$USAGE" -ge "$THRESHOLD" ]; then
  logger -t p2pool-disk-alert "Disk usage is ${USAGE}% >= ${THRESHOLD}% on / — consider cleaning up or expanding storage"
fi
SH
chmod 755 /usr/local/bin/p2pool-disk-alert.sh

cat > /etc/systemd/system/p2pool-disk-alert.service <<'UNIT'
[Unit]
Description=p2pool disk usage alert

[Service]
Type=oneshot
ExecStart=/usr/local/bin/p2pool-disk-alert.sh
UNIT

cat > /etc/systemd/system/p2pool-disk-alert.timer <<'UNIT'
[Unit]
Description=Run p2pool disk usage alert every hour

[Timer]
OnBootSec=5min
OnUnitActiveSec=1h

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload || true
systemctl enable --now p2pool-disk-alert.timer || true

# Write /etc/p2pool-bot.env if a bot token was provided
if [[ -n "$BOT_TOKEN" ]]; then
    info "Writing /etc/p2pool-bot.env"
    cat > /etc/p2pool-bot.env <<EOF
BOT_TOKEN=$BOT_TOKEN
LOCAL_EVENT_PORT=9349
P2POOL_API_URL=http://127.0.0.1:9348
SUBSCRIPTIONS_FILE=$P2POOL_DIR/telegram_bot/subscriptions.json
EOF
    chmod 600 /etc/p2pool-bot.env
    info "Bot env file written: /etc/p2pool-bot.env"
fi

systemctl daemon-reload
info "Installer completed."
hr
echo "  Installation complete — summary:"
echo "    User:            $USER"
echo "    p2pool dir:      $P2POOL_DIR"
echo "    Network:         $NETWORK"
echo "    bitcoind RPC:    ${RPC_USER}@${RPC_HOST}:${RPC_PORT}"
if [[ -n "$RPC_PASS" ]]; then
    echo "    RPC password:    (set)"
else
    echo "    RPC password:    NOT SET"
fi
echo "    Payout address:  $PAYOUT_ADDRESS"
echo "    Service file:    /etc/systemd/system/p2pool.service"
if [[ -n "$BOT_TOKEN" ]]; then
    echo "    Telegram bot:    enabled — /etc/p2pool-bot.env written"
    echo "    Node name:       ${_effective_node_name}"
else
    echo "    Telegram bot:    disabled"
fi
hr
echo

if [[ -z "$RPC_PASS" ]]; then
    echo "  ⚠  RPC password was not set. Edit the service before starting:"
    echo "       sudo systemctl edit p2pool.service"
    echo "     Paste the following (replacing <PASSWORD>):"
    echo "       [Service]"
    echo "       ExecStart="
    echo "       ExecStart=$_EXECSTART <PASSWORD>"
    echo
fi

echo "  To start p2pool:"
echo "    sudo systemctl start p2pool.service"
echo "    sudo journalctl -u p2pool.service -n 100 --no-pager -f"
echo

if [[ -z "$BOT_TOKEN" ]]; then
    echo "  To enable Telegram bot notifications later:"
    echo "    1. Get a token from @BotFather on Telegram"
    echo "    2. sudo install -m 600 -o $USER $P2POOL_DIR/telegram_bot/.env.example /etc/p2pool-bot.env"
    echo "    3. sudo nano /etc/p2pool-bot.env   # set BOT_TOKEN"
    echo "    4. sudo systemctl restart p2pool.service"
    echo
fi

echo "  Full documentation: INSTALL_UBUNTU_24.04_PY2_PYPY.md"

exit 0
