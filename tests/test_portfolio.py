import httpx
import pytest
import respx
from mcp.server.fastmcp.exceptions import ToolError

from server.northbeam_mcp import (
    _compute_blended_metrics,
    _aggregate_spend_by_platform,
    _portfolio_health,
)

import server.client as client_module


def test_compute_blended_metrics_basic():
    rows = [
        {"spend": 100, "clicks": 50, "impressions": 10000},
        {"spend": 200, "clicks": 100, "impressions": 20000},
    ]

    result = _compute_blended_metrics(rows)

    assert result["total_spend"] == 300.0
    assert result["total_clicks"] == 150
    assert result["total_impressions"] == 30000
    assert result["blended_cpc"] == 2.0
    assert result["blended_cpm"] == 10.0
    assert result["blended_ctr"] == 0.5


def test_compute_blended_metrics_zero_clicks():
    rows = [{"spend": 100, "clicks": 0, "impressions": 5000}]

    result = _compute_blended_metrics(rows)

    assert result["blended_cpc"] is None
    assert result["blended_cpm"] == 20.0
    assert result["blended_ctr"] == 0.0


def test_compute_blended_metrics_zero_impressions():
    rows = [{"spend": 100, "clicks": 0, "impressions": 0}]

    result = _compute_blended_metrics(rows)

    assert result["blended_cpc"] is None
    assert result["blended_cpm"] is None
    assert result["blended_ctr"] is None


def test_compute_blended_metrics_empty_rows():
    result = _compute_blended_metrics([])

    assert result["total_spend"] == 0.0
    assert result["blended_cpc"] is None
    assert result["blended_cpm"] is None
    assert result["blended_ctr"] is None


def test_aggregate_spend_by_platform():
    rows = [
        {"platform_name": "Facebook", "spend": 100, "clicks": 50, "impressions": 10000},
        {"platform_name": "Facebook", "spend": 200, "clicks": 100, "impressions": 20000},
        {"platform_name": "TikTok", "spend": 50, "clicks": 25, "impressions": 5000},
    ]

    result = _aggregate_spend_by_platform(rows)

    assert len(result) == 2
    fb = result[0]
    assert fb["platform"] == "Facebook"
    assert fb["spend"] == 300.0
    assert fb["clicks"] == 150
    assert fb["impressions"] == 30000
    assert fb["cpc"] == 2.0
    assert fb["cpm"] == 10.0
    assert fb["spend_share"] == 85.7

    tt = result[1]
    assert tt["platform"] == "TikTok"
    assert tt["spend"] == 50.0
    assert tt["spend_share"] == 14.3


def test_aggregate_spend_by_platform_empty():
    result = _aggregate_spend_by_platform([])
    assert result == []


def test_aggregate_spend_by_platform_missing_platform():
    rows = [{"spend": 100, "clicks": 10, "impressions": 1000}]

    result = _aggregate_spend_by_platform(rows)

    assert len(result) == 1
    assert result[0]["platform"] == "Unknown"


async def test_portfolio_health_full_flow(
    config,
    sample_export_create_response,
    sample_export_completed_response,
    sample_export_csv,
    monkeypatch,
):
    monkeypatch.setattr(client_module, "EXPORT_POLL_INTERVAL", 0.01)

    spend_response = {
        "data": [
            {"platform_name": "Facebook", "spend": 150, "clicks": 180, "impressions": 12000,
             "date": "2026-04-20", "platform_account_id": "", "campaign_id": "c1",
             "campaign_name": "C1", "spend_currency": "USD",
             "created_at": "2026-04-20T00:00:00Z", "updated_at": "2026-04-20T00:00:00Z"},
            {"platform_name": "TikTok", "spend": 50, "clicks": 25, "impressions": 5000,
             "date": "2026-04-20", "platform_account_id": "", "campaign_id": "c2",
             "campaign_name": "C2", "spend_currency": "USD",
             "created_at": "2026-04-20T00:00:00Z", "updated_at": "2026-04-20T00:00:00Z"},
        ],
        "page": 1, "page_size": 1000, "total_pages": 1, "total_count": 2,
    }

    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(200, json=spend_response)
        )
        respx.post("https://api.northbeam.io/v1/exports/data-export").mock(
            return_value=httpx.Response(200, json=sample_export_create_response)
        )
        respx.get("https://api.northbeam.io/v1/exports/data-export/result/exp-test-123").mock(
            return_value=httpx.Response(200, json=sample_export_completed_response)
        )
        respx.get("https://storage.example.com/export.csv").mock(
            return_value=httpx.Response(200, text=sample_export_csv)
        )

        result = await _portfolio_health(
            config=config,
            date_start="2026-04-14",
            date_end="2026-04-20",
        )

    assert result["summary"]["total_spend"] == 200.0
    assert result["summary"]["total_clicks"] == 205
    assert result["summary"]["blended_cpc"] is not None
    assert result["summary"]["date_range"] == {"start": "2026-04-14", "end": "2026-04-20"}
    assert len(result["spend_by_platform"]) == 2
    assert result["spend_by_platform"][0]["platform"] == "Facebook"
    assert "outcomes" in result
    assert len(result["outcomes"]) == 2


