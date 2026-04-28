# P2Pool BCH — Telegram Notification Bot

The `telegram_bot/` directory contains a Python 3 bot that sends push
notifications to subscribed miners when pool events occur.

---

## Event types

| Event | Subscription flag | Description |
|---|---|---|
| 🟢 **Worker connected** | `connect` | Fires when a miner authenticates via Stratum — shows worker name and IP. Multi-socket ASIC firmware (Bitmain D-series, etc.) opens several parallel TCP sockets per worker; the alert fires **once** when the first socket connects, not per-socket. Subsequent simultaneous sockets are silently absorbed by the connection refcount. |
| 🔴 **Worker disconnected** | `disconnect` | Fires only when the **last** TCP socket for that worker closes — partial drops with surviving sockets do not trigger. **Suppressed if the worker reconnects within 60 s** (transient TCP drops, pool software restarts) and recorded as a flap instead. |
| 🟡 **Worker silent (no shares)** | `disconnect` | Fires when a worker's TCP is alive but no `mining.submit` has arrived for ≥10 minutes — i.e. the miner is connected but not actually mining (firmware hung, hashrate dead, fan stalled, ASIC chip failure, etc.). Includes the idle duration. |
| ✅ **Worker active again** | `connect` | Fires when a previously-silent worker resumes submitting shares. |
| ⚠️ **Worker flapping** | `disconnect` | Fires once when a worker has had **≥5 disconnect/reconnect cycles within 1 hour**. While in flapping state the bot suppresses the noisy individual connect/disconnect alerts so you only get this one summary instead of dozens. |
| ✅ **Worker stable** | `connect` | Fires when a flapping worker calms down (≤1 cycle in the last hour). Individual connect/disconnect alerts resume. |
| 📦 **Share found** | `share` | A valid share was submitted — shows worker name and share hash. |
| 🏆 **Block found** | `block` | A block was solved by the pool — shows worker name, reward amount, and a clickable link to the block explorer. |

Alerts are matched by **BCH address** — each user registers their address
and receives only events for that address.

The four user-facing toggles in the bot menu are `connect`, `disconnect`,
`share`, and `block`. All seven worker-state event types ride on the
`connect` (good news) or `disconnect` (bad news) flag, so existing
subscribers receive the new alert types automatically — no opt-in needed.

---

## Architecture

```
p2pool (PyPy2, Twisted)
  ├─ stratum.py        per-socket connectionMade / connectionLost
  │   └─ wb.worker_connected / wb.worker_disconnected   (RAW events, per TCP socket)
  │
  ├─ work.py           refcounted state per username
  │   ├─ wb.connected_workers[username]['conn_count']++/--
  │   ├─ wb.worker_first_connected   (fires only when count 0→1)
  │   ├─ wb.worker_last_disconnected (fires only when count 1→0)
  │   ├─ silence loop: 60 s LoopingCall checks last_submit_time
  │   ├─ wb.worker_silent       (≥10 min since last mining.submit)
  │   └─ wb.worker_active_again (silent worker resumes submitting)
  │
  ├─ notifier.py       LocalEventPusher
  │   ├─ subscribes to the SEMANTIC events above (not raw)
  │   ├─ 60 s grace timer debounces fast flaps
  │   └─ rolling 1 h flap-rate counter → worker_flapping / worker_stable
  │
  └─ POST /event  →  aiohttp server (bot, Python 3)
                          └─ match subscribers by address
                          └─ send Telegram messages via PTB
```

- The bot runs as a **child process of p2pool** when `--run-bot` is used.
  No separate service is required for typical deployments.
- The bot binds only to `127.0.0.1` — it is not exposed externally.
- State (subscriptions) is kept in a single JSON file protected by a file lock.

---

## Quick setup (with the installer)

The installer `contrib/install_ubuntu_24.04_py2_pypy.sh` handles everything
if you supply a bot token during the interactive session:

1. Creates `<p2pool-dir>/bot-venv/` with all Python 3 dependencies.
2. Writes `/etc/p2pool-bot.env` (mode 600) with your token and defaults.
3. Patches `contrib/p2pool-run.sh` to auto-pass `--run-bot` whenever both
   the venv and the env file are present.

After installation, start p2pool and the bot launches alongside it:

```bash
sudo systemctl start p2pool.service
sudo journalctl -u p2pool.service -n 50 --no-pager | grep -i bot
# Expected: Telegram bot started (PID 12345)
```

