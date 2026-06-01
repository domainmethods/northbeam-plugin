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
    "attribution_models": ["northbeam_custom__va"],
    "accounting_modes": ["accrual"],
    "attribution_windows": ["7"]
  },
  "metrics": [
    {"id": "txns"},
    {"id": "rev"}
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

`northbeam_data_export` sums additive metrics such as `rev`, `txns`, and
`conversions` when multiple CSV rows share the same requested breakdown keys.
Known ratio/efficiency metrics such as `roas` and `cac` are not summed. They are
returned only for single-row groups; multi-row groups return `null` for those
metrics rather than reporting an invalid total.

Compatibility note: the plugin also accepts older internal test fixtures that
use `export_id`, `COMPLETED`, and `download_url`, but new requests are sent
using the current Data Export API shape above.

## Export Statuses

- **PENDING** — export queued
- **PROCESSING** — export running
- **SUCCESS** — ready to download via the result URL
- **FAILED** — export failed, check `error` field for details
