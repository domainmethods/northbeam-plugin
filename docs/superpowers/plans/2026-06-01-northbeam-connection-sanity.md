# Northbeam Connection Sanity Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Northbeam setup verify credentials, Spend API, Data Export metadata, and a tiny outcome export while fixing the current Data Export API contract.

**Architecture:** Add a pure `server/data_export.py` helper module for request/response normalization, then route `_data_export`, `_run_export_pipeline`, `_portfolio_health`, and `_check_connection` through those helpers. Keep public MCP signatures stable while translating them to the live Northbeam Data Export schema. Setup becomes a full-surface diagnostic; normal analysis calls do not run setup checks first.

**Tech Stack:** Python 3.11, FastMCP, httpx, pytest, pytest-asyncio, respx, Codex plugin cachebuster tools.

---

## File Structure

- Create: `server/data_export.py`
  - Pure helpers for Data Export payloads, breakdown metadata, response extraction, and CSV column aliases.
- Create: `tests/test_data_export.py`
  - Unit tests for pure helpers, no HTTP.
- Modify: `server/client.py`
  - Treat Data Export `SUCCESS` as terminal success in addition to `COMPLETED`.
- Modify: `server/northbeam_mcp.py`
  - Use the helper module, update aggregation, update export pipeline, expand `_check_connection`.
- Modify: `tests/test_client.py`
  - Add poll-status compatibility tests.
- Modify: `tests/test_tools.py`
  - Update Data Export flow tests for current request/response shapes and setup sanity output.
- Modify: `tests/test_portfolio.py`
  - Verify portfolio health uses current Data Export payloads and metadata-derived breakdown values.
- Modify: `tests/conftest.py`
  - Update export fixtures to include current response shape and realistic metadata where shared fixtures make tests simpler.
- Modify: `skills/setup/SKILL.md`
  - Explain the full-surface setup check and zero-spend-row wording.
- Modify: `skills/analyze/SKILL.md`
  - Clarify outcome metric IDs and order/transaction routing.
- Modify: `docs/northbeam-data-export-api.md`
  - Replace stale request/response examples with the current API contract.
- Modify: `.codex-plugin/plugin.json`
  - Cachebuster update through the plugin-creator helper during final plugin reinstall.

---

### Task 1: Add Pure Data Export Helpers

**Files:**
- Create: `server/data_export.py`
- Create: `tests/test_data_export.py`

- [ ] **Step 1: Write failing helper tests**

Create `tests/test_data_export.py`:

```python
import pytest

from server.data_export import (
    build_breakdown_value_lookup,
    build_data_export_payload,
    breakdown_column_candidates,
    extract_download_url,
    extract_export_id,
    metric_column_candidates,
)


def test_build_data_export_payload_without_breakdowns():
    payload = build_data_export_payload(
        date_start="2026-05-31",
        date_end="2026-05-31",
        metrics=["txns", "rev"],
        breakdowns=[],
        attribution_model="northbeam_custom__va",
        attribution_window="7",
    )

    assert payload["level"] == "platform"
    assert payload["time_granularity"] == "DAILY"
    assert payload["period_type"] == "FIXED"
    assert payload["period_options"] == {
        "period_starting_at": "2026-05-31T00:00:00Z",
        "period_ending_at": "2026-05-31T23:59:59Z",
    }
    assert payload["breakdowns"] == []
    assert payload["attribution_options"] == {
        "attribution_models": ["northbeam_custom__va"],
        "accounting_modes": ["accrual"],
        "attribution_windows": ["7"],
    }
    assert payload["metrics"] == [{"id": "txns"}, {"id": "rev"}]


def test_build_data_export_payload_maps_breakdown_alias_with_values():
    payload = build_data_export_payload(
        date_start="2026-05-31",
        date_end="2026-05-31",
        metrics=["rev"],
        breakdowns=["platform"],
        breakdown_values={
            "Platform (Northbeam)": ["Facebook Ads", "Google Ads"],
        },
    )

    assert payload["breakdowns"] == [
        {
            "key": "Platform (Northbeam)",
            "values": ["Facebook Ads", "Google Ads"],
        }
    ]


def test_build_data_export_payload_rejects_breakdown_without_values():
    with pytest.raises(ValueError, match="Breakdown values required"):
        build_data_export_payload(
            date_start="2026-05-31",
            date_end="2026-05-31",
            metrics=["rev"],
            breakdowns=["platform"],
        )


def test_build_breakdown_value_lookup_handles_current_metadata_shape():
    lookup = build_breakdown_value_lookup({
        "breakdowns": {
            "breakdowns": [
                {"key": "Platform (Northbeam)", "values": ["Facebook Ads"]},
                {"key": "Category (Northbeam)", "values": ["Email"]},
            ]
        }
    })

    assert lookup == {
        "Platform (Northbeam)": ["Facebook Ads"],
        "Category (Northbeam)": ["Email"],
    }


def test_extract_export_id_accepts_current_and_legacy_shapes():
    assert extract_export_id({"id": "new-id"}) == "new-id"
    assert extract_export_id({"export_id": "old-id"}) == "old-id"
    assert extract_export_id({}) is None


def test_extract_download_url_accepts_current_and_legacy_shapes():
    assert extract_download_url({"result": ["https://storage.example.com/new.csv"]}) == (
        "https://storage.example.com/new.csv"
    )
    assert extract_download_url({"result": "https://storage.example.com/one.csv"}) == (
        "https://storage.example.com/one.csv"
    )
    assert extract_download_url({"download_url": "https://storage.example.com/old.csv"}) == (
        "https://storage.example.com/old.csv"
    )
    assert extract_download_url({}) is None


def test_metric_and_breakdown_column_candidates_cover_live_csv_names():
    assert metric_column_candidates("txns") == ["txns", "transactions"]
    assert metric_column_candidates("rev") == ["rev"]
    assert breakdown_column_candidates("platform") == [
        "platform",
        "Platform (Northbeam)",
        "breakdown_platform_northbeam",
    ]
```

