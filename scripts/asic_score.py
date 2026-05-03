#!/usr/bin/env python3
"""
asic_score.py — comparative pool scoring from a single vantage point.

Performs the FR-1.15 stratum handshake against multiple pools in parallel,
measures every protocol-step latency, optionally submits a synthetic share
to time the reject path, and prints a head-to-head scoring table.

Run from BOTH the user's workstation and Alexey's prod node; the two sets
of scores reveal whether path/peering or pool-side processing dominates
the FR-1.15 firmware's pool-ranking decision.

Usage:
    python3 asic_score.py                       # default 3 pools, 60s
    python3 asic_score.py --duration 30
    python3 asic_score.py --target ours,p2p-spb.xyz:9338
    python3 asic_score.py --submit-all          # also fake-submit to kr1z1s
    python3 asic_score.py --json                # machine-readable output

Default targets (in order):
    ours      = 109.161.52.148:9348            (Alexey, Yaroslavl)
    spb       = p2p-spb.xyz:9338               (kr1z1s, St Petersburg)
    rov       = rov.p2p-spb.xyz:9338           (kr1z1s, Rostov-on-Don)
"""
import argparse
import asyncio
import json
import socket
import sys
import time

DEFAULT_TARGETS = [
    ("ours", "109.161.52.148", 9348),
    ("spb",  "p2p-spb.xyz",    9338),
    ("rov",  "rov.p2p-spb.xyz", 9338),
]

UA_FR115 = "Antminer S21+/Tue Apr 22 15:05:57 CST 2025"
ADDRESS  = "qqncfzq2hp6hqj9899j5j5gwpslwu4ash5tqs25907"
WORKER   = "score-fr115"


def ms_since(t0):
    return (time.time() - t0) * 1000.0


