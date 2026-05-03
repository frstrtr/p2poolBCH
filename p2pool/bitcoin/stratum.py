import json
import os
import random
import sys
import time

from twisted.internet import defer, protocol, reactor
from twisted.web import client as web_client
from twisted.python import log

from p2pool.bitcoin import data as bitcoin_data, getwork
from p2pool.util import expiring_dict, jsonrpc, pack

def clip(num, bot, top):
    return min(top, max(bot, num))

# ── Diagnostic / compatibility toggles ────────────────────────────────────
# Set the env var to "1" / "true" / "yes" to disable the feature.  Useful
# for A/B testing which post-handshake server-pushed RPC a strict-firmware
# miner (e.g. Antminer S21+ on stock) is rejecting.  Leave unset for the
# default behaviour.  Values are read once at module load — restart the
# p2pool process for changes to take effect.
def _envflag(name):
    return os.environ.get(name, '').strip().lower() in ('1', 'true', 'yes', 'on')

def _envfloat(name, default=0.0):
    try:
        return float(os.environ.get(name, '').strip() or default)
    except ValueError:
        return default

DISABLE_ASICBOOST    = _envflag('STRATUM_DISABLE_ASICBOOST')
DISABLE_LATENCY_PING = _envflag('STRATUM_DISABLE_LATENCY_PING')
DISABLE_IDLE_NUDGE   = _envflag('STRATUM_DISABLE_IDLE_NUDGE')
# Send mining.notify version/nbits/ntime hex fields as little-endian byte
# sequences instead of big-endian (the legacy behaviour).  p2pool-cc and
# some custom forks default to LE; the stratum protocol never settled on
# one and historic implementations diverge.  Symptoms of the wrong order
# on a strict-firmware miner: the connection handshakes cleanly, jobs are
# accepted by the firmware's parser, but **no shares are ever submitted**
# — the firmware's internal timestamp/target sanity check decides every
# computed share is invalid (often manifests as 'stratum time … seconds
# in the future' on miners that log).  Antminer S21+ stock FR-1.15 is a
# suspected case (FR-1.42 / vnish / T21 / S19 / bitaxe all submit fine
# under the BE default).  Default off = legacy BE behaviour.  Toggle on
# only if a strict-firmware miner is connecting + handshake-completing
# but never submitting, AND raising STRATUM_MIN_INITIAL_DIFF didn't help.
NOTIFY_HEX_LE        = _envflag('STRATUM_NOTIFY_HEX_LE')
# Diff escalation experiment: when set to N seconds (>0) and no submits
# have arrived from a session within N seconds, multiply the effective
# diff by VARDIFF_CLIP (= the same factor vardiff itself uses at max
# ramp-up speed for an actively submitting miner).  Continues each step
# every N seconds until either (a) the first submit arrives (vardiff
# takes over), (b) the connection drops, or (c) the diff hits
# RATCHET_MAX (default 2^24 = 16,777,216).
#
# Use case: experimentally probe the FR-1.15 firmware threshold by
# escalating diff at the SAME natural pace vardiff would use for an
# active miner — no magic numbers, just self-driven vardiff for a
# session that has no actual samples to drive the loop.
# Pre-condition: STRATUM_FIXED_INITIAL_DIFF must be set (gives us a
# defined starting value); the ratchet multiplies that base by
# VARDIFF_CLIP**N per step.
#
# Recommended: STRATUM_NO_SUBMIT_RATCHET_SECONDS=30 with default
# VARDIFF_CLIP=10.0 sweeps 65k → 650k → 6.5M → 16M (cap) over
# ~90 seconds — natural aggressive ramp-up rate.
# With VARDIFF_CLIP=2.0 (kr1z1s-style), same period sweeps slower:
# 65k → 130k → 260k → 520k → 1M ... over 5 steps in 150 seconds.
# Default 0 = off.
NO_SUBMIT_RATCHET_SECONDS = _envfloat('STRATUM_NO_SUBMIT_RATCHET_SECONDS', 0.0)
NO_SUBMIT_RATCHET_MAX     = int(_envfloat('STRATUM_NO_SUBMIT_RATCHET_MAX', 16777216))
# Alternative ratchet mode: ratchet on every NOTIFY (every new job)
# instead of on a fixed time interval.  Per kr1z1s operator guidance:
# "I used to have the difficulty set to two in various powers, such as
# 8, 9, 10, 11" — i.e., each new job/notify advances to the next
# power-of-2 diff.  When STRATUM_RATCHET_PER_NOTIFY=1 is set, the
# step increments after every _send_work call (one per notify) until
# the first submit OR the cap.  Coexists with seconds-based ratchet:
# whichever fires first applies.  Default off.
RATCHET_PER_NOTIFY        = _envflag('STRATUM_RATCHET_PER_NOTIFY')
# Forced initial pseudoshare difficulty (in stratum diff units).  When
# set to a positive value, _send_work clamps the per-session vardiff
# state to EXACTLY this value on the first notify, ignoring the
# natural local-hash-rate-derived target entirely.  This is stricter
# than STRATUM_MIN_INITIAL_DIFF (which is only a floor, leaving the
# natural value to apply when it's already higher).  Use this when an
# external doc / firmware spec calls for a specific value (e.g. FR-1.15
# documented threshold = 65536 exactly) and you want zero ambiguity in
# what the miner sees.  Vardiff still ratchets normally after the first
# accepted submit.  Default 0 = no override (natural calculation, plus
# any STRATUM_MIN_INITIAL_DIFF floor).
FIXED_INITIAL_DIFF   = _envfloat('STRATUM_FIXED_INITIAL_DIFF', 0.0)
# Minimum initial pseudoshare difficulty floor (in stratum diff units).
# Stock Bitmain firmware (Antminer S21+ FR-1.15+) silently refuses to
# submit shares when set_difficulty is below an internal threshold.
# External documentation (and 2026-05-03 empirical confirmation) places
# the FR-1.15 threshold at 65536 — pools offering lower initial diff
# are treated as "garbage traffic" and the firmware refuses to hash at
# all on them.  Without submits, vardiff has no samples to adapt and
# stays stuck at the cold-start seed — the firmware then closes the
# connection cleanly after ~90 s ("no shares accepted in N s, try the
# backup pool"), producing the warm-backup keepalive cycle.
# Setting this floor ensures the *first* notify already meets the
# firmware's threshold; vardiff samples on the first submit and takes
# over normally from there.  Vnish, T21, S19, and bitaxe all submit
# fine at lower diffs so they aren't broken by raising this floor —
# they just produce shares less often.  Default 0 = no floor (legacy
# behaviour).  **Recommended: 65536 for fleets containing stock
# Antminer S21+ FR-1.15.**  Lower values (16k-32k) leave the firmware
# in garbage-traffic perception even though they were proposed in
# earlier internal notes — those notes were incomplete.  Clamped to
# consensus min_share_target inside _send_work so we never produce
# sub-consensus pseudoshares.
MIN_INITIAL_DIFF     = _envfloat('STRATUM_MIN_INITIAL_DIFF', 0.0)
# Length of the server-assigned extranonce1 prefix, in BYTES.  Stratum
# convention: the pool reserves a session-unique extranonce1 prefix and
# the miner extends it with extranonce2 to form the full coinbase nonce
# slot.  jtoomim p2pool BCH sends extranonce1="" (zero bytes) +
# extranonce2_size=COINBASE_NONCE_LENGTH (4) historically.  Strict
# firmware (observed: Antminer S21+ stock FR-1.15) silently chokes on
# the empty extranonce1 and never submits shares — this is the most
# likely root cause of the 145 s 0-submit clean-FIN cycle and matches
# the behaviour seen on kr1z1s (p2p-spb.xyz, version 77.0.0-12-g5493200)
# which assigns a 1-byte extranonce1 ("fa") and en2_size=3 and is
# observed handling FR-1.15 fine.  Set this to 1 (or higher) to match
# ckpool / slush / NiceHash convention; extranonce2_size shrinks by the
# same amount so the total nonce slot stays at COINBASE_NONCE_LENGTH.
# Each stratum session generates a fresh os.urandom() value so
# different connections get distinct extranonce1 prefixes.  Default 0 =
# legacy empty extranonce1.  Recommended: 1 if any miner in the fleet
# is Antminer stock-firmware FR-1.15 or earlier.
EXTRANONCE1_LEN      = int(_envfloat('STRATUM_EXTRANONCE1_LEN', 0))
# Defer the first set_difficulty/notify push until *after* mining.authorize
# completes, instead of also firing one immediately after mining.subscribe.
# Legacy behaviour: rpc_subscribe schedules a _send_work, then rpc_authorize
# also schedules one — producing TWO consecutive clean=true notifies within
# ~250 ms of each other.  Kr1z1s (and ckpool / NiceHash / slush) send ONE
# notify after the full handshake.  Strict CGMiner branches in stock Bitmain
# firmware (Antminer S21+ FR-1.15) may interpret two clean=true notifies
# within sub-second as "the pool just had a transient reset" and lose trust
# (-> 145 s 0-submit cycle).  Discovered via paired wire-tap with
# scripts/asic_simulator.py.  Set STRATUM_NOTIFY_AFTER_AUTH=1 to skip the
# subscribe-time send_work call; authorize-time send_work is unchanged so
# the miner still gets work as soon as the handshake completes.  Default
# off = legacy double-notify.  Recommended on if any FR-1.15 stock miners
# are connecting.
NOTIFY_AFTER_AUTH    = _envflag('STRATUM_NOTIFY_AFTER_AUTH')
# Strict slush/NiceHash-style mining.subscribe response (nested array of
# (method, id) pairs, includes mining.set_difficulty subscription).
# Required by some Bitmain stock firmware (S21+ stock) which enforces the
# spec literally; the older flat form ['mining.notify', id] is silently
# accepted by bitaxe/Whatsminer/vnish but rejected by strict CGMiner branches.
NICEHASH_COMPAT      = _envflag('STRATUM_NICEHASH_COMPAT')
# Per-connection dialog trace: dumps every send/recv with timestamp,
# peer IP, worker name, method, and key params.  Reconstructs the seconds
# leading up to a disconnect from journald.  Verbose — leave off in
# steady state; flip on right before reproducing a disconnect.
TRACE                = _envflag('STRATUM_TRACE')
# Vardiff per-ratchet clip factor.  The vardiff multiplier is computed as
# (actual_interval / desired_interval) and then clamped to [1/F, F] before
# being applied to self.target.  Default F=10.0 matches the LTC+DOGE
# merged-mining fork (commit 1d66c752) which proved this in production:
# wider per-ratchet bound reaches steady-state in a single step rather
# than chasing it over many small adjustments.  The narrow (0.5, 2.0)
# upstream default forces high-hashrate ASICs (Antminer S21+ class) to
# spend dozens of ratchets converging on equilibrium diff, during which
# every mismatch between pool ratchet and miner-side set_difficulty
# adoption produces a reject — stock FR-1.15 firmware tolerates this
# poorly and silently drops the connection.  One big jump beats many
# small ones.  Override with STRATUM_VARDIFF_CLIP=<F>.  Must be > 1.0.
VARDIFF_CLIP         = max(1.001, _envfloat('STRATUM_VARDIFF_CLIP', 10.0))
if DISABLE_ASICBOOST:
    print 'STRATUM: ASICBoost (BIP310 version-rolling) DISABLED via STRATUM_DISABLE_ASICBOOST'