- [ ] **Step 2: Run helper tests to verify they fail**

Run:

```bash
uv run pytest tests/test_data_export.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'server.data_export'`.

- [ ] **Step 3: Implement `server/data_export.py`**

Create `server/data_export.py`:

```python
from __future__ import annotations

from datetime import datetime
from typing import Any

BREAKDOWN_ALIASES = {
    "platform": "Platform (Northbeam)",
    "category": "Category (Northbeam)",
    "targeting": "Targeting (Northbeam)",
}

BREAKDOWN_COLUMN_ALIASES = {
    "Platform (Northbeam)": ["breakdown_platform_northbeam"],
    "Category (Northbeam)": ["breakdown_category_northbeam"],
    "Targeting (Northbeam)": ["breakdown_targeting_northbeam"],
}

METRIC_COLUMN_ALIASES = {
    "txns": ["transactions"],
}

DEFAULT_EXPORT_OPTIONS = {
    "export_aggregation": "BREAKDOWN",
    "remove_zero_spend": False,
    "aggregate_data": False,
    "include_ids": False,
    "include_kind_and_platform": False,
}


def normalize_breakdown_key(breakdown: str) -> str:
    return BREAKDOWN_ALIASES.get(breakdown.lower(), breakdown)


def _date_to_day_bounds(date_value: str) -> tuple[str, str]:
    parsed = datetime.strptime(date_value, "%Y-%m-%d").date()
    date_text = parsed.isoformat()
    return f"{date_text}T00:00:00Z", f"{date_text}T23:59:59Z"


def build_breakdown_value_lookup(options: dict[str, Any]) -> dict[str, list[str]]:
    raw_breakdowns = options.get("breakdowns", options)
    if isinstance(raw_breakdowns, dict):
        items = raw_breakdowns.get("breakdowns") or raw_breakdowns.get("data") or []
    else:
        items = []

    lookup: dict[str, list[str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = item.get("key") or item.get("name") or item.get("id")
        values = item.get("values") or []
        if key:
            lookup[str(key)] = list(values)
    return lookup


def build_data_export_payload(
    *,
    date_start: str,
    date_end: str,
    metrics: list[str],
    breakdowns: list[str],
    attribution_model: str = "northbeam_custom__va",
    attribution_window: str = "7",
    breakdown_values: dict[str, list[str]] | None = None,
    level: str = "platform",
    time_granularity: str = "DAILY",
    accounting_mode: str = "accrual",
) -> dict[str, Any]:
    period_starting_at, _ = _date_to_day_bounds(date_start)
    _, period_ending_at = _date_to_day_bounds(date_end)

    normalized_breakdowns: list[dict[str, Any]] = []
    for breakdown in breakdowns:
        key = normalize_breakdown_key(breakdown)
        values = (breakdown_values or {}).get(key)
        if not values:
            raise ValueError(f"Breakdown values required for {key}")
        normalized_breakdowns.append({"key": key, "values": values})

    return {
        "level": level,
        "time_granularity": time_granularity,
        "period_type": "FIXED",
        "period_options": {
            "period_starting_at": period_starting_at,
            "period_ending_at": period_ending_at,
        },
        "breakdowns": normalized_breakdowns,
        "options": dict(DEFAULT_EXPORT_OPTIONS),
        "attribution_options": {
            "attribution_models": [attribution_model],
            "accounting_modes": [accounting_mode],
            "attribution_windows": [attribution_window],
        },
        "metrics": [{"id": metric} for metric in metrics],
    }


def extract_export_id(create_result: dict[str, Any]) -> str | None:
    export_id = create_result.get("id") or create_result.get("export_id")
    return str(export_id) if export_id else None


def extract_download_url(poll_result: dict[str, Any]) -> str | None:
    if poll_result.get("download_url"):
        return str(poll_result["download_url"])

    result = poll_result.get("result")
    if isinstance(result, list) and result:
        return str(result[0])
    if isinstance(result, str):
        return result
    return None


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def metric_column_candidates(metric_id: str) -> list[str]:
    return _dedupe([metric_id, *METRIC_COLUMN_ALIASES.get(metric_id, [])])


def breakdown_column_candidates(breakdown: str) -> list[str]:
    key = normalize_breakdown_key(breakdown)
    return _dedupe([
        breakdown,
        key,
        *BREAKDOWN_COLUMN_ALIASES.get(key, []),
    ])
```

- [ ] **Step 4: Run helper tests to verify they pass**

Run:

