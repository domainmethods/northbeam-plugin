# Northbeam Plugin for Codex and Claude Code

Marketing analytics through the Northbeam API. The plugin covers spend data
(impressions, clicks, CPC, CPM, CTR) and outcome metrics (revenue, ROAS, CAC,
conversions) through Northbeam's Spend and Data Export APIs.

## Prerequisites

- Codex with plugin support, or Claude Code with plugin support
- [uv](https://docs.astral.sh/uv/) for Python dependency management
- Northbeam API access: API Key and Client ID from **Settings > API Keys**

## What The Plugin Installs

The plugin has two parts that work together:

- Skills: `/northbeam:setup` and `/northbeam:analyze` tell the assistant how to
  help with Northbeam.
- MCP server: the local Python process that securely calls the Northbeam API
  when a skill needs real data.

Non-technical users should install the plugin, save credentials once, then use
plain-language requests like "Check portfolio health this month."

## Claude Code Installation

Claude Code has the cleanest credential flow because its plugin system prompts
for plugin options when the plugin is enabled.

1. Add the marketplace:

   ```text
   /plugin marketplace add domainmethods/northbeam-plugin
   ```

2. Install the plugin:

   ```text
   /plugin install northbeam@domainmethods/northbeam-plugin
   ```

3. When Claude Code asks for plugin configuration, paste:
   - `NORTHBEAM_API_KEY`
   - `NORTHBEAM_CLIENT_ID`
   - `NORTHBEAM_API_ENV` (`prod` unless you use UAT)

4. Reload plugins if you installed from an already-open session:

   ```text
   /reload-plugins
   ```

5. Run setup:

   ```text
   /northbeam:setup
   ```

Claude Code stores sensitive plugin values in its credential store. You should
not need a `.env` file for the normal Claude Code path.

## Codex Installation

### Local Personal Marketplace

Use this path when developing or installing this checkout directly.

1. Sync a clean plugin copy into your personal plugin directory.

   ```bash
   mkdir -p ~/plugins ~/.agents/plugins
   rsync -a --delete \
     --include='.env.example' \
     --exclude='.git/' \
     --exclude='.env*' \
     --exclude='.venv/' \
     --exclude='.pytest_cache/' \
     --exclude='__pycache__/' \
     --exclude='*.pyc' \
     --exclude='.spec-workflow/' \
     --exclude='docs/' \
     ./ ~/plugins/northbeam/
   ```

   Run the same `rsync` command again after local edits and before reinstalling.
   Do not point the marketplace at a working tree that contains real `.env`
   credentials; Codex copies local plugin files into its plugin cache.

2. Add the plugin to `~/.agents/plugins/marketplace.json`.

   If that file already has plugins, add the `northbeam` object to the existing
   `plugins` array instead of replacing the file.

   ```json
   {
     "name": "personal",
     "interface": {
       "displayName": "Personal"
     },
     "plugins": [
       {
         "name": "northbeam",
         "source": {
           "source": "local",
           "path": "./plugins/northbeam"
         },
         "policy": {
           "installation": "AVAILABLE",
           "authentication": "ON_INSTALL"
         },
         "category": "Productivity"
       }
     ]
   }
   ```

   Codex discovers this personal marketplace automatically. You do not need to
   run `codex plugin marketplace add` for this default personal-marketplace
   location.

3. Install or reinstall the plugin:

   ```bash
   codex plugin add northbeam@personal
   ```

4. Confirm it is installed and enabled:

   ```bash
   codex plugin list
   ```

5. Start a new Codex thread so Codex loads the plugin skills and MCP tools.

### Credentials

The MCP server needs a Northbeam API Key and Client ID. Codex does not currently
use Claude Code's plugin `userConfig` credential prompt, so the normal Codex
path is a small local credential file in your Codex home directory.

Recommended setup:

```bash
uv run python -m server.setup_credentials
```

If you already have a project `.env` file, copy it into the Codex credential
file:

```bash
uv run python -m server.setup_credentials --from-dotenv .env
```

The helper writes `~/.codex/northbeam.env` with user-only file permissions.
Restart Codex or open a new thread after saving credentials.

Advanced alternative: export the variables before starting Codex:

```bash
export NORTHBEAM_API_KEY="..."
export NORTHBEAM_CLIENT_ID="..."
export NORTHBEAM_API_ENV="prod"  # optional; use "uat" for UAT
codex
```

Existing real environment variables take precedence over credential files.
Project `.env` files are supported for local development, but they are not the
recommended Codex Desktop path because plugin MCP servers run from Codex's
installed plugin copy, not necessarily from your project directory.

Do not commit real credentials. `.env` is ignored; `.env.example` is the safe
template to commit. `~/.codex/northbeam.env` lives outside the repository.

### Verify Connection

In Codex, run:

```text
/northbeam:setup
```

The setup skill validates your credentials and can create a business context
profile at `~/.northbeam/profile.json` for monthly budgets, KPI targets,
ROAS goals, and campaign naming conventions.

The setup check validates both the Spend API and Data Export API. A zero spend
row count means Northbeam returned no ad spend records for the checked day; it
does not mean orders or transactions are missing.

## Codex Usage

After installation, use the Northbeam skills directly:

```text
/northbeam:setup
/northbeam:analyze Check portfolio health for this month.
/northbeam:analyze Which channels are over budget pace?
/northbeam:analyze Compare ROAS by channel for last week vs the prior week.
```

You can also ask natural-language questions in a Codex thread after the plugin
is loaded:

```text
Find spend anomalies this week.
Show Facebook CPC and CPM for the last 14 days.
Which campaigns should I watch for diminishing returns?
Compare revenue and ROAS by channel for month to date.
```

## Skills

### `/northbeam:setup`

Checks the Northbeam API connection and optionally writes a business context
profile for budget pacing and target-vs-actual analysis.

### `/northbeam:analyze`

Strategic marketing analysis. Capabilities include:

- Ad hoc spend queries with derived metrics (CPC, CPM, CTR)
- Outcome metrics through Data Export (revenue, ROAS, CAC, conversions)
- Attribution model comparison across models
- Period-over-period comparisons (WoW, MoM, YoY)
- Anomaly detection for spend gaps, efficiency spikes, and revenue drops
- Campaign naming intelligence
- Diminishing returns detection
- Budget allocation modeling
- Portfolio health dashboards
- Year-over-year seasonality context
- DTC/ecommerce benchmark context

## MCP Tools

These tools are available when the plugin is active:

| Tool | Description |
|------|-------------|
| `northbeam_list_spend` | Query spend records with filters for date, platform, campaign, ad, and pagination |
| `northbeam_data_export` | Run async data exports for outcome metrics such as revenue, ROAS, CAC, and conversions |
| `northbeam_list_options` | Discover available breakdowns, metrics, and attribution models |
| `northbeam_portfolio_health` | Build a holistic spend-efficiency and outcome-metric snapshot in one concurrent call |
| `northbeam_check_connection` | Validate credentials and report visible platforms |

## Troubleshooting

### Credentials Not Working

- Run `/northbeam:setup` to re-check your API key and client ID.
- Verify the values in the Northbeam dashboard under **Settings > API Keys**.
- In Claude Code, open `/plugin`, reconfigure Northbeam, then run
  `/reload-plugins`.
- In Codex, run `uv run python -m server.setup_credentials` from this checkout,
  or copy an existing `.env` with
  `uv run python -m server.setup_credentials --from-dotenv .env`.
- Start a new thread after changing credentials.

### MCP Server Will Not Start

- Confirm `uv` is installed:

  ```bash
  uv --version
  ```

- Confirm the server imports from this checkout:

  ```bash
  uv run python -c "import server.northbeam_mcp; print('ok')"
  ```

### Codex Plugin Not Loading

- Check plugin status:

  ```bash
  codex plugin list
  ```

- Reinstall after changing `.codex-plugin/plugin.json`:

  ```bash
  codex plugin add northbeam@personal
  ```

- Start a new Codex thread after reinstalling.

## Development Checks

Run the plugin validator and tests before publishing or reinstalling a changed
plugin:

```bash
python3 ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
uv run pytest
```

## License

MIT
