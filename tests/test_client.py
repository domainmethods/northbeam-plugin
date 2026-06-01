import httpx
import pytest
import respx
from server.client import NorthbeamClient, NorthbeamAuthError, NorthbeamAPIError


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


async def test_non_dict_json_error_body_handled(config):
    """Proxies/WAFs may return JSON-encoded strings instead of objects."""
    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(500, json="Internal Server Error")
        )

        async with NorthbeamClient(config) as client:
            with pytest.raises(Exception, match="Internal Server Error"):
                await client.list_spend(date="2026-04-20")


async def test_base_url_resolves_correctly(config):
    """Verify trailing-slash base URL + relative path produces correct URL."""
    with respx.mock:
        route = respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(200, json={
                "data": [], "page": 1, "page_size": 1000,
                "total_pages": 1, "total_count": 0,
            })
        )

        async with NorthbeamClient(config) as client:
            await client.list_spend(date="2026-04-20")

        assert route.called
        assert str(route.calls[0].request.url).startswith("https://api.northbeam.io/v1/spend")


async def test_200_with_non_json_body_raises_api_error(config):
    """A WAF or proxy may return 200 OK with HTML instead of JSON."""
    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(200, text="<html>OK</html>")
        )

        async with NorthbeamClient(config) as client:
            with pytest.raises(Exception, match="Invalid JSON response"):
                await client.list_spend(date="2026-04-20")


async def test_list_export_options_returns_combined_metadata(config):
    breakdowns_resp = {"data": [{"id": "platform", "name": "Platform"}]}
    metrics_resp = {"data": [{"id": "revenue", "name": "Revenue"}]}
    models_resp = {"data": [{"id": "northbeam_custom__va", "name": "Northbeam Custom VA"}]}

    with respx.mock:
        respx.get("https://api.northbeam.io/v1/exports/breakdowns").mock(
            return_value=httpx.Response(200, json=breakdowns_resp)
        )
        respx.get("https://api.northbeam.io/v1/exports/metrics").mock(
            return_value=httpx.Response(200, json=metrics_resp)
        )
        respx.get("https://api.northbeam.io/v1/exports/attribution-models").mock(
            return_value=httpx.Response(200, json=models_resp)
        )

        async with NorthbeamClient(config) as client:
            result = await client.list_export_options()

    assert result["breakdowns"] == breakdowns_resp
    assert result["metrics"] == metrics_resp
    assert result["attribution_models"] == models_resp


async def test_list_export_options_auth_error_propagates(config):
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

        async with NorthbeamClient(config) as client:
            with pytest.raises(ExceptionGroup) as exc_info:
                await client.list_export_options()

    assert any(
        isinstance(e, NorthbeamAuthError) for e in exc_info.value.exceptions
    )


async def test_create_data_export_sends_post_with_body(config):
    request_body = {
        "date_start": "2026-04-14",
        "date_end": "2026-04-20",
        "attribution_model": "northbeam_custom__va",
        "attribution_window": "7",
        "breakdowns": ["platform"],
        "metrics": ["revenue"],
    }

    with respx.mock:
        route = respx.post("https://api.northbeam.io/v1/exports/data-export").mock(
            return_value=httpx.Response(200, json={"export_id": "exp-abc"})
        )

        async with NorthbeamClient(config) as client:
            result = await client.create_data_export(request_body)

    assert result["export_id"] == "exp-abc"
    assert route.called


async def test_create_data_export_422_singular_error_array_includes_detail(config):
    with respx.mock:
        respx.post("https://api.northbeam.io/v1/exports/data-export").mock(
            return_value=httpx.Response(
                422,
                json={"error": [{"loc": ["metrics", 0], "msg": "bad"}]},
            )
        )

        async with NorthbeamClient(config) as client:
            with pytest.raises(Exception, match=r"metrics.*bad"):
                await client.create_data_export({"metrics": [{"id": "bad"}]})


async def test_poll_export_result_returns_on_completed(config):
    with respx.mock:
        route = respx.get("https://api.northbeam.io/v1/exports/data-export/result/exp-abc")
        route.side_effect = [
            httpx.Response(200, json={"status": "PENDING"}),
            httpx.Response(200, json={"status": "PROCESSING"}),
            httpx.Response(200, json={
                "status": "COMPLETED",
                "download_url": "https://storage.example.com/export.csv",
            }),
        ]

        async with NorthbeamClient(config) as client:
            result = await client.poll_export_result("exp-abc")

    assert result["status"] == "COMPLETED"
    assert result["download_url"] == "https://storage.example.com/export.csv"
    assert route.call_count == 3


async def test_poll_export_result_returns_on_success(config, monkeypatch):
    import server.client as client_module
    monkeypatch.setattr(client_module, "EXPORT_POLL_INTERVAL", 0.01)

    with respx.mock:
        route = respx.get("https://api.northbeam.io/v1/exports/data-export/result/exp-success")
        route.side_effect = [
            httpx.Response(200, json={"status": "PENDING"}),
            httpx.Response(200, json={
                "status": "SUCCESS",
                "result": ["https://storage.example.com/export.csv"],
            }),
        ]

        async with NorthbeamClient(config) as client:
            result = await client.poll_export_result("exp-success")

    assert result["status"] == "SUCCESS"
    assert result["result"] == ["https://storage.example.com/export.csv"]
    assert route.call_count == 2


