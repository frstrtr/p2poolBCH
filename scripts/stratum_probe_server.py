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
import copy
import csv
import json
import os
import re
import sys
import time
from collections import OrderedDict, defaultdict

ANSI = sys.stdout.isatty()
def C(code, s):
    return "\033[%sm%s\033[0m" % (code, s) if ANSI else s

def now_ts():
    t = time.time()
    return time.strftime("%H:%M:%S", time.localtime(t)) + ".%03d" % int((t % 1) * 1000)


# Default rotating-hypothesis matrix.  Each entry tests one wire-shape
# parameter against FR-1.15-class firmware (Antminer S21+ stock,
# bmminer-derived).  See memory/reference_bmminer_stratum_contract.md
# for the source-code-derived expectations behind each row.
#
# `expect` values:
#   works              — handshake completes, miner submits shares
#   reject_subscribe   — miner closes before/at subscribe response
#   reject_configure   — miner closes during/after configure
#   silent_drop        — miner stays connected but eventually times out
#   maybe              — uncertain (test to find out)
#
# `tags`:
#   silent             — completion uses --rotate-silent-window timer
#   expects_reject     — completion uses short-reject window (30s)
#
# Anything else uses the success-window / min-submits criterion.
DEFAULT_MATRIX = [
    {"name": "baseline",                "overrides": {},
     "expect": "works",            "tags": []},

    # extranonce2_size sweep — bmminer hard-rejects outside [2,16]
    {"name": "n2size_2",                "overrides": {"extranonce2_size": 2},
     "expect": "works",            "tags": []},
    {"name": "n2size_4",                "overrides": {"extranonce2_size": 4},
     "expect": "works",            "tags": []},
    {"name": "n2size_8",                "overrides": {"extranonce2_size": 8},
     "expect": "works",            "tags": []},
    {"name": "n2size_16",               "overrides": {"extranonce2_size": 16},
     "expect": "works",            "tags": []},
    {"name": "n2size_1",                "overrides": {"extranonce2_size": 1},
     "expect": "reject_subscribe", "tags": ["expects_reject"]},
    {"name": "n2size_17",               "overrides": {"extranonce2_size": 17},
     "expect": "reject_subscribe", "tags": ["expects_reject"]},
    {"name": "n2size_32",               "overrides": {"extranonce2_size": 32},
     "expect": "reject_subscribe", "tags": ["expects_reject"]},

    # version-rolling mask sweep — BM1368 chip rolls bits [13:28]
    {"name": "mask_1fffe000",           "overrides": {"version_mask": "1fffe000"},
     "expect": "works",            "tags": []},
    {"name": "mask_18000000",           "overrides": {"version_mask": "18000000"},
     "expect": "maybe",            "tags": []},
    {"name": "mask_00800000",           "overrides": {"version_mask": "00800000"},
     "expect": "reject_configure", "tags": ["expects_reject"]},
    {"name": "mask_00000000",           "overrides": {"version_mask": "00000000"},
     "expect": "reject_configure", "tags": ["expects_reject"]},

    # subscribe response form
    {"name": "subscribe_flat",          "overrides": {"subscribe_form": "flat"},
     "expect": "works",            "tags": []},
    {"name": "subscribe_nested",        "overrides": {"subscribe_form": "nested"},
     "expect": "works",            "tags": []},

    # keepalive cadence
    {"name": "keepalive_120",           "overrides": {"keepalive_interval": 120.0},
     "expect": "works",            "tags": []},
    {"name": "keepalive_0",             "overrides": {"keepalive_interval": 0.0},
     "expect": "works",            "tags": []},

    # silent server — no notifies after subscribe; miner times out at ~300s
    {"name": "silent_after_subscribe",  "overrides": {"silent_after_subscribe": True},
     "expect": "silent_drop",      "tags": ["silent"]},

    # mid-session set_version_mask push behaviour
    {"name": "nopush_set_version_mask", "overrides": {"push_set_version_mask": False},
     "expect": "works",            "tags": []},
    {"name": "push_set_version_mask",   "overrides": {"push_set_version_mask": True},
     "expect": "works",            "tags": []},
]


