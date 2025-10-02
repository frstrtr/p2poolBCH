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

We provide an installer script that automates the steps below: `contrib/install_ubuntu_24.04_py2_pypy.sh`.
Run it as root (sudo) on a clean Ubuntu 24.04 VM to perform package installation, PyPy extraction,
local OpenSSL build, cryptography build for PyPy, and creation of a wrapper and systemd service
template. The service template uses placeholders for RPC credentials and payout address; you must
edit the unit or create a drop-in to set real values before starting the pool.

Quick start (automated):

```bash
sudo /home/user0/Github/p2pool/contrib/install_ubuntu_24.04_py2_pypy.sh
```

After the script completes, edit the installed systemd unit or create a drop-in override to set
the RPC credentials from `/home/user0/.bitcoin/bitcoin.conf` and an explicit payout address (do
not include the `bitcoincash:` prefix). Then reload and restart systemd:

```bash
sudo systemctl daemon-reload
sudo systemctl restart p2pool.service
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

6) Create the p2pool wrapper and the systemd unit template

The wrapper (created at `/home/user0/Github/p2pool/contrib/p2pool-run.sh`) exports the LD_LIBRARY_PATH
pointing at the local OpenSSL and then execs the PyPy binary to run `run_p2pool.py`. The installer also
places a systemd service template at `/etc/systemd/system/p2pool.service` (copied from
`contrib/p2pool.service`).

Important: before starting the service, replace the placeholder RPC credentials and set an explicit
payout address (without the `bitcoincash:` prefix) in the unit or better, in a drop-in override `
/etc/systemd/system/p2pool.service.d/override.conf` like:

```ini
[Service]
ExecStart=
ExecStart=/home/user0/Github/p2pool/contrib/p2pool-run.sh --net bitcoincash --address <YOUR_ADDR> <rpcuser> <rpcpassword>
```

Then reload systemd and restart the service:

```bash
sudo systemctl daemon-reload
sudo systemctl restart p2pool.service
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

If you want, you can copy the contents of `contrib/install_ubuntu_24.04_py2_pypy.sh` and run it on a fresh
Ubuntu 24.04 machine to reproduce the steps automatically. Remember to edit the installed systemd unit or
create a drop-in override with real RPC credentials and an explicit payout address before allowing the service
to run in production.
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
[Unit]

