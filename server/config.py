from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_URLS = {
    "prod": "https://api.northbeam.io/v1",
    "uat": "https://api-uat.northbeam.io/v1",
}

CLAUDE_OPTION_PREFIX = "CLAUDE_PLUGIN_OPTION_"


class NorthbeamConfigError(Exception):
    pass


def _is_missing_config_value(value: str | None) -> bool:
    if value is None:
        return True

    stripped = value.strip()
    if not stripped:
        return True

    return stripped.startswith("${") and stripped.endswith("}")


def _strip_inline_comment(value: str) -> str:
    in_single_quote = False
    in_double_quote = False

    for index, char in enumerate(value):
        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
        elif char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
        elif (
            char == "#"
            and not in_single_quote
            and not in_double_quote
            and (index == 0 or value[index - 1].isspace())
        ):
            return value[:index].rstrip()

    return value


def _dotenv_candidates(dotenv_path: str | None) -> list[Path]:
    if dotenv_path is not None:
        return [Path(dotenv_path).expanduser()]

    candidates: list[Path] = []
    explicit_path = os.environ.get("NORTHBEAM_CREDENTIALS_FILE")
    if explicit_path:
        candidates.append(Path(explicit_path).expanduser())

    candidates.append(Path(".env"))
    parent_cwd = os.environ.get("PWD")
    if parent_cwd:
        candidates.append(Path(parent_cwd).expanduser() / ".env")

    home = Path.home()
    candidates.extend([
        home / ".codex" / "northbeam.env",
        home / ".northbeam" / "env",
    ])

    unique_candidates: list[Path] = []
    for path in candidates:
        if path not in unique_candidates:
            unique_candidates.append(path)
    return unique_candidates


def load_dotenv(dotenv_path: str | None = None) -> None:
    for path in _dotenv_candidates(dotenv_path):
        if not path.is_file():
            continue

        try:
            dotenv_file = path.open("r", encoding="utf-8")
        except OSError:
            continue

        with dotenv_file:
            for raw_line in dotenv_file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue

                key, value = line.split("=", 1)
                key = key.strip()
                if key.startswith("export "):
                    key = key[len("export "):].strip()
                if not key:
                    continue

                value = _strip_inline_comment(value.strip()).strip("'\"")
                if _is_missing_config_value(os.environ.get(key)):
                    os.environ[key] = value


def _get_config_value(name: str) -> str | None:
    for key in (name, f"{CLAUDE_OPTION_PREFIX}{name}"):
        value = os.getenv(key)
        if not _is_missing_config_value(value):
            return value.strip()
    return None


@dataclass(frozen=True)
class NorthbeamConfig:
    api_key: str
    client_id: str
    base_url: str
    environment: str

    def auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": self.api_key,
            "Data-Client-ID": self.client_id,
            "Content-Type": "application/json",
        }


def load_config() -> NorthbeamConfig:
    load_dotenv()

    api_key = _get_config_value("NORTHBEAM_API_KEY")
    if api_key is None:
        raise NorthbeamConfigError(
            "NORTHBEAM_API_KEY is not configured. "
            "Run /northbeam:setup for configuration instructions."
        )

    client_id = _get_config_value("NORTHBEAM_CLIENT_ID")
    if client_id is None:
        raise NorthbeamConfigError(
            "NORTHBEAM_CLIENT_ID is not configured. "
            "Run /northbeam:setup for configuration instructions."
        )

    environment = (_get_config_value("NORTHBEAM_API_ENV") or "prod").lower()
    base_url = BASE_URLS.get(environment, BASE_URLS["prod"])

    return NorthbeamConfig(
        api_key=api_key,
        client_id=client_id,
        base_url=base_url,
        environment=environment,
    )
