# P2Pool Dashboard Guide

The dashboard (`dashboard.html`) is the primary monitoring UI for a p2pool-BCH node. It polls the node's JSON API endpoints and renders live stats, charts, and tables in a dark-themed single-page layout.

---

## Navigation

| Page | Purpose |
|------|---------|
| **Dashboard** | Real-time pool overview (this page) |
| **Graphs** | Historical hashrate charts (classic d3 view) |
| **Stratum** | Per-worker share counters, ban/security stats |
| **Miners** | Full miner list with payout details |
| **Classic** | Original p2pool index.html |

The header also shows:
- **Pool tab(s)** — coin symbol badge(s); click to switch pools when `multipool.js` is configured with multiple endpoints
- **Version** — running p2pool version string
- **🔄 Refresh** / **⏸ Auto-Refresh** — manual or timed data reload

---

## Header Info Boxes

### ⛏️ Miner Configuration
Displays the stratum connection details for this node:
- **Stratum URL** — auto-detected from the current browser host, e.g. `stratum+tcp://192.168.86.93:9348`
- **Username** — `<YOUR_BCH_ADDRESS>.WORKERNAME`
- **Password** — any string

### 🔗 Network
- **Peers** — total P2Pool peer count (Out | In breakdown)
- **Uptime** — time since the p2pool process started

---

## Stat Cards (top row)

### 🌐 P2Pool Hash Rate
The combined hashrate of all miners contributing to the decentralized pool sharechain.
- **DOA+Orphan** — combined stale rate as a percentage
- **Share Difficulty** — current p2pool share target difficulty

### ⛏️ Local Hash Rate
Hashrate seen by *this node's stratum* (miners connected locally).
- **DOA** — dead-on-arrival share rate for local miners
- **Expected Share** — estimated time until the next valid share from local miners

### 📊 Shares
Lifetime share counts submitted from local miners.
- **Orphan / Dead** — stale share breakdown
- **Efficiency** — good-shares ratio `(total − stale) / total × 100%`

### 🎯 Best Share
The highest proof-of-work share ever submitted by local miners, expressed as a percentage of the current network difficulty.
- Top line — current-round best share
- Sub-line — difficulty value vs. current network difficulty
- 🏆 row — all-time record and median

### 💰 Miners Block Value
Estimated BCH reward (after pool fee) that miners would receive if a block were found *right now*.
- **Expected** — time until next block at current pool hashrate (uses actual pool hashrate from the sharechain, not just local)

### 💎 Node Fee
The node operator's fee percentage and estimated absolute earnings per block.

---

## 📈 Pool Hashrate Chart

An interactive Highcharts area chart showing time-series data.

### Time Range Buttons
`1H` · `1D` · `1W` · `1M` · `1Y` — filter the visible data window; Y-axis scales to the visible series automatically.

### Series (click legend to toggle)

| Series | Color | Default | Description |
|--------|-------|---------|-------------|
| Good | Blue `#008de4` | off | Valid-share hashrate |
| DOA | Red `#ff6b6b` | off | Dead-on-arrival hashrate |
| Blocks | Green `#00ff88` | on | Block-found markers on a separate normalized axis (never inflates hashrate scale) |
| Workers | Purple `#9c27b0` | off | Total stratum worker connections submitting shares |
| Miners | Indigo `#673ab7` | on | Unique payout addresses |
| Connected | Pink `#e91e63` | on | Live stratum connections from all workers |
| Luck | Amber `#ffc107` | on | Luck trend line — 100% = exactly on schedule |
| Net Diff | Violet `#8b5cf6` | off | Network difficulty (secondary axis) |
| Local | Green `#4caf50` | on | Hashrate of miners connected to *this* node |

### Stat Bar (below chart)
| Field | Meaning |
|-------|---------|
| Current | Pool hashrate at the most recent data point |
| Average | Mean across the selected time window |
| Peak | Maximum hashrate in the window |
| Workers/Miners | Worker connections / unique addresses |
| Local | Current local hashrate (green) |
| Avg Luck | Harmonic-mean luck over recent blocks; >100% = lucky, <100% = unlucky |

### Fullscreen Mode
Click ⛶ to expand the chart to full-screen. All legend toggles and time-range buttons work identically. Close with ✕.

---

## Luck Badges (Recent Blocks header)

| Badge | Description |
|-------|-------------|
| **Round: X%** | Luck for the *current* unfinished round — how long the pool has been searching vs. expected |
| **7d: X%** | Harmonic-mean luck over the last 7 days of found blocks |
| **30d: X%** | Harmonic-mean luck over the last 30 days |

> **Luck formula:** `expected_time / actual_time × 100%`.  
> 100% = average, >100% = found block faster than expected (lucky), <100% = took longer (unlucky).

---

## 🏆 Recent Blocks Table

Blocks found by the pool, newest first.

| Column | Description |
|--------|-------------|
| Time | When the block was submitted |
| Height | BCH block height |
| Block Hash | Last 12 hex chars of the block hash (links to block explorer) |
| Luck | `expected_time / actual_time × 100%` for that block |
| Round | `actual_time_to_find / expected_time_to_find` — hover for tooltip with full numbers |
| Net Diff | Network difficulty target at block time |
| Hash Diff | Actual proof-of-work difficulty of the block hash |
| ✓ | Block confirmation status (`✓` confirmed, `✗` orphaned) |

