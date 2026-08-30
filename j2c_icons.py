import discord
import os

# J2C Interface Button Icons Configuration
# You can set:
# 1. A local image file path (e.g., "cogs/j2c_icons/lock.png" or "lock.png").
#    The bot will automatically upload this image as a custom emoji to the server.
# 2. A custom Discord emoji string (e.g., "<:emoji_name:emoji_id>" or "<a:emoji_name:emoji_id>").
# 3. A Unicode emoji (e.g., "🔒").
#
# Change these values to configure the button icons.

J2C_ICONS = {
    "lock": "cogs/j2c_icons/lock.png",
    "unlock": "cogs/j2c_icons/unlock.png",
    "hide": "cogs/j2c_icons/hide.png",
    "unhide": "cogs/j2c_icons/unhide.png",
    "limit": "cogs/j2c_icons/limit.png",
    "invite": "cogs/j2c_icons/invite.png",
    "ban": "cogs/j2c_icons/ban.png",
    "permit": "cogs/j2c_icons/permit.png",
    "rename": "cogs/j2c_icons/rename.png",
    "bitrate": "cogs/j2c_icons/bitrate.png",
    "region": "cogs/j2c_icons/region.png",
    "template": "cogs/j2c_icons/template.png",
    "chat": "cogs/j2c_icons/chat.png",
    "waiting": "cogs/j2c_icons/waiting.png",
    "claim": "cogs/j2c_icons/claim.png",
    "transfer": "cogs/j2c_icons/transfer.png",
}

# Fallback Unicode emojis to use if an image path is specified but cannot be resolved/uploaded.
FALLBACK_UNICODE_EMOJIS = {
    "lock": "🔒", "unlock": "🔓", "hide": "👻", "unhide": "👁️",
    "limit": "👥", "invite": "➕", "ban": "❌", "permit": "👤",
    "rename": "📝", "bitrate": "🎧", "region": "💾", "template": "🗂️",
    "chat": "💬", "waiting": "🕒", "claim": "👑", "transfer": "⚡",
}

def is_image_path(val: str) -> bool:
    """Checks if a configuration value represents an image file path."""
    if not isinstance(val, str):
        return False
    # Check if it has a typical image extension
    ext = os.path.splitext(val.lower())[1]
    if ext in ['.png', '.jpg', '.jpeg', '.webp', '.gif']:
        return True
    # Or if the path exists on disk directly
    if os.path.exists(val):
        return True
    return False

def resolve_image_path(val: str) -> str:
    """Resolves relative image paths to absolute paths within the project directory."""
    if os.path.isabs(val):
        return val
    # Check relative to working directory
    if os.path.exists(val):
        return os.path.abspath(val)
    # Check relative to this configuration file's directory (workspace root)
    base_dir = os.path.dirname(os.path.abspath(__file__))
    project_relative = os.path.join(base_dir, val)
    if os.path.exists(project_relative):
        return project_relative
    # Check relative to cogs directory
    cogs_relative = os.path.join(base_dir, 'cogs', val)
    if os.path.exists(cogs_relative):
        return cogs_relative
    return os.path.abspath(val)

def get_j2c_emoji(action: str, guild_emojis_cache=None):
    """
    Resolves the icon configured for an action into a format suitable for discord.py UI components.
    Returns a discord.PartialEmoji for custom/uploaded emojis or a string for Unicode emojis.
    """
    raw = J2C_ICONS.get(action)
    if not raw:
        return None
        
    if is_image_path(raw):
        if guild_emojis_cache and action in guild_emojis_cache:
            return guild_emojis_cache[action]
        return FALLBACK_UNICODE_EMOJIS.get(action)

    if raw.startswith("<:") or raw.startswith("<a:"):
        try:
            return discord.PartialEmoji.from_str(raw)
        except Exception:
            return None
    return raw
