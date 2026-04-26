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

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger("northbeam-mcp")

mcp = FastMCP("northbeam")

AUTH_ERROR_MSG = (
    "Authentication failed. Your NORTHBEAM_API_KEY or NORTHBEAM_CLIENT_ID "
    "may be missing or invalid. Run /northbeam:setup to check credentials."
)


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

        return result
    except (NorthbeamAuthError, NorthbeamConfigError):
        raise ToolError(AUTH_ERROR_MSG) from None
    except ToolError:
        raise
    except Exception as e:
        logger.error("list_spend error: %s", e)
        raise ToolError(f"Error querying Northbeam: {e}")


async def _check_connection(config: NorthbeamConfig | None = None) -> str:
    """Check Northbeam API connectivity and report visible platforms."""
    try:
        if config is None:
            config = load_config()
        yesterday = (date_type.today() - timedelta(days=1)).isoformat()
        async with NorthbeamClient(config) as client:
            result = await client.list_spend(date=yesterday, page_size=1000)

        platforms = sorted(set(
            r.get("platform_name") for r in (result.get("data") or [])
            if r.get("platform_name")
        ))
        record_count = result.get("total_count") or 0

        lines = [
            "Status: Connected",
            f"Environment: {config.environment}",
            f"Records found (yesterday): {record_count}",
            f"Platforms visible: {', '.join(platforms) if platforms else 'none (no data for yesterday)'}",
        ]
        return "\n".join(lines)

    except (NorthbeamAuthError, NorthbeamConfigError):
        raise ToolError(
            "Status: Not connected — authentication failed. "
            "Run /northbeam:setup for configuration instructions."
        )
    except ToolError:
        raise
    except Exception as e:
        logger.error("check_connection error: %s", e)
        raise ToolError(f"Status: Not connected — {e}")


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
    """Query Northbeam spend records. Returns spend, clicks, and impressions data
    filterable by date range, platform, campaign, adset, and ad. Use fetch_all=true
    to auto-paginate and retrieve all matching records.

    Date parameters: provide 'date' for a single day, or 'date_start'+'date_end'
    for a range. Format: YYYY-MM-DD.

    platform_name filters results client-side (case-insensitive). Example: 'Facebook'.
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
        key = tuple(row.get(b, "") for b in breakdowns)
        group = groups[key]
        group["_count"] += 1
        for m in metrics:
            try:
                group[m] = group.get(m, 0.0) + float(row.get(m, 0))
            except (ValueError, TypeError):
                pass

    aggregated: list[dict[str, Any]] = []
    for key, group in groups.items():
        entry: dict[str, Any] = dict(zip(breakdowns, key))
        for m in metrics:
            entry[m] = round(group.get(m, 0.0), 2)
        entry["_row_count"] = group["_count"]
        aggregated.append(entry)

    aggregated.sort(key=lambda r: r.get(metrics[0], 0) if metrics else 0, reverse=True)
    return aggregated


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
            body = {
                "date_start": date_start,
                "date_end": date_end,
                "attribution_model": attribution_model,
                "attribution_window": attribution_window,
                "breakdowns": effective_breakdowns,
                "metrics": effective_metrics,
            }
            create_result = await client.create_data_export(body)
            export_id = create_result.get("export_id")
            if not export_id:
                raise ToolError("Northbeam API response missing 'export_id'")

            poll_result = await client.poll_export_result(export_id)
            download_url = poll_result.get("download_url")
            if not download_url:
                raise ToolError("Northbeam API response missing 'download_url'")

            csv_result = await client.download_export_csv(download_url)
            raw_rows = csv_result["data"]
            total_rows = csv_result["total_rows"]

        if effective_breakdowns and effective_metrics and raw_rows:
            aggregated = _aggregate_export_rows(
                raw_rows, effective_breakdowns, effective_metrics
            )
        else:
            aggregated = raw_rows

        return {
            "summary": {
                "total_raw_rows": total_rows,
                "aggregated_groups": len(aggregated),
                "date_range": {"start": date_start, "end": date_end},
                "attribution_model": attribution_model,
                "attribution_window": attribution_window,
                "breakdowns": effective_breakdowns,
                "metrics": effective_metrics,
            },
            "data": aggregated,
        }

    except (NorthbeamAuthError, NorthbeamConfigError):
        raise ToolError(AUTH_ERROR_MSG) from None
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
    metrics. All rows are included (no truncation).
    """
    return await _data_export(
        date_start=date_start,
        date_end=date_end,
        metrics=metrics,
        breakdowns=breakdowns,
        attribution_model=attribution_model,
        attribution_window=attribution_window,
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
