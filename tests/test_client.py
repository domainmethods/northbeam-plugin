import httpx
import pytest
import respx
from server.client import NorthbeamClient


async def test_list_spend_sends_auth_headers(config, sample_spend_response):
    with respx.mock:
        route = respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(200, json=sample_spend_response)
        )

        async with NorthbeamClient(config) as client:
            await client.list_spend(date="2026-04-20")

        assert route.called
        request = route.calls[0].request
        assert request.headers["Authorization"] == "test-key"
        assert request.headers["Data-Client-ID"] == "test-client"


async def test_list_spend_passes_query_params(config, sample_spend_response):
    with respx.mock:
        route = respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(200, json=sample_spend_response)
        )

        async with NorthbeamClient(config) as client:
            await client.list_spend(
                date_start="2026-04-14",
                date_end="2026-04-20",
                platform_account_id="fb-123",
                campaign_id="camp-1",
            )

        params = dict(route.calls[0].request.url.params)
        assert params["date_start"] == "2026-04-14"
        assert params["date_end"] == "2026-04-20"
        assert params["platform_account_id"] == "fb-123"
        assert params["campaign_id"] == "camp-1"


async def test_list_spend_returns_parsed_response(config, sample_spend_response):
    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(200, json=sample_spend_response)
        )

        async with NorthbeamClient(config) as client:
            result = await client.list_spend(date="2026-04-20")

    assert result["total_count"] == 1
    assert len(result["data"]) == 1
    assert result["data"][0]["platform_name"] == "Facebook"


async def test_list_spend_fetch_all_paginates(config, sample_spend_record):
    page1 = {
        "data": [sample_spend_record],
        "page": 1,
        "page_size": 1,
        "total_pages": 2,
        "total_count": 2,
    }
    record2 = {**sample_spend_record, "campaign_id": "camp-2", "spend": 200.00}
    page2 = {
        "data": [record2],
        "page": 2,
        "page_size": 1,
        "total_pages": 2,
        "total_count": 2,
    }

    with respx.mock:
        route = respx.get("https://api.northbeam.io/v1/spend")
        route.side_effect = [
            httpx.Response(200, json=page1),
            httpx.Response(200, json=page2),
        ]

        async with NorthbeamClient(config) as client:
            result = await client.list_spend(date="2026-04-20", fetch_all=True)

    assert len(result["data"]) == 2
    assert result["pages_fetched"] == 2
    assert result["total_count"] == 2


async def test_list_spend_401_raises_auth_error(config):
    error_body = {"message": "Authentication failed."}

    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(401, json=error_body)
        )

        async with NorthbeamClient(config) as client:
            with pytest.raises(Exception, match="Authentication failed"):
                await client.list_spend(date="2026-04-20")


async def test_list_spend_422_raises_validation_error(config):
    error_body = {
        "message": "Validation error",
        "errors": [{"loc": "date", "msg": "invalid format", "type": "value_error"}],
    }

    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(422, json=error_body)
        )

        async with NorthbeamClient(config) as client:
            with pytest.raises(Exception, match="Validation error"):
                await client.list_spend(date="bad-date")


async def test_list_spend_retries_on_500(config, sample_spend_response):
    with respx.mock:
        route = respx.get("https://api.northbeam.io/v1/spend")
        route.side_effect = [
            httpx.Response(500, json={"message": "Internal error"}),
            httpx.Response(200, json=sample_spend_response),
        ]

        async with NorthbeamClient(config) as client:
            result = await client.list_spend(date="2026-04-20")

    assert len(result["data"]) == 1
    assert route.call_count == 2


async def test_list_spend_retries_on_429_with_retry_after(config, sample_spend_response):
    with respx.mock:
        route = respx.get("https://api.northbeam.io/v1/spend")
        route.side_effect = [
            httpx.Response(429, headers={"Retry-After": "0"}, json={"message": "Rate limited"}),
            httpx.Response(200, json=sample_spend_response),
        ]

        async with NorthbeamClient(config) as client:
            result = await client.list_spend(date="2026-04-20")

    assert len(result["data"]) == 1
    assert route.call_count == 2


async def test_list_spend_429_with_http_date_retry_after_falls_back(config, sample_spend_response):
    with respx.mock:
        route = respx.get("https://api.northbeam.io/v1/spend")
        route.side_effect = [
            httpx.Response(429, headers={"Retry-After": "Fri, 31 Dec 2026 23:59:59 GMT"}, json={"message": "Rate limited"}),
            httpx.Response(200, json=sample_spend_response),
        ]

        async with NorthbeamClient(config) as client:
            result = await client.list_spend(date="2026-04-20")

    assert len(result["data"]) == 1
    assert route.call_count == 2


async def test_list_spend_gives_up_after_max_retries(config):
    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(500, json={"message": "Down"})
        )

        async with NorthbeamClient(config) as client:
            with pytest.raises(Exception, match="Down"):
                await client.list_spend(date="2026-04-20")


async def test_list_spend_retries_on_network_error(config, sample_spend_response):
    with respx.mock:
        route = respx.get("https://api.northbeam.io/v1/spend")
        route.side_effect = [
            httpx.ConnectTimeout("Connection timed out"),
            httpx.Response(200, json=sample_spend_response),
        ]

        async with NorthbeamClient(config) as client:
            result = await client.list_spend(date="2026-04-20")

    assert len(result["data"]) == 1
    assert route.call_count == 2


async def test_list_spend_network_error_gives_up_after_max_retries(config):
    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            side_effect=httpx.ConnectTimeout("Connection timed out")
        )

        async with NorthbeamClient(config) as client:
            with pytest.raises(Exception, match="Network error"):
                await client.list_spend(date="2026-04-20")


async def test_list_spend_handles_non_json_500_response(config):
    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(502, text="<html>Bad Gateway</html>")
        )

        async with NorthbeamClient(config) as client:
            with pytest.raises(Exception, match="Bad Gateway"):
                await client.list_spend(date="2026-04-20")


async def test_list_spend_handles_html_403_response(config):
    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(403, text="<html>Forbidden</html>")
        )

        async with NorthbeamClient(config) as client:
            with pytest.raises(Exception, match="HTTP 403"):
                await client.list_spend(date="2026-04-20")


async def test_list_spend_429_exhaustion_raises_clear_error(config):
    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "0"}, json={"message": "Rate limited"})
        )

        async with NorthbeamClient(config) as client:
            with pytest.raises(Exception, match="Rate limited.*429.*max retries"):
                await client.list_spend(date="2026-04-20")


async def test_error_body_response_field_normalized_to_message(config):
    """Northbeam API docs use 'response' instead of 'message' in error bodies."""
    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(401, json={"status": "error", "response": "Invalid credentials"})
        )

        async with NorthbeamClient(config) as client:
            with pytest.raises(Exception, match="Invalid credentials"):
                await client.list_spend(date="2026-04-20")
