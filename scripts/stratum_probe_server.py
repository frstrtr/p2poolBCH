#!/usr/bin/env python3
"""
stratum_probe_server.py — diagnostic stratum-v1 server for figuring out
what strict-firmware ASICs (Antminer S21+ stock, etc.) actually need
from the wire dialog.

WHAT IT DOES
    Accepts TCP, reads line-delimited JSON-RPC, logs every line in/out
    with millisecond timestamps and per-connection IDs, and pushes
    enough mining.set_difficulty / mining.notify traffic that the miner
    keeps hashing past the handshake.  Many strict firmwares only fail
    AFTER the first share — silent log up to that point.

WHY
    The Antminer S21+ stock firmware (FR-1.15, FF-1.13) does not log
    the wire-level stratum dialog — only "network connection lost over
    300 seconds" symptoms.  Sit this probe in front of the miner and
    you can see exactly which message it accepts and which it bails on.

USAGE
    python3 stratum_probe_server.py [--port 3333] [flags...]

    Defaults match current p2pool production behaviour (flat subscribe,
    version-rolling on, mask 1fffe000, keepalive 120s).  Run with no
    flags to reproduce the failing case; flip one flag at a time to
    A/B which combination the miner actually accepts.

    To recreate "STRATUM_NICEHASH_COMPAT=1":
        --subscribe-form=nested
    To recreate "STRATUM_DISABLE_ASICBOOST=1":
        --no-version-rolling
    To recreate "STRATUM_DISABLE_LATENCY_PING=1":
        --keepalive-interval=0

OUTPUT
    [HH:MM:SS.mmm] [conn-N] <-- {miner JSON line}
    [HH:MM:SS.mmm] [conn-N] --> {our JSON response}
    [HH:MM:SS.mmm] [conn-N] == MILESTONE: subscribe — UA='cgminer/...' ...
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time

ANSI = sys.stdout.isatty()
def C(code, s):
    return "\033[%sm%s\033[0m" % (code, s) if ANSI else s

def now_ts():
    t = time.time()
    return time.strftime("%H:%M:%S", time.localtime(t)) + ".%03d" % int((t % 1) * 1000)


class StratumProbeServer:
    def __init__(self, args):
        self.args = args
        self.conn_seq = 0
        self.job_seq = 0
        self.capture = open(args.capture, "ab", buffering=0) if args.capture else None
        self.require_ua_re = (re.compile(args.require_ua_pattern, re.IGNORECASE)
                              if args.require_ua_pattern else None)

    def log(self, cid, direction, msg, color=None):
        line = "[%s] [conn-%d] %s %s" % (now_ts(), cid, direction, msg)
        if color:
            line = C(color, line)
        print(line, flush=True)

    def cap(self, cid, direction, raw):
        if self.capture:
            self.capture.write(("[%s] conn-%d %s %s\n" % (now_ts(), cid, direction, raw)).encode())

    async def handle(self, reader, writer):
        self.conn_seq += 1
        cid = self.conn_seq
        peer = writer.get_extra_info("peername")
        state = {
            "writer": writer,
            "alive": True,
            "configure_seen": False,
            "subscribe_seen": False,
            "authorize_seen": False,
            "submits": 0,
            "version_rolling_negotiated": False,
            "negotiated_mask": None,
            "first_notify_sent": False,
        }
        self.log(cid, "==", "NEW CONNECTION from %s" % (peer,), "32")

        ka_task = None
        notify_task = None
        try:
            while True:
                try:
                    line = await reader.readline()
                except (ConnectionError, asyncio.IncompleteReadError) as e:
                    self.log(cid, "!!", "read error: %s" % e, "31")
                    break
                if not line:
                    self.log(cid, "==", "EOF (peer closed)", "33")
                    break
                raw = line.decode("utf-8", errors="replace").rstrip()
                if not raw:
                    continue
                self.cap(cid, "<--", raw)
                try:
                    msg = json.loads(raw)
                except Exception:
                    self.log(cid, "<--", "NON-JSON: %r" % raw, "31")
                    continue
                self.log(cid, "<--", json.dumps(msg, separators=(",", ":")), "36")

                method = msg.get("method")
                msg_id = msg.get("id")
                params = msg.get("params", []) or []

                if method == "mining.configure":
                    await self.on_configure(cid, state, msg_id, params)
                elif method == "mining.subscribe":
                    await self.on_subscribe(cid, state, msg_id, params)
                elif method == "mining.authorize":
                    await self.on_authorize(cid, state, msg_id, params)
                    if notify_task is None and not self.args.silent_after_subscribe:
                        notify_task = asyncio.create_task(self.notify_loop(cid, state))
                    if ka_task is None and self.args.keepalive_interval > 0:
                        ka_task = asyncio.create_task(self.keepalive_loop(cid, state))
                elif method == "mining.submit":
                    await self.on_submit(cid, state, msg_id, params)
                elif method == "mining.suggest_difficulty":
                    await self.send(cid, state, {"id": msg_id, "result": True, "error": None})
                elif method == "mining.extranonce.subscribe":
                    await self.send(cid, state, {"id": msg_id, "result": True, "error": None})
                elif method is None and "result" in msg:
                    self.log(cid, "==", "client response id=%s result=%r" %
                             (msg_id, msg.get("result")), "35")
                else:
                    self.log(cid, "!!", "unhandled method: %s" % method, "33")
                    if msg_id is not None:
                        await self.send(cid, state, {"id": msg_id, "result": None,
                                                     "error": [20, "Unknown method", None]})
        finally:
            state["alive"] = False
            for t in (ka_task, notify_task):
                if t and not t.done():
                    t.cancel()
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            self.log(cid, "==", "CONNECTION CLOSED — submits=%d configure=%s subscribe=%s authorize=%s" %
                     (state["submits"], state["configure_seen"], state["subscribe_seen"],
                      state["authorize_seen"]), "32")

    async def send(self, cid, state, obj):
        if not state["alive"]:
            return
        line = json.dumps(obj, separators=(",", ":")) + "\n"
        try:
            state["writer"].write(line.encode())
            await state["writer"].drain()
        except Exception as e:
            self.log(cid, "!!", "send error: %s" % e, "31")
            state["alive"] = False
            return
        self.log(cid, "-->", line.rstrip(), "33")
        self.cap(cid, "-->", line.rstrip())

    async def on_configure(self, cid, state, msg_id, params):
        state["configure_seen"] = True
        extensions = params[0] if len(params) > 0 else []
        ext_params = params[1] if len(params) > 1 else {}
        result = {}
        if "version-rolling" in extensions:
            if self.args.no_version_rolling:
                result["version-rolling"] = False
                self.log(cid, "==",
                         "MILESTONE: configure — version-rolling DECLINED (--no-version-rolling)",
                         "32")
            else:
                miner_mask_hex = ext_params.get("version-rolling.mask", "ffffffff")
                pool_mask = int(self.args.version_mask, 16)
                miner_mask = int(miner_mask_hex, 16)
                negotiated = pool_mask & miner_mask
                state["version_rolling_negotiated"] = True
                state["negotiated_mask"] = negotiated
                result["version-rolling"] = True
                result["version-rolling.mask"] = "%08x" % negotiated
                self.log(cid, "==",
                         "MILESTONE: configure — version-rolling AGREED "
                         "miner=%s pool=%s negotiated=%08x" %
                         (miner_mask_hex, self.args.version_mask, negotiated), "32")
        for other in extensions:
            if other != "version-rolling" and other not in result:
                result[other] = False
        await self.send(cid, state, {"id": msg_id, "result": result, "error": None})

    async def on_subscribe(self, cid, state, msg_id, params):
        state["subscribe_seen"] = True
        miner_ua = params[0] if params else "?"
        if self.require_ua_re and not self.require_ua_re.search(str(miner_ua)):
            self.log(cid, "==",
                     "MILESTONE: REJECTED — UA=%r does not match /%s/i — closing connection" %
                     (miner_ua, self.args.require_ua_pattern), "31")
            await self.send(cid, state, {"id": msg_id, "result": None,
                                         "error": [20, "Worker not allowed on this probe", None]})
            state["alive"] = False
            try:
                state["writer"].close()
            except Exception:
                pass
            return
        if self.args.subscribe_form == "nested":
            subs = [
                ["mining.set_difficulty", "b4b6693b72a50c7116db18d6497cac52"],
                ["mining.notify",         "ae6812eb4cd7735a302a8a9dd95cf71f"],
            ]
        else:
            subs = ["mining.notify", "ae6812eb4cd7735a302a8a9dd95cf71f"]
        result = [subs, self.args.extranonce1, self.args.extranonce2_size]
        self.log(cid, "==",
                 "MILESTONE: subscribe — UA=%r form=%s en1=%s en2_size=%d" %
                 (miner_ua, self.args.subscribe_form, self.args.extranonce1,
                  self.args.extranonce2_size), "32")
        await self.send(cid, state, {"id": msg_id, "result": result, "error": None})
        if self.args.push_set_version_mask and state["version_rolling_negotiated"]:
            mask_hex = "%08x" % state["negotiated_mask"]
            await self.send(cid, state, {"id": None,
                                         "method": "mining.set_version_mask",
                                         "params": [mask_hex]})
            self.log(cid, "==", "pushed mining.set_version_mask(%s)" % mask_hex, "32")

    async def on_authorize(self, cid, state, msg_id, params):
        state["authorize_seen"] = True
        user = params[0] if params else "?"
        self.log(cid, "==", "MILESTONE: authorize — user=%r" % user, "32")
        await self.send(cid, state, {"id": msg_id, "result": True, "error": None})

    async def on_submit(self, cid, state, msg_id, params):
        state["submits"] += 1
        self.log(cid, "==",
                 "MILESTONE: SUBMIT #%d params=%s" % (state["submits"], params), "32")
        if self.args.reject_shares:
            await self.send(cid, state, {"id": msg_id, "result": False,
                                         "error": [23, "Low difficulty share", None]})
        else:
            await self.send(cid, state, {"id": msg_id, "result": True, "error": None})

    async def notify_loop(self, cid, state):
        try:
            await asyncio.sleep(self.args.first_notify_delay)
            await self.push_set_difficulty(cid, state)
            await self.push_notify(cid, state, clean_jobs=True)
            state["first_notify_sent"] = True
            if self.args.notify_interval > 0:
                while state["alive"]:
                    await asyncio.sleep(self.args.notify_interval)
                    if not state["alive"]:
                        return
                    await self.push_notify(cid, state, clean_jobs=False)
        except asyncio.CancelledError:
            return

    async def push_set_difficulty(self, cid, state):
        await self.send(cid, state, {"id": None,
                                     "method": "mining.set_difficulty",
                                     "params": [self.args.difficulty]})

    async def push_notify(self, cid, state, clean_jobs):
        self.job_seq += 1
        job_id = "%x" % self.job_seq
        prevhash = "00" * 32
        en1_bytes = len(self.args.extranonce1) // 2
        scriptsig_len = en1_bytes + self.args.extranonce2_size  # must be < 253
        coinb1 = ("01000000"                                  # tx version
                  "01"                                        # input count
                  "00" * 32 +                                 # prev txid
                  "ffffffff" +                                # prev vout
                  "%02x" % scriptsig_len)                     # scriptSig length varint
        coinb2 = ("ffffffff"                                  # sequence
                  "01"                                        # output count
                  "00f2052a01000000"                          # value (50 BTC)
                  "1976a914" + "00" * 20 + "88ac"             # P2PKH to all-zeros
                  "00000000")                                 # locktime
        merkle = []
        version = "20000000"
        nbits = "1d00ffff"
        ntime = "%08x" % int(time.time())
        params = [job_id, prevhash, coinb1, coinb2, merkle, version, nbits, ntime, clean_jobs]
        await self.send(cid, state,
                        {"id": None, "method": "mining.notify", "params": params})

    async def keepalive_loop(self, cid, state):
        try:
            while state["alive"]:
                await asyncio.sleep(self.args.keepalive_interval)
                if not state["alive"]:
                    return
                await self.send(cid, state, {"id": int(time.time()),
                                             "method": "client.get_version",
                                             "params": []})
        except asyncio.CancelledError:
            return


async def main():
    p = argparse.ArgumentParser(
        description="diagnostic stratum-v1 probe server (Antminer S21+ etc.)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--bind", default="0.0.0.0")
    p.add_argument("--port", type=int, default=3333)
    p.add_argument("--subscribe-form", choices=["flat", "nested"], default="flat",
                   help="flat = ['mining.notify',id] (current p2pool default); "
                        "nested = slush/NiceHash form (STRATUM_NICEHASH_COMPAT=1)")
    p.add_argument("--no-version-rolling", action="store_true",
                   help="decline mining.configure version-rolling (=STRATUM_DISABLE_ASICBOOST=1)")
    p.add_argument("--version-mask", default="1fffe000",
                   help="pool's allowed version-rolling mask (hex)")
    p.add_argument("--push-set-version-mask", action="store_true",
                   help="send mining.set_version_mask after subscribe")
    p.add_argument("--difficulty", type=float, default=1.0)
    p.add_argument("--extranonce1", default="6e6f6e63",
                   help="extranonce1 hex (default 4 bytes 'nonc')")
    p.add_argument("--extranonce2-size", type=int, default=4)
    p.add_argument("--first-notify-delay", type=float, default=0.5,
                   help="seconds after authorize before first set_difficulty/notify")
    p.add_argument("--keepalive-interval", type=float, default=120.0,
                   help="0 = off; otherwise client.get_version every N seconds")
    p.add_argument("--notify-interval", type=float, default=30.0,
                   help="0 = no further notifies after the first")
    p.add_argument("--reject-shares", action="store_true",
                   help="reject every mining.submit instead of accepting")
    p.add_argument("--silent-after-subscribe", action="store_true",
                   help="accept handshake but never send set_difficulty/notify "
                        "(reproduces 'no work' starvation)")
    p.add_argument("--capture", default=None,
                   help="raw line capture file (append mode)")
    p.add_argument("--require-ua-pattern", default=None,
                   help="reject mining.subscribe whose UA (params[0]) doesn't match "
                        "this case-insensitive regex; e.g. 'antminer.*s21' or "
                        "'bmminer.*s21' to fence the probe to S21+ only and stop "
                        "stray miners from parking hashrate here")
    args = p.parse_args()

    server = StratumProbeServer(args)
    s = await asyncio.start_server(server.handle, args.bind, args.port)
    addrs = ", ".join(str(sock.getsockname()) for sock in s.sockets)
    print("[%s] STRATUM PROBE listening on %s" % (now_ts(), addrs), flush=True)
    print("[%s] config: %s" % (now_ts(), vars(args)), flush=True)
    print("[%s] point your S21+ here (any user/pass) and watch the dialog." % now_ts(),
          flush=True)
    async with s:
        await s.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