---

## Manual setup

### 1. Create a bot

1. Open Telegram and search for **@BotFather**.
2. Send `/newbot` and follow the prompts.
3. Copy the token you receive (format: `123456:ABC-DEF...`).

### 2. Install Python 3 dependencies

```bash
# from the p2pool root directory
python3 -m venv bot-venv
bot-venv/bin/pip install -r telegram_bot/requirements.txt
```

### 3. Create the environment file

```bash
sudo install -m 600 -o ubuntu telegram_bot/.env.example /etc/p2pool-bot.env
sudo nano /etc/p2pool-bot.env
```

Minimum required content:

```ini
BOT_TOKEN=123456:ABC-DEF...

# These have sensible defaults — change only if needed:
LOCAL_EVENT_PORT=9349
P2POOL_API_URL=http://127.0.0.1:9348
SUBSCRIPTIONS_FILE=/home/ubuntu/Github/p2pool/telegram_bot/subscriptions.json

# Optional: broadcast every event to a channel (bot must be admin there):
# BROADCAST_CHANNEL_ID=-1001234567890
```

### 4. Start the bot (via p2pool)

Pass the extra flags when launching p2pool:

```bash
pypy run_p2pool.py --net bitcoincash \
  --run-bot \
  --bot-python bot-venv/bin/python3 \
  --bot-env-file /etc/p2pool-bot.env \
  --node-name mynode \
  <other p2pool args>
```

`p2pool-run.sh` does this automatically when `/etc/p2pool-bot.env` exists.

---

## Environment variables reference

| Variable | Default | Required | Description |
|---|---|---|---|
| `BOT_TOKEN` | — | **yes** | Telegram bot token from @BotFather |
| `LOCAL_EVENT_PORT` | `9349` | no | Port the aiohttp event server listens on |
| `P2POOL_API_URL` | `http://127.0.0.1:9348` | no | p2pool web API base URL |
| `SUBSCRIPTIONS_FILE` | `telegram_bot/subscriptions.json` | no | Path to subscription store |
| `BROADCAST_CHANNEL_ID` | (empty) | no | Channel ID for broadcast alerts; bot must be admin |
| `ONE_SUB_PER_ADDRESS` | `false` | no | When `true`, each BCH address may only be claimed by one subscriber |
| `BOT_PROXY` | (empty) | no | Outbound proxy URL for the Telegram API. Use `http://`, `https://`, `socks5://` or `socks5h://`; embed credentials inline (`scheme://user:pass@host:port`) when required. Leave empty for direct connection. |
| `BOT_PROXY_GET_UPDATES` | inherits `BOT_PROXY` | no | Separate proxy for the long-poll `getUpdates` connection. Most setups can leave this unset. |

---

## p2pool CLI flags

| Flag | Default | Description |
|---|---|---|
| `--run-bot` | (off) | Launch bot as subprocess |
| `--bot-python PATH` | `python3` | Python 3 interpreter (use venv path) |
| `--bot-env-file PATH` | (none) | File of `KEY=VALUE` env vars for the bot |
| `--local-bot-url URL` | `http://127.0.0.1:9349` | Where p2pool POSTs events |
| `--node-name NAME` | system hostname | Label shown in alert messages |

---

## User interaction (Telegram commands)

Send `/start` to the bot to open the menu. All further interaction uses
inline buttons — no other commands are needed.

| Action | How |
|---|---|
| Set your BCH address | **📝 Set mining address** → type or paste address |
| Toggle alert types | Buttons for each type flip on/off |
| View current settings | Shown in the menu message |
| Unsubscribe | **🗑 Unsubscribe** → confirm |

Addresses are matched exactly. Cashaddr format (`bitcoincash:q...`) and
legacy format (`1...`) are both accepted.

When you send an address the bot validates it in two stages:

1. **Format check** — if the address doesn't match a recognised BCH
   format, the bot explains the error and asks you to try again.
2. **Activity check** — the bot queries the node's `/miner_stats` API. If
   the address has no active hashrate yet, you see a warning and two
   inline buttons:
   - **💾 Save anyway** — stores the address; you'll get alerts as soon
     as the miner connects.
   - **✏️ Different address** — go back and enter a different address.

   If the API is unreachable (e.g. p2pool is still starting up) the bot
   saves the address immediately without warning.

---

## Broadcast channel

