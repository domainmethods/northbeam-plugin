# Northbeam UI-Parity: Re-source Spend, Efficiency, Revenue & ROAS from Data Export

**Date:** 2026-06-01
**Status:** Design — pending user review
**Supersedes assumptions in:** `docs/northbeam-data-export-api.md`, `references/spend-api-schema.md`, `skills/analyze/SKILL.md`, the 2026-04-25 data-export spec.

## 1. Problem

The plugin sources ad spend from `GET /v1/spend` (the "Spend API"). That endpoint is **upload-only** — it returns only spend a customer pushed in for non-integrated channels, and returns empty (`total_count: 0`) for natively-integrated accounts (Facebook/Google/TikTok). Every spend-dependent feature (spend totals, CPC/CPM/CTR, budget pacing, anomaly detection, diminishing-returns, budget allocation, portfolio health) therefore silently returns zeros for a normal account.

Separately, the outcome side uses the wrong revenue metric (`rev`) and the wrong attribution defaults, so revenue/ROAS don't match the Northbeam web UI. The aggregator double-counts rows when the API returns multiple accounting modes. And the test suite is green against mock data shaped like the (wrong) Spend API, which is why none of this surfaced.

**Verified ground truth (May 2026, this account):**
- Data Export `spend` by Platform = Facebook $407,056.74 + TikTok $70,866.64 + Google $67,753.45 = **$545,676.83**, matching the dashboard to the penny. `GET /v1/spend` returned 0 for the same period.
- Facebook revenue under the account's UI default (Clicks only / 1-day window / accrual) = **$89,097.31**, ROAS **0.22** (= 89,097.31 / 407,056.74).

## 2. Success Criteria

**The skill/MCP/CLI must produce the same analysis the user sees in the Northbeam web UI.** Concretely:
1. Spend by platform matches the dashboard to the penny.
2. Impressions, clicks, CPC, CPM, CTR match the dashboard per platform and blended.
3. Revenue and ROAS match the dashboard under the account's default attribution (Clicks only / 1-day / accrual), and remain correct when the user overrides model/window/accounting mode.
4. No double-counting when the API returns multiple accounting modes or windows.
5. Tests exercise the **real** Data Export CSV column names and reconcile against the verified May 2026 figures.

## 3. Architecture (Approach B)

Keep the upload-only path available but relabel it, and make Data Export the source of truth for spend, efficiency, and outcomes.

- **New tool `northbeam_spend`** — Data Export-backed. Returns spend + efficiency (impressions, clicks, CPC, CPM, CTR) by platform/campaign, in the same row shape the old spend tool returned, so all downstream `analyze` capabilities (pacing, anomalies, fatigue, allocation) keep working unchanged.
- **Relabel `northbeam_list_spend` → `northbeam_list_uploaded_spend`** — explicitly the upload API for non-integrated channels. Unchanged behavior; new name + docstring make its scope honest.
- **`northbeam_data_export`** — outcomes (revenue, ROAS, CAC, transactions); fixed to use `revAttributed` and correct attribution defaults, with the fan-out fix.
- **`northbeam_portfolio_health`** — sources spend/efficiency from `northbeam_spend`'s Data Export path (not the upload API) and outcomes from the fixed export path.
- **`northbeam_check_connection`** — outcome probe uses `revAttributed`; messaging clarifies upload-API scope.
- **`northbeam_list_options`** — unchanged.

### Data flow

```
spend / efficiency:   Data Export (spend, cpm, ctr, ecpc, imprs)  ──► northbeam_spend ──► analyze capabilities
revenue / ROAS:       Data Export (revAttributed, roas, txns, cac) ──► northbeam_data_export
uploaded spend only:  GET /v1/spend ──► northbeam_list_uploaded_spend (rare; non-integrated channels)
```

## 4. Component Changes

### 4.1 `server/data_export.py`

- **Metric column aliases.** Extend `METRIC_COLUMN_ALIASES` so metric ids map to their CSV column names:
  - `impressions → imprs`
  - (keep `txns → transactions`)
  - Clicks have no clean generic CSV column; they are **derived**, not aliased (see 4.3).
- **Attribution defaults.** Change `build_data_export_payload` defaults to match the UI:
  - `attribution_model: "northbeam_custom"` (Clicks only) — was `northbeam_custom__va`.
  - `attribution_window: "1"` — was `"7"`.
  - `accounting_mode: "accrual"` (unchanged).
  - All remain caller-overridable.
