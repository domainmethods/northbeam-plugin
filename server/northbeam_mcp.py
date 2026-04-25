from __future__ import annotations

import logging
import sys
from datetime import date as date_type, timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from server.client import NorthbeamClient, NorthbeamAuthError
from server.config import NorthbeamConfig, load_config

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger("northbeam-mcp")

mcp = FastMCP("northbeam")


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
    except (NorthbeamAuthError, ValueError):
        raise ToolError(
            "Authentication failed. Your NORTHBEAM_API_KEY or NORTHBEAM_CLIENT_ID "
            "may be missing or invalid. Run /northbeam:setup to check credentials."
        )
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

    except (NorthbeamAuthError, ValueError):
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


@mcp.tool()
async def northbeam_check_connection() -> str:
    """Check Northbeam API connectivity. Validates credentials and reports
    the environment (prod/uat) and which ad platforms are visible."""
    return await _check_connection()


if __name__ == "__main__":
    mcp.run(transport="stdio")