if DISABLE_LATENCY_PING:
    print 'STRATUM: client.get_version latency ping DISABLED via STRATUM_DISABLE_LATENCY_PING'
if DISABLE_IDLE_NUDGE:
    print 'STRATUM: idle-reconnect nudge DISABLED via STRATUM_DISABLE_IDLE_NUDGE'
if NICEHASH_COMPAT:
    print 'STRATUM: NiceHash-compatible subscribe response ENABLED via STRATUM_NICEHASH_COMPAT'
if TRACE:
    print 'STRATUM: per-connection dialog trace ENABLED via STRATUM_TRACE'
if MIN_INITIAL_DIFF > 0:
    print 'STRATUM: minimum pseudoshare-difficulty floor = %g via STRATUM_MIN_INITIAL_DIFF' % MIN_INITIAL_DIFF
if FIXED_INITIAL_DIFF > 0:
    print 'STRATUM: FORCED initial pseudoshare difficulty = %g via STRATUM_FIXED_INITIAL_DIFF (overrides natural calc)' % FIXED_INITIAL_DIFF
if NO_SUBMIT_RATCHET_SECONDS > 0:
    print 'STRATUM: no-submit DIFF RATCHET = x%.3g every %.1fs (cap %d) via STRATUM_NO_SUBMIT_RATCHET_SECONDS' % (VARDIFF_CLIP, NO_SUBMIT_RATCHET_SECONDS, NO_SUBMIT_RATCHET_MAX)
