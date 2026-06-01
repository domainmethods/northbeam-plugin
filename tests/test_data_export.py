import pytest

from server.data_export import (
    build_breakdown_value_lookup,
    build_data_export_payload,
    breakdown_column_candidates,
    extract_download_url,
    extract_export_id,
    metric_column_candidates,
)


def test_build_data_export_payload_without_breakdowns():
    payload = build_data_export_payload(
        date_start="2026-05-31",
        date_end="2026-05-31",
        metrics=["txns", "rev"],
        breakdowns=[],
        attribution_model="northbeam_custom__va",
        attribution_window="7",
    )

    assert payload["level"] == "platform"
    assert payload["time_granularity"] == "DAILY"
    assert payload["period_type"] == "FIXED"
    assert payload["period_options"] == {
        "period_starting_at": "2026-05-31T00:00:00Z",
        "period_ending_at": "2026-05-31T23:59:59Z",
    }
    assert payload["breakdowns"] == []
    assert payload["attribution_options"] == {
        "attribution_models": ["northbeam_custom__va"],
        "accounting_modes": ["accrual"],
        "attribution_windows": ["7"],
    }
    assert payload["metrics"] == [{"id": "txns"}, {"id": "rev"}]


def test_build_data_export_payload_maps_breakdown_alias_with_values():
    payload = build_data_export_payload(
        date_start="2026-05-31",
        date_end="2026-05-31",
        metrics=["rev"],
        breakdowns=["platform"],
        breakdown_values={
            "Platform (Northbeam)": ["Facebook Ads", "Google Ads"],
        },
    )

    assert payload["breakdowns"] == [
        {
            "key": "Platform (Northbeam)",
            "values": ["Facebook Ads", "Google Ads"],
        }
    ]


def test_build_data_export_payload_rejects_breakdown_without_values():
    with pytest.raises(ValueError, match="Breakdown values required"):
        build_data_export_payload(
            date_start="2026-05-31",
            date_end="2026-05-31",
            metrics=["rev"],
            breakdowns=["platform"],
        )


def test_build_breakdown_value_lookup_handles_current_metadata_shape():
    lookup = build_breakdown_value_lookup({
        "breakdowns": {
            "breakdowns": [
                {"key": "Platform (Northbeam)", "values": ["Facebook Ads"]},
                {"key": "Category (Northbeam)", "values": ["Email"]},
            ]
        }
    })

    assert lookup == {
        "Platform (Northbeam)": ["Facebook Ads"],
        "Category (Northbeam)": ["Email"],
    }


def test_build_breakdown_value_lookup_handles_direct_list_shape():
    lookup = build_breakdown_value_lookup({
        "breakdowns": [
            {"key": "Platform (Northbeam)", "values": ["Facebook Ads"]},
        ]
    })

    assert lookup == {"Platform (Northbeam)": ["Facebook Ads"]}


def test_extract_export_id_accepts_current_and_legacy_shapes():
    assert extract_export_id({"id": "new-id"}) == "new-id"
    assert extract_export_id({"export_id": "old-id"}) == "old-id"
    assert extract_export_id({}) is None


def test_extract_download_url_accepts_current_and_legacy_shapes():
    assert extract_download_url({"result": ["https://storage.example.com/new.csv"]}) == (
        "https://storage.example.com/new.csv"
    )
    assert extract_download_url({"result": "https://storage.example.com/one.csv"}) == (
        "https://storage.example.com/one.csv"
    )
    assert extract_download_url({"download_url": "https://storage.example.com/old.csv"}) == (
        "https://storage.example.com/old.csv"
    )
    assert extract_download_url({}) is None


def test_metric_and_breakdown_column_candidates_cover_live_csv_names():
    assert metric_column_candidates("txns") == ["txns", "transactions"]
    assert metric_column_candidates("rev") == ["rev"]
    assert breakdown_column_candidates("platform") == [
        "platform",
        "Platform (Northbeam)",
        "breakdown_platform_northbeam",
    ]


def test_build_data_export_payload_uses_ui_defaults():
    payload = build_data_export_payload(
        date_start="2026-05-31",
        date_end="2026-05-31",
        metrics=["revAttributed"],
        breakdowns=[],
    )

    assert payload["attribution_options"] == {
        "attribution_models": ["northbeam_custom"],
        "accounting_modes": ["accrual"],
        "attribution_windows": ["1"],
    }


def test_metric_column_candidates_maps_impressions_to_imprs():
    assert metric_column_candidates("impressions") == ["impressions", "imprs"]
