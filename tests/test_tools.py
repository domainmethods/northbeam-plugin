import httpx
import pytest
import respx
from mcp.server.fastmcp.exceptions import ToolError
from server.northbeam_mcp import (
    _list_spend, _check_connection, _list_options, _data_export,
    _aggregate_export_rows,
)

import server.client as client_module


async def test_list_spend_tool_returns_dict(config, sample_spend_response):
    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(200, json=sample_spend_response)
        )

        result = await _list_spend(
            config=config,
            date="2026-04-20",
        )

    assert isinstance(result, dict)
    assert result["data"][0]["platform_name"] == "Facebook"
    assert result["total_count"] == 1


async def test_list_spend_tool_with_date_range(config, sample_spend_response):
    with respx.mock:
        route = respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(200, json=sample_spend_response)
        )

        await _list_spend(
            config=config,
            date_start="2026-04-14",
            date_end="2026-04-20",
        )

    params = dict(route.calls[0].request.url.params)
    assert params["date_start"] == "2026-04-14"
    assert params["date_end"] == "2026-04-20"


async def test_list_spend_tool_auth_error_raises_tool_error(config):
    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(401, json={"message": "Bad key"})
        )

        with pytest.raises(ToolError, match="Authentication failed"):
            await _list_spend(config=config, date="2026-04-20")


async def test_check_connection_success(config):
    response_body = {
        "data": [
            {"platform_name": "Facebook", "date": "2026-04-20", "spend": 100,
             "platform_account_id": "", "campaign_id": "c1", "campaign_name": "C1",
             "spend_currency": "USD", "created_at": "2026-04-20T00:00:00Z",
             "updated_at": "2026-04-20T00:00:00Z"},
            {"platform_name": "TikTok", "date": "2026-04-20", "spend": 50,
             "platform_account_id": "", "campaign_id": "c2", "campaign_name": "C2",
             "spend_currency": "USD", "created_at": "2026-04-20T00:00:00Z",
             "updated_at": "2026-04-20T00:00:00Z"},
        ],
        "page": 1,
        "page_size": 1000,
        "total_pages": 1,
        "total_count": 2,
    }

    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(200, json=response_body)
        )

        result = await _check_connection(config=config)

    assert "connected" in result.lower()
    assert "prod" in result.lower()
    assert "Facebook" in result
    assert "TikTok" in result


async def test_check_connection_auth_failure_raises_tool_error(config):
    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(401, json={"message": "Bad key"})
        )

        with pytest.raises(ToolError, match="Not connected"):
            await _check_connection(config=config)


async def test_check_connection_handles_empty_response(config):
    """API returning empty JSON object should not crash."""
    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(200, json={})
        )

        result = await _check_connection(config=config)

    assert "connected" in result.lower()
    assert "0" in result


async def test_list_spend_missing_config_raises_tool_error(monkeypatch):
    monkeypatch.delenv("NORTHBEAM_API_KEY", raising=False)
    monkeypatch.delenv("NORTHBEAM_CLIENT_ID", raising=False)

    with pytest.raises(ToolError, match="Authentication failed"):
        await _list_spend(config=None, date="2026-04-20")


async def test_list_options_returns_combined_metadata(config, sample_export_options):
    with respx.mock:
        respx.get("https://api.northbeam.io/v1/exports/breakdowns").mock(
            return_value=httpx.Response(200, json=sample_export_options["breakdowns"])
        )
        respx.get("https://api.northbeam.io/v1/exports/metrics").mock(
            return_value=httpx.Response(200, json=sample_export_options["metrics"])
        )
        respx.get("https://api.northbeam.io/v1/exports/attribution-models").mock(
            return_value=httpx.Response(200, json=sample_export_options["attribution_models"])
        )

        result = await _list_options(config=config)

    assert "breakdowns" in result
    assert "metrics" in result
    assert "attribution_models" in result


async def test_list_options_unwraps_exception_group_auth_error(config):
    with respx.mock:
        respx.get("https://api.northbeam.io/v1/exports/breakdowns").mock(
            return_value=httpx.Response(401, json={"message": "Bad key"})
        )
        respx.get("https://api.northbeam.io/v1/exports/metrics").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        respx.get("https://api.northbeam.io/v1/exports/attribution-models").mock(
            return_value=httpx.Response(200, json={"data": []})
        )

        with pytest.raises(ToolError, match="Authentication failed"):
            await _list_options(config=config)


async def test_list_options_missing_config_raises_tool_error(monkeypatch):
    monkeypatch.delenv("NORTHBEAM_API_KEY", raising=False)
    monkeypatch.delenv("NORTHBEAM_CLIENT_ID", raising=False)

    with pytest.raises(ToolError, match="Authentication failed"):
        await _list_options(config=None)


