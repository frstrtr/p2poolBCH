# Installing p2pool on Ubuntu 24.04 (local-only, PyPy/Python2 support)

This guide documents a tested, local-only path to run the p2pool codebase on Ubuntu 24.04. It focuses on the repository's README recommendation to use PyPy (PyPy2) and explains alternatives (compile Python 2.7, build older OpenSSL) when necessary.

High level choices (recommended order):

- Recommended (fastest, least invasive): Install a local PyPy2 binary in your home directory, bootstrap pip there, and install the p2pool Python requirements into that PyPy environment. This avoids system package conflicts and works well on modern Ubuntu releases.
- Alternative (system-level, more invasive): Build and install Python 2.7 from source (into /usr/local) and install pip, then install requirements. This is more work and may require building an OpenSSL that matches the expected ABI for legacy pyOpenSSL packages.
- Avoid Docker only if you explicitly disallow it (this guide respects local-only requirement).

Prerequisites / assumptions

- You have a regular user account with sudo available for installing build dependencies (the PyPy path described below itself does not require sudo).
- You have at least ~200 MB free for PyPy + packages; building cryptography/pyOpenSSL from source may require more disk space temporarily.
- This guide targets Ubuntu 24.04 ("noble"); adjust apt package names for other releases.

Overview of tasks

1. Install system build dependencies (only required for building native wheels).
2. Download and extract PyPy2 binary into your home directory.
3. Bootstrap pip for PyPy and install Python requirements for p2pool.
4. Create or configure `~/.bitcoin/bitcoin.conf` (RPC credentials) or point p2pool to an external bitcoind RPC endpoint.
5. Start p2pool with the extracted PyPy binary and verify it runs.

Quick (recommended) commands

Run these commands to get a working PyPy-based p2pool quickly. Read the detailed sections below if you want to understand alternatives or troubleshoot.

```bash
cd /usr/local/src
wget https://www.python.org/ftp/python/2.7.18/Python-2.7.18.tgz
# Installing p2pool on Ubuntu 24.04 (local-only, PyPy/Python2 support)

This guide documents a tested, local-only path to run the p2pool codebase on Ubuntu 24.04 using
PyPy2. Modern Ubuntu packages and binary wheels can be incompatible with PyPy2 binary extensions,
so this document shows how to build a local OpenSSL 1.1.1 and build `cryptography`/`pyOpenSSL`
against it. An automated helper is included at `contrib/install_ubuntu_24.04_py2_pypy.sh`.

High level options

- Recommended: install a local PyPy2 binary in your home directory, bootstrap pip there, and
  install the p2pool Python requirements into that PyPy environment. This avoids changing system
  OpenSSL or system Python and is the least invasive option.
- Alternative: build Python 2.7 from source and compile it against a private OpenSSL if you need
  a system-level CPython 2.7. This is more work and more invasive.

Prerequisites / assumptions

- You have a regular user account with sudo available for installing build dependencies and the
  systemd unit (the PyPy runtime installation itself does not require sudo).
- This document uses `/home/user0` as an example home directory. Adjust paths if your user is
  different.

Automated helper (recommended)

We provide an **interactive** installer at `contrib/install_ubuntu_24.04_py2_pypy.sh`.
Run it as root (sudo) on a clean Ubuntu 24.04 VM. It will prompt for all required settings
(user, network, RPC host/port/credentials, payout address, optional Telegram bot token) and
show a confirmation summary before doing anything. RPC credentials and the payout address are
baked directly into the generated systemd unit — no post-install editing or drop-in override is
needed.

Quick start (interactive):

```bash
sudo /home/user0/Github/p2pool/contrib/install_ubuntu_24.04_py2_pypy.sh
```

Or supply all values on the command line and skip prompts:

```bash
sudo /home/user0/Github/p2pool/contrib/install_ubuntu_24.04_py2_pypy.sh \
  --user user0 --network bitcoincash \
  --rpc-host 127.0.0.1 --rpc-port 8332 \
  --rpc-user bitcoinrpc --rpc-pass YOURPASS \
  --address YOUR_BCH_ADDRESS \
  --yes
```

After the script completes, start the service:

```bash
sudo systemctl start p2pool.service
sudo journalctl -u p2pool.service -n 200 --no-pager
```

Manual steps (what the script automates)

1) Install system build dependencies

```bash
sudo apt-get update
sudo apt-get install -y build-essential gcc make perl pkg-config ccache git wget ca-certificates \
  libbz2-dev libreadline-dev libncurses5-dev zlib1g-dev libgdbm-dev libffi-dev libsqlite3-dev \
  python3-venv python3-distutils liblzma-dev libssl-dev curl
