from __future__ import annotations

import logging
import sys
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
            return await client.list_spend(
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
    """
    return await _list_spend(
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


async def _data_export(
    config: NorthbeamConfig | None = None,
    date_start: str = "",
    date_end: str = "",
    metrics: list[str] | None = None,
    breakdowns: list[str] | None = None,
    attribution_model: str = "northbeam_custom__va",
    attribution_window: str = "7",
) -> dict[str, Any]:
    """Run a full Data Export: create → poll → download → summarize."""
    try:
        if config is None:
            config = load_config()
        async with NorthbeamClient(config) as client:
            body = {
                "date_start": date_start,
                "date_end": date_end,
                "attribution_model": attribution_model,
                "attribution_window": attribution_window,
                "breakdowns": breakdowns or [],
                "metrics": metrics or [],
            }
            create_result = await client.create_data_export(body)
            export_id = create_result["export_id"]

            poll_result = await client.poll_export_result(export_id)
            download_url = poll_result["download_url"]

            csv_result = await client.download_export_csv(download_url)
            rows = csv_result["data"]
            total_rows = csv_result["total_rows"]

        result: dict[str, Any] = {
            "summary": {
                "total_rows": total_rows,
                "date_range": {"start": date_start, "end": date_end},
                "attribution_model": attribution_model,
                "attribution_window": attribution_window,
                "columns": list(rows[0].keys()) if rows else [],
            },
            "data": rows,
        }
        if total_rows > len(rows):
            result["summary"]["note"] = (
                f"Showing {len(rows)} of {total_rows} rows. "
                "Narrow the date range or breakdowns to see all data."
            )
        return result

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

    Returns a summary with row count, date range, columns, and a sample of
    up to 20 rows. The full dataset is included when total rows ≤ 20.
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
