from __future__ import annotations

import logging
import sys
from collections import defaultdict
from datetime import date as date_type, timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from server.client import NorthbeamClient, NorthbeamAuthError
from server.config import NorthbeamConfig, NorthbeamConfigError, load_config
from server.data_export import (
    breakdown_column_candidates,
    build_breakdown_value_lookup,
    build_data_export_payload,
    date_column_candidates,
    extract_download_url,
    extract_export_id,
    metric_column_candidates,
    partition_column_candidates,
)

logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
logger = logging.getLogger("northbeam-mcp")

mcp = FastMCP("northbeam")

AUTH_ERROR_MSG = (
    "Authentication failed. Your NORTHBEAM_API_KEY or NORTHBEAM_CLIENT_ID "
    "may be missing or invalid. Run /northbeam:setup to check credentials."
)

MAX_RESULT_ROWS = 200
# Daily (per-day) spend results are intrinsically larger: a month of
# campaign-level data is ~100+ campaigns x ~30 days. Cap generously so a typical
# anomaly (14-day) or fatigue (28-day) pull is not silently truncated, while
# still bounding the response. Callers should check `truncated`/`total_count`.
MAX_DAILY_RESULT_ROWS = 5000
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

# Requesting any of these makes the Data Export fan out into one row per
# accounting mode (accrual + cash), repeating spend on each.
REVENUE_EXPORT_METRICS = {"rev", "revattributed", "revenue", "roas"}

_SPEND_COMPONENT_NAMES = ("spend", "cost")
_REVENUE_COMPONENT_NAMES = ("revattributed", "rev", "revenue")
_TXN_COMPONENT_NAMES = ("txns", "transactions", "orders", "conversions", "conversion")

