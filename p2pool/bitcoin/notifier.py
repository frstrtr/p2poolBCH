"""
LocalEventPusher: fires p2pool worker/share/block events to a local Python 3
Telegram bot over HTTP POST.  Uses Twisted's getPage (already a dependency) so
no new PyPy2 imports are needed.

Wire format (POST body, JSON):
    {"type": "worker_connected",    "node": "vm301", "username": "rig1.w1",
     "address": "bitcoincash:qp...", "ip": "192.168.1.5",
     "latency_ms": 12.3,    # optional, omitted if first ping hasn't
                            # landed within _CONNECT_NOTIFY_GRACE seconds
     "ts": 1714000000.0}
    {"type": "worker_disconnected", ...same fields...}
    {"type": "worker_silent",       "username": ..., "address": ...,
     "idle_seconds": 720, "ts": ...}
    {"type": "worker_active_again", "username": ..., "address": ..., "ts": ...}
    {"type": "worker_flapping",     "username": ..., "address": ...,
     "flap_count": 6, "window_seconds": 3600, "ts": ...}
    {"type": "worker_stable",       "username": ..., "address": ..., "ts": ...}
    {"type": "share_found",  "node": ..., "username": ..., "address": ...,
     "hash": "abcd1234...", "dead": false, "ts": ...}
    {"type": "block_found",  "node": ..., "username": ..., "address": ...,
     "hash": "abcd1234...", "reward_sat": 625000000, "symbol": "BCH",
     "explorer_url": "https://explorer.bitcoin.com/bch/block/abcd1234...", "ts": ...}
"""
import json
import time

from twisted.internet import reactor
from twisted.python import log
from twisted.web.client import getPage

# Grace period before a disconnect notification is sent.
# If the worker reconnects within this window the alert is suppressed and a
# flap is recorded instead.
_DISCONNECT_GRACE = 60  # seconds

# How long the worker_connected alert is held back so the first latency
# ping (scheduled by stratum.py 1 s after connect) has time to land in
# wb.connected_workers[username]['latency'] before we read it.  2 s is
# enough margin for the typical 50–500 ms RTT plus the 1 s ping delay,
# while staying below the 60 s flap window so the disconnect-cancel
# semantics are unaffected.
_CONNECT_NOTIFY_GRACE = 2  # seconds

# Flap detection: a flap = full disconnect followed by full reconnect within
# the grace window.  When _FLAP_THRESHOLD flaps occur within _FLAP_WINDOW we
# raise a single 'worker_flapping' alert; once the rolling count drops back
# below _FLAP_CLEAR_THRESHOLD we raise 'worker_stable'.
_FLAP_WINDOW = 60 * 60          # 1 hour rolling window
_FLAP_THRESHOLD = 5             # flaps to trigger alert
_FLAP_CLEAR_THRESHOLD = 2       # flaps to clear alert
_FLAP_PRUNE_INTERVAL = 60       # seconds between sweep / clear checks