```

If pip needs to build `cryptography` from source and asks for Rust, also install:

```bash
sudo apt-get install -y rustc cargo
```

2) Install PyPy2 binary locally (example: `pypy2.7-v7.3.20`)

```bash
cd /home/user0
wget https://downloads.python.org/pypy/pypy2.7-v7.3.20-linux64.tar.bz2
tar -xjf pypy2.7-v7.3.20-linux64.tar.bz2
# binary lives at /home/user0/pypy2.7-v7.3.20-linux64/bin/pypy
/home/user0/pypy2.7-v7.3.20-linux64/bin/pypy --version
```

3) Build OpenSSL 1.1.1 locally (prefix: `/home/user0/openssl-1.1`)

```bash
cd /tmp
wget https://www.openssl.org/source/openssl-1.1.1u.tar.gz
tar xzf openssl-1.1.1u.tar.gz
cd openssl-1.1.1u
./config --prefix=/home/user0/openssl-1.1 no-shared
make -j$(nproc)
make install_sw
```

Notes: installing OpenSSL locally keeps the system OpenSSL untouched. We rely on setting
LD_LIBRARY_PATH so that the PyPy-built `cryptography` extension loads against `/home/user0/openssl-1.1/lib`.

4) Build cryptography and pyOpenSSL for PyPy against the local OpenSSL

```bash
export LD_LIBRARY_PATH=/home/user0/openssl-1.1/lib
export OPENSSL_DIR=/home/user0/openssl-1.1
export OPENSSL_LIBDIR=/home/user0/openssl-1.1/lib
/home/user0/pypy2.7-v7.3.20-linux64/bin/pypy -m ensurepip || true
/home/user0/pypy2.7-v7.3.20-linux64/bin/pypy -m pip install --upgrade pip setuptools wheel
env LD_LIBRARY_PATH=$LD_LIBRARY_PATH OPENSSL_DIR=$OPENSSL_DIR OPENSSL_LIBDIR=$OPENSSL_LIBDIR \
  /home/user0/pypy2.7-v7.3.20-linux64/bin/pypy -m pip install --no-binary :all: cryptography pyOpenSSL
```

5) Install project requirements into PyPy

```bash
/home/user0/pypy2.7-v7.3.20-linux64/bin/pypy -m pip install -r /home/user0/Github/p2pool/requirements.txt
```

6) Create the p2pool wrapper and systemd unit

The wrapper (created at `/home/user0/Github/p2pool/contrib/p2pool-run.sh`) exports `LD_LIBRARY_PATH`
pointing at the local OpenSSL and execs the PyPy binary. The systemd unit
(`/etc/systemd/system/p2pool.service`) is generated with the real RPC credentials, network, and
payout address already embedded in `ExecStart` — collected either interactively or from CLI flags.

If you ran the installer with `--yes` and left `--rpc-pass` empty, the password will be missing from
the `ExecStart` line. In that case add a drop-in override:

```bash
sudo systemctl edit p2pool.service
# Paste:
# [Service]
# ExecStart=
# ExecStart=/home/user0/Github/p2pool/contrib/p2pool-run.sh --net bitcoincash \
#   --address YOUR_ADDR bitcoinrpc YOURPASS
```

Then start the service:

```bash
sudo systemctl start p2pool.service
sudo journalctl -u p2pool.service -n 200 --no-pager
```

Verification

- Confirm the running ExecStart includes the explicit payout address:

```bash
systemctl show -p ExecStart p2pool.service
```

- Check the p2pool log for the printed payout address and normal pool metrics:

```bash
grep -i "Payout address" /home/user0/p2pool.out || tail -n 120 /home/user0/p2pool.out
```

Troubleshooting notes

- If you see an ImportError about undefined symbols from `cryptography` (e.g. `FIPS_mode`), confirm the
  wrapper exports `LD_LIBRARY_PATH` containing `/home/user0/openssl-1.1/lib` and that `cryptography` was
  built with `OPENSSL_DIR=/home/user0/openssl-1.1`.
- If p2pool reports "Check failed! Make sure that you're connected to the right bitcoind...", ensure
  you started p2pool with `--net bitcoincash` for a Bitcoin Cash node.
- Dynamic payout address mode (`--address dynamic`) can trigger RPC compatibility issues on some nodes
  (missing RPC methods) and cause a keypool-related crash; using an explicit payout address avoids that.

Appendix: manual OpenSSL build (advanced)

If you prefer to install OpenSSL 1.1.1 under a different prefix (for example `/opt/openssl-1.1`), follow the
same pattern: configure with `--prefix=...`, install into that prefix, and set `LD_LIBRARY_PATH` plus
`OPENSSL_DIR`/`OPENSSL_LIBDIR` environment variables when building `cryptography` and when running p2pool.

Notes on upgrade and cleanup

- To remove the local PyPy runtime, delete the extracted PyPy directory under your home directory.
- To remove the local OpenSSL, delete the `/home/user0/openssl-1.1` directory (make sure no other process
  depends on it).

The easiest way to reproduce the steps above on a fresh Ubuntu 24.04 machine is to run
`contrib/install_ubuntu_24.04_py2_pypy.sh` interactively. It will prompt for all settings, show a
summary, and generate a ready-to-run service unit with real credentials embedded. Pass `--yes` to
skip all prompts and accept defaults (useful for scripted deployments).
[## Operational additions installed by the automated installer

The included installer now also configures a few operational helpers to make production hosts more robust:

- chrony (time synchronization): the installer installs and enables `chrony` and starts it immediately.
- logrotate: a file `/etc/logrotate.d/p2pool` is installed to rotate `/home/<user>/p2pool.out` and
  `/home/<user>/p2pool.log` weekly (12 retained rotations, compressed, copytruncate).
- disk space alert: a small script `/usr/local/bin/p2pool-disk-alert.sh` and a systemd timer
  `p2pool-disk-alert.timer` are installed. The timer runs hourly and logs a syslog warning when `/` usage
  reaches or exceeds 90%.

Verification commands

Run these commands on the host where you installed p2pool (replace `user0` with your user):

```bash
# chrony / time sync
systemctl status chrony
chronyc tracking || chronyc sourcestats

