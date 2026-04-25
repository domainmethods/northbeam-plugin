from __future__ import annotations

import os
from dataclasses import dataclass

BASE_URLS = {
    "prod": "https://api.northbeam.io/v1",
    "uat": "https://api-uat.northbeam.io/v1",
}


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
    api_key = os.getenv("NORTHBEAM_API_KEY")
    if not api_key:
        raise ValueError(
            "NORTHBEAM_API_KEY environment variable is not set. "
            "Run /northbeam:setup for configuration instructions."
        )

    client_id = os.getenv("NORTHBEAM_CLIENT_ID")
    if not client_id:
        raise ValueError(
            "NORTHBEAM_CLIENT_ID environment variable is not set. "
            "Run /northbeam:setup for configuration instructions."
        )

    environment = os.getenv("NORTHBEAM_API_ENV", "prod").lower()
    base_url = BASE_URLS.get(environment, BASE_URLS["prod"])

    return NorthbeamConfig(
        api_key=api_key,
        client_id=client_id,
        base_url=base_url,
        environment=environment,
    )
