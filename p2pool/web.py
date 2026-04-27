from __future__ import division

import errno
import json
import os
import sys
import time
import traceback

from twisted.internet import defer, reactor
from twisted.python import log
from twisted.web import resource, static

import p2pool
from bitcoin import data as bitcoin_data, helper as bitcoin_helper
from . import data as p2pool_data, p2p
from util import deferral, deferred_resource, graph, math, memory, pack, variable

def _atomic_read(filename):
    try:
        with open(filename, 'rb') as f:
            return f.read()
    except IOError, e:
        if e.errno != errno.ENOENT:
            raise
    try:
        with open(filename + '.new', 'rb') as f:
            return f.read()
    except IOError, e:
        if e.errno != errno.ENOENT:
            raise
    return None

def _atomic_write(filename, data):
    with open(filename + '.new', 'wb') as f:
        f.write(data)
        f.flush()
        try:
            os.fsync(f.fileno())
        except:
            pass
    try:
        os.rename(filename + '.new', filename)
    except: # XXX windows can't overwrite
        os.remove(filename)
        os.rename(filename + '.new', filename)

def get_web_root(wb, datadir_path, bitcoind_getinfo_var, stop_event=variable.Event(), static_dir=None,
                 enable_miner_messages=False, transition_message=None, trusted_proxy=None):
    node = wb.node
    start_time = time.time()

    _LOCALHOST_IPS = ('127.0.0.1', '::1', '::ffff:127.0.0.1')

    def _get_real_client_ip(request):
        peer_ip = request.getClientIP()
        if trusted_proxy and peer_ip == trusted_proxy:
            forwarded = request.getHeader('X-Forwarded-For')
            if forwarded:
                return forwarded.split(',')[0].strip()
        return peer_ip

    def _is_localhost(request):
        return _get_real_client_ip(request) in _LOCALHOST_IPS

    web_root = resource.Resource()
    
    def get_users():
        height, last = node.tracker.get_height_and_last(node.best_share_var.value)
        weights, total_weight, donation_weight = node.tracker.get_cumulative_weights(node.best_share_var.value, min(height, 720), 65535*2**256)
        res = {}
        for addr in sorted(weights, key=lambda s: weights[s]):
            res[addr] = weights[addr]/total_weight
        return res
    
    def get_current_scaled_txouts(scale, trunc=0):
        txouts = node.get_current_txouts()
        total = sum(txouts.itervalues())
        results = dict((addr, value*scale//total) for addr, value in txouts.iteritems())
        if trunc > 0:
            total_random = 0
            random_set = set()
            for s in sorted(results, key=results.__getitem__):
                if results[s] >= trunc:
                    break
                total_random += results[s]
                random_set.add(s)
            if total_random:
                winner = math.weighted_choice((addr, results[script]) for addr in random_set)
                for addr in random_set:
                    del results[addr]
                results[winner] = total_random
        if sum(results.itervalues()) < int(scale):
            results[math.weighted_choice(results.iteritems())] += int(scale) - sum(results.itervalues())
        return results
    
    def get_patron_sendmany(total=None, trunc='0.01'):
        if total is None:
            return 'need total argument. go to patron_sendmany/<TOTAL>'
        total = int(float(total)*1e8)
        trunc = int(float(trunc)*1e8)
        return json.dumps(dict(
            (bitcoin_data.script2_to_address(script, node.net.PARENT), value/1e8)
            for script, value in get_current_scaled_txouts(total, trunc).iteritems()
            if bitcoin_data.script2_to_address(script, node.net.PARENT) is not None
        ))
    
    def get_global_stats():
        # averaged over last hour
        if node.best_share_var.value is None or node.tracker.get_height(node.best_share_var.value) < 10:
            return None
        lookbehind = min(node.tracker.get_height(node.best_share_var.value), 3600//node.net.SHARE_PERIOD)
        
        nonstale_hash_rate = p2pool_data.get_pool_attempts_per_second(node.tracker, node.best_share_var.value, lookbehind)
        stale_prop = p2pool_data.get_average_stale_prop(node.tracker, node.best_share_var.value, lookbehind)
        diff = bitcoin_data.target_to_difficulty(wb.current_work.value['bits'].target)

        return dict(
            pool_nonstale_hash_rate=nonstale_hash_rate,
            pool_hash_rate=nonstale_hash_rate/(1 - stale_prop),
            pool_stale_prop=stale_prop,
            min_difficulty=bitcoin_data.target_to_difficulty(node.tracker.items[node.best_share_var.value].max_target),
            network_block_difficulty=diff,
            network_hashrate=(diff * 2**32 // node.net.PARENT.BLOCK_PERIOD),
        )
    
    def get_local_stats():
        if node.tracker.get_height(node.best_share_var.value) < 10:
            return None
        lookbehind = min(node.tracker.get_height(node.best_share_var.value), 3600//node.net.SHARE_PERIOD)
        
        global_stale_prop = p2pool_data.get_average_stale_prop(node.tracker, node.best_share_var.value, lookbehind)
        
        my_unstale_count = sum(1 for share in node.tracker.get_chain(node.best_share_var.value, lookbehind) if share.hash in wb.my_share_hashes)
        my_orphan_count = sum(1 for share in node.tracker.get_chain(node.best_share_var.value, lookbehind) if share.hash in wb.my_share_hashes and share.share_data['stale_info'] == 'orphan')
        my_doa_count = sum(1 for share in node.tracker.get_chain(node.best_share_var.value, lookbehind) if share.hash in wb.my_share_hashes and share.share_data['stale_info'] == 'doa')
        my_share_count = my_unstale_count + my_orphan_count + my_doa_count
        my_stale_count = my_orphan_count + my_doa_count
        
        my_stale_prop = my_stale_count/my_share_count if my_share_count != 0 else None
        
        my_work = sum(bitcoin_data.target_to_average_attempts(share.target)
            for share in node.tracker.get_chain(node.best_share_var.value, lookbehind - 1)
            if share.hash in wb.my_share_hashes)
        actual_time = (node.tracker.items[node.best_share_var.value].timestamp -
            node.tracker.items[node.tracker.get_nth_parent_hash(node.best_share_var.value, lookbehind - 1)].timestamp)
        share_att_s = my_work / actual_time
        
        miner_hash_rates, miner_dead_hash_rates = wb.get_local_rates()
        (stale_orphan_shares, stale_doa_shares), shares, _ = wb.get_stale_counts()

        miner_last_difficulties = {}
        for addr in wb.last_work_shares.value:
            miner_last_difficulties[addr] = bitcoin_data.target_to_difficulty(wb.last_work_shares.value[addr].target)
        
        return dict(
            my_hash_rates_in_last_hour=dict(
                note="DEPRECATED",
                nonstale=share_att_s,
                rewarded=share_att_s/(1 - global_stale_prop),
                actual=share_att_s/(1 - my_stale_prop) if my_stale_prop is not None else 0, # 0 because we don't have any shares anyway
            ),
            my_share_counts_in_last_hour=dict(
                shares=my_share_count,
                unstale_shares=my_unstale_count,
                stale_shares=my_stale_count,
                orphan_stale_shares=my_orphan_count,
                doa_stale_shares=my_doa_count,
            ),
            my_stale_proportions_in_last_hour=dict(
                stale=my_stale_prop,
                orphan_stale=my_orphan_count/my_share_count if my_share_count != 0 else None,
                dead_stale=my_doa_count/my_share_count if my_share_count != 0 else None,
            ),
            miner_hash_rates=miner_hash_rates,
            miner_dead_hash_rates=miner_dead_hash_rates,
            miner_last_difficulties=miner_last_difficulties,
            efficiency_if_miner_perfect=(1 - stale_orphan_shares/shares)/(1 - global_stale_prop) if shares else None, # ignores dead shares because those are miner's fault and indicated by pseudoshare rejection
            efficiency=(1 - (stale_orphan_shares+stale_doa_shares)/shares)/(1 - global_stale_prop) if shares else None,
            peers=dict(
                incoming=sum(1 for peer in node.p2p_node.peers.itervalues() if peer.incoming),
                outgoing=sum(1 for peer in node.p2p_node.peers.itervalues() if not peer.incoming),
            ),
            shares=dict(
                total=shares,
                orphan=stale_orphan_shares,
                dead=stale_doa_shares,
            ),
            uptime=time.time() - start_time,
            attempts_to_share=bitcoin_data.target_to_average_attempts(node.tracker.items[node.best_share_var.value].max_target),
            attempts_to_block=bitcoin_data.target_to_average_attempts(node.bitcoind_work.value['bits'].target),
            block_value=node.bitcoind_work.value['subsidy']*1e-8,
            block_value_payments=node.bitcoind_work.value['subsidy']*1e-8 * (1 - wb.donation_percentage/100),
            block_value_miner=node.bitcoind_work.value['subsidy']*1e-8 * (1 - wb.donation_percentage/100) * (1 - getattr(wb, 'node_owner_fee', wb.worker_fee)/100),
            attempts_to_merged_block=None,
            warnings=p2pool_data.get_warnings(node.tracker, node.best_share_var.value, node.net, bitcoind_getinfo_var.value, node.bitcoind_work.value),
            donation_proportion=wb.donation_percentage/100,
            version=p2pool.__version__,
            protocol_version=p2p.Protocol.VERSION,
            fee=getattr(wb, 'node_owner_fee', wb.worker_fee),
        )
    
    class WebInterface(deferred_resource.DeferredResource):
        def __init__(self, func, mime_type='application/json', args=()):
            deferred_resource.DeferredResource.__init__(self)
            self.func, self.mime_type, self.args = func, mime_type, args
        
        def getChild(self, child, request):
            return WebInterface(self.func, self.mime_type, self.args + (child,))
        
        @defer.inlineCallbacks
        def render_GET(self, request):
            request.setHeader('Content-Type', self.mime_type)
            request.setHeader('Access-Control-Allow-Origin', '*')
            res = yield self.func(*self.args)
            defer.returnValue(json.dumps(res) if self.mime_type == 'application/json' else res)
    
    def decent_height():
        return min(node.tracker.get_height(node.best_share_var.value), 720)
    web_root.putChild('rate', WebInterface(lambda: p2pool_data.get_pool_attempts_per_second(node.tracker, node.best_share_var.value, decent_height())/(1-p2pool_data.get_average_stale_prop(node.tracker, node.best_share_var.value, decent_height()))))
    web_root.putChild('difficulty', WebInterface(lambda: bitcoin_data.target_to_difficulty(node.tracker.items[node.best_share_var.value].max_target)))
    web_root.putChild('users', WebInterface(get_users))
    web_root.putChild('user_stales', WebInterface(lambda:
        p2pool_data.get_user_stale_props(node.tracker, node.best_share_var.value,
            node.tracker.get_height(node.best_share_var.value), node.net.PARENT)))
    web_root.putChild('fee', WebInterface(lambda: wb.worker_fee))
    web_root.putChild('current_payouts', WebInterface(lambda: dict(
        (address, value/1e8) for address, value
            in node.get_current_txouts().iteritems())))
    web_root.putChild('patron_sendmany', WebInterface(get_patron_sendmany, 'text/plain'))
    web_root.putChild('global_stats', WebInterface(get_global_stats))
    web_root.putChild('local_stats', WebInterface(get_local_stats))

    # ==== Version Signaling ====
    def get_version_signaling():
        if node.best_share_var.value is None:
            return None
        chain_height = node.tracker.get_height(node.best_share_var.value)
        if chain_height < 10:
            return None
        chain_length = node.net.CHAIN_LENGTH
        lookbehind = min(chain_height, chain_length // 10)
        try:
            counts = p2pool_data.get_desired_version_counts(node.tracker, node.best_share_var.value, lookbehind)
        except:
            counts = {}
        total_weight = sum(counts.itervalues())
        if total_weight == 0:
            return None
        version_percentages = {}
        for version, weight in counts.iteritems():
            version_percentages[str(version)] = {
                'weight': weight,
                'percentage': (weight / total_weight) * 100
            }
        share_type_counts = {}
        share_type_names = {
            17: 'Share', 32: 'PreSegwitShare', 33: 'NewShare',
            34: 'SegwitMiningShare', 35: 'PaddingBugfixShare',
        }
        overall_total = 0
        full_chain_desired = {}
        _scan_limit = min(chain_height, chain_length)
        try:
            _sh = node.best_share_var.value
            _pos = 0
            while _sh is not None and _pos < _scan_limit:
                _s = node.tracker.items.get(_sh)
                if _s is None:
                    break
                share_type_counts[_s.VERSION] = share_type_counts.get(_s.VERSION, 0) + 1
                _dv = getattr(_s, 'desired_version', _s.VERSION)
                full_chain_desired[_dv] = full_chain_desired.get(_dv, 0) + 1
                overall_total += 1
                _sh = _s.previous_hash
                _pos += 1
        except:
            pass
        total_shares = sum(share_type_counts.values()) if share_type_counts else 0
        share_types = {}
        for version, cnt in sorted(share_type_counts.items()):
            name = share_type_names.get(version, 'V%d' % version)
            share_types[str(version)] = {
                'name': name, 'count': cnt,
                'percentage': (cnt / total_shares * 100) if total_shares > 0 else 0
            }
        full_chain_version_pcts = {}
        for ver, cnt in full_chain_desired.items():
            full_chain_version_pcts[str(ver)] = {
                'count': cnt,
                'percentage': (cnt * 100.0 / overall_total) if overall_total > 0 else 0
            }
        current_share = node.tracker.items.get(node.best_share_var.value)
        current_share_type = current_share.VERSION if current_share else None
        current_share_name = share_type_names.get(current_share_type, 'V%d' % current_share_type) if current_share_type else 'Unknown'
        sampling_window_size = chain_length // 10
        majority_version = max(counts, key=counts.__getitem__) if counts else current_share_type
        return dict(
            versions=version_percentages,
            share_types=share_types,
            full_chain_versions=full_chain_version_pcts,
            current_share_type=current_share_type,
            current_share_name=current_share_name,
            chain_height=chain_height,
            chain_length_required=chain_length,
            chain_maturity=min(100.0, chain_height * 100.0 / chain_length),
            chain_ready=(chain_height >= chain_length),
            sampling_window_size=sampling_window_size,
            show_transition=False,  # BCH: no pending transition
            status='no_transition',
        )
    web_root.putChild('version_signaling', WebInterface(get_version_signaling))

    # ==== Stratum Stats (BCH: uses local rates since no pool_stats module) ====
    def get_stratum_stats():
        try:
            miner_hash_rates, miner_dead_hash_rates = wb.get_local_rates()
            formatted_workers = {}
            # Include workers with live connections that have submitted at least one share
            for worker_name, info in wb.connected_workers.iteritems():
                if worker_name not in miner_hash_rates:
                    last_diff = info.get('last_diff', 0)
                    if not last_diff:
                        continue  # connected but never submitted here — mining elsewhere
                    ws = wb.worker_shares.get(worker_name, {})
                    _hist0 = wb.worker_latency_history.get(worker_name, [])
                    _recent0 = [r for ts, r in _hist0]
                    _24h_avg0 = sum(_recent0) / len(_recent0) if _recent0 else None
                    formatted_workers[worker_name] = {
                        'hash_rate': 0,
                        'dead_hash_rate': 0,
                        'accepted': ws.get('accepted', 0),
                        'rejected': ws.get('rejected', 0),
                        'shares': ws.get('accepted', 0) + ws.get('rejected', 0),
                        'last_seen': info['since'],
                        'first_seen': info['since'],
                        'connections': 1,
                        'active_connections': 1,
                        'backup_connections': 0,
                        'connection_difficulties': [last_diff] if last_diff else [],
                        'latency': info.get('latency', None),
                        'latency_24h_avg': _24h_avg0,
                        'merged_addresses': {},
                        'merged_auto_converted': False,
                        'merged_redistributed': False,
                        'merged_reverse_converted': False,
                    }
            for worker_name, hr in miner_hash_rates.iteritems():
                if worker_name not in wb.connected_workers:
                    continue  # disconnected; stale rate-monitor data
                doa = miner_dead_hash_rates.get(worker_name, 0)
                last_diff = wb.connected_workers.get(worker_name, {}).get('last_diff', 0)
                first_seen = wb.connected_workers.get(worker_name, {}).get('since', start_time)
                ws = wb.worker_shares.get(worker_name, {})
                _hist = wb.worker_latency_history.get(worker_name, [])
                _recent = [r for ts, r in _hist]
                _24h_avg = sum(_recent) / len(_recent) if _recent else None
                formatted_workers[worker_name] = {
                    'hash_rate': hr,
                    'dead_hash_rate': doa,
                    'accepted': ws.get('accepted', 0),
                    'rejected': ws.get('rejected', 0),
                    'shares': ws.get('accepted', 0) + ws.get('rejected', 0),
                    'last_seen': time.time(),
                    'first_seen': first_seen,
                    'connections': 1,
                    'active_connections': 1,
                    'backup_connections': 0,
                    'connection_difficulties': [last_diff] if last_diff else [],
                    'latency': wb.connected_workers.get(worker_name, {}).get('latency', None),
                    'latency_24h_avg': _24h_avg,
                    'merged_addresses': {},
                    'merged_auto_converted': False,
                    'merged_redistributed': False,
                    'merged_reverse_converted': False,
                }

            # Build IP stats from connected_workers
            ip_connections = {}
            ip_workers_map = {}
            for wname, info in wb.connected_workers.iteritems():
                ip = info.get('ip', 'unknown')
                ip_connections[ip] = ip_connections.get(ip, 0) + 1
                if ip not in ip_workers_map:
                    ip_workers_map[ip] = set()
                ip_workers_map[ip].add(wname)
            ip_workers = {ip: len(workers) for ip, workers in ip_workers_map.iteritems()}

            # Submission rate: shares per second over last ~50 pseudoshares
            now = time.time()
            shares_ts = [t for t, w in wb.recent_shares_ts_work]
            if len(shares_ts) >= 2:
                elapsed = now - shares_ts[0]
                submission_rate = len(shares_ts) / elapsed if elapsed > 0 else 0
            else:
                submission_rate = 0

            total_accepted = wb.total_shares.get('accepted', 0)
            total_rejected = wb.total_shares.get('rejected', 0)

            return {
                'pool': {
                    'connections': len(wb.connected_workers),
                    'workers': len(formatted_workers),
                    'total_workers': len(formatted_workers),
                    'total_hash_rate': sum(miner_hash_rates.itervalues()),
                    'total_accepted': total_accepted,
                    'total_rejected': total_rejected,
                    'submission_rate': submission_rate,
                    'uptime': time.time() - start_time,
                    'ip_connections': ip_connections,
                    'ip_workers': ip_workers,
                    'threat_thresholds': {
                        'connection_worker_elevated': 4.0,
                        'connection_worker_warning': 6.0,
                    },
                },
                'workers': formatted_workers,
            }
        except Exception as e:
            return {'error': str(e), 'workers': {}}
    web_root.putChild('stratum_stats', WebInterface(get_stratum_stats))

    def get_stratum_security():
        now = time.time()
        shares_ts = [t for t, w in wb.recent_shares_ts_work]
        rate_10s = sum(1 for t in shares_ts if now - t <= 10) / 10.0
        rate_60s = sum(1 for t in shares_ts if now - t <= 60) / 60.0
        burst_ratio = (rate_10s / rate_60s) if rate_60s > 0 else 1.0

        suspicious = []
        for wname, ws in wb.worker_shares.iteritems():
            total = ws.get('accepted', 0) + ws.get('rejected', 0)
            if total > 10 and total > 0 and ws.get('rejected', 0) / float(total) > 0.5:
                suspicious.append({'name': wname, 'rate': rate_10s})

        return {
            'rate_10s': rate_10s,
            'rate_60s': rate_60s,
            'burst_ratio': burst_ratio,
            'banned_ips_count': 0,
            'banned_workers_count': 0,
            'threat_level': 0,
            'threat_reasons': [],
            'suspicious_workers': suspicious,
            'limits': {
                'max_submissions_per_sec': 1000,
                'max_connections_per_ip': 50,
            },
            'ddos_detected': False,
            'bans': [],
        }
    web_root.putChild('stratum_security', WebInterface(get_stratum_security))

    def get_ban_stats():
        return {
            'banned_ips_count': 0,
            'banned_workers_count': 0,
            'banned_ips': [],
            'banned_workers': [],
            'ip_violations': [],
            'total_banned': 0,
            'bans': [],
        }
    web_root.putChild('ban_stats', WebInterface(get_ban_stats))

    def get_connected_miners():
        try:
            addresses = set()
            for user in wb.connected_workers:
                base = user.split('+')[0].split('/')[0].split('.')[0].split('_')[0]
                if base:
                    addresses.add(base)
            # Fall back to hash-rate-based detection if no live tracking data
            if not addresses:
                miner_hash_rates, _ = wb.get_local_rates()
                for user in miner_hash_rates:
                    base = user.split('+')[0].split('/')[0].split('.')[0].split('_')[0]
                    if base:
                        addresses.add(base)
            return list(addresses)
        except:
            return []
    web_root.putChild('connected_miners', WebInterface(get_connected_miners))

    # ==== Per-miner stats (miner.html) ====
    def get_miner_stats(address):
        try:
            miner_hash_rates, miner_dead_hash_rates = wb.get_local_rates()
            current_payouts = node.get_current_txouts()
            share_diff = bitcoin_data.target_to_difficulty(node.tracker.items[node.best_share_var.value].max_target) if node.best_share_var.value else 0

            # Collect per-address hashrate from all workers matching this address
            total_hr = 0
            total_doa = 0
            for wname, hr in miner_hash_rates.iteritems():
                base = wname.split(',')[0].split('+')[0].split('/')[0].split('.')[0].split('_')[0]
                if base == address:
                    total_hr += hr
                    total_doa += miner_dead_hash_rates.get(wname, 0)

            # Check if miner is currently connected
            is_connected = any(
                wname.split(',')[0].split('+')[0].split('/')[0].split('.')[0].split('_')[0] == address
                for wname in wb.connected_workers
            )
            active = is_connected or total_hr > 0

            # Current payout from share chain (current_txouts keys are address strings)
            payout_sat = 0
            for addr_key, val in current_payouts.iteritems():
                # Normalize: handle both 'bitcoincash:qp...' and 'qp...' formats
                body = addr_key[len('bitcoincash:'):] if isinstance(addr_key, str) and addr_key.startswith('bitcoincash:') else addr_key
                if addr_key == address or body == address or 'bitcoincash:' + addr_key == address:
                    payout_sat += val
                    break

            doa_rate = (total_doa / total_hr) if total_hr > 0 else 0

            # Count accepted/rejected shares across all workers for this address
            total_accepted = 0
            total_rejected = 0
            for wname, ws in wb.worker_shares.iteritems():
                base = wname.split(',')[0].split('+')[0].split('/')[0].split('.')[0].split('_')[0]
                if base == address:
                    total_accepted += ws.get('accepted', 0)
                    total_rejected += ws.get('rejected', 0)

            best_diff_all = wb.address_best_diff.get(address, 0)
            best_diff_round = wb.address_round_best_diff.get(address, 0)
            try:
                network_diff = bitcoin_data.target_to_difficulty(node.bitcoind_work.value['bits'].target) if node.bitcoind_work.value else 0
            except Exception:
                network_diff = 0
            chance = (best_diff_all / network_diff * 100) if (best_diff_all > 0 and network_diff > 0) else 0

            return dict(
                address=address,
                active=active,
                hashrate=total_hr,
                dead_hashrate=total_doa,
                estimated_hashrate=False,
                doa_rate=doa_rate,
                share_difficulty=share_diff,
                current_payout=payout_sat / 1e8,
                total_shares=total_accepted + total_rejected,
                dead_shares=total_rejected,
                best_difficulty_all_time=best_diff_all,
                best_difficulty_session=best_diff_all,
                best_difficulty_round=best_diff_round,
                chance_to_find_block=chance,
                hashrate_periods={
                    '1m': {'hashrate': total_hr},
                    '10m': {'hashrate': total_hr},
                    '1h': {'hashrate': total_hr},
                },
                merged_payouts=[],
            )
        except Exception as e:
            return dict(address=address, active=False, error=str(e))
    web_root.putChild('miner_stats', WebInterface(get_miner_stats))

    def get_miner_payouts(address):
        try:
            # Collect found blocks from share chain that had a reward for this address
            chain_height = node.tracker.get_height(node.best_share_var.value) if node.best_share_var.value else 0
            blocks = [
                s for s in node.tracker.get_chain(node.best_share_var.value, min(chain_height, node.net.CHAIN_LENGTH))
                if s.pow_hash <= s.header['bits'].target
            ] if node.best_share_var.value else []

            explorer_prefix = ''
            try:
                from bitcoin.networks import bitcoincash as bchn
                explorer_prefix = getattr(bchn, 'BLOCK_EXPLORER_URL_PREFIX', '')
            except Exception:
                pass

            blocks_out = []
            for s in blocks:
                # Estimate this miner's share in the block
                share = 0
                try:
                    txouts = s.share_info.get('new_transaction_hashes', {})
                    # Use global current payouts as approximation
                    current_payouts = node.get_current_txouts()
                    # current_payouts keys are already address strings (not script bytes)
                    for addr_key, val in current_payouts.iteritems():
                        body = addr_key[len('bitcoincash:'):] if isinstance(addr_key, str) and addr_key.startswith('bitcoincash:') else addr_key
                        if addr_key == address or body == address or 'bitcoincash:' + addr_key == address:
                            share = val / 1e8
                            break
                except Exception:
                    pass

                block_hash = '%064x' % s.header_hash
                blocks_out.append(dict(
                    timestamp=s.timestamp,
                    block_height=None,
                    block_hash=block_hash,
                    block_reward=s.share_data['subsidy'] / 1e8,
                    estimated_payout=share,
                    confirmations=0,
                    status='confirmed',
                    explorer_url=explorer_prefix + block_hash if explorer_prefix else '#',
                ))

            return dict(
                blocks_found=len(blocks_out),
                total_estimated_rewards=sum(b['estimated_payout'] for b in blocks_out),
                confirmed_rewards=sum(b['estimated_payout'] for b in blocks_out),
                maturing_rewards=0,
                blocks=blocks_out,
            )
        except Exception as e:
            return dict(error=str(e), blocks=[])
    web_root.putChild('miner_payouts', WebInterface(get_miner_payouts))

    web_root.putChild('merged_miner_payouts', WebInterface(lambda address: dict(
        address=address, payouts=[], merged=[]
    )))
    def get_best_share():
        if node.best_share_var.value is None:
            return None
        network_difficulty = bitcoin_data.target_to_difficulty(node.bitcoind_work.value['bits'].target)

        def pct_of_block(diff, net_diff):
            return (diff / net_diff * 100) if net_diff > 0 and diff > 0 else 0

        tip = node.best_share_var.value
        height = node.tracker.get_height(tip)
        window = min(height, node.net.REAL_CHAIN_LENGTH)
        best_diff_round = 0
        best_diff_all = 0
        try:
            for s in node.tracker.get_chain(tip, window):
                diff = bitcoin_data.target_to_difficulty(s.target)
                if diff > best_diff_all:
                    best_diff_all = diff
                if diff > best_diff_round:
                    best_diff_round = diff
        except Exception:
            pass
        median_pct = None
        try:
            diffs = sorted(bitcoin_data.target_to_difficulty(s.target)
                           for s in node.tracker.get_chain(tip, window))
            n = len(diffs)
            if n > 0:
                median_diff = diffs[n // 2] if n % 2 == 1 else (diffs[n // 2 - 1] + diffs[n // 2]) / 2.0
                median_pct = pct_of_block(median_diff, network_difficulty)
        except Exception:
            pass
        result = dict(
            network_difficulty=network_difficulty,
            all_time=dict(
                difficulty=best_diff_all,
                pct_of_block=pct_of_block(best_diff_all, network_difficulty),
                net_diff_at_time=network_difficulty,
                miner=None, timestamp=None,
            ),
            session=dict(
                difficulty=best_diff_round,
                pct_of_block=pct_of_block(best_diff_round, network_difficulty),
                net_diff_at_time=network_difficulty,
                miner=None, timestamp=None, started=start_time,
            ),
            round=dict(
                difficulty=best_diff_round,
                pct_of_block=pct_of_block(best_diff_round, network_difficulty),
                net_diff_at_time=network_difficulty,
                miner=None, timestamp=None, started=None,
            ),
        )
        if median_pct is not None:
            result['median_pct'] = median_pct
        return result
    web_root.putChild('best_share', WebInterface(get_best_share))

    # ==== Stub endpoints for dashboard compatibility ====
    web_root.putChild('v36_status', WebInterface(lambda: None))
    web_root.putChild('node_info', WebInterface(lambda: dict(
        version=p2pool.__version__,
        protocol_version=p2p.Protocol.VERSION,
        uptime=time.time() - start_time,
    )))
    def get_peer_list():
        result = []
        for peer in node.p2p_node.peers.itervalues():
            host = peer.transport.getPeer().host
            port = peer.transport.getPeer().port
            uptime = time.time() - getattr(peer, 'connect_time', time.time())
            result.append(dict(
                address='%s:%i' % (host, port),
                host=host,
                port=port,
                web_port=node.net.WORKER_PORT,
                version=peer.other_sub_version,
                incoming=peer.incoming,
                uptime=uptime,
                downtime=0,
                txpool_size=peer.remembered_txs_size,
            ))
        return result
    web_root.putChild('peer_list', WebInterface(get_peer_list))
    def get_luck_stats():
        if node.best_share_var.value is None or node.bitcoind_work.value is None:
            return dict(luck_available=False, current_luck_trend=None, blocks=[])
        try:
            attempts_per_block = bitcoin_data.target_to_average_attempts(node.bitcoind_work.value['bits'].target)
            attempts_per_share = bitcoin_data.target_to_average_attempts(node.tracker.items[node.best_share_var.value].max_target)
            chain_height = node.tracker.get_height(node.best_share_var.value)
            block_shares = [
                s for s in node.tracker.get_chain(node.best_share_var.value, min(chain_height, node.net.CHAIN_LENGTH))
                if s.pow_hash <= s.header['bits'].target
            ]
            block_heights = {s.hash: node.tracker.get_height(s.hash) for s in block_shares}
            block_shares.sort(key=lambda s: -block_heights[s.hash])  # newest first

            expected_shares = float(attempts_per_block) / attempts_per_share if attempts_per_share > 0 else 0.0

            # Current round luck: shares elapsed since last block vs expected
            current_luck_trend = None
            if expected_shares > 0:
                tip_height = chain_height
                last_block_h = block_heights[block_shares[0].hash] if block_shares else 0
                shares_since = tip_height - last_block_h
                if shares_since > 0:
                    current_luck_trend = 100.0 * expected_shares / shares_since

            blocks_out = []
            for i, s in enumerate(block_shares):
                if i + 1 < len(block_shares):
                    actual_shares = block_heights[s.hash] - block_heights[block_shares[i + 1].hash]
                    luck = (100.0 * expected_shares / actual_shares) if actual_shares > 0 and expected_shares > 0 else None
                else:
                    luck = None
                blocks_out.append(dict(ts=s.timestamp, hash='%064x' % s.header_hash, luck=luck))

            return dict(luck_available=True, current_luck_trend=current_luck_trend, blocks=blocks_out)
        except Exception:
            return dict(luck_available=False, current_luck_trend=None, blocks=[])
    web_root.putChild('luck_stats', WebInterface(get_luck_stats))
    web_root.putChild('broadcaster_status', WebInterface(bitcoin_helper.get_broadcaster_status))
    web_root.putChild('merged_broadcaster_status', WebInterface(lambda: None))
    web_root.putChild('merged_stats', WebInterface(lambda: None))
    web_root.putChild('recent_merged_blocks', WebInterface(lambda: []))
    web_root.putChild('discovered_merged_blocks', WebInterface(lambda: []))
    web_root.putChild('current_merged_payouts', WebInterface(lambda: dict(
        (address, {'amount': value/1e8, 'merged': []}) for address, value
            in node.get_current_txouts().iteritems())))

    web_root.putChild('peer_addresses', WebInterface(lambda: ' '.join('%s%s' % (peer.transport.getPeer().host, ':'+str(peer.transport.getPeer().port) if peer.transport.getPeer().port != node.net.P2P_PORT else '') for peer in node.p2p_node.peers.itervalues())))
    web_root.putChild('peer_txpool_sizes', WebInterface(lambda: dict(('%s:%i' % (peer.transport.getPeer().host, peer.transport.getPeer().port), peer.remembered_txs_size) for peer in node.p2p_node.peers.itervalues())))
    web_root.putChild('pings', WebInterface(defer.inlineCallbacks(lambda: defer.returnValue(
        dict([(a, (yield b)) for a, b in
            [(
                '%s:%i' % (peer.transport.getPeer().host, peer.transport.getPeer().port),
                defer.inlineCallbacks(lambda peer=peer: defer.returnValue(
                    min([(yield peer.do_ping().addCallback(lambda x: x/0.001).addErrback(lambda fail: None)) for i in xrange(3)])
                ))()
            ) for peer in list(node.p2p_node.peers.itervalues())]
        ])
    ))))
    web_root.putChild('peer_versions', WebInterface(lambda: dict(('%s:%i' % peer.addr, peer.other_sub_version) for peer in node.p2p_node.peers.itervalues())))
    web_root.putChild('payout_addr', WebInterface(lambda: wb.address))
    web_root.putChild('payout_addrs', WebInterface(
        lambda: list(add['address'] for add in wb.pubkeys.keys)))
    def get_recent_blocks():
        try:
            if node.best_share_var.value is None or node.bitcoind_work.value is None:
                return []
            chain_height = node.tracker.get_height(node.best_share_var.value)
            block_shares = [
                s for s in node.tracker.get_chain(
                    node.best_share_var.value,
                    min(chain_height, node.net.CHAIN_LENGTH))
                if s.pow_hash <= s.header['bits'].target
            ]
            if not block_shares:
                return []

            # Heights for luck calculation
            block_heights = {s.hash: node.tracker.get_height(s.hash) for s in block_shares}
            block_shares.sort(key=lambda s: -block_heights[s.hash])  # newest first

            # Expected shares per block (using current network/share difficulty for luck comparison)
            expected_shares = 0.0
            try:
                attempts_per_share = bitcoin_data.target_to_average_attempts(
                    node.tracker.items[node.best_share_var.value].max_target)
                attempts_per_block = bitcoin_data.target_to_average_attempts(
                    node.bitcoind_work.value['bits'].target)
                if attempts_per_share > 0:
                    expected_shares = float(attempts_per_block) / attempts_per_share
            except Exception:
                pass

            result = []
            for i, s in enumerate(block_shares):
                h = block_heights[s.hash]
                if i + 1 < len(block_shares):
                    prev_h = block_heights[block_shares[i + 1].hash]
                    actual_shares = h - prev_h
                    time_to_find = float(s.timestamp - block_shares[i + 1].timestamp)
                    luck = (100.0 * expected_shares / actual_shares) if actual_shares > 0 and expected_shares > 0 else None
                    luck_method = 'shares'
                else:
                    time_to_find = None
                    luck = None
                    luck_method = 'first_block'

                # Compute expected_time using actual pool hashrate at this block's position.
                # This matches the "Miners Block Value" card formula: attempts_to_block / pool_hash_rate.
                # The old formula (expected_shares * SHARE_PERIOD) assumed only 1 share per SHARE_PERIOD
                # (single worker) and was wrong by a factor of ~num_workers.
                expected_time = None
                try:
                    lookbehind_at_block = min(h, max(2, 3600 // node.net.SHARE_PERIOD))
                    if lookbehind_at_block >= 2:
                        pool_rate_at_block = p2pool_data.get_pool_attempts_per_second(
                            node.tracker, s.hash, lookbehind_at_block)
                        if pool_rate_at_block > 0:
                            attempts_per_block_at_time = bitcoin_data.target_to_average_attempts(
                                s.header['bits'].target)
                            expected_time = float(attempts_per_block_at_time) / pool_rate_at_block
                except Exception:
                    pass

                try:
                    actual_hash_diff = bitcoin_data.target_to_difficulty(s.pow_hash) if s.pow_hash > 0 else None
                except Exception:
                    actual_hash_diff = None

                result.append(dict(
                    ts=s.timestamp,
                    hash='%064x' % s.header_hash,
                    number=p2pool_data.parse_bip0034(s.share_data['coinbase'])[0],
                    share='%064x' % s.hash,
                    share_difficulty=bitcoin_data.target_to_difficulty(s.target),
                    network_difficulty=bitcoin_data.target_to_difficulty(s.header['bits'].target),
                    actual_hash_difficulty=actual_hash_diff,
                    miner=(bitcoin_data.script2_to_address(s.new_script, node.net.PARENT.ADDRESS_VERSION, -1, node.net.PARENT) or
                           bitcoin_data.script2_to_address(s.new_script, node.net.PARENT.ADDRESS_P2SH_VERSION, -1, node.net.PARENT) or ''),
                    verified=s.hash in node.tracker.verified.items,
                    status='confirmed' if s.hash in node.tracker.verified.items else 'pending',
                    luck=luck,
                    luck_method=luck_method,
                    time_to_find=time_to_find,
                    expected_time=expected_time,
                ))
            return result
        except Exception:
            return []
    web_root.putChild('recent_blocks', WebInterface(get_recent_blocks))
    web_root.putChild('uptime', WebInterface(lambda: time.time() - start_time))
    web_root.putChild('stale_rates', WebInterface(lambda: p2pool_data.get_stale_counts(node.tracker, node.best_share_var.value, decent_height(), rates=True)))
    
    new_root = resource.Resource()
    web_root.putChild('web', new_root)
    
    stat_log = []
    if os.path.exists(os.path.join(datadir_path, 'stats')):
        try:
            with open(os.path.join(datadir_path, 'stats'), 'rb') as f:
                stat_log = json.loads(f.read())
        except:
            log.err(None, 'Error loading stats:')
    def update_stat_log():
        while stat_log and stat_log[0]['time'] < time.time() - 24*60*60:
            stat_log.pop(0)
        
        lookbehind = 3600//node.net.SHARE_PERIOD
        if node.tracker.get_height(node.best_share_var.value) < lookbehind:
            return None
        
        global_stale_prop = p2pool_data.get_average_stale_prop(node.tracker, node.best_share_var.value, lookbehind)
        (stale_orphan_shares, stale_doa_shares), shares, _ = wb.get_stale_counts()
        miner_hash_rates, miner_dead_hash_rates = wb.get_local_rates()
        
        my_current_payout=0.0
        for add in wb.pubkeys.keys:
            my_current_payout += node.get_current_txouts().get(
                    add['address'], 0)*1e-8
        stat_log.append(dict(
            time=time.time(),
            pool_hash_rate=p2pool_data.get_pool_attempts_per_second(node.tracker, node.best_share_var.value, lookbehind)/(1-global_stale_prop),
            pool_stale_prop=global_stale_prop,
            local_hash_rates=miner_hash_rates,
            local_dead_hash_rates=miner_dead_hash_rates,
            shares=shares,
            stale_shares=stale_orphan_shares + stale_doa_shares,
            stale_shares_breakdown=dict(orphan=stale_orphan_shares, doa=stale_doa_shares),
            current_payout=my_current_payout,
            peers=dict(
                incoming=sum(1 for peer in node.p2p_node.peers.itervalues() if peer.incoming),
                outgoing=sum(1 for peer in node.p2p_node.peers.itervalues() if not peer.incoming),
            ),
            attempts_to_share=bitcoin_data.target_to_average_attempts(node.tracker.items[node.best_share_var.value].max_target),
            attempts_to_block=bitcoin_data.target_to_average_attempts(node.bitcoind_work.value['bits'].target),
            block_value=node.bitcoind_work.value['subsidy']*1e-8,
        ))
        
        with open(os.path.join(datadir_path, 'stats'), 'wb') as f:
            f.write(json.dumps(stat_log))
    x = deferral.RobustLoopingCall(update_stat_log)
    x.start(5*60)
    stop_event.watch(x.stop)
    new_root.putChild('log', WebInterface(lambda: stat_log))
    
    def get_share(share_hash_str):
        if int(share_hash_str, 16) not in node.tracker.items:
            return None
        share = node.tracker.items[int(share_hash_str, 16)]
        
        return dict(
            parent='%064x' % share.previous_hash if share.previous_hash else "None",
            far_parent='%064x' % share.share_info['far_share_hash'] if share.share_info['far_share_hash'] else "None",
            children=['%064x' % x for x in sorted(node.tracker.reverse.get(share.hash, set()), key=lambda sh: -len(node.tracker.reverse.get(sh, set())))], # sorted from most children to least children
            type_name=type(share).__name__,
            local=dict(
                verified=share.hash in node.tracker.verified.items,
                time_first_seen=start_time if share.time_seen == 0 else share.time_seen,
                peer_first_received_from=share.peer_addr,
            ),
            share_data=dict(
                timestamp=share.timestamp,
                target=share.target,
                max_target=share.max_target,
                payout_address=share.address if share.address else
                                bitcoin_data.script2_to_address(
                                    share.new_script,
                                    node.net.PARENT.ADDRESS_VERSION,
                                    node.net.PARENT),
                donation=share.share_data['donation']/65535,
                stale_info=share.share_data['stale_info'],
                nonce=share.share_data['nonce'],
                desired_version=share.share_data['desired_version'],
                absheight=share.absheight,
                abswork=share.abswork,
            ),
            block=dict(
                hash='%064x' % share.header_hash,
                header=dict(
                    version=share.header['version'],
                    previous_block='%064x' % share.header['previous_block'],
                    merkle_root='%064x' % share.header['merkle_root'],
                    timestamp=share.header['timestamp'],
                    target=share.header['bits'].target,
                    nonce=share.header['nonce'],
                ),
                gentx=dict(
                    hash='%064x' % share.gentx_hash,
                    raw=bitcoin_data.tx_id_type.pack(share.gentx).encode('hex') if hasattr(share, 'gentx') else "unknown",
                    coinbase=share.share_data['coinbase'].ljust(2, '\x00').encode('hex'),
                    value=share.share_data['subsidy']*1e-8,
                    last_txout_nonce='%016x' % share.contents['last_txout_nonce'],
                ),
                other_transaction_hashes=['%064x' % x for x in share.get_other_tx_hashes(node.tracker)],
            ),
        )

    def get_share_address(share_hash_str):
        if int(share_hash_str, 16) not in node.tracker.items:
            return None
        share = node.tracker.items[int(share_hash_str, 16)]
        try:
            return share.address
        except AttributeError:
            return bitcoin_data.script2_to_address(share.new_script,
                                                   node.net.ADDRESS_VERSION, -1,
                                                   node.net.PARENT)

    new_root.putChild('payout_address', WebInterface(lambda share_hash_str: get_share_address(share_hash_str)))
    new_root.putChild('share', WebInterface(lambda share_hash_str: get_share(share_hash_str)))
    new_root.putChild('heads', WebInterface(lambda: ['%064x' % x for x in node.tracker.heads]))
    new_root.putChild('verified_heads', WebInterface(lambda: ['%064x' % x for x in node.tracker.verified.heads]))
    new_root.putChild('tails', WebInterface(lambda: ['%064x' % x for t in node.tracker.tails for x in node.tracker.reverse.get(t, set())]))
    new_root.putChild('verified_tails', WebInterface(lambda: ['%064x' % x for t in node.tracker.verified.tails for x in node.tracker.verified.reverse.get(t, set())]))
    new_root.putChild('best_share_hash', WebInterface(lambda: '%064x' % node.best_share_var.value))
    new_root.putChild('my_share_hashes', WebInterface(lambda: ['%064x' % my_share_hash for my_share_hash in wb.my_share_hashes]))
    new_root.putChild('my_share_hashes50', WebInterface(lambda: ['%064x' % my_share_hash for my_share_hash in list(wb.my_share_hashes)[:50]]))
    def get_share_data(share_hash_str):
        if int(share_hash_str, 16) not in node.tracker.items:
            return ''
        share = node.tracker.items[int(share_hash_str, 16)]
        return p2pool_data.share_type.pack(share.as_share())
    new_root.putChild('share_data', WebInterface(lambda share_hash_str: get_share_data(share_hash_str), 'application/octet-stream'))
    new_root.putChild('currency_info', WebInterface(lambda: dict(
        symbol=node.net.PARENT.SYMBOL,
        block_explorer_url_prefix=node.net.PARENT.BLOCK_EXPLORER_URL_PREFIX,
        address_explorer_url_prefix=node.net.PARENT.ADDRESS_EXPLORER_URL_PREFIX,
        tx_explorer_url_prefix=node.net.PARENT.TX_EXPLORER_URL_PREFIX,
        block_period=node.net.PARENT.BLOCK_PERIOD,
    )))
    new_root.putChild('version', WebInterface(lambda: p2pool.__version__))
    
    hd_path = os.path.join(datadir_path, 'graph_db')
    hd_data = _atomic_read(hd_path)
    hd_obj = {}
    if hd_data is not None:
        try:
            hd_obj = json.loads(hd_data)
        except Exception:
            log.err(None, 'Error reading graph database:')
    dataview_descriptions = {
        'last_hour': graph.DataViewDescription(150, 60*60),
        'last_day': graph.DataViewDescription(300, 60*60*24),
        'last_week': graph.DataViewDescription(300, 60*60*24*7),
        'last_month': graph.DataViewDescription(300, 60*60*24*30),
        'last_year': graph.DataViewDescription(300, 60*60*24*365.25),
    }
    hd = graph.HistoryDatabase.from_obj({
        'local_hash_rate': graph.DataStreamDescription(dataview_descriptions, is_gauge=False),
        'local_dead_hash_rate': graph.DataStreamDescription(dataview_descriptions, is_gauge=False),
        'local_share_hash_rates': graph.DataStreamDescription(dataview_descriptions, is_gauge=False,
            multivalues=True, multivalue_undefined_means_0=True,
            default_func=graph.make_multivalue_migrator(dict(good='local_share_hash_rate', dead='local_dead_share_hash_rate', orphan='local_orphan_share_hash_rate'),
                post_func=lambda bins: [dict((k, (v[0] - (sum(bin.get(rem_k, (0, 0))[0] for rem_k in ['dead', 'orphan']) if k == 'good' else 0), v[1])) for k, v in bin.iteritems()) for bin in bins])),
        'pool_rates': graph.DataStreamDescription(dataview_descriptions, multivalues=True,
            multivalue_undefined_means_0=True),
        'current_payout': graph.DataStreamDescription(dataview_descriptions),
        'current_payouts': graph.DataStreamDescription(dataview_descriptions, multivalues=True),
        'peers': graph.DataStreamDescription(dataview_descriptions, multivalues=True, default_func=graph.make_multivalue_migrator(dict(incoming='incoming_peers', outgoing='outgoing_peers'))),
        'miner_hash_rates': graph.DataStreamDescription(dataview_descriptions, is_gauge=False, multivalues=True, multivalues_keep=10000),
        'miner_dead_hash_rates': graph.DataStreamDescription(dataview_descriptions, is_gauge=False, multivalues=True, multivalues_keep=10000),
        'desired_version_rates': graph.DataStreamDescription(dataview_descriptions, multivalues=True,
            multivalue_undefined_means_0=True),
        'traffic_rate': graph.DataStreamDescription(dataview_descriptions, is_gauge=False, multivalues=True),
        'getwork_latency': graph.DataStreamDescription(dataview_descriptions),
        'memory_usage': graph.DataStreamDescription(dataview_descriptions),
        'connected_miners': graph.DataStreamDescription(dataview_descriptions),
        'unique_miner_count': graph.DataStreamDescription(dataview_descriptions),
        'worker_count': graph.DataStreamDescription(dataview_descriptions),
    }, hd_obj)
    x = deferral.RobustLoopingCall(lambda: _atomic_write(hd_path, json.dumps(hd.to_obj())))
    x.start(100)
    stop_event.watch(x.stop)
    @wb.pseudoshare_received.watch
    def _(work, dead, user):
        t = time.time()
        hd.datastreams['local_hash_rate'].add_datum(t, work)
        if dead:
            hd.datastreams['local_dead_hash_rate'].add_datum(t, work)
        if user is not None:
            hd.datastreams['miner_hash_rates'].add_datum(t, {user: work})
            if dead:
                hd.datastreams['miner_dead_hash_rates'].add_datum(t, {user: work})
    @wb.share_received.watch
    def _(work, dead, share_hash):
        t = time.time()
        if not dead:
            hd.datastreams['local_share_hash_rates'].add_datum(t, dict(good=work))
        else:
            hd.datastreams['local_share_hash_rates'].add_datum(t, dict(dead=work))
        def later():
            res = node.tracker.is_child_of(share_hash, node.best_share_var.value)
            if res is None: res = False # share isn't connected to sharechain? assume orphaned
            if res and dead: # share was DOA, but is now in sharechain
                # move from dead to good
                hd.datastreams['local_share_hash_rates'].add_datum(t, dict(dead=-work, good=work))
            elif not res and not dead: # share wasn't DOA, and isn't in sharechain
                # move from good to orphan
                hd.datastreams['local_share_hash_rates'].add_datum(t, dict(good=-work, orphan=work))
        reactor.callLater(200, later)
    @node.p2p_node.traffic_happened.watch
    def _(name, bytes):
        hd.datastreams['traffic_rate'].add_datum(time.time(), {name: bytes})
    def add_point():
        if node.tracker.get_height(node.best_share_var.value) < 10:
            return None
        lookbehind = min(node.net.CHAIN_LENGTH, 60*60//node.net.SHARE_PERIOD, node.tracker.get_height(node.best_share_var.value))
        t = time.time()
        
        pool_rates = p2pool_data.get_stale_counts(node.tracker, node.best_share_var.value, lookbehind, rates=True)
        pool_total = sum(pool_rates.itervalues())
        hd.datastreams['pool_rates'].add_datum(t, pool_rates)
        
        current_txouts = node.get_current_txouts()
        my_current_payouts = 0.0
        for add in wb.pubkeys.keys:
            my_current_payouts += current_txouts.get(
                    add['address'], 0) * 1e-8
        hd.datastreams['current_payout'].add_datum(t, my_current_payouts)
        miner_hash_rates, miner_dead_hash_rates = wb.get_local_rates()
        # current_txouts keys are already address strings (from WeightsSkipList using share.address)
        # Try normalized lookup: strip/add 'bitcoincash:' prefix to handle mixed formats
        def lookup_payout(addr, txouts):
            if addr in txouts:
                return txouts[addr]
            # Try without prefix
            body = addr[len('bitcoincash:'):] if addr.startswith('bitcoincash:') else None
            if body and body in txouts:
                return txouts[body]
            # Try with prefix
            with_prefix = 'bitcoincash:' + addr
            if with_prefix in txouts:
                return txouts[with_prefix]
            return 0
        payout_by_addr = {}
        for worker_name in miner_hash_rates:
            base_addr = worker_name.split(',')[0].split('+')[0].split('/')[0].split('.')[0].split('_')[0]
            val = lookup_payout(base_addr, current_txouts)
            if val > 0:
                payout_by_addr[base_addr] = val * 1e-8
        hd.datastreams['current_payouts'].add_datum(t, payout_by_addr)
        
        hd.datastreams['peers'].add_datum(t, dict(
            incoming=sum(1 for peer in node.p2p_node.peers.itervalues() if peer.incoming),
            outgoing=sum(1 for peer in node.p2p_node.peers.itervalues() if not peer.incoming),
        ))
        
        vs = p2pool_data.get_desired_version_counts(node.tracker, node.best_share_var.value, lookbehind)
        vs_total = sum(vs.itervalues())
        hd.datastreams['desired_version_rates'].add_datum(t, dict((str(k), v/vs_total*pool_total) for k, v in vs.iteritems()))
        try:
            hd.datastreams['memory_usage'].add_datum(t, memory.resident())
        except:
            if p2pool.DEBUG:
                traceback.print_exc()
        # Track worker/miner counts for graphs
        hd.datastreams['worker_count'].add_datum(t, len(miner_hash_rates))
        unique_addrs = set()
        for user in miner_hash_rates:
            base = user.split(',')[0].split('+')[0].split('/')[0].split('.')[0].split('_')[0]
            unique_addrs.add(base)
        hd.datastreams['unique_miner_count'].add_datum(t, len(unique_addrs))
        hd.datastreams['connected_miners'].add_datum(t, len(wb.connected_workers))
    x = deferral.RobustLoopingCall(add_point)
    x.start(5)
    stop_event.watch(x.stop)
    @node.bitcoind_work.changed.watch
    def _(new_work):
        hd.datastreams['getwork_latency'].add_datum(time.time(), new_work['latency'])
    new_root.putChild('graph_data', WebInterface(lambda source, view: hd.datastreams[source].dataviews[view].get_data(time.time())))

    # ==== Network difficulty history endpoint ====
    network_diff_history = []
    known_diff_timestamps = set()
    network_diff_history_path = os.path.join(datadir_path, 'network_difficulty_history')
    raw_nd = _atomic_read(network_diff_history_path)
    if raw_nd is not None:
        try:
            network_diff_history[:] = json.loads(raw_nd)
            known_diff_timestamps.update(int(b['ts']) for b in network_diff_history if b.get('ts'))
        except:
            pass

    def add_network_diff_sample(timestamp, network_diff, source='block'):
        ts_key = int(timestamp)
        if ts_key not in known_diff_timestamps:
            network_diff_history.append({'ts': timestamp, 'network_diff': network_diff, 'source': source})
            known_diff_timestamps.add(ts_key)
            network_diff_history.sort(key=lambda x: x['ts'])

    def sample_current_network_diff():
        try:
            if wb.current_work.value and 'bits' in wb.current_work.value:
                add_network_diff_sample(time.time(),
                    bitcoin_data.target_to_difficulty(wb.current_work.value['bits'].target), 'periodic')
        except:
            pass

    def save_network_diff_history():
        try:
            while len(network_diff_history) > 2000:
                oldest = network_diff_history.pop(0)
                known_diff_timestamps.discard(int(oldest['ts']))
            _atomic_write(network_diff_history_path, json.dumps(network_diff_history))
        except:
            pass

    x_nd = deferral.RobustLoopingCall(save_network_diff_history)
    x_nd.start(120)
    stop_event.watch(x_nd.stop)
    x_nd2 = deferral.RobustLoopingCall(sample_current_network_diff)
    x_nd2.start(300)
    stop_event.watch(x_nd2.stop)

    class NetworkDifficultyResource(resource.Resource):
        def render_GET(self, request):
            request.setHeader('Content-Type', 'application/json')
            request.setHeader('Access-Control-Allow-Origin', '*')
            try:
                period = (request.args.get('period') or ['hour'])[0]
                now = time.time()
                cutoffs = {'hour': 3600, 'day': 86400, 'week': 604800, 'month': 2592000, 'year': 31536000}
                cutoff = now - cutoffs.get(period, 3600)
                samples = [d for d in network_diff_history if d['ts'] >= cutoff]
                if wb.current_work.value and 'bits' in wb.current_work.value:
                    samples.append({'ts': now,
                        'network_diff': bitcoin_data.target_to_difficulty(wb.current_work.value['bits'].target),
                        'source': 'current'})
                samples.sort(key=lambda x: x['ts'])
                return json.dumps(samples)
            except:
                return json.dumps([])
    web_root.putChild('network_difficulty', NetworkDifficultyResource())

    if static_dir is None:
        static_dir = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), 'web-static')
    web_root.putChild('static', static.File(static_dir))
    
    return web_root
