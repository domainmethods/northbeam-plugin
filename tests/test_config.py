import pytest
from server.config import load_config, NorthbeamConfig


def test_load_config_from_env(monkeypatch):
    monkeypatch.setenv("NORTHBEAM_API_KEY", "test-key-123")
    monkeypatch.setenv("NORTHBEAM_CLIENT_ID", "test-client-456")
    monkeypatch.delenv("NORTHBEAM_API_ENV", raising=False)

    config = load_config()

    assert config.api_key == "test-key-123"
    assert config.client_id == "test-client-456"
    assert config.base_url == "https://api.northbeam.io/v1"
    assert config.environment == "prod"


def test_load_config_uat_environment(monkeypatch):
    monkeypatch.setenv("NORTHBEAM_API_KEY", "test-key")
    monkeypatch.setenv("NORTHBEAM_CLIENT_ID", "test-client")
    monkeypatch.setenv("NORTHBEAM_API_ENV", "uat")

    config = load_config()

    assert config.base_url == "https://api-uat.northbeam.io/v1"
    assert config.environment == "uat"


def test_load_config_missing_api_key(monkeypatch):
    monkeypatch.delenv("NORTHBEAM_API_KEY", raising=False)
    monkeypatch.setenv("NORTHBEAM_CLIENT_ID", "test-client")

    with pytest.raises(ValueError, match="NORTHBEAM_API_KEY"):
        load_config()


def test_load_config_missing_client_id(monkeypatch):
    monkeypatch.setenv("NORTHBEAM_API_KEY", "test-key")
    monkeypatch.delenv("NORTHBEAM_CLIENT_ID", raising=False)

    with pytest.raises(ValueError, match="NORTHBEAM_CLIENT_ID"):
        load_config()


def test_config_auth_headers():
    config = NorthbeamConfig(
        api_key="my-key",
        client_id="my-client",
        base_url="https://api.northbeam.io/v1",
        environment="prod",
    )

    headers = config.auth_headers()

    assert headers == {
        "Authorization": "my-key",
        "Data-Client-ID": "my-client",
        "Content-Type": "application/json",
    }
