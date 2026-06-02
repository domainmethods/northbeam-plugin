from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
from typing import Any

import httpx

from server.config import NorthbeamConfig

MAX_RETRIES = 3
INITIAL_BACKOFF = 0.5
EXPORT_POLL_INTERVAL = 2.0


def _resolve_poll_timeout() -> float:
    """Read the export poll timeout (seconds) from the environment.

    Factored out so tests can verify env handling without reloading the module
    (reloading rebinds this module's exception classes and breaks isinstance
    checks in already-imported callers)."""
    return float(os.environ.get("NORTHBEAM_EXPORT_TIMEOUT", "180.0"))


EXPORT_POLL_TIMEOUT = _resolve_poll_timeout()
DOWNLOAD_TIMEOUT = 60.0
EXPORT_SUCCESS_STATUSES = {"COMPLETED", "SUCCESS"}
EXPORT_FAILURE_STATUSES = {"FAILED", "FAILURE"}
HTTP_CLIENT_LOGGERS = ("httpx", "httpcore")


def _suppress_http_client_info_logging() -> None:
    for logger_name in HTTP_CLIENT_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


_suppress_http_client_info_logging()


class NorthbeamAuthError(Exception):
    pass


class NorthbeamValidationError(Exception):
    pass


class NorthbeamAPIError(Exception):
    pass


def _normalize_export_status(status: Any) -> str:
    return str(status or "").strip().upper()


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
            "capped": total_pages > MAX_PAGES,
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
        last_status = "PENDING"
        try:
            async with asyncio.timeout(EXPORT_POLL_TIMEOUT):
                while True:
                    result = await self._request_with_retry(
                        "GET", f"exports/data-export/result/{export_id}"
                    )
                    status = _normalize_export_status(result.get("status"))
                    if status:
                        last_status = status
                    if status in EXPORT_SUCCESS_STATUSES:
                        return result
                    if status in EXPORT_FAILURE_STATUSES:
                        raise NorthbeamAPIError(
                            f"Export {export_id} failed: "
                            f"{result.get('error', 'unknown')}"
                        )
                    await asyncio.sleep(EXPORT_POLL_INTERVAL)
        except TimeoutError as e:
            raise NorthbeamAPIError(
                f"Export {export_id} did not finish within "
                f"{EXPORT_POLL_TIMEOUT:.0f}s (last status: {last_status}). "
                "Northbeam's export queue may be busy — retry shortly."
            ) from e

    async def download_export_csv(
        self, download_url: str
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT) as http:
                response = await http.get(download_url)
                response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise NorthbeamAPIError(
                f"Export CSV download failed with HTTP {e.response.status_code}"
            ) from None
        except httpx.RequestError as e:
            raise NorthbeamAPIError(
                f"Export CSV download failed: {e.__class__.__name__}"
            ) from None

        # Northbeam's signed-URL CSVs are often UTF-8 with a BOM; decoding as
        # utf-8-sig strips it so the first column header isn't read as
        # "﻿breakdown_..." and silently failing every column lookup.
        try:
            text = response.content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = response.text

        reader = csv.DictReader(io.StringIO(text))
        columns = list(reader.fieldnames or [])
        if not columns:
            return {"data": [], "total_rows": 0, "columns": []}

        rows = list(reader)
        return {"data": rows, "total_rows": len(rows), "columns": columns}

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
                errors = body.get("errors", body.get("error", []))
                detail = "; ".join(
                    f"{e.get('loc', 'unknown')}: {e.get('msg', 'error')}"
                    for e in errors if isinstance(e, dict)
                ) if isinstance(errors, list) else ""
                raise NorthbeamValidationError(
                    f"Validation error: {body.get('message', '')} {detail}".strip()
                )

            if response.status_code == 429:
                if attempt < MAX_RETRIES - 1:
                    retry_after_header = response.headers.get("Retry-After")
                    try:
                        # Honor a numeric Retry-After; otherwise (missing header
                        # or an HTTP-date we don't parse) back off exponentially.
                        retry_after = (
                            float(retry_after_header)
                            if retry_after_header is not None
                            else INITIAL_BACKOFF * (2 ** attempt)
                        )
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
                # Don't echo the raw body — it may carry signed-URL tokens or
                # other sensitive content into logs/clients.
                raise NorthbeamAPIError(
                    f"Invalid JSON response from Northbeam API (HTTP {response.status_code})"
                )

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