```bash
uv run pytest tests/test_data_export.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit helper work**

```bash
git add server/data_export.py tests/test_data_export.py
git commit -m "feat: add Northbeam data export helpers"
```

---

### Task 2: Update Data Export Polling Compatibility

**Files:**
- Modify: `server/client.py`
- Modify: `tests/test_client.py`

- [ ] **Step 1: Write failing poll compatibility test**

Add this test near `test_poll_export_result_returns_on_completed` in `tests/test_client.py`:

```python
async def test_poll_export_result_returns_on_success(config):
    with respx.mock:
        route = respx.get("https://api.northbeam.io/v1/exports/data-export/result/exp-success")
        route.side_effect = [
            httpx.Response(200, json={"status": "PENDING"}),
            httpx.Response(200, json={
                "status": "SUCCESS",
                "result": ["https://storage.example.com/export.csv"],
            }),
        ]

        async with NorthbeamClient(config) as client:
            result = await client.poll_export_result("exp-success")

    assert result["status"] == "SUCCESS"
    assert result["result"] == ["https://storage.example.com/export.csv"]
    assert route.call_count == 2
```

- [ ] **Step 2: Run the new client test to verify it fails**

Run:

```bash
uv run pytest tests/test_client.py::test_poll_export_result_returns_on_success -v
```

Expected: FAIL by timing out or continuing to poll because `SUCCESS` is not treated as terminal success.

- [ ] **Step 3: Implement status normalization**

Modify `server/client.py` near the constants:

```python
EXPORT_SUCCESS_STATUSES = {"COMPLETED", "SUCCESS"}
EXPORT_FAILURE_STATUSES = {"FAILED", "FAILURE"}
```

Add a private helper near `_parse_body`:

```python
def _normalize_export_status(status: Any) -> str:
    return str(status or "").upper()
```

Update `poll_export_result`:

```python
status = _normalize_export_status(result.get("status"))
if status in EXPORT_SUCCESS_STATUSES:
    return result
if status in EXPORT_FAILURE_STATUSES:
    raise NorthbeamAPIError(
        f"Export {export_id} failed: "
        f"{result.get('error', 'unknown')}"
    )
```

- [ ] **Step 4: Run client tests**

Run:

```bash
uv run pytest tests/test_client.py::test_poll_export_result_returns_on_completed tests/test_client.py::test_poll_export_result_returns_on_success tests/test_client.py::test_poll_export_result_raises_on_failed -v
```

Expected: PASS.

- [ ] **Step 5: Commit polling compatibility**

```bash
git add server/client.py tests/test_client.py
git commit -m "fix: support current Northbeam export polling status"
```

---

### Task 3: Update Data Export Pipeline and Aggregation

**Files:**
- Modify: `server/northbeam_mcp.py`
- Modify: `tests/test_tools.py`
- Modify: `tests/conftest.py`

Implementation note: aggregate additive metrics such as `rev`, `txns`, and
`conversions` by summing them. Do not sum ratio metrics such as `roas` or `cac`;
multi-row groups should report those metrics as unavailable/null unless the code
has additive inputs to recompute the ratio.

- [ ] **Step 1: Update shared export fixtures**

Modify `tests/conftest.py`:

```python
@pytest.fixture
def sample_export_options():
    return {
        "breakdowns": {
            "breakdowns": [
                {"key": "Platform (Northbeam)", "values": ["Facebook Ads", "TikTok"]},
                {"key": "Category (Northbeam)", "values": ["Email", "Paid - Prospecting"]},
            ]
        },
        "metrics": {"metrics": [{"id": "rev", "label": "Rev"}, {"id": "txns", "label": "Transactions"}]},
        "attribution_models": {
            "attribution_models": [{"id": "northbeam_custom__va", "name": "Clicks + Modeled Views"}]
        },
    }


@pytest.fixture
def sample_export_create_response():
    return {"id": "exp-test-123"}


@pytest.fixture
def sample_export_completed_response():
    return {
        "status": "SUCCESS",
        "result": ["https://storage.example.com/export.csv"],
    }


@pytest.fixture
def sample_export_csv():
    return (
        "breakdown_platform_northbeam,campaign_name,rev,roas\n"
        "Facebook Ads,FB_Prospecting,1500.00,3.20\n"
        "TikTok,TT_Retargeting,800.00,2.10\n"
    )
```

- [ ] **Step 2: Write failing pipeline tests for current payload shape**

Add these tests to `tests/test_tools.py`:

```python
import json
```

```python
async def test_data_export_uses_current_payload_without_breakdowns(
    config,
    sample_export_create_response,
    sample_export_completed_response,
    monkeypatch,
):
    monkeypatch.setattr(client_module, "EXPORT_POLL_INTERVAL", 0.01)
    csv_content = "transactions,rev\n1.5,100.25\n2.5,200.75\n"

    with respx.mock:
        post_route = respx.post("https://api.northbeam.io/v1/exports/data-export").mock(
            return_value=httpx.Response(201, json=sample_export_create_response)
        )
        respx.get("https://api.northbeam.io/v1/exports/data-export/result/exp-test-123").mock(
            return_value=httpx.Response(200, json=sample_export_completed_response)
        )
        respx.get("https://storage.example.com/export.csv").mock(
            return_value=httpx.Response(200, text=csv_content)
        )

        result = await _data_export(
            config=config,
            date_start="2026-05-31",
            date_end="2026-05-31",
            metrics=["txns", "rev"],
            breakdowns=[],
        )

    sent = json.loads(post_route.calls[0].request.content)
    assert "date_start" not in sent
    assert "attribution_model" not in sent
    assert sent["period_type"] == "FIXED"
    assert sent["period_options"]["period_starting_at"] == "2026-05-31T00:00:00Z"
    assert sent["period_options"]["period_ending_at"] == "2026-05-31T23:59:59Z"
    assert sent["metrics"] == [{"id": "txns"}, {"id": "rev"}]
    assert result["data"][0]["txns"] == 4.0
    assert result["data"][0]["rev"] == 301.0


