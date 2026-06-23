import discord
from discord.ext import commands
from discord import app_commands
import json
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
import db

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
TALLY_CHANNEL_ID = int(os.getenv("TALLY_CHANNEL_ID", "0"))

intents = discord.Intents.default()
intents.members = True
intents.invites = True
intents.guild_scheduled_events = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Cache of invite uses before a new member joins
invite_cache: dict[int, dict[str, int]] = {}


async def fetch_invite_cache(guild: discord.Guild):
    """Snapshot current invite use counts for a guild."""
    invites = await guild.invites()
    return {inv.code: inv.uses for inv in invites}


# ─── Events ────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")
    for guild in bot.guilds:
        invite_cache[guild.id] = await fetch_invite_cache(guild)
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")


@bot.event
async def on_invite_create(invite: discord.Invite):
    """Keep cache fresh when new invites are created."""
    if invite.guild:
        invite_cache.setdefault(invite.guild.id, {})[invite.code] = invite.uses or 0


@bot.event
async def on_invite_delete(invite: discord.Invite):
    """Remove deleted invites from cache."""
    if invite.guild:
        invite_cache.get(invite.guild.id, {}).pop(invite.code, None)


@bot.event
async def on_member_join(member: discord.Member):
    """Detect which invite was used and link it to a registered agent."""
    guild = member.guild
    old_uses = invite_cache.get(guild.id, {})
    new_uses = await fetch_invite_cache(guild)
    invite_cache[guild.id] = new_uses

    used_code = None
    for code, uses in new_uses.items():
        if uses > old_uses.get(code, 0):
            used_code = code
            break

    agent_id = None
    if used_code:
        agent_id = db.get_agent_by_invite(used_code)

    db.record_member_join(
        member_id=member.id,
        member_name=str(member),
        agent_id=agent_id,
        invite_code=used_code,
        joined_at=member.joined_at or datetime.now(timezone.utc),
    )

    if agent_id:
        agent = db.get_agent(agent_id)
        print(
            f"[JOIN] {member} joined via agent '{agent['name']}' (invite: {used_code})"
        )
    else:
        print(f"[JOIN] {member} joined (no tracked agent found, code={used_code})")


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    """Detect premium subscription role changes (Discord Shop memberships)."""
    guild = after.guild
    premium_role_id = db.get_premium_role(guild.id)
    if not premium_role_id:
        return

    before_role_ids = {r.id for r in before.roles}
    after_role_ids = {r.id for r in after.roles}

    gained = after_role_ids - before_role_ids
    lost = before_role_ids - after_role_ids

    if premium_role_id in gained:
        # Member just received the premium role
        record = db.get_member_record(after.id)
        if record and record.get("agent_id"):
            db.record_premium_purchase(
                member_id=after.id,
                purchased_at=datetime.now(timezone.utc),
            )
            print(f"[PREMIUM] {after} gained premium (agent: {record['agent_id']})")
            await update_tally_channel(guild)

    if premium_role_id in lost:
        # Member lost the premium role — mark as lapsed
        db.mark_premium_lapsed(after.id)
        print(f"[LAPSED] {after} lost premium role")
        await update_tally_channel(guild)


# ─── Tally Channel ─────────────────────────────────────────────────────────────

async def update_tally_channel(guild: discord.Guild):
    """Rebuild the tally embed and post/edit it in the designated channel."""
    if not TALLY_CHANNEL_ID:
        return
    channel = guild.get_channel(TALLY_CHANNEL_ID)
    if not channel:
        return

    agents = db.list_agents()
    embed = discord.Embed(
        title="📊 Agent Referral Tally",
        color=0x5865F2,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text="Auto-updated • excludes free trials")

    if not agents:
        embed.description = "No agents registered yet. Use `/add_agent` to get started."
    else:
        for agent in agents:
            stats = db.get_agent_stats(agent["agent_id"])
            invites = stats["total_invites"]
            conversions = stats["premium_conversions"]
            rate = f"{(conversions/invites*100):.0f}%" if invites else "—"
            
            lines = [
                f"**Invites:** {invites}",
                f"**Premium Conversions:** {conversions}",
                f"**Conversion Rate:** {rate}",
            ]
            # Show last 5 converting members
            if stats["recent_conversions"]:
                names = ", ".join(stats["recent_conversions"][-5:])
                lines.append(f"**Recent:** {names}")

            embed.add_field(
                name=f"🧑‍💼 {agent['name']}",
                value="\n".join(lines),
                inline=True,
            )

    # Try to edit existing tally message; otherwise send new one
    tally_msg_id = db.get_tally_message_id(guild.id)
    msg = None
    if tally_msg_id:
        try:
            msg = await channel.fetch_message(tally_msg_id)
            await msg.edit(embed=embed)
            return
        except discord.NotFound:
            pass

    msg = await channel.send(embed=embed)
    db.set_tally_message_id(guild.id, msg.id)


