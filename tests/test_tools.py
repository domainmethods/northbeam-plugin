import json

import httpx
import pytest
import respx
from mcp.server.fastmcp.exceptions import ToolError
from server.northbeam_mcp import (
    _list_spend, _check_connection, _list_options, _data_export,
    _aggregate_export_rows, _enrich_spend_rows, MAX_RESULT_ROWS,
    _spend_rows_from_aggregated, _compute_blended_metrics,
    _aggregate_spend_by_platform, _spend_via_export,
)

import server.client as client_module


def _mock_export_options(sample_export_options):
    respx.get("https://api.northbeam.io/v1/exports/breakdowns").mock(
        return_value=httpx.Response(200, json=sample_export_options["breakdowns"])
    )
    respx.get("https://api.northbeam.io/v1/exports/metrics").mock(
        return_value=httpx.Response(200, json=sample_export_options["metrics"])
    )
    respx.get("https://api.northbeam.io/v1/exports/attribution-models").mock(
        return_value=httpx.Response(200, json=sample_export_options["attribution_models"])
    )


def _mock_successful_connection_outcome(
    sample_export_options,
    sample_export_create_response,
    sample_export_completed_response,
    csv_content="transactions,attributed_rev\n0,0\n",
):
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


async def test_check_connection_success(
    config,
    sample_export_options,
    sample_export_create_response,
    sample_export_completed_response,
    monkeypatch,
):
    monkeypatch.setattr(client_module, "EXPORT_POLL_INTERVAL", 0.01)
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
        _mock_successful_connection_outcome(
            sample_export_options,
            sample_export_create_response,
            sample_export_completed_response,
        )

        result = await _check_connection(config=config)

    assert "Status: Connected" in result
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


async def test_check_connection_handles_empty_response(
    config,
    sample_export_options,
    sample_export_create_response,
    sample_export_completed_response,
    monkeypatch,
):
    """API returning empty JSON object should not crash."""
    monkeypatch.setattr(client_module, "EXPORT_POLL_INTERVAL", 0.01)
    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(200, json={})
        )
        _mock_successful_connection_outcome(
            sample_export_options,
            sample_export_create_response,
            sample_export_completed_response,
        )

        result = await _check_connection(config=config)

    assert "connected" in result.lower()
    assert "0" in result


async def test_check_connection_reports_spend_and_outcome_surfaces(
    config,
    sample_export_options,
    sample_export_create_response,
    sample_export_completed_response,
    monkeypatch,
):
    monkeypatch.setattr(client_module, "EXPORT_POLL_INTERVAL", 0.01)
    spend_response = {
        "data": [],
        "page": 1,
        "page_size": 1000,
        "total_pages": 1,
        "total_count": 0,
    }
    csv_content = "transactions,attributed_rev\n80.38863860198144,18372.969881449368\n"

    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(200, json=spend_response)
        )
        breakdowns_route = respx.get("https://api.northbeam.io/v1/exports/breakdowns").mock(
            return_value=httpx.Response(200, json=sample_export_options["breakdowns"])
        )
        metrics_route = respx.get("https://api.northbeam.io/v1/exports/metrics").mock(
            return_value=httpx.Response(200, json=sample_export_options["metrics"])
        )
        models_route = respx.get("https://api.northbeam.io/v1/exports/attribution-models").mock(
            return_value=httpx.Response(200, json=sample_export_options["attribution_models"])
        )
        respx.post("https://api.northbeam.io/v1/exports/data-export").mock(
            return_value=httpx.Response(201, json=sample_export_create_response)
        )
        respx.get("https://api.northbeam.io/v1/exports/data-export/result/exp-test-123").mock(
            return_value=httpx.Response(200, json=sample_export_completed_response)
        )
        respx.get("https://storage.example.com/export.csv").mock(
            return_value=httpx.Response(200, text=csv_content)
        )

        result = await _check_connection(config=config, check_date="2026-05-31")

    assert "Status: Connected" in result
    assert "Environment: prod" in result
    assert "Uploaded Spend API: OK - 0 uploaded spend rows for 2026-05-31" in result
    assert "Data Export metadata: OK" in result
    assert "Data Export API: OK - transactions=80.39, revenue=18372.97 for 2026-05-31" in result
    assert "real ad spend comes from the Data Export API" in result
    assert "Outcome data exists even though spend rows are zero" in result
    assert breakdowns_route.called
    assert metrics_route.called
    assert models_route.called


