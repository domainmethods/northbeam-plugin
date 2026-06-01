import httpx
import pytest
import respx
from mcp.server.fastmcp.exceptions import ToolError
from server.config import NorthbeamConfig
from server.northbeam_mcp import _list_spend, _check_connection
import server.client as client_module


@pytest.fixture
def config():
    return NorthbeamConfig(
        api_key="integration-key",
        client_id="integration-client",
        base_url="https://api.northbeam.io/v1",
        environment="prod",
    )


def _make_spend_records(count: int, platform: str = "Facebook") -> list[dict]:
    records = []
    for i in range(count):
        records.append({
            "date": f"2026-04-{20 - i:02d}",
            "platform_name": platform,
            "platform_account_id": f"{platform.lower()}-acct",
            "campaign_id": f"camp-{i}",
            "campaign_name": f"{platform[:2].upper()}_Prospecting_LAL1_US_Q2_Video",
            "adset_id": f"adset-{i}",
            "adset_name": f"Adset {i}",
            "ad_id": f"ad-{i}",
            "ad_name": f"Ad {i}",
            "spend": 100.0 + i * 10,
            "spend_currency": "USD",
            "impressions": 10000 + i * 500,
            "clicks": 150 + i * 10,
            "created_at": f"2026-04-{20 - i:02d}T06:00:00Z",
            "updated_at": f"2026-04-{20 - i:02d}T18:00:00Z",
        })
    return records


async def test_full_query_with_multiple_platforms(config):
    fb_records = _make_spend_records(3, "Facebook")
    tt_records = _make_spend_records(2, "TikTok")
    all_records = fb_records + tt_records

    response_body = {
        "data": all_records,
        "page": 1,
        "page_size": 1000,
        "total_pages": 1,
        "total_count": 5,
    }

    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(200, json=response_body)
        )

        result = await _list_spend(
            config=config,
            date_start="2026-04-14",
            date_end="2026-04-20",
            fetch_all=True,
        )

    assert result["total_count"] == 5
    assert len(result["data"]) == 5

    platforms = set(r["platform_name"] for r in result["data"])
    assert platforms == {"Facebook", "TikTok"}


async def test_full_pagination_across_pages(config):
    page1_records = _make_spend_records(2, "Facebook")
    page2_records = _make_spend_records(2, "TikTok")

    page1 = {
        "data": page1_records,
        "page": 1,
        "page_size": 2,
        "total_pages": 2,
        "total_count": 4,
    }
    page2 = {
        "data": page2_records,
        "page": 2,
        "page_size": 2,
        "total_pages": 2,
        "total_count": 4,
    }

    with respx.mock:
        route = respx.get("https://api.northbeam.io/v1/spend")
        route.side_effect = [
            httpx.Response(200, json=page1),
            httpx.Response(200, json=page2),
        ]

        result = await _list_spend(
            config=config,
            date_start="2026-04-14",
            date_end="2026-04-20",
            fetch_all=True,
        )

    assert len(result["data"]) == 4
    assert result["pages_fetched"] == 2


async def test_check_connection_shows_all_platforms(config, monkeypatch):
    monkeypatch.setattr(client_module, "EXPORT_POLL_INTERVAL", 0.01)
    records = _make_spend_records(2, "Facebook") + _make_spend_records(1, "Google")
    response_body = {
        "data": records,
        "page": 1,
        "page_size": 1000,
        "total_pages": 1,
        "total_count": 3,
    }
    csv_content = "transactions,rev\n5,1234.56\n"

    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(200, json=response_body)
        )
        respx.get("https://api.northbeam.io/v1/exports/breakdowns").mock(
            return_value=httpx.Response(200, json={"breakdowns": []})
        )
        respx.get("https://api.northbeam.io/v1/exports/metrics").mock(
            return_value=httpx.Response(200, json={"metrics": []})
        )
        respx.get("https://api.northbeam.io/v1/exports/attribution-models").mock(
            return_value=httpx.Response(200, json={"attribution_models": []})
        )
        respx.post("https://api.northbeam.io/v1/exports/data-export").mock(
            return_value=httpx.Response(201, json={"id": "exp-integration-123"})
        )
        respx.get("https://api.northbeam.io/v1/exports/data-export/result/exp-integration-123").mock(
            return_value=httpx.Response(
                200,
                json={"status": "SUCCESS", "result": ["https://storage.example.com/export.csv"]},
            )
        )
        respx.get("https://storage.example.com/export.csv").mock(
            return_value=httpx.Response(200, text=csv_content)
        )

        result = await _check_connection(config=config)

    assert "Status: Connected" in result
    assert "Environment: prod" in result
    assert "Spend API: OK - 3 spend rows" in result
    assert "Data Export metadata: OK" in result
    assert "Data Export API: OK - transactions=5.00, revenue=1234.56" in result
    assert "Facebook" in result
    assert "Google" in result


async def test_connection_failure_raises_tool_error(config):
    with respx.mock:
        respx.get("https://api.northbeam.io/v1/spend").mock(
            return_value=httpx.Response(401, json={"message": "Invalid API key"})
        )

        with pytest.raises(ToolError, match="Not connected"):
            await _check_connection(config=config)
