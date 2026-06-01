from __future__ import annotations

import asyncio
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
    extract_download_url,
    extract_export_id,
    metric_column_candidates,
)

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger("northbeam-mcp")

mcp = FastMCP("northbeam")

AUTH_ERROR_MSG = (
    "Authentication failed. Your NORTHBEAM_API_KEY or NORTHBEAM_CLIENT_ID "
    "may be missing or invalid. Run /northbeam:setup to check credentials."
)

MAX_RESULT_ROWS = 200


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


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
        metrics=["txns", "rev"],
        breakdowns=[],
    )
    rows = await _run_export_pipeline(
        client,
        body,
        breakdowns=[],
        metrics=["txns", "rev"],
    )
    totals = rows[0] if rows else {"txns": 0.0, "rev": 0.0}
    return {
        "transactions": _safe_float(totals.get("txns")),
        "revenue": _safe_float(totals.get("rev")),
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
            except (NorthbeamAuthError, NorthbeamConfigError):
                raise
            except ExceptionGroup as eg:
                if _exception_group_contains_auth_error(eg):
                    raise NorthbeamAuthError(str(eg)) from eg
                message = _format_exception_message(eg)
                status = "Partially connected"
                if metadata_line is None:
                    metadata_line = f"Data Export metadata: Failed - {message}"
                    outcome_line = "Data Export API: Skipped - metadata check failed"
                else:
                    outcome_line = f"Data Export API: Failed - {message}"
            except Exception as e:
                message = _format_exception_message(e)
                status = "Partially connected"
                if metadata_line is None:
                    metadata_line = f"Data Export metadata: Failed - {message}"
                    outcome_line = "Data Export API: Skipped - metadata check failed"
                else:
                    outcome_line = f"Data Export API: Failed - {message}"

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
async def northbeam_list_spend(
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
    """Query Northbeam spend records. Returns spend, clicks, impressions, and
    pre-computed efficiency metrics (CPC, CPM, CTR) per row.
    Filterable by date range, platform, campaign, adset, and ad.
    Use fetch_all=true to auto-paginate and retrieve all matching records.

    Date parameters: provide 'date' for a single day, or 'date_start'+'date_end'
    for a range. Format: YYYY-MM-DD.

    platform_name filters results server-side (case-insensitive). Example: 'Facebook'.
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


def _aggregate_export_rows(
    rows: list[dict[str, str]],
    breakdowns: list[str],
    metrics: list[str],
) -> list[dict[str, Any]]:
    """Aggregate raw CSV rows by breakdown keys, summing metric values."""
    groups: dict[tuple, dict[str, Any]] = defaultdict(
        lambda: {"_count": 0}
    )

    for row in rows:
        key = tuple(
            _first_row_value(row, breakdown_column_candidates(b))
            for b in breakdowns
        )
        group = groups[key]
        group["_count"] += 1
        for m in metrics:
            metric_value = _first_row_value(row, metric_column_candidates(m), default=0)
            group[m] = group.get(m, 0.0) + _safe_float(metric_value)

    aggregated: list[dict[str, Any]] = []
    for key, group in groups.items():
        entry: dict[str, Any] = dict(zip(breakdowns, key))
        for m in metrics:
            entry[m] = round(group.get(m, 0.0), 2)
        entry["_row_count"] = group["_count"]
        aggregated.append(entry)

    aggregated.sort(key=lambda r: r.get(metrics[0], 0) if metrics else 0, reverse=True)
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
        )
    except ValueError as e:
        raise ToolError(str(e)) from None


async def _data_export(
    config: NorthbeamConfig | None = None,
    date_start: str = "",
    date_end: str = "",
    metrics: list[str] | None = None,
    breakdowns: list[str] | None = None,
    attribution_model: str = "northbeam_custom__va",
    attribution_window: str = "7",
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

        aggregated = _aggregate_export_rows(
            raw_rows, effective_breakdowns, effective_metrics
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
    """Check Northbeam API connectivity. Validates credentials and reports
    the environment (prod/uat) and which ad platforms are visible."""
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
    attribution_model: str = "northbeam_custom__va",
    attribution_window: str = "7",
) -> dict[str, Any]:
    """Run a Northbeam Data Export for outcome metrics (revenue, ROAS, CAC,
    conversions, etc.) with flexible breakdowns and attribution settings.

    Use northbeam_list_options to discover valid metric/breakdown/model values.

    Returns aggregated data grouped by the requested breakdowns with summed
    metrics. Results capped at 200 rows (sorted by first metric descending).
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

    return {
        "total_spend": round(total_spend, 2),
        "total_clicks": int(total_clicks),
        "total_impressions": int(total_impressions),
        "blended_cpc": round(total_spend / total_clicks, 2) if total_clicks > 0 else None,
        "blended_cpm": round((total_spend / total_impressions) * 1000, 2) if total_impressions > 0 else None,
        "blended_ctr": round((total_clicks / total_impressions) * 100, 2) if total_impressions > 0 else None,
    }


def _aggregate_spend_by_platform(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    platforms: dict[str, dict[str, float]] = {}

    for row in rows:
        platform = row.get("platform_name") or "Unknown"
        if platform not in platforms:
            platforms[platform] = {"spend": 0.0, "clicks": 0.0, "impressions": 0.0}
        p = platforms[platform]
        p["spend"] += _safe_float(row.get("spend"))
        p["clicks"] += _safe_float(row.get("clicks"))
        p["impressions"] += _safe_float(row.get("impressions"))

    result = []
    for platform, totals in sorted(platforms.items(), key=lambda x: x[1]["spend"], reverse=True):
        spend = totals["spend"]
        clicks = totals["clicks"]
        impressions = totals["impressions"]
        result.append({
            "platform": platform,
            "spend": round(spend, 2),
            "clicks": int(clicks),
            "impressions": int(impressions),
            "cpc": round(spend / clicks, 2) if clicks > 0 else None,
            "cpm": round((spend / impressions) * 1000, 2) if impressions > 0 else None,
            "ctr": round((clicks / impressions) * 100, 2) if impressions > 0 else None,
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

        return _aggregate_export_rows(
            raw_rows,
            effective_breakdowns,
            effective_metrics,
        )
    return []


async def _portfolio_health(
    config: NorthbeamConfig | None = None,
    date_start: str = "",
    date_end: str = "",
    attribution_model: str = "northbeam_custom__va",
    attribution_window: str = "7",
) -> dict[str, Any]:
    try:
        if config is None:
            config = load_config()

        if not date_start or not date_end:
            today = date_type.today()
            date_end = date_end or today.isoformat()
            date_start = date_start or today.replace(day=1).isoformat()

        async with NorthbeamClient(config) as client:
            export_result: list[dict[str, Any]] | BaseException | None = None
            try:
                export_body = await _build_export_body(
                    client,
                    date_start=date_start,
                    date_end=date_end,
                    metrics=["rev", "roas"],
                    breakdowns=["platform"],
                    attribution_model=attribution_model,
                    attribution_window=attribution_window,
                )
            except (NorthbeamAuthError, NorthbeamConfigError):
                raise
            except ExceptionGroup as eg:
                if _exception_group_contains_auth_error(eg):
                    raise NorthbeamAuthError(str(eg)) from eg
                export_result = RuntimeError(_format_exception_message(eg))
            except Exception as e:
                export_result = e

            if export_result is None:
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
                spend_result, export_result = await asyncio.gather(
                    spend_task, export_task, return_exceptions=True
                )
            else:
                try:
                    spend_result = await client.list_spend(
                        date_start=date_start,
                        date_end=date_end,
                        fetch_all=True,
                    )
                except Exception as e:
                    spend_result = e

        if isinstance(spend_result, Exception):
            if isinstance(spend_result, (NorthbeamAuthError, NorthbeamConfigError)):
                raise ToolError(AUTH_ERROR_MSG) from None
            raise ToolError(f"Spend query failed: {spend_result}")

        spend_rows = spend_result.get("data") or []
        blended = _compute_blended_metrics(spend_rows)
        by_platform = _aggregate_spend_by_platform(spend_rows)

        outcome_data = None
        outcome_error = None
        if isinstance(export_result, Exception):
            outcome_error = str(export_result)
        else:
            outcome_data = export_result

        response: dict[str, Any] = {
            "summary": {
                "date_range": {"start": date_start, "end": date_end},
                "attribution_model": attribution_model,
                "record_count": len(spend_rows),
                **blended,
            },
            "spend_by_platform": by_platform,
        }

        if outcome_data is not None:
            response["outcomes"] = outcome_data
        if outcome_error is not None:
            response["outcome_error"] = outcome_error

        return response

    except (NorthbeamAuthError, NorthbeamConfigError):
        raise ToolError(AUTH_ERROR_MSG) from None
    except ToolError:
        raise
    except ExceptionGroup as eg:
        if _exception_group_contains_auth_error(eg):
            raise ToolError(AUTH_ERROR_MSG) from None
        logger.error("portfolio_health error: %s", eg)
        raise ToolError(f"Error building portfolio health: {_format_exception_message(eg)}")
    except Exception as e:
        logger.error("portfolio_health error: %s", e)
        raise ToolError(f"Error building portfolio health: {e}")


@mcp.tool()
async def northbeam_portfolio_health(
    date_start: str = "",
    date_end: str = "",
    attribution_model: str = "northbeam_custom__va",
    attribution_window: str = "7",
) -> dict[str, Any]:
    """Get a holistic portfolio health snapshot combining spend efficiency
    metrics (CPC, CPM, CTR) with outcome metrics (revenue, ROAS).

    Runs spend and data export queries concurrently for faster results.
    Defaults to month-to-date if no dates provided.

    Returns: blended metrics, per-platform breakdown with spend share,
    and outcome data aggregated by platform.
    """
    return await _portfolio_health(
        date_start=date_start,
        date_end=date_end,
        attribution_model=attribution_model,
        attribution_window=attribution_window,
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
