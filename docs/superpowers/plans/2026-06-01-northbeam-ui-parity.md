# Northbeam UI-Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make spend, efficiency, revenue, and ROAS returned by the plugin match the Northbeam web UI exactly, by re-sourcing spend/efficiency from the Data Export API, using `revAttributed` for revenue/ROAS, killing the accounting-mode fan-out double-count, and aligning attribution defaults to the UI (Clicks only / 1-day / accrual).

**Architecture:** Approach B. The upload-only Spend API (`GET /v1/spend`) is relabeled `northbeam_list_uploaded_spend`. A new `northbeam_spend` tool sources platform-level spend + efficiency from a Data Export. `northbeam_data_export`, `northbeam_portfolio_health`, and the connection probe are corrected to use `revAttributed` and the UI attribution defaults. A partition filter in the aggregator drops the cash-vs-accrual fan-out rows so spend/revenue are never summed across accounting modes. Portfolio health runs **one combined export** (spend + outcomes from the same accrual rows) so ROAS is consistent by construction.

**Tech Stack:** Python 3.11+, FastMCP, httpx, pytest + respx. Run everything with `uv run`.

---

## Background the implementer needs

- **Spend API is upload-only.** `GET /v1/spend` returns only spend a customer *uploaded* for non-integrated channels; it returns `total_count: 0` for natively-integrated accounts (Facebook/Google/TikTok). Real platform spend lives in the Data Export API via the `spend` metric.
- **Data Export CSV column quirks.** The metric id `impressions` comes back in a CSV column named `imprs`. There is no clean generic `clicks` column — derive clicks per row from `ecpc` (`clicks = spend / ecpc`). Platform names come back in `breakdown_platform_northbeam`.
- **Revenue metric.** The UI "Revenue"/ROAS column is `revAttributed` (windowed, model-dependent, accrual). `rev` is a cash/total basis number that reads empty in the accrual windowed view — never use `rev` for UI parity.
- **Attribution defaults.** UI default for this account = Clicks only (`northbeam_custom`) / 1-day window / accrual. Current code defaults to `northbeam_custom__va` / `7` — wrong.
- **Fan-out trap.** When a revenue metric is requested, the API returns extra rows per breakdown — one per accounting mode (an "Accrual performance" row at the requested window PLUS a "Cash snapshot" row at lifetime). Spend is repeated on both, so blind summation doubles spend. The fix filters to the requested accounting mode before aggregating.
- **Verified ground truth (May 2026):** Data Export `spend` by platform = Facebook $407,056.74 + TikTok $70,866.64 + Google $67,753.45 = **$545,676.83** (matches dashboard to the penny). Facebook `revAttributed` under Clicks only / 1-day / accrual = **$89,097.31**, ROAS **0.22**.
- **zsh note:** zsh does not word-split unquoted variables. In any shell command, pass explicit file paths to `grep`, not a `$VAR` holding a space-separated list.

The branch `northbeam-ui-parity` is already checked out with the design spec committed.

---

### Task 1: `impressions→imprs` alias and UI attribution defaults in `data_export.py`

**Files:**
- Modify: `server/data_export.py:18-20` (METRIC_COLUMN_ALIASES), `server/data_export.py:67-68` (payload defaults)
- Test: `tests/test_data_export.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_data_export.py`:

```python
def test_build_data_export_payload_uses_ui_defaults():
    payload = build_data_export_payload(
        date_start="2026-05-31",
        date_end="2026-05-31",
        metrics=["revAttributed"],
        breakdowns=[],
    )

    assert payload["attribution_options"] == {
        "attribution_models": ["northbeam_custom"],
        "accounting_modes": ["accrual"],
        "attribution_windows": ["1"],
    }


def test_metric_column_candidates_maps_impressions_to_imprs():
    assert metric_column_candidates("impressions") == ["impressions", "imprs"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_data_export.py::test_build_data_export_payload_uses_ui_defaults tests/test_data_export.py::test_metric_column_candidates_maps_impressions_to_imprs -v`
Expected: FAIL — defaults are still `northbeam_custom__va`/`7`; `impressions` has no alias.

- [ ] **Step 3: Make the changes**

In `server/data_export.py`, replace the alias map (lines 18-20):

```python
METRIC_COLUMN_ALIASES = {
    "txns": ["transactions"],
    "impressions": ["imprs"],
}
```

And change the two defaults in `build_data_export_payload` (lines 67-68) from:

```python
    attribution_model: str = "northbeam_custom__va",
    attribution_window: str = "7",
```

to:

```python
    attribution_model: str = "northbeam_custom",
    attribution_window: str = "1",
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_data_export.py -v`
Expected: PASS. (The existing `test_build_data_export_payload_without_breakdowns` still passes — it passes `attribution_model`/`attribution_window` explicitly.)

- [ ] **Step 5: Commit**

```bash
git add server/data_export.py tests/test_data_export.py
git commit -m "feat: imprs alias and UI attribution defaults in data_export"
```

---

### Task 2: Accounting-mode partition column helper in `data_export.py`

**Files:**
- Modify: `server/data_export.py` (add `PARTITION_COLUMN_ALIASES` near the other alias maps; add `partition_column_candidates` near `metric_column_candidates`)
- Test: `tests/test_data_export.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_data_export.py` (and add `partition_column_candidates` to the existing import block at the top of the file):

```python
def test_partition_column_candidates_for_accounting_mode():
    assert partition_column_candidates("accounting_mode") == [
        "accounting_mode",
        "Accounting Mode",
        "accounting_mode_northbeam",
    ]


def test_partition_column_candidates_unknown_returns_self():
    assert partition_column_candidates("nonexistent") == ["nonexistent"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_data_export.py::test_partition_column_candidates_for_accounting_mode -v`
Expected: FAIL with `ImportError` / `NameError` — `partition_column_candidates` does not exist.

- [ ] **Step 3: Add the helper**

In `server/data_export.py`, add after `METRIC_COLUMN_ALIASES` (after line 20):

```python
# The Data Export API returns one row per accounting mode when a revenue metric
# is requested ("Accrual performance" + "Cash snapshot"). These are the CSV
# column-name candidates that carry the accounting mode, used to drop the
# duplicate rows before aggregation. Confirm/extend the exact name against a
# live export (see the live-verification task in the plan).
PARTITION_COLUMN_ALIASES = {
    "accounting_mode": [
        "accounting_mode",
        "Accounting Mode",
        "accounting_mode_northbeam",
    ],
}
```

And add near `metric_column_candidates` (after line 130):

```python
def partition_column_candidates(name: str) -> list[str]:
    return _dedupe([name, *PARTITION_COLUMN_ALIASES.get(name, [])])
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_data_export.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/data_export.py tests/test_data_export.py
git commit -m "feat: accounting-mode partition column candidates"
```

---

### Task 3: Env-configurable, clearer export poll timeout in `client.py`

**Files:**
- Modify: `server/client.py:1-17` (import os + constant), `server/client.py:140-159` (`poll_export_result`)
- Test: `tests/test_client.py`

- [ ] **Step 1: Write/adjust the failing tests**

In `tests/test_client.py`, replace the body of `test_poll_export_result_raises_on_timeout` (lines 454-466) with:

```python
async def test_poll_export_result_raises_on_timeout(config, monkeypatch):
    import server.client as client_module
    monkeypatch.setattr(client_module, "EXPORT_POLL_TIMEOUT", 0.1)
    monkeypatch.setattr(client_module, "EXPORT_POLL_INTERVAL", 0.05)

    with respx.mock:
        respx.get("https://api.northbeam.io/v1/exports/data-export/result/exp-slow").mock(
            return_value=httpx.Response(200, json={"status": "PENDING"})
        )

        async with NorthbeamClient(config) as client:
            with pytest.raises(NorthbeamAPIError) as exc_info:
                await client.poll_export_result("exp-slow")

    message = str(exc_info.value)
    assert "did not finish" in message
    assert "PENDING" in message
    assert "queue may be busy" in message
```