- **Accounting/window rule.** Encode that attribution windows apply only to accrual. When `accounting_mode == "cash"`, the window is meaningless (cash = lifetime); the builder should not imply otherwise (document it; optionally normalize the window to a sentinel for cash).
- **Granularity.** Keep `time_granularity` a parameter (default `DAILY`). Period totals are produced by client-side aggregation across days (4.3), which already works. Coarser server-side granularity (WEEKLY/MONTHLY) is a **deferred optimization** — it must be re-verified against a healthy export queue before adoption (see §7) and is out of scope here.

### 4.2 `server/client.py`

- **Configurable, generous poll timeout.** `EXPORT_POLL_TIMEOUT` (currently 60s) becomes configurable via env (`NORTHBEAM_EXPORT_TIMEOUT`, default raised to 180s). Attribution-heavy exports legitimately take longer than 60s.
- **Clear timeout message.** On timeout, report the last observed status and that the export is still queued/processing on Northbeam's side (e.g. "Export still PENDING after 180s — Northbeam's export queue may be busy; retry shortly"), distinguishing PENDING (queued) from PROCESSING (running). This is a real, observed failure mode (queue backlog), not an auth/config error, and must not be reported as one.

### 4.3 `server/northbeam_mcp.py`

- **Fan-out fix in `_aggregate_export_rows`.** Include the CSV's `accounting_mode` and `attribution_window` in the group key (or filter raw rows to the requested mode+window before aggregating) so accrual and cash rows never collapse into one group. Additive metrics (`spend`, `revAttributed`, `txns`) are then summed within a single (breakdown, mode, window) partition only. Tools select the partition matching the requested mode/window. This removes the double-count at the source.
- **Efficiency derivation.** Replace the Spend-API column assumptions in `_enrich_spend_rows`, `_compute_blended_metrics`, and `_aggregate_spend_by_platform`:
  - Impressions from the `imprs` column.
  - Clicks derived per row from `ecpc` (`clicks = spend / ecpc` when `ecpc > 0`; equivalently `ctr × imprs`). There is no reliable generic `clicks` column, and platform-namespaced click columns are definitionally inconsistent — do **not** sum them.
  - Per-platform CPC/CPM/CTR taken from Northbeam's own `cpm`/`ctr`/`ecpc` columns where present (match the UI by construction); blended efficiency computed as sum(spend)/sum(imprs)/sum(derived-clicks), then divided — never averaged.
  - Platform name from `breakdown_platform_northbeam` (not `platform_name`).
- **New `northbeam_spend` tool.** Runs a Data Export (`spend`, `cpm`, `ctr`, `ecpc`, `imprs`) broken down by platform (and optionally campaign), returns rows in the legacy spend shape (`platform_name`/`campaign_name`, `spend`, `impressions`, `clicks`, `cpc`, `cpm`, `ctr`) so the `analyze` capabilities consume it unchanged. Spend is attribution-independent, so this export is cheap (no attribution compute) and not subject to the fan-out beyond the single-partition selection.
- **Relabel `northbeam_list_spend → northbeam_list_uploaded_spend`.** Same implementation; new name + docstring stating it returns only uploaded spend for non-integrated channels and is empty for natively-integrated accounts.
- **`_portfolio_health`.** Source spend/efficiency from the Data Export path (via the `northbeam_spend` internals), not `list_spend`. Source outcomes via the fixed export (`revAttributed`, `roas`). Apply the fan-out fix. Keep the concurrent spend+outcome execution.
- **`_run_outcome_sanity_probe` (connection check).** Request `revAttributed` (+ `txns`) instead of `rev`, with the single-partition selection so the sanity numbers aren't doubled.
- **Outcome metric semantics.** `revAttributed` is additive; `roas`/`cac` are ratios (left null for multi-row groups, or recomputed from additive inputs). Update `ADDITIVE_EXPORT_METRICS` accordingly (add `revAttributed`; `rev` may stay for the rare cash/lifetime use but is no longer the default revenue source).

### 4.4 Skills