async def test_check_connection_reports_partial_data_export_failure(
    config,
    sample_export_options,
):
    spend_response = {
        "data": [],
        "page": 1,
        "page_size": 1000,
        "total_pages": 1,
        "total_count": 0,
    }

    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(200, json=spend_response)
        )
        breakdowns_route = respx.get("https://api.northbeam.io/v1/exports/breakdowns").mock(
            return_value=httpx.Response(200, json=sample_export_options["breakdowns"])
        )
        metrics_route = respx.get("https://api.northbeam.io/v1/exports/metrics").mock(
            return_value=httpx.Response(200, json=sample_export_options["metrics"])
        )
        models_route = respx.get("https://api.northbeam.io/v1/exports/attribution-models").mock(
            return_value=httpx.Response(200, json=sample_export_options["attribution_models"])
        )
        respx.post("https://api.northbeam.io/v1/exports/data-export").mock(
            return_value=httpx.Response(422, json={"error": [{"loc": ["metrics", 0], "msg": "bad"}]})
        )

        result = await _check_connection(config=config, check_date="2026-05-31")

    assert "Status: Partially connected" in result
    assert "Spend API: OK" in result
    assert "Data Export metadata: OK" in result
    assert "Data Export API: Failed" in result
    assert "metrics" in result
    assert "bad" in result
    assert breakdowns_route.called
    assert metrics_route.called
    assert models_route.called


async def test_check_connection_reports_partial_metadata_failure(config):
    spend_response = {
        "data": [],
        "page": 1,
        "page_size": 1000,
        "total_pages": 1,
        "total_count": 0,
    }

    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(200, json=spend_response)
        )
        respx.get("https://api.northbeam.io/v1/exports/breakdowns").mock(
            return_value=httpx.Response(500, json={"message": "metadata unavailable"})
        )
        respx.get("https://api.northbeam.io/v1/exports/metrics").mock(
            return_value=httpx.Response(200, json={"metrics": []})
        )
        respx.get("https://api.northbeam.io/v1/exports/attribution-models").mock(
            return_value=httpx.Response(200, json={"attribution_models": []})
        )

        result = await _check_connection(config=config, check_date="2026-05-31")

    assert "Status: Partially connected" in result
    assert "Spend API: OK" in result
    assert "Data Export metadata: Failed" in result
    assert "metadata unavailable" in result
    assert "Data Export API: Skipped - metadata check failed" in result
    assert "Spend API credentials worked" in result


async def test_check_connection_reports_partial_metadata_auth_failure(config):
    spend_response = {
        "data": [],
        "page": 1,
        "page_size": 1000,
        "total_pages": 1,
        "total_count": 0,
    }

    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(200, json=spend_response)
        )
        respx.get("https://api.northbeam.io/v1/exports/breakdowns").mock(
            return_value=httpx.Response(401, json={"message": "Data Export denied"})
        )
        respx.get("https://api.northbeam.io/v1/exports/metrics").mock(
            return_value=httpx.Response(200, json={"metrics": []})
        )
        respx.get("https://api.northbeam.io/v1/exports/attribution-models").mock(
            return_value=httpx.Response(200, json={"attribution_models": []})
        )

        result = await _check_connection(config=config, check_date="2026-05-31")

    assert "Status: Partially connected" in result
    assert "Spend API: OK" in result
    assert "Data Export metadata: Failed - authentication/configuration failed" in result
    assert "Data Export API: Skipped - metadata check failed" in result
    assert "Status: Not connected" not in result


