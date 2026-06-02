---
name: setup
description: Check Northbeam API credentials, configure connection, and set up business context profile. Use when first connecting to Northbeam or troubleshooting authentication issues.
allowed-tools: mcp__northbeam__northbeam_check_connection, Read, Write
user-invocable: true
disable-model-invocation: false
---

# Northbeam Setup

You are helping the user configure their Northbeam API connection and business context profile.

## Step 1: Check Connection

Call the `northbeam_check_connection` tool.

**If connected:** Report the environment and summarize each checked surface:

- Uploaded Spend API status and uploaded-spend row count for yesterday
- Data Export metadata status
- Data Export API status with transactions and revenue (revAttributed) for yesterday

A zero uploaded-spend count is normal for natively-integrated accounts — real ad
spend comes from the Data Export API (the `northbeam_spend` tool), not the
uploaded-spend endpoint. Skip to Step 3 (business context profile).

**If partially connected:** Explain which surface failed. If Spend API works
but Data Export fails, do not tell the user to re-enter credentials unless the
error is authentication-related; this usually means the plugin or API contract
needs attention.

**If not connected (authentication failed or env vars missing):** Guide the user through configuration:

1. Tell them to get their credentials:
   - Log in to the Northbeam dashboard
   - Navigate to **Settings → API Keys**
   - Copy the **API Key** and **Client ID**

2. Give client-specific setup instructions:

   **Claude Code:** Tell them to open `/plugin`, reconfigure Northbeam, and paste the API Key and Client ID into the plugin prompts. `NORTHBEAM_API_ENV` should be `prod` unless they use UAT. Then tell them to run `/reload-plugins`.

   **Codex:** Tell them to run the credential helper. It needs no local checkout — `uvx` fetches and runs it straight from the repo:

   ```bash
   uvx --from "git+https://github.com/domainmethods/northbeam-plugin#subdirectory=northbeam" northbeam-setup
   ```

   It prompts for the API Key and Client ID (input is hidden) and writes `~/.codex/northbeam.env` with user-only file permissions. If they already have a `.env` file, tell them to import it instead:

   ```bash
   uvx --from "git+https://github.com/domainmethods/northbeam-plugin#subdirectory=northbeam" northbeam-setup --from-dotenv .env
   ```

   Avoid asking the user to paste API secrets into chat — the helper reads them locally so the keys never pass through the Codex conversation.

3. After they confirm the credentials are saved, tell them to restart the client or open a new thread for the changes to take effect, then re-run `/northbeam:setup`.

## Step 2: Validate Connection

Once credentials are set, call `northbeam_check_connection` again. Confirm:
- The environment shown (prod or uat)
- Uploaded Spend API status and uploaded-spend row count
- Data Export metadata status
- Data Export API transactions and revenue (revAttributed) status

A zero uploaded-spend count is normal for natively-integrated accounts — real ad
spend comes from the Data Export API (`northbeam_spend`).

If the check is partially connected, explain which surface failed. If Spend API
works but Data Export fails, do not tell the user to re-enter credentials unless
the error is authentication-related; this usually means the plugin or API
contract needs attention.

If the check succeeds, congratulate them and proceed to Step 3.

## Step 3: Business Context Profile (Optional)

Ask the user if they'd like to set up a business context profile. Explain that it enables:
- Budget pacing alerts ("you're 30% over your Facebook budget this month")
- Target-relative analysis ("CPC is 20% above your $2.00 target")
- Campaign naming intelligence ("group by funnel stage from your naming convention")

The profile schema is documented in `${CLAUDE_PLUGIN_ROOT}/config/profile-template.json`
(fields: `monthly_budgets`, `targets`, `roas_goal`, `campaign_naming_pattern`,
`fiscal_month_start`, `currency`). Read it first if you want the exact structure
the analyze skill expects, then collect values for those fields.

If they want to proceed, ask them one question at a time:

1. **Monthly budgets by platform:** "What's your monthly budget for each platform? (e.g., Facebook: $25,000, TikTok: $10,000)"
2. **Target metrics:** "What are your target CPC, CPM, and CTR for each platform?" (offer to skip if they don't have targets yet)
3. **ROAS goal:** "What's your target ROAS?" (offer to skip)
4. **Campaign naming convention:** "Do your campaign names follow a pattern? For example: FB_Prospecting_LAL1_US_Q2_Video. If so, describe the segments." (offer to skip)
5. **Currency:** "What currency are your budgets in?" (default USD)

After collecting answers, write the profile to `~/.northbeam/profile.json` using the
Write tool, following the structure in
`${CLAUDE_PLUGIN_ROOT}/config/profile-template.json`. Confirm the file was saved.

If they decline the profile, that's fine - the analyze skill works without it (just without budget pacing and target comparisons).
