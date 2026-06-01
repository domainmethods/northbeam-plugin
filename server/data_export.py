from __future__ import annotations

from datetime import datetime
from typing import Any

BREAKDOWN_ALIASES = {
    "platform": "Platform (Northbeam)",
    "category": "Category (Northbeam)",
    "targeting": "Targeting (Northbeam)",
}

BREAKDOWN_COLUMN_ALIASES = {
    "Platform (Northbeam)": ["breakdown_platform_northbeam"],
    "Category (Northbeam)": ["breakdown_category_northbeam"],
    "Targeting (Northbeam)": ["breakdown_targeting_northbeam"],
}

METRIC_COLUMN_ALIASES = {
    "txns": ["transactions"],
    "impressions": ["imprs"],
}

DEFAULT_EXPORT_OPTIONS = {
    "export_aggregation": "BREAKDOWN",
    "remove_zero_spend": False,
    "aggregate_data": False,
    "include_ids": False,
    "include_kind_and_platform": False,
}


def normalize_breakdown_key(breakdown: str) -> str:
    return BREAKDOWN_ALIASES.get(breakdown.lower(), breakdown)


def _date_to_day_bounds(date_value: str) -> tuple[str, str]:
    parsed = datetime.strptime(date_value, "%Y-%m-%d").date()
    date_text = parsed.isoformat()
    return f"{date_text}T00:00:00Z", f"{date_text}T23:59:59Z"


def build_breakdown_value_lookup(options: dict[str, Any]) -> dict[str, list[str]]:
    raw_breakdowns = options.get("breakdowns", options)
    if isinstance(raw_breakdowns, dict):
        items = raw_breakdowns.get("breakdowns") or raw_breakdowns.get("data") or []
    elif isinstance(raw_breakdowns, list):
        items = raw_breakdowns
    else:
        items = []

    lookup: dict[str, list[str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = item.get("key") or item.get("name") or item.get("id")
        values = item.get("values") or []
        if key:
            lookup[str(key)] = list(values)
    return lookup


def build_data_export_payload(
    *,
    date_start: str,
    date_end: str,
    metrics: list[str],
    breakdowns: list[str],
    attribution_model: str = "northbeam_custom",
    attribution_window: str = "1",
    breakdown_values: dict[str, list[str]] | None = None,
    level: str = "platform",
    time_granularity: str = "DAILY",
    accounting_mode: str = "accrual",
) -> dict[str, Any]:
    period_starting_at, _ = _date_to_day_bounds(date_start)
    _, period_ending_at = _date_to_day_bounds(date_end)

    normalized_breakdowns: list[dict[str, Any]] = []
    for breakdown in breakdowns:
        key = normalize_breakdown_key(breakdown)
        values = (breakdown_values or {}).get(key)
        if not values:
            raise ValueError(f"Breakdown values required for {key}")
        normalized_breakdowns.append({"key": key, "values": values})

    return {
        "level": level,
        "time_granularity": time_granularity,
        "period_type": "FIXED",
        "period_options": {
            "period_starting_at": period_starting_at,
            "period_ending_at": period_ending_at,
        },
        "breakdowns": normalized_breakdowns,
        "options": dict(DEFAULT_EXPORT_OPTIONS),
        "attribution_options": {
            "attribution_models": [attribution_model],
            "accounting_modes": [accounting_mode],
            "attribution_windows": [attribution_window],
        },
        "metrics": [{"id": metric} for metric in metrics],
    }


def extract_export_id(create_result: dict[str, Any]) -> str | None:
    export_id = create_result.get("id") or create_result.get("export_id")
    return str(export_id) if export_id else None


def extract_download_url(poll_result: dict[str, Any]) -> str | None:
    if poll_result.get("download_url"):
        return str(poll_result["download_url"])

    result = poll_result.get("result")
    if isinstance(result, list) and result:
        return str(result[0])
    if isinstance(result, str):
        return result
    return None


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def metric_column_candidates(metric_id: str) -> list[str]:
    return _dedupe([metric_id, *METRIC_COLUMN_ALIASES.get(metric_id, [])])


def breakdown_column_candidates(breakdown: str) -> list[str]:
    key = normalize_breakdown_key(breakdown)
    return _dedupe([
        breakdown,
        key,
        *BREAKDOWN_COLUMN_ALIASES.get(key, []),
    ])