async def test_data_export_fetches_breakdown_values_for_non_empty_breakdowns(
    config,
    sample_export_options,
    sample_export_create_response,
    sample_export_completed_response,
    monkeypatch,
):
    monkeypatch.setattr(client_module, "EXPORT_POLL_INTERVAL", 0.01)
    csv_content = "breakdown_platform_northbeam,rev\nFacebook Ads,100\nTikTok,50\n"

    with respx.mock:
        breakdowns_route = respx.get("https://api.northbeam.io/v1/exports/breakdowns").mock(
            return_value=httpx.Response(200, json=sample_export_options["breakdowns"])
        )
        metrics_route = respx.get("https://api.northbeam.io/v1/exports/metrics").mock(
            return_value=httpx.Response(200, json=sample_export_options["metrics"])
        )
        models_route = respx.get("https://api.northbeam.io/v1/exports/attribution-models").mock(
            return_value=httpx.Response(200, json=sample_export_options["attribution_models"])
        )
        post_route = respx.post("https://api.northbeam.io/v1/exports/data-export").mock(
            return_value=httpx.Response(201, json=sample_export_create_response)
        )
        respx.get("https://api.northbeam.io/v1/exports/data-export/result/exp-test-123").mock(
            return_value=httpx.Response(200, json=sample_export_completed_response)
        )
        respx.get("https://storage.example.com/export.csv").mock(
            return_value=httpx.Response(200, text=csv_content)
        )

        result = await _data_export(
            config=config,
            date_start="2026-05-31",
            date_end="2026-05-31",
            metrics=["rev"],
            breakdowns=["platform"],
        )

    sent = json.loads(post_route.calls[0].request.content)
    assert sent["breakdowns"] == [
        {"key": "Platform (Northbeam)", "values": ["Facebook Ads", "TikTok"]}
    ]
    assert breakdowns_route.called
    assert metrics_route.called
    assert models_route.called
    assert result["data"][0]["platform"] == "Facebook Ads"
    assert result["data"][0]["rev"] == 100.0
```

Add a focused aggregation regression test:

```python
def test_aggregate_export_rows_uses_live_metric_and_breakdown_columns():
    rows = [
        {"breakdown_platform_northbeam": "Facebook Ads", "transactions": "1.5", "rev": "100"},
        {"breakdown_platform_northbeam": "Facebook Ads", "transactions": "2.5", "rev": "200"},
    ]

    result = _aggregate_export_rows(rows, ["platform"], ["txns", "rev"])

    assert result == [
        {
            "platform": "Facebook Ads",
            "txns": 4.0,
            "rev": 300.0,
            "_row_count": 2,
        }
    ]
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run:

```bash
uv run pytest tests/test_tools.py::test_data_export_uses_current_payload_without_breakdowns tests/test_tools.py::test_data_export_fetches_breakdown_values_for_non_empty_breakdowns tests/test_tools.py::test_aggregate_export_rows_uses_live_metric_and_breakdown_columns -v
```

Expected: FAIL because `_data_export` still sends the old payload and aggregation does not know live CSV aliases.

- [ ] **Step 4: Implement pipeline changes**

Modify imports in `server/northbeam_mcp.py`:

```python
from server.data_export import (
    breakdown_column_candidates,
    build_breakdown_value_lookup,
    build_data_export_payload,
    extract_download_url,
    extract_export_id,
    metric_column_candidates,
)
```

Add a row-value helper near `_safe_float`:

```python
def _first_row_value(row: dict[str, Any], candidates: list[str], default: Any = "") -> Any:
    for candidate in candidates:
        if candidate in row:
            return row.get(candidate)
    return default
```

Update `_aggregate_export_rows`:

```python
def _aggregate_export_rows(
    rows: list[dict[str, str]],
    breakdowns: list[str],
    metrics: list[str],
) -> list[dict[str, Any]]:
    groups: dict[tuple, dict[str, Any]] = defaultdict(
        lambda: {"_count": 0}
    )

    for row in rows:
        key = tuple(
            _first_row_value(row, breakdown_column_candidates(b), "")
            for b in breakdowns
        )
        group = groups[key]
        group["_count"] += 1
        for metric in metrics:
            raw_value = _first_row_value(row, metric_column_candidates(metric), 0)
            group[metric] = group.get(metric, 0.0) + _safe_float(raw_value)

    aggregated: list[dict[str, Any]] = []
    for key, group in groups.items():
        entry: dict[str, Any] = dict(zip(breakdowns, key))
        for metric in metrics:
            entry[metric] = round(group.get(metric, 0.0), 2)
        entry["_row_count"] = group["_count"]
        aggregated.append(entry)

    aggregated.sort(key=lambda r: r.get(metrics[0], 0) if metrics else 0, reverse=True)
    return aggregated
```

Add an async payload builder:

