# Northbeam Spend Intelligence

A Claude Code plugin for strategic marketing spend analysis via the Northbeam API. Designed for business users and data analysts.

## What It Does

Ask questions about your ad spend in natural language:

- "How are we doing?" — get a portfolio health briefing
- "Where should we cut spend?" — efficiency analysis with recommendations
- "Compare Facebook vs TikTok for last month" — cross-platform comparison
- "Any red flags?" — anomaly detection across campaigns
- "I have $10K more to spend, where should it go?" — budget allocation modeling

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
Strategic spend analysis. Capabilities include:
- Ad hoc spend queries with derived metrics (CPC, CPM, CTR)
- Automatic period-over-period comparisons (WoW, MoM, YoY)
- Anomaly detection (spend gaps, efficiency spikes, budget pacing)
- Campaign naming intelligence (parse dimensions from naming conventions)
- Diminishing returns detection (audience fatigue alerts)
- Budget allocation modeling (marginal efficiency recommendations)
- Portfolio health dashboard (morning briefing command)
- Year-over-year seasonality context
- Industry benchmarking (DTC/ecommerce ranges)
- Adaptive output formatting (conversational, tables, CSV, executive summary)

## Requirements

- Python 3.11+
- Northbeam account with API access
- Claude Code (Desktop, CLI, VS Code, or JetBrains)