async def measure_pool(label, host, port, duration, do_submit):
    """Run a single FR-1.15 handshake + observation + optional fake submit.
    Return a dict of metrics."""
    metrics = {
        "label": label, "host": host, "port": port,
        "dns_ms": None, "connect_ms": None,
        "subscribe_ms": None, "authorize_ms": None, "configure_ms": None,
        "first_notify_ms": None, "first_setdiff_ms": None,
        "notify_count": 0, "setdiff_count": 0,
        "initial_diff": None, "wire_form": None, "jsonrpc20_field": None,
        "submit_ack_ms": None, "submit_error_code": None, "submit_error_msg": None,
        "bytes_in": 0, "lines_in": 0,
        "session_alive_ms": None, "closed_by_peer": False,
        "error": None,
    }

    # DNS resolve (separated so we can attribute time to it)
    try:
        t0 = time.time()
        infos = await asyncio.get_event_loop().getaddrinfo(host, port, family=socket.AF_INET, type=socket.SOCK_STREAM)
        metrics["dns_ms"] = ms_since(t0)
        sockaddr = infos[0][-1]
    except Exception as e:
        metrics["error"] = "dns: %s" % e
        return metrics

    # TCP connect
    try:
        t0 = time.time()
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host=sockaddr[0], port=sockaddr[1]),
            timeout=10.0,
        )
        metrics["connect_ms"] = ms_since(t0)
    except Exception as e:
        metrics["error"] = "connect: %s" % e
        return metrics

    # disable Nagle on our client side too
    try:
        sock = writer.get_extra_info("socket")
        if sock is not None:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except Exception:
        pass

    session_start = time.time()
    next_id = [1]
    pending = {}

    async def send(method, params):
        rid = next_id[0]; next_id[0] += 1
        obj = {"id": rid, "method": method, "params": params}
        line = (json.dumps(obj) + "\n").encode()
        t = time.time()
        writer.write(line)
        await writer.drain()
        pending[rid] = (method, t)
        return rid

    # 1. mining.configure (BIP310 version-rolling)
    cfg_id = await send("mining.configure",
                        [["version-rolling"],
                         {"version-rolling.mask": "1fffe000",
                          "version-rolling.min-bit-count": 16}])
    # 2. mining.subscribe
    sub_id = await send("mining.subscribe", [UA_FR115])
    # 3. mining.authorize
    full_worker = "%s.%s" % (ADDRESS, WORKER)
    auth_id = await send("mining.authorize", [full_worker, "x"])

    submit_id_holder = [None]   # set when we send fake submit
    deadline = time.time() + duration

    while time.time() < deadline:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        except Exception as e:
            metrics["error"] = "read: %s" % e
            break
        if not line:
            metrics["closed_by_peer"] = True
            break
        metrics["bytes_in"] += len(line)
        metrics["lines_in"] += 1
        try:
            msg = json.loads(line.decode("utf-8", errors="replace"))
        except Exception:
            continue

        # detect JSON-RPC 2.0 marker (legacy 1.0 = no field; 2.0 = "jsonrpc":"2.0")
        if metrics["jsonrpc20_field"] is None:
            metrics["jsonrpc20_field"] = (msg.get("jsonrpc") == "2.0")

        msg_id = msg.get("id")
        method = msg.get("method")

        if method is None:
            # response to one of our requests
            pend = pending.pop(msg_id, None)
            if pend is None:
                continue
            pend_method, pend_t = pend
            ms = ms_since(pend_t)
            if pend_method == "mining.configure":
                metrics["configure_ms"] = ms
            elif pend_method == "mining.subscribe":
                metrics["subscribe_ms"] = ms
                # introspect subscribe shape — kr1z1s/ckpool send nested
                # [[notify, subid], [setdiff, subid]] vs flat [notify, subid]
                result = msg.get("result")
                if isinstance(result, list) and result:
                    inner = result[0]
                    if isinstance(inner, list) and inner and isinstance(inner[0], list):
                        metrics["wire_form"] = "nested"
                    else:
                        metrics["wire_form"] = "flat"
            elif pend_method == "mining.authorize":
                metrics["authorize_ms"] = ms
            elif pend_method == "mining.submit":
                metrics["submit_ack_ms"] = ms
                err = msg.get("error")
                if err is not None:
                    if isinstance(err, list) and len(err) >= 2:
                        metrics["submit_error_code"] = err[0]
                        metrics["submit_error_msg"] = err[1]
                    else:
                        metrics["submit_error_msg"] = repr(err)
        else:
            params = msg.get("params") or []
            if method == "mining.set_difficulty":
                metrics["setdiff_count"] += 1
                if metrics["first_setdiff_ms"] is None:
                    metrics["first_setdiff_ms"] = ms_since(session_start)
                if metrics["initial_diff"] is None and params:
                    metrics["initial_diff"] = params[0]
            elif method == "mining.notify":
                metrics["notify_count"] += 1
                if metrics["first_notify_ms"] is None:
                    metrics["first_notify_ms"] = ms_since(session_start)
                # send fake submit on first notify (only to "ours" by default)
                if do_submit and submit_id_holder[0] is None and params:
                    jobid = params[0]
                    submit_id_holder[0] = await send(
                        "mining.submit",
                        [full_worker, jobid, "deadbeef", "00000000", "cafef00d", "1fffe000"],
                    )

    metrics["session_alive_ms"] = ms_since(session_start)
    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass
    return metrics


def composite_score(m):
    """Lower = better.  Weighted sum of step latencies + small penalties.
    Designed to approximate what FR-1.15 firmware's ranking heuristic might
    care about: connect speed, time-to-first-notify, submit-ack speed.
    Skipped fields contribute their typical-bad-case values."""
    if m["error"]:
        return float("inf")
    score = 0.0
    score += (m["connect_ms"]      or 500.0)
    score += (m["subscribe_ms"]    or 500.0)
    score += (m["authorize_ms"]    or 500.0)
    score += (m["first_notify_ms"] or 5000.0) * 0.5  # weight halved (already includes earlier RTTs)
    if m["submit_ack_ms"] is not None:
        score += m["submit_ack_ms"] * 0.5
    if m["closed_by_peer"]:
        score += 1000.0
    return score


