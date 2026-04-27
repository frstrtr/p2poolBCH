**Ubuntu 24.04 automated installer (recommended)**

For Ubuntu 24.04 systems, we provide an interactive installer script that sets up PyPy2, builds a
local OpenSSL 1.1, and configures p2pool with systemd integration.

Quick start (interactive — prompts for all settings):

```bash
sudo ./contrib/install_ubuntu_24.04_py2_pypy.sh
```

Or supply everything on the command line for unattended installs:

```bash
sudo ./contrib/install_ubuntu_24.04_py2_pypy.sh \
  --user ubuntu --network bitcoincash \
  --rpc-host BITCOIND_HOST --rpc-port 8332 \
  --rpc-user RPCUSER --rpc-pass RPCPASS \
  --address YOUR_PAYOUT_ADDRESS \
  --yes
```

The installer:
- Asks for all settings interactively (with sensible defaults) and shows a confirmation summary
- Downloads and installs PyPy2 runtime and builds OpenSSL 1.1 locally
- Installs all Python dependencies into PyPy
- Creates `p2pool-run.sh` and a ready-to-use systemd service with real credentials baked in
- Creates the Telegram bot Python 3 venv and writes `/etc/p2pool-bot.env` if a bot token is provided
- Configures chrony (time sync), logrotate, and disk-space monitoring

For detailed instructions, troubleshooting, and manual setup steps, see `INSTALL_UBUNTU_24.04_PY2_PYPY.md`.
For Telegram bot setup and usage, see `TELEGRAM_BOT.md`.

---

**Quick start: Docker + Telegram bot (new user)**

Everything you need to run your own p2pool-BCH node with Telegram notifications,
using the pre-built GHCR image — no compilation required.

**Prerequisites**
- A running **Bitcoin Cash Node (BCHN)** with JSON-RPC enabled.  
  In `bitcoin.conf`:
  ```
  server=1
  rpcuser=YOUR_RPC_USER
  rpcpassword=YOUR_RPC_PASS
  rpcallowip=0.0.0.0/0    # tighten to your Docker subnet in production
  ```
- **Docker** installed on the host:
  ```bash
  sudo apt update && sudo apt install -y docker.io
  sudo systemctl enable --now docker
  sudo usermod -aG docker $USER
  newgrp docker   # apply group without logging out; or log out and back in
  ```