async def test_check_connection_reports_partial_poll_failure(
    config,
    sample_export_options,
    sample_export_create_response,
    monkeypatch,
):
    monkeypatch.setattr(client_module, "EXPORT_POLL_INTERVAL", 0.01)
    spend_response = {
        "data": [],
        "page": 1,
        "page_size": 1000,
        "total_pages": 1,
        "total_count": 0,
    }

    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(200, json=spend_response)
        )
        _mock_export_options(sample_export_options)
        respx.post("https://api.northbeam.io/v1/exports/data-export").mock(
            return_value=httpx.Response(201, json=sample_export_create_response)
        )
        respx.get("https://api.northbeam.io/v1/exports/data-export/result/exp-test-123").mock(
            return_value=httpx.Response(200, json={"status": "FAILED", "error": "upstream failed"})
        )

        result = await _check_connection(config=config, check_date="2026-05-31")

    assert "Status: Partially connected" in result
    assert "Spend API: OK" in result
    assert "Data Export metadata: OK" in result
    assert "Data Export API: Failed" in result
    assert "upstream failed" in result


async def test_check_connection_reports_partial_download_failure(
    config,
    sample_export_options,
    sample_export_create_response,
    sample_export_completed_response,
    monkeypatch,
):
    monkeypatch.setattr(client_module, "EXPORT_POLL_INTERVAL", 0.01)
    spend_response = {
        "data": [],
        "page": 1,
        "page_size": 1000,
        "total_pages": 1,
        "total_count": 0,
    }

    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(200, json=spend_response)
        )
        _mock_export_options(sample_export_options)
        respx.post("https://api.northbeam.io/v1/exports/data-export").mock(
            return_value=httpx.Response(201, json=sample_export_create_response)
        )
        respx.get("https://api.northbeam.io/v1/exports/data-export/result/exp-test-123").mock(
            return_value=httpx.Response(200, json=sample_export_completed_response)
        )
        respx.get("https://storage.example.com/export.csv").mock(
            return_value=httpx.Response(503, text="download unavailable")
        )

        result = await _check_connection(config=config, check_date="2026-05-31")

    assert "Status: Partially connected" in result
    assert "Spend API: OK" in result
    assert "Data Export metadata: OK" in result
    assert "Data Export API: Failed" in result
    assert "Export CSV download failed with HTTP 503" in result
    assert "storage.example.com" not in result


async def test_list_spend_missing_config_raises_tool_error(monkeypatch, tmp_path):
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


async def test_list_options_missing_config_raises_tool_error(monkeypatch, tmp_path):
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
        await _list_options(config=None)


async def test_data_export_full_flow(
    config,
    sample_export_options,
    sample_export_create_response,
    sample_export_completed_response,
    sample_export_csv,
    monkeypatch,
):
    monkeypatch.setattr(client_module, "EXPORT_POLL_INTERVAL", 0.01)

    with respx.mock:
        _mock_export_options(sample_export_options)
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
            metrics=["revAttributed", "roas"],
            breakdowns=["platform"],
        )

    assert result["summary"]["total_raw_rows"] == 2
    assert result["summary"]["aggregated_groups"] == 2
    assert result["summary"]["returned_rows"] == 2
    assert result["summary"]["truncated"] is False
    assert result["summary"]["date_range"] == {"start": "2026-04-14", "end": "2026-04-20"}
    assert result["summary"]["attribution_model"] == "northbeam_custom"
    assert result["summary"]["attribution_window"] == "1"
    assert len(result["data"]) == 2
    assert result["data"][0]["platform"] == "Facebook Ads"
    assert result["data"][0]["revAttributed"] == 1500.0
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
                breakdowns=[],
            )


async def test_data_export_metadata_auth_error_raises_tool_error(config):
    with respx.mock:
        respx.get("https://api.northbeam.io/v1/exports/breakdowns").mock(
            return_value=httpx.Response(401, json={"message": "Bad key"})
        )
        respx.get("https://api.northbeam.io/v1/exports/metrics").mock(
            return_value=httpx.Response(200, json={"metrics": []})
        )
        respx.get("https://api.northbeam.io/v1/exports/attribution-models").mock(
            return_value=httpx.Response(200, json={"attribution_models": []})
        )

        with pytest.raises(ToolError, match="Authentication failed"):
            await _data_export(
                config=config,
                date_start="2026-04-14",
                date_end="2026-04-20",
                metrics=["rev"],
                breakdowns=["platform"],
            )


