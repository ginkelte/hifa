# Vendored assets

Copied as is from the upstream packages. **No build step, no network at runtime** (SPEC-070
§2.4, REQ-N005). Updating any file here requires updating this table and re-running the budget
test (`tests/e2e/web/test_static_budget.py`).

| File | Version | License | gzip -9 | sha256 |
|---|---|---|---|---|
| `htmx.min.js` | 2.0.4 | BSD-2-Clause | 16 326 B | `e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447` |
| `alpine.csp.min.js` | 3.14.8 (`@alpinejs/csp`) | MIT | 16 439 B | `c98f46291087f0f93c9cdefb682d120fe1d2b3dcb649de83cfc1ad5b1fac58e4` |
| `sortable.min.js` | 1.15.2 | MIT | 14 757 B | `ca68430703c4f5960e90735867c6e94d29b5a3de37107d8100e5a301007e9e6e` |

## Why the CSP build of Alpine

SPEC-070 R14 sets a CSP without `unsafe-eval`. The regular `alpinejs` CDN build uses `eval()` to
compile expressions; `@alpinejs/csp` does not (verified: `grep -c 'eval(' = 0`). Any upgrade must
keep that property or the CSP breaks silently in the browser while every server-side test stays
green.

## Note on `sortable.min.js`

SPEC-070 §2.4 budgets 10 KB gzip for Sortable 1.15.2; the real file is 14 757 B. The total budget
(≤ 75 KB) still holds, so the per-file estimate is the inaccurate number, not the file.
