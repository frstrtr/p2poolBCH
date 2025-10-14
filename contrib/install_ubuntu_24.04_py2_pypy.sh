#!/usr/bin/env bash
set -euo pipefail
# Automated installer for Ubuntu 24.04 to run p2pool under PyPy2 with a local OpenSSL 1.1
# This script reproduces the manual steps used to get p2pool working on a modern Ubuntu
# where system OpenSSL/cryptography wheels are incompatible with PyPy2 binary extensions.

# Usage: sudo ./install_ubuntu_24.04_py2_pypy.sh --user USERNAME --rpc-host HOST --rpc-port PORT --rpc-user USER --rpc-pass PASS --address ADDRESS

USER=${USER:-user0}
RPC_HOST=""
RPC_PORT=""
RPC_USER=""
RPC_PASS=""
PAYOUT_ADDRESS=""

# provisional BASE_HOME so script doesn't fail under 'set -u' before args are parsed
BASE_HOME="/home/${USER}"
P2POOL_DIR="$BASE_HOME/Github/p2pool"
OPENSSL_PREFIX="$BASE_HOME/openssl-1.1"
PYPY_VERSION="pypy2.7-v7.3.20-linux64"
PYPY_BIN="$BASE_HOME/$PYPY_VERSION/bin/pypy"

function info { echo "[INFO] $*"; }
function die { echo "[ERROR] $*" >&2; exit 1; }

if [ "$(id -u)" -ne 0 ]; then
  die "This script must be run as root (it installs packages and creates systemd units). Use sudo." 
fi


while [ "$#" -gt 0 ]; do
  case "$1" in
    --user) USER="$2"; shift 2;;
    --rpc-host) RPC_HOST="$2"; shift 2;;
    --rpc-port) RPC_PORT="$2"; shift 2;;
    --rpc-user) RPC_USER="$2"; shift 2;;
    --rpc-pass) RPC_PASS="$2"; shift 2;;
    --address) PAYOUT_ADDRESS="$2"; shift 2;;
    --help) echo "Usage: $0 --user USER --rpc-host HOST --rpc-port PORT --rpc-user USER --rpc-pass PASS --address ADDRESS"; exit 0;;
    *) echo "Unknown arg: $1"; exit 1;;
  esac
done

# compute derived paths after parsing --user so sudo-wrapped invocations that pass --user work
BASE_HOME="/home/${USER}"
P2POOL_DIR="$BASE_HOME/Github/p2pool"
OPENSSL_PREFIX="$BASE_HOME/openssl-1.1"
PYPY_VERSION="pypy2.7-v7.3.20-linux64"
PYPY_BIN="$BASE_HOME/$PYPY_VERSION/bin/pypy"

if [ ! -d "$BASE_HOME" ]; then
  die "Home dir $BASE_HOME does not exist. Pass a correct --user or create the user first."
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
export LDFLAGS="-L$OPENSSL_PREFIX/lib"
export CFLAGS="-I$OPENSSL_PREFIX/include"

PIP_CMD=("$PYPY_BIN" -m pip)

info "Ensure pip is available for PyPy"
# Run from a stable directory to avoid getcwd() errors
sudo -u "$USER" bash -lc "cd \"$BASE_HOME\" && \"$PYPY_BIN\" -m ensurepip" || true
sudo -u "$USER" bash -lc "cd \"$BASE_HOME\" && ${PIP_CMD[*]} install --upgrade pip setuptools wheel"

info "Install typing and wheel first (required for Twisted setup.py)"
sudo -u "$USER" bash -lc "cd \"$BASE_HOME\" && ${PIP_CMD[*]} install typing wheel"

info "Install remaining requirements from requirements.txt into PyPy"
if [ -f "$P2POOL_DIR/requirements.txt" ]; then
  sudo -u "$USER" bash -lc "cd \"$BASE_HOME\" && ${PIP_CMD[*]} install -r \"$P2POOL_DIR/requirements.txt\""
fi

info "Rebuild cryptography against local OpenSSL to avoid FIPS_mode undefined symbol error"
sudo -u "$USER" bash -lc "cd \"$BASE_HOME\" && LD_LIBRARY_PATH=\"$OPENSSL_PREFIX/lib\" LDFLAGS=\"-L$OPENSSL_PREFIX/lib\" CFLAGS=\"-I$OPENSSL_PREFIX/include\" ${PIP_CMD[*]} install --no-binary cryptography --ignore-installed --no-deps cryptography==3.3.2"

