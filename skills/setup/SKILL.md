---
name: setup
description: Check Northbeam API credentials, configure connection, and set up business context profile. Use when first connecting to Northbeam or troubleshooting authentication issues.
allowed-tools: mcp__northbeam__northbeam_check_connection, mcp__northbeam__northbeam_list_spend, Read, Write
user-invocable: true
disable-model-invocation: false
---

# Northbeam Setup

You are helping the user configure their Northbeam API connection and business context profile.

## Step 1: Check Connection

Call the `northbeam_check_connection` tool.

**If connected:** Report the environment and visible platforms. Skip to Step 3 (business context profile).

**If not connected (authentication failed or env vars missing):** Guide the user through configuration:

1. Tell them to get their credentials:
   - Log in to the Northbeam dashboard
   - Navigate to **Settings → API Keys**
   - Copy the **API Key** and **Client ID**

2. Tell them to configure the plugin credentials in the environment used to launch Codex, or in a `.env` file in the project directory where they start Codex:
   - `NORTHBEAM_API_KEY`
   - `NORTHBEAM_CLIENT_ID`
   - `NORTHBEAM_API_ENV=prod` or `NORTHBEAM_API_ENV=uat` (optional; defaults to `prod`)

3. After they confirm the credentials are saved, tell them to restart Codex or start a new Codex thread from that project directory for the changes to take effect, then re-run `/northbeam:setup`.

## Step 2: Validate Connection

Once credentials are set, call `northbeam_check_connection` again. Confirm:
- The environment shown (prod or uat)
- The platforms visible
- The record count

If the check succeeds, congratulate them and proceed to Step 3.

## Step 3: Business Context Profile (Optional)

Ask the user if they'd like to set up a business context profile. Explain that it enables:
- Budget pacing alerts ("you're 30% over your Facebook budget this month")
- Target-relative analysis ("CPC is 20% above your $2.00 target")
- Campaign naming intelligence ("group by funnel stage from your naming convention")

If they want to proceed, ask them one question at a time:

1. **Monthly budgets by platform:** "What's your monthly budget for each platform? (e.g., Facebook: $25,000, TikTok: $10,000)"
2. **Target metrics:** "What are your target CPC, CPM, and CTR for each platform?" (offer to skip if they don't have targets yet)
3. **ROAS goal:** "What's your target ROAS?" (offer to skip)
4. **Campaign naming convention:** "Do your campaign names follow a pattern? For example: FB_Prospecting_LAL1_US_Q2_Video. If so, describe the segments." (offer to skip)
5. **Currency:** "What currency are your budgets in?" (default USD)

After collecting answers, write the profile to `~/.codex/northbeam-profile.json` using the Write tool. Confirm the file was saved.

If they decline the profile, that's fine — the analyze skill works without it (just without budget pacing and target comparisons).
