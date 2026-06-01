# Northbeam Connection Sanity Check - Design Spec

**Goal:** Make `/northbeam:setup` verify the Northbeam plugin end to end across
the surfaces users depend on: credentials, Spend API, Data Export metadata, and
a tiny outcome export. The check must make it clear that "0 spend rows" does not
mean "0 orders".

**Context:** The current `northbeam_check_connection` only queries
`GET /v1/spend` for yesterday. That correctly validates credentials and the
Spend API, but it missed a Data Export API schema drift. The installed
`northbeam_data_export` tool still sends the older top-level
`date_start` / `date_end` / `attribution_model` payload shape, while the live
API now expects `period_type`, `period_options`, `attribution_options`, and
metric objects.

**Approved direction:** Run a lightweight full-surface diagnostic by default
from setup. Setup is diagnostic, so it can spend a small amount of time and
create one tiny export job. Normal analysis tools should not run this sanity
check before every query.

**Sources checked:**
- Northbeam Data Export guide:
  https://docs.northbeam.io/docs/northbeam-api-data-export-1
- Northbeam Data Export API reference:
  https://northbeam-data-export.readme.io/reference/post_data-export
- Live API validation on 2026-06-01 confirmed `period_options` uses
  `period_starting_at` and `period_ending_at` ISO datetimes.
- Live API validation on 2026-06-01 confirmed non-empty `breakdowns` entries
  require both `key` and `values`.

---

## Current Problems

1. `_check_connection` reports one count named "Records found (yesterday)" from
   spend rows only. This is easy to misread as order volume.
2. `_data_export`, `_run_export_pipeline`, and `_portfolio_health` build Data
   Export payloads with the stale API shape.
3. `NorthbeamClient.poll_export_result` only treats `COMPLETED` as success and
   only reads `download_url`, but the current API returns `SUCCESS` with result
   links under `result`.
4. `NorthbeamClient.create_data_export` callers only look for `export_id`, but
   the current API returns `id`.
5. Local docs and tests encode the older Data Export contract, so they would not
   catch the current breakage.

## Non-Goals

- Do not add an Orders API tool in this tranche.
- Do not change credential storage or print credential values.
- Do not add a separate user-facing sanity tool in this tranche.
- Do not touch `.spec-workflow` or `.spec-template` files.

---

## API Contract

Add a small pure helper module at `server/data_export.py`. This keeps the
current MCP file from growing more and makes request/response normalization easy
to unit test without running the MCP server.

User-facing parameters stay stable:

```python
date_start: str
date_end: str
metrics: list[str]
breakdowns: list[str]
attribution_model: str = "northbeam_custom__va"
attribution_window: str = "7"
```

The builder also accepts a `breakdown_values` mapping produced from
`GET /exports/breakdowns`. Non-empty breakdown requests require this metadata
because the live API rejects `{"key": "Platform (Northbeam)"}` without a
`values` array.

The setup sanity probe uses no breakdowns, so its payload is intentionally
small:

```python
{
    "level": "platform",
    "time_granularity": "DAILY",
    "period_type": "FIXED",
    "period_options": {
        "period_starting_at": "2026-05-31T00:00:00Z",
        "period_ending_at": "2026-05-31T23:59:59Z",
    },
    "breakdowns": [],
    "options": {
        "export_aggregation": "BREAKDOWN",
        "remove_zero_spend": False,
        "aggregate_data": False,
        "include_ids": False,
        "include_kind_and_platform": False,
    },
    "attribution_options": {
        "attribution_models": ["northbeam_custom__va"],
        "accounting_modes": ["accrual"],
        "attribution_windows": ["7"],
    },
    "metrics": [
        {"id": "txns"},
        {"id": "rev"},
    ],
}
```

### Breakdown Mapping

Existing callers pass strings. The builder must support two cases:

- Already-current breakdown keys such as `"Platform (Northbeam)"` become
  `{"key": "Platform (Northbeam)", "values": ["Facebook Ads", "Google Ads"]}`.
- Legacy aliases used in the current code, such as `"platform"`, map to
  `"Platform (Northbeam)"` and then use that key's values from metadata.