```python
async def _build_export_body(
    client: NorthbeamClient,
    *,
    date_start: str,
    date_end: str,
    metrics: list[str],
    breakdowns: list[str],
    attribution_model: str,
    attribution_window: str,
) -> dict[str, Any]:
    breakdown_values = None
    if breakdowns:
        options = await client.list_export_options()
        breakdown_values = build_breakdown_value_lookup({"breakdowns": options["breakdowns"]})

    try:
        return build_data_export_payload(
            date_start=date_start,
            date_end=date_end,
            metrics=metrics,
            breakdowns=breakdowns,
            attribution_model=attribution_model,
            attribution_window=attribution_window,
            breakdown_values=breakdown_values,
        )
    except ValueError as e:
        raise ToolError(str(e)) from None
```

Update `_data_export` to call `_build_export_body`, then extract current or legacy response fields:

```python
body = await _build_export_body(
    client,
    date_start=date_start,
    date_end=date_end,
    metrics=effective_metrics,
    breakdowns=effective_breakdowns,
    attribution_model=attribution_model,
    attribution_window=attribution_window,
)
create_result = await client.create_data_export(body)
export_id = extract_export_id(create_result)
if not export_id:
    raise ToolError("Northbeam API response missing export id")

poll_result = await client.poll_export_result(export_id)
download_url = extract_download_url(poll_result)
if not download_url:
    raise ToolError("Northbeam API response missing download URL")
```

Update `_run_export_pipeline` to use `extract_export_id` and `extract_download_url`.

- [ ] **Step 5: Run pipeline tests**

Run:

```bash
uv run pytest tests/test_data_export.py tests/test_tools.py::test_data_export_uses_current_payload_without_breakdowns tests/test_tools.py::test_data_export_fetches_breakdown_values_for_non_empty_breakdowns tests/test_tools.py::test_aggregate_export_rows_uses_live_metric_and_breakdown_columns -v
```

Expected: PASS.

- [ ] **Step 6: Run all tool tests**

Run:

```bash
uv run pytest tests/test_tools.py -v
```

Expected: PASS. If older tests fail because fixture response shapes changed, update the assertions to the stable public behavior, not the stale request body.

- [ ] **Step 7: Commit pipeline work**

```bash
git add server/northbeam_mcp.py tests/test_tools.py tests/conftest.py
git commit -m "fix: use current Northbeam data export contract"
```

---

### Task 4: Expand Setup Connection Sanity Check

**Files:**
- Modify: `server/northbeam_mcp.py`
- Modify: `tests/test_tools.py`

- [ ] **Step 1: Write full-surface setup tests**

Update `_check_connection` tests in `tests/test_tools.py`.

Add:

```python
async def test_check_connection_reports_spend_and_outcome_surfaces(
    config,
    sample_export_options,
    sample_export_create_response,
    sample_export_completed_response,
    monkeypatch,
):
    monkeypatch.setattr(client_module, "EXPORT_POLL_INTERVAL", 0.01)
    spend_response = {
        "data": [],
        "page": 1,
        "page_size": 1000,
        "total_pages": 1,
        "total_count": 0,
    }
    csv_content = "transactions,rev\n80.38863860198144,18372.969881449368\n"

    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(200, json=spend_response)
        )
        breakdowns_route = respx.get("https://api.northbeam.io/v1/exports/breakdowns").mock(
            return_value=httpx.Response(200, json=sample_export_options["breakdowns"])
        )
        metrics_route = respx.get("https://api.northbeam.io/v1/exports/metrics").mock(
            return_value=httpx.Response(200, json=sample_export_options["metrics"])
        )
        models_route = respx.get("https://api.northbeam.io/v1/exports/attribution-models").mock(
            return_value=httpx.Response(200, json=sample_export_options["attribution_models"])
        )
        respx.post("https://api.northbeam.io/v1/exports/data-export").mock(
            return_value=httpx.Response(201, json=sample_export_create_response)
        )
        respx.get("https://api.northbeam.io/v1/exports/data-export/result/exp-test-123").mock(
            return_value=httpx.Response(200, json=sample_export_completed_response)
        )
        respx.get("https://storage.example.com/export.csv").mock(
            return_value=httpx.Response(200, text=csv_content)
        )

        result = await _check_connection(config=config, check_date="2026-05-31")

    assert "Status: Connected" in result
    assert "Environment: prod" in result
    assert "Spend API: OK - 0 spend rows for 2026-05-31" in result
    assert "Data Export metadata: OK" in result
    assert "Data Export API: OK - transactions=80.39, revenue=18372.97 for 2026-05-31" in result
    assert "spend rows are ad spend records, not orders or transactions" in result
    assert "Outcome data exists even though spend rows are zero" in result
    assert breakdowns_route.called
    assert metrics_route.called
    assert models_route.called
```

Add:

```python
async def test_check_connection_reports_partial_data_export_failure(
    config,
    sample_export_options,
):
    spend_response = {
        "data": [],
        "page": 1,
        "page_size": 1000,
        "total_pages": 1,
        "total_count": 0,
    }

    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(200, json=spend_response)
        )
        breakdowns_route = respx.get("https://api.northbeam.io/v1/exports/breakdowns").mock(
            return_value=httpx.Response(200, json=sample_export_options["breakdowns"])
        )
        metrics_route = respx.get("https://api.northbeam.io/v1/exports/metrics").mock(
            return_value=httpx.Response(200, json=sample_export_options["metrics"])
        )
        models_route = respx.get("https://api.northbeam.io/v1/exports/attribution-models").mock(
            return_value=httpx.Response(200, json=sample_export_options["attribution_models"])
        )
        respx.post("https://api.northbeam.io/v1/exports/data-export").mock(
            return_value=httpx.Response(422, json={"error": [{"loc": ["metrics", 0], "msg": "bad"}]})
        )

        result = await _check_connection(config=config, check_date="2026-05-31")

    assert "Status: Partially connected" in result
    assert "Spend API: OK" in result
    assert "Data Export metadata: OK" in result
    assert "Data Export API: Failed" in result
    assert breakdowns_route.called
    assert metrics_route.called
    assert models_route.called
```

