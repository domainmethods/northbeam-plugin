# Northbeam Spend Intelligence

A Claude Code plugin for marketing analytics via the Northbeam API. Covers both spend data (impressions, clicks, CPC/CPM) and outcome metrics (revenue, ROAS, CAC, conversions) through the Spend and Data Export APIs.

## Prerequisites

- [Claude Code](https://claude.ai/code) (CLI, Desktop, VS Code, or JetBrains)
- Python 3.11+
- Northbeam account with API access (API Key + Client ID from **Settings > API Keys**)

## Installation

### 1. Add the marketplace

```bash
/plugin marketplace add domainmethods/northbeam-plugin
```

### 2. Install the plugin

```bash
/plugin install northbeam@domainmethods/northbeam-plugin
```

On first enable, Claude Code prompts for your credentials:

- **NORTHBEAM_API_KEY** — your Northbeam API key
- **NORTHBEAM_CLIENT_ID** — your Northbeam Client ID
- **NORTHBEAM_API_ENV** — `prod` (default) or `uat`

Sensitive values are stored in your system keychain. No manual `settings.json` edits needed.

### Verify Connection

```
/northbeam:setup
```

This validates your credentials and optionally sets up your business context profile (monthly budgets, KPI targets, campaign naming conventions).

## Skills

### `/northbeam:setup`
Configure credentials and business context profile.

### `/northbeam:analyze`
Strategic marketing analysis. Capabilities include:
- Ad hoc spend queries with derived metrics (CPC, CPM, CTR)
- Outcome metrics via Data Export (revenue, ROAS, CAC, conversions)
- Attribution model comparison (side-by-side across models)
- Automatic period-over-period comparisons (WoW, MoM, YoY)
- Anomaly detection (spend gaps, efficiency spikes, revenue drops)
- Campaign naming intelligence (parse dimensions from naming conventions)
- Diminishing returns detection (audience fatigue alerts)
- Budget allocation modeling (efficiency + ROAS signals)
- Portfolio health dashboard (spend + outcomes morning briefing)
- Year-over-year seasonality context
- Industry benchmarking (DTC/ecommerce ranges)

## MCP Tools

These tools are available to Claude when the plugin is active:

| Tool | Description |
|------|-------------|
| `northbeam_list_spend` | Query spend records with filters (date, platform, campaign, ad) and pagination |
| `northbeam_data_export` | Run async data exports for outcome metrics (revenue, ROAS, CAC, conversions) |
| `northbeam_list_options` | Discover available breakdowns, metrics, and attribution models |
| `northbeam_check_connection` | Validate credentials and report visible platforms |

## Troubleshooting

### Credentials not working
- Run `/northbeam:setup` to re-check your API key and client ID
- Verify your credentials in the Northbeam dashboard under **Settings > API Keys**

### MCP server won't start
- Confirm Python 3.11+ is in your PATH: `python --version`
- Install dependencies: `pip install httpx mcp`
- Check for errors: run `claude --debug` and look for MCP initialization failures

### Plugin not loading
- Run `/plugins` to check plugin status and error messages
- Try `/reload-plugins` to force a refresh

## License

MIT
