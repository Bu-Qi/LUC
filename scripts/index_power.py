"""Resumable, read-only LUC power indexing on QKI. Python standard library only."""
import argparse
import concurrent.futures as futures
import datetime as dt
import json
import os
from pathlib import Path
import re
import time
import urllib.request
from urllib.parse import urlsplit

CONTRACT = '0x5c8426a122bf473a068e6d9dd5d2d624cbf958f9'
CHAIN = 20181205
ZERO = '0x' + '0' * 40
RPCS = [f'https://rpc{i}.{d}' for d in ('qkirpc.org', 'qkiscan.io') for i in (1, 2, 3)]
SELECTORS = {'power':'0x503371a5', 'totalPower':'0xdb3ad22c', 'users':'0xdd94a29d', 'decimals':'0x313ce567'}
TOPICS = ['0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef', '0x9d228d69b5fdb8d273a2336f8fb8612d039631024ea9bf09c424a9503aa078f0']
ARG_POSITIONS = {'0xa9059cbb':[0], '0x23b872dd':[0, 1], '0x0840605a':[0]}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def address(s):
    return s.lower() if isinstance(s, str) and re.fullmatch(r'0x[0-9a-fA-F]{40}', s) else None


def word_address(s):
    return address('0x' + s[-40:]) if isinstance(s, str) and re.fullmatch(r'0x0{24}[0-9a-fA-F]{40}', s) else None


def uint(s):
    if not isinstance(s, str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}', s):
        raise ValueError('Invalid uint256 response; missing state or incorrect contract')
    return int(s, 16)


def calldata(name, account=None):
    return SELECTORS[name] + (address(account)[2:].rjust(64, '0') if account is not None else '')


def input_addresses(data):
    out = set()
    for i in ARG_POSITIONS.get(str(data)[:10].lower(), []):
        a = word_address('0x' + data[10+i*64:10+(i+1)*64])
        if a and a != ZERO:
            out.add(a)
    return out


def block_addresses(block):
    if not isinstance(block.get('transactions'), list):
        raise ValueError('Block transaction list missing')
    out = set()
    for tx in block['transactions']:
        if not isinstance(tx, dict):
            raise ValueError('Expected full transactions, not hashes')
        if address(tx.get('to')) == CONTRACT:
            a = address(tx.get('from'))
            if a and a != ZERO:
                out.add(a)
            out.update(input_addresses(tx.get('input', '')))
    return out


def log_addresses(logs):
    out = set()
    for log in logs:
        topics = log.get('topics', [])
        if log.get('removed') or address(log.get('address')) != CONTRACT or not topics or topics[0].lower() not in TOPICS:
            continue
        for value in topics[1:3]:
            a = word_address(value)
            if a and a != ZERO:
                out.add(a)
    return out


def trace_addresses(frame):
    if not isinstance(frame, dict) or frame.get('error'):
        return set()
    out = set()
    if address(frame.get('to')) == CONTRACT and frame.get('type', '').upper() in ('CALL', 'STATICCALL'):
        a = address(frame.get('from'))
        if a and a != ZERO:
            out.add(a)
        out.update(input_addresses(frame.get('input', '')))
    for child in frame.get('calls', []):
        out.update(trace_addresses(child))
    return out


def ranking(values):
    ordered = sorted(((a, int(p)) for a, p in values.items() if int(p) > 0), key=lambda x: (-x[1], x[0]))
    rows, previous, rank = [], None, 0
    for i, (a, power) in enumerate(ordered, 1):
        if power != previous:
            rank = i
        rows.append({'rank': rank, 'address': a, 'power_raw': str(power)})
        previous = power
    return rows


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':'))+'\n', encoding='utf-8')
    tmp.replace(path)


class RPC:
    def __init__(self, url):
        self.url, self.label, self.batch_supported = url, urlsplit(url).hostname, True

    def request(self, payload):
        for attempt in range(2):
            try:
                req = urllib.request.Request(self.url, data=json.dumps(payload).encode(), headers={'Content-Type':'application/json', 'User-Agent':'LUC-Power/2.0'})
                with urllib.request.urlopen(req, timeout=12) as response:
                    return json.load(response)
            except Exception as error:
                if attempt:
                    # Never publish private endpoint URLs / API keys in public logs.
                    raise RuntimeError(f'{self.label}: transport {type(error).__name__}') from None
                time.sleep(0.5)

    def call(self, method, params):
        obj = self.request({'jsonrpc':'2.0', 'id':1, 'method':method, 'params':params})
        if not isinstance(obj, dict) or obj.get('error') or 'result' not in obj:
            code = obj.get('error', {}).get('code', 'invalid') if isinstance(obj, dict) else 'invalid'
            raise RuntimeError(f'{self.label}: {method} RPC error {code}')
        return obj['result']

    def many(self, calls):
        if not calls:
            return []
        if self.batch_supported:
            try:
                raw = self.request([{'jsonrpc':'2.0', 'id':i, 'method':m, 'params':p} for i, (m,p) in enumerate(calls)])
            except RuntimeError:
                raw = None
            if isinstance(raw, list) and len(raw) == len(calls):
                by_id = {x.get('id'):x for x in raw if isinstance(x, dict)}
                if all(i in by_id and 'result' in by_id[i] and not by_id[i].get('error') for i in range(len(calls))):
                    return [by_id[i]['result'] for i in range(len(calls))]
            self.batch_supported = False
        with futures.ThreadPoolExecutor(max_workers=4) as pool:
            return list(pool.map(lambda c: self.call(*c), calls))

    def read(self, name, tag):
        return uint(self.call('eth_call', [{'to':CONTRACT, 'data':calldata(name)}, tag]))


