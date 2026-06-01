import pytest
from server.config import (
    load_config,
    load_dotenv,
    NorthbeamConfig,
    NorthbeamConfigError,
)


def test_load_config_from_env(monkeypatch):
    monkeypatch.setenv("NORTHBEAM_API_KEY", "test-key-123")
    monkeypatch.setenv("NORTHBEAM_CLIENT_ID", "test-client-456")
    monkeypatch.delenv("NORTHBEAM_API_ENV", raising=False)

    config = load_config()

    assert config.api_key == "test-key-123"
    assert config.client_id == "test-client-456"
    assert config.base_url == "https://api.northbeam.io/v1"
    assert config.environment == "prod"


def test_load_config_from_dotenv_file(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PWD", str(tmp_path))
    monkeypatch.delenv("NORTHBEAM_API_KEY", raising=False)
    monkeypatch.delenv("NORTHBEAM_CLIENT_ID", raising=False)
    monkeypatch.delenv("NORTHBEAM_API_ENV", raising=False)
    tmp_path.joinpath(".env").write_text(
        "\n".join([
            "NORTHBEAM_API_KEY=dotenv-key",
            "NORTHBEAM_CLIENT_ID=dotenv-client",
            "NORTHBEAM_API_ENV=uat # use UAT",
        ]),
        encoding="utf-8",
    )

    config = load_config()

    assert config.api_key == "dotenv-key"
    assert config.client_id == "dotenv-client"
    assert config.environment == "uat"
    assert config.base_url == "https://api-uat.northbeam.io/v1"


def test_load_config_from_pwd_dotenv(monkeypatch, tmp_path):
    cache_dir = tmp_path / "cache"
    project_dir = tmp_path / "project"
    cache_dir.mkdir()
    project_dir.mkdir()
    monkeypatch.chdir(cache_dir)
    monkeypatch.setenv("PWD", str(project_dir))
    monkeypatch.delenv("NORTHBEAM_API_KEY", raising=False)
    monkeypatch.delenv("NORTHBEAM_CLIENT_ID", raising=False)
    monkeypatch.delenv("NORTHBEAM_API_ENV", raising=False)
    project_dir.joinpath(".env").write_text(
        "\n".join([
            "NORTHBEAM_API_KEY=pwd-dotenv-key",
            "NORTHBEAM_CLIENT_ID=pwd-dotenv-client",
        ]),
        encoding="utf-8",
    )

    config = load_config()

    assert config.api_key == "pwd-dotenv-key"
    assert config.client_id == "pwd-dotenv-client"


def test_load_dotenv_ignores_comments_blanks_and_malformed_lines(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PWD", str(tmp_path))
    monkeypatch.delenv("NORTHBEAM_API_KEY", raising=False)
    monkeypatch.delenv("NORTHBEAM_CLIENT_ID", raising=False)
    tmp_path.joinpath(".env").write_text(
        "\n".join([
            "",
            "# local Northbeam credentials",
            "NORTHBEAM_API_KEY='quoted-key'",
            "malformed",
            "NORTHBEAM_CLIENT_ID=\"quoted-client\"",
        ]),
        encoding="utf-8",
    )

    load_dotenv()

    assert load_config().api_key == "quoted-key"
    assert load_config().client_id == "quoted-client"


def test_load_dotenv_does_not_overwrite_existing_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PWD", str(tmp_path))
    monkeypatch.setenv("NORTHBEAM_API_KEY", "existing-key")
    monkeypatch.setenv("NORTHBEAM_CLIENT_ID", "existing-client")
    tmp_path.joinpath(".env").write_text(
        "\n".join([
            "NORTHBEAM_API_KEY=dotenv-key",
            "NORTHBEAM_CLIENT_ID=dotenv-client",
        ]),
        encoding="utf-8",
    )

    load_dotenv()

    assert load_config().api_key == "existing-key"
    assert load_config().client_id == "existing-client"


def test_load_dotenv_fills_empty_env_values(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PWD", str(tmp_path))
    monkeypatch.setenv("NORTHBEAM_API_KEY", "")
    monkeypatch.setenv("NORTHBEAM_CLIENT_ID", "")
    tmp_path.joinpath(".env").write_text(
        "\n".join([
            "NORTHBEAM_API_KEY=dotenv-key",
            "NORTHBEAM_CLIENT_ID=dotenv-client",
        ]),
        encoding="utf-8",
    )

    load_dotenv()

    assert load_config().api_key == "dotenv-key"
    assert load_config().client_id == "dotenv-client"


def test_load_config_uat_environment(monkeypatch):
    monkeypatch.setenv("NORTHBEAM_API_KEY", "test-key")
    monkeypatch.setenv("NORTHBEAM_CLIENT_ID", "test-client")
    monkeypatch.setenv("NORTHBEAM_API_ENV", "uat")

    config = load_config()

    assert config.base_url == "https://api-uat.northbeam.io/v1"
    assert config.environment == "uat"


def test_load_config_missing_api_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PWD", str(tmp_path))
    monkeypatch.delenv("NORTHBEAM_API_KEY", raising=False)
    monkeypatch.setenv("NORTHBEAM_CLIENT_ID", "test-client")

    with pytest.raises(NorthbeamConfigError, match="NORTHBEAM_API_KEY"):
        load_config()


def test_load_config_missing_client_id(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PWD", str(tmp_path))
    monkeypatch.setenv("NORTHBEAM_API_KEY", "test-key")
    monkeypatch.delenv("NORTHBEAM_CLIENT_ID", raising=False)

    with pytest.raises(NorthbeamConfigError, match="NORTHBEAM_CLIENT_ID"):
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
