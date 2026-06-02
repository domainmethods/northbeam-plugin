# Northbeam for Claude Code & Codex

**Ask plain-English questions about your ad spend and get instant analysis — no spreadsheets, no SQL.**

This plugin connects Claude Code (or Codex) to your Northbeam account so you can ask things like *"How are we doing this month?"* or *"Which channels should I cut?"* and get a clear, data-backed answer drawn straight from your real numbers. It pulls spend, efficiency (clicks, CPC, CPM, CTR), and outcomes (revenue, ROAS, CAC, orders) and turns them into the kind of summary you'd otherwise spend an hour building by hand.

---

## Where this works

Use this plugin in **Claude Code** (the terminal or desktop coding tool) or **Codex**. Those launch the small local connector that actually fetches your Northbeam data.

It does **not** work in the **Claude.ai chat app**, **Claude Cowork**, or **Claude Code on the web**. Those load the plugin's commands but run in a cloud sandbox that can't start the local connector — so the `/northbeam:*` commands show up, but the assistant can't pull your numbers. That's a current Anthropic platform limitation (those environments only support cloud-hosted connectors), not a setup mistake. Stick to Claude Code in your terminal, or Codex.

---

## What you can ask

Once it's set up, just type questions in plain language:

- "How are we doing this month?"
- "Which channels are over budget pace?"
- "Compare ROAS by channel for last week vs. the week before."
- "Find any spend anomalies in the last 14 days."
- "Where should we double down, and where should we cut?"
- "Show me Facebook CPC and CPM for the last 14 days."

You can also run the built-in commands directly:

```text
/northbeam:setup                                  ← connect your account (run once)
/northbeam:analyze Check portfolio health this month.
```

---

## What you'll need

1. **A Northbeam account** with API access. Grab two values from the Northbeam dashboard under **Settings → API Keys**:
   - your **API Key**
   - your **Client ID**