def _apply_overrides(base_args, overrides):
    """Return a shallow copy of an argparse.Namespace with overrides applied."""
    ns = argparse.Namespace(**vars(base_args))
    for k, v in overrides.items():
        setattr(ns, k, v)
    return ns


class StratumProbeServer:
    def __init__(self, args):
        self.args = args
        self.conn_seq = 0
        self.job_seq = 0
        self.capture = open(args.capture, "ab", buffering=0) if args.capture else None
        self.require_ua_re = (re.compile(args.require_ua_pattern, re.IGNORECASE)
                              if args.require_ua_pattern else None)

        # ---- Rotation state ------------------------------------------------
        self.rotate_enabled = bool(args.rotate)
        self.matrix = []
        self.rotate_idx = 0
        self.completed_passes = 0
        self.results = []  # list of dicts, one per completed cycle
        self.results_csv_path = args.results_csv
        self.results_csv_fh = None
        self.results_csv_writer = None
        self._results_csv_fields = [
            "timestamp", "cid",
            "hypothesis_idx", "hypothesis_name", "hypothesis_expect",
            "configure_seen", "subscribe_seen", "authorize_seen",
            "submits", "submits_our",
            "duration_sec", "terminated_by",
            "first_submit_ms", "first_submit_our_ms",
            "first_share_accepted",
        ]

        if self.rotate_enabled:
            self.matrix = self._load_matrix(args.rotate_file)
            if not self.matrix:
                print("[%s] ROTATE: empty matrix; rotation disabled" % now_ts(), flush=True)
                self.rotate_enabled = False
            else:
                if args.start_at:
                    names = [h["name"] for h in self.matrix]
                    if args.start_at not in names:
                        raise SystemExit("--start-at: %r not in matrix; available: %s" %
                                         (args.start_at, names))
                    self.rotate_idx = names.index(args.start_at)
                if self.results_csv_path:
                    fresh = not os.path.exists(self.results_csv_path)
                    self.results_csv_fh = open(self.results_csv_path, "a",
                                               newline="", buffering=1)
                    self.results_csv_writer = csv.DictWriter(
                        self.results_csv_fh, fieldnames=self._results_csv_fields)
                    if fresh:
                        self.results_csv_writer.writeheader()
                self._announce_matrix()

    @staticmethod
    def _load_matrix(path):
        if not path:
            return [dict(h) for h in DEFAULT_MATRIX]
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise SystemExit("--rotate-file: top level must be a JSON list")
        for h in data:
            if "name" not in h or "overrides" not in h:
                raise SystemExit("--rotate-file: each entry needs 'name' and 'overrides'")
            h.setdefault("expect", "maybe")
            h.setdefault("tags", [])
        return data

    def _announce_matrix(self):
        print("[%s] ROTATE: %d hypotheses, starting at idx=%d (%s); loop=%s" % (
            now_ts(), len(self.matrix), self.rotate_idx,
            self.matrix[self.rotate_idx]["name"], self.args.rotate_loop), flush=True)
        for i, h in enumerate(self.matrix):
            mark = "->" if i == self.rotate_idx else "  "
            print("  %s %2d. %-26s expect=%-18s overrides=%s" % (
                mark, i, h["name"], h["expect"], h["overrides"]), flush=True)

    def _next_hypothesis(self):
        """Return (idx, hypothesis_dict) for the current rotation slot, or
        (None, None) if rotation has finished and --rotate-loop is off."""
        if not self.rotate_enabled:
            return (None, None)
        if self.rotate_idx >= len(self.matrix):
            if self.args.rotate_loop:
                self.completed_passes += 1
                self.rotate_idx = 0
            else:
                return (None, None)
        h = self.matrix[self.rotate_idx]
        return (self.rotate_idx, h)

    def _advance_hypothesis(self):
        if not self.rotate_enabled:
            return
        self.rotate_idx += 1
        # If we've consumed the last entry and --rotate-loop is off, mark
        # rotation finished so subsequent connections fall back to baseline.
        if self.rotate_idx >= len(self.matrix) and not self.args.rotate_loop:
            print("[%s] ROTATE: full pass complete (%d hypotheses); not looping" %
                  (now_ts(), len(self.matrix)), flush=True)
            self.print_summary()
            if self.args.rotate_exit_after_pass:
                print("[%s] ROTATE: --rotate-exit-after-pass set; "
                      "exiting in 3s to let in-flight logs flush" %
                      now_ts(), flush=True)
                # asyncio.run() raises if the loop is stop()ped from under it,
                # so we just os._exit(0) instead. By this point the summary
                # has printed and the CSV has been flushed (csv writer in
                # _record_result calls fp.flush() per row), so a hard exit
                # loses nothing.
                import os as _os
                import sys as _sys
                _sys.stdout.flush()
                _sys.stderr.flush()
                try:
                    loop = asyncio.get_running_loop()
                    loop.call_later(3.0, lambda: _os._exit(0))
                except RuntimeError:
                    _os._exit(0)

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

        # Skip rotation for loopback peers (typically dashboard pollers
        # hitting /local_stats every few seconds — they don't speak
        # stratum but they DO consume hypothesis slots if not filtered).
        peer_host = peer[0] if peer else ""
        is_loopback = peer_host in ("127.0.0.1", "::1", "localhost")

        # Bind a hypothesis to this connection.  When rotation is off, the
        # effective args are simply the baseline.
        if is_loopback and self.rotate_enabled:
            # Don't consume a rotation slot for loopback pollers.  Serve
            # them with the baseline args (which is what they'd hit on
            # real p2pool anyway) and don't track them in the matrix.
            hyp_idx, hyp = (None, None)
        else:
            hyp_idx, hyp = self._next_hypothesis()
        if hyp is not None:
            eff_args = _apply_overrides(self.args, hyp.get("overrides", {}))
            hyp_name = hyp["name"]
            hyp_expect = hyp.get("expect", "maybe")
            hyp_tags = list(hyp.get("tags", []))
        else:
            eff_args = self.args
            hyp_name = "(loopback-bypass)" if is_loopback else "(no-rotate)"
            hyp_expect = "n/a"
            hyp_tags = []

        state = {
            "writer": writer,
            "alive": True,
            "configure_seen": False,
            "subscribe_seen": False,
            "authorize_seen": False,
            "submits": 0,
            # submits_our: only those submits whose job_id is one we
            # sent in mining.notify on THIS connection. Anything else is
            # a chip-flush of nonces queued from the miner's previous
            # pool — still useful to log, but not a vote that this
            # hypothesis works.
            "submits_our": 0,
            "our_job_ids": set(),
            "first_share_accepted": None,
            "version_rolling_negotiated": False,
            "negotiated_mask": None,
            "first_notify_sent": False,
            "args": eff_args,
            "hyp_idx": hyp_idx,
            "hyp_name": hyp_name,
            "hyp_expect": hyp_expect,
            "hyp_tags": hyp_tags,
            "conn_start": time.time(),
            "first_submit_ts": None,
            "first_submit_our_ts": None,
            "terminated_by": None,
            "rotation_consumed": (hyp_idx is not None),
        }
        self.log(cid, "==", "NEW CONNECTION from %s" % (peer,), "32")
        if hyp_idx is not None:
            self.log(cid, "==",
                     "HYPOTHESIS START: idx=%d/%d name=%s expect=%s overrides=%s" %
                     (hyp_idx, len(self.matrix), hyp_name, hyp_expect,
                      hyp.get("overrides", {})), "1;35")

        ka_task = None
        notify_task = None
        hyp_task = None
        if hyp_idx is not None:
            hyp_task = asyncio.create_task(self.hypothesis_monitor(cid, state))
        try:
            while True:
                try:
                    line = await reader.readline()
                except (ConnectionError, asyncio.IncompleteReadError) as e:
                    self.log(cid, "!!", "read error: %s" % e, "31")
                    if state["terminated_by"] is None:
                        state["terminated_by"] = "peer_error"
                    break
                if not line:
                    self.log(cid, "==", "EOF (peer closed)", "33")
                    if state["terminated_by"] is None:
                        state["terminated_by"] = "peer_eof"
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
                    if notify_task is None and not state["args"].silent_after_subscribe:
                        notify_task = asyncio.create_task(self.notify_loop(cid, state))
                    if ka_task is None and state["args"].keepalive_interval > 0:
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
            for t in (ka_task, notify_task, hyp_task):
                if t and not t.done():
                    t.cancel()
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            self.log(cid, "==",
                     "CONNECTION CLOSED — submits=%d (our=%d, stale=%d) "
                     "configure=%s subscribe=%s authorize=%s" %
                     (state["submits"], state["submits_our"],
                      state["submits"] - state["submits_our"],
                      state["configure_seen"], state["subscribe_seen"],
                      state["authorize_seen"]), "32")
            if state.get("rotation_consumed"):
                # Engagement gate: only count this as a real cycle if the
                # connection at least reached mining.subscribe.  Bare TCP
                # connects, port scanners, half-open NAT probes, and
                # malformed clients should NOT advance the rotation
                # index — they leave the slot for the next real miner.
                if state["subscribe_seen"]:
                    self._record_completion(cid, state)
                    # Serialize advancement: when several parallel S21+
                    # connections all bind to the same hypothesis idx
                    # (e.g. one TCP per chip) and close at roughly the
                    # same time, only the FIRST close advances the
                    # cursor. Later closes still record results — but
                    # don't bump idx again, otherwise rotation would
                    # skip 2 hypotheses per round of parallel conns.
                    if state["hyp_idx"] == self.rotate_idx:
                        self._advance_hypothesis()
                    else:
                        self.log(cid, "==",
                                 "ROTATION ABSORBED: name=%s hyp_idx=%d "
                                 "but cursor already at %d/%d "
                                 "(parallel-conn deduplication)" %
                                 (state["hyp_name"], state["hyp_idx"],
                                  self.rotate_idx, len(self.matrix)),
                                 "1;35")
                else:
                    self.log(cid, "==",
                             "HYPOTHESIS NOT-CONSUMED: name=%s "
                             "(connection ended before mining.subscribe; "
                             "rotation index unchanged at %d/%d)" %
                             (state["hyp_name"], self.rotate_idx,
                              len(self.matrix)), "35")

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
            if state["args"].no_version_rolling:
                result["version-rolling"] = False
                self.log(cid, "==",
                         "MILESTONE: configure — version-rolling DECLINED (--no-version-rolling)",
                         "32")
            else:
                miner_mask_hex = ext_params.get("version-rolling.mask", "ffffffff")
                pool_mask = int(state["args"].version_mask, 16)
                miner_mask = int(miner_mask_hex, 16)
                negotiated = pool_mask & miner_mask
                state["version_rolling_negotiated"] = True
                state["negotiated_mask"] = negotiated
                result["version-rolling"] = True
                result["version-rolling.mask"] = "%08x" % negotiated
                self.log(cid, "==",
                         "MILESTONE: configure — version-rolling AGREED "
                         "miner=%s pool=%s negotiated=%08x" %
                         (miner_mask_hex, state["args"].version_mask, negotiated), "32")
        for other in extensions:
            if other != "version-rolling" and other not in result:
                result[other] = False
        await self.send(cid, state, {"id": msg_id, "result": result, "error": None})

    async def on_subscribe(self, cid, state, msg_id, params):
        # Note: do NOT set subscribe_seen=True yet — UA filter still pending.
        # If we set it before the UA check, a rejected UA would still
        # advance the rotation index (engagement-gate counts subscribe_seen
        # as 'engaged').  Set subscribe_seen=True AFTER the UA filter passes.
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
        state["subscribe_seen"] = True
        if state["args"].subscribe_form == "nested":
            subs = [
                ["mining.set_difficulty", "b4b6693b72a50c7116db18d6497cac52"],
                ["mining.notify",         "ae6812eb4cd7735a302a8a9dd95cf71f"],
            ]
        else:
            subs = ["mining.notify", "ae6812eb4cd7735a302a8a9dd95cf71f"]
        result = [subs, state["args"].extranonce1, state["args"].extranonce2_size]
        self.log(cid, "==",
                 "MILESTONE: subscribe — UA=%r form=%s en1=%s en2_size=%d" %
                 (miner_ua, state["args"].subscribe_form, state["args"].extranonce1,
                  state["args"].extranonce2_size), "32")
        await self.send(cid, state, {"id": msg_id, "result": result, "error": None})
        if state["args"].push_set_version_mask and state["version_rolling_negotiated"]:
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
        if state["first_submit_ts"] is None:
            state["first_submit_ts"] = time.time()
        # Stratum mining.submit params order: [worker, job_id, en2, ntime, nonce, ...]
        sub_job_id = params[1] if len(params) > 1 else None
        is_our_job = sub_job_id is not None and sub_job_id in state["our_job_ids"]
        if is_our_job:
            state["submits_our"] += 1
            if state["first_submit_our_ts"] is None:
                state["first_submit_our_ts"] = time.time()
        origin = "OUR" if is_our_job else "STALE"
        self.log(cid, "==",
                 "MILESTONE: SUBMIT #%d (%s job=%s our=%d) params=%s" %
                 (state["submits"], origin, sub_job_id,
                  state["submits_our"], params), "32")
        if state["args"].reject_shares:
            await self.send(cid, state, {"id": msg_id, "result": False,
                                         "error": [23, "Low difficulty share", None]})
            if state["first_share_accepted"] is None:
                state["first_share_accepted"] = False
        else:
            await self.send(cid, state, {"id": msg_id, "result": True, "error": None})
            if state["first_share_accepted"] is None:
                state["first_share_accepted"] = True

    async def notify_loop(self, cid, state):
        try:
            await asyncio.sleep(state["args"].first_notify_delay)
            await self.push_set_difficulty(cid, state)
            await self.push_notify(cid, state, clean_jobs=True)
            state["first_notify_sent"] = True
            if state["args"].notify_interval > 0:
                while state["alive"]:
                    await asyncio.sleep(state["args"].notify_interval)
                    if not state["alive"]:
                        return
                    await self.push_notify(cid, state, clean_jobs=False)
        except asyncio.CancelledError:
            return

    async def push_set_difficulty(self, cid, state):
        await self.send(cid, state, {"id": None,
                                     "method": "mining.set_difficulty",
                                     "params": [state["args"].difficulty]})

    async def push_notify(self, cid, state, clean_jobs):
        self.job_seq += 1
        job_id = "%x" % self.job_seq
        state["our_job_ids"].add(job_id)
        prevhash = "00" * 32
        en1_bytes = len(state["args"].extranonce1) // 2
        scriptsig_len = en1_bytes + state["args"].extranonce2_size  # must be < 253
        # IMPORTANT: explicit `+` between every fragment.  Python concatenates
        # adjacent string literals at parse time BEFORE `*` applies, so a
        # bareword chain like
        #     "01000000" "01" "00" * 32 + ...
        # parses as ("010000000100") * 32 — 192 garbage bytes — NOT what
        # you want for the prev_txid field.  This bug shipped a malformed
        # coinbase tx for the entire 2026-05-04 wire-shape probe matrix
        # and explains why FR-1.15 firmware accepted the handshake but
        # silently refused to mine: it validates coinbase structure, saw
        # garbage where prev_txid should be, declined to submit.
        coinb1 = ("01000000"                                # tx version (LE int32)
                  + "01"                                    # input count varint
                  + "00" * 32                               # prev txid (32 zero bytes — coinbase)
                  + "ffffffff"                              # prev vout
                  + ("%02x" % scriptsig_len))               # scriptSig length varint
        coinb2 = ("ffffffff"                                # input sequence
                  + "01"                                    # output count varint
                  + "00f2052a01000000"                      # value (50 BTC LE)
                  + "1976a914"                              # OP_DUP OP_HASH160 push20
                  + "00" * 20                               # 20-byte P2PKH addr (zeros)
                  + "88ac"                                  # OP_EQUALVERIFY OP_CHECKSIG
                  + "00000000")                             # locktime
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
                await asyncio.sleep(state["args"].keepalive_interval)
                if not state["alive"]:
                    return
                await self.send(cid, state, {"id": int(time.time()),
                                             "method": "client.get_version",
                                             "params": []})
        except asyncio.CancelledError:
            return

    # ------------------------------------------------------------------
    # Rotation: hypothesis monitor, completion recording, summary
    # ------------------------------------------------------------------

    async def hypothesis_monitor(self, cid, state):
        """Decide when this connection's hypothesis is 'complete' and close
        our side of the socket so the miner reconnects → next hypothesis."""
        try:
            tags = state["hyp_tags"]
            args = self.args  # rotation timings come from the SERVER args, not eff_args
            if "silent" in tags:
                window = args.rotate_silent_window
                term_label = "our_close_silent_window"
            elif "expects_reject" in tags:
                window = args.rotate_reject_window
                term_label = "our_close_reject_window"
            else:
                window = args.rotate_success_window
                term_label = "our_close_after_window"

            min_submits = args.rotate_min_submits

            deadline = state["conn_start"] + window
            while state["alive"]:
                now = time.time()
                # Success-path early-exit: enough submits collected.
                # Gate on submits_our (our-job-id-matched), not the raw
                # total — chip-flush stale-nonce floods can otherwise
                # trigger early-exit and contaminate the verdict for a
                # hypothesis that, against our work, would have been silent.
                if ("silent" not in tags
                        and "expects_reject" not in tags
                        and min_submits > 0
                        and state["submits_our"] >= min_submits):
                    state["terminated_by"] = "our_close_min_submits"
                    self._close_for_rotation(cid, state,
                                             "min_submits_our reached (%d, total=%d)" %
                                             (state["submits_our"], state["submits"]))
                    return
                if now >= deadline:
                    state["terminated_by"] = term_label
                    self._close_for_rotation(cid, state, "window=%.0fs elapsed" % window)
                    return
                await asyncio.sleep(min(0.5, deadline - now))
        except asyncio.CancelledError:
            return

    def _close_for_rotation(self, cid, state, reason):
        self.log(cid, "==",
                 "HYPOTHESIS COMPLETE (server-close): name=%s reason=%s submits=%d" %
                 (state["hyp_name"], reason, state["submits"]), "1;35")
        state["alive"] = False
        try:
            state["writer"].close()
        except Exception:
            pass

    def _record_completion(self, cid, state):
        dur = time.time() - state["conn_start"]
        first_ms = (None if state["first_submit_ts"] is None
                    else int((state["first_submit_ts"] - state["conn_start"]) * 1000))
        first_our_ms = (None if state["first_submit_our_ts"] is None
                        else int((state["first_submit_our_ts"] - state["conn_start"]) * 1000))
        row = OrderedDict([
            ("timestamp", time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())),
            ("cid", cid),
            ("hypothesis_idx", state["hyp_idx"]),
            ("hypothesis_name", state["hyp_name"]),
            ("hypothesis_expect", state["hyp_expect"]),
            ("configure_seen", int(state["configure_seen"])),
            ("subscribe_seen", int(state["subscribe_seen"])),
            ("authorize_seen", int(state["authorize_seen"])),
            ("submits", state["submits"]),
            ("submits_our", state["submits_our"]),
            ("duration_sec", "%.2f" % dur),
            ("terminated_by", state["terminated_by"] or "unknown"),
            ("first_submit_ms", "" if first_ms is None else first_ms),
            ("first_submit_our_ms", "" if first_our_ms is None else first_our_ms),
            ("first_share_accepted",
             "" if state["first_share_accepted"] is None
             else int(bool(state["first_share_accepted"]))),
        ])
        self.results.append(row)
        # Compute "next" hypothesis for the log line.
        next_label = "(end)"
        if self.args.rotate_loop or (self.rotate_idx + 1) < len(self.matrix):
            ni = (self.rotate_idx + 1) % len(self.matrix)
            next_label = self.matrix[ni]["name"]
        self.log(cid, "==",
                 "HYPOTHESIS RESULT: name=%s submits=%d our=%d dur=%.1fs term=%s next=%s" %
                 (state["hyp_name"], state["submits"], state["submits_our"], dur,
                  state["terminated_by"] or "unknown", next_label), "1;35")
        if self.results_csv_writer:
            self.results_csv_writer.writerow(row)

    def print_summary(self):
        if not self.results:
            return
        # Aggregate by hypothesis name across all cycles seen so far.
        agg = defaultdict(lambda: {
            "cycles": 0, "submits_total": 0, "submits_our_total": 0,
            "configure": 0, "subscribe": 0, "authorize": 0,
            "peer_close": 0, "server_close": 0,
            "cycles_with_our_submit": 0,
        })
        for row in self.results:
            a = agg[row["hypothesis_name"]]
            a["cycles"] += 1
            a["submits_total"] += int(row["submits"])
            a["submits_our_total"] += int(row.get("submits_our", 0) or 0)
            a["configure"] += int(row["configure_seen"])
            a["subscribe"] += int(row["subscribe_seen"])
            a["authorize"] += int(row["authorize_seen"])
            term = row["terminated_by"] or ""
            if term.startswith("peer_"):
                a["peer_close"] += 1
            elif term.startswith("our_"):
                a["server_close"] += 1
            if int(row.get("submits_our", 0) or 0) > 0:
                a["cycles_with_our_submit"] += 1

        print("[%s] === ROTATION SUMMARY (%d total cycles, %d distinct hypotheses) ===" %
              (now_ts(), len(self.results), len(agg)), flush=True)
        # Header
        print("  %-26s  %5s  %4s  %4s  %4s  %6s  %5s  %4s  %4s  %4s" %
              ("hypothesis", "cyc", "cfg", "sub", "auth",
               "subm", "our", "wonc", "psh", "ssh"), flush=True)
        # Preserve matrix order for printing.
        ordered_names = [h["name"] for h in self.matrix]
        for name in ordered_names + [n for n in agg if n not in ordered_names]:
            if name not in agg:
                continue
            a = agg[name]
            print("  %-26s  %5d  %4d  %4d  %4d  %6d  %5d  %4d  %4d  %4d" %
                  (name, a["cycles"], a["configure"], a["subscribe"],
                   a["authorize"], a["submits_total"], a["submits_our_total"],
                   a["cycles_with_our_submit"],
                   a["peer_close"], a["server_close"]), flush=True)
        print("  legend: cyc=cycles cfg=configure-seen sub=subscribe-seen "
              "auth=authorize-seen subm=total-submits-incl-stale "
              "our=our-job-id-matched-submits "
              "wonc=cycles-with-at-least-1-our-submit "
              "psh=peer-close ssh=server-close",
              flush=True)


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
    # ---- Rotating-hypothesis mode --------------------------------------
    p.add_argument("--rotate", action="store_true",
                   help="enable rotating-hypothesis mode: each new TCP "
                        "connection gets the next hypothesis from the matrix; "
                        "the probe closes our side once a completion criterion "
                        "is met so the miner reconnects and rolls to the next.")
    p.add_argument("--rotate-file", default=None,
                   help="JSON file with custom hypothesis matrix (list of "
                        "{name, overrides, expect, tags}); defaults to the "
                        "built-in 19-row matrix when --rotate is on.")
    p.add_argument("--start-at", default=None,
                   help="skip ahead to this hypothesis name on first connection")
    p.add_argument("--rotate-loop", action="store_true",
                   help="cycle the matrix forever; default is one full pass "
                        "then stop scheduling new hypotheses (existing "
                        "connections continue)")
    p.add_argument("--rotate-success-window", type=float, default=60.0,
                   help="seconds to keep a 'works'-class hypothesis open "
                        "before closing our side (success path)")
    p.add_argument("--rotate-silent-window", type=float, default=320.0,
                   help="seconds to keep a 'silent_*'-tagged hypothesis open; "
                        "slightly past the FR-1.15 300s drop")
    p.add_argument("--rotate-reject-window", type=float, default=30.0,
                   help="hard cap for 'expects_reject'-tagged hypotheses if "
                        "the miner doesn't close on its own first")
    p.add_argument("--rotate-min-submits", type=int, default=5,
                   help="for success-path hypotheses, close after this many "
                        "submits if reached before --rotate-success-window")
    p.add_argument("--rotate-exit-after-pass", action="store_true",
                   help="exit the probe cleanly after one full matrix pass "
                        "and the end-of-pass summary (mutually useful with "
                        "no --rotate-loop, for finite captures)")
    p.add_argument("--results-csv", default=None,
                   help="append one row per completed hypothesis cycle to "
                        "this CSV (created if missing, headers written then)")
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
