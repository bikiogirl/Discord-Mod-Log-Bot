# ==========================================
#   Discord Music Bot
#   Created & Maintained by Bikio
#   GitHub: https://github.com/bikiogirl
# ==========================================
import asyncio
import datetime as dt
import json
import os

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv
print("Done !")

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0"))
GUILD_ID = os.getenv("GUILD_ID")  # optional: instant slash-command sync for one server
BANS_FILE = "tempbans.json"

# ---------------------------------------------------------------- intents
intents = discord.Intents.default()
intents.members = True          # Server Members Intent (privileged)
intents.message_content = True  # Message Content Intent (privileged)


class ModBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync()
        else:
            await self.tree.sync()
        unban_loop.start()


bot = ModBot()

# ---------------------------------------------------------------- helpers
COLOR_RED = discord.Color.red()
COLOR_GREEN = discord.Color.green()
COLOR_BLUE = discord.Color.blurple()
COLOR_ORANGE = discord.Color.orange()


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def cut(text: str | None, limit: int = 1000) -> str:
    if not text:
        return "—"
    return text if len(text) <= limit else text[: limit - 3] + "..."


async def get_log_channel() -> discord.abc.Messageable | None:
    ch = bot.get_channel(LOG_CHANNEL_ID)
    if ch is None:
        try:
            ch = await bot.fetch_channel(LOG_CHANNEL_ID)
        except discord.HTTPException:
            return None
    return ch


async def send_log(embed: discord.Embed, guild: discord.Guild | None = None):
    ch = await get_log_channel()
    if ch is None:
        return
    # only log events from the guild that owns the log channel
    if guild is not None and getattr(ch, "guild", None) and ch.guild.id != guild.id:
        return
    embed.timestamp = now()
    try:
        await ch.send(embed=embed)
    except discord.HTTPException:
        pass


async def find_executor(guild: discord.Guild, action: discord.AuditLogAction, target_id: int | None = None):
    """Best-effort: who performed an action (needs View Audit Log permission)."""
    if not guild.me.guild_permissions.view_audit_log:
        return None
    await asyncio.sleep(1.5)  # audit log entries can arrive slightly late
    try:
        async for entry in guild.audit_logs(limit=8, action=action):
            if (now() - entry.created_at).total_seconds() > 15:
                break
            if target_id is None or (entry.target is not None and entry.target.id == target_id):
                return entry.user
    except discord.HTTPException:
        pass
    return None