if RATCHET_PER_NOTIFY:
    print 'STRATUM: per-notify DIFF RATCHET = x%.3g every new job (cap %d) via STRATUM_RATCHET_PER_NOTIFY' % (VARDIFF_CLIP, NO_SUBMIT_RATCHET_MAX)
if NOTIFY_HEX_LE:
    print 'STRATUM: mining.notify version/nbits/ntime hex sent as LITTLE-ENDIAN via STRATUM_NOTIFY_HEX_LE'
if EXTRANONCE1_LEN > 0:
    print 'STRATUM: server-assigned extranonce1 = %d byte(s) per session via STRATUM_EXTRANONCE1_LEN' % EXTRANONCE1_LEN
if NOTIFY_AFTER_AUTH:
    print 'STRATUM: first work-push DEFERRED to post-authorize (no double-notify) via STRATUM_NOTIFY_AFTER_AUTH'
if VARDIFF_CLIP != 10.0:
    print 'STRATUM: vardiff per-ratchet clip = (%.3f, %.3f) via STRATUM_VARDIFF_CLIP=%.3f' % (1.0/VARDIFF_CLIP, VARDIFF_CLIP, VARDIFF_CLIP)
else:
    print 'STRATUM: vardiff per-ratchet clip = (0.100, 10.000) (default; matches LTC+DOGE merged-v36)'

def _ts():
    t = time.time()
    return time.strftime('%H:%M:%S', time.localtime(t)) + ('.%03d' % int((t % 1) * 1000))

