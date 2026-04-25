import httpx
import pytest
import respx
from server.northbeam_mcp import _list_spend, _check_connection


@pytest.mark.asyncio
async def test_list_spend_tool_returns_formatted_json(config, sample_spend_response):
    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(200, json=sample_spend_response)
        )

        result = await _list_spend(
            config=config,
            date="2026-04-20",
        )

    assert '"platform_name": "Facebook"' in result
    assert '"total_count": 1' in result


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_list_spend_tool_auth_error_returns_message(config):
    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(401, json={"message": "Bad key"})
        )

        result = await _list_spend(config=config, date="2026-04-20")

    assert "Authentication failed" in result
    assert "/northbeam:setup" in result


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_check_connection_auth_failure(config):
    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(401, json={"message": "Bad key"})
        )

        result = await _check_connection(config=config)

    assert "not connected" in result.lower() or "failed" in result.lower()


@pytest.mark.asyncio
async def test_list_spend_missing_config_returns_setup_message(monkeypatch):
    monkeypatch.delenv("NORTHBEAM_API_KEY", raising=False)
    monkeypatch.delenv("NORTHBEAM_CLIENT_ID", raising=False)

    result = await _list_spend(config=None, date="2026-04-20")

    assert "Error querying Northbeam" in result
    assert "NORTHBEAM_API_KEY" in result