def fmt(v, suffix=""):
    if v is None:
        return "-"
    if isinstance(v, float):
        return "%.1f%s" % (v, suffix)
    return "%s%s" % (v, suffix)


def render_table(results):
    cols = [("label", "pool"),
            ("dns_ms", "dns"),
            ("connect_ms", "connect"),
            ("configure_ms", "configure"),
            ("subscribe_ms", "subscribe"),
            ("authorize_ms", "authorize"),
            ("first_notify_ms", "1st-notify"),
            ("notify_count", "notifies"),
            ("initial_diff", "init-diff"),
            ("wire_form", "form"),
            ("jsonrpc20_field", "rpc2.0?"),
            ("submit_ack_ms", "submit-ack"),
            ("submit_error_msg", "reject-msg"),
            ("score", "SCORE"),
            ("error", "error")]
    # build score
    for m in results:
        m["score"] = composite_score(m)

    # column widths
    widths = {}
    for k, h in cols:
        widths[k] = len(h)
    rows = []
    for m in results:
        row = {}
        for k, _ in cols:
            v = m.get(k)
            if k in ("connect_ms", "configure_ms", "subscribe_ms", "authorize_ms",
                     "first_notify_ms", "submit_ack_ms", "dns_ms"):
                s = fmt(v, "ms")
            elif k == "score":
                s = "inf" if v == float("inf") else "%.0f" % v
            elif k == "initial_diff" and isinstance(v, (int, float)):
                s = "%.3g" % v
            elif v is True:
                s = "yes"
            elif v is False:
                s = "no"
            else:
                s = fmt(v)
            row[k] = s
            widths[k] = max(widths[k], len(s))
        rows.append(row)

    # render header
    header = "  ".join(h.ljust(widths[k]) for k, h in cols)
    sep    = "  ".join("-" * widths[k] for k, _ in cols)
    print(header)
    print(sep)
    # sort by score
    rows_sorted = sorted(rows, key=lambda r: float("inf") if r["score"] == "inf" else float(r["score"]))
    for r in rows_sorted:
        print("  ".join(r[k].ljust(widths[k]) for k, _ in cols))


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--target", action="append", default=None,
                   help="comma-separated label=host:port (repeatable). "
                        "Defaults to ours/spb/rov.")
    p.add_argument("--duration", type=float, default=60.0,
                   help="session duration in seconds (default 60)")
    p.add_argument("--submit-all", action="store_true",
                   help="fake-submit to ALL targets (default: only --submit-ours)")
    p.add_argument("--submit-ours", action="store_true", default=True,
                   help="fake-submit to 'ours' only (default true)")
    p.add_argument("--no-submit", action="store_true",
                   help="don't fake-submit anywhere")
    p.add_argument("--json", action="store_true", help="print JSON output instead of table")
    args = p.parse_args()

    # parse targets
    if args.target:
        targets = []
        for t in args.target:
            for piece in t.split(","):
                if "=" in piece:
                    label, hp = piece.split("=", 1)
                else:
                    label, hp = piece, piece
                host, _, port = hp.rpartition(":")
                targets.append((label, host, int(port)))
    else:
        targets = DEFAULT_TARGETS

    # decide which targets get fake submits
    if args.no_submit:
        submit_set = set()
    elif args.submit_all:
        submit_set = {t[0] for t in targets}
    else:
        submit_set = {"ours"}

    print("# asic_score.py — duration=%ds, submit-set=%s" % (
        int(args.duration), sorted(submit_set)), file=sys.stderr)
    print("# vantage: %s" % socket.gethostname(), file=sys.stderr)

    results = await asyncio.gather(*[
        measure_pool(label, host, port, args.duration, label in submit_set)
        for label, host, port in targets
    ])

    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        render_table(results)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