# ─── Slash Commands ─────────────────────────────────────────────────────────────

@bot.tree.command(name="add_agent", description="Register a new referral agent with their invite link.")
@app_commands.describe(
    name="Agent's display name",
    invite_code="The Discord invite code this agent uses (just the code, not the full URL)",
)
@app_commands.checks.has_permissions(administrator=True)
async def add_agent(interaction: discord.Interaction, name: str, invite_code: str):
    # Validate the invite exists in this guild
    try:
        invites = await interaction.guild.invites()
        codes = [inv.code for inv in invites]
        if invite_code not in codes:
            await interaction.response.send_message(
                f"⚠️ Invite code `{invite_code}` not found in this server's active invites. "
                "Please create the invite first, then register it.",
                ephemeral=True,
            )
            return
    except discord.Forbidden:
        pass  # Bot lacks permission to list invites; register anyway

    agent_id = db.add_agent(name=name, invite_code=invite_code, guild_id=interaction.guild_id)
    await interaction.response.send_message(
        f"✅ Agent **{name}** registered with invite code `{invite_code}` (ID: `{agent_id}`).",
        ephemeral=True,
    )
    await update_tally_channel(interaction.guild)


@bot.tree.command(name="list_agents", description="List all registered referral agents.")
@app_commands.checks.has_permissions(administrator=True)
async def list_agents(interaction: discord.Interaction):
    agents = db.list_agents(guild_id=interaction.guild_id)
    if not agents:
        await interaction.response.send_message("No agents registered yet.", ephemeral=True)
        return

    lines = []
    for a in agents:
        stats = db.get_agent_stats(a["agent_id"])
        lines.append(
            f"• **{a['name']}** — code: `{a['invite_code']}` | "
            f"invites: {stats['total_invites']} | premium: {stats['premium_conversions']}"
        )
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@bot.tree.command(name="remove_agent", description="Remove a referral agent.")
@app_commands.describe(name="Exact agent name to remove")
@app_commands.checks.has_permissions(administrator=True)
async def remove_agent(interaction: discord.Interaction, name: str):
    removed = db.remove_agent(name=name, guild_id=interaction.guild_id)
    if removed:
        await interaction.response.send_message(f"🗑️ Agent **{name}** removed.", ephemeral=True)
        await update_tally_channel(interaction.guild)
    else:
        await interaction.response.send_message(f"Agent **{name}** not found.", ephemeral=True)


@bot.tree.command(name="set_premium_role", description="Set the role that Discord Shop premium members receive.")
@app_commands.describe(role="The premium subscriber role")
@app_commands.checks.has_permissions(administrator=True)
async def set_premium_role(interaction: discord.Interaction, role: discord.Role):
    db.set_premium_role(guild_id=interaction.guild_id, role_id=role.id)
    await interaction.response.send_message(
        f"✅ Premium role set to **{role.name}**. "
        "Members who receive this role will be tracked as conversions.",
        ephemeral=True,
    )


@bot.tree.command(name="refresh_tally", description="Force-refresh the tally channel embed.")
@app_commands.checks.has_permissions(administrator=True)
async def refresh_tally(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await update_tally_channel(interaction.guild)
    await interaction.followup.send("✅ Tally refreshed.", ephemeral=True)


@bot.tree.command(name="link_member", description="Manually link a member to an agent (for backdating or corrections).")
@app_commands.describe(member="The Discord member", agent_name="The agent's registered name")
@app_commands.checks.has_permissions(administrator=True)
async def link_member(interaction: discord.Interaction, member: discord.Member, agent_name: str):
    agent = db.get_agent_by_name(agent_name, guild_id=interaction.guild_id)
    if not agent:
        await interaction.response.send_message(f"Agent **{agent_name}** not found.", ephemeral=True)
        return
    db.update_member_agent(member_id=member.id, agent_id=agent["agent_id"])
    await interaction.response.send_message(
        f"✅ **{member.display_name}** linked to agent **{agent_name}**.", ephemeral=True
    )
    await update_tally_channel(interaction.guild)


@add_agent.error
@list_agents.error
@remove_agent.error
@set_premium_role.error
@refresh_tally.error
@link_member.error
async def admin_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You need Administrator permission.", ephemeral=True)


bot.run(TOKEN)