async def test_data_export_uses_current_payload_without_breakdowns(
    config,
    sample_export_create_response,
    sample_export_completed_response,
    monkeypatch,
):
    monkeypatch.setattr(client_module, "EXPORT_POLL_INTERVAL", 0.01)
    csv_content = "transactions,rev\n1.5,100.25\n2.5,200.75\n"

    with respx.mock:
        post_route = respx.post("https://api.northbeam.io/v1/exports/data-export").mock(
            return_value=httpx.Response(201, json=sample_export_create_response)
        )
        respx.get("https://api.northbeam.io/v1/exports/data-export/result/exp-test-123").mock(
            return_value=httpx.Response(200, json=sample_export_completed_response)
        )
        respx.get("https://storage.example.com/export.csv").mock(
            return_value=httpx.Response(200, text=csv_content)
        )

        result = await _data_export(
            config=config,
            date_start="2026-05-31",
            date_end="2026-05-31",
            metrics=["txns", "rev"],
            breakdowns=[],
        )

    sent = json.loads(post_route.calls[0].request.content)
    assert "date_start" not in sent
    assert "attribution_model" not in sent
    assert sent["period_type"] == "FIXED"
    assert sent["period_options"]["period_starting_at"] == "2026-05-31T00:00:00Z"
    assert sent["period_options"]["period_ending_at"] == "2026-05-31T23:59:59Z"
    assert sent["metrics"] == [{"id": "txns"}, {"id": "rev"}]
    assert result["data"][0]["txns"] == 4.0
    assert result["data"][0]["rev"] == 301.0


async def test_data_export_fetches_breakdown_values_for_non_empty_breakdowns(
    config,
    sample_export_options,
    sample_export_create_response,
    sample_export_completed_response,
    monkeypatch,
):
    monkeypatch.setattr(client_module, "EXPORT_POLL_INTERVAL", 0.01)
    csv_content = "breakdown_platform_northbeam,rev\nFacebook Ads,100\nTikTok,50\n"

    with respx.mock:
        breakdowns_route = respx.get("https://api.northbeam.io/v1/exports/breakdowns").mock(
            return_value=httpx.Response(200, json=sample_export_options["breakdowns"])
        )
        metrics_route = respx.get("https://api.northbeam.io/v1/exports/metrics").mock(
            return_value=httpx.Response(200, json=sample_export_options["metrics"])
        )
        models_route = respx.get("https://api.northbeam.io/v1/exports/attribution-models").mock(
            return_value=httpx.Response(200, json=sample_export_options["attribution_models"])
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

        result = await _data_export(
            config=config,
            date_start="2026-05-31",
            date_end="2026-05-31",
            metrics=["rev"],
            breakdowns=["platform"],
        )

    sent = json.loads(post_route.calls[0].request.content)
    assert sent["breakdowns"] == [
        {"key": "Platform (Northbeam)", "values": ["Facebook Ads", "TikTok"]}
    ]
    assert breakdowns_route.called
    assert metrics_route.called
    assert models_route.called
    assert result["data"][0]["platform"] == "Facebook Ads"
    assert result["data"][0]["rev"] == 100.0


async def test_data_export_aggregates_by_breakdown(
    config,
    sample_export_options,
    sample_export_create_response,
    sample_export_completed_response,
    monkeypatch,
):
    monkeypatch.setattr(client_module, "EXPORT_POLL_INTERVAL", 0.01)

    header = "breakdown_platform_northbeam,rev\n"
    rows_csv = "Facebook Ads,100\nFacebook Ads,200\nTikTok,50\nTikTok,150\n"
    csv_content = header + rows_csv

    with respx.mock:
        _mock_export_options(sample_export_options)
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
            metrics=["rev"],
            breakdowns=["platform"],
        )

    assert result["summary"]["total_raw_rows"] == 4
    assert result["summary"]["aggregated_groups"] == 2
    assert len(result["data"]) == 2
    assert result["data"][0]["platform"] == "Facebook Ads"
    assert result["data"][0]["rev"] == 300.0
    assert result["data"][0]["_row_count"] == 2
    assert result["data"][1]["platform"] == "TikTok"
    assert result["data"][1]["rev"] == 200.0


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


