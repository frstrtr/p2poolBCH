# P2Pool for Dogecoin

This is a port of P2Pool to support Dogecoin mining. P2Pool is a decentralized Bitcoin mining pool that works by creating a peer-to-peer network of miner nodes.

## Features

- **Decentralized mining**: No central pool operator
- **PPLNS payout system**: Pay Per Last N Shares
- **Low variance**: Share chain smooths out payments
- **No registration required**: Mine directly to your Dogecoin address
- **Scrypt POW support**: Native support for Dogecoin's scrypt algorithm

## Requirements

### System Requirements
- Linux (Ubuntu/Debian recommended)
- Python 2.7 or PyPy 2.7 (PyPy recommended for better performance)
- 2GB+ RAM
- 200GB+ disk space for Dogecoin blockchain

### Software Requirements
- **Dogecoin Core 1.14.0 or newer**
- **Python/PyPy 2.7**
- **Build tools** for compiling the scrypt module

## Installation

### 1. Install Dependencies

```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install -y git build-essential python-dev libssl-dev

# For PyPy (recommended)
wget https://downloads.python.org/pypy/pypy2.7-v7.3.20-linux64.tar.bz2
tar xf pypy2.7-v7.3.20-linux64.tar.bz2
```

### 2. Install Dogecoin Core

Download and install Dogecoin Core 1.14.8 or newer:

```bash
# Download Dogecoin Core
wget https://github.com/dogecoin/dogecoin/releases/download/v1.14.8/dogecoin-1.14.8-x86_64-linux-gnu.tar.gz
tar xzf dogecoin-1.14.8-x86_64-linux-gnu.tar.gz
sudo cp dogecoin-1.14.8/bin/* /usr/local/bin/
```

### 3. Configure Dogecoin Core

Create `~/.dogecoin/dogecoin.conf`:

```conf
server=1
rpcuser=your_rpc_username
rpcpassword=your_strong_rpc_password
rpcport=22555
port=22556

# Optional performance settings
dbcache=2000
maxmempool=300
```

Start Dogecoin Core and wait for it to sync:

```bash
dogecoind -daemon
# Check sync status
dogecoin-cli getblockchaininfo
```

### 4. Clone P2Pool

```bash
git clone https://github.com/frstrtr/p2poolBCH.git
cd p2poolBCH
git checkout dogecoin
```

### 5. Build Scrypt Module

Dogecoin uses scrypt POW, so you need to build the litecoin_scrypt module:

```bash
cd litecoin_scrypt
# If using PyPy
~/pypy2.7-v7.3.20-linux64/bin/pypy setup.py install

# If using system Python
python setup.py install

cd ..
```

### 6. Install Python Dependencies

```bash
# If using PyPy
~/pypy2.7-v7.3.20-linux64/bin/pypy -m pip install twisted

# If using system Python
pip install -r requirements.txt
```

## Running P2Pool

### Basic Usage

```bash
# With PyPy (recommended)
~/pypy2.7-v7.3.20-linux64/bin/pypy run_p2pool.py \
    --net dogecoin \
    --address YOUR_DOGECOIN_ADDRESS \
    YOUR_RPC_USERNAME YOUR_RPC_PASSWORD

# With system Python
python run_p2pool.py \
    --net dogecoin \
    --address YOUR_DOGECOIN_ADDRESS \
    YOUR_RPC_USERNAME YOUR_RPC_PASSWORD
```

### Common Options

- `--net dogecoin` - Specifies Dogecoin network
- `--address DOGE_ADDRESS` - Your Dogecoin payout address (required)
- `--give-author PERCENTAGE` - Donation to P2Pool development (default: 0.0, recommended: 1.0)
- `--logfile LOGFILE` - Log file path
- `--bitcoind-rpc-port PORT` - Dogecoin RPC port (default: 22555)

### Example with Options

```bash
~/pypy2.7-v7.3.20-linux64/bin/pypy run_p2pool.py \
    --net dogecoin \
    --give-author 1.0 \
    --logfile p2pool-dogecoin.log \
    --address DNJx6HYka3mncLBaw1TLUARtXxe2BgjHf6 \
    p2poolrpcuser strong_password_here
```

## Systemd Service (Optional)

Create `/etc/systemd/system/dogecoind.service`:

```ini
[Unit]
Description=Dogecoin Core
After=network.target

[Service]
Type=forking
User=your_username
ExecStart=/usr/local/bin/dogecoind -daemon -conf=/home/your_username/.dogecoin/dogecoin.conf
ExecStop=/usr/local/bin/dogecoin-cli stop
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

Create `/etc/systemd/system/p2pool-dogecoin.service`:

```ini
[Unit]
Description=P2Pool Dogecoin
After=network.target dogecoind.service
Requires=dogecoind.service