And add a new test:

```python
def test_export_poll_timeout_reads_env(monkeypatch):
    import importlib
    import server.client as client_module
    monkeypatch.setenv("NORTHBEAM_EXPORT_TIMEOUT", "240")
    importlib.reload(client_module)
    try:
        assert client_module.EXPORT_POLL_TIMEOUT == 240.0
    finally:
        monkeypatch.delenv("NORTHBEAM_EXPORT_TIMEOUT", raising=False)
        importlib.reload(client_module)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_client.py::test_poll_export_result_raises_on_timeout tests/test_client.py::test_export_poll_timeout_reads_env -v`
Expected: FAIL — current message says "timed out" (no "did not finish"/"PENDING"/"queue may be busy"); timeout is a hardcoded `60.0`, not env-driven.

- [ ] **Step 3: Make the changes**

In `server/client.py`, add `import os` to the imports (after line 6, `import logging`):

```python
import logging
import os
```

Replace the constant (line 16):

```python
EXPORT_POLL_TIMEOUT = float(os.environ.get("NORTHBEAM_EXPORT_TIMEOUT", "180.0"))
```

Replace `poll_export_result` (lines 140-159) with:

```python
    async def poll_export_result(self, export_id: str) -> dict[str, Any]:
        last_status = "PENDING"
        try:
            async with asyncio.timeout(EXPORT_POLL_TIMEOUT):
                while True:
                    result = await self._request_with_retry(
                        "GET", f"exports/data-export/result/{export_id}"
                    )
                    status = _normalize_export_status(result.get("status"))
                    if status:
                        last_status = status
                    if status in EXPORT_SUCCESS_STATUSES:
                        return result
                    if status in EXPORT_FAILURE_STATUSES:
                        raise NorthbeamAPIError(
                            f"Export {export_id} failed: "
                            f"{result.get('error', 'unknown')}"
                        )
                    await asyncio.sleep(EXPORT_POLL_INTERVAL)
        except TimeoutError as e:
            raise NorthbeamAPIError(
                f"Export {export_id} did not finish within "
                f"{EXPORT_POLL_TIMEOUT:.0f}s (last status: {last_status}). "
                "Northbeam's export queue may be busy — retry shortly."
            ) from e
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_client.py -v`
Expected: PASS (all client tests, including the unchanged poll/success/failed tests).

- [ ] **Step 5: Commit**

```bash
git add server/client.py tests/test_client.py
git commit -m "feat: configurable export poll timeout with clearer queue message"
```

---

### Task 4: Partition-aware aggregator + `revAttributed` additive (fan-out fix)

**Files:**
- Modify: `server/northbeam_mcp.py:35-47` (ADDITIVE_EXPORT_METRICS), `server/northbeam_mcp.py:348-390` (`_aggregate_export_rows`), `server/northbeam_mcp.py:464-466` (`_data_export` call site), `server/northbeam_mcp.py:600-645` (`_run_export_pipeline`)
- Modify imports: `server/northbeam_mcp.py:15-22` (add `partition_column_candidates`)
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_tools.py`:

```python
def test_aggregate_export_rows_drops_fanout_accounting_modes():
    rows = [
        {"breakdown_platform_northbeam": "Facebook Ads", "spend": "407056.74",
         "revAttributed": "89097.31", "accounting_mode": "Accrual performance"},
        {"breakdown_platform_northbeam": "Facebook Ads", "spend": "407056.74",
         "revAttributed": "154000.00", "accounting_mode": "Cash snapshot"},
    ]

    result = _aggregate_export_rows(
        rows, ["platform"], ["spend", "revAttributed"], accounting_mode="accrual"
    )

    assert len(result) == 1
    assert result[0]["spend"] == 407056.74
    assert result[0]["revAttributed"] == 89097.31


def test_aggregate_export_rows_without_partition_column_is_unchanged():
    rows = [
        {"breakdown_platform_northbeam": "Facebook Ads", "spend": "100"},
        {"breakdown_platform_northbeam": "Facebook Ads", "spend": "200"},
    ]

    result = _aggregate_export_rows(
        rows, ["platform"], ["spend"], accounting_mode="accrual"
    )

    assert len(result) == 1
    assert result[0]["spend"] == 300.0
```

Also add `_aggregate_export_rows` to the existing import in `tests/test_tools.py` if not already present (it is imported on line 9).

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_tools.py::test_aggregate_export_rows_drops_fanout_accounting_modes -v`
Expected: FAIL — `_aggregate_export_rows` has no `accounting_mode` parameter, and currently sums both rows (spend = 814113.48).

- [ ] **Step 3: Make the changes**

In `server/northbeam_mcp.py`, update the import block (lines 15-22) to add `partition_column_candidates`:

```python
from server.data_export import (
    breakdown_column_candidates,
    build_breakdown_value_lookup,
    build_data_export_payload,
    extract_download_url,
    extract_export_id,
    metric_column_candidates,
    partition_column_candidates,
)
```

Add `"revattributed"` to `ADDITIVE_EXPORT_METRICS` (insert into the set, lines 35-47):

```python
ADDITIVE_EXPORT_METRICS = {
    "clicks",
    "conversion",
    "conversions",
    "cost",
    "impressions",
    "orders",
    "revenue",
    "rev",
    "revattributed",
    "spend",
    "transactions",
    "txns",
}
```

Add a helper directly above `_aggregate_export_rows` (before line 348):

```python
def _select_accounting_partition(
    rows: list[dict[str, str]],
    accounting_mode: str | None,
) -> list[dict[str, str]]:
    """Drop fan-out duplicates. When a revenue metric is requested the API
    returns one row per accounting mode (accrual + cash); spend is repeated on
    each, so summing doubles it. When the CSV carries an accounting-mode column,
    keep only rows matching the requested mode."""
    if not accounting_mode or not rows:
        return rows
    candidates = partition_column_candidates("accounting_mode")
    column = next((c for c in candidates if c in rows[0]), None)
    if column is None:
        return rows
    needle = accounting_mode.strip().lower()
    filtered = [
        row for row in rows
        if str(row.get(column, "")).strip().lower().startswith(needle)
    ]
    if not filtered:
        logger.warning(
            "accounting-mode column %r present but no rows matched %r; "
            "keeping all rows (verify the partition column name)",
            column, accounting_mode,
        )
        return rows
    return filtered
```

Replace the `_aggregate_export_rows` signature and the first lines of its body (lines 348-356) so it accepts `accounting_mode` and filters first:

```python
def _aggregate_export_rows(
    rows: list[dict[str, str]],
    breakdowns: list[str],
    metrics: list[str],
    accounting_mode: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate raw CSV rows by breakdown keys, after dropping fan-out
    duplicate accounting-mode rows."""
    rows = _select_accounting_partition(rows, accounting_mode)
    groups: dict[tuple, dict[str, Any]] = defaultdict(
        lambda: {"_count": 0}
    )
```

(The rest of `_aggregate_export_rows`, from `for row in rows:` onward, is unchanged.)

In `_data_export`, update the aggregation call (lines 464-466) to extract and pass the accounting mode from the body:

```python
        accounting_modes = (
            body.get("attribution_options", {}).get("accounting_modes") or []
        )
        accounting_mode = accounting_modes[0] if accounting_modes else None
        aggregated = _aggregate_export_rows(
            raw_rows, effective_breakdowns, effective_metrics,
            accounting_mode=accounting_mode,
        ) if raw_rows else []
```

In `_run_export_pipeline`, update the final aggregation call (lines 640-644) the same way:

```python
        accounting_modes = (
            body.get("attribution_options", {}).get("accounting_modes") or []
        )
        accounting_mode = accounting_modes[0] if accounting_modes else None
        return _aggregate_export_rows(
            raw_rows,
            effective_breakdowns,
            effective_metrics,
            accounting_mode=accounting_mode,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tools.py -v`
Expected: PASS. The pre-existing aggregator tests (`test_aggregate_export_rows_sums_additive_metrics_only`, etc.) still pass because they don't pass `accounting_mode` (default `None` → no filtering).

- [ ] **Step 5: Commit**

```bash
git add server/northbeam_mcp.py tests/test_tools.py
git commit -m "fix: drop accounting-mode fan-out rows before aggregation"
```

---

### Task 5: `_spend_rows_from_aggregated` mapper (efficiency from imprs + ecpc)

**Files:**
- Modify: `server/northbeam_mcp.py` (add `_spend_rows_from_aggregated` near `_enrich_spend_rows`, after line 78)
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_tools.py` (add `_spend_rows_from_aggregated` to the import from `server.northbeam_mcp`):

```python
def test_spend_rows_from_aggregated_derives_clicks_and_impressions():
    aggregated = [
        {"platform": "Facebook Ads", "spend": 407056.74,
         "impressions": 10000000.0, "ecpc": 0.50},
    ]

    rows = _spend_rows_from_aggregated(aggregated)

    assert rows[0]["platform_name"] == "Facebook Ads"
    assert rows[0]["spend"] == 407056.74
    assert rows[0]["impressions"] == 10000000.0
    # clicks = spend / ecpc
    assert rows[0]["clicks"] == 407056.74 / 0.50
    # cpc enriched from spend / clicks == ecpc
    assert rows[0]["cpc"] == round(0.50, 2)


def test_spend_rows_from_aggregated_reconciles_may_2026_totals():
    aggregated = [
        {"platform": "Facebook Ads", "spend": 407056.74, "impressions": 1.0, "ecpc": 1.0},
        {"platform": "TikTok", "spend": 70866.64, "impressions": 1.0, "ecpc": 1.0},
        {"platform": "Google", "spend": 67753.45, "impressions": 1.0, "ecpc": 1.0},
    ]

    rows = _spend_rows_from_aggregated(aggregated)
    blended = _compute_blended_metrics(rows)
    by_platform = _aggregate_spend_by_platform(rows)

    assert blended["total_spend"] == 545676.83
    assert by_platform[0]["platform"] == "Facebook Ads"
    assert by_platform[0]["spend"] == 407056.74


def test_spend_rows_from_aggregated_zero_ecpc_yields_zero_clicks():
    aggregated = [{"platform": "Email", "spend": 100.0, "impressions": 0.0, "ecpc": 0.0}]

    rows = _spend_rows_from_aggregated(aggregated)

    assert rows[0]["clicks"] == 0.0
    assert rows[0]["cpc"] is None
```

`_compute_blended_metrics` and `_aggregate_spend_by_platform` are already importable from `server.northbeam_mcp` (used by `tests/test_portfolio.py`). Import them in `tests/test_tools.py` as well.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tools.py::test_spend_rows_from_aggregated_derives_clicks_and_impressions -v`
Expected: FAIL — `_spend_rows_from_aggregated` does not exist.

- [ ] **Step 3: Add the mapper**

In `server/northbeam_mcp.py`, add after `_enrich_spend_rows` (after line 78):

```python
def _spend_rows_from_aggregated(
    aggregated: list[dict[str, Any]],
    breakdown_key: str = "platform",
) -> list[dict[str, Any]]:
    """Convert per-breakdown Data Export rows into the legacy spend-row shape
    (`platform_name`, `spend`, `impressions`, `clicks`) that the analyze
    capabilities consume. Impressions come from the `imprs`-backed `impressions`
    metric; clicks are derived from `ecpc` (clicks = spend / ecpc)."""
    rows: list[dict[str, Any]] = []
    for entry in aggregated:
        spend = _safe_float(entry.get("spend"))
        impressions = _safe_float(entry.get("impressions"))
        ecpc = _safe_float(entry.get("ecpc"))
        clicks = spend / ecpc if ecpc > 0 else 0.0
        rows.append({
            "platform_name": entry.get(breakdown_key) or "Unknown",
            "spend": spend,
            "impressions": impressions,
            "clicks": clicks,
        })
    return _enrich_spend_rows(rows)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/northbeam_mcp.py tests/test_tools.py
git commit -m "feat: map Data Export rows to spend shape (imprs + ecpc-derived clicks)"
```

---

### Task 6: `northbeam_spend` tool (Data Export-backed spend + efficiency)

**Files:**
- Modify: `server/northbeam_mcp.py` (add `_spend_via_export` and the `@mcp.tool() northbeam_spend`, after `_list_options` ends, near line 346)
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_tools.py` (import `_spend_via_export` from `server.northbeam_mcp`):

```python
async def test_spend_via_export_returns_legacy_spend_shape(
    config,
    sample_export_options,
    sample_export_create_response,
    sample_export_completed_response,
    monkeypatch,
):
    monkeypatch.setattr(client_module, "EXPORT_POLL_INTERVAL", 0.01)
    csv_content = (
        "breakdown_platform_northbeam,spend,imprs,ecpc\n"
        "Facebook Ads,407056.74,10000000,0.50\n"
        "TikTok,70866.64,3000000,0.40\n"
    )

    with respx.mock:
        _mock_export_options(sample_export_options)
        post_route = respx.post("https://api.northbeam.io/v1/exports/data-export").mock(
            return_value=httpx.Response(201, json=sample_export_create_response)
        )
        respx.get("https://api.northbeam.io/v1/exports/data-export/result/exp-test-123").mock(
            return_value=httpx.Response(200, json=sample_export_completed_response)
        )
        respx.get("https://storage.example.com/export.csv").mock(
            return_value=httpx.Response(200, text=csv_content)
        )

        result = await _spend_via_export(
            config=config,
            date_start="2026-05-01",
            date_end="2026-05-31",
        )

    sent = json.loads(post_route.calls[0].request.content)
    assert sent["metrics"] == [{"id": "spend"}, {"id": "impressions"}, {"id": "ecpc"}]
    assert sent["attribution_options"]["attribution_models"] == ["northbeam_custom"]
    assert sent["attribution_options"]["attribution_windows"] == ["1"]

    assert result["total_count"] == 2
    fb = next(r for r in result["data"] if r["platform_name"] == "Facebook Ads")
    assert fb["spend"] == 407056.74
    assert fb["impressions"] == 10000000.0
    assert fb["clicks"] == 407056.74 / 0.50
    assert fb["cpc"] == 0.5


async def test_spend_via_export_filters_by_platform_name(
    config,
    sample_export_options,
    sample_export_create_response,
    sample_export_completed_response,
    monkeypatch,
):
    monkeypatch.setattr(client_module, "EXPORT_POLL_INTERVAL", 0.01)
    csv_content = (
        "breakdown_platform_northbeam,spend,imprs,ecpc\n"
        "Facebook Ads,407056.74,10000000,0.50\n"
        "TikTok,70866.64,3000000,0.40\n"
    )

    with respx.mock:
        _mock_export_options(sample_export_options)
        respx.post("https://api.northbeam.io/v1/exports/data-export").mock(
            return_value=httpx.Response(201, json=sample_export_create_response)
        )
        respx.get("https://api.northbeam.io/v1/exports/data-export/result/exp-test-123").mock(
            return_value=httpx.Response(200, json=sample_export_completed_response)
        )
        respx.get("https://storage.example.com/export.csv").mock(
            return_value=httpx.Response(200, text=csv_content)
        )

        result = await _spend_via_export(
            config=config,
            date_start="2026-05-01",
            date_end="2026-05-31",
            platform_name="facebook ads",
        )

    assert result["total_count"] == 1
    assert result["data"][0]["platform_name"] == "Facebook Ads"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tools.py::test_spend_via_export_returns_legacy_spend_shape -v`
Expected: FAIL — `_spend_via_export` does not exist.

- [ ] **Step 3: Implement `_spend_via_export` and the tool**

In `server/northbeam_mcp.py`, add after `_list_options` (after line 345) — note it reuses `_build_export_body` and `_run_export_pipeline` defined later in the module, which is fine at call time:

```python
SPEND_EXPORT_METRICS = ["spend", "impressions", "ecpc"]