def test_aggregate_export_rows_sums_additive_metrics_only():
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
    assert fb["roas"] is None
    assert fb["_row_count"] == 2
    assert tt["revenue"] == 800.0
    assert tt["roas"] == 4.0
    assert tt["_row_count"] == 1


def test_aggregate_export_rows_handles_non_numeric():
    rows = [
        {"platform": "Facebook", "revenue": "bad_value"},
        {"platform": "Facebook", "revenue": "100"},
    ]

    result = _aggregate_export_rows(rows, ["platform"], ["revenue"])

    assert len(result) == 1
    assert result[0]["revenue"] == 100.0


def test_aggregate_export_rows_uses_live_metric_and_breakdown_columns():
    rows = [
        {"breakdown_platform_northbeam": "Facebook Ads", "transactions": "1.5", "rev": "100"},
        {"breakdown_platform_northbeam": "Facebook Ads", "transactions": "2.5", "rev": "200"},
    ]

    result = _aggregate_export_rows(rows, ["platform"], ["txns", "rev"])

    assert result == [
        {
            "platform": "Facebook Ads",
            "txns": 4.0,
            "rev": 300.0,
            "_row_count": 2,
        }
    ]


def test_enrich_spend_rows_computes_derived_metrics():
    rows = [
        {"spend": 150.0, "clicks": 180, "impressions": 12000},
        {"spend": 50.0, "clicks": 25, "impressions": 5000},
    ]

    result = _enrich_spend_rows(rows)

    assert result[0]["cpc"] == round(150.0 / 180, 2)
    assert result[0]["cpm"] == round((150.0 / 12000) * 1000, 2)
    assert result[0]["ctr"] == round((180 / 12000) * 100, 2)
    assert result[1]["cpc"] == round(50.0 / 25, 2)
    assert result[1]["cpm"] == round((50.0 / 5000) * 1000, 2)
    assert result[1]["ctr"] == round((25 / 5000) * 100, 2)


def test_enrich_spend_rows_handles_zero_clicks():
    rows = [{"spend": 100.0, "clicks": 0, "impressions": 5000}]

    result = _enrich_spend_rows(rows)

    assert result[0]["cpc"] is None
    assert result[0]["cpm"] == round((100.0 / 5000) * 1000, 2)
    assert result[0]["ctr"] == round(0, 2)


def test_enrich_spend_rows_handles_zero_impressions():
    rows = [{"spend": 100.0, "clicks": 50, "impressions": 0}]

    result = _enrich_spend_rows(rows)

    assert result[0]["cpc"] == round(100.0 / 50, 2)
    assert result[0]["cpm"] is None
    assert result[0]["ctr"] is None


def test_enrich_spend_rows_handles_missing_fields():
    rows = [{"spend": 100.0}]

    result = _enrich_spend_rows(rows)

    assert result[0]["cpc"] is None
    assert result[0]["cpm"] is None
    assert result[0]["ctr"] is None


async def test_list_spend_returns_enriched_data(config, sample_spend_response):
    """Verify _list_spend integrates enrichment — sample has spend=150, clicks=180, impressions=12000."""
    import httpx
    import respx

    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(200, json=sample_spend_response)
        )

        result = await _list_spend(config=config, date="2026-04-20")

    row = result["data"][0]
    assert "cpc" in row
    assert "cpm" in row
    assert "ctr" in row
    assert row["cpc"] == round(150.0 / 180, 2)


async def test_data_export_truncates_high_cardinality(
    config,
    sample_export_options,
    sample_export_create_response,
    sample_export_completed_response,
    monkeypatch,
):
    monkeypatch.setattr(client_module, "EXPORT_POLL_INTERVAL", 0.01)

    header = "breakdown_platform_northbeam,rev\n"
    rows_csv = "".join(f"platform-{i},{i * 10}\n" for i in range(300))
    csv_content = header + rows_csv

    with respx.mock:
        _mock_export_options(sample_export_options)
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
            metrics=["rev"],
            breakdowns=["platform"],
        )

    assert result["summary"]["total_raw_rows"] == 300
    assert result["summary"]["aggregated_groups"] == 300
    assert result["summary"]["returned_rows"] == MAX_RESULT_ROWS
    assert result["summary"]["truncated"] is True
    assert len(result["data"]) == MAX_RESULT_ROWS


