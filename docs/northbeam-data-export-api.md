# Northbeam Data Export API Reference

Quick reference for the Data Export API endpoints used by the `northbeam_data_export` and `northbeam_list_options` MCP tools.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/exports/data-export` | Create async data export, returns `{id}` |
| GET | `/v1/exports/data-export/result/{id}` | Poll export status: PENDING -> PROCESSING -> SUCCESS \| FAILED |
| GET | `/v1/exports/breakdowns` | List available breakdown dimensions |
| GET | `/v1/exports/metrics` | List available metrics |
| GET | `/v1/exports/attribution-models` | List available attribution models |

## Authentication

Same as Spend API — `Authorization` header (API key) + `Data-Client-ID` header (client ID).

## Create Export Request Body

```json
{
  "level": "platform",
  "time_granularity": "DAILY",
  "period_type": "FIXED",
  "period_options": {
    "period_starting_at": "2026-05-31T00:00:00Z",
    "period_ending_at": "2026-05-31T23:59:59Z"
  },
  "breakdowns": [
    {
      "key": "Platform (Northbeam)",
      "values": ["Facebook Ads", "Google Ads"]
    }
  ],
  "options": {
    "export_aggregation": "BREAKDOWN",
    "remove_zero_spend": false,
    "aggregate_data": false,
    "include_ids": false,
    "include_kind_and_platform": false
  },
  "attribution_options": {
    "attribution_models": ["northbeam_custom"],
    "accounting_modes": ["accrual"],
    "attribution_windows": ["1"]
  },
  "metrics": [
    {"id": "txns"},
    {"id": "revAttributed"}
  ]
}
```

## Create Export Response

```json
{"id": "exp-test-123"}
```

## Poll Success Response

```json
{
  "data_export_id": "exp-test-123",
  "status": "SUCCESS",
  "result": ["https://storage.example.com/export.csv"]
}
```

The result URL is pre-signed and returns CSV data with breakdown columns plus
metric columns. The URL does not require authentication headers.

## Aggregation Behavior

`northbeam_data_export` sums additive metrics such as `revAttributed`, `txns`,
`spend`, and `impressions` (CSV column `imprs`) when multiple CSV rows share the
same requested breakdown keys. Ratio metrics such as `roas`, `cac`, and `ecpc`
are not summed — they are returned only for single-row groups; multi-row groups
return `null`.

**Revenue metric:** Use `revAttributed` (the UI "Revenue"/ROAS basis — windowed,
model-dependent, accrual). `rev` is a cash/total-basis number that reads empty in
the accrual windowed view; do not use it for UI parity.

**Accounting-mode fan-out:** When a revenue metric is requested, the API returns
one row per accounting mode — an "Accrual performance" row at the requested
window plus a "Cash snapshot" row at lifetime — with spend repeated on each. The
plugin filters to the requested accounting mode (default `accrual`) before
aggregating so spend/revenue are not double-counted. Attribution windows apply
only to accrual mode; cash mode is always lifetime.

Compatibility note: the plugin also accepts older internal test fixtures that
use `export_id`, `COMPLETED`, and `download_url`, but new requests are sent
using the current Data Export API shape above.

## Export Statuses

- **PENDING** — export queued
- **PROCESSING** — export running
- **SUCCESS** — ready to download via the result URL
- **FAILED** — export failed, check `error` field for details