# Ratio metrics, recomputed from summed additive components when those inputs are
# present in the aggregated group: (numerator_names, denominator_names, scale).
# A per-row ratio cannot be summed or averaged across raw rows, so a group
# spanning multiple raw rows (daily granularity, multi-campaign platforms, or
# revenue fan-out) must rebuild the ratio from its additive parts. Computing it
# the same way for single- and multi-row groups also keeps the two consistent.
_RATIO_METRIC_COMPONENTS = {
    "roas": (_REVENUE_COMPONENT_NAMES, _SPEND_COMPONENT_NAMES, 1.0),
    "cac": (_SPEND_COMPONENT_NAMES, _TXN_COMPONENT_NAMES, 1.0),
    "cpc": (_SPEND_COMPONENT_NAMES, ("clicks",), 1.0),
    "ecpc": (_SPEND_COMPONENT_NAMES, ("clicks",), 1.0),
    "cpm": (_SPEND_COMPONENT_NAMES, ("impressions",), 1000.0),
    "ctr": (("clicks",), ("impressions",), 100.0),
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _is_additive_export_metric(metric: str) -> bool:
    return metric.lower() in ADDITIVE_EXPORT_METRICS


def _metrics_contain(metrics: list[str] | None, names: set[str]) -> bool:
    return any(metric.lower() in names for metric in (metrics or []))


def _row_revenue(row: dict[str, Any]) -> float | None:
    """Return a row's attributed revenue from whichever revenue key is present
    (revAttributed preferred), or None when the row carries no revenue."""
    for name in ("revAttributed", "rev", "revenue"):
        if row.get(name) is not None:
            return _safe_float(row.get(name))
    return None


def _rows_have_revenue(rows: list[dict[str, Any]]) -> bool:
    return any(_row_revenue(row) is not None for row in rows)


def _recompute_ratio_metric(metric: str, group: dict[str, Any]) -> float | None:
    """Recompute a ratio metric from the group's summed additive components.

    Returns None when the numerator or denominator inputs are absent from the
    group or the denominator is zero, so the caller can fall back to a single
    passthrough value (or null for genuinely un-summable multi-row groups)."""
    spec = _RATIO_METRIC_COMPONENTS.get(metric.lower())
    if spec is None:
        return None
    numerator_names, denominator_names, scale = spec
    sums = {
        key.lower(): float(value)
        for key, value in group.items()
        if key not in ("_count", "_metric_values") and isinstance(value, (int, float))
    }
    numerator = next((sums[n] for n in numerator_names if n in sums), None)
    denominator = next((sums[n] for n in denominator_names if n in sums), None)
    if numerator is None or denominator is None or denominator == 0:
        return None
    return round((numerator / denominator) * scale, 2)


def _first_row_value(row: dict[str, Any], candidates: list[str], default: Any = "") -> Any:
    for candidate in candidates:
        if candidate in row:
            return row.get(candidate)
    return default


def _enrich_spend_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        spend = _safe_float(row.get("spend"))
        clicks = _safe_float(row.get("clicks"))
        impressions = _safe_float(row.get("impressions"))

        row["cpc"] = round(spend / clicks, 2) if clicks > 0 else None
        row["cpm"] = round((spend / impressions) * 1000, 2) if impressions > 0 else None
        row["ctr"] = round((clicks / impressions) * 100, 2) if impressions > 0 else None

    return rows


def _spend_rows_from_aggregated(
    aggregated: list[dict[str, Any]],
    breakdown_key: str = "platform",
    campaign_key: str | None = None,
) -> list[dict[str, Any]]:
    """Convert aggregated Data Export rows into the legacy spend-row shape
    (`platform_name`, optionally `campaign_name`, `spend`, `impressions`,
    `clicks`) that the analyze capabilities consume. Impressions come from the
    `imprs`-backed `impressions` metric. Clicks come from the aggregator's
    summed per-raw-row `clicks` (each row's spend / ecpc) when present; otherwise
    they fall back to spend / aggregated-ecpc for single-row groups. When
    `campaign_key` is set (campaign-level exports), each row also carries
    `campaign_name`. When the aggregated entry carries a `date` (daily
    granularity), it is passed through so callers get a per-day time series."""
    rows: list[dict[str, Any]] = []
    for entry in aggregated:
        spend = _safe_float(entry.get("spend"))
        impressions = _safe_float(entry.get("impressions"))
        if "clicks" in entry:
            # Aggregator summed per-raw-row clicks (spend/ecpc); use directly,
            # since an aggregated ecpc is nulled across multi-row groups.
            clicks = _safe_float(entry.get("clicks"))
        else:
            ecpc = _safe_float(entry.get("ecpc"))
            clicks = spend / ecpc if ecpc > 0 else 0.0
        row = {
            "platform_name": entry.get(breakdown_key) or "Unknown",
            "spend": spend,
            "impressions": impressions,
            "clicks": clicks,
        }
        if "date" in entry:
            row["date"] = entry.get("date")
        if campaign_key:
            row["campaign_name"] = entry.get(campaign_key) or "Unknown"
        # Pass through attributed revenue when the export carried it (portfolio
        # health requests revAttributed), so downstream blended/per-platform ROAS
        # can be computed. Spend-only entries have no revenue and are unchanged.
        revenue = _row_revenue(entry)
        if revenue is not None:
            row["revAttributed"] = revenue
        rows.append(row)
    return _enrich_spend_rows(rows)


async def _list_spend(
    config: NorthbeamConfig | None = None,
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
    """Query Northbeam spend records with optional filters and pagination."""
    try:
        if config is None:
            config = load_config()
        async with NorthbeamClient(config) as client:
            result = await client.list_spend(
                date=date,
                date_start=date_start,
                date_end=date_end,
                platform_account_id=platform_account_id,
                campaign_id=campaign_id,
                adset_id=adset_id,
                ad_id=ad_id,
                page=page,
                page_size=page_size,
                fetch_all=fetch_all,
            )

        if platform_name and result.get("data"):
            needle = platform_name.lower()
            filtered = [
                r for r in result["data"]
                if (r.get("platform_name") or "").lower() == needle
            ]
            result["data"] = filtered
            result["total_count"] = len(filtered)

        if result.get("data"):
            _enrich_spend_rows(result["data"])

        return result
    except (NorthbeamAuthError, NorthbeamConfigError):
        raise ToolError(AUTH_ERROR_MSG) from None
    except ToolError:
        raise
    except Exception as e:
        logger.error("list_spend error: %s", e)
        raise ToolError(f"Error querying Northbeam: {e}")


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


async def _run_metadata_sanity_probe(client: NorthbeamClient) -> str:
    await client.list_export_options()
    return "Data Export metadata: OK - metrics, breakdowns, attribution models available"


def _exception_group_contains_auth_error(error: ExceptionGroup) -> bool:
    for exc in error.exceptions:
        if isinstance(exc, (NorthbeamAuthError, NorthbeamConfigError)):
            return True
        if isinstance(exc, ExceptionGroup) and _exception_group_contains_auth_error(exc):
            return True
    return False


def _format_exception_message(error: BaseException) -> str:
    if isinstance(error, ExceptionGroup):
        for exc in error.exceptions:
            return _format_exception_message(exc)
    return str(error)


def _data_export_failure_message(error: BaseException) -> str:
    message = _format_exception_message(error)
    if isinstance(error, (NorthbeamAuthError, NorthbeamConfigError)):
        return f"authentication/configuration failed: {message}"
    if isinstance(error, ExceptionGroup) and _exception_group_contains_auth_error(error):
        return f"authentication/configuration failed: {message}"
    return message


def _set_data_export_failure_lines(
    metadata_line: str | None,
    error: BaseException,
) -> tuple[str, str]:
    message = _data_export_failure_message(error)
    if metadata_line is None:
        return (
            f"Data Export metadata: Failed - {message}",
            "Data Export API: Skipped - metadata check failed",
        )
    return metadata_line, f"Data Export API: Failed - {message}"


async def _check_connection(
    config: NorthbeamConfig | None = None,
    check_date: str | None = None,
) -> str:
    """Check Northbeam API connectivity and report spend and outcome surfaces."""
    try:
        if config is None:
            config = load_config()
        yesterday = check_date or (date_type.today() - timedelta(days=1)).isoformat()
        async with NorthbeamClient(config) as client:
            result = await client.list_spend(date=yesterday, page_size=1000)
            status = "Connected"
            metadata_line: str | None = None
            outcome_line = "Data Export API: Skipped"
            outcome = {"transactions": 0.0, "revenue": 0.0}

            try:
                metadata_line = await _run_metadata_sanity_probe(client)
                outcome = await _run_outcome_sanity_probe(client, check_date=yesterday)
                outcome_line = (
                    "Data Export API: OK - "
                    f"transactions={outcome['transactions']:.2f}, "
                    f"revenue={outcome['revenue']:.2f} for {yesterday}"
                )
            except (NorthbeamAuthError, NorthbeamConfigError) as e:
                status = "Partially connected"
                metadata_line, outcome_line = _set_data_export_failure_lines(
                    metadata_line, e
                )
            except ExceptionGroup as eg:
                status = "Partially connected"
                metadata_line, outcome_line = _set_data_export_failure_lines(
                    metadata_line, eg
                )
            except Exception as e:
                status = "Partially connected"
                metadata_line, outcome_line = _set_data_export_failure_lines(
                    metadata_line, e
                )

        platforms = sorted(set(
            r.get("platform_name") for r in (result.get("data") or [])
            if r.get("platform_name")
        ))
        record_count = result.get("total_count") or 0
        platform_text = (
            ", ".join(platforms)
            if platforms
            else f"none (no spend rows for {yesterday})"
        )

        lines = [
            f"Status: {status}",
            f"Environment: {config.environment}",
            f"Uploaded Spend API: OK - {record_count} uploaded spend rows for "
            f"{yesterday} (0 is normal for natively-integrated accounts)",
            metadata_line or "Data Export metadata: Skipped",
            outcome_line,
            f"Platforms with uploaded spend: {platform_text}",
            "Note: real ad spend comes from the Data Export API (northbeam_spend); "
            "the Uploaded Spend API only returns customer-uploaded, non-integrated spend.",
        ]
        if status == "Partially connected":
            lines.append(
                "Note: Spend API credentials worked, but outcome metrics may be "
                "unavailable until Data Export is fixed."
            )
        if status == "Connected" and record_count == 0 and (
            outcome["transactions"] > 0 or outcome["revenue"] > 0
        ):
            lines.append(
                "Outcome data exists even though spend rows are zero; this usually means "
                "the Spend API has no ad spend records for that date, not that orders are missing."
            )
        return "\n".join(lines)

    except (NorthbeamAuthError, NorthbeamConfigError):
        raise ToolError(
            "Status: Not connected - authentication failed. "
            "Run /northbeam:setup for configuration instructions."
        )
    except ToolError:
        raise
    except Exception as e:
        logger.error("check_connection error: %s", e)
        raise ToolError(f"Status: Not connected - {e}")


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


async def _list_options(config: NorthbeamConfig | None = None) -> dict[str, Any]:
    """Fetch available breakdowns, metrics, and attribution models."""
    try:
        if config is None:
            config = load_config()
        async with NorthbeamClient(config) as client:
            return await client.list_export_options()
    except (NorthbeamAuthError, NorthbeamConfigError):
        raise ToolError(AUTH_ERROR_MSG) from None
    except ExceptionGroup as eg:
        if any(isinstance(e, (NorthbeamAuthError, NorthbeamConfigError)) for e in eg.exceptions):
            raise ToolError(AUTH_ERROR_MSG) from None
        logger.error("list_options error: %r", eg)
        raise ToolError(f"Error fetching export options: {eg.exceptions[0]}") from None
    except ToolError:
        raise
    except Exception as e:
        logger.error("list_options error: %s", e)
        raise ToolError(f"Error fetching export options: {e}")


SPEND_EXPORT_METRICS = ["spend", "impressions", "ecpc"]
_VALID_SPEND_BREAKDOWNS = ("platform", "campaign")
_VALID_SPEND_GRANULARITIES = ("total", "daily")


async def _spend_via_export(
    config: NorthbeamConfig | None = None,
    date_start: str = "",
    date_end: str = "",
    platform_name: str | None = None,
    breakdown: str = "platform",
    attribution_model: str = "northbeam_custom",
    attribution_window: str = "1",
    time_granularity: str = "total",
) -> dict[str, Any]:
    """Source spend + efficiency (impressions, clicks, CPC, CPM, CTR) from the
    Data Export API, by platform (default) or by campaign. Spend is
    attribution-independent; the defaults match the account's UI default.

    breakdown="campaign" sends level=campaign (campaign is the export level, NOT
    a Northbeam breakdown dimension) with a Platform payload breakdown, then
    aggregates on platform + campaign_name so campaigns never collapse.

    time_granularity="total" (default) returns one period-total row per group;
    "daily" runs the export with export_aggregation="DATE" and returns one row
    per group per day (each carrying a `date`), for time-series analysis
    (anomalies, fatigue). Daily results use the larger MAX_DAILY_RESULT_ROWS cap.

    Results are capped (MAX_RESULT_ROWS for totals, MAX_DAILY_RESULT_ROWS for
    daily); check `truncated` and narrow by platform_name or date range if set."""
    breakdown = (breakdown or "platform").lower()
    if breakdown not in _VALID_SPEND_BREAKDOWNS:
        raise ToolError(
            f"Unsupported breakdown {breakdown!r}; use 'platform' or 'campaign'."
        )
    time_granularity = (time_granularity or "total").lower()
    if time_granularity not in _VALID_SPEND_GRANULARITIES:
        raise ToolError(
            f"Unsupported time_granularity {time_granularity!r}; use 'total' or "
            "'daily'."
        )
    if breakdown == "campaign":
        level = "campaign"
        aggregation_breakdowns = ["platform", "campaign_name"]
        campaign_key = "campaign_name"
    else:
        level = "platform"
        aggregation_breakdowns = ["platform"]
        campaign_key = None

    if time_granularity == "daily":
        export_aggregation = "DATE"
        group_by_date = True
        row_cap = MAX_DAILY_RESULT_ROWS
    else:
        export_aggregation = "BREAKDOWN"
        group_by_date = False
        row_cap = MAX_RESULT_ROWS

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
                level=level,
                export_aggregation=export_aggregation,
            )
            aggregated = await _run_export_pipeline(
                client, body,
                breakdowns=aggregation_breakdowns,
                metrics=SPEND_EXPORT_METRICS,
                group_by_date=group_by_date,
            )

        rows = _spend_rows_from_aggregated(aggregated, campaign_key=campaign_key)
        if platform_name:
            needle = platform_name.lower()
            rows = [r for r in rows if (r.get("platform_name") or "").lower() == needle]

        total_count = len(rows)
        truncated = total_count > row_cap
        return {
            "data": rows[:row_cap],
            "total_count": total_count,
            "truncated": truncated,
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
    breakdown: str = "platform",
    attribution_model: str = "northbeam_custom",
    attribution_window: str = "1",
    time_granularity: str = "total",
) -> dict[str, Any]:
    """Query ad spend and efficiency (spend, impressions, clicks, CPC, CPM, CTR)
    from the Northbeam Data Export API. This is the source of truth for spend on
    natively-integrated accounts (Facebook/Google/TikTok).

    Date format: YYYY-MM-DD. platform_name filters results (case-insensitive).
    breakdown="platform" (default) returns one row per platform; "campaign"
    returns one row per campaign (with platform_name + campaign_name) for
    campaign-level analysis (anomalies, fatigue, naming intelligence).

    time_granularity="total" (default) returns one period-total row per group;
    "daily" returns one row per group per day, each with a `date` (YYYY-MM-DD),
    for time-series analysis like anomaly detection and diminishing-returns/
    fatigue trends. Combine breakdown="campaign" with time_granularity="daily"
    for per-campaign daily series.

    Defaults match the account's UI default (Clicks only / 1-day / accrual);
    spend itself is attribution-independent.
    """
    return await _spend_via_export(
        date_start=date_start,
        date_end=date_end,
        platform_name=platform_name,
        breakdown=breakdown,
        attribution_model=attribution_model,
        attribution_window=attribution_window,
        time_granularity=time_granularity,
    )


def _detect_accounting_fanout(
    rows: list[dict[str, str]],
    breakdowns: list[str],
) -> bool:
    """Heuristically detect revenue fan-out when no accounting-mode column is
    present to dedupe on. Fan-out repeats each breakdown's row once per
    accounting mode with identical spend, so a breakdown group containing two or
    more rows sharing the same spend value is the signature. Used only as a
    backstop: Northbeam normally returns the accounting-mode column, which the
    column-based path dedupes directly."""
    if not rows:
        return False
    spend_candidates = metric_column_candidates("spend")
    groups: dict[tuple, list[str]] = defaultdict(list)
    for row in rows:
        key = tuple(
            _first_row_value(row, breakdown_column_candidates(b), default="")
            for b in breakdowns
        )
        spend_value = _first_row_value(row, spend_candidates, default=None)
        if spend_value is not None:
            groups[key].append(str(spend_value).strip())
    for spend_values in groups.values():
        if len(spend_values) - len(set(spend_values)) >= 1:
            return True
    return False


def _select_accounting_partition(
    rows: list[dict[str, str]],
    accounting_mode: str | None,
    *,
    metrics: list[str] | None = None,
    breakdowns: list[str] | None = None,
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
        # No column to dedupe on. If both spend and a revenue metric were
        # requested, fan-out would silently double spend — fail loud when the
        # structural signature is present rather than return doubled numbers.
        revenue_requested = _metrics_contain(metrics, REVENUE_EXPORT_METRICS)
        spend_requested = _metrics_contain(metrics, set(_SPEND_COMPONENT_NAMES))
        if (
            revenue_requested
            and spend_requested
            and _detect_accounting_fanout(rows, breakdowns or [])
        ):
            raise ToolError(
                "Data Export appears to have fanned out into multiple accounting "
                "modes (duplicate spend per breakdown) but no accounting-mode "
                "column was found to dedupe on; refusing to aggregate to avoid "
                "double-counting spend. Add the accounting-mode column name to "
                "data_export.PARTITION_COLUMN_ALIASES['accounting_mode']."
            )
        return rows
    needle = accounting_mode.strip().lower()
    filtered = [
        row for row in rows
        if str(row.get(column, "")).strip().lower().startswith(needle)
    ]
    if filtered:
        return filtered

    # Nothing matched the requested mode. If several distinct modes are present,
    # summing them would double spend (the very fan-out this guard prevents) and
    # we cannot tell which rows to keep — fail loud rather than return
    # silently-doubled numbers. A single unmatched mode cannot double spend, so
    # keep those rows with a warning.
    distinct = sorted({str(row.get(column, "")).strip() for row in rows})
    if len(distinct) > 1:
        raise ToolError(
            f"Data Export returned multiple accounting modes {distinct} in column "
            f"{column!r}, none matching the requested mode {accounting_mode!r}; "
            f"refusing to aggregate to avoid double-counting spend. Verify the "
            f"accounting-mode value format in data_export.PARTITION_COLUMN_ALIASES."
        )
    logger.warning(
        "accounting-mode column %r present with a single unmatched mode %r "
        "(requested %r); keeping rows (no fan-out to drop)",
        column, distinct[0] if distinct else "", accounting_mode,
    )
    return rows


def _aggregate_export_rows(
    rows: list[dict[str, str]],
    breakdowns: list[str],
    metrics: list[str],
    accounting_mode: str | None = None,
    derive_clicks: bool = False,
    group_by_date: bool = False,
) -> list[dict[str, Any]]:
    """Aggregate raw CSV rows by breakdown keys, after dropping fan-out
    duplicate accounting-mode rows. When `derive_clicks` is set (the spend and
    portfolio paths), a summed per-raw-row `clicks` field is attached; generic
    exports leave it off so the response contract stays exactly the requested
    metrics. When `group_by_date` is set (daily granularity, the export is run
    with export_aggregation="DATE"), the per-row `date` column is added to the
    group key and emitted on each entry, so the same breakdown on different days
    stays as separate rows forming a per-day time series."""
    if rows:
        # Fail loud when a requested breakdown, additive metric, or the date
        # column does not resolve to any CSV column: _first_row_value would
        # otherwise silently fall back to a default (empty/zero), producing
        # confidently-wrong totals that look valid. Ratio metrics are exempt —
        # they may be recomputed from components rather than read from a column.
        sample = rows[0]
        for b in breakdowns:
            if not any(c in sample for c in breakdown_column_candidates(b)):
                raise ToolError(
                    f"Breakdown {b!r} did not match any column in the Data Export "
                    f"(columns: {sorted(sample)}); tried {breakdown_column_candidates(b)}. "
                    f"Add the live column name to data_export.BREAKDOWN_COLUMN_ALIASES."
                )
        for m in metrics:
            if not _is_additive_export_metric(m):
                continue
            if not any(c in sample for c in metric_column_candidates(m)):
                raise ToolError(
                    f"Metric {m!r} did not match any column in the Data Export "
                    f"(columns: {sorted(sample)}); tried {metric_column_candidates(m)}. "
                    f"Add the live column name to data_export.METRIC_COLUMN_ALIASES."
                )
        if group_by_date and not any(c in sample for c in date_column_candidates()):
            raise ToolError(
                "Daily granularity requested (export_aggregation='DATE') but no date "
                f"column was found in the Data Export (columns: {sorted(sample)}); "
                f"tried {date_column_candidates()}. Add it to "
                "data_export.DATE_COLUMN_CANDIDATES."
            )

    rows = _select_accounting_partition(
        rows, accounting_mode, metrics=metrics, breakdowns=breakdowns
    )
    groups: dict[tuple, dict[str, Any]] = defaultdict(
        lambda: {"_count": 0}
    )

    # `ecpc` (cost per click) is a per-row ratio, so an aggregated ecpc is null
    # for any group spanning multiple raw rows (e.g. daily granularity). Derive
    # clicks at the raw-row level (clicks = spend / ecpc) and sum them — an
    # additive quantity that survives aggregation — so CPC/CTR reconcile.
    derive_clicks = derive_clicks and "ecpc" in metrics and "spend" in metrics

    for row in rows:
        breakdown_key = tuple(
            _first_row_value(row, breakdown_column_candidates(b))
            for b in breakdowns
        )
        if group_by_date:
            date_value = _first_row_value(row, date_column_candidates(), default="")
            key: tuple = (date_value, *breakdown_key)
        else:
            key = breakdown_key
        group = groups[key]
        group["_count"] += 1
        if derive_clicks:
            row_spend = _safe_float(
                _first_row_value(row, metric_column_candidates("spend"), default=0)
            )
            row_ecpc = _safe_float(
                _first_row_value(row, metric_column_candidates("ecpc"), default=0)
            )
            if row_ecpc > 0:
                group["clicks"] = group.get("clicks", 0.0) + row_spend / row_ecpc
        for m in metrics:
            metric_value = _first_row_value(row, metric_column_candidates(m), default=0)
            value = _safe_float(metric_value)
            if _is_additive_export_metric(m):
                group[m] = group.get(m, 0.0) + value
            else:
                metric_values = group.setdefault("_metric_values", {})
                metric_values.setdefault(m, []).append(value)

    aggregated: list[dict[str, Any]] = []
    for key, group in groups.items():
        if group_by_date:
            date_value = key[0]
            breakdown_values = key[1:]
        else:
            date_value = None
            breakdown_values = key
        entry: dict[str, Any] = dict(zip(breakdowns, breakdown_values))
        if group_by_date:
            entry["date"] = date_value
        # Additive metrics first so the group's summed components are in place
        # before any ratio is rebuilt from them.
        for m in metrics:
            if _is_additive_export_metric(m):
                entry[m] = round(group.get(m, 0.0), 2)
        if derive_clicks:
            entry["clicks"] = group.get("clicks", 0.0)
        # Ratio metrics: recompute from the summed additive components when those
        # inputs are present (correct for multi-row groups, where a raw per-row
        # ratio cannot be summed or averaged). Fall back to the single passthrough
        # value for one-row groups, or null when a multi-row group lacks the
        # components to rebuild the ratio.
        for m in metrics:
            if _is_additive_export_metric(m):
                continue
            recomputed = _recompute_ratio_metric(m, group)
            if recomputed is not None:
                entry[m] = recomputed
                continue
            values = group.get("_metric_values", {}).get(m, [])
            entry[m] = round(values[0], 2) if len(values) == 1 else None
        entry["_row_count"] = group["_count"]
        aggregated.append(entry)

    primary_metric = metrics[0] if metrics else None
    if group_by_date:
        # Chronological series, ties broken by the primary metric descending.
        aggregated.sort(
            key=lambda r: (
                r.get("date") or "",
                -(_safe_float(r.get(primary_metric)) if primary_metric else 0),
            )
        )
    else:
        aggregated.sort(
            key=lambda r: _safe_float(r.get(primary_metric)) if primary_metric else 0,
            reverse=True,
        )
    return aggregated


async def _build_export_body(
    client: NorthbeamClient,
    *,
    date_start: str,
    date_end: str,
    metrics: list[str],
    breakdowns: list[str],
    attribution_model: str,
    attribution_window: str,
    level: str = "platform",
    export_aggregation: str = "BREAKDOWN",
) -> dict[str, Any]:
    try:
        breakdown_values = None
        if breakdowns:
            options = await client.list_export_options()
            breakdown_values = build_breakdown_value_lookup(
                {"breakdowns": options["breakdowns"]}
            )

        return build_data_export_payload(
            date_start=date_start,
            date_end=date_end,
            metrics=metrics,
            breakdowns=breakdowns,
            attribution_model=attribution_model,
            attribution_window=attribution_window,
            breakdown_values=breakdown_values,
            level=level,
            export_aggregation=export_aggregation,
        )
    except ValueError as e:
        raise ToolError(str(e)) from None


async def _data_export(
    config: NorthbeamConfig | None = None,
    date_start: str = "",
    date_end: str = "",
    metrics: list[str] | None = None,
    breakdowns: list[str] | None = None,
    attribution_model: str = "northbeam_custom",
    attribution_window: str = "1",
) -> dict[str, Any]:
    """Run a full Data Export: create → poll → download → aggregate."""
    try:
        if config is None:
            config = load_config()
        effective_metrics = metrics or []
        effective_breakdowns = breakdowns or []

        async with NorthbeamClient(config) as client:
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

            csv_result = await client.download_export_csv(download_url)
            raw_rows = csv_result["data"]
            total_rows = csv_result["total_rows"]

        accounting_modes = (
            body.get("attribution_options", {}).get("accounting_modes") or []
        )
        accounting_mode = accounting_modes[0] if accounting_modes else None
        aggregated = _aggregate_export_rows(
            raw_rows, effective_breakdowns, effective_metrics,
            accounting_mode=accounting_mode,
        ) if raw_rows else []

        total_groups = len(aggregated)
        truncated = total_groups > MAX_RESULT_ROWS
        data = aggregated[:MAX_RESULT_ROWS]

        return {
            "summary": {
                "total_raw_rows": total_rows,
                "aggregated_groups": total_groups,
                "returned_rows": len(data),
                "truncated": truncated,
                "date_range": {"start": date_start, "end": date_end},
                "attribution_model": attribution_model,
                "attribution_window": attribution_window,
                "breakdowns": effective_breakdowns,
                "metrics": effective_metrics,
                "non_additive_metrics": [
                    metric for metric in effective_metrics
                    if not _is_additive_export_metric(metric)
                ],
            },
            "data": data,
        }

    except (NorthbeamAuthError, NorthbeamConfigError):
        raise ToolError(AUTH_ERROR_MSG) from None
    except ExceptionGroup as eg:
        if any(isinstance(e, (NorthbeamAuthError, NorthbeamConfigError)) for e in eg.exceptions):
            raise ToolError(AUTH_ERROR_MSG) from None
        logger.error("data_export error: %r", eg)
        raise ToolError(f"Error running data export: {eg.exceptions[0]}") from None
    except ToolError:
        raise
    except Exception as e:
        logger.error("data_export error: %s", e)
        raise ToolError(f"Error running data export: {e}")


@mcp.tool()
async def northbeam_check_connection() -> str:
    """Check Northbeam API connectivity and report the environment (prod/uat).

    Validates three surfaces: the Uploaded Spend API (a zero row count is normal
    for natively-integrated accounts — real ad spend comes from the Data Export
    API), Data Export metadata, and a small Data Export outcome probe
    (transactions + revAttributed). Also lists any platforms with uploaded spend.
    """
    return await _check_connection()


@mcp.tool()
async def northbeam_list_options() -> dict[str, Any]:
    """List available breakdowns, metrics, and attribution models
    for the Northbeam Data Export API. Use this to discover valid
    parameter values before calling northbeam_data_export."""
    return await _list_options()


@mcp.tool()
async def northbeam_data_export(
    date_start: str,
    date_end: str,
    metrics: list[str],
    breakdowns: list[str],
    attribution_model: str = "northbeam_custom",
    attribution_window: str = "1",
) -> dict[str, Any]:
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
    return await _data_export(
        date_start=date_start,
        date_end=date_end,
        metrics=metrics,
        breakdowns=breakdowns,
        attribution_model=attribution_model,
        attribution_window=attribution_window,
    )


def _compute_blended_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_spend = sum(_safe_float(r.get("spend")) for r in rows)
    total_clicks = sum(_safe_float(r.get("clicks")) for r in rows)
    total_impressions = sum(_safe_float(r.get("impressions")) for r in rows)

    # Only roll up revenue/ROAS when at least one row actually carried revenue,
    # so spend-only portfolios report blended_roas=None rather than a misleading 0.
    revenue_values = [_row_revenue(r) for r in rows]
    revenue_present = any(v is not None for v in revenue_values)
    total_revenue = sum(v for v in revenue_values if v is not None)

    return {
        "total_spend": round(total_spend, 2),
        "total_clicks": round(total_clicks),
        "total_impressions": round(total_impressions),
        "blended_cpc": round(total_spend / total_clicks, 2) if total_clicks > 0 else None,
        "blended_cpm": round((total_spend / total_impressions) * 1000, 2) if total_impressions > 0 else None,
        "blended_ctr": round((total_clicks / total_impressions) * 100, 2) if total_impressions > 0 else None,
        "blended_roas": round(total_revenue / total_spend, 2) if (revenue_present and total_spend > 0) else None,
    }


def _aggregate_spend_by_platform(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    platforms: dict[str, dict[str, float]] = {}

    for row in rows:
        platform = row.get("platform_name") or "Unknown"
        if platform not in platforms:
            platforms[platform] = {
                "spend": 0.0, "clicks": 0.0, "impressions": 0.0,
                "revenue": 0.0, "revenue_present": False,
            }
        p = platforms[platform]
        p["spend"] += _safe_float(row.get("spend"))
        p["clicks"] += _safe_float(row.get("clicks"))
        p["impressions"] += _safe_float(row.get("impressions"))
        revenue = _row_revenue(row)
        if revenue is not None:
            p["revenue"] += revenue
            p["revenue_present"] = True

    result = []
    for platform, totals in sorted(platforms.items(), key=lambda x: x[1]["spend"], reverse=True):
        spend = totals["spend"]
        clicks = totals["clicks"]
        impressions = totals["impressions"]
        revenue_present = totals["revenue_present"]
        result.append({
            "platform": platform,
            "spend": round(spend, 2),
            "clicks": round(clicks),
            "impressions": round(impressions),
            "cpc": round(spend / clicks, 2) if clicks > 0 else None,
            "cpm": round((spend / impressions) * 1000, 2) if impressions > 0 else None,
            "ctr": round((clicks / impressions) * 100, 2) if impressions > 0 else None,
            "roas": round(totals["revenue"] / spend, 2) if (revenue_present and spend > 0) else None,
            "spend_share": None,
        })

    total_spend = sum(p["spend"] for p in result)
    if total_spend > 0:
        for p in result:
            p["spend_share"] = round((p["spend"] / total_spend) * 100, 1)

    return result


async def _run_export_pipeline(
    client: NorthbeamClient,
    body: dict[str, Any],
    breakdowns: list[str] | None = None,
    metrics: list[str] | None = None,
    group_by_date: bool = False,
) -> list[dict[str, Any]]:
    create_result = await client.create_data_export(body)
    export_id = extract_export_id(create_result)
    if not export_id:
        raise ToolError("Northbeam API response missing export id")

    poll_result = await client.poll_export_result(export_id)
    download_url = extract_download_url(poll_result)
    if not download_url:
        raise ToolError("Northbeam API response missing download URL")

    csv_result = await client.download_export_csv(download_url)
    raw_rows = csv_result["data"]

    if raw_rows:
        effective_breakdowns = breakdowns
        if effective_breakdowns is None:
            raw_breakdowns = body.get("breakdowns", [])
            effective_breakdowns = [
                breakdown.get("key", "")
                if isinstance(breakdown, dict)
                else str(breakdown)
                for breakdown in raw_breakdowns
            ]

        effective_metrics = metrics
        if effective_metrics is None:
            raw_metrics = body.get("metrics", [])
            effective_metrics = [
                metric.get("id", "")
                if isinstance(metric, dict)
                else str(metric)
                for metric in raw_metrics
            ]

        accounting_modes = (
            body.get("attribution_options", {}).get("accounting_modes") or []
        )
        accounting_mode = accounting_modes[0] if accounting_modes else None
        return _aggregate_export_rows(
            raw_rows,
            effective_breakdowns,
            effective_metrics,
            accounting_mode=accounting_mode,
            derive_clicks=True,
            group_by_date=group_by_date,
        )
    return []


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
        logger.error("portfolio_health error: %r", eg)
        raise ToolError(
            f"Error building portfolio health: {_format_exception_message(eg)}"
        ) from None
    except Exception as e:
        logger.error("portfolio_health error: %s", e)
        raise ToolError(f"Error building portfolio health: {e}")


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


if __name__ == "__main__":
    mcp.run(transport="stdio")
