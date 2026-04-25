# Northbeam Spend API Reference

## Endpoint

`GET /v1/spend` — List spend records

**Prod:** `https://api.northbeam.io/v1/spend`
**UAT:** `https://api-uat.northbeam.io/v1/spend`

## Authentication

| Header | Value |
|--------|-------|
| `Authorization` | API key from Northbeam dashboard |
| `Data-Client-ID` | Client ID from Northbeam dashboard |
| `Content-Type` | `application/json` |

## Query Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `date` | YYYY-MM-DD | Single date. Use alone OR use date_start/date_end. |
| `date_start` | YYYY-MM-DD | Range start. Pair with date_end. |
| `date_end` | YYYY-MM-DD | Range end. Pair with date_start. |
| `platform_account_id` | string | Filter by ad platform account ID |
| `campaign_id` | string | Filter by campaign ID |
| `adset_id` | string | Filter by ad set ID |
| `ad_id` | string | Filter by ad ID |
| `page` | integer (≥1) | Page number, default 1 |
| `page_size` | integer (max 1000) | Results per page, default 1000 |

## Response Fields

Each spend record contains:

| Field | Type | Description |
|-------|------|-------------|
| `date` | YYYY-MM-DD | Date of the spend entry |
| `platform_name` | string | Ad platform name (e.g., "Facebook", "TikTok") |
| `platform_account_id` | string | Ad platform account identifier |
| `campaign_id` | string | Campaign identifier (maps to utm_campaign) |
| `campaign_name` | string | Campaign display name |
| `adset_id` | string | Ad set identifier (maps to utm_term) |
| `adset_name` | string | Ad set display name |
| `ad_id` | string | Ad identifier (maps to utm_content) |
| `ad_name` | string | Ad display name |
| `spend` | number (≥0) | Amount spent in spend_currency |
| `spend_currency` | string (3 chars) | ISO 4217 currency code |
| `impressions` | number (≥0) | Impression count for the day |
| `clicks` | number (≥0) | Click count for the day |
| `created_at` | datetime | Record creation timestamp |
| `updated_at` | datetime | Record last update timestamp |

## Pagination Response Metadata

| Field | Type | Description |
|-------|------|-------------|
| `data` | array | Array of spend records |
| `page` | integer | Current page number |
| `page_size` | integer | Results per page |
| `total_pages` | integer | Total number of pages |
| `total_count` | integer | Total number of matching records |

## Derived Metrics (Computed by the Skill)

These are not returned by the API but are computed from the raw fields:

| Metric | Formula | Description |
|--------|---------|-------------|
| CPC | spend / clicks | Cost per click |
| CPM | (spend / impressions) × 1000 | Cost per thousand impressions |
| CTR | (clicks / impressions) × 100 | Click-through rate (%) |

Handle division by zero: if clicks=0, CPC is undefined. If impressions=0, CPM and CTR are undefined. Present as "N/A" rather than infinity.

## Rate Limits

- Max 1000 results per page
- Retry on 429 responses; respect the Retry-After header
- Retry on 5xx responses with exponential backoff (max 3 attempts)

## Notes

- Spend values represent the full daily amount. Northbeam distributes daily spend linearly across 24 hours internally.
- The API uses upsert semantics keyed on (date, platform_account_id, campaign_id, adset_id, ad_id). Querying the same filters returns the latest state.
