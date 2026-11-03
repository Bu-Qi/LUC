# QKI Power Dashboard

Static GitHub Pages dashboard for read-only QKI contract power queries.

## GitHub Pages

This repository deploys the static site from `/` using `.github/workflows/pages.yml`.

After the first successful Actions run, enable Pages with **Settings → Pages → Build and deployment → GitHub Actions** if it is not enabled automatically.

## Important data scope

GitHub Pages is static hosting: it does not run the Python indexer or SQLite backend from the full dashboard package. The page reads `totalPower()` and `power(address)` from the configured QKI RPC. An address leaderboard can only rank addresses supplied by the visitor (paste one address per line or comma-separated). It is **not a complete network-wide address leaderboard** unless a separately verified full address index is supplied.

Contract: `0x5c8426a122Bf473a068e6D9Dd5d2D624CbF958f9`  
Chain ID: `20181205`  
Power decimals: `3`

Default RPC candidates: `https://rpc1.qkirpc.org`, `https://rpc2.qkirpc.org`, `https://rpc3.qkirpc.org` (availability must be verified by the browser).

## Local preview

Open `index.html` in a browser. For GitHub Pages, use the published URL under Settings → Pages.