async def test_data_export_full_flow(
    config,
    sample_export_create_response,
    sample_export_completed_response,
    sample_export_csv,
    monkeypatch,
):
    monkeypatch.setattr(client_module, "EXPORT_POLL_INTERVAL", 0.01)

    with respx.mock:
        respx.post("https://api.northbeam.io/v1/exports/data-export").mock(
            return_value=httpx.Response(200, json=sample_export_create_response)
        )
        respx.get("https://api.northbeam.io/v1/exports/data-export/result/exp-test-123").mock(
            return_value=httpx.Response(200, json=sample_export_completed_response)
        )
        respx.get("https://storage.example.com/export.csv").mock(
            return_value=httpx.Response(200, text=sample_export_csv)
        )

        result = await _data_export(
            config=config,
            date_start="2026-04-14",
            date_end="2026-04-20",
            metrics=["revenue", "roas"],
            breakdowns=["platform", "campaign_name"],
        )

    assert result["summary"]["total_raw_rows"] == 2
    assert result["summary"]["aggregated_groups"] == 2
    assert result["summary"]["date_range"] == {"start": "2026-04-14", "end": "2026-04-20"}
    assert result["summary"]["attribution_model"] == "northbeam_custom__va"
    assert len(result["data"]) == 2
    assert result["data"][0]["platform"] == "Facebook"
    assert result["data"][0]["revenue"] == 1500.0
    assert result["data"][0]["roas"] == 3.2


async def test_data_export_auth_error_raises_tool_error(config):
    with respx.mock:
        respx.post("https://api.northbeam.io/v1/exports/data-export").mock(
            return_value=httpx.Response(401, json={"message": "Bad key"})
        )

        with pytest.raises(ToolError, match="Authentication failed"):
            await _data_export(
                config=config,
                date_start="2026-04-14",
                date_end="2026-04-20",
                metrics=["revenue"],
                breakdowns=["platform"],
            )


async def test_data_export_aggregates_by_breakdown(
    config,
    sample_export_create_response,
    sample_export_completed_response,
    monkeypatch,
):
    monkeypatch.setattr(client_module, "EXPORT_POLL_INTERVAL", 0.01)

    header = "platform,revenue\n"
    rows_csv = "Facebook,100\nFacebook,200\nTikTok,50\nTikTok,150\n"
    csv_content = header + rows_csv

    with respx.mock:
        respx.post("https://api.northbeam.io/v1/exports/data-export").mock(
            return_value=httpx.Response(200, json=sample_export_create_response)
        )
        respx.get("https://api.northbeam.io/v1/exports/data-export/result/exp-test-123").mock(
            return_value=httpx.Response(200, json=sample_export_completed_response)
        )
        respx.get("https://storage.example.com/export.csv").mock(
            return_value=httpx.Response(200, text=csv_content)
        )

        result = await _data_export(
            config=config,
            date_start="2026-04-14",
            date_end="2026-04-20",
            metrics=["revenue"],
            breakdowns=["platform"],
        )

    assert result["summary"]["total_raw_rows"] == 4
    assert result["summary"]["aggregated_groups"] == 2
    assert len(result["data"]) == 2
    assert result["data"][0]["platform"] == "Facebook"
    assert result["data"][0]["revenue"] == 300.0
    assert result["data"][0]["_row_count"] == 2
    assert result["data"][1]["platform"] == "TikTok"
    assert result["data"][1]["revenue"] == 200.0


async def test_list_spend_platform_name_filters_results(config):
    multi_platform_response = {
        "data": [
            {"platform_name": "Facebook", "spend": 100, "date": "2026-04-20"},
            {"platform_name": "TikTok", "spend": 50, "date": "2026-04-20"},
            {"platform_name": "Facebook", "spend": 200, "date": "2026-04-19"},
        ],
        "page": 1,
        "page_size": 1000,
        "total_pages": 1,
        "total_count": 3,
    }

    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(200, json=multi_platform_response)
        )

        result = await _list_spend(
            config=config,
            date_start="2026-04-19",
            date_end="2026-04-20",
            platform_name="facebook",
        )

    assert len(result["data"]) == 2
    assert all(r["platform_name"] == "Facebook" for r in result["data"])
    assert result["total_count"] == 2


async def test_list_spend_platform_name_none_returns_all(config, sample_spend_response):
    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(200, json=sample_spend_response)
        )

        result = await _list_spend(config=config, date="2026-04-20")

    assert len(result["data"]) == 1


def test_aggregate_export_rows_sums_metrics():
    rows = [
        {"platform": "Facebook", "revenue": "1000", "roas": "3.0"},
        {"platform": "Facebook", "revenue": "500", "roas": "2.0"},
        {"platform": "TikTok", "revenue": "800", "roas": "4.0"},
    ]

    result = _aggregate_export_rows(rows, ["platform"], ["revenue", "roas"])

    assert len(result) == 2
    fb = next(r for r in result if r["platform"] == "Facebook")
    tt = next(r for r in result if r["platform"] == "TikTok")
    assert fb["revenue"] == 1500.0
    assert fb["roas"] == 5.0
    assert fb["_row_count"] == 2
    assert tt["revenue"] == 800.0
    assert tt["_row_count"] == 1


def test_aggregate_export_rows_handles_non_numeric():
    rows = [
        {"platform": "Facebook", "revenue": "bad_value"},
        {"platform": "Facebook", "revenue": "100"},
    ]

    result = _aggregate_export_rows(rows, ["platform"], ["revenue"])

    assert len(result) == 1
    assert result[0]["revenue"] == 100.0