async def test_data_export_empty_breakdowns_aggregates_totals(
    config,
    sample_export_create_response,
    sample_export_completed_response,
    monkeypatch,
):
    monkeypatch.setattr(client_module, "EXPORT_POLL_INTERVAL", 0.01)

    csv_content = "revenue,roas\n1000,3.0\n500,2.0\n"

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
            metrics=["revenue", "roas"],
            breakdowns=[],
        )

    assert len(result["data"]) == 1
    assert result["data"][0]["revenue"] == 1500.0
    assert result["data"][0]["roas"] is None
    assert result["data"][0]["_row_count"] == 2
    assert result["summary"]["non_additive_metrics"] == ["roas"]


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


def test_aggregate_export_rows_raises_when_multiple_modes_none_match():
    """If the accounting-mode column is present with MULTIPLE distinct modes but
    none matches the requested one (e.g. Northbeam relabels 'Accrual
    performance'), summing would silently double spend. Fail loud instead."""
    rows = [
        {"breakdown_platform_northbeam": "Facebook Ads", "spend": "100",
         "accounting_mode": "Cash snapshot"},
        {"breakdown_platform_northbeam": "Facebook Ads", "spend": "100",
         "accounting_mode": "Lifetime blended"},
    ]

    with pytest.raises(ToolError, match="accounting mode"):
        _aggregate_export_rows(
            rows, ["platform"], ["spend"], accounting_mode="accrual"
        )


def test_aggregate_export_rows_single_unmatched_mode_keeps_rows():
    """A single accounting mode that doesn't match cannot double spend, so keep
    the rows (no fan-out to drop) rather than raising."""
    rows = [
        {"breakdown_platform_northbeam": "Facebook Ads", "spend": "100",
         "accounting_mode": "Cash snapshot"},
        {"breakdown_platform_northbeam": "Facebook Ads", "spend": "200",
         "accounting_mode": "Cash snapshot"},
    ]

    result = _aggregate_export_rows(
        rows, ["platform"], ["spend"], accounting_mode="accrual"
    )

    assert len(result) == 1
    assert result[0]["spend"] == 300.0


def test_aggregate_export_rows_omits_clicks_unless_requested():
    """Generic exports must NOT gain an unsolicited derived `clicks` field; it is
    only attached when the spend path explicitly opts in via derive_clicks."""
    rows = [
        {"breakdown_platform_northbeam": "Facebook Ads", "spend": "100", "ecpc": "0.5"},
    ]

    generic = _aggregate_export_rows(rows, ["platform"], ["spend", "ecpc"])
    assert "clicks" not in generic[0]

    opted_in = _aggregate_export_rows(
        rows, ["platform"], ["spend", "ecpc"], derive_clicks=True
    )
    assert opted_in[0]["clicks"] == 200.0


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


def test_spend_rows_from_aggregated_emits_campaign_name_when_keyed():
    # Verified live: level=campaign exports carry a `campaign_name` column, and
    # aggregating on ["platform", "campaign_name"] yields entries with both keys.
    aggregated = [
        {"platform": "Facebook Ads", "campaign_name": "PARTNERSHIPS-CBO",
         "spend": 798.61, "impressions": 63490.0, "ecpc": 1.0676604278},
    ]

    rows = _spend_rows_from_aggregated(aggregated, campaign_key="campaign_name")

    assert rows[0]["platform_name"] == "Facebook Ads"
    assert rows[0]["campaign_name"] == "PARTNERSHIPS-CBO"
    assert rows[0]["spend"] == 798.61
    assert rows[0]["clicks"] == 798.61 / 1.0676604278


def test_spend_rows_from_aggregated_omits_campaign_name_by_default():
    aggregated = [{"platform": "Facebook Ads", "spend": 100.0, "impressions": 10.0, "ecpc": 1.0}]

    rows = _spend_rows_from_aggregated(aggregated)

    assert "campaign_name" not in rows[0]


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


