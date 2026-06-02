import json

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
    monkeypatch,
):
    monkeypatch.setattr(client_module, "EXPORT_POLL_INTERVAL", 0.01)

    # The live Data Export returns attributed revenue in a column literally
    # named `attributed_rev` even though the requested metric id is
    # `revAttributed` (mirrors the `imprs`/`impressions` mismatch). The fixture
    # uses the real column name so the metric-id->column alias is exercised.
    csv_content = (
        "breakdown_platform_northbeam,spend,imprs,ecpc,attributed_rev,roas,accounting_mode\n"
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


async def test_portfolio_health_auth_error(config):
    with respx.mock:
        respx.get("https://api.northbeam.io/v1/exports/breakdowns").mock(
            return_value=httpx.Response(401, json={"message": "Bad key"})
        )
        respx.get("https://api.northbeam.io/v1/exports/metrics").mock(
            return_value=httpx.Response(401, json={"message": "Bad key"})
        )
        respx.get("https://api.northbeam.io/v1/exports/attribution-models").mock(
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


async def test_portfolio_health_export_failure_raises(config):
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