class StratumRPCMiningProvider(object):
    def __init__(self, wb, other, transport):
        self.pool_version_mask = 0x1fffe000
        self.wb = wb
        self.other = other
        self.transport = transport
        
        self.username = None
        self.handler_map = expiring_dict.ExpiringDict(300)
        
        self.watch_id = self.wb.new_work_event.watch(self._send_work)

        self.recent_shares = []
        self.target = None
        self.share_rate = wb.share_rate
        self.fixed_target = False
        self.desired_pseudoshare_target = None
        self._ping_active = False
        self._ping_call = None
        self._last_reconnect_at = 0  # timestamp of last client.reconnect sent
        # FR-1.15 quirk #1: jobids MUST be monotonically increasing.  The
        # Amlogic Stratum dispatcher in stock Bitmain firmware sometimes
        # holds onto previous work for 10-30s if the new jobid is "less
        # than" the previous one in lexicographical order.  Random ints
        # (our previous behaviour) trigger this regularly.  Per-session
        # counter starting at 1 keeps every notify strictly newer than
        # the last.  Wraps at 2**32 (4 billion) — at ~30 notifies/sec
        # that's many years.  Outside the handler_map TTL (300s) so no
        # collision risk.
        self._jobid_counter = 0
        # FR-1.15 quirk #3: race condition where simultaneous arrival of
        # set_difficulty + notify can corrupt the miner's internal target
        # to 0 or 0xFFFF.  Workaround: only emit set_difficulty when the
        # diff actually changed, and pause briefly before the notify so
        # the miner has time to process the new diff before the new job.
        self._last_sent_diff = None
        # No-submit diff-ratchet state (env-gated via
        # STRATUM_NO_SUBMIT_RATCHET_SECONDS).  Each step doubles the
        # effective diff vs FIXED_INITIAL_DIFF; cancels on first submit
        # or connection close.
        self._ratchet_steps = 0
        self._ratchet_timer = None
        # Throttle the "deferred work send" warning during long startup
        # phases (peers connecting / sharechain download / bitcoind down)
        # so we log at most once per _DEFERRED_WORK_LOG_INTERVAL.
        self._deferred_work_logged_at = 0
        # Per-session server-assigned extranonce1 (see STRATUM_EXTRANONCE1_LEN).
        # When EXTRANONCE1_LEN==0 this is the empty string and the legacy
        # behaviour is preserved exactly: extranonce1="" is sent, the miner
        # owns the full COINBASE_NONCE_LENGTH-byte nonce slot.
        if EXTRANONCE1_LEN > 0:
            n = min(EXTRANONCE1_LEN, self.wb.COINBASE_NONCE_LENGTH - 1)
            self._extranonce1 = os.urandom(n)
        else:
            self._extranonce1 = b''

    def _trace(self, direction, method, **fields):
        if not TRACE:
            return
        try:
            ip = self.transport.getPeer().host
        except Exception:
            ip = '?'
        parts = ' '.join('%s=%s' % (k, v) for k, v in fields.items())
        print '[STRATUM_TRACE] %s [%s] [%s] %s %s%s%s' % (
            _ts(), ip, self.username or '-', direction, method,
            ' ' if parts else '', parts)

    def rpc_subscribe(self, miner_version=None, session_id=None, *args):
        self._trace('<--', 'subscribe', ua=repr(miner_version),
                    form=('nested' if NICEHASH_COMPAT else 'flat'))
        # Skip the subscribe-time send_work when STRATUM_NOTIFY_AFTER_AUTH is
        # set; rpc_authorize will fire send_work after the full handshake
        # completes — producing exactly ONE notify, matching kr1z1s / ckpool.
        if not NOTIFY_AFTER_AUTH:
            reactor.callLater(0, self._send_work)
        en1_hex = self._extranonce1.encode('hex')
        en2_size = self.wb.COINBASE_NONCE_LENGTH - len(self._extranonce1)
        # Subscription IDs: short, session-correlated (en1 prefix + index),
        # matching kr1z1s's wire format which is observed handling Antminer
        # S21+ stock FR-1.15 fine.  Strict CGMiner-derived parsers in stock
        # firmware may have fixed-size buffers for the subscription-id
        # string and overflow on the legacy 32-char hex constants.  When no
        # extranonce1 prefix is configured, fall back to short literals.
        id_prefix = en1_hex if en1_hex else 'p2p'
        notify_id   = id_prefix + '1'
        setdiff_id  = id_prefix + '2'
        if NICEHASH_COMPAT:
            # Strict slush/NiceHash form: subscription_details is a LIST of
            # (method, id) pairs.  The order matters: kr1z1s (and ckpool /
            # NiceHash) puts mining.notify FIRST then mining.set_difficulty.
            # Stock CGMiner forks have been observed position-parsing this
            # array and breaking when the order is reversed.
            return [
                [
                    ["mining.notify",         notify_id],
                    ["mining.set_difficulty", setdiff_id],
                ],
                en1_hex,
                en2_size,
            ]
        return [
            ["mining.notify", notify_id], # subscription details
            en1_hex, # extranonce1
            en2_size, # extranonce2_size
        ]
    
    def rpc_authorize(self, username, password):
        self._trace('<--', 'authorize', user=username)
        if not hasattr(self, 'authorized'): # authorize can be called many times in one connection
            print '>>>Authorize: %s from %s' % (username, self.transport.getPeer().host)
            self.authorized = username
        self.username = username.strip()
        
        self.user, self.address, self.desired_share_target, self.desired_pseudoshare_target = self.wb.get_user_details(username)
        try:
            peer_ip = self.transport.getPeer().host
            self.wb.worker_connected.happened(self.username, self.address, peer_ip)
        except:
            pass
        reactor.callLater(0, self._send_work)
        if not self._ping_active:
            self._ping_active = True
            self._ping_call = reactor.callLater(1, self._ping_once)  # first ping ~immediately
        return True

    # How long a worker can be connected-but-silent before we nudge it to
    # reconnect so it re-evaluates pool priorities (picks priority 1 = us).
    _IDLE_RECONNECT_AFTER = 15 * 60   # 15 min idle threshold
    _IDLE_RECONNECT_COOLDOWN = 15 * 60 # minimum gap between reconnect nudges

    # client.get_version cadence.  ALSO acts as a stratum keepalive — most
    # ASIC firmware enforces a client-side "no pool data for N seconds"
    # timeout (Antminer S21+ stock: 300 s).  Sending the ping just inside
    # that window prevents miners from declaring the connection lost
    # during quiet periods between mining.notify pushes.
    _LATENCY_PING_INTERVAL = 120  # seconds — well under the 300 s S21+ cutoff

    def _ping_once(self):
        self._ping_call = None
        if not self._ping_active or not self.username:
            return

        # ── idle-reconnect nudge ─────────────────────────────────────────────
        # If the worker hasn't submitted a share here in _IDLE_RECONNECT_AFTER
        # seconds, send client.reconnect so the miner drops and re-evaluates
        # pool priorities.  Braiins OS / most ASIC firmware will reconnect to
        # pool priority 1 (this node) after receiving client.reconnect.
        # Skip entirely when STRATUM_DISABLE_IDLE_NUDGE is set.
        if not DISABLE_IDLE_NUDGE:
            now = time.time()
            worker_info = self.wb.connected_workers.get(self.username)
            if worker_info is not None:
                last_submit = worker_info.get('last_submit_time', 0)
                connected_since = worker_info.get('since', now)
                idle_secs = now - last_submit if last_submit else now - connected_since
                cooldown_ok = now - self._last_reconnect_at > self._IDLE_RECONNECT_COOLDOWN
                if idle_secs >= self._IDLE_RECONNECT_AFTER and cooldown_ok:
                    self._last_reconnect_at = now
                    worker_info['reconnect_nudges'] = worker_info.get('reconnect_nudges', 0) + 1
                    try:
                        port = self.transport.getHost().port
                    except Exception:
                        port = 9348
                    # Send graceful client.reconnect; firmware will reconnect and
                    # re-evaluate priority list — priority 1 (this pool) wins.
                    self._trace('-->', 'reconnect', port=port, idle=int(idle_secs))
                    self.other.svc_client.rpc_reconnect('', port, 0).addErrback(
                        lambda err: self.transport.loseConnection()
                        # If miner doesn't support client.reconnect, force-drop TCP.
                        # The miner will reconnect immediately and pick priority 1.
                    )
                    if self._ping_active:
                        self._ping_call = reactor.callLater(self._LATENCY_PING_INTERVAL, self._ping_once)
                    return  # skip latency ping this cycle

        # Skip the client.get_version probe entirely when
        # STRATUM_DISABLE_LATENCY_PING is set.  Useful when strict firmware
        # mishandles unexpected server-pushed RPCs during/after the
        # subscribe→configure→authorize handshake.  Still reschedule the
        # tick so the idle-nudge check above keeps running unless that's
        # also disabled (in which case there's nothing left to do).
        if DISABLE_LATENCY_PING:
            if self._ping_active and not DISABLE_IDLE_NUDGE:
                self._ping_call = reactor.callLater(self._LATENCY_PING_INTERVAL, self._ping_once)
            return

        t0 = time.time()
        _username_snap = self.username  # snapshot before async callbacks
        self._trace('-->', 'get_version', purpose='keepalive+rtt')
        d = self.other.svc_client.rpc_get_version()
        def _record_rtt(rtt):
            now_t = time.time()
            w_info = self.wb.connected_workers.get(_username_snap)
            if w_info is not None:
                alpha = 0.2
                prev = w_info.get('latency', rtt)
                w_info['latency'] = alpha * rtt + (1.0 - alpha) * prev
            hist = self.wb.worker_latency_history.setdefault(_username_snap, [])
            hist.append((now_t, rtt))
            cutoff = now_t - 86400
            while hist and hist[0][0] < cutoff:
                hist.pop(0)
        def on_response(result):
            _record_rtt(time.time() - t0)
            if self._ping_active:
                self._ping_call = reactor.callLater(self._LATENCY_PING_INTERVAL, self._ping_once)
        def on_error(err):
            if err.check(defer.TimeoutError):
                # Miner silently ignored client.get_version (stock Antminer,
                # ESP-Miner/bitaxe, some Whatsminer fw).
                # Fallback chain:
                #   1. HTTP GET /api/system/info (bitaxe reports responseTime)
                #   2. Any HTTP response → use HTTP request time as RTT proxy
                #   3. HTTP fails → TCP connect to port 4028 (CGMiner API,
                #      open on Antminer/Avalon/most ASICs, no auth required)
                #   4. All fail → skip (latency stays '-')
                ip = self.wb.connected_workers.get(_username_snap or '', {}).get('ip')
                if ip:
                    http_t0 = [time.time()]
                    url = ('http://%s/api/system/info' % ip).encode('ascii')
                    http_d = web_client.getPage(url, timeout=3)
                    def _on_http(data):
                        # bitaxe reports its own measured pool RTT
                        try:
                            rt_ms = json.loads(data).get('responseTime')
                            if rt_ms is not None:
                                _record_rtt(float(rt_ms) / 1000.0)
                                return
                        except Exception:
                            pass
                        # Any other miner with a 200 response: use HTTP RTT
                        _record_rtt(time.time() - http_t0[0])
                    def _on_http_err(e):
                        from twisted.web import error as web_error
                        if e.check(web_error.Error):
                            # Got an HTTP error response (401/302/404 etc.) —
                            # TCP handshake succeeded so HTTP time is valid RTT
                            _record_rtt(time.time() - http_t0[0])
                        else:
                            # Port 80 not open or timed out — try TCP connect
                            # to port 4028 (CGMiner API, open on most ASICs)
                            tcp_t0 = [time.time()]
                            _ip = ip  # loop-safe copy
                            class _RTTProto(protocol.Protocol):
                                def connectionMade(self):
                                    _record_rtt(time.time() - tcp_t0[0])
                                    self.transport.loseConnection()
                            class _RTTFactory(protocol.ClientFactory):
                                def buildProtocol(self, addr): return _RTTProto()
                                def clientConnectionFailed(self, c, r): pass
                            reactor.connectTCP(_ip, 4028, _RTTFactory(), timeout=3)
                    http_d.addCallback(_on_http)
                    http_d.addErrback(_on_http_err)
            else:
                # Miner returned a JSON-RPC error response — still a valid
                # network round-trip, so record the RTT.
                _record_rtt(time.time() - t0)
            if self._ping_active:
                self._ping_call = reactor.callLater(self._LATENCY_PING_INTERVAL, self._ping_once)
        d.addCallbacks(on_response, on_error)

    def rpc_configure(self, extensions, extensionParameters):
        #extensions is a list of extension codes defined in BIP310
        #extensionParameters is a dict of parameters for each extension code
        self._trace('<--', 'configure', exts=','.join(extensions),
                    mask=extensionParameters.get('version-rolling.mask', '-'))
        if 'version-rolling' in extensions:
            # When STRATUM_DISABLE_ASICBOOST is set, decline version-rolling
            # by returning the disabled form.  This mimics vanilla jtoomim
            # p2pool which lacks rpc_configure entirely; strict firmware
            # (Antminer stock) then falls back to non-rolled mining.
            if DISABLE_ASICBOOST:
                return {"version-rolling": False}
            #mask from miner is mandatory but we dont use it
            miner_mask = extensionParameters['version-rolling.mask']
            #min-bit-count from miner is mandatory but we dont use it
            try:
                minbitcount = extensionParameters['version-rolling.min-bit-count']
            except:
                log.err("A miner tried to connect with a malformed version-rolling.min-bit-count parameter. This is probably a bug in your mining software. Braiins OS is known to have this bug. You should complain to them.")
                minbitcount = 2 # probably not needed
            #according to the spec, pool should return largest mask possible (to support mining proxies)
            return {"version-rolling" : True, "version-rolling.mask" : '{:08x}'.format(self.pool_version_mask&(int(miner_mask,16)))}
            #pool can send mining.set_version_mask at any time if the pool mask changes

        if 'minimum-difficulty' in extensions:
            print 'Extension method minimum-difficulty not implemented'
        if 'subscribe-extranonce' in extensions:
            print 'Extension method subscribe-extranonce not implemented'

    _DEFERRED_WORK_LOG_INTERVAL = 30  # seconds between repeated startup-state log lines

    def _send_work(self):
        try:
            x, got_response = self.wb.get_work(*self.wb.preprocess_request('' if self.username is None else self.username))
        except jsonrpc.Error as e:
            # Expected JSONRPC errors raised by get_work() during startup
            # or transient outages: peers not yet connected, sharechain
            # still downloading, lost contact with bitcoind, unknown
            # softfork.  These are NOT bugs — keep the miner's stratum
            # connection alive; new_work_event will retry _send_work as
            # soon as the node recovers.  Log at most once per 30 s to
            # avoid flooding the journal during long startups.
            now = time.time()
            if now - self._deferred_work_logged_at >= self._DEFERRED_WORK_LOG_INTERVAL:
                self._deferred_work_logged_at = now
                log.msg('Stratum %s: deferring work send (%s); retry on next new_work_event' % (
                    self.username or '?', e))
            return
        except:
            log.err()
            self.transport.loseConnection()
            return
        # Reset the throttle so a fresh startup-state outage logs immediately.
        self._deferred_work_logged_at = 0
        if self.desired_pseudoshare_target:
            self.fixed_target = True
            self.target = self.desired_pseudoshare_target
            self.target = max(self.target, int(x['bits'].target))
        else:
            self.fixed_target = False
            self.target = x['share_target'] if self.target == None else max(x['min_share_target'], self.target)
        # Apply STRATUM_FIXED_INITIAL_DIFF override (env-gated, default off).
        # Sets the initial target to EXACTLY the configured diff, ignoring
        # the natural local-hash-rate-derived value.  Use when an external
        # spec demands a specific value (e.g. FR-1.15 = 65536 exactly).
        # Only applies on the first _send_work call per session
        # (self.target == None at that point); subsequent vardiff updates
        # ratchet normally from there.  Skipped for fixed_target users
        # (operator opted into a manual diff via the worker name suffix).
        if FIXED_INITIAL_DIFF > 0 and not self.fixed_target and len(self.recent_shares) == 0:
            # Apply ratchet multiplier: same factor vardiff itself uses for
            # max-rate ramp-up (VARDIFF_CLIP).  Steps reset to 0 on each
            # connection (per-session state) and increment via _ratchet_diff.
            # When NO_SUBMIT_RATCHET disabled, steps stays 0 and effective
            # diff equals FIXED_INITIAL_DIFF unchanged.
            effective_diff = FIXED_INITIAL_DIFF * (VARDIFF_CLIP ** self._ratchet_steps)
            forced_target = bitcoin_data.difficulty_to_target(
                effective_diff / self.wb.net.DUMB_SCRYPT_DIFF)
            self.target = max(x['min_share_target'], forced_target)
        # Apply STRATUM_MIN_INITIAL_DIFF floor (env-gated, default off).
        # Smaller target = harder = higher stratum diff; we clamp self.target
        # *down* to the configured floor's target equivalent, then re-clamp
        # *up* to consensus min_share_target.  Skipped for fixed_target users
        # (operator opted into a manual diff via the worker name suffix).
        elif MIN_INITIAL_DIFF > 0 and not self.fixed_target:
            floor_target = bitcoin_data.difficulty_to_target(
                MIN_INITIAL_DIFF / self.wb.net.DUMB_SCRYPT_DIFF)
            if self.target > floor_target:
                self.target = floor_target
            self.target = max(x['min_share_target'], self.target)
        # Jobid generation: monotonically increasing per-session counter
        # (FR-1.15 quirk #1 — Amlogic dispatcher holds onto previous work
        # for 10-30s if new jobid is "less than" previous in
        # lexicographical order).  Counter wraps at 2**32 (well beyond
        # handler_map TTL).  10-digit max width keeps strict CGMiner
        # parsers in Bitmain stock firmware happy (their fixed-size jobid
        # buffer overflowed our previous 2**128 random ids).
        self._jobid_counter = (self._jobid_counter + 1) % (2**32)
        jobid = str(self._jobid_counter)
        new_diff = bitcoin_data.target_to_difficulty(self.target)*self.wb.net.DUMB_SCRYPT_DIFF
        # FR-1.15 quirk #3: only emit set_difficulty when the diff
        # actually changed; emitting it on every notify (when diff is
        # unchanged) wastes a wire message and increases the chance of
        # the simultaneous-arrival race that corrupts the miner's
        # internal target.  When diff DOES change, we still issue
        # set_difficulty before notify on the same TCP socket — the
        # ordering is preserved by Twisted's serialized writes.
        if new_diff != self._last_sent_diff:
            self._trace('-->', 'set_difficulty', diff='%.4g' % new_diff)
            self.other.svc_mining.rpc_set_difficulty(new_diff).addErrback(lambda err: None)
            self._last_sent_diff = new_diff
        self._trace('-->', 'notify', jobid=jobid, clean=True)
        # Hex encoding of the 4-byte fields version/nbits/ntime: legacy
        # default is BE (LE pack + _swap4 = BE bytes); STRATUM_NOTIFY_HEX_LE
        # skips _swap4 to emit raw LE bytes (matches p2pool-cc's default).
        # prevhash and merkle branches always go through _swap4 — that
        # convention is fixed by the stratum protocol.
        if NOTIFY_HEX_LE:
            version_hex = pack.IntType(32).pack(x['version']).encode('hex')
            nbits_hex   = pack.IntType(32).pack(x['bits'].bits).encode('hex')
            ntime_hex   = pack.IntType(32).pack(x['timestamp']).encode('hex')
        else:
            version_hex = getwork._swap4(pack.IntType(32).pack(x['version'])).encode('hex')
            nbits_hex   = getwork._swap4(pack.IntType(32).pack(x['bits'].bits)).encode('hex')
            ntime_hex   = getwork._swap4(pack.IntType(32).pack(x['timestamp'])).encode('hex')
        self.other.svc_mining.rpc_notify(
            jobid, # jobid
            getwork._swap4(pack.IntType(256).pack(x['previous_block'])).encode('hex'), # prevhash
            x['coinb1'].encode('hex'), # coinb1
            x['coinb2'].encode('hex'), # coinb2
            [pack.IntType(256).pack(s).encode('hex') for s in x['merkle_link']['branch']], # merkle_branch
            version_hex,
            nbits_hex,
            ntime_hex,
            True, # clean_jobs
        ).addErrback(lambda err: None)
        # Capture the target THAT WAS CURRENT WHEN THIS NOTIFY WENT OUT, so
        # that any submit referencing this jobid is validated against the
        # target the miner was told to mine against — NOT the (possibly
        # ratcheted-since) self.target.  Without this, a vardiff ratchet
        # between notify-issue and submit-arrive causes the miner's in-flight
        # work to fail "hash > target" validation even though it meets the
        # diff we explicitly set_difficulty'd to (live evidence: 2026-05-02
        # 18:18-18:22 trace, ~50% reject rate on s21p2355).
        self.handler_map[jobid] = x, got_response, self.target, time.time()
        # Schedule diff-ratchet timer: if no submits arrive within
        # NO_SUBMIT_RATCHET_SECONDS, double the effective FIXED_INITIAL_DIFF
        # and re-emit work.  Only schedules if no timer is already pending
        # (so share-chain advances don't keep resetting the window).
        # Cancelled on first submit / connection close.
        if (NO_SUBMIT_RATCHET_SECONDS > 0 and FIXED_INITIAL_DIFF > 0
                and not self.fixed_target and len(self.recent_shares) == 0):
            if self._ratchet_timer is None or not self._ratchet_timer.active():
                self._ratchet_timer = reactor.callLater(
                    NO_SUBMIT_RATCHET_SECONDS, self._ratchet_diff)

        # Per-notify ratchet: each new job advances the diff one step.
        # Per kr1z1s operator practice — increment after this notify so
        # the NEXT notify uses the higher diff.  Stops at the cap.
        # Coexists with the seconds-based timer (whichever is enabled).
        if (RATCHET_PER_NOTIFY and FIXED_INITIAL_DIFF > 0
                and not self.fixed_target and len(self.recent_shares) == 0):
            next_diff = FIXED_INITIAL_DIFF * (VARDIFF_CLIP ** (self._ratchet_steps + 1))
            if next_diff <= NO_SUBMIT_RATCHET_MAX:
                self._ratchet_steps += 1

    def _ratchet_diff(self):
        # Fired by the no-submit timer.  Multiplies the effective diff by
        # VARDIFF_CLIP (= the same magnitude vardiff uses for max-rate
        # ramp-up of an active miner) and triggers a fresh _send_work so
        # the miner sees the new value.  Stops at the ceiling.
        if len(self.recent_shares) > 0:
            return  # safety: vardiff already started, leave alone
        next_diff = FIXED_INITIAL_DIFF * (VARDIFF_CLIP ** (self._ratchet_steps + 1))
        if next_diff > NO_SUBMIT_RATCHET_MAX:
            return  # ceiling reached
        self._ratchet_steps += 1
        try:
            self._send_work()
        except Exception:
            log.err(None, 'ratchet _send_work failed:')

    def rpc_submit(self, worker_name, job_id, extranonce2, ntime, nonce, version_bits = None, *args):
        #asicboost: version_bits is the version mask that the miner used
        self._trace('<--', 'submit', worker=worker_name, jobid=job_id,
                    nonce=nonce, vmask=(version_bits or '-'))
        # First submit attempt cancels the no-submit ratchet — vardiff
        # will take over from here regardless of accept/reject result.
        if self._ratchet_timer is not None and self._ratchet_timer.active():
            self._ratchet_timer.cancel()
            self._ratchet_timer = None
        worker_name = worker_name.strip()
        # Track every submit attempt (regardless of accept/reject) for diagnostics
        worker_info = self.wb.connected_workers.get(worker_name)
        if worker_info is not None:
            worker_info['last_submit_time'] = time.time()
            worker_info['submit_count'] = worker_info.get('submit_count', 0) + 1
        if job_id not in self.handler_map:
            print >>sys.stderr, '''Couldn't link returned work's job id with its handler. This should only happen if this process was recently restarted!'''
            #self.other.svc_client.rpc_reconnect().addErrback(lambda err: None)
            return False
        x, got_response, job_target, job_issue_time = self.handler_map[job_id]
        # Full coinbase nonce = our session's extranonce1 (server-assigned
        # prefix, possibly empty) + the miner's submitted extranonce2.
        # The combined length must still equal COINBASE_NONCE_LENGTH so
        # the resulting coinbase has the exact width the share-chain
        # generator built coinb1/coinb2 around.
        miner_en2 = extranonce2.decode('hex')
        assert len(miner_en2) == self.wb.COINBASE_NONCE_LENGTH - len(self._extranonce1)
        coinb_nonce = self._extranonce1 + miner_en2
        assert len(coinb_nonce) == self.wb.COINBASE_NONCE_LENGTH
        new_packed_gentx = x['coinb1'] + coinb_nonce + x['coinb2']

        job_version = x['version']
        nversion = job_version
        #check if miner changed bits that they were not supposed to change
        if version_bits:
            if ((~self.pool_version_mask) & int(version_bits,16)) != 0:
                #todo: how to raise error back to miner?
                #protocol does not say error needs to be returned but ckpool returns
                #{"error": "Invalid version mask", "id": "id", "result":""}
                raise ValueError("Invalid version mask {0}".format(version_bits))
            nversion = (job_version & ~self.pool_version_mask) | (int(version_bits,16) & self.pool_version_mask)
            #nversion = nversion & int(version_bits,16)

        # Match the byte-order convention used in rpc_notify above: when
        # we send LE hex, the miner echoes ntime/nonce back in LE hex
        # too, and we must decode without _swap4 to recover the integer.
        if NOTIFY_HEX_LE:
            ntime_int = pack.IntType(32).unpack(ntime.decode('hex'))
            nonce_int = pack.IntType(32).unpack(nonce.decode('hex'))
        else:
            ntime_int = pack.IntType(32).unpack(getwork._swap4(ntime.decode('hex')))
            nonce_int = pack.IntType(32).unpack(getwork._swap4(nonce.decode('hex')))
        header = dict(
            version=nversion,
            previous_block=x['previous_block'],
            merkle_root=bitcoin_data.check_merkle_link(bitcoin_data.hash256(new_packed_gentx), x['merkle_link']), # new_packed_gentx has witness data stripped
            timestamp=ntime_int,
            bits=x['bits'],
            nonce=nonce_int,
        )
        # Validate against job_target (target at notify-issue time), NOT
        # self.target (which may have ratcheted since).  Eliminates the
        # vardiff race documented above the handler_map assignment.
        result = got_response(header, worker_name, coinb_nonce, job_target)
        # work.py returns (on_time, accepted) tuple; old fallback path
        # accepts a bare bool from any legacy caller path.
        if isinstance(result, tuple):
            on_time, accepted = result
        else:
            on_time, accepted = result, result

        # Structured REJECT trace — fires only on hash>target rejects (and
        # only when STRATUM_TRACE=1).  Captures the missing diagnostic
        # context that the existing 'hash > target' print lines don't have:
        # jobid (correlate to specific notify), age since notify (catch
        # stale-jobid edge cases), vmask (catch FR-1.15 version-rolling
        # collisions), and target_drift (did vardiff ratchet between notify
        # and submit even though we capture-at-notify).  Always-on summary
        # line stays in work.py so users without TRACE still see rejects.
        if not accepted:
            age_ms = int((time.time() - job_issue_time) * 1000)
            if self.target == job_target:
                drift = 'same'
            elif self.target < job_target:
                drift = 'tighter %.3fx' % (float(job_target) / float(self.target))
            else:
                drift = 'looser %.3fx' % (float(self.target) / float(job_target))
            self._trace('==', 'REJECT', worker=worker_name, jobid=job_id,
                        age='%dms' % age_ms, vmask=(version_bits or '-'),
                        drift=drift, on_time=on_time)

        # adjust difficulty on this stratum to target ~10sec/pseudoshare.
        # Only ACCEPTED shares feed vardiff samples — rejected (hash > target)
        # submissions would otherwise inflate the apparent share rate, ratchet
        # diff up too aggressively, and worsen the next race.  Antminer S21+
        # FR-1.15 stock under heavy version-rolling produces a steady stream
        # of ~1.0-1.2x-target near-misses; counting those as samples sent the
        # vardiff loop into a self-reinforcing oscillation observed in the
        # 2026-05-02 retention regression (s21p2355 18:18-18:22 trace).
        if not self.fixed_target and accepted:
            self.recent_shares.append(time.time())
            if len(self.recent_shares) > 12 or (time.time() - self.recent_shares[0]) > 10*len(self.recent_shares)*self.share_rate:
                old_time = self.recent_shares[0]
                del self.recent_shares[0]
                olddiff = bitcoin_data.target_to_difficulty(self.target)
                self.target = int(self.target * clip((time.time() - old_time)/(len(self.recent_shares)*self.share_rate), 1.0/VARDIFF_CLIP, VARDIFF_CLIP) + 0.5)
                newtarget = clip(self.target, self.wb.net.SANE_TARGET_RANGE[0], self.wb.net.SANE_TARGET_RANGE[1])
                if newtarget != self.target:
                    print "Clipping target from %064x to %064x" % (self.target, newtarget)
                    self.target = newtarget
                self.target = max(x['min_share_target'], self.target)
                self.recent_shares = [time.time()]
                self._send_work()

        # Return accepted (not on_time) to the JSON-RPC client.  Stratum
        # mining.submit response of False = miner counts the share as
        # rejected.  Pre-fix code returned on_time, which sent False to the
        # miner whenever the share was DOA — even though we credited the
        # share internally.  FR-1.15 stock counts those as rejects and
        # demotes our pool ranking; this fix removes that perceived-reject
        # tax entirely.
        return accepted

    
    def close(self):
        self._ping_active = False
        if self._ping_call is not None and self._ping_call.active():
            self._ping_call.cancel()
            self._ping_call = None
        if self._ratchet_timer is not None and self._ratchet_timer.active():
            self._ratchet_timer.cancel()
            self._ratchet_timer = None
        self.wb.new_work_event.unwatch(self.watch_id)