- A **Telegram bot token** — message [@BotFather](https://t.me/BotFather), send `/newbot`,
  follow the prompts, and copy the token it gives you (`123456:ABC-DEF...`).

**Steps**

1. Pull the image:
   ```bash
   docker pull ghcr.io/frstrtr/p2poolbch:latest
   ```

2. Run it (replace the placeholder values):
   ```bash
   docker run -d --restart unless-stopped \
     --network host \
     -e RPC_HOST=<BCHN_IP> \
     -e RPC_USER=<rpcuser> \
     -e RPC_PASS=<rpcpassword> \
     -e PAYOUT_ADDRESS=<your_BCH_address> \
     -e BOT_TOKEN=<token_from_BotFather> \
     --name p2pool-bch \
     ghcr.io/frstrtr/p2poolbch:latest
   ```
   > Use `--network host` when p2pool and BCHN run on the same machine or LAN.  
   > For bridge networking add `-p 9348:9348 -p 9349:9349` instead.

3. Verify it started:
   ```bash
   docker logs -f p2pool-bch
   ```
   You should see `...success!` for both the RPC and P2P BCHN connections,
   followed by `Telegram bot started`.

4. Open Telegram, find your bot by the username you gave BotFather, and send `/start`.
   - Tap **📝 Set mining address** and enter your BCH address.
   - Toggle notifications on/off with the inline buttons.
   - Point your miner at `<host_ip>:9348` (stratum).

5. *(Optional)* Forward port **9349** on your router to the host for better P2P peer connectivity.

See `TELEGRAM_BOT.md` for the full bot reference (broadcast channels, env-var list, troubleshooting).

---

**P2pool installation with pypy -- Windows**


On Windows, pypy is only supported via the Windows Subsystem for Linux (WSL). P2pool on pypy on WSL is much faster than P2pool on
CPython on native Windows. To install WSL, first follow the steps outlined here:


https://msdn.microsoft.com/en-us/commandline/wsl/install_guide


Once you've done that, run bash and follow the rest of the steps below.


**P2pool installation with pypy -- Linux and Windows**


Copy and paste the following commands into a bash shell in order to install p2pool on Windows or Linux.

>sudo apt-get update

>sudo apt-get -y install pypy pypy-dev pypy-setuptools gcc build-essential git


>wget https://bootstrap.pypa.io/ez_setup.py -O - | sudo pypy
>sudo rm setuptools-*.zip


>wget https://pypi.python.org/packages/source/z/zope.interface/zope.interface-4.1.3.tar.gz#md5=9ae3d24c0c7415deb249dd1a132f0f79

>tar zxf zope.interface-4.1.3.tar.gz

>cd zope.interface-4.1.3/

>sudo pypy setup.py install

>cd ..

>sudo rm -r zope.interface-4.1.3*


>wget https://pypi.python.org/packages/source/T/Twisted/Twisted-15.4.0.tar.bz2

>tar jxf Twisted-15.4.0.tar.bz2

>cd Twisted-15.4.0

>sudo pypy setup.py install

>cd ..

>sudo rm -r Twisted-15.4.0*


>git clone https://github.com/jtoomim/p2pool.git

>cd p2pool


You'll also need to install and run your bitcoind or altcoind of choice, and edit ~/.bitcoin/bitcoin.conf (or the corresponding file for litecoin or whatever other coin you intend to mine) with your bitcoind's RPC username and password. Launch your bitcoind or altcoind, and after it has finished downloading blocks and syncing, go to your p2pool directory and run


>pypy run_p2pool.py

**Docker (recommended for containerised deployments)**

The included multi-stage `Dockerfile` builds a self-contained image with PyPy 2.7, a locally-compiled OpenSSL 1.1, and an optional Telegram bot venv.  All p2pool settings are passed via environment variables — no config file editing required.

Build the image:

```bash
docker build -t p2pool-bch .
```

Run against a remote BCHN node:

```bash
docker run -d --restart unless-stopped \
  -e RPC_HOST=192.168.86.110 \
  -e RPC_USER=bitcoinrpc \
  -e RPC_PASS=YOURPASS \
  -e PAYOUT_ADDRESS=YOUR_BCH_ADDRESS \
  -e NETWORK=bitcoincash \
  -p 9348:9348 \
  -p 9349:9349 \
  --name p2pool-bch \
  p2pool-bch
```

Key environment variables (`docker-entrypoint.sh`):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `RPC_HOST` | ✓ | — | bitcoind hostname or IP |
| `RPC_USER` | ✓ | — | bitcoind RPC username |
| `RPC_PASS` | ✓ | — | bitcoind RPC password |
| `PAYOUT_ADDRESS` | — | `dynamic` | BCH payout address; `dynamic` = use miner login name |
| `NETWORK` | — | `bitcoincash` | p2pool network (e.g. `bitcoincash_testnet`) |
| `WORKER_PORT` | — | `9348` | Stratum + web UI port |
| `P2POOL_PORT` | — | `9349` | p2pool P2P network port |
| `NODE_NAME` | — | hostname | Human-readable node label in shares |
| `BOT_TOKEN` | — | — | Telegram bot token (or mount `/etc/p2pool-bot.env`) |
| `P2POOL_EXTRA_ARGS` | — | — | Extra flags appended verbatim to the command line |

Ports: **9348** (stratum/web UI — miners connect here), **9349** (p2pool P2P — forward from router for better peer connectivity).

Smoke test:

```bash
docker run --rm p2pool-bch pypy run_p2pool.py --help
```

**Container networking modes**

Networking is not baked into the image — it is chosen at runtime.  Pick whichever mode fits your setup:

| Mode | How | Best for |
|------|-----|----------|
| **host** | `--network host` | Dedicated VM or bare-metal host — simplest, no NAT, no port-forwarding needed |
| **macvlan** (bridged LAN) | `docker-compose.yml` (included) | Container gets its own LAN IP and MAC; miners reach it directly |
| **bridge + `-p`** | `-p 9348:9348 -p 9349:9349` | Shared host, multiple containers; requires router port-forward for P2P |

*Host networking* (recommended for a dedicated VM):
```bash
docker run -d --restart unless-stopped \
  --network host \
  -e RPC_HOST=192.168.86.110 \
  -e RPC_USER=bitcoinrpc \
  -e RPC_PASS=YOURPASS \
  -e PAYOUT_ADDRESS=YOUR_BCH_ADDRESS \
  --name p2pool-bch \
  p2pool-bch
```

*Macvlan (bridged LAN)* — container appears on LAN with its own IP, no port-mapping needed:
```bash
# Edit docker-compose.yml first:
#   parent: eth0          → your host's LAN interface (ip -br link)
#   ipv4_address          → a free static LAN IP
#   RPC_HOST              → your BCHN node's IP
docker compose up -d
```
> **macvlan host↔container caveat:** the Docker host itself cannot reach a macvlan container by default.
> Create a shim to fix this:
> ```bash
> ip link add macvlan-shim link eth0 type macvlan mode bridge
> ip addr add 192.168.86.241/32 dev macvlan-shim
> ip link set macvlan-shim up
> ip route add 192.168.86.240/32 dev macvlan-shim
> ```

**Pre-built image from GitHub Container Registry (ghcr.io)**

Every push to `master` and every `v*` release tag is automatically built and pushed to GHCR by the included GitHub Actions workflow (`.github/workflows/docker.yml`).  No local build required — just pull and run:

```bash
# latest master build
docker pull ghcr.io/frstrtr/p2poolbch:latest

docker run -d --restart unless-stopped \
  -e RPC_HOST=192.168.86.110 \
  -e RPC_USER=bitcoinrpc \
  -e RPC_PASS=YOURPASS \
  -e PAYOUT_ADDRESS=YOUR_BCH_ADDRESS \
  -p 9348:9348 \
  -p 9349:9349 \
  --name p2pool-bch \
  ghcr.io/frstrtr/p2poolbch:latest
```

Available tags:

| Tag | Source |
|-----|--------|
| `latest` | latest `master` build |
| `master` | same as `latest` |
| `vX.Y.Z` | release tag (semver) |
| `sha-<short>` | specific commit SHA |

The workflow also runs a smoke test on every PR (build only, no push) to catch regressions before merge.

**jtoomimnet vs mainnet**


If you wish to use the original forrestv btc mainnet instead of jtoomimnet, then replace


>git clone https://github.com/jtoomim/p2pool.git

>cd p2pool


above with


>git clone https://github.com/p2pool/p2pool.git

>cd p2pool


Note: The BTC p2pools currently have low hashrate, which means that payouts will be infrequent, large, and unpredictable. As of Feb 2018, blocks are found on jtoomimnet on average once every 25 days, and blocks are found on mainnet on average once every 108 days. Do not mine on BTC p2pool unless you are very patient and can tolerate receiving no revenue for several months.


**Miner setup**


P2pool communicates with miners via the stratum protocol. For BTC, configure your miners with the following information:


>URL: stratum+tcp://(Your node's IP address or hostname):9332

>Worker: (Your bitcoin address)

>Password: x



Mining to Legacy (P2PKH), SegWit/MultiSig (P2SH) and Bech32 addresses are supported for the following coins with the specified address prefixes:

|Coin		|P2PKH	|P2SH	|Bech32				|
|---------------|-------|-------|-------------------------------|
|Bitcoin	|`1...`	|`3...`	|`bc1...`			|
|Bitcoin Cash*	|`1...`	| (test)|`bitcoincash:q...` or `q...`	|
|Bitcoin SV*	|`1...`	| (test)|`bitcoincash:q...` or `q...`	| 
|Litecoin	|`L...`	|`M...`	|`ltc1...`			|
* Bitcoin Cash and Bitcoin SV uses cashaddr instead of Bech32

**Only Legacy addresses (P2PKH) are supported for coins not mentioned above. If you use an address that p2pool cannot understand, then p2pool will mine to that node's default address instead.**


If you wish to modify the mining difficulty, you may add something like "address+4096" after your mining address to set the pseudoshare difficulty to 4096, or "address/65536" to set the actual share difficulty to 65536 or the p2pool minimum share difficulty, whichever is higher. Pseudoshares only affect hashrate statistics, whereas actual shares affect revenue variance and efficiency.


**Firewall considerations**


If your node is behind a firewall or behind NAT (i.e. on a private IP address), you may want to forward ports to your p2pool server. P2pool uses two ports: one for p2p communication with the p2pool network, and another for both the web UI and for stratum communication with workers. For Bitcoin, those ports are 9333 (p2p) and 9332 (stratum/web). For Litecoin, they are 9326 (p2p) and 9327 (stratum/web). For Bitcoin Cash, they are 9349 (p2p) and 9348 (stratum/web).
