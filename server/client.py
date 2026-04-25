from __future__ import annotations

import asyncio
import csv
import io
from typing import Any

import httpx

from server.config import NorthbeamConfig

MAX_RETRIES = 3
INITIAL_BACKOFF = 0.5
EXPORT_POLL_INTERVAL = 2.0
EXPORT_POLL_TIMEOUT = 60.0
SAMPLE_SIZE = 20
DOWNLOAD_TIMEOUT = 60.0


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
        for current_page in range(1, MAX_PAGES + 1):
            params["page"] = current_page
            result = await self._request_with_retry("GET", "spend", params=params)
            page_data = result.get("data") or []
            if not page_data:
                break
            all_data.extend(page_data)
            if current_page >= (result.get("total_pages") or 1):
                break

        total_pages = result.get("total_pages") or 1
        return {
            "data": all_data,
            "total_count": result.get("total_count") or len(all_data),
            "pages_fetched": current_page,
            "capped": current_page >= MAX_PAGES and total_pages > MAX_PAGES,
        }

    async def list_export_options(self) -> dict[str, Any]:
        async with asyncio.TaskGroup() as tg:
            bd_task = tg.create_task(
                self._request_with_retry("GET", "exports/breakdowns")
            )
            met_task = tg.create_task(
                self._request_with_retry("GET", "exports/metrics")
            )
            mod_task = tg.create_task(
                self._request_with_retry("GET", "exports/attribution-models")
            )
        return {
            "breakdowns": bd_task.result(),
            "metrics": met_task.result(),
            "attribution_models": mod_task.result(),
        }

    async def create_data_export(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request_with_retry(
            "POST", "exports/data-export", json=body
        )

    async def poll_export_result(self, export_id: str) -> dict[str, Any]:
        try:
            async with asyncio.timeout(EXPORT_POLL_TIMEOUT):
                while True:
                    result = await self._request_with_retry(
                        "GET", f"exports/data-export/result/{export_id}"
                    )
                    status = result.get("status", "").upper()
                    if status == "COMPLETED":
                        return result
                    if status == "FAILED":
                        raise NorthbeamAPIError(
                            f"Export {export_id} failed: "
                            f"{result.get('error', 'unknown')}"
                        )
                    await asyncio.sleep(EXPORT_POLL_INTERVAL)
        except TimeoutError as e:
            raise NorthbeamAPIError(
                f"Export {export_id} timed out after {EXPORT_POLL_TIMEOUT}s"
            ) from e

    async def download_export_csv(
        self, download_url: str, sample_size: int = SAMPLE_SIZE
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT) as http:
            response = await http.get(download_url)
            response.raise_for_status()
            text = response.text
            if not text.strip():
                return {"data": [], "total_rows": 0, "columns": []}

        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            return {"data": [], "total_rows": 0, "columns": []}

        rows: list[dict[str, str]] = []
        total_rows = 0
        for row in reader:
            if total_rows < sample_size:
                rows.append(dict(row))
            total_rows += 1

        return {"data": rows, "total_rows": total_rows, "columns": list(reader.fieldnames)}

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._http is None:
            raise RuntimeError("NorthbeamClient must be used as an async context manager")

        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                response = await self._http.request(method, path, params=params, json=json)
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
