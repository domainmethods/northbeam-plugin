from __future__ import annotations

import asyncio
import logging
from datetime import date as date_type
from typing import Any

from mcp.server.fastmcp.exceptions import ToolError

from server.client import NorthbeamClient, NorthbeamAuthError
from server.config import NorthbeamConfig, NorthbeamConfigError, load_config

logger = logging.getLogger("northbeam-mcp")

AUTH_ERROR_MSG = (
    "Authentication failed. Your NORTHBEAM_API_KEY or NORTHBEAM_CLIENT_ID "
    "may be missing or invalid. Run /northbeam:setup to check credentials."
)


def _compute_blended_metrics(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute blended CPC, CPM, CTR across all rows."""
    total_spend = sum(float(r.get("spend") or 0) for r in rows)
    total_clicks = sum(float(r.get("clicks") or 0) for r in rows)
    total_impressions = sum(float(r.get("impressions") or 0) for r in rows)

    return {
        "total_spend": round(total_spend, 2),
        "total_clicks": int(total_clicks),
        "total_impressions": int(total_impressions),
        "blended_cpc": round(total_spend / total_clicks, 2) if total_clicks > 0 else None,
        "blended_cpm": round((total_spend / total_impressions) * 1000, 2) if total_impressions > 0 else None,
        "blended_ctr": round((total_clicks / total_impressions) * 100, 2) if total_impressions > 0 else None,
    }


def _aggregate_spend_by_platform(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Aggregate spend rows by platform with derived metrics."""
    platforms: dict[str, dict[str, float]] = {}

    for row in rows:
        platform = row.get("platform_name") or "Unknown"
        if platform not in platforms:
            platforms[platform] = {"spend": 0.0, "clicks": 0.0, "impressions": 0.0}
        p = platforms[platform]
        p["spend"] += float(row.get("spend") or 0)
        p["clicks"] += float(row.get("clicks") or 0)
        p["impressions"] += float(row.get("impressions") or 0)

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
            "spend_share": None,  # filled below
        })

    total_spend = sum(p["spend"] for p in result)
    if total_spend > 0:
        for p in result:
            p["spend_share"] = round((p["spend"] / total_spend) * 100, 1)

    return result


async def _portfolio_health(
    config: NorthbeamConfig | None = None,
    date_start: str = "",
    date_end: str = "",
    attribution_model: str = "northbeam_custom__va",
    attribution_window: str = "7",
) -> dict[str, Any]:
    """Run spend + data export concurrently for a holistic portfolio view."""
    try:
        if config is None:
            config = load_config()

        if not date_start or not date_end:
            today = date_type.today()
            date_end = date_end or today.isoformat()
            date_start = date_start or today.replace(day=1).isoformat()

        async with NorthbeamClient(config) as client:
            spend_task = asyncio.create_task(
                client.list_spend(
                    date_start=date_start,
                    date_end=date_end,
                    fetch_all=True,
                )
            )

            export_body = {
                "date_start": date_start,
                "date_end": date_end,
                "attribution_model": attribution_model,
                "attribution_window": attribution_window,
                "breakdowns": ["platform"],
                "metrics": ["revenue", "roas"],
            }
            export_task = asyncio.create_task(
                _run_export_pipeline(client, export_body)
            )

            spend_result, export_result = await asyncio.gather(
                spend_task, export_task, return_exceptions=True
            )

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
    except Exception as e:
        logger.error("portfolio_health error: %s", e)
        raise ToolError(f"Error building portfolio health: {e}")


async def _run_export_pipeline(
    client: NorthbeamClient,
    body: dict[str, Any],
) -> list[dict[str, Any]]:
    """Create export, poll, download, and aggregate by platform."""
    from server.northbeam_mcp import _aggregate_export_rows

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

    if raw_rows:
        return _aggregate_export_rows(
            raw_rows,
            body.get("breakdowns", []),
            body.get("metrics", []),
        )
    return []
