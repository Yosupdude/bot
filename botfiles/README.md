# Discord Agent Referral Tracker

Automatically tracks which agent invited a member, and whether that member bought a **paid premium membership** from the Discord Shop — fully excluding free trials.

---

## How it works

1. Each **Agent** has a unique Discord invite link registered to their name.
2. When a new member joins, the bot detects which invite was used and links them to that agent.
3. When the member receives the **premium role** (granted by Discord Shop on paid purchase), the bot records a conversion.
4. A **tally embed** in a designated channel updates automatically — showing each agent's invites, paid conversions, and conversion rate.

---

## Setup Guide

### 1 — Create the Discord Bot

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. Click **New Application** → give it a name
3. Go to **Bot** → click **Reset Token** → copy the token
4. Under **Privileged Gateway Intents**, enable:
   - ✅ **Server Members Intent**
   - ✅ **Guild Invites** (under Privileged Gateway if shown)
5. Go to **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot Permissions: `Manage Guild`, `View Channels`, `Send Messages`, `Embed Links`, `Read Message History`, `Manage Roles` *(only needed to read role changes)*
6. Open the generated URL and invite the bot to your server.

### 2 — Configure the project

```bash
# Clone / copy the files, then:
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your bot token and tally channel ID
```

### 3 — Enable Developer Mode in Discord

Go to **User Settings → Advanced → Developer Mode** ✅  
This lets you right-click any channel/message/role to **Copy ID**.

### 4 — Set the tally channel

Right-click the channel you want the tally posted in → **Copy ID** → paste into `.env` as `TALLY_CHANNEL_ID`.

### 5 — Run the bot

```bash
python bot.py
```

The bot will start, cache all current invite counts, and sync slash commands (may take up to 1 hour to appear globally, instant for guild commands).

---

## First-time Discord setup (in your server)

### Step A — Set the premium role
The bot needs to know which role Discord Shop grants when someone buys a **paid** membership.

1. Go to **Server Settings → Roles** and find or create the role Discord Shop assigns (check under Server Subscriptions / Monetization settings).
2. Run `/set_premium_role` and select that role.

> **Excluding free trials:** Discord Shop grants different roles for trials vs paid memberships, or you can create a separate "Subscriber" role that's only assigned after the trial converts. Set `/set_premium_role` to the **paid-only** role.

### Step B — Create agent invite links

For each agent:
1. In Discord, go to **Server Settings → Invites → Create Invite**
2. Set expiry to **Never** and max uses to **No limit**
3. Copy just the code at the end (e.g. `aBcDeFg` from `discord.gg/aBcDeFg`)

### Step C — Register agents

```
/add_agent name:Sarah invite_code:aBcDeFg
/add_agent name:Marcus invite_code:xYz1234
```

The tally embed will appear/update in your tally channel immediately.

---

## Slash Commands

| Command | Permission | Description |
|---|---|---|
| `/add_agent name invite_code` | Admin | Register a new agent with their invite code |
| `/list_agents` | Admin | Show all agents and their stats |
| `/remove_agent name` | Admin | Remove an agent |
| `/set_premium_role role` | Admin | Set which role = paid premium member |
| `/refresh_tally` | Admin | Force-refresh the tally embed |
| `/link_member member agent_name` | Admin | Manually link a member to an agent (corrections/backfill) |

---

## Database

Data is stored in `tracker.db` (SQLite, auto-created). Tables:

- **agents** — registered agents and their invite codes
- **member_joins** — every member who joined and which agent (if any) referred them
- **premium_purchases** — members who received the premium role and when
- **guild_settings** — per-server config (premium role ID, tally message ID)

---

## Notes

- The bot caches invite use-counts on startup and on every join, so it can diff which invite was used. If the bot was **offline when a member joined**, that join won't be linked to an agent.
- Use `/link_member` to manually correct any missed joins.
- The tally embed is edited in-place (same message) to avoid channel spam.
- Free trial members who never convert won't appear in the "premium conversions" count — only those who receive the designated paid role are counted.
