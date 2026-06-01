import sys

from server.setup_credentials import main, write_codex_credentials


def test_write_codex_credentials_creates_private_file(tmp_path):
    path = tmp_path / ".codex" / "northbeam.env"

    result = write_codex_credentials(
        "test-key",
        "test-client",
        "uat",
        path,
    )

    assert result == path
    assert path.read_text(encoding="utf-8") == (
        "NORTHBEAM_API_KEY=test-key\n"
        "NORTHBEAM_CLIENT_ID=test-client\n"
        "NORTHBEAM_API_ENV=uat\n"
    )
    assert path.stat().st_mode & 0o777 == 0o600


def test_main_from_dotenv_defaults_environment(tmp_path, monkeypatch, capsys):
    dotenv = tmp_path / ".env"
    target = tmp_path / "northbeam.env"
    dotenv.write_text(
        "\n".join([
            "NORTHBEAM_API_KEY=test-key",
            "NORTHBEAM_CLIENT_ID=test-client",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "setup_credentials.py",
            "--from-dotenv",
            str(dotenv),
            "--path",
            str(target),
        ],
    )

    main()

    assert "Saved Northbeam credentials" in capsys.readouterr().out
    assert "NORTHBEAM_API_ENV=prod" in target.read_text(encoding="utf-8")