2. **`uv`** — a small, free tool that lets the plugin run its data connector. One-time install:

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

   (On Windows, or for other options, see the [uv install guide](https://docs.astral.sh/uv/).) You don't need to know how it works — the plugin uses it behind the scenes.

That's it. No coding required.

---

## Get started (Claude Code)

Claude Code is the easiest way in — it asks for your keys with a simple prompt and stores them securely for you. No files to edit.

1. **Add the plugin marketplace:**

   ```text
   /plugin marketplace add domainmethods/northbeam-plugin
   ```

2. **Install the plugin:**

   ```text
   /plugin install northbeam@domainmethods/northbeam-plugin
   ```

3. **Paste your credentials** when Claude Code prompts you:
   - `NORTHBEAM_API_KEY` — your API Key
   - `NORTHBEAM_CLIENT_ID` — your Client ID
   - `NORTHBEAM_API_ENV` — leave as `prod` (only change to `uat` if Northbeam told you to)

4. **Reload** if you installed mid-session:

   ```text
   /reload-plugins
   ```

5. **Connect your account** (run once):

   ```text
   /northbeam:setup
   ```

6. **Ask your first question:**

   ```text
   /northbeam:analyze How are we doing this month?
   ```

Your keys are kept in Claude Code's secure credential store — you won't need any `.env` file or config file for this path.

> **Using Codex instead?** That path needs a couple of extra setup steps — see [Advanced / Developer Setup](#advanced--developer-setup) below.

---

## What it can do

After setup, the assistant can:

- **Answer spend questions** — totals, trends, and efficiency (CPC, CPM, CTR) by channel, campaign, or date range.
- **Track outcomes** — revenue, ROAS, CAC, and orders, including attribution-model comparisons.
- **Compare over time** — week-over-week, month-over-month, year-over-year, with the swings called out.
- **Catch problems early** — stopped campaigns, efficiency spikes, revenue drops, and zero-spend days.
- **Watch your budget** — pacing vs. your monthly targets (over-pacing / on-track / behind).
- **Spot diminishing returns** — campaigns getting more expensive, plus efficient ones worth scaling.
- **Give you a morning briefing** — a one-screen portfolio health snapshot.

---

## Save your goals (optional, recommended)

When you run `/northbeam:setup`, you can also save a quick business profile — your monthly budgets, KPI targets, ROAS goal, and campaign naming convention. The assistant uses these to frame answers as *"vs. your $X target"* instead of just raw numbers, and it unlocks budget-pacing checks. It's stored locally at `~/.northbeam/profile.json`. You can skip it and add it later.

---

## Troubleshooting

**"Authentication failed" / "Run /northbeam:setup"**
Your keys are missing or wrong. Run `/northbeam:setup` to re-check, and confirm the values in the Northbeam dashboard under **Settings → API Keys**. In Claude Code you can also open `/plugin`, reconfigure Northbeam, then run `/reload-plugins`. Start a fresh chat after changing credentials.

**Zero uploaded spend, but you know you're spending**
That's normal. If your ad accounts are connected to Northbeam directly, your real spend comes through the Data Export (which the plugin uses automatically) — the separate "uploaded spend" number is only for manually-uploaded, non-integrated channels and is expected to be `0`.

**The `/northbeam:*` commands appear, but the assistant can't pull any data**
You're probably in the Claude.ai chat app, Claude Cowork, or Claude Code on the web. Those load the plugin's commands but don't run its local data connector (see [Where this works](#where-this-works)). Switch to Claude Code in your terminal — or Codex — where the connector runs.

**The plugin won't start / `uv` errors**
Make sure `uv` installed correctly:

```bash
uv --version
```

If that prints a version and things still fail, see the developer notes below.

---

## Advanced / Developer Setup

Everything below is for Codex users and for people developing or maintaining the plugin. Non-developers on Claude Code can stop reading at this line.

### Using it in Codex

Codex installs the plugin from the same GitHub repo, but it doesn't have Claude Code's credential prompt, so you save your keys with one extra command.

#### 1. Install the plugin

```bash
codex plugin marketplace add domainmethods/northbeam-plugin
codex plugin add northbeam@northbeam-plugin
```

Confirm it loaded, then start a new Codex thread so it picks up the plugin's skills and MCP tools:

```bash
codex plugin list
```

#### 2. Save your credentials

Run the credential helper. It needs no local checkout — `uvx` fetches and runs it straight from the repo:

```bash
uvx --from "git+https://github.com/domainmethods/northbeam-plugin#subdirectory=northbeam" northbeam-setup
```

It prompts for your API Key and Client ID (input is hidden) and writes `~/.codex/northbeam.env` with user-only permissions — your keys never pass through the Codex chat. If you already have a project `.env`, import it instead:

```bash
uvx --from "git+https://github.com/domainmethods/northbeam-plugin#subdirectory=northbeam" northbeam-setup --from-dotenv .env
```

Restart Codex or open a new thread afterward, then run `/northbeam:setup` to confirm the connection.

Advanced alternative — export the variables before launching Codex:

```bash
export NORTHBEAM_API_KEY="..."
export NORTHBEAM_CLIENT_ID="..."
export NORTHBEAM_API_ENV="prod"   # optional; "uat" for UAT
codex
```

Real environment variables take precedence over credential files. Project `.env` files work for local development but aren't the recommended Codex path, since plugin MCP servers run from Codex's installed plugin copy rather than your project directory. Never commit real credentials — `.env` is gitignored; `.env.example` is the safe template.

#### 3. Advanced environment variables

Optional and rarely needed; sensible defaults apply.

- `NORTHBEAM_CREDENTIALS_FILE` — absolute path to a credentials file loaded before the built-in search locations (`.env`, `$PWD/.env`, `~/.codex/northbeam.env`, `~/.northbeam/env`). Use it to keep credentials outside the project tree.
- `NORTHBEAM_EXPORT_TIMEOUT` — seconds to wait for a Data Export job before giving up (default `180`). Raise it for very large date ranges or high-cardinality breakdowns.

### How it works

The plugin has two parts: **skills** (`/northbeam:setup`, `/northbeam:analyze`) that tell the assistant how to help, and a local **MCP server** (a small Python process launched with `uv`) that securely calls the Northbeam API when a skill needs real data.

Spend and efficiency come from Northbeam's **Data Export API** (the source of truth for integrated accounts), not the legacy Spend API (`GET /v1/spend`), which is upload-only and used only for customer-uploaded, non-integrated spend.

Tools exposed when the plugin is active:

| Tool | Description |
|------|-------------|
| `northbeam_spend` | Platform-level ad spend + efficiency (CPC, CPM, CTR) from the Data Export API — the source of truth for spend on integrated accounts |
| `northbeam_list_uploaded_spend` | Query customer-uploaded spend (non-integrated channels) from the Spend API; empty for integrated accounts |
| `northbeam_data_export` | Run async data exports for outcomes such as revAttributed, ROAS, CAC, and conversions |
| `northbeam_list_options` | Discover available breakdowns, metrics, and attribution models |
| `northbeam_portfolio_health` | Build a spend-efficiency + outcome snapshot from one combined Data Export |
| `northbeam_check_connection` | Validate Uploaded Spend API access, Data Export metadata, and a small revAttributed probe |

### Repo layout

The installable plugin lives in the [`northbeam/`](northbeam/) subdirectory (this is what both marketplaces point at, and what `uvx --from "git+...#subdirectory=northbeam"` builds). The repo root holds the two marketplace manifests (`.claude-plugin/marketplace.json` for Claude Code, `.agents/plugins/marketplace.json` for Codex), this README, and `docs/`. Run all development commands from inside `northbeam/`.

### Testing local changes in Codex

Point Codex at your working tree instead of GitHub — it reads the root `.agents/plugins/marketplace.json`, which resolves the plugin from `./northbeam`:

```bash
codex plugin marketplace add /absolute/path/to/northbeam-plugin
codex plugin add northbeam@northbeam-plugin
codex plugin list
```

Re-run `codex plugin add` after edits, and start a new Codex thread to reload. Codex copies the plugin into its cache, so never keep real `.env` credentials inside `northbeam/`.

### Development checks

Run the validator and tests before publishing or reinstalling a changed plugin:

```bash
python3 ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py northbeam
cd northbeam && uv run --extra dev pytest
```

### Developer troubleshooting

- **MCP server won't start** — confirm the server imports cleanly:

  ```bash
  cd northbeam && uv run python -c "import server.northbeam_mcp; print('ok')"
  ```

- **Windows: `FileNotFoundError: [WinError 3]` on `jsonschema_specifications\schemas\...`** — this is Windows' 260-character path limit (`MAX_PATH`). The Claude Code desktop app installs plugins under a very deep session directory, and the Python environment's JSON-schema tree tips the total path over the limit. The plugin avoids this automatically by relocating its virtualenv to a short path via `UV_PROJECT_ENVIRONMENT` (`%LOCALAPPDATA%\northbeam-mcp-venv`) in `.mcp.claude.json` — no admin rights or registry change needed. **Codex on Windows** uses a separate config that does not support variable expansion, so if you ever hit the same error there, set a short venv path in your shell before launching Codex:

  ```powershell
  $env:UV_PROJECT_ENVIRONMENT = "$env:LOCALAPPDATA\northbeam-mcp-venv"
  codex
  ```

  (Codex reads `UV_PROJECT_ENVIRONMENT` from the parent environment. Use `setx UV_PROJECT_ENVIRONMENT "%LOCALAPPDATA%\northbeam-mcp-venv"` to make it persistent.)

- **Codex plugin not loading** — check `codex plugin list`, reinstall with `codex plugin add northbeam@northbeam-plugin` after editing `northbeam/.codex-plugin/plugin.json`, and start a new Codex thread.

---

## License

MIT