info "Create p2pool wrapper and systemd unit"
cat > "$P2POOL_DIR/contrib/p2pool-run.sh" <<'BASH'
#!/usr/bin/env bash
set -euo pipefail
BASE_DIR="/home/USER_PLACEHOLDER"
OPENSSL_DIR="$BASE_DIR/openssl-1.1"
PYPY_BIN="$BASE_DIR/PYPY_PLACEHOLDER/bin/pypy"
P2POOL_DIR="$BASE_DIR/Github/p2pool"
LOGFILE="$BASE_DIR/p2pool.log"
export LD_LIBRARY_PATH="$OPENSSL_DIR/lib:${LD_LIBRARY_PATH:-}"
export OPENSSL_DIR="$OPENSSL_DIR"
export OPENSSL_LIBDIR="$OPENSSL_DIR/lib"
cd "$P2POOL_DIR"
exec "$PYPY_BIN" run_p2pool.py --logfile "$LOGFILE" "$@"
BASH

sed -i "s|USER_PLACEHOLDER|$USER|g" "$P2POOL_DIR/contrib/p2pool-run.sh"
sed -i "s|PYPY_PLACEHOLDER|$PYPY_VERSION|g" "$P2POOL_DIR/contrib/p2pool-run.sh"
chmod +x "$P2POOL_DIR/contrib/p2pool-run.sh"

cat > "$P2POOL_DIR/contrib/p2pool.service" <<'UNIT'
[Unit]
Description=p2pool (local wrapper)
After=network.target bitcoind.service

[Service]
Type=simple
User=USER_PLACEHOLDER
Group=USER_PLACEHOLDER
WorkingDirectory=/home/USER_PLACEHOLDER/Github/p2pool
Environment=LD_LIBRARY_PATH=/home/USER_PLACEHOLDER/openssl-1.1/lib
ExecStart=/home/USER_PLACEHOLDER/Github/p2pool/contrib/p2pool-run.sh --net bitcoincash --address dynamic --numaddresses 2 p2poolrpcuser RPCPASSWORD_PLACEHOLDER
Restart=on-failure
RestartSec=5
StandardOutput=append:/home/USER_PLACEHOLDER/p2pool.out
StandardError=append:/home/USER_PLACEHOLDER/p2pool.out

[Install]
WantedBy=multi-user.target
UNIT

sed -i "s|USER_PLACEHOLDER|$USER|g" "$P2POOL_DIR/contrib/p2pool.service"
chmod 644 "$P2POOL_DIR/contrib/p2pool.service"

info "Install systemd unit (the service uses placeholders for rpc creds; edit or create a drop-in to supply real rpcuser/rpcpassword)"
cp "$P2POOL_DIR/contrib/p2pool.service" /etc/systemd/system/p2pool.service
systemctl daemon-reload
systemctl enable p2pool.service || true

# Install and enable time sync (chrony)
info "Enable time sync with chrony"
systemctl enable --now chrony || true

# Create logrotate config for p2pool logs
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

# Create disk-space alert script and systemd timer
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
Unit

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

if [ -n "$RPC_HOST" ] && [ -n "$RPC_PORT" ] && [ -n "$RPC_USER" ] && [ -n "$RPC_PASS" ] && [ -n "$PAYOUT_ADDRESS" ]; then
  info "Creating systemd drop-in override to point at remote RPC and use explicit payout address"
  mkdir -p /etc/systemd/system/p2pool.service.d
  cat > /tmp/p2pool-override.conf <<EOF
[Service]
ExecStart=
ExecStart=$P2POOL_DIR/contrib/p2pool-run.sh --net bitcoincash --address $PAYOUT_ADDRESS $RPC_USER $RPC_PASS --bitcoind-address $RPC_HOST --bitcoind-rpc-port $RPC_PORT
EOF
  mv /tmp/p2pool-override.conf /etc/systemd/system/p2pool.service.d/override.conf
  systemctl daemon-reload
  systemctl restart p2pool.service
  info "Override installed and service restarted"
fi
info "Installer completed."
echo
echo "Next manual steps (important):"
echo " - Edit /etc/systemd/system/p2pool.service or create /etc/systemd/system/p2pool.service.d/override.conf to supply your RPC user/password and explicit payout address (without 'bitcoincash:' prefix)." 
echo " - Example override to set payout address:\n  [Service]\n  ExecStart=\"\"\n  ExecStart=/home/$USER/Github/p2pool/contrib/p2pool-run.sh --net bitcoincash --address <YOUR_ADDR> <rpcuser> <rpcpassword>\n" 
echo " - After editing, run: systemctl daemon-reload && systemctl restart p2pool.service && journalctl -u p2pool.service -n 200 --no-pager"

exit 0
