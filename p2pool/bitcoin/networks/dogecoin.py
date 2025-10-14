import os
import platform

from twisted.internet import defer

from .. import helper
from p2pool.util import pack


P2P_PREFIX = 'c0c0c0c0'.decode('hex')
P2P_PORT = 22556
ADDRESS_VERSION = 30
ADDRESS_P2SH_VERSION = 22
RPC_PORT = 22555
RPC_CHECK = defer.inlineCallbacks(lambda bitcoind: defer.returnValue(
            'validateaddress' in (yield bitcoind.rpc_help()) and
            (yield helper.check_block_header(bitcoind, '1a91e3dace36e2be3bf030a65679fe821aa1d6ef92e7c9902eb318182c355691')) and
            (yield bitcoind.rpc_getblockchaininfo())['chain'] == 'main'
        ))
# Dogecoin subsidy: After block 600,000 it's a fixed 10,000 DOGE per block
# Before that it was random rewards that halved, but we're well past that now
SUBSIDY_FUNC = lambda height: 10000*100000000 if height >= 600000 else 500000*100000000 >> (height + 1)//100000
POW_FUNC = lambda data: pack.IntType(256).unpack(__import__('ltc_scrypt').getPoWHash(data))
BLOCK_PERIOD = 60 # s (1 minute blocks)
SYMBOL = 'DOGE'
CONF_FILE_FUNC = lambda: os.path.join(os.path.join(os.environ['APPDATA'], 'DogeCoin') if platform.system() == 'Windows' else os.path.expanduser('~/Library/Application Support/Dogecoin/') if platform.system() == 'Darwin' else os.path.expanduser('~/.dogecoin'), 'dogecoin.conf')
BLOCK_EXPLORER_URL_PREFIX = 'https://dogechain.info/block/'
ADDRESS_EXPLORER_URL_PREFIX = 'https://dogechain.info/address/'
TX_EXPLORER_URL_PREFIX = 'https://dogechain.info/tx/'
SANE_TARGET_RANGE = (2**256//1000000000 - 1, 2**256//1000 - 1)
DUMB_SCRYPT_DIFF = 2**16
DUST_THRESHOLD = 0.1e8