def select_rpc(urls):
    errors = []
    for url in urls:
        if urlsplit(url).scheme != 'https':
            continue
        rpc = RPC(url)
        try:
            if int(rpc.call('eth_chainId', []), 16) != CHAIN:
                raise ValueError('wrong chain ID')
            head = int(rpc.call('eth_blockNumber', []), 16)
            tag = hex(max(0, head-12))
            if rpc.call('eth_getCode', [CONTRACT, tag]) in ('0x', '0x0', None):
                raise ValueError('no contract bytecode')
            if rpc.read('decimals', tag) != 3:
                raise ValueError('unexpected LUC decimals')
            rpc.read('totalPower', tag)
            return rpc
        except Exception as error:
            errors.append(f'{rpc.label}: {error}')
    raise RuntimeError('No usable QKI RPC. '+'; '.join(errors))


def initial_state():
    start = max(0, int(os.environ.get('LUC_START_BLOCK', '0')))
    return {'schema':2, 'contract':CONTRACT, 'chain_id':CHAIN, 'start_block':start, 'next_block':start,
            'next_log_block':start, 'next_trace_block':start, 'last_block_hash':None, 'addresses':[], 'log_span':20000}


def validate_block(block, n):
    if not isinstance(block, dict) or int(block.get('number', '-1'), 16) != n or not block.get('hash'):
        raise ValueError(f'Missing/incorrect block #{n}; cursor not advanced')


