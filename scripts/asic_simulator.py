#!/usr/bin/env python3
"""
asic_simulator.py — fake Antminer S21+ FR-1.15 stratum client for A/B
testing pool implementations.

What it does
------------
Connects to one or more stratum pools simultaneously, completes the
FR-1.15 handshake (configure → subscribe → authorize) using the same
JSON-RPC frames a real stock unit sends, then stays connected and
logs every server-pushed message.  Optionally submits crafted fake
shares (will be rejected as low-difficulty — that's intentional; we're
sniffing the rejection code/message, not actually mining).

Why
---
Black-box debugging real ASICs is slow (firmware silently filters,
takes ~90 s per cycle, no logs from the miner side).  This simulator
replicates the FR-1.15 handshake bit-exactly and lets us:

  1. Capture extended notify cadence + push messages we'd miss in a
     short probe (set_version_mask, mid-session set_difficulty, etc.)
  2. Submit controlled fake shares to compare pool error responses —
     differences in error codes / messages reveal validation paths.
  3. Test specific behavioural patterns under our control (delay,
     drop, replay) without waiting for real hardware.

Usage
-----
    python3 asic_simulator.py [--target HOST:PORT ...] [options]

    Defaults: targets = both p2p-spb.xyz:9338 (kr1z1s) and
    109.161.52.148:9348 (ours).  Run for 60 s, log everything.

    --target HOST:PORT     repeatable; targets to connect to
    --duration SECONDS     how long to keep connections open (default 60)
    --ua STRING            user-agent for mining.subscribe (default: FR-1.15 stock S21+)
    --address ADDR         BCH payout address for mining.authorize
    --worker NAME          worker suffix
    --submit-fake-share    after first notify, send a synthetic share with
                           extranonce2='deadbeef' / nonce='cafef00d' / ntime=0;
                           pool will reject — we read the error.  Only run
                           against your OWN pools (we don't want kr1z1s bans).
    --capture FILE         tee everything to this file (line-buffered)
    --no-color             disable terminal colour codes

Output format:
    [HH:MM:SS.mmm] [pool=label] direction message

Where direction is one of:
    -->  outgoing (we → pool)
    <--  incoming (pool → us)
    ==   milestone (handshake stage / session event)
    !!   error / unexpected
"""
import argparse
import asyncio
import json
import sys
import time

ANSI = sys.stdout.isatty()
def C(code, s):
    return "\033[%sm%s\033[0m" % (code, s) if ANSI else s

def now_ts():
    t = time.time()
    return time.strftime("%H:%M:%S", time.localtime(t)) + ".%03d" % int((t % 1) * 1000)