- **`skills/analyze/SKILL.md`:**
  - Tool-routing table: spend / impressions / clicks / CPC / CPM / CTR / budget pacing → **`northbeam_spend`** (not `northbeam_list_spend`). Revenue / ROAS / CAC / orders → `northbeam_data_export`. Uploaded (non-integrated) spend → `northbeam_list_uploaded_spend`.
  - "Data Export Defaults" section: Clicks only (`northbeam_custom`) / 1-day window / accrual — matching the UI default — and note these are account-configurable and overridable.
  - "Common outcome metric IDs": revenue = `revAttributed` (the UI "Revenue"/ROAS column); note `rev` is cash/total basis and reads empty in the accrual windowed view. ROAS = revAttributed / spend.
  - Note the accrual-only window rule and that cash = lifetime.
- **`skills/setup/SKILL.md`:** Reframe the connection summary so a zero uploaded-spend count is expected for integrated accounts and is **not** a data gap; real spend comes from Data Export.

### 4.5 Docs & references

- **`references/spend-api-schema.md`:** Retitle/annotate `GET /v1/spend` as the **upload API for non-integrated channels**, not the source of all ad spend. Note the derived CPC/CPM/CTR there apply only to uploaded rows.
- **`docs/northbeam-data-export-api.md`:** Update the example to `revAttributed` + `northbeam_custom` + window `"1"`; document the accounting-mode/window rule, the per-(mode,window) row fan-out and how the plugin partitions it, and the poll-timeout behavior.
- **`README.md`:** Correct the top description (spend/efficiency come from Data Export; Spend API is upload-only) and the setup-check note (lines ~4, ~186–188).

### 4.6 Tests

The current mocks use Spend-API column names (`platform_name`, `clicks`, `impressions`) and assert `rev` / `northbeam_custom__va`. Rewrite them to reflect reality:
- **Fixtures** use real Data Export CSV columns: `breakdown_platform_northbeam`, `imprs`, `ecpc`, `cpm`, `ctr`, `spend`, `revAttributed`, `roas`, `txns`, `accounting_mode`, `attribution_window`.
- **Fan-out test:** a CSV with both an `Accrual performance` row and a `Cash snapshot` row for the same platform must NOT double spend or revenue — the accrual partition is selected.
- **Reconciliation test:** with fixtures mirroring May 2026, blended/by-platform spend totals the verified figures, and ROAS = revAttributed/spend ≈ 0.22 for Facebook.
- **Derivation tests:** impressions read from `imprs`; clicks derived from `ecpc`; CPC/CPM/CTR match.
- **Relabel test:** `northbeam_list_uploaded_spend` exists and is documented as upload-only; new `northbeam_spend` returns the legacy spend shape from Data Export.
- **Defaults tests:** export bodies default to `northbeam_custom` / `"1"` / `accrual`.
- **Timeout test:** a never-completing export yields the clear "still PENDING/PROCESSING" message, not an auth error.

## 5. Error Handling

| Condition | Behavior |
|-----------|----------|
| Export stuck PENDING/PROCESSING past timeout | Clear message naming the last status + "queue may be busy, retry"; not an auth error. Timeout configurable. |
| Auth/config failure | Existing `AUTH_ERROR_MSG` → "Run /northbeam:setup". |
| Empty uploaded-spend (integrated account) | Expected; `northbeam_list_uploaded_spend` and the connection check explain this is not a data gap. |
| Export returns multiple (mode,window) partitions | Plugin selects the requested partition; never sums across them. |
| `ecpc`/`imprs` missing or zero | CPC/CPM/CTR null (N/A), consistent with current divide-by-zero handling. |

## 6. Out of Scope (YAGNI)

- Server-side coarser granularity (WEEKLY/MONTHLY) — deferred until verifiable against a healthy queue.
- Attribution-model comparison UX changes beyond correcting defaults/metrics.
- Subscription/LTV/first-time revenue variants — the many `rev*` metrics exist but are not needed for UI parity on the headline numbers.
- Caching/persistence of export results.

## 7. Risks & Notes

- **Export queue degradation is real and transient.** During investigation, even a trivial spend-only export sat at `PENDING` and never reached `PROCESSING` for 80s+, while the same request returned in seconds earlier. The configurable, generous timeout + clear messaging is the mitigation; we cannot make Northbeam's queue faster.
- **Account-configurable defaults.** "Clicks only / 1-day / accrual" matches *this* account's current UI default. Because the UI default is account-configurable, the values are overridable and the skill should say so.
- **Granularity optimization needs re-verification.** MONTHLY/WEEKLY exports could not be confirmed (queue was degraded during design); do not adopt without testing against a healthy queue.
