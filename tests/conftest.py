import pytest
from server.config import NorthbeamConfig


@pytest.fixture
def config():
    return NorthbeamConfig(
        api_key="test-key",
        client_id="test-client",
        base_url="https://api.northbeam.io/v1",
        environment="prod",
    )


@pytest.fixture
def sample_spend_record():
    return {
        "date": "2026-04-20",
        "platform_name": "Facebook",
        "platform_account_id": "fb-123",
        "campaign_id": "camp-1",
        "campaign_name": "FB_Prospecting_LAL1_US_Q2_Video",
        "adset_id": "adset-1",
        "adset_name": "LAL 1% US",
        "ad_id": "ad-1",
        "ad_name": "Video Creative A",
        "spend": 150.00,
        "spend_currency": "USD",
        "impressions": 12000,
        "clicks": 180,
        "created_at": "2026-04-20T06:00:00Z",
        "updated_at": "2026-04-20T18:00:00Z",
    }


@pytest.fixture
def sample_spend_response(sample_spend_record):
    return {
        "data": [sample_spend_record],
        "page": 1,
        "page_size": 1000,
        "total_pages": 1,
        "total_count": 1,
    }


@pytest.fixture
def sample_export_options():
    return {
        "breakdowns": {
            "breakdowns": [
                {"key": "Platform (Northbeam)", "values": ["Facebook Ads", "TikTok"]},
                {"key": "Category (Northbeam)", "values": ["Email", "Paid - Prospecting"]},
            ]
        },
        "metrics": {"metrics": [
            {"id": "revAttributed", "label": "Attributed Rev"},
            {"id": "txns", "label": "Transactions"},
            {"id": "spend", "label": "Spend"},
        ]},
        "attribution_models": {
            "attribution_models": [
                {"id": "northbeam_custom", "name": "Clicks only"},
                {"id": "northbeam_custom__enh", "name": "Clicks + Deterministic Views"},
                {"id": "northbeam_custom__va", "name": "Clicks + Modeled Views"},
            ]
        },
    }


@pytest.fixture
def sample_export_create_response():
    return {"id": "exp-test-123"}


@pytest.fixture
def sample_export_completed_response():
    return {
        "status": "SUCCESS",
        "result": ["https://storage.example.com/export.csv"],
    }


@pytest.fixture
def sample_export_csv():
    return (
        # Live export returns attributed revenue in column `attributed_rev`
        # (requested metric id is `revAttributed`); use the real column name so
        # the metric-id->column alias is exercised end-to-end.
        "breakdown_platform_northbeam,campaign_name,attributed_rev,roas\n"
        "Facebook Ads,FB_Prospecting,1500.00,3.20\n"
        "TikTok,TT_Retargeting,800.00,2.10\n"
    )
