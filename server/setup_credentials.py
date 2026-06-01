from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path

from server.config import _strip_inline_comment

DEFAULT_CODEX_CREDENTIALS_FILE = Path.home() / ".codex" / "northbeam.env"


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if not key:
            continue

        values[key] = _strip_inline_comment(value.strip()).strip("'\"")
    return values


def _prompt_value(label: str, *, secret: bool = False, default: str | None = None) -> str:
    prompt = f"{label}"
    if default:
        prompt += f" [{default}]"
    prompt += ": "

    value = getpass.getpass(prompt) if secret else input(prompt)
    if not value and default is not None:
        return default
    return value.strip()


def write_codex_credentials(
    api_key: str,
    client_id: str,
    environment: str = "prod",
    path: Path = DEFAULT_CODEX_CREDENTIALS_FILE,
) -> Path:
    path = path.expanduser()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    content = "\n".join([
        f"NORTHBEAM_API_KEY={api_key}",
        f"NORTHBEAM_CLIENT_ID={client_id}",
        f"NORTHBEAM_API_ENV={environment or 'prod'}",
        "",
    ])

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as credentials_file:
        credentials_file.write(content)
    os.chmod(path, 0o600)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Save Northbeam credentials for the Codex plugin."
    )
    parser.add_argument(
        "--from-dotenv",
        type=Path,
        help="Read credentials from an existing .env file instead of prompting.",
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=DEFAULT_CODEX_CREDENTIALS_FILE,
        help="Credential file to write. Defaults to ~/.codex/northbeam.env.",
    )
    args = parser.parse_args()

    values: dict[str, str] = {}
    if args.from_dotenv:
        values = _parse_env_file(args.from_dotenv.expanduser())

    api_key = values.get("NORTHBEAM_API_KEY") or _prompt_value(
        "Northbeam API key",
        secret=True,
    )
    client_id = values.get("NORTHBEAM_CLIENT_ID") or _prompt_value(
        "Northbeam Client ID",
        secret=True,
    )
    environment = values.get("NORTHBEAM_API_ENV")
    if not environment:
        environment = "prod" if args.from_dotenv else _prompt_value(
            "Northbeam API environment",
            default="prod",
        )

    path = write_codex_credentials(api_key, client_id, environment, args.path)
    print(f"Saved Northbeam credentials for Codex at {path}")
    print("Restart Codex or open a new thread, then run /northbeam:setup.")


if __name__ == "__main__":
    main()