Keep the first alias map small and explicit:

```python
{
    "platform": "Platform (Northbeam)",
    "category": "Category (Northbeam)",
    "targeting": "Targeting (Northbeam)",
}
```

If a breakdown string is unknown or has no values in the metadata response,
raise a user-facing `ToolError` explaining that the requested breakdown is not
available. Do not silently drop user-requested breakdowns and do not send
invalid key-only breakdown objects.

Add a helper to convert the metadata response into a lookup:

```python
{
    "Platform (Northbeam)": ["Facebook Ads", "Google Ads"],
    "Category (Northbeam)": ["Other", "Email"],
}
```

`_data_export` and `_portfolio_health` must fetch export options before
building payloads with non-empty breakdowns. They can skip this metadata fetch
when `breakdowns=[]`.

### Metric Mapping

Metric inputs remain metric IDs. Convert every string to `{"id": metric_id}`.
Do not label metrics unless a later request needs custom CSV column names.

### Date Handling

Convert inclusive `YYYY-MM-DD` dates to UTC ISO datetimes:

- start: `YYYY-MM-DDT00:00:00Z`
- end: `YYYY-MM-DDT23:59:59Z`

The Data Export API period is fixed by `period_type: "FIXED"`. Setup's
yesterday sanity check can either use this same fixed-date builder or
`period_type: "YESTERDAY"`. Prefer the shared fixed-date builder to exercise the
same path that normal analysis uses.

---

## Client Response Compatibility

Update `NorthbeamClient` to normalize current and older response shapes:

### Create Export

Accept both:

- current: `{"id": "exp-test-123"}`
- older/local tests: `{"export_id": "exp-test-123"}`

The client method can keep returning raw JSON, but the pipeline helper should
extract with:

```python
export_id = create_result.get("id") or create_result.get("export_id")
```

### Poll Export

Treat both `SUCCESS` and `COMPLETED` as terminal success. Treat `FAILED` as
terminal failure. Continue polling for pending statuses.

Accept download URLs from:

- current: `result`, which is a list of URLs
- older/local tests: `download_url`, a single URL string

Normalize in a helper:

```python
def _extract_download_url(poll_result: dict[str, Any]) -> str | None:
    if poll_result.get("download_url"):
        return poll_result["download_url"]
    result = poll_result.get("result")
    if isinstance(result, list) and result:
        return result[0]
    if isinstance(result, str):
        return result
    return None
```

---

## Expanded Setup Sanity Flow

Replace `_check_connection` with a fuller diagnostic that still returns a
human-readable string for the MCP tool.

Flow:

1. Load credentials. If missing or auth fails on any required surface, return
   `Status: Not connected` through `ToolError`.
2. Query Spend API for yesterday with `page_size=1000`.
3. Query Data Export metadata via `list_export_options`.
4. Run a tiny Data Export for yesterday:
   - metrics: `txns`, `rev`
   - breakdowns: none or `Platform (Northbeam)`. Prefer none for the cheapest
     aggregate outcome check.
   - attribution model/window: `northbeam_custom__va`, `7`
   - accounting mode: `accrual`
5. Aggregate downloaded rows and report totals for transactions and revenue.

Recommended output:

```text
Status: Connected
Environment: prod
Spend API: OK - 0 spend rows for 2026-05-31
Data Export metadata: OK - metrics, breakdowns, attribution models available
Data Export API: OK - transactions=80.39, revenue=18372.97 for 2026-05-31
Platforms visible from spend: none (no spend rows for 2026-05-31)
Note: spend rows are ad spend records, not orders or transactions.
```

If spend succeeds but Data Export fails:

```text
Status: Partially connected
Environment: prod
Spend API: OK - 0 spend rows for 2026-05-31
Data Export metadata: OK
Data Export API: Failed - Validation error: metrics.0: bad
Note: credentials work, but outcome metrics are not usable until Data Export is fixed.
```

If Data Export has outcome rows while spend rows are zero, include a note:

```text
Outcome data exists even though spend rows are zero; this usually means the
Spend API has no ad spend records for that date, not that orders are missing.
```