Add:

```python
async def test_check_connection_reports_partial_metadata_failure(config):
    spend_response = {
        "data": [],
        "page": 1,
        "page_size": 1000,
        "total_pages": 1,
        "total_count": 0,
    }

    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(200, json=spend_response)
        )
        respx.get("https://api.northbeam.io/v1/exports/breakdowns").mock(
            return_value=httpx.Response(500, json={"message": "metadata unavailable"})
        )
        respx.get("https://api.northbeam.io/v1/exports/metrics").mock(
            return_value=httpx.Response(200, json={"metrics": []})
        )
        respx.get("https://api.northbeam.io/v1/exports/attribution-models").mock(
            return_value=httpx.Response(200, json={"attribution_models": []})
        )

        result = await _check_connection(config=config, check_date="2026-05-31")

    assert "Status: Partially connected" in result
    assert "Spend API: OK" in result
    assert "Data Export metadata: Failed" in result
    assert "Data Export API: Skipped - metadata check failed" in result
```

- [ ] **Step 2: Run setup tests to verify they fail**

Run:

```bash
uv run pytest tests/test_tools.py::test_check_connection_reports_spend_and_outcome_surfaces tests/test_tools.py::test_check_connection_reports_partial_data_export_failure tests/test_tools.py::test_check_connection_reports_partial_metadata_failure -v
```

Expected: FAIL because `_check_connection` does not accept `check_date` and only checks spend.

- [ ] **Step 3: Implement the expanded `_check_connection`**

Change the private signature:

```python
async def _check_connection(
    config: NorthbeamConfig | None = None,
    check_date: str | None = None,
) -> str:
```

Inside `_check_connection`, compute the date:

```python
yesterday = check_date or (date_type.today() - timedelta(days=1)).isoformat()
```

Add a tiny outcome probe helper:

```python
async def _run_outcome_sanity_probe(
    client: NorthbeamClient,
    *,
    check_date: str,
) -> dict[str, float]:
    body = build_data_export_payload(
        date_start=check_date,
        date_end=check_date,
        metrics=["txns", "rev"],
        breakdowns=[],
    )
    rows = await _run_export_pipeline(client, body, breakdowns=[], metrics=["txns", "rev"])
    totals = rows[0] if rows else {"txns": 0.0, "rev": 0.0}
    return {
        "transactions": _safe_float(totals.get("txns")),
        "revenue": _safe_float(totals.get("rev")),
    }
```

Add a metadata probe helper:

```python
async def _run_metadata_sanity_probe(client: NorthbeamClient) -> str:
    await client.list_export_options()
    return "Data Export metadata: OK - metrics, breakdowns, attribution models available"
```

If `_run_export_pipeline` still only accepts `body`, update it to:

```python
async def _run_export_pipeline(
    client: NorthbeamClient,
    body: dict[str, Any],
    *,
    breakdowns: list[str],
    metrics: list[str],
) -> list[dict[str, Any]]:
```

Use `breakdowns` and `metrics` when aggregating, because current request body now stores metric objects and breakdown objects:

```python
return _aggregate_export_rows(raw_rows, breakdowns, metrics) if raw_rows else []
```

Initialize export status before the Data Export try block:

```python
status = "Connected"
metadata_line: str | None = None
outcome_line = "Data Export API: Skipped"
outcome = {"transactions": 0.0, "revenue": 0.0}
```

Then run metadata before the outcome export so metadata failures are distinguishable:

```python
try:
    metadata_line = await _run_metadata_sanity_probe(client)
    outcome = await _run_outcome_sanity_probe(client, check_date=yesterday)
    outcome_line = (
        "Data Export API: OK - "
        f"transactions={outcome['transactions']:.2f}, "
        f"revenue={outcome['revenue']:.2f} for {yesterday}"
    )
except Exception as e:
    status = "Partially connected"
    if metadata_line is None:
        metadata_line = f"Data Export metadata: Failed - {e}"
        outcome_line = "Data Export API: Skipped - metadata check failed"
    else:
        outcome_line = f"Data Export API: Failed - {e}"
```

Keep auth failures as:

```python
raise ToolError(
    "Status: Not connected - authentication failed. "
    "Run /northbeam:setup for configuration instructions."
)
```

Build final output from the spend line, metadata line, outcome line, platforms
line, and explanatory notes:

```python
platform_text = (
    ", ".join(platforms)
    if platforms
    else f"none (no spend rows for {yesterday})"
)
lines = [
    f"Status: {status}",
    f"Environment: {config.environment}",
    f"Spend API: OK - {record_count} spend rows for {yesterday}",
    metadata_line or "Data Export metadata: Skipped",
    outcome_line,
    f"Platforms visible from spend: {platform_text}",
    "Note: spend rows are ad spend records, not orders or transactions.",
]
if status == "Connected" and record_count == 0 and (
    outcome["transactions"] > 0 or outcome["revenue"] > 0
):
    lines.append(
        "Outcome data exists even though spend rows are zero; this usually means "
        "the Spend API has no ad spend records for that date, not that orders are missing."
    )
return "\n".join(lines)
```

