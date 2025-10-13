from p2pool.bitcoin import networks

# CHAIN_LENGTH = number of shares back client keeps
# REAL_CHAIN_LENGTH = maximum number of shares back client uses to compute payout
# REAL_CHAIN_LENGTH must always be <= CHAIN_LENGTH
# REAL_CHAIN_LENGTH must be changed in sync with all other clients
# changes can be done by changing one, then the other

PARENT = networks.nets['dogecoin']
SHARE_PERIOD = 15 # seconds target spacing
CHAIN_LENGTH = 12*60*60//15 # shares -- 12 hours
REAL_CHAIN_LENGTH = 12*60*60//15 # shares -- 12 hours
TARGET_LOOKBEHIND = 20 # shares coinbase maturity
SPREAD = 10 # blocks
IDENTIFIER = 'D0D1D2D3B2F68CD9'.decode('hex')
PREFIX = 'D0D3D4D541C11DD9'.decode('hex')
P2P_PORT = 8555
MIN_TARGET = 0
MAX_TARGET = 2**256//2**20 - 1
PERSIST = True
WORKER_PORT = 9555
BOOTSTRAP_ADDRS = [
        'p2pool.org',
        'rav3n.dtdns.net',
        'dogepool.pw',
        ]
ANNOUNCE_CHANNEL = '#p2pool-alt'
VERSION_CHECK = lambda v: None if 1140000 <= v else 'Dogecoin version too old. Upgrade to 1.14.0 or newer!'
VERSION_WARNING = lambda v: None
SOFTFORKS_REQUIRED = set()
MINIMUM_PROTOCOL_VERSION = 3301
BLOCK_MAX_SIZE = 1000000
BLOCK_MAX_WEIGHT = 4000000