def scan(rpc, state, target, deadline, checkpoint):
    known = set(state['addresses'])
    logs_working, trace_working = True, os.environ.get('LUC_TRACE', '0') == '1'
    warnings = []
    if state.get('last_block_hash') and state['next_block'] > state['start_block']:
        n = state['next_block']-1
        block = rpc.call('eth_getBlockByNumber', [hex(n), False])
        validate_block(block, n)
        if block['hash'].lower() != state['last_block_hash'].lower():
            for key in ('next_block', 'next_log_block', 'next_trace_block'):
                state[key] = state['start_block']
            state['last_block_hash'] = None
            warnings.append('Checkpoint reorganized; restarting discovery, retaining candidate superset')
    while time.monotonic() < deadline:
        moved = False
        if logs_working and state['next_log_block'] <= target:
            lo = state['next_log_block']; hi = min(target, lo+state['log_span']-1)
            try:
                logs = rpc.call('eth_getLogs', [{'address':CONTRACT, 'fromBlock':hex(lo), 'toBlock':hex(hi), 'topics':[TOPICS]}])
                if not isinstance(logs, list):
                    raise ValueError('Invalid logs')
                known.update(log_addresses(logs))
                state['next_log_block'] = hi+1
                state['addresses'] = sorted(known)
                checkpoint(); moved = True
            except Exception:
                state['log_span'] = max(1, state['log_span']//2)
                if state['log_span'] == 1:
                    logs_working = False
                    warnings.append('Logs paused at saved cursor; full block scan continues')
        if state['next_block'] <= target and time.monotonic() < deadline:
            lo = state['next_block']; hi = min(target+1, lo+25)
            blocks = rpc.many([('eth_getBlockByNumber', [hex(n), True]) for n in range(lo, hi)])
            previous = state.get('last_block_hash')
            if len(blocks) != hi-lo:
                raise ValueError('Incomplete block batch')
            for n, block in zip(range(lo,hi), blocks):
                validate_block(block,n)
                if previous and block.get('parentHash','').lower() != previous.lower():
                    raise ValueError('Non-contiguous block batch; cursor not advanced')
                known.update(block_addresses(block)); previous = block['hash']
            state['next_block'], state['last_block_hash'] = hi, previous
            state['addresses'] = sorted(known)
            checkpoint(); moved = True
        if trace_working and state['next_trace_block'] <= target and time.monotonic() < deadline:
            n = state['next_trace_block']
            try:
                traces = rpc.call('debug_traceBlockByNumber', [hex(n), {'tracer':'callTracer','timeout':'5s'}])
                if not isinstance(traces,list):
                    raise ValueError('Invalid trace')
                for entry in traces:
                    if not isinstance(entry,dict) or entry.get('error') or not isinstance(entry.get('result'),dict):
                        raise ValueError('Incomplete trace')
                    known.update(trace_addresses(entry['result']))
                state['next_trace_block'] = n+1; moved = True
            except Exception:
                trace_working = False
                warnings.append('Historical callTracer unavailable; trace cursor retained')
        state['addresses'] = sorted(known)
        checkpoint()
        if not moved and (not logs_working or state['next_log_block'] > target):
            break
    return warnings


def make_snapshot(rpc,state):
    n = max(0,int(rpc.call('eth_blockNumber',[]),16)-12); tag = hex(n)
    block = rpc.call('eth_getBlockByNumber',[tag,False]); validate_block(block,n)
    total,users,decimals = [uint(v) for v in rpc.many([('eth_call',[{'to':CONTRACT,'data':calldata(k)},tag]) for k in ('totalPower','users','decimals')])]
    if decimals != 3:
        raise ValueError('LUC decimals mismatch')
    accounts = sorted(set(state['addresses'])); values = {}; deadline = time.monotonic()+240
    for i in range(0,len(accounts),75):
        if time.monotonic() > deadline:
            raise TimeoutError('Snapshot budget exceeded; previous snapshot retained')
        part = accounts[i:i+75]
        results = rpc.many([('eth_call',[{'to':CONTRACT,'data':calldata('power',a)},tag]) for a in part])
        if len(results) != len(part):
            raise ValueError('Missing power responses')
        values.update({a:uint(v) for a,v in zip(part,results)})
    check = rpc.call('eth_getBlockByNumber',[tag,False])
    if not check or check.get('hash') != block['hash']:
        raise ValueError('Snapshot reorganized; previous snapshot retained')
    indexed = sum(values.values()); rows = ranking(values)
    return {'schema':2,'asset':'LUC','chain':'QKI','chain_id':CHAIN,'contract':CONTRACT,'decimals':3,
            'updated_at':now(),'block_number':n,'block_hash':block['hash'],'total_power_raw':str(total),
            'registry_count_raw':str(users),'indexed_power_raw':str(indexed),'gap_raw':str(total-indexed),
            'power_reconciled':total == indexed,'candidate_count':len(values),'positive_count':len(rows),'rows':rows,
            'scan_at_snapshot':{k:state[k] for k in ('start_block','next_block','next_log_block','next_trace_block')},
            'source_assumption':'Supplied source matches deployment and all power changes update totalPower','rpc_host':rpc.label}


def run(root,seconds):
    root = Path(root); state_path = root/'state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else initial_state()
    if state.get('schema') != 2 or state.get('contract') != CONTRACT or state.get('chain_id') != CHAIN:
        raise ValueError('Incompatible saved state; refusing to overwrite')
    if any(address(a) != a or a == ZERO for a in state['addresses']):
        raise ValueError('Invalid saved address')
    state['addresses'] = sorted(set(state['addresses']))
    status = {'schema':2,'asset':'LUC','attempted_at':now(),'state':'running','warnings':[]}; code = 0
    try:
        urls = [u for u in re.split(r'[\s,;]+',os.environ.get('QKI_RPC_URLS','').strip()) if u]
        rpc = select_rpc(urls or RPCS)
        target = max(0,int(rpc.call('eth_blockNumber',[]),16)-12)
        status.update(target_block=target,rpc_host=rpc.label)
        try:
            status['warnings'] = scan(rpc,state,target,time.monotonic()+seconds,lambda:save(state_path,state))
        except Exception as error:
            status['warnings'].append(f'Scan paused with checkpoint retained: {error}')
        snapshot = make_snapshot(rpc,state)
        save(root/'public'/'ranking.json',snapshot)
        status['state'] = 'ok'
    except Exception as error:
        status.update(state='error',error=str(error)); code = 2
    finally:
        save(state_path,state)
        status.update(finished_at=now(),candidate_count=len(state['addresses']),trace_enabled=os.environ.get('LUC_TRACE','0')=='1',
                      **{k:state[k] for k in ('start_block','next_block','next_log_block','next_trace_block')})
        save(root/'public'/'status.json',status)
        print(json.dumps(status,ensure_ascii=True),flush=True)
    return code


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--state-dir',default='.luc-data')
    parser.add_argument('--seconds',type=int,default=480)
    args = parser.parse_args()
    if not 5 <= args.seconds <= 1200:
        parser.error('--seconds must be 5..1200')
    raise SystemExit(run(args.state_dir,args.seconds))