async def test_portfolio_health_auth_error(config):
    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(401, json={"message": "Bad key"})
        )
        respx.post("https://api.northbeam.io/v1/exports/data-export").mock(
            return_value=httpx.Response(401, json={"message": "Bad key"})
        )

        with pytest.raises(ToolError, match="Authentication failed"):
            await _portfolio_health(
                config=config,
                date_start="2026-04-14",
                date_end="2026-04-20",
            )


async def test_portfolio_health_graceful_export_failure(config, monkeypatch):
    """If data export fails but spend succeeds, return spend data with error note."""
    spend_response = {
        "data": [
            {"platform_name": "Facebook", "spend": 100, "clicks": 50, "impressions": 10000,
             "date": "2026-04-20"},
        ],
        "page": 1, "page_size": 1000, "total_pages": 1, "total_count": 1,
    }

    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(200, json=spend_response)
        )
        respx.post("https://api.northbeam.io/v1/exports/data-export").mock(
            return_value=httpx.Response(500, json={"message": "Internal error"})
        )

        result = await _portfolio_health(
            config=config,
            date_start="2026-04-14",
            date_end="2026-04-20",
        )

    assert result["summary"]["total_spend"] == 100.0
    assert len(result["spend_by_platform"]) == 1
    assert "outcomes" not in result
    assert "outcome_error" in result


async def test_portfolio_health_missing_config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PWD", str(tmp_path))
    monkeypatch.delenv("NORTHBEAM_API_KEY", raising=False)
    monkeypatch.delenv("NORTHBEAM_CLIENT_ID", raising=False)
    monkeypatch.delenv("NORTHBEAM_API_ENV", raising=False)
    monkeypatch.delenv("NORTHBEAM_CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_NORTHBEAM_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_NORTHBEAM_CLIENT_ID", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_NORTHBEAM_API_ENV", raising=False)

    with pytest.raises(ToolError, match="Authentication failed"):
        await _portfolio_health(config=None, date_start="2026-04-14", date_end="2026-04-20")


def test_compute_blended_metrics_handles_non_numeric():
    rows = [
        {"spend": "N/A", "clicks": "bad", "impressions": 5000},
        {"spend": 100, "clicks": 50, "impressions": "invalid"},
    ]

    result = _compute_blended_metrics(rows)

    assert result["total_spend"] == 100.0
    assert result["total_clicks"] == 50
    assert result["total_impressions"] == 5000
    assert result["blended_cpc"] == 2.0
    assert result["blended_cpm"] == 20.0
    assert result["blended_ctr"] == 1.0


def test_aggregate_spend_by_platform_handles_non_numeric():
    rows = [
        {"platform_name": "Facebook", "spend": "N/A", "clicks": "bad", "impressions": 5000},
        {"platform_name": "Facebook", "spend": 100, "clicks": 50, "impressions": 10000},
    ]

    result = _aggregate_spend_by_platform(rows)

    assert len(result) == 1
    fb = result[0]
    assert fb["spend"] == 100.0
    assert fb["clicks"] == 50
    assert fb["impressions"] == 15000