class StratumProtocol(jsonrpc.LineBasedPeer):
    def connectionMade(self):
        # ── Latency optimizations ─────────────────────────────────────────
        # Disable Nagle's algorithm.  Stratum frames are tiny (set_difficulty
        # ~80 B, submit ~200 B, notify ~600 B); Nagle batches them up to 200 ms
        # waiting for MTU-fill — pure latency tax for short JSON-RPC traffic.
        # With TCP_NODELAY off, every frame is sent immediately.  Single
        # biggest pool-side latency win for stratum servers; matters most for
        # geographically-distant miners where every ms of RTT counts toward
        # the firmware's pool-quality ranking heuristics.  Bitmain stock
        # FR-1.15 is observed picking pools by RTT-granularity at the
        # intra-DC level, so this is also our best shot at competing with
        # geographically-closer pools (e.g. kr1z1s in St. Petersburg/Rostov
        # vs us elsewhere).
        # SO_KEEPALIVE makes the OS detect dead connections faster than
        # application-level idle-detection, freeing resources cleanly.
        try:
            self.transport.setTcpNoDelay(True)
            self.transport.setTcpKeepAlive(True)
        except Exception:
            pass
        # ──────────────────────────────────────────────────────────────────
        self.svc_mining = StratumRPCMiningProvider(self.factory.wb, self.other, self.transport)
        if TRACE:
            try:
                peer = self.transport.getPeer()
                print '[STRATUM_TRACE] %s [%s:%d] [-] == TCP_CONNECT' % (
                    _ts(), peer.host, peer.port)
            except Exception:
                pass

    def connectionLost(self, reason):
        svc = self.svc_mining
        if TRACE:
            try:
                peer = self.transport.getPeer()
                who = getattr(svc, 'username', None) or '-'
                # reason.value carries the underlying twisted error (e.g.
                # ConnectionDone, ConnectionLost, RST) — exactly the field
                # we want to disambiguate "miner sent FIN" vs "we hit a
                # protocol error" vs "TCP RST from middlebox".
                print '[STRATUM_TRACE] %s [%s:%d] [%s] == TCP_DISCONNECT reason=%r' % (
                    _ts(), peer.host, peer.port, who,
                    getattr(reason, 'value', reason))
            except Exception:
                pass
        if getattr(svc, 'address', None) is not None:
            try:
                peer_ip = self.transport.getPeer().host
                svc.wb.worker_disconnected.happened(svc.username, svc.address, peer_ip)
            except:
                pass
        svc.close()

class StratumServerFactory(protocol.ServerFactory):
    protocol = StratumProtocol
    
    def __init__(self, wb):
        self.wb = wb
