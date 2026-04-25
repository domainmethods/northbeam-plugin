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
        "breakdowns": {"data": [{"id": "platform", "name": "Platform"}]},
        "metrics": {"data": [{"id": "revenue", "name": "Revenue"}]},
        "attribution_models": {"data": [{"id": "northbeam_custom__va", "name": "NB Custom VA"}]},
    }


@pytest.fixture
def sample_export_create_response():
    return {"export_id": "exp-test-123"}


@pytest.fixture
def sample_export_completed_response():
    return {
        "status": "COMPLETED",
        "download_url": "https://storage.example.com/export.csv",
    }


@pytest.fixture
def sample_export_csv():
    return (
        "platform,campaign_name,revenue,roas\n"
        "Facebook,FB_Prospecting,1500.00,3.20\n"
        "TikTok,TT_Retargeting,800.00,2.10\n"
    )