class StratumSim:
    def __init__(self, args):
        self.args = args
        self.capture_fh = open(args.capture, "ab", buffering=0) if args.capture else None
        self.next_id = 1
        self.pending = {}  # id → (label, method)
        # Per-pool state for periodic summary display
        self.state = {}  # label -> dict of running counters/last-values
        self.start_time = time.time()

    def log(self, label, direction, msg, color=None):
        line = "[%s] [%s] %s %s" % (now_ts(), label, direction, msg)
        if color and not self.args.no_color:
            line = C(color, line)
        # --quiet-wire suppresses per-line stdout but keeps capture file complete
        if not self.args.quiet_wire:
            print(line, flush=True)
        if self.capture_fh:
            self.capture_fh.write((line + "\n").encode("utf-8", errors="replace"))

    async def run_one(self, label, host, port):
        self.log(label, "==", "connecting to %s:%d" % (host, port), "32")
        try:
            reader, writer = await asyncio.open_connection(host, port)
        except Exception as e:
            self.log(label, "!!", "connect failed: %s" % e, "31")
            return

        async def send(method, params, response_id=None):
            if response_id is None:
                response_id = self.next_id
                self.next_id += 1
            obj = {"id": response_id, "method": method, "params": params}
            line = json.dumps(obj) + "\n"
            writer.write(line.encode())
            await writer.drain()
            self.pending[(label, response_id)] = method
            self.log(label, "-->", line.rstrip(), "33")
            return response_id

        # 1. Configure (BIP310 version-rolling, exactly as FR-1.15 sends it)
        configure_id = await send("mining.configure",
            [["version-rolling"],
             {"version-rolling.mask": "1fffe000", "version-rolling.min-bit-count": 16}])

        # 2. Subscribe
        subscribe_id = await send("mining.subscribe", [self.args.ua])

        # 3. Authorize
        worker = "%s.%s" % (self.args.address, self.args.worker)
        authorize_id = await send("mining.authorize", [worker, "x"])

        # Initialize per-pool state for summary
        st = self.state.setdefault(label, dict(
            connected_at=time.time(), bytes_in=0, lines_in=0,
            extranonce1=None, en2_size=None, subscriptions=None,
            wire_form=None, jsonrpc20=None,
            notify_count=0, set_diff_count=0, error_count=0,
            other_method_count={}, last_diff=None, all_diffs=[],
            last_jobid=None, jobids=[], notify_times=[],
            last_event_at=time.time(), version_mask=None,
            extranonce_changes=0, reconnect_requests=0,
            ntime_field=None, bits_field=None,
        ))

        # Read loop
        first_notify_time = None
        first_notify_data = None
        deadline = time.time() + self.args.duration
        try:
            while time.time() < deadline:
                try:
                    line = await asyncio.wait_for(reader.readline(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    self.log(label, "!!", "read error: %s" % e, "31")
                    st["error_count"] += 1
                    break
                if not line:
                    self.log(label, "==", "EOF (peer closed)", "33")
                    st["error_count"] += 1
                    break
                st["bytes_in"] += len(line)
                st["lines_in"] += 1
                st["last_event_at"] = time.time()
                raw = line.decode("utf-8", errors="replace").rstrip()
                if not raw:
                    continue
                self.log(label, "<--", raw, "36")
                try:
                    msg = json.loads(raw)
                except Exception:
                    self.log(label, "!!", "non-JSON: %r" % raw, "31")
                    st["error_count"] += 1
                    continue

                # Detect JSON-RPC 2.0 marker
                if st["jsonrpc20"] is None:
                    st["jsonrpc20"] = (msg.get("jsonrpc") == "2.0")

                msg_id = msg.get("id")
                method = msg.get("method")
                if method is None:
                    # response
                    pending_method = self.pending.pop((label, msg_id), None)
                    self.log(label, "==", "response to id=%s (%s) result=%r error=%r" % (
                        msg_id, pending_method, msg.get("result"), msg.get("error")), "32")
                    if pending_method == "mining.subscribe":
                        result = msg.get("result")
                        if isinstance(result, list) and len(result) >= 3:
                            st["subscriptions"] = result[0]
                            st["extranonce1"] = result[1]
                            st["en2_size"] = result[2]
                            inner = result[0]
                            if isinstance(inner, list) and inner and isinstance(inner[0], list):
                                st["wire_form"] = "nested"
                            else:
                                st["wire_form"] = "flat"
                else:
                    # server-pushed
                    self.log(label, "==", "PUSH %s id=%r" % (method, msg_id), "35")
                    params = msg.get("params") or []
                    if method == "mining.notify":
                        st["notify_count"] += 1
                        st["notify_times"].append(time.time())
                        if params:
                            st["last_jobid"] = params[0]
                            st["jobids"].append(params[0])
                            if len(params) > 5: st["version_field"] = params[5] if len(params) > 5 else None
                            if len(params) > 6: st["bits_field"] = params[6]
                            if len(params) > 7: st["ntime_field"] = params[7]
                        if first_notify_time is None:
                            first_notify_time = time.time()
                            first_notify_data = params
                            if self.args.submit_fake_share:
                                jobid = first_notify_data[0] if first_notify_data else "0"
                                await send("mining.submit",
                                    [worker, jobid, "deadbeef", "00000000", "cafef00d", "1fffe000"])
                    elif method == "mining.set_difficulty":
                        st["set_diff_count"] += 1
                        if params:
                            st["last_diff"] = params[0]
                            st["all_diffs"].append(params[0])
                    elif method == "mining.set_extranonce":
                        st["extranonce_changes"] += 1
                    elif method == "mining.set_version_mask":
                        if params: st["version_mask"] = params[0]
                    elif method == "client.reconnect":
                        st["reconnect_requests"] += 1
                    else:
                        st["other_method_count"][method] = st["other_method_count"].get(method, 0) + 1
        finally:
            self.log(label, "==", "closing connection", "32")
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    def _short_label(self, label):
        """Shorten host:port labels for table display.  Known prefixes get
        canonical short names; otherwise truncate to 10 chars."""
        SHORT = {
            "109.161.52.148": "ours",
            "p2p-spb.xyz": "kr-spb",
            "rov.p2p-spb.xyz": "kr-rov",
            "ekb.p2p-spb.xyz": "kr-ekb",
            "usa.p2p-spb.xyz": "kr-usa",
            "ss.antpool.com": "antp",
            "stratum.antpool.com": "antp2",
            "btc.f2pool.com": "f2p",
            "stratum.f2pool.com": "f2p2",
            "btc.viabtc.io": "viabtc",
            "bch.viabtc.com": "viabch",
            "stratum.braiins.com": "brain",
            "stratum.slushpool.com": "slush",
        }
        host = label.rsplit(":", 1)[0]
        return SHORT.get(host, host[:10])

    async def summary_loop(self):
        """Periodic per-pool status summary in scrolling-table format."""
        if self.args.summary_every <= 0:
            return
        # Column widths
        cols = [
            ("time",     10),
            ("pool",     8),
            ("notif",    6),
            ("setdiff",  8),
            ("last_diff", 12),
            ("last_jobid", 14),
            ("cadence",  9),
            ("quiet",    7),
            ("jobid_pat", 9),
            ("alerts",   28),
        ]
        def fmt_row(values, color=None):
            cells = []
            for (_, w), v in zip(cols, values):
                s = str(v) if v is not None else "-"
                if len(s) > w-1: s = s[:w-2] + ".."
                cells.append(s.ljust(w))
            line = " ".join(cells)
            if color and not self.args.no_color: line = C(color, line)
            return line
        header = fmt_row([c[0].upper() for c in cols], color="1;33")
        sep    = " ".join("-" * (w-1) for _, w in cols)

        cycles = 0
        while True:
            await asyncio.sleep(self.args.summary_every)
            if not self.state:
                continue
            # Reprint header every 12 cycles
            if cycles % 12 == 0:
                print()
                print(header, flush=True)
                print(sep, flush=True)
                if self.capture_fh:
                    self.capture_fh.write(("\n" + header + "\n" + sep + "\n").encode("utf-8"))
            cycles += 1

            elapsed = time.time() - self.start_time
            elapsed_str = "+%dm%02ds" % (elapsed // 60, elapsed % 60)

            for label, st in self.state.items():
                short = self._short_label(label)
                last_evt = time.time() - st.get("last_event_at", time.time())
                # notify cadence
                nt = st.get("notify_times", [])
                cadence = "-"
                if len(nt) > 1:
                    gaps = [nt[i+1]-nt[i] for i in range(len(nt)-1)]
                    cadence = "%.1fs" % (sum(gaps)/len(gaps))
                # jobid pattern
                jl = st.get("jobids", [])
                pat = "-"
                if len(jl) >= 3:
                    try:
                        ints = [int(j) for j in jl[-10:]]  # only check recent 10
                        mono = all(ints[i] < ints[i+1] for i in range(len(ints)-1))
                        pat = "MONO_INT" if mono else "RAND_INT"
                    except ValueError:
                        pat = "NON_INT"
                # alerts column: condense any unusual events
                alerts = []
                if st.get("extranonce_changes", 0) > 0:
                    alerts.append("EXT:%d" % st["extranonce_changes"])
                if st.get("reconnect_requests", 0) > 0:
                    alerts.append("RECN:%d" % st["reconnect_requests"])
                if st.get("version_mask"):
                    alerts.append("vmask")
                if st.get("error_count", 0) > 0:
                    alerts.append("ERR:%d" % st["error_count"])
                for k, v in st.get("other_method_count", {}).items():
                    alerts.append("%s:%d" % (k.replace("mining.", "m.").replace("client.", "c."), v))
                if last_evt > 60:
                    alerts.append("STALE:%ds" % int(last_evt))
                alerts_str = ",".join(alerts) if alerts else "ok"

                last_diff = st.get("last_diff")
                if isinstance(last_diff, float):
                    last_diff_s = "%.4g" % last_diff
                else:
                    last_diff_s = str(last_diff) if last_diff is not None else "-"

                # Determine row color: red on errors, yellow on staleness, green on ok
                row_color = None
                if not self.args.no_color:
                    if "ERR:" in alerts_str or "STALE:" in alerts_str:
                        row_color = "31"
                    elif st.get("notify_count", 0) > 0 and last_evt < 30:
                        row_color = "32"
                    else:
                        row_color = "37"

                row = fmt_row([
                    elapsed_str,
                    short,
                    st.get("notify_count", 0),
                    st.get("set_diff_count", 0),
                    last_diff_s,
                    st.get("last_jobid"),
                    cadence,
                    "%.1fs" % last_evt,
                    pat,
                    alerts_str,
                ], color=row_color)
                print(row, flush=True)
                if self.capture_fh:
                    self.capture_fh.write((row + "\n").encode("utf-8", errors="replace"))


async def main():
    p = argparse.ArgumentParser(
        description="fake Antminer S21+ FR-1.15 stratum client for pool A/B testing")
    p.add_argument("--target", action="append", default=None,
                   help="HOST:PORT — repeatable.  Default: both kr1z1s (p2p-spb.xyz:9338) and ours (109.161.52.148:9348).")
    p.add_argument("--duration", type=float, default=60.0,
                   help="seconds to keep connections open (default 60)")
    p.add_argument("--ua", default="Antminer S21+/Tue Apr 22 15:05:57 CST 2025",
                   help="user-agent for mining.subscribe")
    p.add_argument("--address", default="qqncfzq2hp6hqj9899j5j5gwpslwu4ash5tqs25907",
                   help="payout address for mining.authorize")
    p.add_argument("--worker", default="sim-fr115",
                   help="worker name suffix")
    p.add_argument("--submit-fake-share", action="store_true",
                   help="after first notify, submit a synthetic share — pool will reject; we read the error")
    p.add_argument("--capture", default=None, help="tee everything to this file")
    p.add_argument("--no-color", action="store_true")
    p.add_argument("--summary-every", type=float, default=0.0,
                   help="seconds between periodic per-pool status summaries (default 0 = off; "
                        "recommended 30-60 for long observations)")
    p.add_argument("--quiet-wire", action="store_true",
                   help="suppress per-line raw wire output (only print summary table). "
                        "Use with --summary-every for clean scrolling-table view.  "
                        "Capture file always gets the full wire trace.")
    args = p.parse_args()

    targets = args.target or [
        "p2p-spb.xyz:9338",       # kr1z1s (works for FR-1.15)
        "109.161.52.148:9348",    # ours (cycling)
    ]

    parsed = []
    for t in targets:
        host, _, port = t.rpartition(":")
        try:
            parsed.append((t, host, int(port)))
        except ValueError:
            print("bad --target: %r" % t, file=sys.stderr)
            sys.exit(2)

    sim = StratumSim(args)
    print("[%s] starting — UA=%r, address=%s, worker=%s, duration=%.0fs, fake_share=%s, summary=%.0fs" % (
        now_ts(), args.ua, args.address, args.worker, args.duration, args.submit_fake_share, args.summary_every),
        flush=True)

    # Schedule the periodic summary task in parallel with the connection tasks.
    # Summary task runs forever; we cancel it once the connections finish.
    summary_task = asyncio.create_task(sim.summary_loop()) if args.summary_every > 0 else None
    try:
        await asyncio.gather(*[sim.run_one(label, host, port) for label, host, port in parsed])
    finally:
        if summary_task:
            summary_task.cancel()
            try:
                await summary_task
            except asyncio.CancelledError:
                pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
