import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('indexer',Path(__file__).resolve().parents[1]/'scripts/index_power.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
A,B,C=['0x'+s*40 for s in ('1','2','3')]
def enc(n):return '0x'+format(n,'064x')
def topic(a):return '0x'+a[2:].rjust(64,'0')
def block(n,txs=None):return {'number':hex(n),'hash':enc(n+100),'parentHash':enc(n+99),'transactions':txs or []}
class Fake:
    label='test.invalid'
    def call(self,method,params):
        if method=='eth_blockNumber':return hex(14)
        if method=='eth_getBlockByNumber':return block(int(params[0],16))
        if method=='eth_getLogs':return [{'address':m.CONTRACT,'topics':[m.TOPICS[0],topic(A),topic(B)]}]
        raise ValueError(method)
    def many(self,calls):
        result=[]
        for method,params in calls:
            if method=='eth_getBlockByNumber':result.append(block(int(params[0],16),[{'from':C,'to':m.CONTRACT,'input':'0x'}]))
            else:
                selector=params[0]['data'][:10]
                value={m.SELECTORS['totalPower']:100000,m.SELECTORS['users']:9,m.SELECTORS['decimals']:3}.get(selector,50000)
                result.append(enc(value))
        return result
class Core(unittest.TestCase):
    def test_address(self):self.assertEqual(m.address(A),A);self.assertIsNone(m.address('0x1'))
    def test_uint256(self):self.assertEqual(m.uint(enc(2**255)),2**255)
    def test_invalid_uint(self):
        with self.assertRaises(ValueError):m.uint('0x')
    def test_bad_word(self):self.assertIsNone(m.word_address('0x'+'f'*64))
    def test_abi(self):self.assertEqual(m.calldata('power',A),'0x503371a5'+A[2:].rjust(64,'0'))
    def test_eventless_airdrop(self):self.assertEqual(m.block_addresses(block(1,[{'from':A,'to':m.CONTRACT,'input':'0x'}])),{A})
    def test_other_contract(self):self.assertEqual(m.block_addresses(block(1,[{'from':A,'to':B}])),set())
    def test_hashes_not_full_transactions(self):
        with self.assertRaises(ValueError):m.block_addresses(block(1,['0xhash']))
    def test_missing_transactions(self):
        with self.assertRaises(ValueError):m.block_addresses({'number':'0x1'})
    def test_transfer_args(self):self.assertEqual(m.input_addresses('0x23b872dd'+topic(A)[2:]+topic(B)[2:]),{A,B})
    def test_logs(self):self.assertEqual(m.log_addresses(Fake().call('eth_getLogs',[])),{A,B})
    def test_removed_logs(self):
        x=Fake().call('eth_getLogs',[])[0];x['removed']=True;self.assertEqual(m.log_addresses([x]),set())
    def test_internal_caller(self):self.assertEqual(m.trace_addresses({'type':'CALL','to':B,'calls':[{'type':'CALL','from':B,'to':m.CONTRACT}]}),{B})
    def test_reverted_ancestor(self):self.assertEqual(m.trace_addresses({'error':'revert','calls':[{'type':'CALL','from':B,'to':m.CONTRACT}]}),set())
    def test_delegatecall(self):self.assertEqual(m.trace_addresses({'type':'DELEGATECALL','from':B,'to':m.CONTRACT}),set())
    def test_exact_sort_ties_zero(self):
        rows=m.ranking({A:2**200,B:2**200,C:2**200-1,m.ZERO:0});self.assertEqual([r['rank'] for r in rows],[1,1,3]);self.assertEqual(rows[0]['power_raw'],str(2**200))
    def test_no_block(self):
        with self.assertRaises(ValueError):m.validate_block(None,1)
    def test_scan_resume(self):
        s=m.initial_state();m.scan(Fake(),s,2,time.monotonic()+1,lambda:None);self.assertEqual(s['next_block'],3);self.assertEqual(set(s['addresses']),{A,B,C})
        m.scan(Fake(),s,3,time.monotonic()+1,lambda:None);self.assertEqual(s['next_block'],4)
    def test_failed_batch_retains_log_candidates(self):
        class Fail(Fake):
            def many(self,calls):raise RuntimeError('unavailable')
        s=m.initial_state()
        with self.assertRaises(RuntimeError):m.scan(Fail(),s,2,time.monotonic()+1,lambda:None)
        self.assertEqual(s['next_block'],0);self.assertEqual(s['next_log_block'],3);self.assertEqual(set(s['addresses']),{A,B})
    def test_same_snapshot_block(self):
        class Check(Fake):
            def many(self,calls):
                assert all(p[1]=='0x2' for method,p in calls)
                return super().many(calls)
        s=m.initial_state();s['addresses']=[A,B];d=m.make_snapshot(Check(),s);self.assertTrue(d['power_reconciled']);self.assertEqual(d['block_number'],2)
    def test_partial_gap(self):
        s=m.initial_state();s['addresses']=[A];d=m.make_snapshot(Fake(),s);self.assertEqual(d['gap_raw'],'50000');self.assertFalse(d['power_reconciled'])
    def test_reorg_refuses_snapshot(self):
        class Reorg(Fake):
            count=0
            def call(self,method,params):
                r=super().call(method,params)
                if method=='eth_getBlockByNumber':
                    self.count+=1
                    if self.count>1:r['hash']=enc(999)
                return r
        with self.assertRaises(ValueError):m.make_snapshot(Reorg(),m.initial_state())
    def test_failed_snapshot_preserved(self):
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'public/ranking.json';m.save(path,{'old':True})
            with patch.object(m,'select_rpc',side_effect=RuntimeError('offline')):self.assertEqual(m.run(t,5),2)
            self.assertEqual(json.loads(path.read_text()),{'old':True})
    def test_batch_fallback(self):
        class Batch(m.RPC):
            def request(self,p):
                if isinstance(p,list):raise RuntimeError('unsupported')
                return {'result':42}
        self.assertEqual(Batch('https://test.invalid').many([('a',[]),('b',[])]),[42,42])
    def test_batch_order(self):
        class Batch(m.RPC):
            def request(self,p):return [{'id':1,'result':'B'},{'id':0,'result':'A'}]
        self.assertEqual(Batch('https://test.invalid').many([('a',[]),('b',[])]),['A','B'])
if __name__=='__main__':unittest.main()