async def _spend_via_export(
    config: NorthbeamConfig | None = None,
    date_start: str = "",
    date_end: str = "",
    platform_name: str | None = None,
    attribution_model: str = "northbeam_custom",
    attribution_window: str = "1",
) -> dict[str, Any]:
    """Source platform-level spend + efficiency (impressions, clicks, CPC, CPM,
    CTR) from the Data Export API. Spend is attribution-independent; the defaults
    match the account's UI default."""
    try:
        if config is None:
            config = load_config()
        async with NorthbeamClient(config) as client:
            body = await _build_export_body(
                client,
                date_start=date_start,
                date_end=date_end,
                metrics=SPEND_EXPORT_METRICS,
                breakdowns=["platform"],
                attribution_model=attribution_model,
                attribution_window=attribution_window,
            )
            aggregated = await _run_export_pipeline(
                client, body, breakdowns=["platform"], metrics=SPEND_EXPORT_METRICS,
            )

        rows = _spend_rows_from_aggregated(aggregated)
        if platform_name:
            needle = platform_name.lower()
            rows = [r for r in rows if (r.get("platform_name") or "").lower() == needle]

        return {
            "data": rows,
            "total_count": len(rows),
            "date_range": {"start": date_start, "end": date_end},
        }
    except (NorthbeamAuthError, NorthbeamConfigError):
        raise ToolError(AUTH_ERROR_MSG) from None
    except ExceptionGroup as eg:
        if _exception_group_contains_auth_error(eg):
            raise ToolError(AUTH_ERROR_MSG) from None
        logger.error("spend export error: %r", eg)
        raise ToolError(f"Error querying Northbeam spend: {eg.exceptions[0]}") from None
    except ToolError:
        raise
    except Exception as e:
        logger.error("spend export error: %s", e)
        raise ToolError(f"Error querying Northbeam spend: {e}")