- [ ] **Step 4: Run setup sanity tests**

Run:

```bash
uv run pytest tests/test_tools.py::test_check_connection_success tests/test_tools.py::test_check_connection_reports_spend_and_outcome_surfaces tests/test_tools.py::test_check_connection_reports_partial_data_export_failure tests/test_tools.py::test_check_connection_reports_partial_metadata_failure tests/test_tools.py::test_check_connection_auth_failure_raises_tool_error -v
```

Expected: PASS.

- [ ] **Step 5: Commit setup sanity work**

```bash
git add server/northbeam_mcp.py tests/test_tools.py
git commit -m "feat: expand Northbeam setup sanity check"
```

---

### Task 5: Update Portfolio Health for Current Data Export

**Files:**
- Modify: `server/northbeam_mcp.py`
- Modify: `tests/test_portfolio.py`

- [ ] **Step 1: Write portfolio payload regression test**

Update `test_portfolio_health_full_flow` in `tests/test_portfolio.py` to mock metadata endpoints and assert the post body:

```python
import json
```

Inside the `with respx.mock:` block before the Data Export POST route mock, add:

```python
respx.get("https://api.northbeam.io/v1/exports/breakdowns").mock(
    return_value=httpx.Response(200, json={
        "breakdowns": [
            {"key": "Platform (Northbeam)", "values": ["Facebook Ads", "TikTok"]}
        ]
    })
)
respx.get("https://api.northbeam.io/v1/exports/metrics").mock(
    return_value=httpx.Response(200, json={"metrics": [{"id": "rev"}, {"id": "roas"}]})
)
respx.get("https://api.northbeam.io/v1/exports/attribution-models").mock(
    return_value=httpx.Response(200, json={
        "attribution_models": [{"id": "northbeam_custom__va"}]
    })
)
post_route = respx.post("https://api.northbeam.io/v1/exports/data-export").mock(
    return_value=httpx.Response(201, json=sample_export_create_response)
)
```

After the call:

```python
sent = json.loads(post_route.calls[0].request.content)
assert sent["metrics"] == [{"id": "rev"}, {"id": "roas"}]
assert sent["breakdowns"] == [
    {"key": "Platform (Northbeam)", "values": ["Facebook Ads", "TikTok"]}
]
assert "date_start" not in sent
assert result["outcomes"][0]["platform"] == "Facebook Ads"
```

- [ ] **Step 2: Run portfolio test to verify it fails**

Run:

```bash
uv run pytest tests/test_portfolio.py::test_portfolio_health_full_flow -v
```

Expected: FAIL because portfolio health still builds the stale export body.

- [ ] **Step 3: Update portfolio export body**

In `_portfolio_health`, replace the inline `export_body` dictionary with:

```python
export_body = await _build_export_body(
    client,
    date_start=date_start,
    date_end=date_end,
    metrics=["rev", "roas"],
    breakdowns=["platform"],
    attribution_model=attribution_model,
    attribution_window=attribution_window,
)
```

Update the export task call:

```python
export_task = asyncio.create_task(
    _run_export_pipeline(
        client,
        export_body,
        breakdowns=["platform"],
        metrics=["rev", "roas"],
    )
)
```

Because `_build_export_body` performs HTTP metadata calls, create the export body before starting the concurrent `spend_task` and `export_task`. Keep the implementation straightforward:

```python
export_body = await _build_export_body(
    client,
    date_start=date_start,
    date_end=date_end,
    metrics=["rev", "roas"],
    breakdowns=["platform"],
    attribution_model=attribution_model,
    attribution_window=attribution_window,
)
spend_task = asyncio.create_task(
    client.list_spend(
        date_start=date_start,
        date_end=date_end,
        fetch_all=True,
    )
)
export_task = asyncio.create_task(
    _run_export_pipeline(
        client,
        export_body,
        breakdowns=["platform"],
        metrics=["rev", "roas"],
    )
)
```

If the export returns multiple raw rows for the same platform, `rev` remains
additive but `roas` must not be summed. Preserve `null` ROAS for those groups
unless additive inputs are available to calculate a weighted ratio.

- [ ] **Step 4: Run portfolio tests**

Run:

```bash
uv run pytest tests/test_portfolio.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit portfolio work**

```bash
git add server/northbeam_mcp.py tests/test_portfolio.py
git commit -m "fix: update portfolio health data export"
```

---

### Task 6: Update Skills and Data Export Docs

**Files:**
- Modify: `skills/setup/SKILL.md`
- Modify: `skills/analyze/SKILL.md`
- Modify: `docs/northbeam-data-export-api.md`
- Modify: `README.md`

- [ ] **Step 1: Update setup skill language**

In `skills/setup/SKILL.md`, replace the Step 1 connected result guidance with:

```markdown
**If connected:** Report the environment and summarize each checked surface:

- Spend API status and spend row count for yesterday
- Data Export metadata status
- Data Export API status with transactions and revenue for yesterday

Make clear that spend rows are ad spend records, not orders. If spend rows are
0 but Data Export returns transactions or revenue, explain that this means
outcome data exists even though no ad spend rows were returned for that date.
Skip to Step 3 (business context profile).

