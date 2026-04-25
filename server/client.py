from __future__ import annotations

import asyncio
from typing import Any

import httpx

from server.config import NorthbeamConfig

MAX_RETRIES = 3
INITIAL_BACKOFF = 0.5


class NorthbeamAuthError(Exception):
    pass


class NorthbeamValidationError(Exception):
    pass


class NorthbeamAPIError(Exception):
    pass


class NorthbeamClient:
    def __init__(self, config: NorthbeamConfig) -> None:
        self._config = config
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> NorthbeamClient:
        base_url = self._config.base_url
        if not base_url.endswith("/"):
            base_url += "/"
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers=self._config.auth_headers(),
            timeout=30.0,
        )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._http:
            await self._http.aclose()

    async def list_spend(
        self,
        *,
        date: str | None = None,
        date_start: str | None = None,
        date_end: str | None = None,
        platform_account_id: str | None = None,
        campaign_id: str | None = None,
        adset_id: str | None = None,
        ad_id: str | None = None,
        page: int = 1,
        page_size: int = 1000,
        fetch_all: bool = False,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            k: v for k, v in {
                "page": page,
                "page_size": page_size,
                "date": date,
                "date_start": date_start,
                "date_end": date_end,
                "platform_account_id": platform_account_id,
                "campaign_id": campaign_id,
                "adset_id": adset_id,
                "ad_id": ad_id,
            }.items() if v is not None
        }

        if not fetch_all:
            return await self._request_with_retry("GET", "spend", params=params)

        MAX_PAGES = 50
        all_data: list[dict] = []
        current_page = 1
        while True:
            params["page"] = current_page
            result = await self._request_with_retry("GET", "spend", params=params)
            all_data.extend(result.get("data") or [])
            total_pages = result.get("total_pages") or 1
            if current_page >= min(total_pages, MAX_PAGES):
                break
            current_page += 1

        total_pages = result.get("total_pages") or 1
        return {
            "data": all_data,
            "total_count": result.get("total_count") or len(all_data),
            "pages_fetched": current_page,
            "capped": current_page >= MAX_PAGES and total_pages > MAX_PAGES,
        }

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._http is None:
            raise RuntimeError("NorthbeamClient must be used as an async context manager")

        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                response = await self._http.request(method, path, params=params)
            except httpx.RequestError as e:
                last_error = NorthbeamAPIError(f"Network error: {e}")
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(INITIAL_BACKOFF * (2 ** attempt))
                    continue
                raise last_error

            if response.status_code == 401:
                body = self._parse_body(response)
                raise NorthbeamAuthError(
                    f"Authentication failed. {body.get('message', '')} "
                    "Run /northbeam:setup to check credentials."
                )

            if response.status_code == 422:
                body = self._parse_body(response)
                errors = body.get("errors", [])
                detail = "; ".join(
                    f"{e.get('loc', 'unknown')}: {e.get('msg', 'error')}"
                    for e in errors if isinstance(e, dict)
                ) if isinstance(errors, list) else ""
                raise NorthbeamValidationError(
                    f"Validation error: {body.get('message', '')} {detail}".strip()
                )

            if response.status_code == 429:
                if attempt < MAX_RETRIES - 1:
                    try:
                        retry_after = float(response.headers.get("Retry-After", INITIAL_BACKOFF))
                    except ValueError:
                        retry_after = INITIAL_BACKOFF * (2 ** attempt)
                    await asyncio.sleep(retry_after)
                    continue
                raise NorthbeamAPIError("Rate limited (429) after max retries")

            if response.status_code >= 500:
                body = self._parse_body(response)
                last_error = NorthbeamAPIError(body.get("message", f"Server error {response.status_code}"))
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(INITIAL_BACKOFF * (2 ** attempt))
                    continue
                raise last_error

            if response.status_code >= 400:
                body = self._parse_body(response)
                raise NorthbeamAPIError(f"HTTP {response.status_code}: {body.get('message', 'Unknown error')}")

            try:
                return response.json()
            except ValueError:
                raise NorthbeamAPIError(f"Invalid JSON response: {response.text}")

        raise last_error or NorthbeamAPIError("Request failed after retries")

    @staticmethod
    def _parse_body(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
            if not isinstance(body, dict):
                return {"message": str(body)}
        except ValueError:
            return {"message": response.text}
        if "message" not in body and "response" in body:
            body["message"] = body["response"]
        return body