# logrotate dry-run for p2pool config
logrotate -d /etc/logrotate.d/p2pool

# check the disk-alert timer and recent service logs
systemctl status p2pool-disk-alert.timer
journalctl -u p2pool-disk-alert.service -n 200 --no-pager

# p2pool service status and logs
systemctl show -p ExecStart p2pool.service
journalctl -u p2pool.service -n 200 --no-pager
```

If you prefer different rotation schedules, log paths, or an alert threshold, edit the files created by the
installer (`/etc/logrotate.d/p2pool`, `/usr/local/bin/p2pool-disk-alert.sh`, and the timer/unit) and then
run `systemctl daemon-reload` and `systemctl restart p2pool-disk-alert.timer` to apply changes.

Security and notes

- The disk-alert script is intentionally simple and only logs to syslog; integrate with your monitoring stack
  (Nagios/Prometheus/Alertmanager/etc.) if you need actionable alerts.
- Time synchronization is critical for correct block timestamp handling; ensure chrony is allowed network access.
]

## Telegram bot notifications (optional)

The repo includes a Python 3 Telegram bot (`telegram_bot/`) that sends push notifications to
subscribed miners when workers connect/disconnect, shares are found, or blocks are solved.

The bot runs as a **child process of p2pool** — no separate service is needed. The p2pool wrapper
`contrib/p2pool-run.sh` auto-enables it if two things exist:

1. `bot-venv/` — a Python 3 venv with the bot's dependencies (created by the installer)
2. `/etc/p2pool-bot.env` — a file containing `BOT_TOKEN` and other settings

### What the installer does

The installer (`contrib/install_ubuntu_24.04_py2_pypy.sh`) already:

- Installs `python3` and `python3-venv` via apt
- Creates `<p2pool-dir>/bot-venv/` and runs
  `pip install -r telegram_bot/requirements.txt` inside it
- Updates the `p2pool-run.sh` wrapper to detect the venv and env file and pass
  `--run-bot --bot-python .../bot-venv/bin/python3 --bot-env-file /etc/p2pool-bot.env`
  to p2pool automatically when both are present

### Enabling the bot

1. Create `/etc/p2pool-bot.env` from the template (mode 600, owner matches the service user):

```bash
sudo install -m 600 -o ubuntu /path/to/p2pool/telegram_bot/.env.example /etc/p2pool-bot.env
sudo nano /etc/p2pool-bot.env
```

Minimum required content:

```ini
BOT_TOKEN=<token from @BotFather>
# These have sensible defaults but can be overridden:
LOCAL_EVENT_PORT=9349
P2POOL_API_URL=http://127.0.0.1:9348
SUBSCRIPTIONS_FILE=/home/ubuntu/Github/p2pool/telegram_bot/subscriptions.json
```

2. Restart p2pool:

```bash
sudo systemctl restart p2pool.service
sudo journalctl -u p2pool.service -n 50 --no-pager | grep -i bot
```

You should see a line like:

```
Telegram bot started (PID 12345)
```

### Bot CLI args (manual / advanced)

If you are not using `p2pool-run.sh` you can pass the args directly:

```bash
pypy run_p2pool.py --net bitcoincash \
  --run-bot \
  --bot-env-file /etc/p2pool-bot.env \
  --node-name vm301 \
  [... other args ...]
```

Optional overrides:

| Arg | Default | Purpose |
|-----|---------|--------|
| `--run-bot` | (flag) | Launch bot subprocess |
| `--bot-python PATH` | `python3` | Python 3 interpreter or venv python to use |
| `--bot-env-file PATH` | (none) | File of `KEY=VALUE` env vars for the bot |
| `--local-bot-url URL` | `http://127.0.0.1:9349` | Where p2pool POSTs events |
| `--node-name NAME` | hostname | Identifier shown in alert messages |

### User interaction (Telegram)

Send `/start` to the bot. Set your BCH mining address via the **📝 Set mining address** button,
then toggle individual alert types (connect, disconnect, share, block) with the inline buttons.
Alerts are matched by address — you receive only events for the address you registered.

### Standalone bot (without --run-bot)

If you prefer to run the bot independently, use `telegram_bot/bot.service`:

```bash
sudo cp /path/to/p2pool/telegram_bot/bot.service /etc/systemd/system/p2pool-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now p2pool-bot.service
```

In that case, do **not** pass `--run-bot` to p2pool; pass `--local-bot-url http://127.0.0.1:9349`
instead so that p2pool POSTs events but does not spawn the bot itself.
