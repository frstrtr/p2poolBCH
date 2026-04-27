"""
LocalEventPusher: fires p2pool worker/share/block events to a local Python 3
Telegram bot over HTTP POST.  Uses Twisted's getPage (already a dependency) so
no new PyPy2 imports are needed.

Wire format (POST body, JSON):
    {"type": "worker_connected",    "node": "vm301", "username": "rig1.w1",
     "address": "bitcoincash:qp...", "ip": "192.168.1.5",  "ts": 1714000000.0}
    {"type": "worker_disconnected", ...same fields...}
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
# If the worker reconnects within this window the alert is suppressed.
_DISCONNECT_GRACE = 60  # seconds


class LocalEventPusher(object):
    def __init__(self, bot_url, node_name):
        # bot_url: e.g. "http://127.0.0.1:9349"
        self._url = str(bot_url).rstrip('/') + '/event'
        self._node = node_name
        # username -> (DelayedCall, payload) for pending disconnect alerts
        self._pending_disconnect = {}

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

    def on_worker_connected(self, username, address, ip):
        # Cancel any pending disconnect notification for this worker
        pending = self._pending_disconnect.pop(username, None)
        if pending is not None:
            dc, _ = pending
            if dc.active():
                dc.cancel()
                return  # suppressed: reconnect within grace window
        self._push({'type': 'worker_connected', 'username': username,
                    'address': address, 'ip': ip})

    def on_worker_disconnected(self, username, address, ip):
        # Don't send immediately — wait for grace period
        if username in self._pending_disconnect:
            return  # already queued
        payload = {'type': 'worker_disconnected', 'username': username,
                   'address': address, 'ip': ip}
        def _fire():
            self._pending_disconnect.pop(username, None)
            self._push(payload)
        dc = reactor.callLater(_DISCONNECT_GRACE, _fire)
        self._pending_disconnect[username] = (dc, payload)

    def on_share_found(self, username, address, share_hash, dead):
        self._push({'type': 'share_found', 'username': username,
                    'address': address, 'hash': share_hash, 'dead': dead})

    def on_block_found(self, username, address, block_hash, subsidy_sat=0, symbol='BCH', explorer_url_prefix=''):
        self._push({'type': 'block_found', 'username': username,
                    'address': address, 'hash': block_hash,
                    'reward_sat': subsidy_sat, 'symbol': symbol,
                    'explorer_url': explorer_url_prefix + block_hash if explorer_url_prefix else ''})