> **Expected time** is computed from the actual pool hashrate measured across the sharechain at the moment the block was found — not a single-miner estimate.

---

## ⛏️ Active Miners Table

Miners currently connected to *this* node's stratum. Rows are grouped by payout address; click a row to expand individual workers.

| Column | Description |
|--------|-------------|
| Address / Worker | Payout address (collapsed) or worker name (expanded) |
| Hashrate | Current rate based on pseudoshares |
| Pseudo Diff | Pseudoshare difficulty (e.g. `372`, `2.44 K`) — vardiff-assigned work per share |
| Time to Share | Estimated time to find next valid p2pool share |
| Last Seen | Time since last pseudoshare received |
| DOA Rate | Dead-on-arrival share percentage |
| Predicted Payout | Estimated BCH payout if block found now |
| 12h Avg | Average hashrate over the last 12 hours (from stat log) |
| 24h Avg | Average hashrate over the last 24 hours (from stat log) |

Enable **Show historical** to include miners who have disconnected but submitted shares recently.

### How "currently connected" is determined

A miner appears as currently connected if **either**:

1. They have at least one live TCP socket on the stratum port (tracked
   per-username via a connection refcount — multi-socket ASIC firmware
   contributes multiple sockets to the same worker), **or**
2. They have submitted a share within the rate-monitor window (10 min) —
   even if their TCP socket is momentarily between reconnects.

This makes the dashboard tolerant of ASIC firmware that flaps TCP
connections frequently: a miner that reconnects every few seconds will
still show consistent hashrate without flickering in and out of the
table. The per-worker `connections` value in `/stratum_stats` is the
true socket count (so a Bitmain D-series with 4 parallel sockets shows
`4`, not `1`); pool-level `connections` sums those across all workers.

---

## 💵 Current Payouts Table

Proportional payout queue for the next block found. Each address receives a share proportional to their work over the last N shares in the sharechain.

---

## 🌐 P2Pool Nodes / 📡 Parent Chain Peers

Side-by-side peer tables. P2Pool nodes are other decentralized pool participants. Parent Chain peers are BCH full-node connections.

Both tables show: address, version, direction (in/out), uptime, and tx-pool depth.

---

## Version Signaling Panel

Shown only when a protocol upgrade is in progress. Tracks the upgrade lifecycle:

| State | Meaning |
|-------|---------|
| `building_chain` | Chain not yet long enough to start voting |
| `waiting` | Waiting for propagation of new version shares |
| `signaling` | Sampling window voting underway |
| `signaling_strong` | Strong majority but not yet threshold |
| `activating` | ≥95% threshold reached, counting confirmation window |
| `propagating` | Activated, V36 shares filling the chain |
| `no_transition` | No upgrade in progress |

Progress bars show chain maturity, vote propagation, sampling-window signaling percentage, and confirmation countdown.

---

## API Endpoints

The dashboard consumes these p2pool JSON endpoints (relative to the node's HTTP port, default `9333`/`9348`):

| Endpoint | Description |
|----------|-------------|
| `/currency_info` | Coin symbol, block period, p2pool port, explorer URLs |
| `/global_stats` | Pool hashrate, difficulty, share count, peer count |
| `/local_stats` | Local hashrate, DOA rate, efficiency, share counts |
| `/recent_blocks` | Last N blocks with luck, time-to-find, hash difficulty |
| `/current_payouts` | Address → proportional payout amount dict |
| `/connected_miners` | Live stratum worker count |
| `/miner_stats` | Per-address hashrate, DOA, pseudo-diff, predicted payout |
| `/miner_payouts` | Per-address proportional payout from current queue |
| `/merged_miner_payouts` | Per-address merged-mining payout |
| `/best_share` | Best share difficulty round/all-time stats |
| `/stratum_stats` | Per-worker share counters, pseudoshare stats |
| `/stratum_security` | Rate-limit and ban configuration |
| `/ban_stats` | Currently banned IPs and reasons |
| `/version_signaling` | Upgrade voting state and progress bars data |
| `/peer_list` | Connected P2Pool and parent-chain peers |
| `/node_info` | Node uptime, version, fee, payout address |
| `/rate` | Raw pool hashrate (H/s) |
| `/difficulty` | Current share difficulty |
| `/fee` | Node fee fraction |
| `/hashrate` | Time-series hashrate data for chart |

---

## Multi-Pool Support

When `multipool.js` detects multiple configured pool endpoints (via `window.MULTIPOOL_CONFIG`), the header shows one tab per coin. Clicking a tab switches all API calls to that pool's base URL and updates the title, currency symbol, and chart data.

---

## Auto-Refresh

The dashboard does **not** auto-refresh by default. Click **⏸ Auto-Refresh: OFF** to enable periodic polling (default interval: 30 seconds). Status shows **▶ Auto-Refresh: ON** while active. Click **🔄 Refresh** at any time for an immediate update.
