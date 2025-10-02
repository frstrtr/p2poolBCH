# Small test script to call bitcoind RPC using p2pool's jsonrpc client
from p2pool.util import jsonrpc
import base64
import sys

url = 'http://127.0.0.1:8332/'
username = 'p2poolrpcuser'
password = '13D3cHFela...'
# replace password placeholder if needed
headers = dict(Authorization='Basic ' + base64.b64encode(username + ':' + password))

proxy = jsonrpc.HTTPProxy(url, headers, timeout=10)

from twisted.internet import reactor
from twisted.internet.task import react
from twisted.internet import defer

@defer.inlineCallbacks
def run_tests():
    try:
        info = yield proxy.rpc_getblockchaininfo()
        print('getblockchaininfo OK, chain=', info.get('chain'))
        count = yield proxy.rpc_getblockcount()
        print('getblockcount OK, count=', count)
    except Exception as e:
        print('RPC call failed:', repr(e))
        import traceback; traceback.print_exc()
    reactor.stop()

if __name__ == '__main__':
    reactor.callWhenRunning(lambda: run_tests())
    reactor.run()