[Service]
Type=simple
User=your_username
WorkingDirectory=/path/to/p2poolBCH
ExecStart=/path/to/pypy run_p2pool.py --net dogecoin --give-author 1.0 --logfile /home/your_username/p2pool.log --address YOUR_DOGE_ADDRESS YOUR_RPC_USER YOUR_RPC_PASSWORD
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start services:

```bash
sudo systemctl daemon-reload
sudo systemctl enable dogecoind p2pool-dogecoin
sudo systemctl start dogecoind
# Wait for sync, then start P2Pool
sudo systemctl start p2pool-dogecoin
```

## Mining to P2Pool

Once P2Pool is running, point your Dogecoin scrypt miner to your P2Pool node:

### Mining Settings
- **Host**: Your P2Pool server IP
- **Port**: 9555 (default worker port)
- **Username**: Your Dogecoin address
- **Password**: x (or anything)
- **Algorithm**: scrypt

### Example with cpuminer

```bash
cpuminer -a scrypt -o http://YOUR_P2POOL_IP:9555 -u YOUR_DOGE_ADDRESS -p x
```

### Example with cgminer

```bash
cgminer --scrypt -o http://YOUR_P2POOL_IP:9555 -u YOUR_DOGE_ADDRESS -p x
```

## Monitoring

### Web Interface

P2Pool provides a web interface at `http://YOUR_P2POOL_IP:9555/`

The interface shows:
- Current hashrate
- Share statistics
- Expected payout
- Pool efficiency
- Connected miners

### Command Line

Check P2Pool logs:
```bash
tail -f p2pool-dogecoin.log
```

Check Dogecoin sync status:
```bash
dogecoin-cli getblockchaininfo
```

Check P2Pool service status:
```bash
sudo systemctl status p2pool-dogecoin
```

## Network Configuration

### Ports Used
- **22555**: Dogecoin RPC port (local only)
- **22556**: Dogecoin P2P port (needs to be open for incoming connections)
- **8555**: P2Pool P2P port (needs to be open for P2Pool peer connections)
- **9555**: P2Pool worker/stratum port (needs to be open for miners)

### Firewall Configuration

```bash
# Allow Dogecoin P2P
sudo ufw allow 22556/tcp

# Allow P2Pool P2P
sudo ufw allow 8555/tcp

# Allow miners to connect
sudo ufw allow 9555/tcp
```

## Troubleshooting

### P2Pool won't start

1. **Check Dogecoin is synced**:
   ```bash
   dogecoin-cli getblockchaininfo
   ```
   Wait until `verificationprogress` is close to 1.0

2. **Verify RPC connection**:
   ```bash
   dogecoin-cli getblockcount
   ```

3. **Check scrypt module is installed**:
   ```bash
   ~/pypy2.7-v7.3.20-linux64/bin/pypy -c "import ltc_scrypt; print 'OK'"
   ```

### No shares being found

- Check your miner configuration
- Verify miner is connected to correct port (9555)
- Check P2Pool logs for miner connections
- Ensure miner is using scrypt algorithm

### High stale rate

- Check your internet connection
- Reduce miner intensity
- Check system clock is accurate
- Ensure low latency to Dogecoin node

## Technical Details

### Network Parameters
- **Share period**: 15 seconds
- **Share chain length**: 12 hours (2880 shares)
- **Protocol version**: 70003
- **P2P prefix**: c0c0c0c0
- **Block time**: 60 seconds
- **Block reward**: 10,000 DOGE (after block 600,000)

### Share Difficulty
P2Pool automatically adjusts share difficulty to target a 15-second share interval. The difficulty increases as your hashrate increases.

### Payouts
Payouts are included directly in the block's coinbase transaction using PPLNS (Pay Per Last N Shares). When the pool finds a block, all miners who contributed shares in the last 12 hours receive their proportional payout.

## Donation

The P2Pool donation address for Dogecoin is:
**DQ8AwqR2XJE9G5dSEfspJYH7Spre85dj6L**

You can donate by using the `--give-author` parameter (recommended: 1.0% or more):

```bash
--give-author 1.0  # 1% donation
```

## Support & Development

- **Original P2Pool**: https://github.com/p2pool/p2pool
- **This fork**: https://github.com/frstrtr/p2poolBCH
- **Dogecoin branch**: https://github.com/frstrtr/p2poolBCH/tree/dogecoin

## License

P2Pool is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.

## Changes from Standard P2Pool

This Dogecoin implementation includes:
- Scrypt POW support via `litecoin_scrypt` module
- Dogecoin network configuration (ports, prefixes, addresses)
- Protocol version 70003 support for Dogecoin compatibility
- PERSIST=False for solo operation (historic Dogecoin P2Pool nodes are offline)
- 10,000 DOGE block subsidy (post-block 600,000)
- 1-minute block timing
- Dogecoin address format support

## Historical Note

This implementation is based on historical Dogecoin P2Pool configurations from 2014, updated for compatibility with modern Dogecoin Core (v1.14+) and current network conditions. Since the original Dogecoin P2Pool network is no longer active, this implementation operates in solo mode (PERSIST=False).