If `BROADCAST_CHANNEL_ID` is set, every event is also sent to that channel
regardless of per-user subscriptions. Useful for a public announcement
channel or team monitoring.

Steps to configure:
1. Create a Telegram channel.
2. Add the bot as an **administrator** with "Post Messages" permission.
3. Get the channel ID (forward a message to `@userinfobot`, or use the
   Bot API `getUpdates` after posting to the channel). Supergroup/channel
   IDs are negative and start with `-100`.
4. Set `BROADCAST_CHANNEL_ID=-100xxxxxxxxxx` in `/etc/p2pool-bot.env`.
5. Restart p2pool.

---

## Standalone mode (without `--run-bot`)

If you prefer to run the bot as its own systemd service (for example, to
keep it running when p2pool is stopped), use the included unit file:

```bash
# Adjust User/WorkingDirectory paths in the file first:
sudo cp telegram_bot/bot.service /etc/systemd/system/p2pool-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now p2pool-bot.service
sudo journalctl -u p2pool-bot.service -f
```

In this mode, p2pool must still be started without `--run-bot`, and the
bot just idles (no events arrive) until p2pool is running.

---

## Edge cases and behaviour notes

### Miner not currently connected
A subscriber can set any BCH address. If no miner with that address ever
connects to this node, the subscriber is registered but simply receives no
alerts. There is no error or warning — the address is stored and will match
events whenever that miner does connect.

### Worker name in alerts
The Stratum authorize string has the form `address[._]workername` (e.g.
`bitcoincash:qpabc.rig1`). The **Worker** line in alerts shows only the
`workername` suffix and is omitted entirely when no suffix is set.

Stratum difficulty hints (`+1000`, `/2`, etc.) are stripped from the worker
name before display — `bitcoincash:qpabc.rig1+1000` shows `Worker: rig1`.

### Multiple workers sharing one address
If a miner runs several rigs all authenticating with the same BCH address
(`addr.rig1`, `addr.rig2`, …), the subscriber for that address receives
alerts for all of them. The **Worker** line identifies which rig triggered
each alert.

### Multi-socket ASIC firmware
Some firmware (Bitmain D-series, Braiins OS, certain S21 builds) opens
2–4 parallel TCP sockets per worker for redundancy and faster job
switching. The bot tracks a per-username **connection refcount**:

- The first socket to connect fires **Worker connected**; the 2nd…Nth
  simultaneous sockets are silently absorbed.
- The 1st…(N–1)th sockets to drop are silently absorbed; only the **last
  socket closing** fires **Worker disconnected**.

So a 4-socket worker that loses one socket and reconnects produces no
alerts at all — the worker never actually stopped mining.

### Silent-but-connected detection
A worker whose TCP is alive but stopped submitting shares (firmware hung,
hashrate dead, network to the upstream RPC node down) is invisible at the
TCP layer. p2pool runs a 60-second background loop that flags any
connected worker idle ≥10 min as **silent** and emits a `worker_silent`
alert. The flag clears (and a `worker_active_again` alert fires) as soon
as a `mining.submit` arrives again — so a hung-then-recovered worker
produces a silent → active-again pair regardless of whether the TCP
socket ever dropped.

The 10-minute threshold is chosen below the 15-minute `client.reconnect`
nudge in stratum so users hear about a broken miner before the nudge
cycle kicks in.

### Flap detection
Every full disconnect/reconnect cycle (whether sub-grace and debounce-
canceled, or full disconnect followed by a fresh reconnect) is recorded
in a **rolling 1-hour flap counter** per username. When a worker hits
**≥5 flaps in 1 h** the bot fires a single **Worker flapping** alert and
suppresses the individual connect/disconnect alerts for that worker —
otherwise an unstable miner could spam dozens of alerts per hour. Once
the rolling count drops back to **<2 flaps in the window**, a **Worker
stable** alert fires and individual alerts resume.

### Address format must match
The bot matches events to subscribers by the **BCH payout address** exactly
(case-insensitive). Use cashaddr format (`bitcoincash:q…`) consistently:

- p2pool internally normalises addresses to cashaddr — use the same format
  when subscribing.
- A legacy `1…` address and its cashaddr equivalent are *not* recognised as
  the same by the bot. If in doubt, check what p2pool logs when your miner
  connects.

### Invalid miner address fallback
If a miner authorizes with an address that p2pool cannot parse (e.g., wrong
network, malformed), p2pool substitutes the pool operator's payout address.
Events for such miners are attributed to the operator's address, not the
miner's claimed address.