# ---------------------------------------------------------------- temp bans storage
def load_bans() -> list[dict]:
    if not os.path.exists(BANS_FILE):
        return []
    try:
        with open(BANS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save_bans(data: list[dict]):
    with open(BANS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


@tasks.loop(seconds=30)
async def unban_loop():
    bans = load_bans()
    if not bans:
        return
    remaining = []
    for entry in bans:
        expires = dt.datetime.fromisoformat(entry["expires_at"])
        if expires > now():
            remaining.append(entry)
            continue
        guild = bot.get_guild(entry["guild_id"])
        if guild is None:
            remaining.append(entry)
            continue
        try:
            await guild.unban(discord.Object(id=entry["user_id"]), reason="Temporary ban expired")
            embed = discord.Embed(title="🔓 Temporary Ban Expired", color=COLOR_GREEN)
            embed.add_field(name="User", value=f"<@{entry['user_id']}> (`{entry['user_id']}`)")
            await send_log(embed, guild)
        except discord.NotFound:
            pass  # already unbanned manually
        except discord.HTTPException:
            remaining.append(entry)  # retry next cycle
    save_bans(remaining)


@unban_loop.before_loop
async def before_unban_loop():
    await bot.wait_until_ready()


# ================================================================ LOGS SYSTEM
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")


# ---- Message delete (works for cached and uncached messages)
@bot.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    if payload.guild_id is None:
        return
    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return
    msg = payload.cached_message
    if msg is not None and msg.author.bot:
        return

    embed = discord.Embed(title="🗑️ Message Deleted", color=COLOR_RED)
    embed.add_field(name="Channel", value=f"<#{payload.channel_id}>", inline=True)
    embed.add_field(name="Message ID", value=str(payload.message_id), inline=True)

    if msg is not None:
        embed.add_field(name="Author", value=f"{msg.author.mention} (`{msg.author.id}`)", inline=False)
        embed.add_field(name="Content", value=cut(msg.content), inline=False)
        if msg.attachments:
            embed.add_field(
                name="Attachments",
                value=cut("\n".join(a.url for a in msg.attachments)),
                inline=False,
            )
        executor = await find_executor(guild, discord.AuditLogAction.message_delete, msg.author.id)
        if executor:
            embed.add_field(name="Deleted by", value=f"{executor.mention}", inline=False)
        embed.set_thumbnail(url=msg.author.display_avatar.url)
    else:
        embed.add_field(name="Content", value="*Message was not cached — content unavailable.*", inline=False)

    await send_log(embed, guild)


# ---- Global avatar change
@bot.event
async def on_user_update(before: discord.User, after: discord.User):
    if before.avatar == after.avatar:
        return
    ch = await get_log_channel()
    guild = getattr(ch, "guild", None)
    if guild is None or guild.get_member(after.id) is None:
        return
    embed = discord.Embed(title="🖼️ Profile Picture Changed", color=COLOR_BLUE)
    embed.add_field(name="User", value=f"{after.mention} (`{after.id}`)", inline=False)
    embed.set_thumbnail(url=before.display_avatar.url)  # old
    embed.set_image(url=after.display_avatar.url)       # new
    embed.set_footer(text="Thumbnail = old • Large image = new")
    await send_log(embed, guild)


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    guild = after.guild

    # ---- Nickname change
    if before.nick != after.nick:
        embed = discord.Embed(title="✏️ Nickname Changed", color=COLOR_BLUE)
        embed.add_field(name="User", value=f"{after.mention} (`{after.id}`)", inline=False)
        embed.add_field(name="Before", value=before.nick or "*None*", inline=True)
        embed.add_field(name="After", value=after.nick or "*None*", inline=True)
        executor = await find_executor(guild, discord.AuditLogAction.member_update, after.id)
        if executor and executor.id != after.id:
            embed.add_field(name="Changed by", value=executor.mention, inline=False)
        embed.set_thumbnail(url=after.display_avatar.url)
        await send_log(embed, guild)

    # ---- Server-specific avatar change
    if before.guild_avatar != after.guild_avatar:
        embed = discord.Embed(title="🖼️ Server Profile Picture Changed", color=COLOR_BLUE)
        embed.add_field(name="User", value=f"{after.mention} (`{after.id}`)", inline=False)
        if before.guild_avatar:
            embed.set_thumbnail(url=before.guild_avatar.url)
        if after.guild_avatar:
            embed.set_image(url=after.guild_avatar.url)
        await send_log(embed, guild)

    # ---- Roles added / removed
    before_ids = {r.id for r in before.roles}
    after_ids = {r.id for r in after.roles}
    added = [r for r in after.roles if r.id not in before_ids]
    removed = [r for r in before.roles if r.id not in after_ids]

    if added or removed:
        executor = await find_executor(guild, discord.AuditLogAction.member_role_update, after.id)
        for role in added:
            embed = discord.Embed(title="➕ Role Added to User", color=COLOR_GREEN)
            embed.add_field(name="User", value=f"{after.mention} (`{after.id}`)", inline=False)
            embed.add_field(name="Role", value=f"{role.mention} (`{role.id}`)", inline=False)
            if executor:
                embed.add_field(name="By", value=executor.mention, inline=False)
            await send_log(embed, guild)
        for role in removed:
            embed = discord.Embed(title="➖ Role Removed from User", color=COLOR_ORANGE)
            embed.add_field(name="User", value=f"{after.mention} (`{after.id}`)", inline=False)
            embed.add_field(name="Role", value=f"`{role.name}` (`{role.id}`)", inline=False)
            if executor:
                embed.add_field(name="By", value=executor.mention, inline=False)
            await send_log(embed, guild)


# ---- Role create / delete / update
@bot.event
async def on_guild_role_create(role: discord.Role):
    embed = discord.Embed(title="🆕 Role Created", color=COLOR_GREEN)
    embed.add_field(name="Role", value=f"{role.mention} (`{role.id}`)", inline=False)
    embed.add_field(name="Color", value=str(role.color), inline=True)
    executor = await find_executor(role.guild, discord.AuditLogAction.role_create, role.id)
    if executor:
        embed.add_field(name="Created by", value=executor.mention, inline=True)
    await send_log(embed, role.guild)


@bot.event
async def on_guild_role_delete(role: discord.Role):
    embed = discord.Embed(title="❌ Role Deleted", color=COLOR_RED)
    embed.add_field(name="Role", value=f"`{role.name}` (`{role.id}`)", inline=False)
    embed.add_field(name="Color", value=str(role.color), inline=True)
    executor = await find_executor(role.guild, discord.AuditLogAction.role_delete, role.id)
    if executor:
        embed.add_field(name="Deleted by", value=executor.mention, inline=True)
    await send_log(embed, role.guild)


@bot.event
async def on_guild_role_update(before: discord.Role, after: discord.Role):
    changes = []
    if before.name != after.name:
        changes.append(("Name", f"`{before.name}` → `{after.name}`"))
    if before.color != after.color:
        changes.append(("Color", f"{before.color} → {after.color}"))
    if before.hoist != after.hoist:
        changes.append(("Display separately", f"{before.hoist} → {after.hoist}"))
    if before.mentionable != after.mentionable:
        changes.append(("Mentionable", f"{before.mentionable} → {after.mentionable}"))
    if before.permissions != after.permissions:
        gained = [n for n, v in after.permissions if v and not getattr(before.permissions, n)]
        lost = [n for n, v in after.permissions if not v and getattr(before.permissions, n)]
        text = ""
        if gained:
            text += "**Added:** " + ", ".join(gained) + "\n"
        if lost:
            text += "**Removed:** " + ", ".join(lost)
        changes.append(("Permissions", cut(text.strip())))

    if not changes:
        return  # e.g. only position changed — ignore (too noisy)

    embed = discord.Embed(title="🛠️ Role Updated", color=COLOR_ORANGE)
    embed.add_field(name="Role", value=f"{after.mention} (`{after.id}`)", inline=False)
    for name, value in changes:
        embed.add_field(name=name, value=value, inline=False)
    executor = await find_executor(after.guild, discord.AuditLogAction.role_update, after.id)
    if executor:
        embed.add_field(name="Edited by", value=executor.mention, inline=False)
    await send_log(embed, after.guild)


# ================================================================ MODERATION
def hierarchy_error(inter: discord.Interaction, target: discord.abc.User) -> str | None:
    """Returns an error string if the action is not allowed, else None."""
    guild = inter.guild
    if target.id == inter.user.id:
        return "You can't do that to yourself."
    if target.id == bot.user.id:
        return "I can't do that to myself."
    if target.id == guild.owner_id:
        return "You can't do that to the server owner."
    if isinstance(target, discord.Member):
        if inter.user.id != guild.owner_id and target.top_role >= inter.user.top_role:
            return "That user's highest role is equal to or higher than yours."
        if target.top_role >= guild.me.top_role:
            return "That user's highest role is equal to or higher than mine."
    return None


def mod_reason(inter: discord.Interaction, reason: str) -> str:
    return f"{reason} | By {inter.user} ({inter.user.id})"[:512]


async def dm_user(user: discord.abc.User, guild: discord.Guild, action: str, reason: str, extra: str = ""):
    embed = discord.Embed(title=f"You were {action} in {guild.name}", color=COLOR_RED)
    embed.add_field(name="Reason", value=cut(reason), inline=False)
    if extra:
        embed.add_field(name="Duration", value=extra, inline=False)
    try:
        await user.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        pass


def mod_log_embed(title: str, color: discord.Color, inter: discord.Interaction,
                  target: discord.abc.User, reason: str, duration: str | None = None) -> discord.Embed:
    embed = discord.Embed(title=title, color=color)
    embed.add_field(name="User", value=f"{target.mention} (`{target.id}`)", inline=True)
    embed.add_field(name="Moderator", value=f"{inter.user.mention}", inline=True)
    if duration:
        embed.add_field(name="Duration", value=duration, inline=False)
    embed.add_field(name="Reason", value=cut(reason), inline=False)
    return embed


# ---- /kick
@bot.tree.command(name="kick", description="Kick a user from the server.")
@app_commands.describe(user="The user to kick", reason="Reason for the kick")
@app_commands.guild_only()
@app_commands.default_permissions(kick_members=True)
@app_commands.checks.has_permissions(kick_members=True)
@app_commands.checks.bot_has_permissions(kick_members=True)
async def kick(inter: discord.Interaction, user: discord.Member, reason: str = "No reason provided"):
    err = hierarchy_error(inter, user)
    if err:
        return await inter.response.send_message(f"❌ {err}", ephemeral=True)

    await dm_user(user, inter.guild, "kicked", reason)
    try:
        await user.kick(reason=mod_reason(inter, reason))
    except discord.HTTPException as e:
        return await inter.response.send_message(f"❌ Failed to kick: {e}", ephemeral=True)

    await inter.response.send_message(f"👢 Kicked **{user}** — {reason}")
    await send_log(mod_log_embed("👢 Member Kicked", COLOR_ORANGE, inter, user, reason), inter.guild)


# ---- /timeout
UNIT_SECONDS = {"minutes": 60, "hours": 3600, "days": 86400}
MAX_TIMEOUT = dt.timedelta(days=28)  # Discord limit


@bot.tree.command(name="timeout", description="Temporarily mute a user (timeout).")
@app_commands.describe(
    user="The user to timeout",
    duration="Amount of time (a number)",
    unit="Minutes, hours or days",
    reason="Reason for the timeout",
)
@app_commands.choices(unit=[
    app_commands.Choice(name="Minutes", value="minutes"),
    app_commands.Choice(name="Hours", value="hours"),
    app_commands.Choice(name="Days", value="days"),
])
@app_commands.guild_only()
@app_commands.default_permissions(moderate_members=True)
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.checks.bot_has_permissions(moderate_members=True)
async def timeout(
    inter: discord.Interaction,
    user: discord.Member,
    duration: app_commands.Range[int, 1, 40320],
    unit: app_commands.Choice[str],
    reason: str = "No reason provided",
):
    err = hierarchy_error(inter, user)
    if err:
        return await inter.response.send_message(f"❌ {err}", ephemeral=True)

    delta = dt.timedelta(seconds=duration * UNIT_SECONDS[unit.value])
    if delta > MAX_TIMEOUT:
        return await inter.response.send_message("❌ Maximum timeout is 28 days.", ephemeral=True)

    label = f"{duration} {unit.name.lower()}"
    await dm_user(user, inter.guild, "timed out", reason, label)
    try:
        await user.timeout(delta, reason=mod_reason(inter, reason))
    except discord.HTTPException as e:
        return await inter.response.send_message(f"❌ Failed to timeout: {e}", ephemeral=True)

    until = discord.utils.format_dt(now() + delta, "R")
    await inter.response.send_message(f"🔇 Timed out **{user}** for **{label}** (ends {until}) — {reason}")
    await send_log(mod_log_embed("🔇 Member Timed Out", COLOR_ORANGE, inter, user, reason, label), inter.guild)


# ---- /ban
@bot.tree.command(name="ban", description="Ban a user (temporary or permanent).")
@app_commands.describe(user="The user to ban", duration="How long the ban lasts", reason="Reason for the ban")
@app_commands.choices(duration=[
    app_commands.Choice(name="1 day", value=1 * 1440),
    app_commands.Choice(name="3 days", value=3 * 1440),
    app_commands.Choice(name="7 days", value=7 * 1440),
    app_commands.Choice(name="14 days", value=14 * 1440),
    app_commands.Choice(name="30 days", value=30 * 1440),
    app_commands.Choice(name="Permanent", value=0),
])
@app_commands.guild_only()
@app_commands.default_permissions(ban_members=True)
@app_commands.checks.has_permissions(ban_members=True)
@app_commands.checks.bot_has_permissions(ban_members=True)
async def ban(
    inter: discord.Interaction,
    user: discord.User,
    duration: app_commands.Choice[int],
    reason: str = "No reason provided",
):
    guild = inter.guild
    target: discord.abc.User = guild.get_member(user.id) or user
    err = hierarchy_error(inter, target)
    if err:
        return await inter.response.send_message(f"❌ {err}", ephemeral=True)

    permanent = duration.value == 0
    label = "Permanent" if permanent else duration.name

    await dm_user(user, guild, "banned", reason, label)
    try:
        await guild.ban(user, reason=mod_reason(inter, reason), delete_message_seconds=0)
    except discord.HTTPException as e:
        return await inter.response.send_message(f"❌ Failed to ban: {e}", ephemeral=True)

    # update temp-ban storage
    bans = [b for b in load_bans() if not (b["guild_id"] == guild.id and b["user_id"] == user.id)]
    if not permanent:
        expires = now() + dt.timedelta(minutes=duration.value)
        bans.append({
            "guild_id": guild.id,
            "user_id": user.id,
            "expires_at": expires.isoformat(),
        })
    save_bans(bans)

    extra = "" if permanent else f" (unbanned {discord.utils.format_dt(now() + dt.timedelta(minutes=duration.value), 'R')})"
    await inter.response.send_message(f"🔨 Banned **{user}** — **{label}**{extra} — {reason}")
    await send_log(mod_log_embed("🔨 Member Banned", COLOR_RED, inter, user, reason, label), guild)


# ---- error handler for permission failures
@bot.tree.error
async def on_app_command_error(inter: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        msg = "❌ You don't have permission to use this command."
    elif isinstance(error, app_commands.BotMissingPermissions):
        msg = "❌ I'm missing the required permissions: " + ", ".join(error.missing_permissions)
    else:
        msg = "❌ Something went wrong."
        print(f"Command error: {error!r}")
    if inter.response.is_done():
        await inter.followup.send(msg, ephemeral=True)
    else:
        await inter.response.send_message(msg, ephemeral=True)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Missing DISCORD_TOKEN in .env")
    if not LOG_CHANNEL_ID:
        raise SystemExit("Missing LOG_CHANNEL_ID in .env")
    bot.run(TOKEN)