async def test_spend_via_export_sums_clicks_across_daily_rows(
    config,
    sample_export_options,
    sample_export_create_response,
    sample_export_completed_response,
    monkeypatch,
):
    """Daily granularity returns one raw row per platform per day. `ecpc` is a
    per-row ratio that the aggregator nulls when a group spans multiple rows, so
    clicks MUST be derived per raw row (spend / ecpc) and summed — never divided
    out of an aggregated ecpc, which would yield zero clicks for every multi-day
    platform (the live-verification bug)."""
    monkeypatch.setattr(client_module, "EXPORT_POLL_INTERVAL", 0.01)
    csv_content = (
        "breakdown_platform_northbeam,spend,imprs,ecpc\n"
        "Facebook Ads,100000,5000000,0.50\n"   # 200,000 clicks
        "Facebook Ads,50000,4000000,0.25\n"    # 200,000 clicks
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
        )

    fb = next(r for r in result["data"] if r["platform_name"] == "Facebook Ads")
    assert fb["spend"] == 150000.0
    assert fb["impressions"] == 9000000.0
    assert fb["clicks"] == 400000.0  # 200,000 + 200,000, NOT 0
    assert fb["cpc"] == 0.38  # 150000 / 400000, rounded


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


async def test_spend_via_export_campaign_breakdown_keeps_campaigns_separate(
    config,
    sample_export_options,
    sample_export_create_response,
    sample_export_completed_response,
    monkeypatch,
):
    monkeypatch.setattr(client_module, "EXPORT_POLL_INTERVAL", 0.01)
    # level=campaign CSV: two campaigns under the SAME platform must not collapse.
    csv_content = (
        "breakdown_platform_northbeam,campaign_name,spend,imprs,ecpc\n"
        "Facebook Ads,PARTNERSHIPS-CBO,798.61,63490,1.0676604278\n"
        "Facebook Ads,SINGLE-FUNNEL,1212.45,48732,2.9937037037\n"
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
            breakdown="campaign",
        )

    sent = json.loads(post_route.calls[0].request.content)
    assert sent["level"] == "campaign"
    payload_breakdown_keys = [b["key"] for b in sent["breakdowns"]]
    assert payload_breakdown_keys == ["Platform (Northbeam)"]
    assert "campaign_name" not in payload_breakdown_keys

    assert result["total_count"] == 2
    names = {r["campaign_name"] for r in result["data"]}
    assert names == {"PARTNERSHIPS-CBO", "SINGLE-FUNNEL"}
    cbo = next(r for r in result["data"] if r["campaign_name"] == "PARTNERSHIPS-CBO")
    assert cbo["platform_name"] == "Facebook Ads"
    assert cbo["spend"] == 798.61


async def test_spend_via_export_rejects_unknown_breakdown(config):
    with pytest.raises(ToolError, match="Unsupported breakdown"):
        await _spend_via_export(
            config=config,
            date_start="2026-05-01",
            date_end="2026-05-31",
            breakdown="adset",
        )


async def test_spend_via_export_campaign_breakdown_filters_by_platform(
    config,
    sample_export_options,
    sample_export_create_response,
    sample_export_completed_response,
    monkeypatch,
):
    monkeypatch.setattr(client_module, "EXPORT_POLL_INTERVAL", 0.01)
    csv_content = (
        "breakdown_platform_northbeam,campaign_name,spend,imprs,ecpc\n"
        "Facebook Ads,FB-CBO,798.61,63490,1.0676604278\n"
        "TikTok,TT-SPARK,1212.45,48732,2.9937037037\n"
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
            breakdown="campaign",
            platform_name="facebook ads",
        )

    assert result["total_count"] == 1
    assert result["data"][0]["campaign_name"] == "FB-CBO"
    assert result["data"][0]["platform_name"] == "Facebook Ads"


def test_uploaded_spend_tool_is_exposed_and_documented():
    from server import northbeam_mcp

    tool = northbeam_mcp.northbeam_list_uploaded_spend
    assert tool.__name__ == "northbeam_list_uploaded_spend"
    assert "upload" in (tool.__doc__ or "").lower()
    assert not hasattr(northbeam_mcp, "northbeam_list_spend")
