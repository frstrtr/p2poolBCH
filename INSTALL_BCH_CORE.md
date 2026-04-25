# Install and Sync Bitcoin Cash Node (BCHN) on Ubuntu/Debian

This guide gets you from zero to a fully synced BCH full node ready for p2pool.

---

## Requirements

- Ubuntu 22.04 / 24.04 (or Debian 12+)
- ~250 GB free disk space (for the full BCH blockchain)
- 4 GB RAM minimum (8 GB recommended)

---

## Step 1 — Download BCHN

Go to the [BCHN download page](https://bitcoincashnode.org/en/download) and grab the latest Linux binary, or use the terminal:

```bash
wget https://github.com/bitcoin-cash-node/bitcoin-cash-node/releases/download/v29.0.0/bitcoin-cash-node-29.0.0-x86_64-linux-gnu.tar.gz
```

Verify the checksum (recommended):

```bash
wget https://github.com/bitcoin-cash-node/bitcoin-cash-node/releases/download/v29.0.0/SHA256SUMS.asc
sha256sum --check --ignore-missing SHA256SUMS.asc
```

---

## Step 2 — Extract and Install

```bash
tar xzf bitcoin-cash-node-29.0.0-x86_64-linux-gnu.tar.gz
sudo install -m 0755 bitcoin-cash-node-29.0.0/bin/bitcoind /usr/local/bin/
sudo install -m 0755 bitcoin-cash-node-29.0.0/bin/bitcoin-cli /usr/local/bin/
```

---

## Step 3 — Configure

Create the config directory and file:

```bash
mkdir -p ~/.bitcoin
cat > ~/.bitcoin/bitcoin.conf << 'EOF'
# Run as background daemon
daemon=1

# Enable JSON-RPC server (required for p2pool)
server=1
rpcuser=bitcoinrpc
rpcpassword=CHANGE_THIS_TO_A_STRONG_PASSWORD

# Allow p2pool to connect (localhost only)
rpcallowip=127.0.0.1

# Transaction index (needed for some p2pool features)
txindex=1

# Limit memory usage (adjust to your RAM)
dbcache=512
EOF
```

**Important:** replace `CHANGE_THIS_TO_A_STRONG_PASSWORD` with a long random string.

---

## Step 4 — Start bitcoind

```bash
bitcoind
```

Check it started:

```bash
bitcoin-cli getblockchaininfo
```

You should see output with `"chain": "main"` and `"blocks"` increasing over time.

---

## Step 5 — Monitor Sync Progress

```bash
# Show current block height vs network tip
bitcoin-cli getblockchaininfo | grep -E '"blocks"|"headers"|"verificationprogress"'
```

`verificationprogress` reaches `1.0` when fully synced. Initial sync takes **6–24 hours** depending on hardware and internet speed.

To watch sync in real time:

```bash
watch -n 10 'bitcoin-cli getblockchaininfo | grep -E "blocks|headers|verificationprogress"'
```

---

## Step 6 — (Optional) Run as a systemd service

```bash
sudo tee /etc/systemd/system/bitcoind.service > /dev/null << EOF
[Unit]
Description=Bitcoin Cash Node
After=network.target

[Service]
ExecStart=/usr/local/bin/bitcoind
User=$USER
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now bitcoind
```

Check status:

```bash
sudo systemctl status bitcoind
journalctl -u bitcoind -f
```

---

## Next Step

Once `verificationprogress` shows `0.9999` or higher, your node is ready.
Follow [INSTALL_UBUNTU_24.04_PY2_PYPY.md](INSTALL_UBUNTU_24.04_PY2_PYPY.md) to install and start p2pool.

Your RPC credentials for p2pool will be:
- `--bitcoind-address 127.0.0.1`
- `--bitcoind-rpc-port 8332`
- `--bitcoind-p2p-port 8333`
- `--bitcoind-rpc-username bitcoinrpc`
- `--bitcoind-rpc-password <the password you set above>`