@mcp.tool()
async def northbeam_spend(
    date_start: str,
    date_end: str,
    platform_name: str | None = None,
    attribution_model: str = "northbeam_custom",
    attribution_window: str = "1",
) -> dict[str, Any]:
    """Query platform-level ad spend and efficiency (spend, impressions, clicks,
    CPC, CPM, CTR) from the Northbeam Data Export API. This is the source of
    truth for spend on natively-integrated accounts (Facebook/Google/TikTok).

    Date format: YYYY-MM-DD. platform_name filters results (case-insensitive).
    Defaults match the account's UI default (Clicks only / 1-day / accrual);
    spend itself is attribution-independent.
    """
    return await _spend_via_export(
        date_start=date_start,
        date_end=date_end,
        platform_name=platform_name,
        attribution_model=attribution_model,
        attribution_window=attribution_window,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/northbeam_mcp.py tests/test_tools.py
git commit -m "feat: add northbeam_spend tool sourcing spend from Data Export"
```

---

### Task 7: Relabel `northbeam_list_spend` → `northbeam_list_uploaded_spend`

**Files:**
- Modify: `server/northbeam_mcp.py:288-324` (the `@mcp.tool()` function name + docstring)
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_tools.py`:

```python
def test_uploaded_spend_tool_is_exposed_and_documented():
    from server import northbeam_mcp

    tool = northbeam_mcp.northbeam_list_uploaded_spend
    assert tool.__name__ == "northbeam_list_uploaded_spend"
    assert "upload" in (tool.__doc__ or "").lower()
    assert not hasattr(northbeam_mcp, "northbeam_list_spend")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_tools.py::test_uploaded_spend_tool_is_exposed_and_documented -v`
Expected: FAIL — `northbeam_list_uploaded_spend` does not exist; `northbeam_list_spend` still does.

- [ ] **Step 3: Rename and re-document the tool**

In `server/northbeam_mcp.py`, replace the tool definition (lines 288-324). Change the function name on line 289 from `northbeam_list_spend` to `northbeam_list_uploaded_spend`, and replace the docstring (lines 302-311) with:

```python
@mcp.tool()
async def northbeam_list_uploaded_spend(
    date: str | None = None,
    date_start: str | None = None,
    date_end: str | None = None,
    platform_name: str | None = None,
    platform_account_id: str | None = None,
    campaign_id: str | None = None,
    adset_id: str | None = None,
    ad_id: str | None = None,
    page: int = 1,
    page_size: int = 1000,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """Query spend a customer UPLOADED via the Spend API for non-integrated
    channels (e.g. email tools). This is NOT the source of platform ad spend —
    it returns empty for natively-integrated accounts (Facebook/Google/TikTok).
    For real ad spend and efficiency, use northbeam_spend.

    Date parameters: provide 'date' for a single day, or 'date_start'+'date_end'
    for a range. Format: YYYY-MM-DD. platform_name filters server-side
    (case-insensitive).
    """
    return await _list_spend(
        date=date,
        date_start=date_start,
        date_end=date_end,
        platform_name=platform_name,
        platform_account_id=platform_account_id,
        campaign_id=campaign_id,
        adset_id=adset_id,
        ad_id=ad_id,
        page=page,
        page_size=page_size,
        fetch_all=fetch_all,
    )
```

(The internal `_list_spend` keeps its name — it is the upload-API path used by the connection check.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/northbeam_mcp.py tests/test_tools.py
git commit -m "refactor: relabel list_spend as list_uploaded_spend (upload-only)"
```

---

### Task 8: `northbeam_data_export` defaults → Clicks only / 1-day

**Files:**
- Modify: `server/northbeam_mcp.py:424-432` (`_data_export` defaults), `server/northbeam_mcp.py:520-546` (`northbeam_data_export` defaults)
- Test: `tests/test_tools.py:471-511` (existing full-flow assertion)

- [ ] **Step 1: Adjust the failing test**

In `tests/test_tools.py`, change the assertion in `test_data_export_full_flow` (line 506) from:

```python
    assert result["summary"]["attribution_model"] == "northbeam_custom__va"
```

to:

```python
    assert result["summary"]["attribution_model"] == "northbeam_custom"
    assert result["summary"]["attribution_window"] == "1"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_tools.py::test_data_export_full_flow -v`
Expected: FAIL — default is still `northbeam_custom__va`.

- [ ] **Step 3: Change the defaults**

In `server/northbeam_mcp.py`, change `_data_export` defaults (lines 430-431) from:

```python
    attribution_model: str = "northbeam_custom__va",
    attribution_window: str = "7",
```

to:

```python
    attribution_model: str = "northbeam_custom",
    attribution_window: str = "1",
```

Change `northbeam_data_export` defaults (lines 526-527) the same way, and update its docstring (lines 529-538) to add a line after the first paragraph:

```python
    """Run a Northbeam Data Export for outcome metrics (revenue, ROAS, CAC,
    conversions, etc.) with flexible breakdowns and attribution settings.

    Revenue is reported via `revAttributed` (the UI "Revenue"/ROAS basis), not
    `rev`. Defaults match the account's UI default: Clicks only
    (`northbeam_custom`), 1-day window, accrual. Use northbeam_list_options to
    discover valid metric/breakdown/model values.

    Returns aggregated data grouped by the requested breakdowns. Additive
    metrics are summed; ratio metrics are left null when a group spans multiple
    raw rows. Results capped at 200 rows (sorted by first metric descending).
    Check summary.truncated — if true, suggest narrower breakdowns.
    """
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/northbeam_mcp.py tests/test_tools.py
git commit -m "feat: default data export to UI attribution (Clicks only / 1-day)"
```

---

### Task 9: Connection check uses `revAttributed`; relabel uploaded-spend line

**Files:**
- Modify: `server/northbeam_mcp.py:135-156` (`_run_outcome_sanity_probe`), `server/northbeam_mcp.py:242-273` (connection summary lines)
- Test: `tests/test_tools.py` (the `test_check_connection_*` tests)

- [ ] **Step 1: Adjust the failing tests**

In `tests/test_tools.py`:

In `_mock_successful_connection_outcome` (line 31), change the default CSV header from `"transactions,rev\n0,0\n"` to:

```python
    csv_content="transactions,revAttributed\n0,0\n",
```

In `test_check_connection_reports_spend_and_outcome_surfaces` (line 181), change the CSV from `"transactions,rev\n..."` to:

```python
    csv_content = "transactions,revAttributed\n80.38863860198144,18372.969881449368\n"
```

and change the spend-line assertion (line 210) from:

```python
    assert "Spend API: OK - 0 spend rows for 2026-05-31" in result
```

to:

```python
    assert "Uploaded Spend API: OK - 0 uploaded spend rows for 2026-05-31" in result
```

In `test_check_connection_handles_empty_response`, the `"0" in result` assertion (line 163) still holds. The other `test_check_connection_reports_partial_*` tests assert the substring `"Spend API: OK"`, which remains a substring of `"Uploaded Spend API: OK"` — no change needed there.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tools.py -k check_connection -v`
Expected: FAIL on the spend-line wording and the revenue value (probe still reads `rev`, header changed to `revAttributed`).

- [ ] **Step 3: Make the changes**

In `server/northbeam_mcp.py`, replace `_run_outcome_sanity_probe` (lines 135-156):

```python
async def _run_outcome_sanity_probe(
    client: NorthbeamClient,
    *,
    check_date: str,
) -> dict[str, float]:
    body = build_data_export_payload(
        date_start=check_date,
        date_end=check_date,
        metrics=["txns", "revAttributed"],
        breakdowns=[],
    )
    rows = await _run_export_pipeline(
        client,
        body,
        breakdowns=[],
        metrics=["txns", "revAttributed"],
    )
    totals = rows[0] if rows else {"txns": 0.0, "revAttributed": 0.0}
    return {
        "transactions": _safe_float(totals.get("txns")),
        "revenue": _safe_float(totals.get("revAttributed")),
    }
```

Replace the spend line in the `lines` list (line 256) from:

```python
            f"Spend API: OK - {record_count} spend rows for {yesterday}",
```

to:

```python
            f"Uploaded Spend API: OK - {record_count} uploaded spend rows for "
            f"{yesterday} (0 is normal for natively-integrated accounts)",
```

Change the platforms line (line 259) from:

```python
            f"Platforms visible from spend: {platform_text}",
```

to:

```python
            f"Platforms with uploaded spend: {platform_text}",
```

And change the trailing note (line 260) from:

```python
            "Note: spend rows are ad spend records, not orders or transactions.",
```

to:

```python
            "Note: real ad spend comes from the Data Export API (northbeam_spend); "
            "the Uploaded Spend API only returns customer-uploaded, non-integrated spend.",
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/northbeam_mcp.py tests/test_tools.py
git commit -m "fix: connection check uses revAttributed and uploaded-spend wording"
```

---

### Task 10: Portfolio health — single combined export, revAttributed, UI defaults

**Files:**
- Modify: `server/northbeam_mcp.py:648-758` (`_portfolio_health`), `server/northbeam_mcp.py:761-782` (`northbeam_portfolio_health` defaults)
- Test: `tests/test_portfolio.py`

- [ ] **Step 1: Rewrite the failing tests**

In `tests/test_portfolio.py`, replace `test_portfolio_health_full_flow` (lines 101-176) with a single-combined-export version that includes a fan-out duplicate row:

```python
async def test_portfolio_health_full_flow(
    config,
    sample_export_create_response,
    sample_export_completed_response,
    monkeypatch,
):
    monkeypatch.setattr(client_module, "EXPORT_POLL_INTERVAL", 0.01)

    csv_content = (
        "breakdown_platform_northbeam,spend,imprs,ecpc,revAttributed,roas,accounting_mode\n"
        "Facebook Ads,407056.74,10000000,0.50,89097.31,0.22,Accrual performance\n"
        "Facebook Ads,407056.74,10000000,0.50,154000.00,0.38,Cash snapshot\n"
        "TikTok,70866.64,3000000,0.40,20000.00,0.28,Accrual performance\n"
        "TikTok,70866.64,3000000,0.40,30000.00,0.42,Cash snapshot\n"
    )

    with respx.mock:
        respx.get("https://api.northbeam.io/v1/exports/breakdowns").mock(
            return_value=httpx.Response(200, json={
                "breakdowns": [
                    {"key": "Platform (Northbeam)", "values": ["Facebook Ads", "TikTok"]}
                ]
            })
        )
        respx.get("https://api.northbeam.io/v1/exports/metrics").mock(
            return_value=httpx.Response(200, json={"metrics": [{"id": "spend"}]})
        )
        respx.get("https://api.northbeam.io/v1/exports/attribution-models").mock(
            return_value=httpx.Response(200, json={
                "attribution_models": [{"id": "northbeam_custom"}]
            })
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

        result = await _portfolio_health(
            config=config,
            date_start="2026-05-01",
            date_end="2026-05-31",
        )

    sent = json.loads(post_route.calls[0].request.content)
    assert sent["metrics"] == [
        {"id": "spend"}, {"id": "impressions"}, {"id": "ecpc"},
        {"id": "revAttributed"}, {"id": "roas"},
    ]
    assert sent["attribution_options"]["attribution_models"] == ["northbeam_custom"]
    assert sent["attribution_options"]["attribution_windows"] == ["1"]

    # Fan-out (Cash snapshot) rows dropped: spend not doubled.
    assert result["summary"]["total_spend"] == 477923.38  # 407056.74 + 70866.64
    fb = result["spend_by_platform"][0]
    assert fb["platform"] == "Facebook Ads"
    assert fb["spend"] == 407056.74

    outcomes = {o["platform"]: o for o in result["outcomes"]}
    assert outcomes["Facebook Ads"]["revAttributed"] == 89097.31
    assert outcomes["Facebook Ads"]["roas"] == 0.22
```

Replace `test_portfolio_health_graceful_export_failure` (lines 205-247) with a hard-fail version:

```python
async def test_portfolio_health_export_failure_raises(config, monkeypatch):
    """The combined export is the only data source; a failure is a ToolError."""
    with respx.mock:
        respx.get("https://api.northbeam.io/v1/exports/breakdowns").mock(
            return_value=httpx.Response(200, json={
                "breakdowns": [
                    {"key": "Platform (Northbeam)", "values": ["Facebook Ads", "TikTok"]}
                ]
            })
        )
        respx.get("https://api.northbeam.io/v1/exports/metrics").mock(
            return_value=httpx.Response(200, json={"metrics": [{"id": "spend"}]})
        )
        respx.get("https://api.northbeam.io/v1/exports/attribution-models").mock(
            return_value=httpx.Response(200, json={
                "attribution_models": [{"id": "northbeam_custom"}]
            })
        )
        respx.post("https://api.northbeam.io/v1/exports/data-export").mock(
            return_value=httpx.Response(500, json={"message": "Internal error"})
        )

        with pytest.raises(ToolError, match="portfolio health"):
            await _portfolio_health(
                config=config,
                date_start="2026-05-01",
                date_end="2026-05-31",
            )
```

Replace `test_portfolio_health_graceful_metadata_failure` (lines 250-287) with:

```python
async def test_portfolio_health_metadata_failure_raises(config, monkeypatch):
    monkeypatch.setattr(client_module, "INITIAL_BACKOFF", 0)
    with respx.mock:
        respx.get("https://api.northbeam.io/v1/exports/breakdowns").mock(
            return_value=httpx.Response(500, json={"message": "Metadata unavailable"})
        )
        respx.get("https://api.northbeam.io/v1/exports/metrics").mock(
            return_value=httpx.Response(200, json={"metrics": [{"id": "spend"}]})
        )
        respx.get("https://api.northbeam.io/v1/exports/attribution-models").mock(
            return_value=httpx.Response(200, json={
                "attribution_models": [{"id": "northbeam_custom"}]
            })
        )

        with pytest.raises(ToolError, match="portfolio health"):
            await _portfolio_health(
                config=config,
                date_start="2026-05-01",
                date_end="2026-05-31",
            )
```

The unit tests for `_compute_blended_metrics` and `_aggregate_spend_by_platform` (lines 17-99, 306-335) are unchanged — those helpers still consume the legacy spend-row shape, which the mapper produces.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_portfolio.py -v`
Expected: FAIL — `_portfolio_health` still calls `list_spend`, sends `["rev","roas"]`, and defaults to `northbeam_custom__va`/`7`.

- [ ] **Step 3: Rewrite `_portfolio_health` and the tool defaults**

In `server/northbeam_mcp.py`, replace `_portfolio_health` (lines 648-758) with:

```python
PORTFOLIO_EXPORT_METRICS = ["spend", "impressions", "ecpc", "revAttributed", "roas"]


async def _portfolio_health(
    config: NorthbeamConfig | None = None,
    date_start: str = "",
    date_end: str = "",
    attribution_model: str = "northbeam_custom",
    attribution_window: str = "1",
) -> dict[str, Any]:
    try:
        if config is None:
            config = load_config()

        if not date_start or not date_end:
            today = date_type.today()
            date_end = date_end or today.isoformat()
            date_start = date_start or today.replace(day=1).isoformat()

        async with NorthbeamClient(config) as client:
            export_body = await _build_export_body(
                client,
                date_start=date_start,
                date_end=date_end,
                metrics=PORTFOLIO_EXPORT_METRICS,
                breakdowns=["platform"],
                attribution_model=attribution_model,
                attribution_window=attribution_window,
            )
            aggregated = await _run_export_pipeline(
                client,
                export_body,
                breakdowns=["platform"],
                metrics=PORTFOLIO_EXPORT_METRICS,
            )

        spend_rows = _spend_rows_from_aggregated(aggregated)
        blended = _compute_blended_metrics(spend_rows)
        by_platform = _aggregate_spend_by_platform(spend_rows)

        return {
            "summary": {
                "date_range": {"start": date_start, "end": date_end},
                "attribution_model": attribution_model,
                "attribution_window": attribution_window,
                "record_count": len(spend_rows),
                **blended,
            },
            "spend_by_platform": by_platform,
            "outcomes": aggregated,
        }

    except (NorthbeamAuthError, NorthbeamConfigError):
        raise ToolError(AUTH_ERROR_MSG) from None
    except ToolError:
        raise
    except ExceptionGroup as eg:
        if _exception_group_contains_auth_error(eg):
            raise ToolError(AUTH_ERROR_MSG) from None
        logger.error("portfolio_health error: %s", eg)
        raise ToolError(
            f"Error building portfolio health: {_format_exception_message(eg)}"
        )
    except Exception as e:
        logger.error("portfolio_health error: %s", e)
        raise ToolError(f"Error building portfolio health: {e}")
```

Change `northbeam_portfolio_health` defaults (lines 765-766) from `northbeam_custom__va`/`7` to `northbeam_custom`/`1`, and update its docstring to mention revAttributed:

```python
@mcp.tool()
async def northbeam_portfolio_health(
    date_start: str = "",
    date_end: str = "",
    attribution_model: str = "northbeam_custom",
    attribution_window: str = "1",
) -> dict[str, Any]:
    """Get a holistic portfolio health snapshot combining spend efficiency
    (CPC, CPM, CTR) with outcomes (revAttributed, ROAS), all from one Data
    Export so spend and revenue come from the same accrual rows.

    Defaults to month-to-date and the account's UI attribution default
    (Clicks only / 1-day / accrual).

    Returns: blended metrics, per-platform breakdown with spend share, and
    per-platform outcomes (revAttributed, roas).
    """
    return await _portfolio_health(
        date_start=date_start,
        date_end=date_end,
        attribution_model=attribution_model,
        attribution_window=attribution_window,
    )
```

- [ ] **Step 4: Run the full suite to verify everything passes**

Run: `uv run pytest -v`
Expected: PASS for the whole suite. If any older portfolio test still references the removed `list_spend` flow, it was replaced in Step 1.

- [ ] **Step 5: Commit**

```bash
git add server/northbeam_mcp.py tests/test_portfolio.py
git commit -m "feat: portfolio health from one combined export (revAttributed, no fan-out)"
```

---

### Task 11: Update fixtures in `tests/conftest.py` to real Data Export columns

**Files:**
- Modify: `tests/conftest.py:47-83` (`sample_export_options`, `sample_export_csv`)
- Test: full suite

- [ ] **Step 1: Update the fixtures**

In `tests/conftest.py`, update `sample_export_options` metrics/models (lines 56-60) to reflect real ids, and `sample_export_csv` (lines 76-83) to use `revAttributed` and an accounting-mode column:

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
        "metrics": {"metrics": [
            {"id": "revAttributed", "label": "Attributed Rev"},
            {"id": "txns", "label": "Transactions"},
            {"id": "spend", "label": "Spend"},
        ]},
        "attribution_models": {
            "attribution_models": [
                {"id": "northbeam_custom", "name": "Clicks only"},
                {"id": "northbeam_custom__enh", "name": "Clicks + Deterministic Views"},
                {"id": "northbeam_custom__va", "name": "Clicks + Modeled Views"},
            ]
        },
    }
```

```python
@pytest.fixture
def sample_export_csv():
    return (
        "breakdown_platform_northbeam,campaign_name,revAttributed,roas\n"
        "Facebook Ads,FB_Prospecting,1500.00,3.20\n"
        "TikTok,TT_Retargeting,800.00,2.10\n"
    )
```

- [ ] **Step 2: Update tests that consume `sample_export_csv`**

`tests/test_tools.py::test_data_export_full_flow` (lines 471-510) uses `sample_export_csv` and asserts `result["data"][0]["rev"]`. Change those metric references from `rev` to `revAttributed`:

In the `_data_export(...)` call (line 497) change `metrics=["rev", "roas"]` to `metrics=["revAttributed", "roas"]`, and the assertions (lines 509-510) from:

```python
    assert result["data"][0]["rev"] == 1500.0
    assert result["data"][0]["roas"] == 3.2
```

to:

```python
    assert result["data"][0]["revAttributed"] == 1500.0
    assert result["data"][0]["roas"] == 3.2
```

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -v`
Expected: PASS. (`tests/test_portfolio.py` no longer uses `sample_export_csv` after Task 10; if any reference remains, point it at the inline CSV from Task 10.)

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py tests/test_tools.py
git commit -m "test: fixtures use real Data Export columns (revAttributed, etc.)"
```

---

### Task 12: Update reference docs (`spend-api-schema.md`, `northbeam-data-export-api.md`, `README.md`)

**Files:**
- Modify: `references/spend-api-schema.md`, `docs/northbeam-data-export-api.md`, `README.md`

- [ ] **Step 1: `references/spend-api-schema.md`**

Replace line 5:

```text
`GET /v1/spend` — List spend records
```

with:

```text
`GET /v1/spend` — List **uploaded** spend records (upload API for non-integrated channels)
```

Add a note block immediately after the Endpoint section (after line 8):

```text
> **Scope:** This endpoint returns only spend a customer *uploaded* for
> non-integrated channels. It returns no rows for natively-integrated platforms
> (Facebook/Google/TikTok) — for those, real spend and efficiency come from the
> Data Export API (`spend`, `imprs`, `ecpc`). See `docs/northbeam-data-export-api.md`.
```

- [ ] **Step 2: `docs/northbeam-data-export-api.md`**

Replace the attribution block in the example body (lines 43-52) from `northbeam_custom__va` / `7` / `rev` to the UI defaults and `revAttributed`:

```json
  "attribution_options": {
    "attribution_models": ["northbeam_custom"],
    "accounting_modes": ["accrual"],
    "attribution_windows": ["1"]
  },
  "metrics": [
    {"id": "txns"},
    {"id": "revAttributed"}
  ]
```

Replace the "Aggregation Behavior" section (lines 74-84) with a version that documents the fan-out and the revenue metric:

```text
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
```

- [ ] **Step 3: `README.md`**

Replace the description (lines 3-5):

```text
Marketing analytics through the Northbeam API. The plugin sources ad spend and
efficiency (impressions, clicks, CPC, CPM, CTR) and outcome metrics (revenue,
ROAS, CAC, conversions) from Northbeam's Data Export API. The legacy Spend API
(`GET /v1/spend`) is upload-only and used only for customer-uploaded,
non-integrated spend.
```

Replace the setup-check note (lines 186-188):

```text
The setup check validates the Uploaded Spend API and the Data Export API. A zero
uploaded-spend row count is normal for natively-integrated accounts — real ad
spend comes from the Data Export API (the `northbeam_spend` tool), not the
uploaded-spend endpoint.
```

Update the MCP Tools table (lines 240-244). Replace the `northbeam_list_spend` row and add a `northbeam_spend` row:

```text
| `northbeam_spend` | Platform-level ad spend + efficiency (CPC, CPM, CTR) from the Data Export API — the source of truth for spend on integrated accounts |
| `northbeam_list_uploaded_spend` | Query customer-uploaded spend (non-integrated channels) from the Spend API; empty for integrated accounts |
| `northbeam_data_export` | Run async data exports for outcomes such as revAttributed, ROAS, CAC, and conversions |
| `northbeam_list_options` | Discover available breakdowns, metrics, and attribution models |
| `northbeam_portfolio_health` | Build a spend-efficiency + outcome snapshot from one combined Data Export |
| `northbeam_check_connection` | Validate Uploaded Spend API access, Data Export metadata, and a small revAttributed probe |
```

Also update the analyze capability bullet (line 222) from "Ad hoc spend queries with derived metrics (CPC, CPM, CTR)" — leave as-is (still accurate). No other README change needed.

- [ ] **Step 4: Verify no stale references remain**

Run: `grep -rn "List spend records\|northbeam_custom__va\|{\"id\": \"rev\"}\|northbeam_list_spend" README.md docs/northbeam-data-export-api.md references/spend-api-schema.md`
Expected: no matches (other than inside `docs/superpowers/` history, which is not in the grep targets).

- [ ] **Step 5: Commit**

```bash
git add -f README.md docs/northbeam-data-export-api.md references/spend-api-schema.md
git commit -m "docs: reflect Data Export as spend source, revAttributed, fan-out"
```

(`docs/` is gitignored; `-f` matches how the existing tracked docs were added. `README.md` and `references/` are not ignored.)

---

### Task 13: Update the analyze skill (`skills/analyze/SKILL.md`)

**Files:**
- Modify: `skills/analyze/SKILL.md` — frontmatter allowed-tools, Authentication, Tool Routing, Data Export Defaults, Derived Metrics, Ad Hoc, and the Tip lines

- [ ] **Step 1: Frontmatter `allowed-tools` (line 4)**

Replace with (add `northbeam_spend`, rename `northbeam_list_spend`):

```text
allowed-tools: mcp__northbeam__northbeam_spend, mcp__northbeam__northbeam_list_uploaded_spend, mcp__northbeam__northbeam_data_export, mcp__northbeam__northbeam_list_options, mcp__northbeam__northbeam_check_connection, mcp__northbeam__northbeam_portfolio_health, Read
```

- [ ] **Step 2: Authentication section (line 20)**

Replace `northbeam_list_spend` with `northbeam_spend`:

```text
Do NOT call `northbeam_check_connection` as a pre-check — it wastes a tool call. Instead, call `northbeam_spend` directly with the user's query. If the tool call fails with an error (you'll see `isError: true` or an error message containing "Authentication failed" or "Run /northbeam:setup"), relay that to the user and stop:
```

- [ ] **Step 3: Tool Routing table (lines 47-55)**

Replace the table and rule-of-thumb:

```text
| User asks about | Tool to use | Why |
|----------------|-------------|-----|
| Spend, impressions, clicks, CPC, CPM, CTR | `northbeam_spend` | Data Export `spend`/`imprs`/`ecpc`; the source of truth for integrated accounts |
| Revenue, ROAS, CAC, conversions, orders | `northbeam_data_export` | Outcome metrics via `revAttributed` |
| "How are we doing?" / portfolio health | `northbeam_portfolio_health` | One combined export: spend + outcomes |
| Budget pacing | `northbeam_spend` | Pacing uses spend data only |
| Uploaded (non-integrated) spend | `northbeam_list_uploaded_spend` | Customer-uploaded spend only; empty for integrated accounts |
| Available metrics/breakdowns | `northbeam_list_options` | Discovery before data_export calls |

**Rule of thumb:** Spend, budget, and efficiency (money going OUT) → `northbeam_spend`. Revenue, ROAS, orders (money coming IN) → `northbeam_data_export`.
```

- [ ] **Step 4: Data Export Defaults + metric IDs (lines 66-85)**

Replace the "Data Export Defaults" list and "Common outcome metric IDs" with:

```text
### Data Export Defaults

Unless the user specifies otherwise, use these defaults — they match the account's UI default:
- Clicks only attribution (`northbeam_custom`)
- 1-day attribution window
- Accrual accounting mode

Attribution windows apply only to accrual mode; cash mode is always lifetime. These are overridable per request.

Common outcome metric IDs:
- `revAttributed` - revenue (the UI "Revenue"/ROAS basis; windowed, model-dependent). Do NOT use `rev` — it is a cash/total basis that reads empty in the accrual windowed view.
- `txns` - transactions/orders
- `roas` - return on ad spend (revAttributed / spend)
- `cac` - customer acquisition cost

Order and transaction questions route to `northbeam_data_export`, not
`northbeam_spend`. Spend rows are ad spend records and should not be used as a
proxy for orders.

When `northbeam_data_export` aggregates multiple raw rows, additive metrics such
as `revAttributed` and `txns` are summed. Ratio metrics such as `roas` and `cac`
may be `null` for multi-row groups; do not add or average them manually unless
you have the additive inputs needed to compute the ratio. The export drops
cash-vs-accrual fan-out duplicates automatically.
```

- [ ] **Step 5: Derived Metrics + Ad Hoc + Tip (lines 130, 142, 156)**

Replace `northbeam_list_spend` with `northbeam_spend` on line 130:

```text
`northbeam_spend` returns pre-computed efficiency metrics on each row:
```

Line 142:

```text
Answer free-form spend questions using `northbeam_spend`.
```

Line 156:

```text
**Tip:** `northbeam_spend` accepts `platform_name` for server-side filtering (case-insensitive). Use it to avoid fetching irrelevant platforms.
```

- [ ] **Step 6: Verify no stale references remain**

Run: `grep -n "northbeam_list_spend\|Northbeam Custom VA\|7-day attribution\|\`rev\` - revenue" skills/analyze/SKILL.md`
Expected: no matches.

- [ ] **Step 7: Commit**

```bash
git add skills/analyze/SKILL.md
git commit -m "docs: route spend to northbeam_spend, revAttributed, UI defaults in analyze skill"
```

---

### Task 14: Update the setup skill + final suite + live verification

**Files:**
- Modify: `skills/setup/SKILL.md` (allowed-tools + spend-row framing)
- Verify: full suite; live API smoke test

- [ ] **Step 1: setup skill `allowed-tools` (line 4)**

Replace with:

```text
allowed-tools: mcp__northbeam__northbeam_check_connection, mcp__northbeam__northbeam_spend, mcp__northbeam__northbeam_list_uploaded_spend, Read, Write
```

- [ ] **Step 2: setup skill spend-row framing (lines 17-26 and 62-70)**

In Step 1 (lines 17-26), replace the connected-summary bullets and note:

```text
**If connected:** Report the environment and summarize each checked surface:

- Uploaded Spend API status and uploaded-spend row count for yesterday
- Data Export metadata status
- Data Export API status with transactions and revenue (revAttributed) for yesterday

A zero uploaded-spend count is normal for natively-integrated accounts — real ad
spend comes from the Data Export API (the `northbeam_spend` tool), not the
uploaded-spend endpoint. Skip to Step 3 (business context profile).
```

In Step 2 (lines 62-70), replace the validation bullets and note correspondingly:

```text
Once credentials are set, call `northbeam_check_connection` again. Confirm:
- The environment shown (prod or uat)
- Uploaded Spend API status and uploaded-spend row count
- Data Export metadata status
- Data Export API transactions and revenue (revAttributed) status

A zero uploaded-spend count is normal for natively-integrated accounts — real ad
spend comes from the Data Export API (`northbeam_spend`).
```

- [ ] **Step 3: Verify no stale references remain in skills**

Run: `grep -rn "northbeam_list_spend" skills`
Expected: no matches.

- [ ] **Step 4: Commit the skill change**

```bash
git add skills/setup/SKILL.md
git commit -m "docs: setup skill clarifies uploaded-spend vs Data Export spend"
```

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest -v`
Expected: ALL PASS. If any test still references `northbeam_list_spend`, `rev` as a default revenue metric, or `northbeam_custom__va` defaults, fix it to match the new behavior.

Also run the plugin validator if available:

Run: `python3 ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .`
Expected: passes (or is skipped if the validator is not installed).

- [ ] **Step 6: Live verification against the real API (UI parity + confirm assumptions)**

This is the reality check the unit tests cannot give. It (a) confirms spend/ROAS match the UI, (b) confirms the exact accounting-mode CSV column name, and (c) confirms whether `revAttributed` triggers the fan-out. Per the user's standing instruction, write the probe to a file and execute it — do not run inline Python.

Write `scripts/verify_ui_parity.py`:

```python
"""Live UI-parity check. Requires real Northbeam credentials in the environment
or a credential file (same resolution as the MCP server). Read-only."""
import asyncio
import json

from server.config import load_config
from server.northbeam_mcp import _spend_via_export, _portfolio_health


async def main() -> None:
    config = load_config()
    spend = await _spend_via_export(
        config=config, date_start="2026-05-01", date_end="2026-05-31",
    )
    print("=== northbeam_spend (May 2026) ===")
    for row in spend["data"]:
        print(f"{row['platform_name']:<16} spend={row['spend']:>14,.2f} "
              f"clicks={row['clicks']:>12,.0f} cpc={row['cpc']}")
    total = sum(r["spend"] for r in spend["data"])
    print(f"TOTAL spend = {total:,.2f}  (expected 545,676.83)")

    health = await _portfolio_health(
        config=config, date_start="2026-05-01", date_end="2026-05-31",
    )
    print("\n=== portfolio outcomes (revAttributed / roas) ===")
    for o in health["outcomes"]:
        print(f"{o.get('platform'):<16} revAttributed={o.get('revAttributed')} "
              f"roas={o.get('roas')}")
    print("\nFacebook expected: revAttributed=89,097.31  roas≈0.22")
    print("\nfull health summary:", json.dumps(health["summary"], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
```

Run: `uv run python scripts/verify_ui_parity.py`

Expected: total spend ≈ 545,676.83; Facebook revAttributed ≈ 89,097.31, roas ≈ 0.22.

**If the numbers don't match:**
- If spend is roughly doubled, the accounting-mode partition didn't engage. Download one raw CSV and inspect the header for the real accounting-mode column name, then add it to `PARTITION_COLUMN_ALIASES["accounting_mode"]` in `server/data_export.py` and re-run. (To inspect, temporarily print `csv_result["columns"]` in `_run_export_pipeline`, or run a one-off download script — write it to a file, don't run inline.)
- If `revAttributed` is empty/zero, re-confirm the metric id via `northbeam_list_options` output and adjust.
- If the export times out (queue degradation), retry later or raise `NORTHBEAM_EXPORT_TIMEOUT`. This is environmental, not a code bug.

Delete the probe script when done (it is a one-off; do not commit it):

```bash
rm scripts/verify_ui_parity.py
```

- [ ] **Step 7: Final commit (only if `PARTITION_COLUMN_ALIASES` needed correction)**

```bash
git add server/data_export.py
git commit -m "fix: correct accounting-mode partition column name from live verification"
```

---

## Self-Review

**Spec coverage** (each spec requirement → task):
- (a) re-source spend + efficiency from Data Export (imprs / ecpc / true blended) → Tasks 5, 6
- (b) fan-out fix (group/filter by accounting mode) → Tasks 2, 4
- (c) attribution defaults `northbeam_custom` / `1` / accrual, configurable → Tasks 1, 6, 8, 10
- (d) revenue/ROAS from `revAttributed` → Tasks 4, 8, 9, 10
- (e) configurable, generous poll timeout + clear message → Task 3
- (f) add `northbeam_spend` + relabel `list_spend` → `list_uploaded_spend` → Tasks 6, 7
- (g) connection check uses revAttributed → Task 9
- (h) portfolio health rewrite (spend source + blended/by-platform) → Task 10 (mapper isolates column knowledge, so the two helper functions are unchanged by design)
- (i) `METRIC_COLUMN_ALIASES` impressions→imprs → Task 1
- (j) docs (data-export-api, spend-api-schema, README) + skills (analyze, setup) → Tasks 12, 13, 14
- (k) rewrite tests to real columns + fan-out + reconciliation → Tasks 1–11 (tests inline per task), 11 (fixtures)

**Deviations from the spec (improvements, same intent):**
- Spec §4.3 said to change the column reads inside `_enrich_spend_rows` / `_compute_blended_metrics` / `_aggregate_spend_by_platform`. Instead, a single mapper (`_spend_rows_from_aggregated`, Task 5) normalizes Data Export rows to the legacy shape those helpers already expect — one place owns the column knowledge, less churn, helpers stay tested.
- Spec §3 data-flow showed separate spend vs outcome exports for portfolio. Implementation uses **one combined export** (Task 10): cheaper, deterministic to test, and ROAS = revAttributed/spend is consistent by construction because both come from the same accrual rows. The accounting-mode filter protects spend from the fan-out.
- Window-based partition filtering is omitted (YAGNI): only one accrual window is requested, so accounting-mode filtering alone removes the duplicate.

**Placeholder scan:** none — every code step has complete code; the only runtime-discovered value (the exact accounting-mode column name) has an explicit live-verification task with a correction path.

**Type/name consistency:** `_spend_rows_from_aggregated`, `_spend_via_export`, `northbeam_spend`, `northbeam_list_uploaded_spend`, `_select_accounting_partition`, `partition_column_candidates`, `PARTITION_COLUMN_ALIASES`, `SPEND_EXPORT_METRICS`, `PORTFOLIO_EXPORT_METRICS` are used identically across the tasks that define and consume them. `_aggregate_export_rows(..., accounting_mode=None)` is backward-compatible with existing direct-call tests.
