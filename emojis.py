import os
import json
import re
import discord

# Default Custom Discord Emojis
DEFAULT_EMOJIS = {
    # Help Categories
    "security": "🛡️",
    "antinuke": "🛡️",
    "antiraid": "🛡️",
    "permit_commands": "👑",
    "permit": "👑",
    "automod": "🛡️",
    "moderation": "⚒️",
    "general": "ℹ️",
    "games": "🎮",
    "embed_system": "❇️",
    "embed": "❇️",
    "utility": "⚙️",
    "automations": "🔗",
    "automation": "🔗",
    "autoresponders": "💖",
    "greetings": "👋",
    "welcome": "🚪",
    "custom_roles": "🎨",
    "roles": "🤌",
    "voice_commands": "🔊",
    "voice": "🔊",
    "tickets": "🎫",
    "helpdesk": "🐍",
    "logging": "📋",
    "voice_master": "🎙️",
    "join2create": "📢",
    "j2c": "📢",
    "bot_settings": "⚙️",
    "settings": "📡",
    "branding": "📡",
    "custom_branding": "📡",
    "payments": "💳",
    "success": "✅",
    "fail": "<:cross:1537988934007529544>",
    "tick": "✅",
    "cross": "❌",
    "upi": "💳",
    "ltc": "💳",
    "giveaway": "🎉",
    "tracking": "📊",
    "leaderboard": "📊",
    "social": "💬",
    "noprefix": "👑",
    "owner": "👑",
    "premium": "💎",
    "premium_avon": "💎",
    
    # UI Elements & Statuses
    "ticket_open": "🎫",
    "lock": "🔒",
    "warn": "⚠️",
    "warning": "⚠️",
    "mute": "🔇",
    "kick": "👢",
    "gwkick": "👢",
    "ban": "🔨",
    "global": "🌐",
    "server": "🏠",
    "delete": "🗑️",
    "success": "✅",
    "error": "❌",
    "failed": "❌",

    # Navigation & Indicators
    "star": "⭐",
    "link": "🔗",
    "ping": "🏓",
    "tip": "💡",
    "arrow": "❯",
    "bullet": "•",
    "point": "👉",
    "hide": "👻"
}

try:
    base_dir = os.path.dirname(os.path.abspath(__file__))
except NameError:
    base_dir = "dist" if os.path.exists("dist/emojis.json") else "."
EMOJIS_FILE = os.path.join(base_dir, "emojis.json")

# Load customized emojis if exists, otherwise create it with defaults
def load_emojis():
    emojis = DEFAULT_EMOJIS.copy()
    if os.path.exists(EMOJIS_FILE):
        try:
            with open(EMOJIS_FILE, "r", encoding="utf-8") as f:
                user_configs = json.load(f)
                for k, v in user_configs.items():
                    if v:
                        emojis[k] = v
        except Exception as e:
            print(f"Error loading emojis.json: {e}")
    else:
        try:
            with open(EMOJIS_FILE, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_EMOJIS, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Error writing default emojis.json: {e}")
    return emojis

# Load on module import
EMOJI_MAPPING = load_emojis()

def reload_emojis():
    global EMOJI_MAPPING
    EMOJI_MAPPING = load_emojis()

def save_emojis(new_emojis):
    try:
        current = {}
        if os.path.exists(EMOJIS_FILE):
            with open(EMOJIS_FILE, "r", encoding="utf-8") as f:
                current = json.load(f)
        for k, v in new_emojis.items():
            current[k] = v
        with open(EMOJIS_FILE, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=4, ensure_ascii=False)
        reload_emojis()
    except Exception as e:
        print(f"Error saving emojis: {e}")


bot_instance = None

UNICODE_FALLBACKS = {
    "security": "🛡️",
    "antinuke": "🛡️",
    "antiraid": "🛡️",
    "permit_commands": "👑",
    "permit": "👑",
    "automod": "▲",
    "moderation": "⚒️",
    "general": "👻",
    "embed_system": "❇️",
    "embed": "❇️",
    "utility": "⚙️",
    "automations": "🔗",
    "automation": "🔗",
    "autoresponders": "💖",
    "greetings": "🚪",
    "welcome": "🚪",
    "custom_roles": "🤌",
    "roles": "🤌",
    "voice_commands": "🔊",
    "voice": "🔊",
    "tickets": "🐍",
    "logging": "🦇",
    "voice_master": "📢",
    "join2create": "📢",
    "j2c": "📢",
    "bot_settings": "📡",
    "settings": "📡",
    "payments": "💳",
    "upi": "💳",
    "ltc": "💳",
    "giveaway": "🎉",
    "success": "✅",
    "fail": "<:cross:1537988934007529544>",
    "tick": "✅",
    "cross": "❌",
    "tracking": "📊",
    "social": "💬",
    "owner": "👑",
    "hide": "👻"
}

def parse_emoji_str(val: str) -> str:
    if not val:
        return ""
    val = str(val).strip()
    gif_match = re.match(r"https?://cdn\.discordapp\.com/emojis/(\d+)\.gif(?:\?\S*)?", val)
    if gif_match:
        return f"<a:custom:{gif_match.group(1)}>"
    png_match = re.match(r"https?://cdn\.discordapp\.com/emojis/(\d+)\.(?:png|webp)(?:\?\S*)?", val)
    if png_match:
        return f"<:custom:{png_match.group(1)}>"
    return val

def get_emoji(name: str) -> str:
    """Get the string representation of a custom emoji (<:name:id> or <a:name:id>)."""
    if not name:
        return ""
    name_str = str(name).strip()

    parsed_cdn = parse_emoji_str(name_str)
    if parsed_cdn != name_str:
        return parsed_cdn

    if name_str.startswith("<:") or name_str.startswith("<a:"):
        return name_str

    clean_name = name_str.lower().replace(" ", "_")
    if clean_name.startswith("ani_"):
        clean_name = clean_name[4:]

    emoji_str = EMOJI_MAPPING.get(clean_name, "") or DEFAULT_EMOJIS.get(clean_name, "")
    if emoji_str and "000000000000000000" not in emoji_str:
        return emoji_str

    return UNICODE_FALLBACKS.get(clean_name, "")

def get_ui_emoji(name: str):
    """Get the emoji object for Discord UI components (Buttons, SelectOptions)."""
    if not name:
        return None
    name_str = str(name).strip()
    clean_name = name_str.lower().replace(" ", "_")
    if clean_name.startswith("ani_"):
        clean_name = clean_name[4:]

    emoji_str = get_emoji(clean_name) or name_str

    if emoji_str and (emoji_str.startswith("<:") or emoji_str.startswith("<a:")):
        try:
            return discord.PartialEmoji.from_str(emoji_str)
        except Exception:
            return UNICODE_FALLBACKS.get(clean_name, "🌐")

    return emoji_str