class LocalEventPusher(object):
    def __init__(self, bot_url, node_name, wb=None):
        # bot_url: e.g. "http://127.0.0.1:9349"
        # wb: optional WorkerBridge so the connect-grace timer can read
        #     wb.connected_workers[username]['latency'] at fire time and
        #     include it in the worker_connected payload.  None disables
        #     the latency lookup gracefully.
        self._url = str(bot_url).rstrip('/') + '/event'
        self._node = node_name
        self._wb = wb
        # username -> (DelayedCall, payload) for pending disconnect alerts
        self._pending_disconnect = {}
        # username -> DelayedCall for pending connect alerts (latency-grace)
        self._pending_connect = {}
        # username -> {'addr': str, 'times': [float, ...]} rolling flap log
        self._flap_history = {}
        # usernames currently in 'flapping' state (we have raised an alert)
        self._flapping = set()
        reactor.callLater(_FLAP_PRUNE_INTERVAL, self._prune_flap_state)

    def _push(self, payload):
        payload['node'] = self._node
        payload['ts'] = time.time()
        body = json.dumps(payload)
        d = getPage(
            str(self._url),
            method='POST',
            postdata=body,
            headers={'Content-Type': 'application/json'},
            timeout=2,
        )
        d.addErrback(lambda err: log.msg(
            'LocalEventPusher: failed to deliver event %s: %s' % (payload.get('type'), err.getErrorMessage())
        ))

    # ---- flap-rate tracking ------------------------------------------------
    def _record_flap(self, username, address):
        now = time.time()
        entry = self._flap_history.setdefault(username, {'addr': address, 'times': []})
        entry['addr'] = address or entry.get('addr', '')
        times = entry['times']
        times.append(now)
        cutoff = now - _FLAP_WINDOW
        while times and times[0] < cutoff:
            times.pop(0)
        if len(times) >= _FLAP_THRESHOLD and username not in self._flapping:
            self._flapping.add(username)
            self._push({'type': 'worker_flapping', 'username': username,
                        'address': entry['addr'], 'flap_count': len(times),
                        'window_seconds': _FLAP_WINDOW})

    def _prune_flap_state(self):
        now = time.time()
        cutoff = now - _FLAP_WINDOW
        cleared = []
        for username in list(self._flap_history.keys()):
            entry = self._flap_history[username]
            times = entry['times']
            while times and times[0] < cutoff:
                times.pop(0)
            if username in self._flapping and len(times) < _FLAP_CLEAR_THRESHOLD:
                self._flapping.discard(username)
                cleared.append((username, entry.get('addr', '')))
            if not times and username not in self._flapping:
                del self._flap_history[username]
        for username, addr in cleared:
            self._push({'type': 'worker_stable', 'username': username,
                        'address': addr})
        reactor.callLater(_FLAP_PRUNE_INTERVAL, self._prune_flap_state)

    # ---- event handlers (subscribed to wb semantic events) ----------------
    def on_worker_connected(self, username, address, ip):
        # Cancel any pending disconnect notification.  If we cancel one, the
        # worker reconnected within the grace window — that's a sub-grace
        # flap, record it.  Otherwise this is a fresh connect alert.
        pending = self._pending_disconnect.pop(username, None)
        if pending is not None:
            dc, _ = pending
            if dc.active():
                dc.cancel()
                self._record_flap(username, address)
                return  # suppressed: reconnect within grace window
        # While a worker is flagged as flapping we suppress individual
        # connect alerts — the user already got one 'worker_flapping' alert
        # and we don't want to spam them with the alternating cycle.
        if username in self._flapping:
            return
        # Defer the push so the first latency ping can complete; at fire
        # time we read the resulting RTT from wb.connected_workers and
        # attach it to the payload as latency_ms.  on_worker_disconnected
        # cancels this if the worker drops before the timer fires, so we
        # never send "Worker connected" after an actual disconnect.
        prior = self._pending_connect.pop(username, None)
        if prior is not None and prior.active():
            prior.cancel()  # superseded by a newer connect (rare)
        payload = {'type': 'worker_connected', 'username': username,
                   'address': address, 'ip': ip}
        def _fire():
            self._pending_connect.pop(username, None)
            if self._wb is not None:
                info = self._wb.connected_workers.get(username) or {}
                lat = info.get('latency')
                if isinstance(lat, (int, float)) and lat > 0:
                    payload['latency_ms'] = round(lat * 1000.0, 1)
            self._push(payload)
        dc = reactor.callLater(_CONNECT_NOTIFY_GRACE, _fire)
        self._pending_connect[username] = dc

    def on_worker_disconnected(self, username, address, ip):
        # If a connect-grace timer is still pending for this username, the
        # worker connected and immediately disconnected before we even sent
        # the connect alert.  Drop both: cancelling the deferred connect
        # avoids a misleading "Worker connected" arriving AFTER the actual
        # disconnect, and we record the cycle as a flap so the flap-rate
        # alarm still picks it up.
        prior_connect = self._pending_connect.pop(username, None)
        if prior_connect is not None and prior_connect.active():
            prior_connect.cancel()
            self._record_flap(username, address)
            return

        # Don't send immediately — wait for grace period.  When the timer
        # fires we record the disconnect as a flap event (regardless of
        # whether reconnect ever follows) so the rolling flap-rate alarm
        # catches both fast (sub-grace) and slow cycles.
        if username in self._pending_disconnect:
            return  # already queued
        payload = {'type': 'worker_disconnected', 'username': username,
                   'address': address, 'ip': ip}
        def _fire():
            self._pending_disconnect.pop(username, None)
            self._record_flap(username, address)
            # Suppress individual disconnect alerts while flapping — the
            # 'worker_flapping' alert is the single notification the user
            # gets until the worker stabilises.
            if username not in self._flapping:
                self._push(payload)
        dc = reactor.callLater(_DISCONNECT_GRACE, _fire)
        self._pending_disconnect[username] = (dc, payload)

    def on_worker_silent(self, username, address, idle_seconds):
        self._push({'type': 'worker_silent', 'username': username,
                    'address': address, 'idle_seconds': idle_seconds})

    def on_worker_active_again(self, username, address):
        self._push({'type': 'worker_active_again', 'username': username,
                    'address': address})

    def on_share_found(self, username, address, share_hash, dead):
        self._push({'type': 'share_found', 'username': username,
                    'address': address, 'hash': share_hash, 'dead': dead})

    def on_block_found(self, username, address, block_hash, subsidy_sat=0, symbol='BCH', explorer_url_prefix=''):
        self._push({'type': 'block_found', 'username': username,
                    'address': address, 'hash': block_hash,
                    'reward_sat': subsidy_sat, 'symbol': symbol,
                    'explorer_url': explorer_url_prefix + block_hash if explorer_url_prefix else ''})
