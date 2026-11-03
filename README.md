# LUC Power Dashboard

LUC business power on the **QKI chain**, not QKI network hash rate or token balances.
Contract: `0x5c8426a122Bf473a068e6D9Dd5d2D624CbF958f9`; chain ID `20181205`; power display scale `1000`.

## Automatic address discovery

1. Auto-detect the contract deployment block using historical `eth_getCode` binary search (or use `LUC_START_BLOCK` as a verified override), then scan every full block from that height. Collect senders of transactions to LUC and known address arguments; supplement with this contract's Transfer/Minted logs.
2. Deduplicate addresses and call `power(address)` for every candidate at one fixed block.
3. Exclude zero-power entries, sort by exact integer power, and use shared ranks for ties (1, 1, 3).
4. Reconcile the sum against `totalPower` at that same block and publish JSON for the static Pages site.

Each run saves cursors and addresses on the **luc-data** branch. Later runs resume, not restart. The workflow is scheduled at minute 7, 22, 37, 52 each hour (UTC); GitHub can delay scheduled runs. Code pushes scan for 60 seconds; scheduled runs use 480 seconds, followed by snapshot collection. A manual run accepts a 5-1200 second scan budget. The frontend refreshes published JSON every two minutes; this does not mean real-time chain indexing.

Failed RPC reads never become zero-power values. A failed snapshot preserves the last successful snapshot. The UI displays snapshot time, block number, scan progress, and exact power gap. A checkpoint hash mismatch resets discovery, retaining a safe candidate superset. Snapshot block hashes are checked before publication. Twelve confirmation blocks are a safety buffer, not a finality guarantee.

## Completeness

Direct scans find eventless direct airdrop calls. Calls through other contracts without emitted events additionally need historical internal-call tracing. Set `LUC_TRACE=1` for an RPC supporting `debug_traceBlockByNumber` with `callTracer`; it has an independent resumable cursor and is off by default. Logs also discover internal interactions that emit Transfer/Minted.

The board remains **partial until exact power totals reconcile**. Reconciliation is conditional on the supplied source matching deployment, all power mutations maintaining totalPower, and correct RPC data. It covers current positive power, not necessarily every historical participant now at zero. `totalUsersAmount` is not a unique holder count. A full genesis scan can need many runs; no completion time is promised.

## Settings

Enable **Settings > Pages > Source: GitHub Actions**. Index checkpoints persist even if Pages deployment is not enabled.

Optional Actions variables/secrets:

| Name | Meaning |
|---|---|
| `QKI_RPC_URLS` | HTTPS URLs separated by whitespace/commas. Six public candidates are included. Use a secret for API-key URLs; only hostname is published. |
| `LUC_START_BLOCK` | Default 0. Set to a verified deployment block only before the first run; saved cursors are not silently reset. |
| `LUC_TRACE` | Default 0. Set 1 for optional internal-call tracing. |

The browser reads saved JSON without requiring an RPC. Optional live address lookup needs a browser-accessible HTTPS RPC with CORS. No wallet, signing, approvals, private keys, or transactions are used. Very large snapshots may exceed the four-minute read budget; use a stronger RPC or adjust the budget after reviewing Actions limits.

## Local tests and run

```sh
python -m unittest discover -s tests -p 'test_*.py' -v
node tests/frontend.cjs
python scripts/index_power.py --state-dir .luc-data --seconds 60
mkdir -p data
cp .luc-data/public/*.json data/
python -m http.server 8000
```

Test fixtures are synthetic and never deployed as real leaderboard data. The luc-data branch is public because this repository is public. Only its public/ directory is copied to the website; state.json is the internal discovery index. RPC credentials are never saved there.