Keep values rounded for display, but keep raw numeric totals in structured
helpers where tests can assert exact values.

---

## Error Handling

- Auth or missing credentials: raise `ToolError` with setup-oriented
  `Status: Not connected` wording.
- Spend API failure after credentials load: raise `ToolError`; setup cannot
  claim connected if the credential validation surface fails.
- Metadata failure: return `Status: Partially connected`.
- Tiny Data Export create/poll/download failure: return
  `Status: Partially connected`.
- Export timeout: return partial status with the timeout message. Do not loop
  forever.
- Validation errors should include the sanitized API validation detail. Do not
  include request headers or credential values.

---

## Impacted Code

### `server/northbeam_mcp.py`

- Use the new Data Export payload builder.
- Use export ID and download URL extraction helpers from `server/data_export.py`.
- Update `_data_export` to use current payload shape.
- Update `_run_export_pipeline` to use the same builder and response extraction.
- Update `_portfolio_health` to request `rev` and `roas` with the current
  export contract and current breakdown key.
- Expand `_check_connection` into the full-surface sanity flow.

### `server/client.py`

- Update `poll_export_result` to treat `SUCCESS` as success.
- Extract status normalization into a tiny private helper for unit tests.
- Keep `download_export_csv` credential-safe by using a standalone HTTP client
  without Northbeam auth headers.

### `server/data_export.py`

- Add pure helpers for Data Export request payload building.
- Add pure helpers for export ID extraction and download URL extraction.
- Add pure helpers for CSV column-name mapping if needed by aggregation.

### `skills/setup/SKILL.md`

- Update Step 1 language to say setup validates Spend API and Data Export API.
- Say a zero spend row count does not mean zero orders.
- Report partial connection as something the user should bring back for a code
  or plugin update, not credential re-entry.

### `skills/analyze/SKILL.md`

- Keep the existing tool routing rule, but make metric examples use current IDs:
  `rev`, `txns`, `roas`, `cac`.
- Clarify that order/transaction questions route to Data Export, not Spend API.

### `docs/northbeam-data-export-api.md`

- Replace the stale request body with the current API contract.
- Document current create and poll response shapes.
- Mention the compatibility parser accepts older test fixtures.

### Tests

Update existing tests instead of adding a parallel suite with duplicate intent.

Required coverage:

1. Data Export builder emits:
   - `period_type: FIXED`
   - ISO `period_starting_at` and `period_ending_at`
   - `attribution_options`
   - metric objects
   - breakdown objects with metadata-derived `values`
2. `_data_export` accepts current create response `id`.
3. `poll_export_result` returns on `SUCCESS`.
4. pipeline extracts a URL from current `result: ["https://storage.example.com/export.csv"]`.
5. `_check_connection` reports connected when spend rows are zero but the tiny
   outcome export returns revenue/transactions.
6. `_check_connection` reports partially connected when Data Export validation
   fails after spend succeeds.
7. `_portfolio_health` uses the same current Data Export builder.
8. Regression test for the old failure: posting top-level `date_start` and
   string metrics must no longer be the expected request shape.
9. Regression test for the new live finding: non-empty Data Export breakdowns
   include a `values` array from metadata.

---

## Acceptance Criteria

- `/northbeam:setup` output distinguishes Spend API rows from orders.
- The installed plugin can run the setup check from a fresh Codex session and
  report both spend and outcome surfaces.
- `northbeam_data_export` works against current Northbeam Data Export API.
- `northbeam_portfolio_health` no longer returns an outcome schema error.
- Docs and tests no longer describe the stale Data Export payload.
- No credential values are printed in logs, test output, docs, or user-facing
  messages.

## Implementation Order

1. Write tests for current Data Export payload and response parsing.
2. Add payload and response normalization helpers.
3. Update `_data_export` and `_run_export_pipeline`.
4. Update `poll_export_result`.
5. Expand `_check_connection`.
6. Update portfolio health.
7. Update setup/analyze skills and Data Export docs.
8. Run unit tests and one live setup check without printing secrets.
