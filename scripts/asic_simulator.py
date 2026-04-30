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

    Defaults: targets = both p2p-spb.xyz:9338 (krizis) and
    109.161.52.148:9348 (ours).  Run for 60 s, log everything.

    --target HOST:PORT     repeatable; targets to connect to
    --duration SECONDS     how long to keep connections open (default 60)
    --ua STRING            user-agent for mining.subscribe (default: FR-1.15 stock S21+)
    --address ADDR         BCH payout address for mining.authorize
    --worker NAME          worker suffix
    --submit-fake-share    after first notify, send a synthetic share with
                           extranonce2='deadbeef' / nonce='cafef00d' / ntime=0;
                           pool will reject — we read the error.  Only run
                           against your OWN pools (we don't want krizis bans).
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

    def log(self, label, direction, msg, color=None):
        line = "[%s] [%s] %s %s" % (now_ts(), label, direction, msg)
        if color and not self.args.no_color:
            line = C(color, line)
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
                    break
                if not line:
                    self.log(label, "==", "EOF (peer closed)", "33")
                    break
                raw = line.decode("utf-8", errors="replace").rstrip()
                if not raw:
                    continue
                self.log(label, "<--", raw, "36")
                try:
                    msg = json.loads(raw)
                except Exception:
                    self.log(label, "!!", "non-JSON: %r" % raw, "31")
                    continue

                msg_id = msg.get("id")
                method = msg.get("method")
                if method is None:
                    # response
                    pending_method = self.pending.pop((label, msg_id), None)
                    self.log(label, "==", "response to id=%s (%s) result=%r error=%r" % (
                        msg_id, pending_method, msg.get("result"), msg.get("error")), "32")
                else:
                    # server-pushed
                    self.log(label, "==", "PUSH %s id=%r" % (method, msg_id), "35")
                    if method == "mining.notify" and first_notify_time is None:
                        first_notify_time = time.time()
                        first_notify_data = msg.get("params", [])
                        if self.args.submit_fake_share:
                            # Submit a synthetic share — pool will reject.
                            # Read the error to learn validation behaviour.
                            jobid = first_notify_data[0] if first_notify_data else "0"
                            await send("mining.submit",
                                [worker, jobid, "deadbeef", "00000000", "cafef00d", "1fffe000"])
        finally:
            self.log(label, "==", "closing connection", "32")
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


async def main():
    p = argparse.ArgumentParser(
        description="fake Antminer S21+ FR-1.15 stratum client for pool A/B testing")
    p.add_argument("--target", action="append", default=None,
                   help="HOST:PORT — repeatable.  Default: both krizis (p2p-spb.xyz:9338) and ours (109.161.52.148:9348).")
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
    args = p.parse_args()

    targets = args.target or [
        "p2p-spb.xyz:9338",       # krizis (works for FR-1.15)
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
    print("[%s] starting — UA=%r, address=%s, worker=%s, duration=%.0fs, fake_share=%s" % (
        now_ts(), args.ua, args.address, args.worker, args.duration, args.submit_fake_share),
        flush=True)

    await asyncio.gather(*[sim.run_one(label, host, port) for label, host, port in parsed])


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