### `ONE_SUB_PER_ADDRESS`
By default multiple Telegram subscribers may register the same BCH address
(useful for team monitoring). Set `ONE_SUB_PER_ADDRESS=true` to enforce one
subscriber per address — a second user trying to claim an already-taken
address is rejected with an error message in the bot.

---

## Outbound proxy (when api.telegram.org is unreachable)

In some regions or hosting providers, direct egress to
`api.telegram.org` is blocked or unstable. Set `BOT_PROXY` (and
optionally `BOT_PROXY_GET_UPDATES`) in `/etc/p2pool-bot.env` to route
the bot's HTTPS API traffic through a proxy. URL schemes accepted:

| Scheme | Notes |
|---|---|
| `http://host:port` | HTTP CONNECT proxy. |
| `https://host:port` | HTTPS proxy with TLS to the proxy itself. |
| `socks5://host:port` | SOCKS5 with hostname resolved by the bot. |
| `socks5h://host:port` | SOCKS5 with hostname resolved by the proxy (preferred when DNS to `api.telegram.org` is also blocked). |

Inline credentials are supported: `scheme://user:pass@host:port`. The
bot logs the proxy URL at startup with the password redacted.

SOCKS support is provided by the `[socks]` extra of
`python-telegram-bot`, already pinned in `requirements.txt` — install
or reinstall the venv after pulling this update so the SOCKS
dependency is present:

```bash
bot-venv/bin/pip install -r telegram_bot/requirements.txt
sudo systemctl restart p2pool.service   # or p2pool-bch.service
```

When `BOT_PROXY_GET_UPDATES` is unset (the common case), the long-poll
`getUpdates` connection inherits `BOT_PROXY`. Set it to a different
URL only when you have separate egress rules for outbound API calls
versus long-poll traffic.

---

## Troubleshooting

**Bot starts but sends no messages**
- Confirm `/etc/p2pool-bot.env` has the correct `BOT_TOKEN`.
- Check that `LOCAL_EVENT_PORT` matches `--local-bot-url` on the p2pool side
  (default both `9349`).
- Make sure you `/start`-ed the bot and set a mining address that matches
  the address p2pool sees in Stratum `authorize`.

**Subscribed but no alerts when miner connects**
- The address stored in the bot must exactly match what p2pool reports.
  Use cashaddr format (`bitcoincash:q…`). Verify by watching the p2pool
  console when your miner authenticates — it logs the parsed address.
- Confirm the relevant alert toggle (e.g. **Connect**) is **ON** in the bot
  menu.
- If your mining software appends a difficulty hint (e.g. `addr.rig1+1000`),
  the bot still matches on the base address — hints are stripped.

**`Telegram bot started` not in p2pool log**
- The venv check in `p2pool-run.sh` requires `bot-venv/bin/python3` to be
  executable and `/etc/p2pool-bot.env` to exist. Verify both:
  ```bash
  ls -la /home/ubuntu/Github/p2pool/bot-venv/bin/python3
  ls -la /etc/p2pool-bot.env
  ```

**`BOT_TOKEN` error / KeyError**
- The env file must contain `BOT_TOKEN=...` (no spaces around `=`, no quotes).

**`Address already in use` on port 9349**
- Another process is using the event port. Change `LOCAL_EVENT_PORT` in
  `/etc/p2pool-bot.env` and pass `--local-bot-url http://127.0.0.1:<port>`
  to p2pool.

**Subscriptions lost after reinstall**
- The subscription store is a plain JSON file at `SUBSCRIPTIONS_FILE`.
  Back it up before reinstalling if you want to preserve subscriptions.

---

## File layout

```
telegram_bot/
├── __init__.py
├── bot.py             # entry point: asyncio loop, aiohttp + PTB
├── bot.service        # standalone systemd unit (optional)
├── config.py          # env var loading
├── event_server.py    # aiohttp POST /event handler
├── handlers.py        # PTB ConversationHandler (/start, buttons)
├── keyboards.py       # inline keyboard builders
├── notifier.py        # send_alert, broadcast_to_channel
├── requirements.txt   # python-telegram-bot>=20, aiohttp>=3.9, filelock
├── subscriptions.py   # JSON subscription store with filelock
└── .env.example       # template for /etc/p2pool-bot.env
```
