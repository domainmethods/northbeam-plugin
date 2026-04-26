# Northbeam Claude Code Plugin

A Claude Code plugin for marketing analytics via the Northbeam API. Covers both spend data (impressions, clicks, CPC/CPM) and outcome metrics (revenue, ROAS, CAC, conversions) through the Spend and Data Export APIs. Designed for business users and data analysts.

## What It Does

Ask questions about your marketing performance in natural language:

- "How are we doing?" — portfolio health briefing with spend + outcome metrics
- "Where should we cut spend?" — efficiency analysis with recommendations
- "Compare Facebook vs TikTok ROAS for last month" — cross-platform outcome comparison
- "Any red flags?" — anomaly detection across campaigns and revenue
- "I have $10K more to spend, where should it go?" — budget allocation with ROAS signals
- "Which attribution model should I use?" — side-by-side model comparison

## Installation

### 1. Install the Plugin

```bash
/plugin install northbeam@your-marketplace
```

### 2. Configure Credentials

Get your API Key and Client ID from the Northbeam dashboard:
**Settings → API Keys**

Add them to your Claude settings file (`~/.claude/settings.json`):

```json
{
  "env": {
    "NORTHBEAM_API_KEY": "your-api-key-here",
    "NORTHBEAM_CLIENT_ID": "your-client-id-here"
  }
}
```

### 3. Verify Connection

Run `/northbeam:setup` to verify your credentials and optionally set up your business context profile (budgets, targets, campaign naming conventions).

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
- Adaptive output formatting (conversational, tables, CSV, executive summary)

## Requirements

- Python 3.11+
- Northbeam account with API access
- Claude Code (Desktop, CLI, VS Code, or JetBrains)