**If partially connected:** Explain which surface failed. If Spend API works
but Data Export fails, do not tell the user to re-enter credentials unless the
error is authentication-related; this usually means the plugin or API contract
needs attention.
```

- [ ] **Step 2: Update analyze skill metric wording**

In `skills/analyze/SKILL.md`, update the Data Export defaults section with:

```markdown
Common outcome metric IDs:
- `rev` - revenue
- `txns` - transactions/orders
- `roas` - return on ad spend
- `cac` - customer acquisition cost

Order and transaction questions route to `northbeam_data_export`, not
`northbeam_list_spend`. Spend rows are ad spend records and should not be used
as a proxy for orders.
```

- [ ] **Step 3: Replace Data Export docs**

Replace the request body in `docs/northbeam-data-export-api.md` with:

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

Document create response:

```json
{"id": "exp-test-123"}
```

Document poll success response:

```json
{
  "data_export_id": "exp-test-123",
  "status": "SUCCESS",
  "result": ["https://storage.example.com/export.csv"]
}
```

Add one paragraph:

```markdown
Compatibility note: the plugin also accepts older internal test fixtures that
use `export_id`, `COMPLETED`, and `download_url`, but new requests are sent
using the current Data Export API shape above.
```

- [ ] **Step 4: Update README setup wording**

In `README.md`, under `### Verify Connection`, add:

```markdown
The setup check validates both the Spend API and Data Export API. A zero spend
row count means Northbeam returned no ad spend records for the checked day; it
does not mean orders or transactions are missing.
```

- [ ] **Step 5: Run docs/skills grep checks**

Run:

```bash
rg -n '"date_start"|attribution_model|download_url|COMPLETED|export_id' docs/northbeam-data-export-api.md skills README.md
```

Expected: no stale Data Export request-body examples remain. Mentions of compatibility terms are allowed only in the compatibility paragraph.

- [ ] **Step 6: Commit docs and skill updates**

```bash
git add skills/setup/SKILL.md skills/analyze/SKILL.md README.md
git add -f docs/northbeam-data-export-api.md
git commit -m "docs: update Northbeam setup and export API guidance"
```

---

### Task 7: Validate, Cachebust, Reinstall, and Verify

**Files:**
- Modify: `.codex-plugin/plugin.json`
- Local install cache through Codex plugin commands.

- [ ] **Step 1: Run full unit tests**

Run:

```bash
uv run pytest
```

Expected: all tests PASS.

- [ ] **Step 2: Run plugin validator**

Run:

```bash
python3 ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
```

Expected: validator exits 0.

- [ ] **Step 3: Run a local live sanity check without printing secrets**

Run:

```bash
NORTHBEAM_CREDENTIALS_FILE=/home/souther/.codex/northbeam.env uv run python - <<'PY'
import asyncio
from server.northbeam_mcp import _check_connection

async def main():
    result = await _check_connection(check_date="2026-05-31")
    print(result)

asyncio.run(main())
PY
```

Expected output includes:

```text
Status: Connected
Environment: prod
Spend API: OK
Data Export metadata: OK
Data Export API: OK
Note: spend rows are ad spend records, not orders or transactions.
```

Do not print environment variable values or credential file contents.

- [ ] **Step 4: Update Codex plugin cachebuster**

Run from the plugin-creator skill root:

```bash
python3 /home/souther/.codex/skills/.system/plugin-creator/scripts/update_plugin_cachebuster.py \
  /home/souther/Projects/northbeam-plugin
```

Expected: `.codex-plugin/plugin.json` version keeps its base version and receives a fresh Codex cachebuster such as `1.0.1+codex.20260601124530`.

- [ ] **Step 5: Sync plugin source to personal plugin directory**

Run from `/home/souther/Projects/northbeam-plugin`:

```bash
rsync -a --delete \
  --include='.env.example' \
  --exclude='.git/' \
  --exclude='.env*' \
  --exclude='.venv/' \
  --exclude='.pytest_cache/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='.spec-workflow/' \
  --exclude='docs/' \
  ./ ~/plugins/northbeam/
```

Expected: no secrets are copied because `.env*` is excluded except `.env.example`.

- [ ] **Step 6: Read marketplace name and reinstall**

Run:

```bash
python3 /home/souther/.codex/skills/.system/plugin-creator/scripts/read_marketplace_name.py
codex plugin add northbeam@personal
```

Expected: the helper prints `personal`, and Codex installs the new version from the personal marketplace. If the helper prints a different name, stop and inspect `/home/souther/.agents/plugins/marketplace.json` before reinstalling so the command targets the actual local marketplace.

- [ ] **Step 7: Confirm installed plugin and MCP config**

Run:

```bash
codex plugin list
codex mcp list
```

Expected:

- `northbeam@personal` is installed and enabled at the fresh cachebuster version.
- MCP `northbeam` command is `uv`.
- MCP args are `run python -m server.northbeam_mcp`.
- MCP env is `-`.
- MCP cwd points at the fresh installed cache path.

- [ ] **Step 8: Commit cachebuster/version update**

```bash
git add .codex-plugin/plugin.json
git commit -m "chore: cachebust Northbeam plugin"
```

- [ ] **Step 9: Final verification in current session**

Run:

```bash
git status --short
uv run pytest
python3 ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
```

Expected:

- Git status is clean.
- Tests pass.
- Plugin validation passes.

Then tell the user to start a new Codex thread to test the newly installed MCP tool surface. Old sessions may keep stale MCP process state.