async def test_poll_export_result_raises_on_failed(config):
    with respx.mock:
        respx.get("https://api.northbeam.io/v1/exports/data-export/result/exp-fail").mock(
            return_value=httpx.Response(200, json={
                "status": "FAILED",
                "error": "Invalid metrics",
            })
        )

        async with NorthbeamClient(config) as client:
            with pytest.raises(NorthbeamAPIError, match="exp-fail failed.*Invalid metrics"):
                await client.poll_export_result("exp-fail")


async def test_poll_export_result_raises_on_timeout(config, monkeypatch):
    import server.client as client_module
    monkeypatch.setattr(client_module, "EXPORT_POLL_TIMEOUT", 0.1)
    monkeypatch.setattr(client_module, "EXPORT_POLL_INTERVAL", 0.05)

    with respx.mock:
        respx.get("https://api.northbeam.io/v1/exports/data-export/result/exp-slow").mock(
            return_value=httpx.Response(200, json={"status": "PROCESSING"})
        )

        async with NorthbeamClient(config) as client:
            with pytest.raises(NorthbeamAPIError, match="timed out"):
                await client.poll_export_result("exp-slow")


async def test_request_with_retry_sends_json_body(config):
    request_body = {"date_start": "2026-04-14", "metrics": ["revenue"]}
    response_body = {"export_id": "exp-123"}

    with respx.mock:
        route = respx.post("https://api.northbeam.io/v1/exports/data-export").mock(
            return_value=httpx.Response(200, json=response_body)
        )

        async with NorthbeamClient(config) as client:
            result = await client._request_with_retry(
                "POST", "exports/data-export", json=request_body
            )

    assert result == response_body
    assert route.called
    sent = route.calls[0].request
    assert sent.headers["content-type"] == "application/json"


async def test_download_export_csv_parses_csv(config):
    csv_content = "platform,revenue,roas\nFacebook,1000.50,3.2\nTikTok,500.25,2.1\n"

    with respx.mock:
        respx.get("https://storage.example.com/export.csv").mock(
            return_value=httpx.Response(200, text=csv_content)
        )

        async with NorthbeamClient(config) as client:
            result = await client.download_export_csv(
                "https://storage.example.com/export.csv"
            )

    assert result["total_rows"] == 2
    assert result["columns"] == ["platform", "revenue", "roas"]
    assert len(result["data"]) == 2
    assert result["data"][0]["platform"] == "Facebook"
    assert result["data"][0]["revenue"] == "1000.50"
    assert result["data"][1]["platform"] == "TikTok"


async def test_download_export_csv_returns_all_rows(config):
    header = "platform,revenue\n"
    rows = "".join(f"Platform{i},{i * 100}\n" for i in range(200))
    csv_content = header + rows

    with respx.mock:
        respx.get("https://storage.example.com/big.csv").mock(
            return_value=httpx.Response(200, text=csv_content)
        )

        async with NorthbeamClient(config) as client:
            result = await client.download_export_csv(
                "https://storage.example.com/big.csv"
            )

    assert result["total_rows"] == 200
    assert len(result["data"]) == 200
    assert result["data"][0]["platform"] == "Platform0"
    assert result["data"][199]["platform"] == "Platform199"


async def test_download_export_csv_handles_empty_csv(config):
    csv_content = "platform,revenue\n"

    with respx.mock:
        respx.get("https://storage.example.com/empty.csv").mock(
            return_value=httpx.Response(200, text=csv_content)
        )

        async with NorthbeamClient(config) as client:
            result = await client.download_export_csv(
                "https://storage.example.com/empty.csv"
            )

    assert result["total_rows"] == 0
    assert result["data"] == []
    assert result["columns"] == ["platform", "revenue"]


async def test_download_export_csv_handles_no_content(config):
    with respx.mock:
        respx.get("https://storage.example.com/nothing.csv").mock(
            return_value=httpx.Response(200, text="")
        )

        async with NorthbeamClient(config) as client:
            result = await client.download_export_csv(
                "https://storage.example.com/nothing.csv"
            )

    assert result["total_rows"] == 0
    assert result["data"] == []
    assert result["columns"] == []


async def test_download_export_csv_does_not_send_auth_headers(config):
    csv_content = "platform,revenue\nFacebook,1000\n"

    with respx.mock:
        route = respx.get("https://storage.example.com/export.csv").mock(
            return_value=httpx.Response(200, text=csv_content)
        )

        async with NorthbeamClient(config) as client:
            await client.download_export_csv(
                "https://storage.example.com/export.csv"
            )

    sent_headers = dict(route.calls[0].request.headers)
    assert "authorization" not in sent_headers
    assert "data-client-id" not in sent_headers


async def test_download_export_csv_handles_quoted_newlines(config):
    csv_content = 'platform,campaign_name,revenue\nFacebook,"Spring\nPromo",1500\nTikTok,Summer,800\n'

    with respx.mock:
        respx.get("https://storage.example.com/export.csv").mock(
            return_value=httpx.Response(200, text=csv_content)
        )

        async with NorthbeamClient(config) as client:
            result = await client.download_export_csv(
                "https://storage.example.com/export.csv"
            )

    assert result["total_rows"] == 2
    assert len(result["data"]) == 2
    assert result["data"][0]["campaign_name"] == "Spring\nPromo"
    assert result["data"][1]["platform"] == "TikTok"
