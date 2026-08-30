import discord
from discord.ext import commands, tasks
import logging
import sys
from dotenv import load_dotenv
import os
import aiohttp
from datetime import timedelta
import datetime
import time
import aiosqlite
import asyncio
import re
import random
from aiohttp import web
from cryptography.hazmat.primitives.asymmetric import ed25519
from emojis import get_emoji, get_ui_emoji, save_emojis
import unicodedata
import database

THEME_COLOR = 0x2B2D31
ERROR_COLOR = 0xED4245
HELP_COLOR = 0x9333EA

HOMOGLYPH_MAP = {
    'а': 'a', 'α': 'a', '@': 'a', '4': 'a', 'ä': 'a', 'á': 'a', 'à': 'a', 'â': 'a', 'ã': 'a', 'å': 'a',
    'в': 'b', 'β': 'b', '8': 'b',
    'с': 'c', '©': 'c', '<': 'c', '(': 'c', 'ç': 'c',
    'đ': 'd', 'ð': 'd',
    'е': 'e', '3': 'e', '€': 'e', 'є': 'e', 'é': 'e', 'è': 'e', 'ê': 'e', 'ë': 'e',
    'ƒ': 'f',
    '6': 'g', '9': 'g',
    'н': 'h', '#': 'h',
    'і': 'i', '!': 'i', 'ï': 'i', 'í': 'i', 'ì': 'i', 'î': 'i',
    '1': 'l', '|': 'l',
    'м': 'm',
    'п': 'n', 'ñ': 'n',
    'о': 'o', '0': 'o', 'ø': 'o', 'θ': 'o', 'ö': 'o', 'ó': 'o', 'ò': 'o', 'ô': 'o', 'õ': 'o',
    'р': 'p', 'ρ': 'p',
    'г': 'r', '®': 'r',
    'ѕ': 's', '$': 's', '5': 's',
    'т': 't', '+': 't', '7': 't',
    'υ': 'u', 'µ': 'u', 'ü': 'u', 'ú': 'u', 'ù': 'u', 'û': 'u',
    'ν': 'v',
    'ш': 'w',
    'х': 'x', '%': 'x',
    'у': 'y', '¥': 'y', 'ÿ': 'y', 'ý': 'y',
    '2': 'z'
}

ZERO_WIDTH_CHARS = {
    '\u200b', '\u200c', '\u200d', '\ufeff', '\u2060',
    '\u200e', '\u200f', '\u00ad', '\u180e',
    '\u2000', '\u2001', '\u2002', '\u2003', '\u2004',
    '\u2005', '\u2006', '\u2007', '\u2008', '\u2009', '\u200a'
}

def normalize_text(text: str) -> str:
    if not text:
        return ""
    cleaned = "".join(ch for ch in text if ch not in ZERO_WIDTH_CHARS)
    nfkd_form = unicodedata.normalize('NFKD', cleaned)
    ascii_text = nfkd_form.encode('ASCII', 'ignore').decode('utf-8')
    if not ascii_text:
        ascii_text = cleaned
    res = []
    for ch in ascii_text.lower():
        res.append(HOMOGLYPH_MAP.get(ch, ch))
    return "".join(res)

ANTILINK_REGEX = re.compile(
    r'(?:https?://|ftp://|www\.)[^\s<>"{}|\\^`]+'
    r'|discord(?:\.gg|\.com/invite|\.me|\.io)/[a-zA-Z0-9_-]+'
    r'|(?:dsc\.gg|t\.me|telegram\.me|bit\.ly|tinyurl\.com|cutt\.ly|is\.gd|rb\.gy|v\.ht|goo\.gl)/[a-zA-Z0-9_-]+'
    r'|\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d{1,5})?\b'
    r'|\b[a-zA-Z0-9_-]+\s*\.\s*(?:com|org|net|xyz|gg|io|info|biz|me|online|site|store|app|dev|tech|co|top|fun|live|link|pro|run|club|space)\b',
    re.IGNORECASE
)

load_dotenv()
token = os.getenv('DISCORD_TOKEN')
public_key_hex = os.getenv('DISCORD_PUBLIC_KEY')

def _parse_developer_ids():
    ids = set()
    for raw in (os.getenv("VIREON_OWNER_ID", ""), os.getenv("VIREON_DEVELOPER_IDS", "")):
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit():
                ids.add(int(part))
    return ids

DEVELOPER_IDS = _parse_developer_ids()
DEVELOPER_ID = 1458089824350240781
main_owners_set = set(DEVELOPER_IDS)
second_owners_set = set()

_original_has_permissions = commands.has_permissions
def custom_has_permissions(**perms):
    original_check = _original_has_permissions(**perms)
    async def predicate(ctx):
        author_id = getattr(ctx.author, 'id', 0)
        if author_id in main_owners_set or author_id in DEVELOPER_IDS or author_id in second_owners_set:
            return True
        if hasattr(ctx.author, 'guild_permissions') and getattr(ctx.author.guild_permissions, 'administrator', False):
            return True
        return await discord.utils.maybe_coroutine(original_check.predicate, ctx)
    return commands.check(predicate)

commands.has_permissions = custom_has_permissions

_original_can_run = commands.Command.can_run
async def custom_can_run(self, ctx):
    author_id = getattr(ctx.author, 'id', 0)
    if author_id in main_owners_set or author_id in DEVELOPER_IDS or author_id in second_owners_set:
        return True
    return await _original_can_run(self, ctx)
commands.Command.can_run = custom_can_run


DB_PATH = 'bot.db'

# Setup logging
handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
logging.basicConfig(level=logging.INFO, handlers=[handler])

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

async def get_prefix(bot, message):
    if not message.guild:
        return ['!', f'<@{bot.user.id}>', f'<@!{bot.user.id}>']
        
    guild_prefix = '!'
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT prefix FROM guild_prefixes WHERE guild_id = ?', (message.guild.id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    guild_prefix = row[0]
    except Exception:
        pass
    
    author_id = getattr(message.author, 'id', 0)
    is_dev = author_id in DEVELOPER_IDS or author_id in main_owners_set or author_id in second_owners_set
    has_np = is_dev
    if not has_np:

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                'SELECT expires_at, guild_id FROM no_prefix WHERE (guild_id=? OR guild_id=0) AND user_id=? ORDER BY (expires_at IS NULL) DESC, expires_at DESC',
                (message.guild.id, message.author.id)
            ) as cursor:
                row = await cursor.fetchone()
            if row is not None:
                expires_at, row_guild_id = row
                if expires_at is None or time.time() < expires_at:
                    has_np = True
                else:
                    await db.execute('DELETE FROM no_prefix WHERE guild_id=? AND user_id=?', (row_guild_id, message.author.id))
                    await db.commit()
    
    empty_prefix = '' if has_np else None
    prefixes = [p for p in [empty_prefix, guild_prefix, f'<@{bot.user.id}>', f'<@!{bot.user.id}>'] if p is not None]
    return prefixes

async def get_role_from_alias(guild_id, alias):
    alias = alias.lower()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT role_id FROM aliases WHERE guild_id = ? AND alias = ?', (guild_id, alias)) as cursor:
            row = await cursor.fetchone()
            if row:
                return row[0]
            return None

def parse_duration(time_str):
    match = re.match(r'(\d+)([smhd])', time_str.lower())
    if not match:
        return None
    num, unit = int(match.group(1)), match.group(2)
    if unit == 's':
        return datetime.timedelta(seconds=num)
    elif unit == 'm':
        return datetime.timedelta(minutes=num)
    elif unit == 'h':
        return datetime.timedelta(hours=num)
    elif unit == 'd':
        return datetime.timedelta(days=num)
    return None

async def send_mod_dm(member, action, guild_name, moderator, reason=None, duration=None):
    """Send a rich embed DM to a member about a moderation action."""
    action_config = {
        'warn':  {'emoji': get_emoji('warn'),  'title': 'Warning',           'color': 0xFEE75C},
        'mute':  {'emoji': get_emoji('mute'), 'title': 'Muted',             'color': 0xE67E22},
        'kick':  {'emoji': get_emoji('kick'), 'title': 'Kicked',            'color': 0x2B2D31},
        'ban':   {'emoji': get_emoji('ban'),   'title': 'Banned',            'color': 0x2B2D31},
    }
    cfg = action_config.get(action, {'emoji': get_emoji('settings'), 'title': action.title(), 'color': 0x5865F2})

    embed = discord.Embed(
        title=f"{cfg['emoji']}  You have been {cfg['title']}!",
        color=cfg['color'],
    )
    embed.add_field(name="Server:", value=f"**{guild_name}**", inline=True)
    embed.add_field(name="Moderator:", value=f"{moderator.mention}", inline=True)
    if duration:
        embed.add_field(name="Duration:", value=f"`{duration}`", inline=True)
    if reason:
        embed.add_field(name="Reason:", value=reason, inline=False)
    else:
        embed.add_field(name="Reason:", value="No reason provided", inline=False)
    embed.set_footer(text="If you believe this was a mistake, contact a server admin.")
    embed.timestamp = discord.utils.utcnow()
    try:
        await member.send(embed=embed)
        return True
    except Exception:
        return False

def verify_signature(signature_hex: str, timestamp: str, body_bytes: bytes) -> bool:
    if not public_key_hex or public_key_hex == "your_discord_application_public_key_here":
        print("[WARNING] DISCORD_PUBLIC_KEY is not configured or holds the placeholder value in .env. Interaction verification will fail.")
        return False
    try:
        # Get verification key
        verify_key = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        # Prepare message to verify: timestamp + body
        message = timestamp.encode('utf-8') + body_bytes
        # Verify signature
        verify_key.verify(bytes.fromhex(signature_hex), message)
        return True
    except Exception as e:
        print(f"[ERROR] Signature verification failed with error: {e}")
        return False

async def handle_interactions(request):
    print(f"[DEBUG] Received interaction request from {request.remote}")
    signature = request.headers.get('X-Signature-Ed25519')
    timestamp = request.headers.get('X-Signature-Timestamp')

    if not signature or not timestamp:
        return web.Response(status=401, text='Missing signature headers')

    body = await request.read()

    if not verify_signature(signature, timestamp, body):
        return web.Response(status=401, text='Invalid request signature')

    try:
        data = await request.json()
    except Exception:
        return web.Response(status=400, text='Invalid JSON')

    interaction_type = data.get('type')
    if interaction_type == 1:  # PING
        return web.json_response({'type': 1})  # PONG

    return web.json_response({'type': 4, 'data': {'content': 'Interaction received'}})

async def run_webserver():
    app = web.Application()
    app.router.add_post('/api/interactions', handle_interactions)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 3000)
    await site.start()
    print("[OK] Interactions webserver running on http://localhost:3000/api/interactions")

async def send_group_suggestions(ctx):
    group_name = ctx.command.name.lower()
    suggestions_map = {
        'create': {
            'role': ('createrole', 'Create a new role in the server.')
        },
        'add': {
            'alias': ('addalias', 'Add a shortcut alias for a role.'),
            'np': ('addnp', 'Add a user or server to the no-prefix list.'),
            'msg': ('addmsg', 'Add messages to a user.')
        },
        'remove': {
            'alias': ('removealias', 'Remove a shortcut alias for a role.'),
            'np': ('remnp', 'Remove a user or server from the no-prefix list.'),
            'msg': ('removemsg', 'Remove messages from a user.'),
            'identity': ('removeidentity', 'Remove a bot-only identity from a user.')
        },
        'list': {
            'aliases': ('listaliases', 'List all role shortcut aliases in the server.'),
            'np': ('listnp', 'List all users and servers on the no-prefix list.')
        },
        'del': {
            'warn': ('delwarn', 'Delete a specific warning by ID.')
        },
        'clear': {
            'warnings': ('clearwarnings', 'Clear all warnings for a member.')
        },
        'setup': {
            'j2c': ('setup_j2c', 'Setup Join to Create voice system and control panel.')
        },
        'reset': {
            'msg': ('resetmsg', 'Reset messages for a user or the whole server.')
        },
        'vc': {
            'kick': ('vckick', 'Kick a member from a voice channel.'),
            'pull': ('vcpull', 'Pull a member into your voice channel.'),
            'deafen': ('vcdeafen', 'Deafen a member in a voice channel.'),
            'mute': ('vcmute', 'Mute a member in a voice channel.'),
            'pullall': ('vcpullall', 'Pull all members into your voice channel.'),
            'kickall': ('vckickall', 'Kick all members from a voice channel.'),
            'deafenall': ('vcdeafenall', 'Deafen all members in a voice channel.')
        },
        'social': {
            'snapchat': ('snapchat', 'Link or view the Snapchat profile of a member.'),
            'instagram': ('instagram', 'Link or view the Instagram profile of a member.'),
            'twitter': ('twitter', 'Link or view the Twitter/X profile of a member.'),
            'telegram': ('telegram', 'Link or view the Telegram profile of a member.')
        },
        'game': {
            'valorant': ('valorant', 'Link or view the Valorant profile of a member.'),
            'xbox': ('xbox', 'Link or view the Xbox profile of a member.'),
            'steam': ('steam', 'Link or view the Steam profile of a member.'),
            'freefire': ('freefire', 'Link or view the Free Fire profile of a member.'),
            'roblox': ('roblox', 'Link or view the Roblox profile of a member.'),
            'coc': ('coc', 'Link or view the Clash of Clans profile of a member.')
        }
    }
    
    if group_name in suggestions_map:
        prefix_disp = ctx.prefix if ctx.prefix else '!'
        embed = discord.Embed(
            title="🔍  Command Suggestions",
            description=f"It seems you typed a base command: `{prefix_disp}{group_name}`\nDid you mean one of the following?",
            color=THEME_COLOR
        )
        for sub, (cmd_name, desc) in suggestions_map[group_name].items():
            embed.add_field(
                name=f"{get_emoji('point')}  {prefix_disp}{group_name} {sub} `(or {prefix_disp}{cmd_name})`",
                value=f"> {desc}",
                inline=False
            )
        embed.timestamp = discord.utils.utcnow()
        await ctx.send(embed=embed)

bot = commands.Bot(command_prefix=get_prefix, intents=intents, help_command=None, case_insensitive=True, status=discord.Status.dnd)
bot.start_time = time.time()

_orig_tree_add = bot.tree.add_command
def _safe_tree_add(command, *args, **kwargs):
    try:
        return _orig_tree_add(command, *args, **kwargs)
    except Exception:
        return None
bot.tree.add_command = _safe_tree_add

def bot_is_main_owner(user_id, guild_id=0):
    return user_id in main_owners_set
bot.is_main_owner = bot_is_main_owner


async def auto_sync_custom_emojis(bot_inst):
    try:
        await bot_inst.wait_until_ready()
        import os
        import asyncio
        import emojis as _emojis_module
        
        ALIAS_MAPPINGS = {
            "gwkick": ["kick"],
            "leave_": ["leave"],
            "pingg": ["ping"],
            "ping_avon": ["ping"],
            "roundboost": ["boost"],
            "roundunmute": ["unmute"],
            "rounduser": ["user", "profiles"],
            "roundloading": ["loading"],
            "roundremove": ["remove", "delete"],
            "roundcategory": ["category"],
            "roundchannel": ["channel"],
            "roundhome": ["home", "server"],
            "warning": ["warn"],
            "name": ["nickname", "rename"],
            "info": ["general"],
            "tickets": ["helpdesk"],
            "9519donordiamond": ["donor", "diamond"],
            "premium_avon": ["premium"],
            "queue_avon": ["queue"],
            "friction_bots": ["bots"],
            "j2c": ["join2create"],
        }

        base_dir = os.path.dirname(os.path.abspath(__file__))
        emoji_dir = os.path.join(base_dir, "emoji_pack")
        if not os.path.exists(emoji_dir):
            emoji_dir = os.path.join(base_dir, "custom_emojis")
        if not os.path.exists(emoji_dir):
            return

        target_guild = None
        for g in bot_inst.guilds:
            perms = g.me.guild_permissions
            if getattr(perms, 'manage_expressions', False) or getattr(perms, 'manage_emojis', False) or getattr(perms, 'manage_emojis_and_stickers', False):
                target_guild = g
                break
        
        updated_map = {}
        for emoji_obj in bot_inst.emojis:
            name_lower = emoji_obj.name.lower()
            clean_key = name_lower[4:] if name_lower.startswith("ani_") else name_lower
            updated_map[name_lower] = str(emoji_obj)
            updated_map[clean_key] = str(emoji_obj)
            if clean_key in ALIAS_MAPPINGS:
                for alias in ALIAS_MAPPINGS[clean_key]:
                    updated_map[alias] = str(emoji_obj)
            if name_lower in ALIAS_MAPPINGS:
                for alias in ALIAS_MAPPINGS[name_lower]:
                    updated_map[alias] = str(emoji_obj)

        if updated_map:
            _emojis_module.save_emojis(updated_map)

        if not target_guild:
            return

        files = [f for f in os.listdir(emoji_dir) if f.lower().endswith(('.png', '.gif', '.jpg', '.jpeg', '.webp'))]
        new_uploaded = {}
        for filename in files:
            key = os.path.splitext(filename)[0].lower()
            emoji_name = f"ani_{key}"
            emoji_name = "".join(c for c in emoji_name if c.isalnum() or c == "_")
            if len(emoji_name) > 32:
                emoji_name = emoji_name[:32]

            existing = discord.utils.get(bot_inst.emojis, name=emoji_name) or discord.utils.get(bot_inst.emojis, name=key)
            if existing:
                new_uploaded[key] = str(existing)
                if key in ALIAS_MAPPINGS:
                    for alias in ALIAS_MAPPINGS[key]:
                        new_uploaded[alias] = str(existing)
                continue

            filepath = os.path.join(emoji_dir, filename)
            try:
                with open(filepath, 'rb') as f:
                    img_bytes = f.read()
                new_emoji = await target_guild.create_custom_emoji(name=emoji_name, image=img_bytes, reason="Vireon auto emoji setup")
                new_uploaded[key] = str(new_emoji)
                if key in ALIAS_MAPPINGS:
                    for alias in ALIAS_MAPPINGS[key]:
                        new_uploaded[alias] = str(new_emoji)
                logging.info(f"[EMOJI] Auto-uploaded {key} -> {new_emoji}")
                await asyncio.sleep(1.5)
            except Exception as e:
                logging.debug(f"[EMOJI] Could not auto-upload {filename}: {e}")

        if new_uploaded:
            _emojis_module.save_emojis(new_uploaded)

    except Exception as e:
        logging.error(f"[EMOJI] Auto sync error: {e}")


@tasks.loop(minutes=5)
async def cleanup_quarantine():
    import time
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT guild_id, user_id, original_roles FROM quarantined_users WHERE expires_at > 0 AND expires_at <= ?', (now,)) as cursor:
            rows = await cursor.fetchall()
        
        for g_id, u_id, r_ids in rows:
            guild = bot.get_guild(g_id)
            if not guild: continue
            member = guild.get_member(u_id)
            if not member: continue
            
            q_role = discord.utils.get(guild.roles, name="Vireon Quarantine")
            if q_role and q_role in member.roles:
                try: await member.remove_roles(q_role, reason="Automod: Quarantine expired")
                except discord.HTTPException: pass
                
            if r_ids:
                roles_to_restore = []
                for r_id_str in r_ids.split(','):
                    role = guild.get_role(int(r_id_str))
                    if role: roles_to_restore.append(role)
                if roles_to_restore:
                    try: await member.add_roles(*roles_to_restore, reason="Automod: Restoring roles after quarantine")
                    except discord.HTTPException: pass
                
            await db.execute('DELETE FROM quarantined_users WHERE guild_id = ? AND user_id = ?', (g_id, u_id))
        await db.commit()

@cleanup_quarantine.before_loop
async def before_cleanup_quarantine():
    await bot.wait_until_ready()

@tasks.loop(minutes=30)
async def cleanup_spam_tracker():
    if hasattr(bot, '_spam_tracker'):
        import time as _time
        now = _time.time()
        for key in list(bot._spam_tracker.keys()):
            bot._spam_tracker[key] = [t for t in bot._spam_tracker[key] if now - t < 600]
            if not bot._spam_tracker[key]:
                del bot._spam_tracker[key]

@bot.event
async def on_ready():
    logging.info(f'[OK] Bot logged in as {bot.user} (ID: {bot.user.id})')
    # Set emojis bot_instance for custom emoji validation
    import emojis as _emojis_module
    _emojis_module.bot_instance = bot
    asyncio.create_task(auto_sync_custom_emojis(bot))
    cleanup_spam_tracker.start()
    if not cleanup_quarantine.is_running():
        cleanup_quarantine.start()
    try:
        await bot.change_presence(status=discord.Status.dnd)
        logging.info('[OK] Set bot status to DND')
    except Exception as e:
        logging.error(f'[ERROR] Failed to set status: {e}')
        
    # Temporarily ignore CommandLimitReached during extension loading (restructuring below will group and sync all commands to 55 top-level slash commands)
    _orig_tree_add = bot.tree.add_command
    def _safe_tree_add(command, *args, **kwargs):
        try:
            return _orig_tree_add(command, *args, **kwargs)
        except Exception:
            return None
    bot.tree.add_command = _safe_tree_add

    # Load extensions first
    all_cogs = [
        'cogs.vc', 'cogs.noprefix', 'cogs.perms', 'cogs.antinuke', 'cogs.leaderboard',
        'cogs.logger', 'cogs.tickets', 'cogs.embed', 'cogs.roles', 'cogs.logging', 
        'cogs.extra', 'cogs.j2c', 'cogs.responders', 'cogs.channels', 'cogs.upi', 
        'cogs.ltc', 'cogs.invites', 'cogs.vouch'
    ]
    for cog in all_cogs:
        if cog not in bot.extensions:
            try:
                await bot.load_extension(cog)
                print(f'[OK] Loaded {cog} extension')
            except Exception as e:
                print(f'[ERROR] Failed to load {cog}: {e}')
    
    # Dynamic command restructuring to group subcommands and reduce top-level count to exactly 55
    def make_group(name, desc, aliases):
        @commands.hybrid_group(name=name, description=desc, aliases=aliases, invoke_without_command=True)
        async def group(ctx):
            await send_group_suggestions(ctx)
        return group

    groups_to_create = {
        'game': ('Link game accounts & statistics.', []),
        'social': ('Link social media profiles.', []),
        'alias': ('Manage command aliases.', []),
        'vc': ('Voice channel moderation & settings.', []),
        'channel': ('Channel moderation & settings.', ['ch']),
        'create': ('Create helper commands.', [])
    }

    mappings = {
        # Games & Social
        ('valorant', 'game', 'valorant'),
        ('xbox', 'game', 'xbox'),
        ('steam', 'game', 'steam'),
        ('freefire', 'game', 'freefire'),
        ('roblox', 'game', 'roblox'),
        ('coc', 'game', 'coc'),
        ('viewgames', 'game', 'view'),
        ('snapchat', 'social', 'snapchat'),
        ('instagram', 'social', 'instagram'),
        ('twitter', 'social', 'twitter'),
        ('telegram', 'social', 'telegram'),
        ('viewsocial', 'social', 'view'),

        # VC & Channel Cleanup
        ('vckick', 'vc', 'kick'),
        ('vcpull', 'vc', 'pull'),
        ('vcdeafen', 'vc', 'deafen'),
        ('vcmute', 'vc', 'mute'),
        ('vcpullall', 'vc', 'pullall'),
        ('vckickall', 'vc', 'kickall'),
        ('vcdeafenall', 'vc', 'deafenall'),
        ('vcdeleteall', 'vc', 'deleteall'),
        ('channeldeleteall', 'channel', 'deleteall'),

        # Helper Aliases / Roles
        ('createrole', 'role', 'create'),
        ('addalias', 'alias', 'add'),
        ('removealias', 'alias', 'remove'),
        ('listaliases', 'alias', 'list'),

        # Leaderboard subcommands
        ('addmsg', 'leaderboard', 'addmsg'),
        ('removemsg', 'leaderboard', 'removemsg'),
        ('resetmsg', 'leaderboard', 'resetmsg'),
        ('setidentity', 'leaderboard', 'setidentity'),
        ('removeidentity', 'leaderboard', 'removeidentity')
    }

    # 1. Remove all individual subcommands first to clear space in the CommandTree (avoids 100 limit exception)
    removed_commands = {}
    for orig, target_group, new_name in mappings:
        cmd = bot.remove_command(orig)
        if cmd:
            removed_commands[orig] = (cmd, target_group, new_name)

    # 2. Add the dynamic groups to the bot
    created_groups = {}
    for name, (desc, aliases) in groups_to_create.items():
        g = bot.get_command(name)
        if not g or not isinstance(g, commands.HybridGroup):
            group_cmd = make_group(name, desc, aliases)
            bot.add_command(group_cmd)
            created_groups[name] = group_cmd
        else:
            created_groups[name] = g

    # 3. Add the subcommands back under their new groups
    for orig, (cmd, target_group, new_name) in removed_commands.items():
        if orig == 'setprefix':
            continue
        cmd.name = new_name
        if hasattr(cmd, 'app_command') and cmd.app_command:
            cmd.app_command.name = new_name
        if target_group in ('leaderboard', 'role'):
            parent = bot.get_command(target_group)
        else:
            parent = created_groups.get(target_group)
            
        if parent and isinstance(parent, commands.HybridGroup):
            existing = parent.get_command(new_name)
            if existing:
                parent.remove_command(new_name)
            parent.add_command(cmd)
            cmd.parent = parent

    # Restore original tree add and rebuild the command tree to register the restructured hybrid subcommands
    bot.tree.add_command = _orig_tree_add
    bot.tree.clear_commands(guild=None)
    for cmd in bot.commands:
        if isinstance(cmd, (commands.HybridCommand, commands.HybridGroup)):
            try:
                bot.tree.add_command(cmd.app_command)
            except Exception as e:
                print(f"Error adding {cmd.name} to tree: {e}")
                logging.error(f"Error adding {cmd.name} to tree: {e}")

    # Sync command tree after all extensions are loaded
    try:
        synced = await bot.tree.sync()
        print(f"[OK] Synced {len(synced)} slash commands with Discord.")
        logging.info(f"[OK] Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"[ERROR] Failed to sync slash commands: {e}")
        logging.error(f"[ERROR] Failed to sync slash commands: {e}")

    # --- Restore database from Discord backup channel (if configured) ---
    try:
        await database.restore_db_from_discord(bot)
    except Exception as e:
        print(f"[BACKUP] Restore error (non-fatal): {e}")
        logging.error(f"[BACKUP] Restore error: {e}")

    await database.init_all_databases()

    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS aliases (
                guild_id INTEGER,
                alias TEXT,
                role_id INTEGER,
                UNIQUE(guild_id, alias)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS no_prefix (
                guild_id INTEGER,
                user_id INTEGER,
                expires_at REAL,
                UNIQUE(guild_id, user_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS guild_prefixes (
                guild_id INTEGER PRIMARY KEY,
                prefix TEXT
            )
        ''')
        try:
            await db.execute("ALTER TABLE no_prefix ADD COLUMN expires_at REAL")
        except Exception:
            pass
        await db.execute('''
            CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER,
                user_id INTEGER,
                moderator_id INTEGER,
                reason TEXT,
                timestamp TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS afk_status (
                user_id INTEGER PRIMARY KEY,
                reason TEXT,
                guild_id INTEGER,
                is_global INTEGER,
                timestamp REAL
            )
        ''')
        await db.commit()
        await db.execute('''
            CREATE TABLE IF NOT EXISTS antinuke_config (
                guild_id INTEGER PRIMARY KEY,
                enabled INTEGER DEFAULT 0,
                log_channel_id INTEGER,
                punishment TEXT DEFAULT 'ban',
                antiraid_enabled INTEGER DEFAULT 0,
                antiraid_threshold INTEGER DEFAULT 10
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS antinuke_owners (
                guild_id INTEGER,
                user_id INTEGER,
                UNIQUE(guild_id, user_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS antinuke_whitelist (
                guild_id INTEGER,
                user_id INTEGER,
                UNIQUE(guild_id, user_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS antinuke_wlroles (
                guild_id INTEGER,
                role_id INTEGER,
                UNIQUE(guild_id, role_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS automod_config (
                guild_id INTEGER PRIMARY KEY,
                antispam_enabled INTEGER DEFAULT 0,
                antilink_enabled INTEGER DEFAULT 0,
                antiword_enabled INTEGER DEFAULT 0,
                spam_max_messages INTEGER DEFAULT 5,
                spam_interval INTEGER DEFAULT 5
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS automod_badwords (
                guild_id INTEGER,
                word TEXT,
                UNIQUE(guild_id, word)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS automod_whitelist (
                guild_id INTEGER,
                target_id INTEGER,
                target_type TEXT,
                module TEXT,
                UNIQUE(guild_id, target_id, module)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS automod_punishments (
                guild_id INTEGER,
                module TEXT,
                punishment TEXT,
                mute_duration INTEGER DEFAULT 1,
                UNIQUE(guild_id, module, punishment)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS vcrole_config (
                guild_id INTEGER PRIMARY KEY,
                role_id INTEGER
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS autoresponders (
                guild_id INTEGER,
                trigger TEXT,
                response TEXT,
                UNIQUE(guild_id, trigger)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS playlists (
                id TEXT PRIMARY KEY,
                user_id INTEGER,
                name TEXT,
                description TEXT DEFAULT NULL,
                tracks TEXT DEFAULT '[]',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total_duration INTEGER DEFAULT 0,
                track_count INTEGER DEFAULT 0,
                UNIQUE(user_id, name)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS welcomer_config (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER,
                message TEXT,
                image_url TEXT,
                embed_name TEXT
            )
        ''')
        try:
            await db.execute("ALTER TABLE welcomer_config ADD COLUMN message TEXT")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE welcomer_config ADD COLUMN image_url TEXT")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE automod_config ADD COLUMN antiword_enabled INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE welcomer_config ADD COLUMN embed_name TEXT")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE second_owners ADD COLUMN guild_id INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE main_owners ADD COLUMN guild_id INTEGER DEFAULT 0")
        except Exception:
            pass
        await db.execute('''
            CREATE TABLE IF NOT EXISTS second_owners (
                user_id INTEGER,
                guild_id INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, guild_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS main_owners (
                user_id INTEGER PRIMARY KEY
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS leave_config (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER,
                message TEXT,
                image_url TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS boost_config (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER,
                message TEXT
            )
        ''')
        async with db.execute('SELECT user_id FROM main_owners') as cursor:
            rows = await cursor.fetchall()
            for (uid,) in rows:
                main_owners_set.add(uid)
        async with db.execute('SELECT user_id FROM second_owners') as cursor:
            rows = await cursor.fetchall()
            for (uid,) in rows:
                second_owners_set.add(uid)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS giveaways (
                message_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                host_id INTEGER NOT NULL,
                prize TEXT NOT NULL,
                winners INTEGER DEFAULT 1,
                end_time TEXT NOT NULL,
                ended INTEGER DEFAULT 0,
                participants TEXT DEFAULT '[]'
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS giveaway_managers (
                guild_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, role_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS locked_nicknames (
                guild_id INTEGER, 
                user_id INTEGER, 
                nickname TEXT, 
                PRIMARY KEY (guild_id, user_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS custom_branding (
                guild_id INTEGER PRIMARY KEY,
                avatar_url TEXT,
                banner_url TEXT,
                description TEXT,
                nickname TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS user_upi (
                user_id INTEGER PRIMARY KEY,
                upi_id TEXT NOT NULL,
                payee_name TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS guild_upi (
                guild_id INTEGER PRIMARY KEY,
                upi_id TEXT NOT NULL,
                payee_name TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS user_ltc (
                user_id INTEGER PRIMARY KEY,
                ltc_address TEXT NOT NULL,
                label TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS guild_ltc (
                guild_id INTEGER PRIMARY KEY,
                ltc_address TEXT NOT NULL,
                label TEXT
            )
        ''')
        await db.commit()
    print("[OK] Database initialized.")
    print('Bot ready! Use !commands for help.')

    # --- Start periodic database backup loop ---
    database.start_backup_loop(bot)

    # --- Start giveaway auto-end checker loop ---
    if not check_giveaways_loop.is_running():
        check_giveaways_loop.start()

@bot.event
async def on_member_join(member):
    # Sticky quarantine
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT original_roles FROM quarantined_users WHERE guild_id = ? AND user_id = ?', (member.guild.id, member.id)) as cursor:
            row = await cursor.fetchone()
        if row:
            q_role = discord.utils.get(member.guild.roles, name="Vireon Quarantine")
            if q_role:
                try: await member.add_roles(q_role, reason="Automod: Sticky Quarantine")
                except discord.HTTPException: pass

    # Prepare the message content
    content = (
        f"🌸🐱 Welcome to **{member.guild.name}**, {member.mention}! 🐱🌸\n"
        f"All-in-one hub for making friends, socialising and having fun!"
    )
    
    # Prepare the embed
    embed = discord.Embed(
        description=(
            f"**Quick reminders:**\n\n"
            f"🌻 . **Stay anonymous.**\n"
            f"~ Get to know people before sharing personal info.\n"
            f"🌻 . **Engage inside the server.**\n"
            f"~ Chats and VCs are moderated and managed.\n"
            f"🌻 . **DMs at your own risk.**\n"
            f"~ DMs are considered as personal responsibility.\n\n"
            f"*Need help? Let us know about your query in the tickets.*\n\n"
            f"Enjoy the purr-fect vibes! 💕"
        ),
        color=0xFEE75C  # Discord Yellow/Gold
    )
    
    # Set the server banner as image if available
    if member.guild.banner:
        embed.set_image(url=member.guild.banner.url)
        
    # Set the footer
    footer_text = f"Message from server: {member.guild.name}"
    if member.guild.description:
        footer_text += f" 🌻 {member.guild.description}"
    embed.set_footer(text=footer_text)
    
    try:
        await member.send(content=content, embed=embed)
    except discord.Forbidden:
        # Ignore if user has DMs closed
        pass
    except discord.HTTPException:
        pass

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT channel_id, message, image_url, embed_name FROM welcomer_config WHERE guild_id = ?', (member.guild.id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    channel_id, custom_message, image_url, embed_name = row
                    channel = member.guild.get_channel(channel_id)
                    if channel:
                        if embed_name:
                            async with db.execute('SELECT data FROM saved_embeds WHERE guild_id = ? AND name = ?', (member.guild.id, embed_name)) as embed_cursor:
                                embed_row = await embed_cursor.fetchone()
                            if embed_row:
                                try:
                                    import json
                                    from cogs.embed import build_embed_from_data
                                    data = json.loads(embed_row[0])
                                    
                                    welcome_embed = build_embed_from_data(data, member)
                                    
                                    await channel.send(content=member.mention, embed=welcome_embed)
                                    return
                                except Exception as e:
                                    print(f"Error loading custom welcome embed: {e}")
                        
                        welcome_text = custom_message if custom_message else f"Hey {member.mention}, welcome to the server! You are member #{len(member.guild.members)}."
                        
                        welcome_embed = discord.Embed(
                            title=f"Welcome to {member.guild.name}!",
                            description=welcome_text,
                            color=THEME_COLOR
                        )
                        welcome_embed.set_thumbnail(url=member.display_avatar.url)
                        if image_url:
                            welcome_embed.set_image(url=image_url)
                        elif member.guild.banner:
                            welcome_embed.set_image(url=member.guild.banner.url)
                        
                        await channel.send(content=member.mention, embed=welcome_embed)
    except Exception:
        pass

@bot.event
async def on_member_remove(member):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT channel_id, message, image_url FROM leave_config WHERE guild_id = ?', (member.guild.id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    channel_id, custom_message, image_url = row
                    channel = member.guild.get_channel(channel_id)
                    if channel:
                        from cogs.embed import safe_format
                        leave_text = custom_message if custom_message else f"{member.name} has left the server."
                        leave_text_fmt = safe_format(leave_text, member)
                        
                        embed = discord.Embed(
                            description=leave_text_fmt,
                            color=ERROR_COLOR
                        )
                        embed.set_author(name="Member Left", icon_url=member.display_avatar.url)
                        embed.set_footer(text=f"Total Members: {member.guild.member_count}")
                        if image_url:
                            embed.set_image(url=image_url)
                        elif member.guild.banner:
                            embed.set_image(url=member.guild.banner.url)
                            
                        await channel.send(embed=embed)
    except Exception:
        pass

@bot.event
async def on_member_update(before, after):
    if before.premium_since is None and after.premium_since is not None:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute('SELECT channel_id, message FROM boost_config WHERE guild_id = ?', (after.guild.id,)) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        channel_id, custom_message = row
                        channel = after.guild.get_channel(channel_id)
                        if channel:
                            from cogs.embed import safe_format
                            boost_text = custom_message if custom_message else f"Thank you {after.mention} for boosting the server!"
                            boost_text_fmt = safe_format(boost_text, after)
                            
                            embed = discord.Embed(
                                description=boost_text_fmt,
                                color=0xEB459E
                            )
                            embed.set_author(name=f"Server Boosted! {get_emoji('boost')}", icon_url=after.display_avatar.url)
                            embed.set_thumbnail(url="https://i.imgur.com/w9U9zP8.png")
                            embed.set_footer(text=f"Server Boosts: {after.guild.premium_subscription_count}")
                            await channel.send(embed=embed)
        except Exception:
            pass

    if before.nick != after.nick:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute('SELECT nickname FROM locked_nicknames WHERE guild_id = ? AND user_id = ?', (after.guild.id, after.id)) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        locked_nick = row[0]
                        if after.nick != locked_nick:
                            # Revert to locked nickname
                            try:
                                await after.edit(nick=locked_nick, reason="Reverted to locked nickname")
                            except discord.Forbidden:
                                pass
        except Exception:
            pass

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return
        
    # User joined a VC (was not in one before)
    if before.channel is None and after.channel is not None:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT role_id FROM vcrole_config WHERE guild_id = ?', (member.guild.id,)) as cursor:
                row = await cursor.fetchone()
                
        if row:
            role = member.guild.get_role(row[0])
            if role and role not in member.roles:
                try:
                    await member.add_roles(role, reason="Joined a voice channel")
                except discord.Forbidden:
                    pass
                except discord.HTTPException:
                    pass

    # User left a VC (is not in one now)
    elif before.channel is not None and after.channel is None:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT role_id FROM vcrole_config WHERE guild_id = ?', (member.guild.id,)) as cursor:
                row = await cursor.fetchone()
                
        if row:
            role = member.guild.get_role(row[0])
            if role and role in member.roles:
                try:
                    await member.remove_roles(role, reason="Left a voice channel")
                except discord.Forbidden:
                    pass
                except discord.HTTPException:
                    pass

@bot.event
async def on_command_error(ctx, error):
    # Ignore unknown commands
    if isinstance(error, commands.CommandNotFound):
        return
    
    # Permission errors - show which permissions are missing
    if isinstance(error, commands.MissingPermissions):
        missing = ', '.join(perm.replace('_', ' ').title() for perm in error.missing_permissions)
        if ctx.command:
            sig = f" {ctx.command.signature}" if ctx.command.signature else ""
            desc = (
                f"```md\n"
                f"<..> [member] | [..] [optional]\n"
                f"```\n"
                f"> `{ctx.prefix}{ctx.command.qualified_name}{sig}`\n\n"
                f"{get_emoji('arrow')} **Error :** You need: **{missing}** permissions"
            )
        else:
            desc = f"{get_emoji('arrow')} **Error :** You need: **{missing}** permissions"
            
        embed = discord.Embed(description=desc, color=THEME_COLOR)
        await ctx.send(embed=embed)
        return
    
    if isinstance(error, commands.BotMissingPermissions):
        missing = ', '.join(perm.replace('_', ' ').title() for perm in error.missing_permissions)
        if ctx.command:
            sig = f" {ctx.command.signature}" if ctx.command.signature else ""
            desc = (
                f"```md\n"
                f"<..> [member] | [..] [optional]\n"
                f"```\n"
                f"> `{ctx.prefix}{ctx.command.qualified_name}{sig}`\n\n"
                f"{get_emoji('arrow')} **Error :** The bot needs: **{missing}** permissions"
            )
        else:
            desc = f"{get_emoji('arrow')} **Error :** The bot needs: **{missing}** permissions"
            
        embed = discord.Embed(description=desc, color=THEME_COLOR)
        await ctx.send(embed=embed)
        return
    
    # Other check failures (e.g. developer-only commands)
    if isinstance(error, commands.CheckFailure):
        if ctx.command:
            sig = f" {ctx.command.signature}" if ctx.command.signature else ""
            desc = (
                f"```md\n"
                f"<..> [member] | [..] [optional]\n"
                f"```\n"
                f"> `{ctx.prefix}{ctx.command.qualified_name}{sig}`\n\n"
                f"{get_emoji('arrow')} **Error :** You don't have permission to use `{ctx.command}`"
            )
        else:
            desc = f"{get_emoji('arrow')} **Error :** You don't have permission to use this command."
            
        embed = discord.Embed(description=desc, color=THEME_COLOR)
        await ctx.send(embed=embed)
        return
    
    # Cooldown
    if isinstance(error, commands.CommandOnCooldown):
        if ctx.command:
            sig = f" {ctx.command.signature}" if ctx.command.signature else ""
            desc = (
                f"```md\n"
                f"<..> [member] | [..] [optional]\n"
                f"```\n"
                f"> `{ctx.prefix}{ctx.command.qualified_name}{sig}`\n\n"
                f"{get_emoji('arrow')} **Error :** `{ctx.command}` is on cooldown. Try again in {error.retry_after:.2f}s"
            )
        else:
            desc = f"{get_emoji('arrow')} **Error :** Command is on cooldown. Try again in {error.retry_after:.2f}s"
            
        embed = discord.Embed(description=desc, color=THEME_COLOR)
        await ctx.send(embed=embed)
        return
    
    # Other command errors
    if isinstance(error, commands.CommandError):
        if ctx.command:
            sig = f" {ctx.command.signature}" if ctx.command.signature else ""
            desc = (
                f"```md\n"
                f"<..> [member] | [..] [optional]\n"
                f"```\n"
                f"> `{ctx.prefix}{ctx.command.qualified_name}{sig}`\n\n"
                f"{get_emoji('arrow')} **Error :** {str(error)}"
            )
        else:
            desc = f"{get_emoji('arrow')} **Error :** {str(error)}"
            
        embed = discord.Embed(description=desc[:4000], color=THEME_COLOR)
        await ctx.send(embed=embed)
        return
    
    # Log unexpected errors
    print(f'[ERROR] Unhandled command error in {ctx.command}: {error}', file=sys.stderr)

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    # Blacklist check — block blacklisted users from using the bot
    if not message.author.bot:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("CREATE TABLE IF NOT EXISTS blacklists (user_id INTEGER PRIMARY KEY)")
                async with db.execute('SELECT 1 FROM blacklists WHERE user_id = ?', (message.author.id,)) as cursor:
                    if await cursor.fetchone():
                        return
        except Exception:
            pass

    if not message.author.bot:
        if message.content.lower().strip() == "developer":
            owner_id = os.getenv("VIREON_OWNER_ID")
            if owner_id and owner_id.isdigit():
                return await message.reply(f"Developer: <@{owner_id}>", mention_author=False)
            return await message.reply("Developer identity is not configured.", mention_author=False)

        async with aiosqlite.connect(DB_PATH) as db:
            # Check Autoresponders
            if message.guild:
                async with db.execute('SELECT trigger, response, match_mode FROM autoresponders WHERE guild_id = ?', (message.guild.id,)) as cursor:
                    ar_rows = await cursor.fetchall()
                msg_content_lower = message.content.lower().strip()
                for trigger, response, match_mode in ar_rows:
                    trigger_lower = trigger.lower().strip()
                    matched = False
                    mm = (match_mode or 'exact').lower().strip()
                    if mm == 'exact':
                        matched = (msg_content_lower == trigger_lower)
                    elif mm == 'startswith':
                        matched = msg_content_lower.startswith(trigger_lower)
                    elif mm == 'contains':
                        matched = (trigger_lower in msg_content_lower)
                    else:
                        matched = (msg_content_lower == trigger_lower)
                        
                    if matched:
                        try:
                            from cogs.embed import safe_format
                            response_fmt = safe_format(response, message.author)
                            await message.channel.send(response_fmt)
                            break
                        except discord.Forbidden:
                            pass

            async with db.execute('SELECT timestamp FROM afk_status WHERE user_id = ?', (message.author.id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    timestamp = row[0]
                    await db.execute('DELETE FROM afk_status WHERE user_id = ?', (message.author.id,))
                    await db.commit()
                    
                    duration_secs = int(time.time() - timestamp)
                    hours = duration_secs // 3600
                    minutes = (duration_secs % 3600) // 60
                    seconds = duration_secs % 60

                    parts = []
                    if hours > 0:
                        parts.append(f"{hours}h")
                    if minutes > 0:
                        parts.append(f"{minutes}m")
                    parts.append(f"{seconds}s")
                    duration_str = " ".join(parts)

                    embed = discord.Embed(
                        description=f"Welcome back {message.author.mention}! I've removed your AFK.",
                        color=THEME_COLOR
                    )
                    embed.add_field(name="💤 Time Spent AFK", value=f"`{duration_str}`", inline=False)
                    embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
                    embed.timestamp = discord.utils.utcnow()
                    
                    try:
                        await message.channel.send(embed=embed)
                    except discord.Forbidden:
                        pass
            
            if message.mentions:
                for user in message.mentions:
                    if user == message.author: continue
                    async with db.execute('SELECT reason, guild_id, is_global, timestamp FROM afk_status WHERE user_id = ?', (user.id,)) as cursor:
                        row = await cursor.fetchone()
                        if row:
                            reason, guild_id, is_global, timestamp = row
                            if is_global == 1 or (message.guild and guild_id == message.guild.id):
                                mins = int((time.time() - timestamp) / 60)
                                time_str = f"{mins} minutes ago" if mins > 0 else "just now"
                                try:
                                    await message.channel.send(embed=discord.Embed(description=f"💤 **{user.display_name}** went AFK {time_str} (<t:{int(timestamp)}:t>): {reason}", color=THEME_COLOR))
                                except discord.Forbidden:
                                    pass



    # --- Automod: Antispam, Antilink, Antiword & Mass Mention Protection ---
    if message.guild and not message.author.bot and message.author.id not in DEVELOPER_IDS and not message.author.guild_permissions.manage_messages:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT antispam_enabled, antilink_enabled, antiword_enabled FROM automod_config WHERE guild_id = ?', (message.guild.id,)) as cursor:
                config = await cursor.fetchone()
            if config:
                antispam_on, antilink_on, antiword_on = config

                # Check whitelist helper
                async def is_whitelisted(module):
                    async with db.execute('SELECT 1 FROM automod_whitelist WHERE guild_id = ? AND target_id = ? AND module = ?', (message.guild.id, message.author.id, module)) as c:
                        if await c.fetchone():
                            return True
                    for role in message.author.roles:
                        async with db.execute('SELECT 1 FROM automod_whitelist WHERE guild_id = ? AND target_id = ? AND module = ?', (message.guild.id, role.id, module)) as c:
                            if await c.fetchone():
                                return True
                    return False

                raw_content = message.content or ""
                norm_content = normalize_text(raw_content)
                stripped_content = re.sub(r'[\.\-_\*\s\/\\,!?@#$%\^&\(\)\+=\[\]\{\}<>|\:~`"\']', '', norm_content)

                # 1. Mass Mention Filter (>3 user/role mentions)
                total_mentions = len(message.mentions) + len(message.role_mentions)
                if total_mentions > 3 and not await is_whitelisted('antispam'):
                    try:
                        await message.delete()
                    except Exception:
                        pass
                    try:
                        await message.author.timeout(datetime.timedelta(minutes=5), reason="Automod: Mass Mention Abuse")
                    except Exception:
                        pass
                    await message.channel.send(content=message.author.mention, embed=discord.Embed(description=f"{get_emoji('error')} Mass mentions (>3) are not permitted!", color=THEME_COLOR), delete_after=5)
                    return

                # 2. Antiword Filter (zero-width & homoglyph resistant)
                if antiword_on and not await is_whitelisted('antiword'):
                    async with db.execute('SELECT word FROM automod_badwords WHERE guild_id = ?', (message.guild.id,)) as wc:
                        banned_words = [row[0].lower() for row in await wc.fetchall()]
                    
                    word_detected = False
                    for w in banned_words:
                        w_norm = normalize_text(w)
                        w_strip = re.sub(r'[\.\-_\*\s\/\\,!?@#$%\^&\(\)\+=\[\]\{\}<>|\:~`"\']', '', w_norm)
                        if w_norm in norm_content or (w_strip and w_strip in stripped_content):
                            word_detected = True
                            break

                    if word_detected:
                        if not hasattr(bot, '_automod_processing'):
                            bot._automod_processing = set()
                        am_key = (message.guild.id, message.author.id)
                        if am_key in bot._automod_processing:
                            return
                        bot._automod_processing.add(am_key)
                        bot.loop.call_later(5.0, bot._automod_processing.discard, am_key)
                        async with db.execute('SELECT punishment, mute_duration FROM automod_punishments WHERE guild_id = ? AND module = ?', (message.guild.id, 'antiword')) as pc:
                            punishments = await pc.fetchall()
                        if not punishments:
                            punishments = [('delete', 0)]
                        for p_type, p_dur in punishments:
                            if p_type == 'delete':
                                try: await message.delete()
                                except discord.HTTPException: pass
                            elif p_type == 'kick':
                                try: await message.author.kick(reason="Automod: Bad word detected")
                                except discord.HTTPException: pass
                            elif p_type == 'mute':
                                try: await message.author.timeout(datetime.timedelta(minutes=p_dur or 1), reason="Automod: Bad word detected")
                                except discord.HTTPException: pass
                            elif p_type == 'quarantine':
                                try:
                                    q_role = discord.utils.get(message.guild.roles, name="Vireon Quarantine")
                                    if not q_role:
                                        q_role = await message.guild.create_role(name="Vireon Quarantine", reason="Automod Quarantine Setup")
                                        for channel in message.guild.channels:
                                            try: await channel.set_permissions(q_role, send_messages=False, add_reactions=False, connect=False, speak=False)
                                            except discord.HTTPException: pass
                                    removable = [r for r in message.author.roles if r != message.guild.default_role and r < message.guild.me.top_role and not r.managed]
                                    if removable:
                                        role_ids = ",".join(str(r.id) for r in removable)
                                        import time; expires_at = int(time.time() + p_dur * 60) if p_dur else 0
                                        await db.execute('INSERT OR REPLACE INTO quarantined_users (guild_id, user_id, original_roles, expires_at) VALUES (?, ?, ?, ?)', (message.guild.id, message.author.id, role_ids, expires_at))
                                        await db.commit()
                                        await message.author.remove_roles(*removable, reason="Automod: Quarantine")
                                    await message.author.add_roles(q_role, reason="Automod: Quarantine")
                                except discord.HTTPException: pass

                        await message.channel.send(content=message.author.mention, embed=discord.Embed(description=f"{get_emoji('error')} Please do not use that word!", color=THEME_COLOR), delete_after=5)
                        return

                # 3. Antilink Filter (zero-width, spoiler & homoglyph resistant)
                if antilink_on and not await is_whitelisted('antilink'):
                    unspoilered = raw_content.replace("||", "")
                    norm_unspoilered = normalize_text(unspoilered)
                    if ANTILINK_REGEX.search(raw_content) or ANTILINK_REGEX.search(norm_content) or ANTILINK_REGEX.search(norm_unspoilered):
                        if not hasattr(bot, '_automod_processing'):
                            bot._automod_processing = set()
                        am_key = (message.guild.id, message.author.id)
                        if am_key in bot._automod_processing:
                            return
                        bot._automod_processing.add(am_key)
                        bot.loop.call_later(5.0, bot._automod_processing.discard, am_key)
                        async with db.execute('SELECT punishment, mute_duration FROM automod_punishments WHERE guild_id = ? AND module = ?', (message.guild.id, 'antilink')) as pc:
                            punishments = await pc.fetchall()
                        if not punishments:
                            punishments = [('delete', 0)]
                        for p_type, p_dur in punishments:
                            if p_type == 'delete':
                                try: await message.delete()
                                except discord.HTTPException: pass
                            elif p_type == 'kick':
                                try: await message.author.kick(reason="Automod: Link detected")
                                except discord.HTTPException: pass
                            elif p_type == 'mute':
                                try: await message.author.timeout(datetime.timedelta(minutes=p_dur or 1), reason="Automod: Link detected")
                                except discord.HTTPException: pass
                            elif p_type == 'quarantine':
                                try:
                                    q_role = discord.utils.get(message.guild.roles, name="Vireon Quarantine")
                                    if not q_role:
                                        q_role = await message.guild.create_role(name="Vireon Quarantine", reason="Automod Quarantine Setup")
                                        for channel in message.guild.channels:
                                            try: await channel.set_permissions(q_role, send_messages=False, add_reactions=False, connect=False, speak=False)
                                            except discord.HTTPException: pass
                                    removable = [r for r in message.author.roles if r != message.guild.default_role and r < message.guild.me.top_role and not r.managed]
                                    if removable:
                                        role_ids = ",".join(str(r.id) for r in removable)
                                        import time; expires_at = int(time.time() + p_dur * 60) if p_dur else 0
                                        await db.execute('INSERT OR REPLACE INTO quarantined_users (guild_id, user_id, original_roles, expires_at) VALUES (?, ?, ?, ?)', (message.guild.id, message.author.id, role_ids, expires_at))
                                        await db.commit()
                                        await message.author.remove_roles(*removable, reason="Automod: Quarantine")
                                    await message.author.add_roles(q_role, reason="Automod: Quarantine")
                                except discord.HTTPException: pass

                        await message.channel.send(content=message.author.mention, embed=discord.Embed(description=f"{get_emoji('error')} Links are not allowed in this server!", color=THEME_COLOR), delete_after=5)
                        return

                # 4. Antispam Filter
                if antispam_on and not await is_whitelisted('antispam'):
                    async with db.execute('SELECT spam_max_messages, spam_interval FROM automod_config WHERE guild_id = ?', (message.guild.id,)) as c2:
                        thresh = await c2.fetchone()
                    max_msgs = thresh[0] if thresh and thresh[0] else 5
                    interval = thresh[1] if thresh and thresh[1] else 5

                    if not hasattr(bot, '_spam_tracker'):
                        bot._spam_tracker = {}
                    import time as _time  # cached by Python after top-level import
                    key = (message.guild.id, message.author.id)
                    now = _time.time()
                    if key not in bot._spam_tracker:
                        bot._spam_tracker[key] = []
                    bot._spam_tracker[key] = [t for t in bot._spam_tracker[key] if now - t < interval]
                    bot._spam_tracker[key].append(now)
                    if len(bot._spam_tracker[key]) >= max_msgs:
                        bot._spam_tracker[key] = []
                        if not hasattr(bot, '_automod_processing'):
                            bot._automod_processing = set()
                        am_key = (message.guild.id, message.author.id)
                        if am_key in bot._automod_processing:
                            return
                        bot._automod_processing.add(am_key)
                        bot.loop.call_later(5.0, bot._automod_processing.discard, am_key)
                        async with db.execute('SELECT punishment, mute_duration FROM automod_punishments WHERE guild_id = ? AND module = ?', (message.guild.id, 'antispam')) as pc:
                            punishments = await pc.fetchall()
                        if not punishments:
                            punishments = [('delete', 0), ('mute', 1)]
                        for p_type, p_dur in punishments:
                            if p_type == 'delete':
                                try: await message.channel.purge(limit=max_msgs, check=lambda m: m.author == message.author)
                                except discord.HTTPException: pass
                            elif p_type == 'kick':
                                try: await message.author.kick(reason="Automod: Spam detected")
                                except discord.HTTPException: pass
                            elif p_type == 'mute':
                                try: await message.author.timeout(datetime.timedelta(minutes=p_dur or 1), reason="Automod: Spam detected")
                                except discord.HTTPException: pass
                            elif p_type == 'quarantine':
                                try:
                                    q_role = discord.utils.get(message.guild.roles, name="Vireon Quarantine")
                                    if not q_role:
                                        q_role = await message.guild.create_role(name="Vireon Quarantine", reason="Automod Quarantine Setup")
                                        for channel in message.guild.channels:
                                            try: await channel.set_permissions(q_role, send_messages=False, add_reactions=False, connect=False, speak=False)
                                            except discord.HTTPException: pass
                                    removable = [r for r in message.author.roles if r != message.guild.default_role and r < message.guild.me.top_role and not r.managed]
                                    if removable:
                                        role_ids = ",".join(str(r.id) for r in removable)
                                        import time; expires_at = int(time.time() + p_dur * 60) if p_dur else 0
                                        await db.execute('INSERT OR REPLACE INTO quarantined_users (guild_id, user_id, original_roles, expires_at) VALUES (?, ?, ?, ?)', (message.guild.id, message.author.id, role_ids, expires_at))
                                        await db.commit()
                                        await message.author.remove_roles(*removable, reason="Automod: Quarantine")
                                    await message.author.add_roles(q_role, reason="Automod: Quarantine")
                                except discord.HTTPException: pass

                        await message.channel.send(content=message.author.mention, embed=discord.Embed(description=f"{get_emoji('mute')} Punished for spamming!", color=THEME_COLOR), delete_after=10)

    # Support mention prefix for commands
    trigger = False
    bot_id = str(bot.user.id)
    original_content = message.content  # Save original before any rewriting
    if message.content.startswith(f'<@{bot_id}>') or message.content.startswith(f'<@!{bot_id}>'):
        if len(message.content) <= len(f'<@{bot_id}>') + 1 or len(message.content) <= len(f'<@!{bot_id}>') + 1:
            embed = discord.Embed(
                description=f"Hey {message.author.mention}, You have reached out to {bot.user.display_name},\n\n> My prefix is `!`\n> Use `!commands` to see my commands!\n> You can also mention me to use commands!",
                color=THEME_COLOR
            )
            embed.set_author(name=bot.user.display_name)
            embed.set_thumbnail(url=bot.user.display_avatar.url)
            embed.set_footer(text=f"Requested by {message.author.name}", icon_url=message.author.display_avatar.url)
            embed.timestamp = discord.utils.utcnow()
            
            view = discord.ui.View()
            view.add_item(discord.ui.Button(label="Invite Me!", url=f"https://discord.com/api/oauth2/authorize?client_id={bot.user.id}&permissions=8&scope=bot%20applications.commands"))
            view.add_item(discord.ui.Button(label="Support Server!", url="https://discord.gg/x8Qgq6HaF3")) # Replace with real link
            await message.channel.send(embed=embed, view=view)
            return
        trigger = True
        new_content = message.content[ len(f'<@{bot_id}>'): ].lstrip() if message.content.startswith(f'<@{bot_id}>') else message.content[ len(f'<@!{bot_id}>'): ].lstrip()
        message.content = new_content

    prefixes = await get_prefix(bot, message)

    # Command rewriting (collapsed to space-separated)
    matched_prefix = None
    if trigger:
        matched_prefix = ""
    else:
        for p in sorted(prefixes, key=len, reverse=True):
            if p and message.content.startswith(p):
                matched_prefix = p
                break
        if matched_prefix is None and "" in prefixes:
            matched_prefix = ""

    if matched_prefix is not None:
        cmd_str = message.content[len(matched_prefix):].strip()
        words = cmd_str.split()
        if words:
            first_word = words[0].lower()
            collapsed_rewrites = {
                'createrole': ('create', 'role'),
                'addalias': ('add', 'alias'),
                'addmsg': ('add', 'msg'),
                'addmessage': ('add', 'msg'),
                'removealias': ('remove', 'alias'),
                'removemsg': ('remove', 'msg'),
                'remmsg': ('remove', 'msg'),
                'removemessage': ('remove', 'msg'),
                'remmessage': ('remove', 'msg'),
                'removeidentity': ('remove', 'identity'),
                'remidentity': ('remove', 'identity'),
                'listaliases': ('list', 'aliases'),
                'delwarn': ('del', 'warn'),
                'clearwarnings': ('clear', 'warnings'),
                'setprefix': ('set', 'prefix'),
                'setidentity': ('set', 'identity'),
                'setup_automod_badge': ('setup', 'automod'),
                'resetmsg': ('reset', 'msg'),
                'resetmessage': ('reset', 'msg'),
                
                # VC
                'vckick': ('vc', 'kick'),
                'vcpull': ('vc', 'pull'),
                'vcdeafen': ('vc', 'deafen'),
                'vcmute': ('vc', 'mute'),
                'vcpullall': ('vc', 'pullall'),
                'vckickall': ('vc', 'kickall'),
                'vcdeafenall': ('vc', 'deafenall'),
                
                # Socials
                'snapchat': ('social', 'snapchat'),
                'instagram': ('social', 'instagram'),
                'twitter': ('social', 'twitter'),
                'telegram': ('social', 'telegram'),
                
                # Games
                'valorant': ('game', 'valorant'),
                'xbox': ('game', 'xbox'),
                'steam': ('game', 'steam'),
                'freefire': ('game', 'freefire'),
                'roblox': ('game', 'roblox'),
                'coc': ('game', 'coc')
            }
            if first_word in collapsed_rewrites:
                group_name, sub_name = collapsed_rewrites[first_word]
                words[0] = group_name
                words.insert(1, sub_name)
                message.content = matched_prefix + " ".join(words)
                
                if trigger:
                    guild_prefix = '!'
                    for p in prefixes:
                        if p and not p.startswith('<@'):
                            guild_prefix = p
                            break
                    message.content = guild_prefix + " ".join(words)

    await bot.process_commands(message)

    # Restore original content for alias checking (rewriting may have modified it)
    message.content = original_content

    # Dynamic role alias assign — skip if a command was already invoked
    ctx = await bot.get_context(message)
    if ctx.valid:
        return
    if message.guild and (message.author.id in DEVELOPER_IDS or message.author.guild_permissions.manage_guild):
        # Determine alias from the command context or manually parse it if there is no prefix
        alias = None
        if ctx.prefix is not None and ctx.invoked_with:
            alias = ctx.invoked_with.lower()
        elif matched_prefix is not None:
            fallback_str = message.content[len(matched_prefix):].strip()
            if fallback_str:
                alias = fallback_str.split()[0].lower()
        else:
            words = message.content.split()
            if words:
                alias = words[0].lower()

        if alias:
            role_id = await get_role_from_alias(message.guild.id, alias)
            if role_id:
                role = message.guild.get_role(role_id)
                if role:
                    if message.mentions:
                        success_list = []
                        fail_list = []
                        for user in message.mentions:
                            try:
                                if role in user.roles:
                                    await user.remove_roles(role)
                                    success_list.append((user, "removed"))
                                else:
                                    await user.add_roles(role)
                                    success_list.append((user, "added"))
                            except discord.Forbidden:
                                fail_list.append((user, "Missing permissions"))
                            except discord.HTTPException as e:
                                fail_list.append((user, str(e)))

                        success_text = "\n".join(f"> {u.mention} — **{a}**" for u, a in success_list) if success_list else "No changes made."
                        fail_text = "\n".join(f"> {u.mention} — {r}" for u, r in fail_list) if fail_list else "No users failed!"

                        embed = discord.Embed(
                            title=f"{get_emoji('roles')}  Role Toggle result:",
                            color=THEME_COLOR,
                        )
                        embed.add_field(name="Moderator:", value=message.author.mention, inline=False)
                        embed.add_field(
                            name="Details:",
                            value=(
                                f"Role: **{role.name}**\n\n"
                                f"{get_emoji('success')} **Successful Toggle** ({len(success_list)})\n{success_text}\n\n"
                                f"{get_emoji('error')} **Unsuccessful Toggle** ({len(fail_list)})\n{fail_text}"
                            ),
                            inline=False,
                        )
                        embed.timestamp = discord.utils.utcnow()
                        await message.channel.send(embed=embed)
                        if not fail_list:
                            await message.add_reaction(f"{get_emoji('success')}")
                    else:
                        embed = discord.Embed(title=f"{get_emoji('roles')}  Role Toggle result:", color=THEME_COLOR)
                        embed.add_field(name="Moderator:", value=message.author.mention, inline=False)
                        embed.add_field(name="Details:", value=f"{get_emoji('error')} Please mention user(s). Usage: `{ctx.prefix}{alias} @user`", inline=False)
                        embed.timestamp = discord.utils.utcnow()
                        await message.channel.send(embed=embed)
                else:
                    # Role was deleted from the server but still exists in aliases
                    embed = discord.Embed(title=f"{get_emoji('roles')}  Role Toggle result:", color=THEME_COLOR)
                    embed.add_field(name="Moderator:", value=message.author.mention, inline=False)
                    embed.add_field(name="Details:", value=f"{get_emoji('error')} Role no longer exists.", inline=False)
                    embed.timestamp = discord.utils.utcnow()
                    await message.channel.send(embed=embed)

HELP_MODULES = {
    "Security": {
        "emoji": get_emoji("security"),
        "description": "Antinuke & Antiraid server protection system",
        "guide": "💡 **Guide:** Protect your server against malicious admins, bot raids, and purges.",
        "commands": ["antinuke", "antinuke setup", "antinuke enable", "antinuke disable", "antinuke status", "antinuke owner", "antinuke whitelist", "antinuke panic", "antinuke recover", "antiraid", "antiraid enable", "antiraid disable", "antiraid status", "antiraid lockdown"]
    },
    "Permit Commands": {
        "emoji": get_emoji("permit_commands"),
        "description": "Owner, developer & whitelisted permit controls",
        "guide": "💡 **Guide:** Manage main owners, second owners, noprefix access, and administrative command permissions.",
        "commands": ["mainowner", "mainowner add", "mainowner remove", "secondowner", "secondowner add", "secondowner remove", "ownerlist", "ownerlist show", "perms", "noprefix", "sync"]
    },
    "Automod": {
        "emoji": get_emoji("automod"),
        "description": "Automated spam, link & word filters",
        "guide": "💡 **Guide:** Filter links, mass spam, and bad words automatically.",
        "commands": ["automod", "antispam", "antispam enable", "antispam disable", "antilink", "antilink enable", "antilink disable", "antiword", "antiword enable", "antiword disable", "automod punishment"]
    },
    "Moderation": {
        "emoji": get_emoji("moderation"),
        "description": "Server moderation and member management tools",
        "guide": "💡 **Guide:** Maintain order with warning purges, message clearing, channel locks, hiding, timeouts, kicks, and bans.",
        "commands": ["kick", "ban", "unban", "mute", "unmute", "warn", "warnings", "delwarn", "clearwarnings", "nick", "nick lock", "nick unlock", "purge", "pb", "nuke", "lock", "unlock", "hide", "unhide", "lockall", "unlockall", "hideall", "unhideall"]
    },
    "General": {
        "emoji": get_emoji("general"),
        "description": "Everyday information and general bot tools",
        "guide": "💡 **Guide:** Check latency, inspect avatars/banners, create polls, snipe deleted messages, or fetch invite links.",
        "commands": ["ping", "av", "ab", "poll", "botinvite", "reply", "serverinfo", "afk", "snipe", "esnipe", "steal"]
    },
    "Embed System": {
        "emoji": get_emoji("embed_system"),
        "description": "Interactive visual embed builder & exporter",
        "guide": "💡 **Guide:** Create, edit, preview, export, and manage custom embed configurations.",
        "commands": ["embed", "embed create", "embed edit", "embed show", "embed delete", "embed export", "embed import", "embed rename", "embed guide"]
    },
    "Utility": {
        "emoji": get_emoji("utility"),
        "description": "Useful utilities & server configuration",
        "guide": "💡 **Guide:** Manage server prefix settings, view permissions, inspect admin lists, or create short URLs.",
        "commands": ["view", "myperms", "viewperms", "botperms", "listadmins", "prefix", "prefix set", "prefix reset", "prefix show", "setprefix", "botsettings", "makeurl"]
    },
    "Automations": {
        "emoji": get_emoji("automations"),
        "description": "Custom autoresponders & autoreactions",
        "guide": "💡 **Guide:** Configure automated trigger responses and channel autoreactions.",
        "commands": ["autoresponder", "autoresponder create", "autoresponder delete", "autoresponder list", "autoresponder show", "autoreact", "autoreact add", "autoreact remove", "autoreact list"]
    },
    "Greetings": {
        "emoji": get_emoji("greetings"),
        "description": "Custom welcome & goodbye layouts",
        "guide": "💡 **Guide:** Welcome new members with custom layouts using the interactive visual Welcomer Dashboard.",
        "commands": ["welcomer", "welcomer channel", "welcomer message", "welcomer image", "welcomer embed", "welcomer test"]
    },
    "CustomRole": {
        "emoji": get_emoji("custom_roles"),
        "description": "Role management & alias controls",
        "guide": "💡 **Guide:** Assign, create, edit role colors/icons, or set up autoroles for new members.",
        "commands": ["role", "role add", "role remove", "role create", "role delete", "role colour", "role rename", "role all", "role bots", "role humans", "role icon", "role info", "role list", "createrole", "addalias", "removealias", "listaliases", "autorole"]
    },
    "Voice Commands": {
        "emoji": get_emoji("voice_commands"),
        "description": "Voice channel member moderation",
        "guide": "💡 **Guide:** Deafen, mute, kick, or pull voice members, and configure voice roles.",
        "commands": ["vckick", "vcpull", "vcmute", "vcdeafen", "vcpullall", "vckickall", "vcdeafenall", "vcrole", "vcrole add", "vcrole remove", "vcrole show"]
    },
    "Tickets": {
        "emoji": get_emoji("tickets"),
        "description": "Server support ticket system",
        "guide": "💡 **Guide:** Create and manage ticket panels, customize support roles, categories, and embeds.",
        "commands": ["ticket", "ticket embed"]
    },
    "Logging": {
        "emoji": get_emoji("logging"),
        "description": "Server activity & moderator event logging",
        "guide": "💡 **Guide:** Audit server events across 13 categories with the interactive logging control dashboard.",
        "commands": ["logging", "logging enable", "logging disable", "logging enableall", "logging disableall", "logging categories"]
    },
    "Voice Master": {
        "emoji": get_emoji("voice_master"),
        "description": "Join to create temporary voice channels",
        "guide": "💡 **Guide:** Manage temporary voice rooms. Lock, hide, rename, set limits, or transfer ownership.",
        "commands": ["j2c", "vc lock", "vc unlock", "vc hide", "vc unhide", "vc limit", "vc rename", "vc claim", "vc transfer", "vc invite", "vc ban", "vc permit"]
    },
    "Bot Settings": {
        "emoji": get_emoji("bot_settings"),
        "description": "Per-server custom bot branding & customization",
        "guide": "💡 **Guide:** Customize the bot's server-specific avatar, banner, description, and nickname.",
        "commands": ["customize", "customize avatar", "customize banner", "customize description", "customize nickname", "customize show", "customize reset"]
    },
    "Payments": {
        "emoji": get_emoji("payments"),
        "description": "UPI & Litecoin payment QR generator & profiles",
        "guide": "💡 **Guide:** Generate scannable UPI or Litecoin payment QR codes for any amount or configure payment profiles.",
        "commands": ["upi", "setupi", "serverupi", "myupi", "delupi", "ltc", "setltc", "serverltc", "myltc", "delltc"]
    },
    "Giveaway": {
        "emoji": get_emoji("giveaway"),
        "description": "Create and manage server giveaways",
        "guide": "💡 **Guide:** Host server giveaways, designate manager roles, start, edit, reroll, or end giveaways.",
        "commands": ["giveaway", "giveaway start", "giveaway end", "giveaway reroll", "giveaway edit", "giveaway list", "giveaway manager add", "giveaway manager remove"]
    },
    "Tracking": {
        "emoji": get_emoji("tracking"),
        "description": "Server stats & message volume leaderboards",
        "guide": "💡 **Guide:** Track member message counts, view top contributor leaderboards, and inspect profiles.",
        "commands": ["leaderboard", "addmsg", "removemsg", "resetmsg", "viewuser", "setidentity", "removeidentity", "adminview", "modview"]
    },
    "Social": {
        "emoji": get_emoji("social"),
        "description": "Link & showcase social & gaming profiles",
        "guide": "💡 **Guide:** Link Valorant, Snapchat, Instagram, Twitter, Telegram, Steam, Xbox, and Free Fire profiles.",
        "commands": ["valorant", "xbox", "snapchat", "instagram", "twitter", "telegram", "steam", "freefire", "roblox", "coc", "viewgames", "viewsocial"]
    },
    "Owner": {
        "emoji": get_emoji("owner"),
        "description": "Bot owner & developer management",
        "guide": "💡 **Guide:** Manage main owners, second owners, sync slash commands, and view developer controls.",
        "commands": ["mainowner", "mainowner add", "mainowner remove", "secondowner", "secondowner add", "secondowner remove", "ownerlist", "ownerlist show", "sync"]
    }
}

class HelpSelect(discord.ui.Select):
    def __init__(self, view: "HelpView"):
        options = []
        for name in view.module_names:
            data = HELP_MODULES[name]
            clean_key = name.lower().replace(" ", "_")
            emoji_obj = None
            emoji_str = get_ui_emoji(data.get("emoji")) or get_ui_emoji(clean_key) or get_ui_emoji(name.lower())
            if emoji_str:
                if isinstance(emoji_str, discord.PartialEmoji):
                    emoji_obj = emoji_str
                elif str(emoji_str).startswith("<") or str(emoji_str).startswith("<a"):
                    try:
                        emoji_obj = discord.PartialEmoji.from_str(str(emoji_str))
                    except Exception:
                        emoji_obj = discord.PartialEmoji(name="🌐")
                else:
                    emoji_obj = discord.PartialEmoji(name=str(emoji_str))
            
            options.append(discord.SelectOption(
                label=name,
                value=name,
                emoji=emoji_obj
            ))
        super().__init__(placeholder="↳ Select a module to see", min_values=1, max_values=1, options=options, row=0)
        self.help_view = view

    async def callback(self, interaction: discord.Interaction):
        module_name = self.values[0]
        if module_name in self.help_view.module_names:
            self.help_view.current_index = self.help_view.module_names.index(module_name)
        await self.help_view.update_message(interaction)


class HelpView(discord.ui.View):
    def __init__(self, ctx, bot, show_owner=False):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.bot = bot
        self.show_owner = show_owner
        
        self.module_names = [
            name for name, data in HELP_MODULES.items()
            if data.get("commands") and (name != "Owner" or self.show_owner)
        ]
        self.current_index = -1
        
        self.select = HelpSelect(self)
        self.add_item(self.select)
        
        bot_user_id = self.bot.user.id if self.bot.user else 0
        invite_link = f"https://discord.com/api/oauth2/authorize?client_id={bot_user_id}&permissions=8&scope=bot%20applications.commands" if bot_user_id else "https://discord.com"
        self.add_item(discord.ui.Button(label="Invite Me", url=invite_link, row=2, emoji="🔗"))
        self.add_item(discord.ui.Button(label="Support", url="https://tinyurl.com/Vireon-HQ", row=2, emoji="💬"))
            
        self.update_button_states()

    def update_button_states(self):
        self.first_button.disabled = (self.current_index == -1)
        self.prev_button.disabled = (self.current_index == -1)
        self.next_button.disabled = (self.current_index == len(self.module_names) - 1)
        self.last_button.disabled = (self.current_index == len(self.module_names) - 1)

    @discord.ui.button(label="<<", style=discord.ButtonStyle.secondary, row=1)
    async def first_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_index = -1
        await self.update_message(interaction)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.success, row=1)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_index > -1:
            self.current_index -= 1
        await self.update_message(interaction)

    @discord.ui.button(label="✕", style=discord.ButtonStyle.danger, row=1)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="Next", style=discord.ButtonStyle.success, row=1)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_index < len(self.module_names) - 1:
            self.current_index += 1
        await self.update_message(interaction)

    @discord.ui.button(label=">>", style=discord.ButtonStyle.secondary, row=1)
    async def last_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_index = len(self.module_names) - 1
        await self.update_message(interaction)

    def get_display_prefix(self):
        ctx_prefix = getattr(self.ctx, 'prefix', None)
        if ctx_prefix and ctx_prefix != '/':
            return ctx_prefix
        if getattr(self.ctx, 'clean_prefix', None) and self.ctx.clean_prefix != '/':
            return self.ctx.clean_prefix
        return '!'

    def get_overview_embed(self):
        try:
            # Calculate exactly the number of commands shown in the help menu
            total_commands = sum(len(module_data.get('commands', [])) for module_data in HELP_MODULES.values())
        except Exception:
            total_commands = 210

        prefix = self.get_display_prefix()
        bot_user_id = self.bot.user.id if self.bot.user else 0
        invite_link = f"https://discord.com/api/oauth2/authorize?client_id={bot_user_id}&permissions=8&scope=bot%20applications.commands" if bot_user_id else "https://discord.com"

        description = (
            f"**Hey , I'm Vireon**\n\n"
            f"• My prefix for this server is `{prefix}`\n"
            f"• Type `{prefix}help [context]` for more\n"
            f"• Total commands: `{total_commands}`\n\n"
        )
        
        cat_lines = []
        for name in self.module_names:
            data = HELP_MODULES[name]
            clean_key = name.lower().replace(" ", "_")
            emoji_val = get_emoji(clean_key) or get_emoji(name.lower()) or data.get("emoji", "🌐")
            cat_lines.append(f"  {emoji_val} » **{name}**")
            
        description += "\n".join(cat_lines)
        
        link_emoji = get_emoji("link") or "🔗"
        
        description += (
            f"\n\n**Pro Tip**\n"
            f"Explore Vireon Premium !\n\n"
            f"{link_emoji} **Links**\n"
            f"[Invite me]({invite_link}) • [Support](https://tinyurl.com/Vireon-HQ)"
        )
        
        embed = discord.Embed(
            description=description,
            color=0x9333EA
        )
        if self.bot.user and self.bot.user.display_avatar:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        return embed

    def get_module_embed(self, module_name):
        data = HELP_MODULES[module_name]
        prefix = self.get_display_prefix()
        
        guide_text = data.get("guide", "")
        lines = []
        
        for cmd_name in data["commands"]:
            cmd = self.bot.get_command(cmd_name)
            if cmd:
                brief = cmd.help or cmd.description or "No description provided."
                brief = brief.split('\n')[0].strip()
                if len(brief) > 80:
                    brief = brief[:77] + "..."
                lines.append(f"> {get_emoji('bullet')} **{prefix}{cmd_name}** — {brief}")
            else:
                lines.append(f"> {get_emoji('bullet')} **{prefix}{cmd_name}**")
        
        desc_parts = []
        if guide_text:
            desc_parts.append(f"{guide_text}\n")
        
        desc_parts.append("**Commands**")
        desc_parts.append("\n\n".join(lines))
        
        clean_key = module_name.lower().replace(" ", "_")
        emoji_val = get_emoji(clean_key) or get_emoji(module_name.lower()) or data.get("emoji", "🌐")
        title = f"{emoji_val} {module_name}"
        
        embed = discord.Embed(
            title=title,
            description="\n\n".join(desc_parts),
            color=0x9333EA
        )
        if self.bot.user and self.bot.user.display_avatar:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        return embed

    async def update_message(self, interaction: discord.Interaction):
        self.update_button_states()
        if self.current_index == -1:
            embed = self.get_overview_embed()
        else:
            embed = self.get_module_embed(self.module_names[self.current_index])
            
        await interaction.response.edit_message(embed=embed, view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.ctx.author.id:
            return True
        await interaction.response.send_message(embed=discord.Embed(description=f"{get_emoji('error')} This menu is not for you.", color=ERROR_COLOR), ephemeral=True)
        return False

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            if hasattr(self, 'message') and self.message:
                await self.message.edit(view=self)
        except (discord.NotFound, discord.HTTPException):
            pass

def is_dev_or_main_owner(author_id: int) -> bool:
    return author_id in DEVELOPER_IDS or author_id in main_owners_set or author_id in second_owners_set

async def _eval_code(ctx, code: str):
    code = code.strip()
    if code.startswith('```'):
        lines = code.split('\n')
        if lines[0].startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].startswith('```'):
            lines = lines[:-1]
        code = '\n'.join(lines)
    
    env = {
        'bot': ctx.bot,
        'ctx': ctx,
        'channel': ctx.channel,
        'author': ctx.author,
        'guild': ctx.guild,
        'message': ctx.message,
        'discord': discord,
        'asyncio': asyncio,
        'aiosqlite': aiosqlite,
        '_': getattr(ctx.bot, '_last_result', None)
    }

    body = "\n".join(f"    {line}" for line in code.split("\n"))
    func_code = f"async def _eval_func():\n{body}"

    import io, contextlib, traceback
    stdout = io.StringIO()
    try:
        exec(func_code, env)
        func = env['_eval_func']
        with contextlib.redirect_stdout(stdout):
            result = await func()
        out = stdout.getvalue()
        if result is not None:
            ctx.bot._last_result = result
            out += f"\nReturn: {result}"
        if not out.strip():
            out = "Executed successfully with no output."
    except Exception as e:
        out = f"Error: {e}\n{traceback.format_exc()}"

    if len(out) > 1900:
        out = out[:1900] + "\n... (truncated)"
    await ctx.send(f"```py\n{out}\n```")

async def _run_shell(ctx, cmd: str):
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        out = stdout.decode('utf-8', errors='ignore') + stderr.decode('utf-8', errors='ignore')
        if not out.strip():
            out = "Executed with no output."
        if len(out) > 1900:
            out = out[:1900] + "\n... (truncated)"
        await ctx.send(f"```sh\n{out}\n```")
    except Exception as e:
        await ctx.send(f"```py\nError: {e}\n```")

async def _run_sql(ctx, query: str):
    db_file = DB_PATH if os.path.exists(DB_PATH) else ('core.db' if os.path.exists('core.db') else 'bot.db')
    try:
        async with aiosqlite.connect(db_file) as db:
            async with db.execute(query) as cursor:
                if query.strip().lower().startswith(('select', 'pragma')):
                    rows = await cursor.fetchall()
                    if not rows:
                        await ctx.send("Query executed. 0 rows returned.")
                        return
                    cols = [description[0] for description in cursor.description] if cursor.description else []
                    res_str = f"Columns: {cols}\n" + "\n".join(str(row) for row in rows[:20])
                    if len(rows) > 20:
                        res_str += f"\n... ({len(rows)} rows total)"
                    if len(res_str) > 1900:
                        res_str = res_str[:1900] + "\n..."
                    await ctx.send(f"```sql\n{res_str}\n```")
                else:
                    await db.commit()
                    await ctx.send(f"Query executed. Rows affected: {cursor.rowcount}")
    except Exception as e:
        await ctx.send(f"```py\nSQL Error: {e}\n```")

async def send_dev_embed(ctx):
    prefix = ctx.prefix if ctx.prefix else "!"
    b = get_emoji('bullet')
    desc = (
        f"{get_emoji('info')} **Info & Status**\n"
        f"{b} `{prefix}dev botinfo` - Full bot statistics\n"
        f"{b} `{prefix}dev ping` - Latency breakdown\n"
        f"{b} `{prefix}dev uptime` - Bot uptime\n"
        f"{b} `{prefix}dev sys` - System info\n"
        f"{b} `{prefix}dev rtt` - Round-trip latency\n\n"

        f"{get_emoji('settings')} **Bot Control**\n"
        f"{b} `{prefix}dev restart` - Restart bot\n"
        f"{b} `{prefix}dev shutdown` - Shut down bot\n"
        f"{b} `{prefix}dev setgame <text>` - Change activity\n"
        f"{b} `{prefix}dev setstatus <status>` - Change status\n\n"

        f"{get_emoji('commands')} **Extensions**\n"
        f"{b} `{prefix}dev load <ext>` - Load a cog\n"
        f"{b} `{prefix}dev unload <ext>` - Unload a cog\n"
        f"{b} `{prefix}dev reload <ext|->` - Reload cog(s)\n"
        f"{b} `{prefix}dev listcogs` - List all cogs\n\n"

        f"{get_emoji('edit')} **Code & Shell**\n"
        f"{b} `{prefix}dev py <code>` - Execute Python\n"
        f"{b} `{prefix}dev pyi <expr>` - Inspect expression\n"
        f"{b} `{prefix}dev sh <cmd>` - Run shell command\n\n"

        f"{get_emoji('search')} **Database**\n"
        f"{b} `{prefix}dev sql <query>` - Run SQL on core.db\n"
        f"{b} `{prefix}dev dbinfo` - List all DB files\n\n"

        f"{get_emoji('rounduser')} **Guild & User Tools**\n"
        f"{b} `{prefix}dev guilds` / `{prefix}dev listguilds` - List all joined guilds\n"
        f"{b} `{prefix}dev listmembers` - Global member count\n"
        f"{b} `{prefix}dev guildinfo <id>` - Guild info\n"
        f"{b} `{prefix}dev leaveserver <id>` - Leave guild\n"
        f"{b} `{prefix}dev userinfo <id>` - User lookup\n"
        f"{b} `{prefix}dev dm <id> <msg>` - DM any user\n"
        f"{b} `{prefix}dev announce <msg>` - DM all owners\n"
        f"{b} `{prefix}dev blacklist <id>` - Blacklist user\n"
        f"{b} `{prefix}dev unblacklist <id>` - Remove blacklist\n\n"

        f"{get_emoji('noprefix')} **Automation & No-Prefix**\n"
        f"{b} `{prefix}dev addnp <user> [scope] [time]` - Add user to no-prefix list\n"
        f"{b} `{prefix}dev remnp <user> [scope]` - Remove user from no-prefix list\n"
        f"{b} `{prefix}dev listnp` - List no-prefix users\n\n"

        f"{get_emoji('utility')} **Misc**\n"
        f"{b} `{prefix}dev clearcache` - Clear cache\n"
        f"{b} `{prefix}dev tasks` - View asyncio tasks\n"
        f"{b} `{prefix}dev cancel <id>` - Cancel a task\n"
        f"{b} `{prefix}dev source <cmd>` - Show command source\n"
        f"{b} `{prefix}dev sync` - Sync slash commands\n"
        f"{b} `{prefix}dev sudo @user <cmd>` - Run cmd as user\n"
    )
    embed = discord.Embed(
        title=f"{get_emoji('owner')} Developer Command Panel",
        description=desc,
        color=THEME_COLOR
    )
    embed.set_footer(text=f"Requested by {ctx.author.display_name} • Developer Only", icon_url=ctx.author.display_avatar.url)
    await ctx.send(embed=embed)

@bot.group(name='dev', aliases=['developer', 'devjsk'], invoke_without_command=True)
async def dev_group(ctx, *, subcommand: str = None):
    if not is_dev_or_main_owner(ctx.author.id):
        return
    if subcommand:
        await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Unknown dev subcommand: `{subcommand}`\nUse `{ctx.prefix}dev` to see all available commands.", color=THEME_COLOR))
        return
    await send_dev_embed(ctx)

@dev_group.command(name='commands')
async def dev_commands(ctx):
    if not is_dev_or_main_owner(ctx.author.id):
        return
    await send_dev_embed(ctx)

@dev_group.command(name='botinfo')
async def dev_botinfo(ctx):
    if not is_dev_or_main_owner(ctx.author.id): return
    import psutil, platform
    proc = psutil.Process()
    mem = proc.memory_info().rss / 1024 / 1024
    start_time = getattr(bot, 'start_time', time.time())
    uptime_sec = int(time.time() - start_time)
    uptime_str = str(timedelta(seconds=uptime_sec))
    embed = discord.Embed(title="⚙ Bot Statistics", color=THEME_COLOR)
    embed.add_field(name="Guilds", value=str(len(bot.guilds)), inline=True)
    embed.add_field(name="Users", value=str(len(bot.users)), inline=True)
    embed.add_field(name="Ping", value=f"{round(bot.latency * 1000)}ms", inline=True)
    embed.add_field(name="Uptime", value=uptime_str, inline=True)
    embed.add_field(name="RAM Usage", value=f"{mem:.2f} MB", inline=True)
    embed.add_field(name="Python / Discord.py", value=f"{platform.python_version()} / {discord.__version__}", inline=True)
    await ctx.send(embed=embed)

@dev_group.command(name='ping')
async def dev_ping(ctx):
    if not is_dev_or_main_owner(ctx.author.id): return
    t1 = time.perf_counter()
    msg = await ctx.send("Pinging...")
    t2 = time.perf_counter()
    api_ms = round(bot.latency * 1000)
    rtt_ms = round((t2 - t1) * 1000)
    await msg.edit(content=f"🏓 **Pong!** API Latency: `{api_ms}ms` | RTT: `{rtt_ms}ms`")

@dev_group.command(name='uptime')
async def dev_uptime(ctx):
    if not is_dev_or_main_owner(ctx.author.id): return
    start_time = getattr(bot, 'start_time', time.time())
    uptime_sec = int(time.time() - start_time)
    await ctx.send(f"⏰ Bot Uptime: `{str(timedelta(seconds=uptime_sec))}`")

@dev_group.command(name='sys')
async def dev_sys(ctx):
    if not is_dev_or_main_owner(ctx.author.id): return
    import platform, psutil
    cpu_usage = psutil.cpu_percent()
    mem = psutil.virtual_memory()
    await ctx.send(f"🖥️ **System Info**\nOS: `{platform.system()} {platform.release()}`\nPython: `{platform.python_version()}`\nCPU Usage: `{cpu_usage}%` ({psutil.cpu_count()} cores)\nRAM Usage: `{mem.percent}%` ({round(mem.used/1024/1024/1024, 2)}GB / {round(mem.total/1024/1024/1024, 2)}GB)")

@dev_group.command(name='rtt')
async def dev_rtt(ctx):
    if not is_dev_or_main_owner(ctx.author.id): return
    t1 = time.perf_counter()
    msg = await ctx.send("Measuring RTT...")
    t2 = time.perf_counter()
    rtt_ms = round((t2 - t1) * 1000)
    await msg.edit(content=f"⏱️ **Round-Trip Latency:** `{rtt_ms}ms`")

@dev_group.command(name='restart')
async def dev_restart(ctx):
    if not is_dev_or_main_owner(ctx.author.id): return
    await ctx.send("🔄 Restarting bot...")
    os.execv(sys.executable, [sys.executable] + sys.argv)

@dev_group.command(name='shutdown')
async def dev_shutdown(ctx):
    if not is_dev_or_main_owner(ctx.author.id): return
    await ctx.send("🛑 Shutting down bot...")
    await bot.close()

@dev_group.command(name='setgame')
async def dev_setgame(ctx, *, text: str):
    if not is_dev_or_main_owner(ctx.author.id): return
    await bot.change_presence(activity=discord.Game(name=text))
    await ctx.send(f"🎮 Changed playing status to: `{text}`")

@dev_group.command(name='setstatus')
async def dev_setstatus(ctx, status: str):
    if not is_dev_or_main_owner(ctx.author.id): return
    s_map = {'online': discord.Status.online, 'dnd': discord.Status.dnd, 'idle': discord.Status.idle, 'invisible': discord.Status.invisible, 'offline': discord.Status.invisible}
    target = s_map.get(status.lower())
    if not target:
        await ctx.send("Valid statuses: `online`, `dnd`, `idle`, `invisible`")
        return
    await bot.change_presence(status=target)
    await ctx.send(f"🟢 Changed status to: `{status.lower()}`")

@dev_group.command(name='load')
async def dev_load(ctx, ext: str):
    if not is_dev_or_main_owner(ctx.author.id): return
    ext_name = ext if ext.startswith('cogs.') else f'cogs.{ext}'
    try:
        await bot.load_extension(ext_name)
        await ctx.send(f"<:tick:1537988932447379457> Loaded `{ext_name}`")
    except Exception as e:
        await ctx.send(f"<:cross:1537988934007529544> Failed to load `{ext_name}`: `{e}`")

@dev_group.command(name='unload')
async def dev_unload(ctx, ext: str):
    if not is_dev_or_main_owner(ctx.author.id): return
    ext_name = ext if ext.startswith('cogs.') else f'cogs.{ext}'
    try:
        await bot.unload_extension(ext_name)
        await ctx.send(f"<:tick:1537988932447379457> Unloaded `{ext_name}`")
    except Exception as e:
        await ctx.send(f"<:cross:1537988934007529544> Failed to unload `{ext_name}`: `{e}`")

@dev_group.command(name='reload')
async def dev_reload(ctx, ext: str = "-"):
    if not is_dev_or_main_owner(ctx.author.id): return
    if ext in ("-", "all"):
        loaded = list(bot.extensions.keys())
        reloaded = []
        failed = []
        for e in loaded:
            try:
                await bot.reload_extension(e)
                reloaded.append(e)
            except Exception as ex:
                failed.append(f"{e} ({ex})")
        msg = f"🔄 Reloaded {len(reloaded)} cogs."
        if failed:
            msg += f"\nFailed ({len(failed)}): " + ", ".join(failed)
        await ctx.send(msg)
    else:
        ext_name = ext if ext.startswith('cogs.') else f'cogs.{ext}'
        try:
            await bot.reload_extension(ext_name)
            await ctx.send(f"<:tick:1537988932447379457> Reloaded `{ext_name}`")
        except Exception as e:
            await ctx.send(f"<:cross:1537988934007529544> Failed to reload `{ext_name}`: `{e}`")

@dev_group.command(name='listcogs')
async def dev_listcogs(ctx):
    if not is_dev_or_main_owner(ctx.author.id): return
    loaded = list(bot.extensions.keys())
    await ctx.send(f"🧩 **Loaded Cogs ({len(loaded)}):**\n" + "\n".join(f"- `{c}`" for c in loaded))

@dev_group.command(name='py')
async def dev_py(ctx, *, code: str):
    if not is_dev_or_main_owner(ctx.author.id): return
    await _eval_code(ctx, code)

@dev_group.command(name='pyi')
async def dev_pyi(ctx, *, expr: str):
    if not is_dev_or_main_owner(ctx.author.id): return
    try:
        res = eval(expr)
        await ctx.send(f"```py\nValue: {repr(res)}\nType: {type(res)}\n```")
    except Exception as e:
        await ctx.send(f"```py\nError: {e}\n```")

@dev_group.command(name='sh')
async def dev_sh(ctx, *, cmd: str):
    if not is_dev_or_main_owner(ctx.author.id): return
    await _run_shell(ctx, cmd)

@dev_group.command(name='sql')
async def dev_sql(ctx, *, query: str):
    if not is_dev_or_main_owner(ctx.author.id): return
    await _run_sql(ctx, query)

@dev_group.command(name='dbinfo')
async def dev_dbinfo(ctx):
    if not is_dev_or_main_owner(ctx.author.id): return
    db_files = [f for f in os.listdir('.') if f.endswith('.db')]
    info = []
    for db in db_files:
        size_kb = round(os.path.getsize(db) / 1024, 2)
        info.append(f"- `{db}`: {size_kb} KB")
    await ctx.send("🗄️ **Database Files:**\n" + ("\n".join(info) if info else "No .db files found."))

@dev_group.command(name='guilds', aliases=['listguilds', 'list_guilds'])
async def dev_guilds(ctx):
    if not is_dev_or_main_owner(ctx.author.id): return
    guild_list = [f"- **{g.name}** (`{g.id}`) - `{g.member_count}` members (Owner: `{g.owner}`)" for g in bot.guilds]
    text = "\n".join(guild_list[:30])
    if len(guild_list) > 30:
        text += f"\n... and {len(guild_list) - 30} more"
    embed = discord.Embed(
        title=f"🏰 Joined Guilds List ({len(bot.guilds)})",
        description=text if text else "Bot is not in any guilds.",
        color=THEME_COLOR
    )
    embed.set_footer(text=f"Total Guilds: {len(bot.guilds)}")
    await ctx.send(embed=embed)

@dev_group.command(name='listmembers', aliases=['members', 'list_members'])
async def dev_members(ctx):
    if not is_dev_or_main_owner(ctx.author.id): return
    total_members = sum(g.member_count or 0 for g in bot.guilds)
    unique_users = len(bot.users)
    bot_users = sum(1 for u in bot.users if u.bot)
    human_users = max(0, unique_users - bot_users)
    
    embed = discord.Embed(
        title="👥 Global Member Statistics",
        color=THEME_COLOR
    )
    embed.add_field(name="Total Guild Members", value=f"`{total_members:,}`", inline=True)
    embed.add_field(name="Unique Cached Users", value=f"`{unique_users:,}`", inline=True)
    embed.add_field(name="Human Members", value=f"`{human_users:,}`", inline=True)
    embed.add_field(name="Bot Accounts", value=f"`{bot_users:,}`", inline=True)
    embed.add_field(name="Total Guilds", value=f"`{len(bot.guilds):,}`", inline=True)
    await ctx.send(embed=embed)

@dev_group.command(name='guildinfo')
async def dev_guildinfo(ctx, guild_id: int):
    if not is_dev_or_main_owner(ctx.author.id): return
    g = bot.get_guild(guild_id)
    if not g:
        await ctx.send("Guild not found.")
        return
    embed = discord.Embed(title=f"Guild Info: {g.name}", color=THEME_COLOR)
    embed.add_field(name="ID", value=str(g.id))
    embed.add_field(name="Owner", value=f"{g.owner} (`{g.owner_id}`)")
    embed.add_field(name="Members", value=str(g.member_count))
    embed.add_field(name="Channels", value=str(len(g.channels)))
    embed.add_field(name="Roles", value=str(len(g.roles)))
    embed.add_field(name="Created At", value=g.created_at.strftime("%Y-%m-%d %H:%M:%S"))
    await ctx.send(embed=embed)

@dev_group.command(name='leaveserver')
async def dev_leaveserver(ctx, guild_id: int):
    if not is_dev_or_main_owner(ctx.author.id): return
    g = bot.get_guild(guild_id)
    if not g:
        await ctx.send("Guild not found.")
        return
    await g.leave()
    await ctx.send(f"Left guild `{g.name}` (`{guild_id}`)")

@dev_group.command(name='userinfo')
async def dev_userinfo(ctx, user_id: int):
    if not is_dev_or_main_owner(ctx.author.id): return
    try:
        user = await bot.fetch_user(user_id)
    except Exception as e:
        await ctx.send(f"Could not fetch user `{user_id}`: {e}")
        return
    embed = discord.Embed(title=f"User Lookup: {user}", color=THEME_COLOR)
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="ID", value=str(user.id))
    embed.add_field(name="Bot", value=str(user.bot))
    embed.add_field(name="Created At", value=user.created_at.strftime("%Y-%m-%d %H:%M:%S"))
    await ctx.send(embed=embed)

@dev_group.command(name='dm')
async def dev_dm(ctx, user_id: int, *, message: str):
    if not is_dev_or_main_owner(ctx.author.id): return
    try:
        user = await bot.fetch_user(user_id)
        await user.send(message)
        await ctx.send(f"DM sent to `{user}` (`{user_id}`)")
    except Exception as e:
        await ctx.send(f"Failed to DM `{user_id}`: {e}")

@dev_group.command(name='announce')
async def dev_announce(ctx, *, message: str):
    if not is_dev_or_main_owner(ctx.author.id): return
    sent = 0
    for uid in main_owners_set:
        try:
            u = await bot.fetch_user(uid)
            await u.send(f"📢 **Developer Announcement:**\n{message}")
            sent += 1
        except Exception:
            pass
    await ctx.send(f"Announcement sent to {sent} main owners/devs.")

@dev_group.command(name='blacklist')
async def dev_blacklist(ctx, user_id: int):
    if not is_dev_or_main_owner(ctx.author.id): return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS blacklists (user_id INTEGER PRIMARY KEY)")
        await db.execute("INSERT OR IGNORE INTO blacklists (user_id) VALUES (?)", (user_id,))
        await db.commit()
    await ctx.send(f"🚫 Blacklisted user ID `{user_id}`")

@dev_group.command(name='unblacklist')
async def dev_unblacklist(ctx, user_id: int):
    if not is_dev_or_main_owner(ctx.author.id): return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS blacklists (user_id INTEGER PRIMARY KEY)")
        await db.execute("DELETE FROM blacklists WHERE user_id = ?", (user_id,))
        await db.commit()
    await ctx.send(f"<:tick:1537988932447379457> Removed blacklist for user ID `{user_id}`")

@dev_group.command(name='clearcache')
async def dev_clearcache(ctx):
    if not is_dev_or_main_owner(ctx.author.id): return
    import gc
    gc.collect()
    await ctx.send("🧹 Caches cleared and garbage collected.")

@dev_group.command(name='tasks')
async def dev_tasks(ctx):
    if not is_dev_or_main_owner(ctx.author.id): return
    all_tasks = asyncio.all_tasks()
    t_list = [f"- Task `{i}`: `{t.get_name()}`" for i, t in enumerate(all_tasks) if not t.done()]
    res = "\n".join(t_list[:25])
    await ctx.send(f"📋 **Active Asyncio Tasks ({len(all_tasks)}):**\n" + (res if res else "No active background tasks."))

@dev_group.command(name='cancel')
async def dev_cancel(ctx, task_id: int):
    if not is_dev_or_main_owner(ctx.author.id): return
    all_tasks = list(asyncio.all_tasks())
    if 0 <= task_id < len(all_tasks):
        all_tasks[task_id].cancel()
        await ctx.send(f"Cancelled task `{task_id}`")
    else:
        await ctx.send("Invalid task ID.")

@dev_group.command(name='source')
async def dev_source(ctx, *, command_name: str):
    if not is_dev_or_main_owner(ctx.author.id): return
    import inspect
    cmd = bot.get_command(command_name)
    if not cmd:
        await ctx.send(f"Command `{command_name}` not found.")
        return
    try:
        src = inspect.getsource(cmd.callback)
        if len(src) > 1900:
            src = src[:1900] + "\n... (truncated)"
        await ctx.send(f"```py\n{src}\n```")
    except Exception as e:
        await ctx.send(f"Could not inspect source: {e}")

@dev_group.command(name='sync')
async def dev_sync(ctx):
    if not is_dev_or_main_owner(ctx.author.id): return
    synced = await bot.tree.sync()
    await ctx.send(f"Synced {len(synced)} slash commands.")

@dev_group.command(name='sudo')
async def dev_sudo(ctx, target: discord.User, *, command_text: str):
    if not is_dev_or_main_owner(ctx.author.id): return
    import copy
    msg = copy.copy(ctx.message)
    msg.author = target
    msg.content = f"{ctx.prefix}{command_text}"
    new_ctx = await bot.get_context(msg)
    await bot.invoke(new_ctx)

@dev_group.command(name='addnp')
async def dev_addnp(ctx, user: discord.User = None, scope: str = 'server', duration: str = None):
    if not is_dev_or_main_owner(ctx.author.id): return
    np_cog = bot.get_cog('NPCommands')
    if np_cog:
        await np_cog._add_np(ctx, user, scope, duration)
    else:
        await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} NPCommands cog is not loaded.", color=THEME_COLOR))

@dev_group.command(name='remnp')
async def dev_remnp(ctx, user: discord.User = None, scope: str = 'server'):
    if not is_dev_or_main_owner(ctx.author.id): return
    np_cog = bot.get_cog('NPCommands')
    if np_cog:
        await np_cog._rem_np(ctx, user, scope)
    else:
        await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} NPCommands cog is not loaded.", color=THEME_COLOR))

@dev_group.command(name='listnp')
async def dev_listnp(ctx):
    if not is_dev_or_main_owner(ctx.author.id): return
    np_cog = bot.get_cog('NPCommands')
    if np_cog:
        await np_cog._list_np(ctx)
    else:
        await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} NPCommands cog is not loaded.", color=THEME_COLOR))

@dev_group.command(name='addpremium')
async def dev_addpremium(ctx, user: discord.User = None, scope: str = 'server', duration: str = None):
    if not is_dev_or_main_owner(ctx.author.id): return
    np_cog = bot.get_cog('NPCommands')
    if np_cog:
        await np_cog._add_premium(ctx, user, scope, duration)
    else:
        await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} NPCommands cog is not loaded.", color=THEME_COLOR))

@dev_group.command(name='rempremium')
async def dev_rempremium(ctx, user: discord.User = None):
    if not is_dev_or_main_owner(ctx.author.id): return
    np_cog = bot.get_cog('NPCommands')
    if np_cog:
        await np_cog._rem_premium(ctx, user)
    else:
        await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} NPCommands cog is not loaded.", color=THEME_COLOR))

@dev_group.command(name='listpremium')
async def dev_listpremium(ctx):
    if not is_dev_or_main_owner(ctx.author.id): return
    np_cog = bot.get_cog('NPCommands')
    if np_cog:
        await np_cog._list_premium(ctx)
    else:
        await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} NPCommands cog is not loaded.", color=THEME_COLOR))

async def is_bot_owner_or_dev(ctx):
    if getattr(ctx.author, 'id', 0) in DEVELOPER_IDS:
        return True
    if getattr(ctx.author, 'id', 0) in main_owners_set:
        return True
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("ALTER TABLE second_owners ADD COLUMN guild_id INTEGER DEFAULT 0")
        except Exception:
            pass
        async with db.execute('SELECT 1 FROM second_owners WHERE user_id = ? AND (guild_id = 0 OR guild_id = ?)', (getattr(ctx.author, 'id', 0), getattr(ctx.guild, 'id', 0))) as cursor:
            if await cursor.fetchone():
                return True
    return False

@bot.hybrid_command(name='help', aliases=['commands'])
async def help_(ctx, *, command_name: str = None):
    is_owner = await is_bot_owner_or_dev(ctx)
    if command_name:
        # Search for module or command
        command_name_title = command_name.title()
        if command_name_title in HELP_MODULES:
            if command_name_title == "Owner" and not is_owner:
                return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Command or module `{command_name}` not found.", color=0x9333EA))
                
            view = HelpView(ctx, bot, show_owner=is_owner)
            if command_name_title in view.module_names:
                view.current_index = view.module_names.index(command_name_title)
            embed = view.get_module_embed(command_name_title)
            view.message = await ctx.send(embed=embed, view=view)
            return

        cmd = bot.get_command(command_name.lower())
        if cmd:
            sig = f" {cmd.signature}" if cmd.signature else ""
            aliases_str = ", ".join(f"`{alias}`" for alias in cmd.aliases) if cmd.aliases else "`None`"
            help_text = cmd.help or cmd.description or "No description provided."
            
            desc = (
                f"```md\n"
                f"<..> [member] | [..] [optional]\n"
                f"```\n"
                f"> `{cmd.qualified_name}{sig}`\n\n"
                f"{get_emoji('arrow')} **Aliases :** {aliases_str}\n"
                f"{get_emoji('arrow')} {help_text}"
            )
            embed = discord.Embed(description=desc, color=0x9333EA)
            await ctx.send(embed=embed)
        else:
            await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Command or module `{command_name}` not found.", color=0x9333EA))
        return

    view = HelpView(ctx, bot, show_owner=is_owner)
    embed = view.get_overview_embed()
    view.message = await ctx.send(embed=embed, view=view)


@bot.hybrid_command()
async def ping(ctx):
    """Show the latency of the bot in ms."""
    latency = round(bot.latency * 1000)
    embed = discord.Embed(
        description=f"{get_emoji('ping')} **Latency :** `{latency}ms`",
        color=THEME_COLOR
    )
    embed.set_author(name="Pong!", icon_url=bot.user.display_avatar.url)
    await ctx.send(embed=embed)


@bot.hybrid_command(name="uploademojis")
async def uploademojis(ctx):
    """Upload cropped custom icons to the server and configure help menu emojis."""
    if ctx.author.id not in DEVELOPER_IDS:
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Only bot developers can run this command.", color=THEME_COLOR))

    if not ctx.guild:
        return await ctx.send(f"{get_emoji('error')} This command must be run inside a Discord server.")

    # Check for permissions to manage emojis/expressions
    perms = ctx.guild.me.guild_permissions
    has_perm = getattr(perms, 'manage_expressions', False) or getattr(perms, 'manage_emojis', False) or getattr(perms, 'manage_emojis_and_stickers', False)
    if not has_perm:
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} I need `Manage Emojis` or `Manage Expressions` permission to upload emojis.", color=THEME_COLOR))

    import os
    custom_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "custom_emojis")
    
    if not os.path.exists(custom_dir):
        os.makedirs(custom_dir)
        return await ctx.send(embed=discord.Embed(
            description=f"{get_emoji('channel_create')} I just created a folder named `custom_emojis` in my files.\n\nPlease drop your full emoji pack (.gif, .png, .jpg, .webp) into that folder.\n**Important:** Name each file what its category is in the bot (e.g. `general.gif`, `moderation.png`).\n\nOnce they are there, run `{ctx.prefix}uploademojis` again!",
            color=THEME_COLOR
        ))

    msg = await ctx.send(f"{get_emoji('settings')} **Starting bulk icon upload from `custom_emojis` folder...**")
    
    uploaded_emojis = {}
    errors = []

    files = [f for f in os.listdir(custom_dir) if f.lower().endswith(('.png', '.gif', '.jpg', '.jpeg', '.webp'))]
    if not files:
        return await msg.edit(content=None, embed=discord.Embed(description=f"{get_emoji('error')} No images or GIFs found in the `custom_emojis` folder.", color=THEME_COLOR))

    for filename in files:
        key = os.path.splitext(filename)[0].lower()
        filepath = os.path.join(custom_dir, filename)

        # Clean up emoji name (Discord requires alphanumeric and underscores, min 2 chars)
        emoji_name = f"ani_{key}"
        emoji_name = "".join(c for c in emoji_name if c.isalnum() or c == "_")
        
        # Check if emoji already exists in the server to avoid duplicates
        existing_emoji = discord.utils.get(ctx.guild.emojis, name=emoji_name)
        if existing_emoji:
            uploaded_emojis[key] = str(existing_emoji)
            continue
            
        try:
            with open(filepath, 'rb') as f:
                image_bytes = f.read()
            new_emoji = await ctx.guild.create_custom_emoji(name=emoji_name, image=image_bytes, reason="Vireon bot configuration")
            uploaded_emojis[key] = str(new_emoji)
        except Exception as e:
            errors.append(f"Failed to upload `{filename}`: {e}")

    if uploaded_emojis:
        save_emojis(uploaded_emojis)
        
    embed = discord.Embed(
        title=f"{get_emoji('upload')} Custom Emojis Uploaded & Configured",
        color=THEME_COLOR
    )
    
    desc_lines = []
    for key, emoji_str in uploaded_emojis.items():
        desc_lines.append(f"{get_emoji('bullet')} **{key.title()}** » {emoji_str}")
        
    embed.description = "\n".join(desc_lines) if desc_lines else "No emojis were uploaded."
    
    if errors:
        embed.add_field(name=f"{get_emoji('warn')} Errors/Warnings", value="\n".join(errors)[:1024], inline=False)
        
    embed.set_footer(text="Emojis saved to emojis.json successfully and help menu updated!")
    await msg.edit(content=None, embed=embed)



@bot.hybrid_command()
async def reply(ctx):
    """Reply to a message."""
    await ctx.reply(embed=discord.Embed(description="This is a reply to your message!", color=THEME_COLOR))

@bot.hybrid_command(name='botinvite', aliases=['binvite'])
async def invite(ctx):
    """Get the invite link for the bot."""
    invite_link = f"https://discord.com/api/oauth2/authorize?client_id={bot.user.id}&permissions=8&scope=bot%20applications.commands"
    embed = discord.Embed(
        title=f"{get_emoji('invite')} Invite Me!",
        description=(
            "Click the link below to invite the bot to your server with Slash Command permissions:\n\n"
            f"🔗 **[Invite Link]({invite_link})**"
        ),
        color=THEME_COLOR
    )
    embed.timestamp = discord.utils.utcnow()
    await ctx.send(embed=embed)


@bot.hybrid_command(aliases=['settings', 'botsetting'])
async def botsettings(ctx):
    """Show the Bot Settings menu."""
    embed = discord.Embed(title="Bot Settings", color=THEME_COLOR)
    
    profile_cmds = ["profile", "bio", "bio clear", "bio set", "badge", "badge list", "badge add", "badge remove"]
    embed.add_field(name="__Profile__", value=" , ".join(f"`{cmd}`" for cmd in profile_cmds), inline=False)
    
    branding_cmds = ["customize", "customize avatar", "customize banner", "customize description", "customize nickname", "customize show", "customize reset"]
    embed.add_field(name="__Branding__ 🌟 *PRO*", value=" , ".join(f"`{cmd}`" for cmd in branding_cmds), inline=False)
    
    prefix_cmds = ["prefix", "prefix set", "prefix reset", "prefix remove", "prefix show", "prefix add"]
    embed.add_field(name="__Prefix__", value=" , ".join(f"`{cmd}`" for cmd in prefix_cmds), inline=False)
    
    await ctx.send(embed=embed)

@bot.hybrid_command()
async def poll(ctx, *, question):
    """Create a simple yes/no poll."""
    embed = discord.Embed(
        title=f"{get_emoji('leaderboard')} New Poll",
        color=0x5865F2,
    )
    embed.add_field(name="Question:", value=question, inline=False)
    embed.add_field(name="Moderator:", value=ctx.author.mention, inline=False)
    embed.add_field(name="Details:", value="React below to vote!\n> 👍 — Yes\n> 👎 — No", inline=False)
    embed.timestamp = discord.utils.utcnow()
    poll_message = await ctx.send(embed=embed)
    await poll_message.add_reaction("👍")
    await poll_message.add_reaction("👎")

@bot.hybrid_command(aliases=['avatar'])
async def av(ctx, member: discord.Member = None):
    """Show a user's avatar in full size."""
    member = member or ctx.author
    avatar_url = member.display_avatar.replace(size=1024)

    # Detect format
    fmt = 'GIF' if member.display_avatar.is_animated() else 'PNG'

    embed = discord.Embed(color=THEME_COLOR)
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    embed.description = f"**{member.name}**\n{fmt}"
    embed.set_image(url=str(avatar_url))
    embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    embed.timestamp = discord.utils.utcnow()
    await ctx.send(embed=embed)

@bot.hybrid_command(aliases=['banner'])
async def ab(ctx, member: discord.Member = None):
    """Show a user's banner in full size."""
    member = member or ctx.author
    
    # We must fetch the user to get the banner, as Member objects don't cache it by default
    try:
        user = await bot.fetch_user(member.id)
    except discord.NotFound:
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} User not found.", color=THEME_COLOR))
        
    if not user.banner:
        embed = discord.Embed(description=f"{get_emoji('error')} **{member.display_name}** does not have a custom banner.", color=THEME_COLOR)
        embed.set_author(name="No Banner", icon_url=member.display_avatar.url)
        return await ctx.send(embed=embed)
        
    banner_url = user.banner.replace(size=1024)
    fmt = 'GIF' if user.banner.is_animated() else 'PNG'

    embed = discord.Embed(color=THEME_COLOR)
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    embed.description = f"**{member.name}**\n{fmt}"
    embed.set_image(url=str(banner_url))
    embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    embed.timestamp = discord.utils.utcnow()
    await ctx.send(embed=embed)

# Old give and createrole commands removed (now handled in role_system.py)

@bot.hybrid_group(name='nick', invoke_without_command=True)
@commands.has_permissions(manage_nicknames=True)
@commands.bot_has_permissions(manage_nicknames=True)
async def nick(ctx, member: discord.Member = None, *, new_nickname: str = None):
    """Change the nickname of a member."""
    if member is None:
        return await ctx.send_help(ctx.command)
        
    if member.id in DEVELOPER_IDS or is_dev_or_main_owner(member.id):
        embed = discord.Embed(description=f"u cant't do {ctx.command.name} to the developer/owner , nigga", color=THEME_COLOR)
        return await ctx.send(embed=embed)

    if not is_dev_or_main_owner(ctx.author.id) and ctx.guild.owner != ctx.author and ctx.author.top_role <= member.top_role:
        embed = discord.Embed(description=f"{get_emoji('error')} You cannot moderate {member.mention} because their highest role is equal to or higher than yours.", color=THEME_COLOR)
        embed.set_author(name="Access Denied", icon_url=ctx.author.display_avatar.url)
        return await ctx.send(embed=embed)

    if member.id == ctx.guild.owner_id:
        embed = discord.Embed(description=f"{get_emoji('error')} I cannot change the nickname of the server owner.", color=THEME_COLOR)
        embed.set_author(name="Nickname Change Failed", icon_url=ctx.author.display_avatar.url)
        return await ctx.send(embed=embed)

    if member.top_role >= ctx.guild.me.top_role:
        embed = discord.Embed(description=f"{get_emoji('error')} I cannot change the nickname of {member.mention} because their highest role is equal to or higher than mine.", color=THEME_COLOR)
        embed.set_author(name="Nickname Change Failed", icon_url=ctx.author.display_avatar.url)
        return await ctx.send(embed=embed)

    try:
        old_nick = member.display_name
        await member.edit(nick=new_nickname, reason=f"Nickname changed by {ctx.author}")
        
        embed = discord.Embed(color=THEME_COLOR)
        if new_nickname:
            embed.title = f"{get_emoji('rename')} Nickname Changed"
            embed.description = f"Successfully changed the nickname of {member.mention}."
            embed.add_field(name="Old Nickname", value=f"`{old_nick}`", inline=True)
            embed.add_field(name="New Nickname", value=f"`{new_nickname}`", inline=True)
        else:
            embed.title = f"{get_emoji('rename')} Nickname Reset"
            embed.description = f"Successfully reset the nickname of {member.mention}."
            embed.add_field(name="Old Nickname", value=f"`{old_nick}`", inline=True)
            
        embed.set_footer(text=f"Changed by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()
        await ctx.send(embed=embed)
    except discord.Forbidden:
        embed = discord.Embed(title=f"{get_emoji('error')} Nickname Change Failed", description=f"Missing permissions to manage nickname for {member.mention}.", color=THEME_COLOR)
        embed.set_footer(text=f"Attempted by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()
        await ctx.send(embed=embed)
    except discord.HTTPException as e:
        embed = discord.Embed(title=f"{get_emoji('error')} Nickname Change Failed", description=f"An error occurred: {e}", color=THEME_COLOR)
        embed.set_footer(text=f"Attempted by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()
        await ctx.send(embed=embed)

@nick.command(name="lock")
@commands.has_permissions(manage_nicknames=True)
@commands.bot_has_permissions(manage_nicknames=True)
async def nick_lock(ctx, member: discord.Member, *, nickname: str = None):
    """Lock a user's nickname so they cannot change it."""
    if member.id in DEVELOPER_IDS or is_dev_or_main_owner(member.id):
        embed = discord.Embed(description=f"u cant't do {ctx.command.name} to the developer/owner , nigga", color=THEME_COLOR)
        return await ctx.send(embed=embed)

    if not is_dev_or_main_owner(ctx.author.id) and ctx.guild.owner != ctx.author and ctx.author.top_role <= member.top_role:
        embed = discord.Embed(description=f"{get_emoji('error')} You cannot moderate {member.mention} because their highest role is equal to or higher than yours.", color=THEME_COLOR)
        embed.set_author(name="Access Denied", icon_url=ctx.author.display_avatar.url)
        return await ctx.send(embed=embed)

    if member.id == ctx.guild.owner_id:
        embed = discord.Embed(description=f"{get_emoji('error')} I cannot lock the nickname of the server owner.", color=THEME_COLOR)
        embed.set_author(name="Nickname Lock Failed", icon_url=ctx.author.display_avatar.url)
        return await ctx.send(embed=embed)

    if member.top_role >= ctx.guild.me.top_role:
        embed = discord.Embed(description=f"{get_emoji('error')} I cannot lock the nickname of {member.mention} because their highest role is equal to or higher than mine.", color=THEME_COLOR)
        embed.set_author(name="Nickname Lock Failed", icon_url=ctx.author.display_avatar.url)
        return await ctx.send(embed=embed)

    target_nick = nickname if nickname is not None else member.display_name

    try:
        if member.nick != target_nick:
            await member.edit(nick=target_nick, reason=f"Nickname locked by {ctx.author}")
    except discord.Forbidden:
        embed = discord.Embed(title=f"{get_emoji('error')} Nickname Lock Failed", description=f"Missing permissions to manage nickname for {member.mention}.", color=THEME_COLOR)
        return await ctx.send(embed=embed)
    except discord.HTTPException as e:
        embed = discord.Embed(title=f"{get_emoji('error')} Nickname Lock Failed", description=f"An error occurred: {e}", color=THEME_COLOR)
        return await ctx.send(embed=embed)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT OR REPLACE INTO locked_nicknames (guild_id, user_id, nickname) VALUES (?, ?, ?)',
            (ctx.guild.id, member.id, target_nick)
        )
        await db.commit()

    embed = discord.Embed(title=f"{get_emoji('lock')} Nickname Locked", description=f"Successfully locked {member.mention}'s nickname to `{target_nick}`.", color=THEME_COLOR)
    embed.set_footer(text=f"Locked by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    embed.timestamp = discord.utils.utcnow()
    await ctx.send(embed=embed)

@nick.command(name="unlock")
@commands.has_permissions(manage_nicknames=True)
@commands.bot_has_permissions(manage_nicknames=True)
async def nick_unlock(ctx, member: discord.Member):
    """Unlock a user's nickname."""
    if not is_dev_or_main_owner(ctx.author.id) and ctx.guild.owner != ctx.author and ctx.author.top_role <= member.top_role:
        embed = discord.Embed(description=f"{get_emoji('error')} You cannot moderate {member.mention} because their highest role is equal to or higher than yours.", color=THEME_COLOR)
        embed.set_author(name="Access Denied", icon_url=ctx.author.display_avatar.url)
        return await ctx.send(embed=embed)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM locked_nicknames WHERE guild_id = ? AND user_id = ?', (ctx.guild.id, member.id))
        await db.commit()

    embed = discord.Embed(title=f"{get_emoji('unlock')} Nickname Unlocked", description=f"Successfully unlocked {member.mention}'s nickname.", color=THEME_COLOR)
    embed.set_footer(text=f"Unlocked by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    embed.timestamp = discord.utils.utcnow()
    await ctx.send(embed=embed)

@bot.hybrid_command()
@commands.has_permissions(kick_members=True)
@commands.bot_has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    """Kick a member from the server."""
    if member.id in DEVELOPER_IDS or is_dev_or_main_owner(member.id):
        embed = discord.Embed(description=f"u cant't do {ctx.command.name} to the developer/owner , nigga", color=THEME_COLOR)
        return await ctx.send(embed=embed)

    if not is_dev_or_main_owner(ctx.author.id) and ctx.guild.owner != ctx.author and ctx.author.top_role <= member.top_role:
        embed = discord.Embed(description=f"{get_emoji('error')} You cannot moderate {member.mention} because their highest role is equal to or higher than yours.", color=THEME_COLOR)
        embed.set_author(name="Access Denied", icon_url=ctx.author.display_avatar.url)
        return await ctx.send(embed=embed)

    if member.top_role >= ctx.guild.me.top_role:
        embed = discord.Embed(description=f"{get_emoji('error')} I cannot kick {member.mention} because their highest role is equal to or higher than mine.", color=THEME_COLOR)
        embed.set_author(name="Kick Failed", icon_url=ctx.author.display_avatar.url)
        return await ctx.send(embed=embed)

    try:
        dm_sent = await send_mod_dm(member, 'kick', ctx.guild.name, ctx.author, duration=None, reason=reason)
        await member.kick(reason=f"{reason} (Kicked by {ctx.author})")
        embed = discord.Embed(
            title=f"{get_emoji('kick')} Member Kicked",
            description=f"**{member}** has been kicked from the server.",
            color=THEME_COLOR
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Reason", value=f"`{reason}`", inline=False)
        embed.add_field(name="DM Status", value=f"`{get_emoji('success') + ' Delivered' if dm_sent else get_emoji('error') + ' Failed'}`", inline=False)
        embed.set_footer(text=f"Moderated by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()
        await ctx.send(embed=embed)
    except discord.Forbidden:
        embed = discord.Embed(title=f"{get_emoji('error')} Kick Failed", description=f"Missing permissions to kick {member.mention}.", color=THEME_COLOR)
        embed.set_footer(text=f"Attempted by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()
        await ctx.send(embed=embed)
    except discord.HTTPException as e:
        embed = discord.Embed(title=f"{get_emoji('error')} Kick Failed", description=f"An error occurred: `{e}`", color=THEME_COLOR)
        embed.set_footer(text=f"Attempted by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()
        await ctx.send(embed=embed)

@bot.hybrid_command()
@commands.has_permissions(ban_members=True)
@commands.bot_has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    """Ban a member from the server."""
    if member.id in DEVELOPER_IDS or is_dev_or_main_owner(member.id):
        embed = discord.Embed(description=f"u cant't do {ctx.command.name} to the developer/owner , nigga", color=THEME_COLOR)
        return await ctx.send(embed=embed)

    if not is_dev_or_main_owner(ctx.author.id) and ctx.guild.owner != ctx.author and ctx.author.top_role <= member.top_role:
        embed = discord.Embed(description=f"{get_emoji('error')} You cannot moderate {member.mention} because their highest role is equal to or higher than yours.", color=THEME_COLOR)
        embed.set_author(name="Access Denied", icon_url=ctx.author.display_avatar.url)
        return await ctx.send(embed=embed)

    if member.top_role >= ctx.guild.me.top_role:
        embed = discord.Embed(description=f"{get_emoji('error')} I cannot ban {member.mention} because their highest role is equal to or higher than mine.", color=THEME_COLOR)
        embed.set_author(name="Ban Failed", icon_url=ctx.author.display_avatar.url)
        return await ctx.send(embed=embed)

    try:
        dm_sent = await send_mod_dm(member, 'ban', ctx.guild.name, ctx.author, duration=None, reason=reason)
        await ctx.guild.ban(member, reason=f"{reason} (Banned by {ctx.author})")
        embed = discord.Embed(
            title=f"{get_emoji('member_ban')} Member Banned",
            description=f"**{member}** has been banned from the server.",
            color=THEME_COLOR
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Reason", value=f"`{reason}`", inline=False)
        embed.add_field(name="DM Status", value=f"`{get_emoji('success') + ' Delivered' if dm_sent else get_emoji('error') + ' Failed'}`", inline=False)
        embed.set_footer(text=f"Moderated by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()
        await ctx.send(embed=embed)
    except discord.Forbidden:
        embed = discord.Embed(title=f"{get_emoji('error')} Ban Failed", description=f"Missing permissions to ban {member.mention}.", color=THEME_COLOR)
        embed.set_footer(text=f"Attempted by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()
        await ctx.send(embed=embed)
    except discord.HTTPException as e:
        embed = discord.Embed(title=f"{get_emoji('error')} Ban Failed", description=f"An error occurred: `{e}`", color=THEME_COLOR)
        embed.set_footer(text=f"Attempted by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()
        await ctx.send(embed=embed)

@bot.hybrid_command()
@commands.has_permissions(ban_members=True)
@commands.bot_has_permissions(ban_members=True)
async def unban(ctx, user: discord.User, *, reason: str = "No reason provided"):
    """Unban a user from the server."""

    try:
        await ctx.guild.unban(user, reason=f"{reason} (Unbanned by {ctx.author})")
        embed = discord.Embed(
            title=f"{get_emoji('unlock')} Member Unbanned",
            description=f"**{user}** has been unbanned from the server.",
            color=THEME_COLOR
        )
        if user.avatar:
            embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="Reason", value=f"`{reason}`", inline=False)
        embed.set_footer(text=f"Moderated by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()
        await ctx.send(embed=embed)
    except discord.Forbidden:
        embed = discord.Embed(title=f"{get_emoji('unlock')} Unban Failed", description=f"Missing permissions to unban {user.mention}.", color=THEME_COLOR)
        embed.set_footer(text=f"Attempted by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()
        await ctx.send(embed=embed)
    except discord.NotFound:
        embed = discord.Embed(title=f"{get_emoji('unlock')} Unban Failed", description=f"User is not banned.", color=THEME_COLOR)
        embed.set_footer(text=f"Attempted by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()
        await ctx.send(embed=embed)
    except discord.HTTPException as e:
        embed = discord.Embed(title=f"{get_emoji('unlock')} Unban Failed", description=f"An error occurred: `{e}`", color=THEME_COLOR)
        embed.set_footer(text=f"Attempted by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()
        await ctx.send(embed=embed)

@bot.hybrid_command()
@commands.has_permissions(moderate_members=True)
@commands.bot_has_permissions(moderate_members=True)
async def mute(ctx, member: discord.Member, time_str: str, *, reason: str = "No reason provided"):
    """Mute a member in the server (timeout)."""
    if member.id in DEVELOPER_IDS or is_dev_or_main_owner(member.id):
        embed = discord.Embed(description=f"u cant't do {ctx.command.name} to the developer/owner , nigga", color=THEME_COLOR)
        return await ctx.send(embed=embed)

    if not is_dev_or_main_owner(ctx.author.id) and ctx.guild.owner != ctx.author and ctx.author.top_role <= member.top_role:
        embed = discord.Embed(description=f"{get_emoji('error')} You cannot moderate {member.mention} because their highest role is equal to or higher than yours.", color=THEME_COLOR)
        embed.set_author(name="Access Denied", icon_url=ctx.author.display_avatar.url)
        return await ctx.send(embed=embed)

    if member.top_role >= ctx.guild.me.top_role:
        embed = discord.Embed(description=f"{get_emoji('error')} I cannot mute {member.mention} because their highest role is equal to or higher than mine.", color=THEME_COLOR)
        embed.set_author(name="Mute Failed", icon_url=ctx.author.display_avatar.url)
        return await ctx.send(embed=embed)

    td = parse_duration(time_str)
    if td is None:
        embed = discord.Embed(description=f"{get_emoji('error')} Invalid time format. Use `1s`, `10m`, `2h`, `1d`.", color=THEME_COLOR)
        embed.set_author(name="Mute Failed", icon_url=ctx.author.display_avatar.url)
        return await ctx.send(embed=embed)

    try:
        dm_sent = await send_mod_dm(member, 'mute', ctx.guild.name, ctx.author, duration=time_str, reason=reason)
        await member.timeout(discord.utils.utcnow() + td, reason=f"{reason} (Muted by {ctx.author})")
        embed = discord.Embed(
            title=f"{get_emoji('mute')} Member Muted",
            description=f"**{member}** has been muted for `{time_str}`.",
            color=THEME_COLOR
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Reason", value=f"`{reason}`", inline=False)
        embed.add_field(name="DM Status", value=f"`{get_emoji('success') + ' Delivered' if dm_sent else get_emoji('error') + ' Failed'}`", inline=False)
        embed.set_footer(text=f"Moderated by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()
        await ctx.send(embed=embed)
    except discord.Forbidden:
        embed = discord.Embed(title=f"{get_emoji('mute')} Mute Failed", description=f"Missing permissions to mute {member.mention}.", color=THEME_COLOR)
        embed.set_footer(text=f"Attempted by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()
        await ctx.send(embed=embed)
    except discord.HTTPException as e:
        embed = discord.Embed(title=f"{get_emoji('mute')} Mute Failed", description=f"An error occurred: `{e}`", color=THEME_COLOR)
        embed.set_footer(text=f"Attempted by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()
        await ctx.send(embed=embed)

@bot.hybrid_command()
@commands.has_permissions(moderate_members=True)
@commands.bot_has_permissions(moderate_members=True)
async def unmute(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    """Unmute a member in the server."""
    if member.id in DEVELOPER_IDS or is_dev_or_main_owner(member.id):
        embed = discord.Embed(description=f"u cant't do {ctx.command.name} to the developer/owner , nigga", color=THEME_COLOR)
        return await ctx.send(embed=embed)

    if not is_dev_or_main_owner(ctx.author.id) and ctx.guild.owner != ctx.author and ctx.author.top_role <= member.top_role:
        embed = discord.Embed(description=f"{get_emoji('error')} You cannot moderate {member.mention} because their highest role is equal to or higher than yours.", color=THEME_COLOR)
        embed.set_author(name="Access Denied", icon_url=ctx.author.display_avatar.url)
        return await ctx.send(embed=embed)

    if member.top_role >= ctx.guild.me.top_role:
        embed = discord.Embed(description=f"{get_emoji('error')} I cannot unmute {member.mention} because their highest role is equal to or higher than mine.", color=THEME_COLOR)
        embed.set_author(name="Unmute Failed", icon_url=ctx.author.display_avatar.url)
        return await ctx.send(embed=embed)

    try:
        await member.timeout(None, reason=f"{reason} (Unmuted by {ctx.author})")
        embed = discord.Embed(
            title=f"{get_emoji('unmute')} Member Unmuted",
            description=f"**{member}** has been unmuted.",
            color=THEME_COLOR
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Reason", value=f"`{reason}`", inline=False)
        embed.set_footer(text=f"Moderated by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()
        await ctx.send(embed=embed)
    except discord.Forbidden:
        embed = discord.Embed(title=f"{get_emoji('unmute')} Unmute Failed", description=f"Missing permissions to unmute {member.mention}.", color=THEME_COLOR)
        embed.set_footer(text=f"Attempted by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()
        await ctx.send(embed=embed)
    except discord.HTTPException as e:
        embed = discord.Embed(title=f"{get_emoji('unmute')} Unmute Failed", description=f"An error occurred: `{e}`", color=THEME_COLOR)
        embed.set_footer(text=f"Attempted by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()
        await ctx.send(embed=embed)

@bot.hybrid_command()
@commands.has_permissions(manage_messages=True)
@commands.bot_has_permissions(manage_messages=True)
async def purge(ctx, target: str = None, amount: int = None):
    """Purge messages. Usage: !purge <N>, !purge bot, !purge @user <N>"""
    if target is None:
        embed = discord.Embed(description=f"{get_emoji('error')} Missing arguments.\n\n**Usage:**\n> `!purge <number>` — Delete messages\n> `!purge bot` — Delete all bot messages\n> `!purge @user <number>` — Delete user's messages", color=THEME_COLOR)
        embed.set_author(name="Purge Failed", icon_url=ctx.author.display_avatar.url)
        msg = await ctx.send(embed=embed)
        await asyncio.sleep(5)
        try:
            await ctx.message.delete()
            await msg.delete()
        except Exception:
            pass
        return

    # --- Case 1: !purge bot ---
    if target.lower() == "bot":
        await ctx.message.delete()
        deleted = await ctx.channel.purge(limit=500, check=lambda m: m.author.bot)
        embed = discord.Embed(
            title=f"{get_emoji('delete')} Bot Messages Purged",
            description=f"Successfully deleted **{len(deleted)}** bot message(s).",
            color=THEME_COLOR
        )
        embed.set_footer(text=f"Moderated by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()
        msg = await ctx.send(embed=embed)
        await asyncio.sleep(5)
        try:
            await msg.delete()
        except Exception:
            pass
        return

    # --- Case 2: !purge @user <number> ---
    # Check if target is a user mention
    member = None
    mention_match = re.match(r'<@!?(\d+)>', target)
    if mention_match:
        member = ctx.guild.get_member(int(mention_match.group(1)))

    if member:
        if not is_dev_or_main_owner(ctx.author.id) and ctx.guild.owner != ctx.author and ctx.author.top_role <= member.top_role:
            embed = discord.Embed(description=f"{get_emoji('error')} You cannot moderate {member.mention} because their highest role is equal to or higher than yours.", color=THEME_COLOR)
            embed.set_author(name="Access Denied", icon_url=ctx.author.display_avatar.url)
            msg = await ctx.send(embed=embed)
            await asyncio.sleep(5)
            try:
                await ctx.message.delete()
                await msg.delete()
            except Exception:
                pass
            return
        
        count = amount if amount else 100
        if count < 1 or count > 500:
            embed = discord.Embed(description=f"{get_emoji('error')} Amount must be between `1` and `500`.", color=THEME_COLOR)
            embed.set_author(name="Purge Failed", icon_url=ctx.author.display_avatar.url)
            msg = await ctx.send(embed=embed)
            await asyncio.sleep(5)
            try:
                await ctx.message.delete()
                await msg.delete()
            except Exception:
                pass
            return
        await ctx.message.delete()
        deleted = await ctx.channel.purge(limit=count, check=lambda m: m.author.id == member.id)
        # Only take up to count
        actual_deleted = len(deleted)
        embed = discord.Embed(
            title=f"{get_emoji('delete')} User Messages Purged",
            description=f"Successfully deleted **{actual_deleted}** message(s) from **{member}**.",
            color=THEME_COLOR
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Moderated by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()
        msg = await ctx.send(embed=embed)
        await asyncio.sleep(5)
        try:
            await msg.delete()
        except Exception:
            pass
        return

    # --- Case 3: !purge <number> ---
    try:
        count = int(target)
    except ValueError:
        embed = discord.Embed(description=f"{get_emoji('error')} `{target}` is not a valid number or user.", color=THEME_COLOR)
        embed.set_author(name="Purge Failed", icon_url=ctx.author.display_avatar.url)
        msg = await ctx.send(embed=embed)
        await asyncio.sleep(5)
        try:
            await ctx.message.delete()
            await msg.delete()
        except Exception:
            pass
        return

    if count < 1 or count > 500:
        embed = discord.Embed(description=f"{get_emoji('error')} Amount must be between `1` and `500`.", color=THEME_COLOR)
        embed.set_author(name="Purge Failed", icon_url=ctx.author.display_avatar.url)
        msg = await ctx.send(embed=embed)
        await asyncio.sleep(5)
        try:
            await ctx.message.delete()
            await msg.delete()
        except Exception:
            pass
        return

    await ctx.message.delete()
    deleted = await ctx.channel.purge(limit=count)
    embed = discord.Embed(
        title=f"{get_emoji('delete')} Messages Purged",
        description=f"Successfully deleted **{len(deleted)}** message(s).",
        color=THEME_COLOR
    )
    embed.set_footer(text=f"Moderated by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    embed.timestamp = discord.utils.utcnow()
    msg = await ctx.send(embed=embed)
    await asyncio.sleep(5)
    try:
        await msg.delete()
    except Exception:
        pass

class ConfirmationView(discord.ui.View):
    def __init__(self, author: discord.Member | discord.User):
        super().__init__(timeout=30)
        self.author = author
        self.value = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(f"{get_emoji('error')} This confirmation is not for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        self.stop()
        try:
            await interaction.response.defer()
        except discord.InteractionResponded:
            pass

    @discord.ui.button(label="No", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        self.stop()
        try:
            await interaction.response.defer()
        except discord.InteractionResponded:
            pass

@bot.hybrid_command()
@commands.has_permissions(manage_channels=True)
async def nuke(ctx):
    """Nuke the current channel."""
    embed_confirm = discord.Embed(
        title=f"{get_emoji('warn')} Confirmation Required",
        description="Are you sure you want to **nuke** this channel? This will delete the current channel and clone it, removing all message history.",
        color=0xFFCC00
    )
    view = ConfirmationView(ctx.author)
    msg_confirm = await ctx.send(embed=embed_confirm, view=view)
    
    await view.wait()
    
    if view.value is not True:
        try:
            await msg_confirm.delete()
        except Exception:
            pass
        if view.value is False:
            await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Nuke command cancelled.", color=THEME_COLOR), delete_after=5)
        else:
            await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Confirmation timed out.", color=THEME_COLOR), delete_after=5)
        return

    try:
        await msg_confirm.delete()
    except Exception:
        pass

    channel = ctx.channel
    try:
        new_channel = await channel.clone(reason=f"Channel nuked by {ctx.author}")
        await new_channel.edit(position=channel.position)
        await channel.delete()
        
        embed = discord.Embed(
            title=f"{get_emoji('nuke')} Channel Nuked",
            description=f"This channel was nuked by {ctx.author.mention}",
            color=THEME_COLOR
        )
        embed.set_image(url="https://media.tenor.com/giqbNnE9nC4AAAAC/explosion-nuke.gif")
        embed.set_footer(text=f"Moderated by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()
        await new_channel.send(embed=embed)
    except discord.Forbidden:
        await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} I don't have permission to manage channels.", color=THEME_COLOR))
    except discord.HTTPException as e:
        await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Failed to nuke channel: {e}", color=THEME_COLOR))



async def change_prefix_logic(ctx, prefix: str):
    if len(prefix) > 5:
        embed = discord.Embed(description=f"{get_emoji('error')} Prefix cannot be longer than 5 characters.", color=THEME_COLOR)
        return await ctx.send(embed=embed)
        
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('INSERT OR REPLACE INTO guild_prefixes (guild_id, prefix) VALUES (?, ?)', (ctx.guild.id, prefix))
        await db.commit()
        
    embed = discord.Embed(description=f"{get_emoji('success')} Custom prefix set to `{prefix}` for this server.", color=THEME_COLOR)
    await ctx.send(embed=embed)

@bot.hybrid_group(name="prefix", invoke_without_command=True)
async def prefix_group(ctx):
    """View or manage the server's prefix."""
    current_prefix = '!'
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT prefix FROM guild_prefixes WHERE guild_id = ?', (ctx.guild.id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                current_prefix = row[0]
    embed = discord.Embed(description=f"My prefix for this server is `{current_prefix}`.\nUse `{current_prefix}prefix set <prefix>` to change it.", color=THEME_COLOR)
    await ctx.send(embed=embed)

@prefix_group.command(name="set")
@commands.has_permissions(manage_guild=True)
async def prefix_set(ctx, prefix: str):
    """Set a custom prefix for this server."""
    await change_prefix_logic(ctx, prefix)

@prefix_group.command(name="reset")
@commands.has_permissions(manage_guild=True)
async def prefix_reset(ctx):
    """Reset the server's prefix back to '!'."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM guild_prefixes WHERE guild_id = ?', (ctx.guild.id,))
        await db.commit()
    embed = discord.Embed(description=f"{get_emoji('success')} Prefix has been reset to `!` for this server.", color=THEME_COLOR)
    await ctx.send(embed=embed)

@bot.hybrid_group(name="set", invoke_without_command=True)
async def set_group(ctx):
    """Settings configuration group."""
    await ctx.send_help(ctx.command)
    
@set_group.command(name="prefix")
@commands.has_permissions(manage_guild=True)
async def set_prefix(ctx, prefix: str):
    """Set a custom prefix for this server."""
    await change_prefix_logic(ctx, prefix)

@bot.hybrid_command(name="setprefix")
@commands.has_permissions(manage_guild=True)
async def setprefix(ctx, prefix: str):
    """Set a custom prefix for this server."""
    await change_prefix_logic(ctx, prefix)
@bot.hybrid_command(name="sync")
async def sync_tree(ctx, spec: str = None):
    """Sync bot slash commands (Owner only)"""
    if not await is_bot_owner_or_dev(ctx):
        await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Only owners can use this command.", color=THEME_COLOR), ephemeral=True)
        return

    if spec == "guild":
        bot.tree.copy_global_to(guild=ctx.guild)
        synced = await bot.tree.sync(guild=ctx.guild)
        await ctx.send(embed=discord.Embed(description=f"{get_emoji('success')} Synced {len(synced)} commands to this guild.", color=THEME_COLOR))
    elif spec == "clear":
        bot.tree.clear_commands(guild=ctx.guild)
        await bot.tree.sync(guild=ctx.guild)
        await ctx.send(embed=discord.Embed(description=f"{get_emoji('success')} Cleared guild commands.", color=THEME_COLOR))
    else:
        synced = await bot.tree.sync()
        await ctx.send(embed=discord.Embed(description=f"{get_emoji('success')} Synced {len(synced)} commands globally.", color=THEME_COLOR))

@bot.hybrid_group(invoke_without_command=True)
async def mainowner(ctx):
    """Manage Main Owners (Owner only)."""
    if not is_dev_or_main_owner(ctx.author.id):
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Only Main Owners can use this command.", color=THEME_COLOR))
    await ctx.send_help(ctx.command)

@mainowner.command(name="add")
async def mainowner_add(ctx, user: discord.User):
    """Add a user to the Main Owners list."""
    if not is_dev_or_main_owner(ctx.author.id):
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Only Main Owners can add Main Owners.", color=THEME_COLOR))
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('INSERT OR IGNORE INTO main_owners (user_id) VALUES (?)', (user.id,))
        await db.commit()
    
    main_owners_set.add(user.id)
    embed = discord.Embed(description=f"{get_emoji('success')} {user.mention} has been added as a **Main Owner**.", color=THEME_COLOR)
    await ctx.send(embed=embed)

@mainowner.command(name="remove")
async def mainowner_remove(ctx, user: discord.User):
    """Remove a user from the Main Owners list."""
    if not is_dev_or_main_owner(ctx.author.id):
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Only Main Owners can remove Main Owners.", color=THEME_COLOR))
    if user.id in DEVELOPER_IDS:
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Cannot remove a Developer from Main Owners.", color=THEME_COLOR))
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM main_owners WHERE user_id = ?', (user.id,))
        await db.commit()
    
    main_owners_set.discard(user.id)
    embed = discord.Embed(description=f"{get_emoji('success')} {user.mention} has been removed from **Main Owners**.", color=THEME_COLOR)
    await ctx.send(embed=embed)

@bot.hybrid_group(invoke_without_command=True)
async def secondowner(ctx):
    """Manage Second Owners (Owner only)."""
    if not is_dev_or_main_owner(ctx.author.id):
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Only Main Owners can use this command.", color=THEME_COLOR))
    await ctx.send_help(ctx.command)

@secondowner.command(name="add")
async def secondowner_add(ctx, user: discord.User):
    """Add a user to the Second Owners list."""
    if not is_dev_or_main_owner(ctx.author.id):
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Only Main Owners can add Second Owners.", color=THEME_COLOR))
    
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("ALTER TABLE second_owners ADD COLUMN guild_id INTEGER DEFAULT 0")
        except Exception:
            pass
        await db.execute('INSERT OR IGNORE INTO second_owners (user_id, guild_id) VALUES (?, 0)', (user.id,))
        await db.commit()
    second_owners_set.add(user.id)
        
    embed = discord.Embed(description=f"{get_emoji('success')} {user.mention} has been added as a **Second Owner**.", color=THEME_COLOR)
    await ctx.send(embed=embed)

@secondowner.command(name="remove")
async def secondowner_remove(ctx, user: discord.User):
    """Remove a user from the Second Owners list."""
    if not is_dev_or_main_owner(ctx.author.id):
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Only Main Owners can remove Second Owners.", color=THEME_COLOR))
    
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("ALTER TABLE second_owners ADD COLUMN guild_id INTEGER DEFAULT 0")
        except Exception:
            pass
        await db.execute('DELETE FROM second_owners WHERE user_id = ? AND guild_id = 0', (user.id,))
        await db.commit()
    second_owners_set.discard(user.id)
        
    embed = discord.Embed(description=f"{get_emoji('success')} {user.mention} has been removed from **Second Owners**.", color=THEME_COLOR)
    await ctx.send(embed=embed)

@bot.hybrid_group(invoke_without_command=True)
async def ownerlist(ctx):
    """View the ownerlist."""
    if not await is_bot_owner_or_dev(ctx):
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Only Owners can use this command.", color=THEME_COLOR))
    await ctx.send_help(ctx.command)

@ownerlist.command(name="show")
async def ownerlist_show(ctx):
    """Show the list of owners."""
    if not await is_bot_owner_or_dev(ctx):
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Only Owners can use this command.", color=THEME_COLOR))
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT user_id FROM second_owners') as cursor:
            second_owners = await cursor.fetchall()
    
    embed = discord.Embed(title=f"{get_emoji('owner')} Owner List", color=THEME_COLOR)
    
    # Fetch main owners from DB + configured VIREON_OWNER_ID
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT user_id FROM main_owners') as cursor:
            db_main_owners = await cursor.fetchall()
    main_owners_ids = set(DEVELOPER_IDS)
    for (uid,) in db_main_owners:
        main_owners_ids.add(uid)
    main_owners = list(main_owners_ids)
        
    main_owners_text = ""
    for i, user_id in enumerate(main_owners, 1):
        main_owners_text += f"{i}. <@{user_id}>\n"
    if not main_owners_text:
        main_owners_text = "None"
    embed.add_field(name="Main owners -", value=main_owners_text, inline=False)
    
    second_owners_text = ""
    for i, (user_id,) in enumerate(second_owners, 1):
        second_owners_text += f"{i}. <@{user_id}>\n"
        if i >= 5:
            break
    if not second_owners_text:
        second_owners_text = "None"
    embed.add_field(name="Second owner", value=second_owners_text, inline=False)
    
    embed.timestamp = discord.utils.utcnow()
    await ctx.send(embed=embed)


# ─── Giveaway System ─────────────────────────────────────────────────────────

class GiveawayJoinButton(discord.ui.View):
    def __init__(self, message_id: int):
        super().__init__(timeout=None)
        self.message_id = message_id

    @discord.ui.button(label="0", emoji=get_ui_emoji("giveaway"), style=discord.ButtonStyle.blurple, custom_id="giveaway_join")
    async def join_giveaway(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT participants, ended FROM giveaways WHERE message_id = ?', (self.message_id,)) as cursor:
                row = await cursor.fetchone()
            if not row:
                return await interaction.response.send_message(f"{get_emoji('error')} Giveaway not found.", ephemeral=True)
            import json
            participants = json.loads(row[0])
            ended = row[1]
            if ended:
                return await interaction.response.send_message(f"{get_emoji('error')} This giveaway has already ended.", ephemeral=True)
            uid = interaction.user.id
            if uid in participants:
                participants.remove(uid)
                action_msg = "You have **left** the giveaway."
            else:
                participants.append(uid)
                action_msg = f"You have **entered** the giveaway! {get_emoji('giveaway')}"
            await db.execute('UPDATE giveaways SET participants = ? WHERE message_id = ?', (json.dumps(participants), self.message_id))
            await db.commit()
        button.label = str(len(participants))
        await interaction.response.send_message(action_msg, ephemeral=True)
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

async def is_giveaway_manager(ctx):
    """Check if the user has manage_guild permission or a giveaway manager role."""
    if getattr(ctx.author, 'id', 0) in main_owners_set:
        return True
    if hasattr(ctx.author, 'guild_permissions') and (getattr(ctx.author.guild_permissions, 'manage_guild', False) or getattr(ctx.author.guild_permissions, 'administrator', False)):
        return True
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT role_id FROM giveaway_managers WHERE guild_id = ?', (ctx.guild.id,)) as cursor:
            rows = await cursor.fetchall()
    manager_role_ids = {r[0] for r in rows}
    if hasattr(ctx.author, 'roles'):
        for role in ctx.author.roles:
            if role.id in manager_role_ids:
                return True
    raise commands.MissingPermissions(['manage_guild'])

@bot.hybrid_group(name="giveaway", aliases=["g"], invoke_without_command=True)
@commands.check(is_giveaway_manager)
async def giveaway_cmd(ctx):
    """Manage giveaways in your server."""
    await send_group_suggestions(ctx)

@giveaway_cmd.command(name="start")
@commands.check(is_giveaway_manager)
async def giveaway_start(ctx, duration: str, winners: int, *, prize: str):
    """Start a giveaway. Duration: 1m, 1h, 1d. Example: !giveaway start 1h 1 Nitro"""
    import json
    # Parse duration
    time_units = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}
    unit = duration[-1].lower()
    if unit not in time_units or not duration[:-1].isdigit():
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Invalid duration. Use `1m`, `1h`, `1d`, etc.", color=THEME_COLOR))
    seconds = int(duration[:-1]) * time_units[unit]
    if seconds < 10 or seconds > 2592000:  # max 30 days
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Duration must be between 10 seconds and 30 days.", color=THEME_COLOR))
    if winners < 1 or winners > 20:
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Winners must be between 1 and 20.", color=THEME_COLOR))

    # Confirmation step
    embed_confirm = discord.Embed(
        title=f"{get_emoji('warn')} Confirmation Required",
        description=f"Are you sure you want to start a giveaway for **{prize}** with `{winners}` winner(s) lasting `{duration}`?",
        color=0xFFCC00
    )
    view_confirm = ConfirmationView(ctx.author)
    msg_confirm = await ctx.send(embed=embed_confirm, view=view_confirm)
    
    await view_confirm.wait()
    
    if view_confirm.value is not True:
        try:
            await msg_confirm.delete()
        except Exception:
            pass
        if view_confirm.value is False:
            await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Giveaway creation cancelled.", color=THEME_COLOR), delete_after=5)
        else:
            await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Confirmation timed out.", color=THEME_COLOR), delete_after=5)
        return

    try:
        await msg_confirm.delete()
    except Exception:
        pass

    end_time = discord.utils.utcnow() + timedelta(seconds=seconds)
    end_ts = int(end_time.timestamp())

    embed = discord.Embed(title=f"{get_emoji('giveaway')} GIVEAWAY {get_emoji('giveaway')}", description=f"**{prize}**", color=THEME_COLOR)
    embed.add_field(name="Ends", value=f"<t:{end_ts}:R> (<t:{end_ts}:f>)", inline=False)
    embed.add_field(name="Hosted by", value=ctx.author.mention, inline=True)
    embed.add_field(name="Winners", value=f"`{winners}`", inline=True)
    embed.set_footer(text=f"Ends at")
    embed.timestamp = end_time

    view = GiveawayJoinButton(0)  # placeholder, will update after sending
    msg = await ctx.send(embed=embed, view=view)

    # Update view with actual message ID and persist
    view.message_id = msg.id
    view.children[0].custom_id = f"giveaway_join_{msg.id}"
    await msg.edit(view=view)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT INTO giveaways (message_id, channel_id, guild_id, host_id, prize, winners, end_time, participants) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (msg.id, ctx.channel.id, ctx.guild.id, ctx.author.id, prize, winners, end_time.isoformat(), json.dumps([]))
        )
        await db.commit()

    # Schedule auto-end
    async def auto_end_giveaway():
        await asyncio.sleep(seconds)
        await _end_giveaway(msg.id)

    asyncio.create_task(auto_end_giveaway())

@giveaway_cmd.command(name="end")
@commands.check(is_giveaway_manager)
async def giveaway_end(ctx, messageid: str):
    """End a giveaway early by its message ID."""
    try:
        mid = int(messageid)
    except ValueError:
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Invalid message ID.", color=THEME_COLOR))
    result = await _end_giveaway(mid)
    if result:
        await ctx.send(embed=discord.Embed(description=f"{get_emoji('success')} Giveaway ended!", color=THEME_COLOR))
    else:
        await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Giveaway not found or already ended.", color=THEME_COLOR))

@giveaway_cmd.command(name="reroll")
@commands.check(is_giveaway_manager)
async def giveaway_reroll(ctx, messageid: str):
    """Reroll winners for a completed giveaway."""
    import json
    try:
        mid = int(messageid)
    except ValueError:
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Invalid message ID.", color=THEME_COLOR))
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT channel_id, prize, winners, participants, ended, guild_id FROM giveaways WHERE message_id = ?', (mid,)) as cursor:
            row = await cursor.fetchone()
    if not row:
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Giveaway not found.", color=THEME_COLOR))
    if not row[4]:
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Giveaway hasn't ended yet. Use `giveaway end` first.", color=THEME_COLOR))

    participants = json.loads(row[3])
    winner_count = row[2]
    prize = row[1]
    channel_id = row[0]
    guild_id = row[5]
    if not participants:
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} No participants to reroll from.", color=THEME_COLOR))

    winners = random.sample(participants, min(winner_count, len(participants)))
    winners_text = ", ".join(f"<@{w}>" for w in winners)
    embed = discord.Embed(title=f"{get_emoji('giveaway')} Giveaway Rerolled!", color=THEME_COLOR)
    embed.add_field(name="Prize", value=f"**{prize}**", inline=False)
    embed.add_field(name="New Winner(s)", value=winners_text, inline=False)
    await ctx.send(embed=embed)

    guild_name = ctx.guild.name if ctx.guild else "Server"
    jump_url = f"https://discord.com/channels/{guild_id}/{channel_id}/{mid}"
    dm_embed = discord.Embed(
        title=f"{get_emoji('giveaway')} You Won a Giveaway Reroll! {get_emoji('giveaway')}",
        description=f"Congratulations! You won the rerolled giveaway for **{prize}** in **{guild_name}**!",
        color=THEME_COLOR
    )
    dm_embed.add_field(name="Prize", value=f"**{prize}**", inline=True)
    dm_embed.add_field(name="Server", value=f"**{guild_name}**", inline=True)
    dm_embed.add_field(name="Giveaway", value=f"[Jump to Giveaway Message]({jump_url})", inline=False)
    dm_embed.set_footer(text="Thank you for participating!")
    dm_embed.timestamp = discord.utils.utcnow()

    for w_id in winners:
        try:
            user = bot.get_user(w_id) or await bot.fetch_user(w_id)
            if user:
                await user.send(embed=dm_embed)
        except Exception:
            pass

@giveaway_cmd.command(name="edit")
@commands.check(is_giveaway_manager)
async def giveaway_edit(ctx, messageid: str, *, prize: str):
    """Edit the prize of an active giveaway."""
    try:
        mid = int(messageid)
    except ValueError:
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Invalid message ID.", color=THEME_COLOR))
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT channel_id, ended, end_time, winners, host_id FROM giveaways WHERE message_id = ?', (mid,)) as cursor:
            row = await cursor.fetchone()
        if not row:
            return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Giveaway not found.", color=THEME_COLOR))
        if row[1]:
            return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Cannot edit an ended giveaway.", color=THEME_COLOR))
        await db.execute('UPDATE giveaways SET prize = ? WHERE message_id = ?', (prize, mid))
        await db.commit()

    # Update the embed on the original message
    try:
        channel = bot.get_channel(row[0])
        if channel:
            msg = await channel.fetch_message(mid)
            embed = msg.embeds[0] if msg.embeds else discord.Embed(title=f"{get_emoji('giveaway')} GIVEAWAY {get_emoji('giveaway')}", color=THEME_COLOR)
            embed.description = f"**{prize}**"
            await msg.edit(embed=embed)
    except Exception:
        pass
    await ctx.send(embed=discord.Embed(description=f"{get_emoji('success')} Giveaway prize updated to **{prize}**.", color=THEME_COLOR))

@giveaway_cmd.command(name="list")
@commands.check(is_giveaway_manager)
async def giveaway_list(ctx):
    """List all active giveaways in this server."""
    import json
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT message_id, channel_id, prize, winners, end_time, participants FROM giveaways WHERE guild_id = ? AND ended = 0', (ctx.guild.id,)) as cursor:
            rows = await cursor.fetchall()
    if not rows:
        return await ctx.send(embed=discord.Embed(description="No active giveaways in this server.", color=THEME_COLOR))

    embed = discord.Embed(title=f"{get_emoji('giveaway')} Active Giveaways", color=THEME_COLOR)
    for mid, cid, prize, winners, end_time, participants in rows:
        end_dt = datetime.datetime.fromisoformat(end_time)
        end_ts = int(end_dt.timestamp())
        p_count = len(json.loads(participants))
        embed.add_field(
            name=f"{prize}",
            value=f"Channel: <#{cid}>\nWinners: `{winners}` | Entries: `{p_count}`\nEnds: <t:{end_ts}:R>\n[Jump](https://discord.com/channels/{ctx.guild.id}/{cid}/{mid})",
            inline=False
        )
    embed.timestamp = discord.utils.utcnow()
    await ctx.send(embed=embed)

# ─── Giveaway Manager Subgroup ───────────────────────────────────────────────

@giveaway_cmd.group(name="manager", invoke_without_command=True)
@commands.has_permissions(manage_guild=True)
async def giveaway_manager(ctx):
    """Manage giveaway manager roles."""
    await ctx.send_help(ctx.command)

@giveaway_manager.command(name="add")
@commands.has_permissions(manage_guild=True)
async def giveaway_manager_add(ctx, role: discord.Role):
    """Add a role as a giveaway manager."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('INSERT OR IGNORE INTO giveaway_managers (guild_id, role_id) VALUES (?, ?)', (ctx.guild.id, role.id))
        await db.commit()
    await ctx.send(embed=discord.Embed(description=f"{get_emoji('success')} {role.mention} has been added as a **Giveaway Manager** role.", color=THEME_COLOR))

@giveaway_manager.command(name="remove")
@commands.has_permissions(manage_guild=True)
async def giveaway_manager_remove(ctx, role: discord.Role):
    """Remove a role from giveaway managers."""
    async with aiosqlite.connect(DB_PATH) as db:
        result = await db.execute('DELETE FROM giveaway_managers WHERE guild_id = ? AND role_id = ?', (ctx.guild.id, role.id))
        await db.commit()
        if result.rowcount == 0:
            return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} That role is not a giveaway manager.", color=THEME_COLOR))
    await ctx.send(embed=discord.Embed(description=f"{get_emoji('success')} {role.mention} has been removed from **Giveaway Managers**.", color=THEME_COLOR))

@giveaway_manager.command(name="list")
@commands.has_permissions(manage_guild=True)
async def giveaway_manager_list(ctx):
    """List all giveaway manager roles."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT role_id FROM giveaway_managers WHERE guild_id = ?', (ctx.guild.id,)) as cursor:
            rows = await cursor.fetchall()
    if not rows:
        return await ctx.send(embed=discord.Embed(description="No giveaway manager roles set.", color=THEME_COLOR))
    
    roles_text = "\n".join(f"{i}. <@&{r[0]}>" for i, r in enumerate(rows, 1))
    embed = discord.Embed(title=f"{get_emoji('giveaway')} Giveaway Manager Roles", description=roles_text, color=THEME_COLOR)
    embed.timestamp = discord.utils.utcnow()
    await ctx.send(embed=embed)

async def _end_giveaway(message_id: int) -> bool:
    """Internal helper to end a giveaway and announce winners."""
    import json
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT channel_id, guild_id, host_id, prize, winners, participants, ended FROM giveaways WHERE message_id = ?', (message_id,)) as cursor:
            row = await cursor.fetchone()
        if not row or row[6]:
            return False
        channel_id, guild_id, host_id, prize, winner_count, participants_json, _ = row
        await db.execute('UPDATE giveaways SET ended = 1 WHERE message_id = ?', (message_id,))
        await db.commit()

    participants = json.loads(participants_json)

    channel = bot.get_channel(channel_id)
    if not channel:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception:
            channel = None

    guild = bot.get_guild(guild_id)
    if not guild and channel and hasattr(channel, 'guild'):
        guild = channel.guild
    if not guild:
        try:
            guild = await bot.fetch_guild(guild_id)
        except Exception:
            guild = None

    guild_name = guild.name if guild else "Server"

    if not participants:
        embed = discord.Embed(title=f"{get_emoji('giveaway')} GIVEAWAY ENDED {get_emoji('giveaway')}", description=f"**{prize}**", color=THEME_COLOR)
        embed.add_field(name="Winner(s)", value="No valid entries.", inline=False)
        embed.add_field(name="Hosted by", value=f"<@{host_id}>", inline=True)
        embed.set_footer(text="Ended")
        embed.timestamp = discord.utils.utcnow()
        if channel:
            try:
                msg = await channel.fetch_message(message_id)
                await msg.edit(embed=embed, view=None)
            except Exception:
                pass
        return True

    winners = random.sample(participants, min(winner_count, len(participants)))
    winners_text = ", ".join(f"<@{w}>" for w in winners)
    embed = discord.Embed(title=f"{get_emoji('giveaway')} GIVEAWAY ENDED {get_emoji('giveaway')}", description=f"**{prize}**", color=THEME_COLOR)
    embed.add_field(name="Winner(s)", value=winners_text, inline=False)
    embed.add_field(name="Hosted by", value=f"<@{host_id}>", inline=True)
    embed.set_footer(text="Ended")
    embed.timestamp = discord.utils.utcnow()

    if channel:
        try:
            msg = await channel.fetch_message(message_id)
            await msg.edit(embed=embed, view=None)
        except Exception:
            pass
        try:
            await channel.send(f"{get_emoji('giveaway')} Congratulations {winners_text}! You won **{prize}**!")
        except Exception:
            pass

    # DM notification for each winner
    jump_url = f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"
    dm_embed = discord.Embed(
        title=f"{get_emoji('giveaway')} You Won a Giveaway! {get_emoji('giveaway')}",
        description=f"Congratulations! You won the giveaway for **{prize}** in **{guild_name}**!",
        color=THEME_COLOR
    )
    dm_embed.add_field(name="Prize", value=f"**{prize}**", inline=True)
    dm_embed.add_field(name="Server", value=f"**{guild_name}**", inline=True)
    dm_embed.add_field(name="Giveaway", value=f"[Jump to Giveaway Message]({jump_url})", inline=False)
    dm_embed.set_footer(text="Thank you for participating!")
    dm_embed.timestamp = discord.utils.utcnow()

    for w_id in winners:
        try:
            user = bot.get_user(w_id) or await bot.fetch_user(w_id)
            if user:
                await user.send(embed=dm_embed)
        except Exception:
            pass

    return True

@tasks.loop(seconds=10)
async def check_giveaways_loop():
    """Background task loop to automatically end active giveaways whose duration has elapsed."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT message_id, end_time FROM giveaways WHERE ended = 0') as cursor:
                rows = await cursor.fetchall()

        now = discord.utils.utcnow()
        for message_id, end_time_str in rows:
            try:
                end_dt = datetime.datetime.fromisoformat(end_time_str)
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=datetime.timezone.utc)
                if now >= end_dt:
                    await _end_giveaway(message_id)
            except Exception as ex:
                logging.error(f"[Giveaway Loop Error] Exception for message_id={message_id}: {ex}")
    except Exception as e:
        logging.error(f"[Giveaway Loop Error] Task loop exception: {e}")

@bot.event
async def on_interaction(interaction: discord.Interaction):
    """Handle persistent giveaway button clicks."""
    if interaction.type != discord.InteractionType.component:
        return
    custom_id = interaction.data.get("custom_id", "")
    if not custom_id.startswith("giveaway_join"):
        return
    # Extract message ID from custom_id or use interaction message
    parts = custom_id.split("_")
    if len(parts) >= 3 and parts[2].isdigit():
        mid = int(parts[2])
    else:
        mid = interaction.message.id

    import json
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT participants, ended FROM giveaways WHERE message_id = ?', (mid,)) as cursor:
            row = await cursor.fetchone()
        if not row:
            return await interaction.response.send_message(f"{get_emoji('error')} Giveaway not found.", ephemeral=True)
        participants = json.loads(row[0])
        if row[1]:
            return await interaction.response.send_message(f"{get_emoji('error')} This giveaway has already ended.", ephemeral=True)
        uid = interaction.user.id
        if uid in participants:
            participants.remove(uid)
            action_msg = "You have **left** the giveaway."
        else:
            participants.append(uid)
            action_msg = f"You have **entered** the giveaway! {get_emoji('giveaway')}"
        await db.execute('UPDATE giveaways SET participants = ? WHERE message_id = ?', (json.dumps(participants), mid))
        await db.commit()

    # Respond to interaction FIRST, then update button label
    await interaction.response.send_message(action_msg, ephemeral=True)
    try:
        view = discord.ui.View(timeout=None)
        btn = discord.ui.Button(label=str(len(participants)), emoji=get_ui_emoji("giveaway"), style=discord.ButtonStyle.blurple, custom_id=f"giveaway_join_{mid}")
        view.add_item(btn)
        await interaction.message.edit(view=view)
    except Exception:
        pass


class WelcomerCustomTextModal(discord.ui.Modal, title="Customize Welcome Text & Image"):
    welcome_msg = discord.ui.TextInput(
        label="Welcome Message Text",
        style=discord.TextStyle.paragraph,
        placeholder="Hey {user.mention}, welcome to {guild.name}! You are member #{guild.member_count}!",
        required=False,
        max_length=2000
    )
    image_url = discord.ui.TextInput(
        label="Image/GIF URL (Optional)",
        style=discord.TextStyle.short,
        placeholder="https://i.imgur.com/...png (or blank for default)",
        required=False,
        max_length=500
    )

    def __init__(self, current_msg: str, current_img: str):
        super().__init__()
        self.welcome_msg.default = current_msg or ""
        self.image_url.default = current_img or ""

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()

class WelcomerEmbedSelect(discord.ui.Select):
    def __init__(self, saved_embeds: list, current_embed: str):
        options = [
            discord.SelectOption(label="Clear Custom Embed (Use Text)", value="none", emoji=get_ui_emoji('delete'), description="Use basic welcome message instead.")
        ]
        for name in saved_embeds[:24]:
            options.append(
                discord.SelectOption(
                    label=f"Embed: {name}",
                    value=name,
                    emoji="🖼️",
                    description=f"Use saved embed '{name}' for welcomes.",
                    default=(name == current_embed)
                )
            )
        super().__init__(placeholder="Select a saved embed for welcomes...", min_values=1, max_values=1, options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        embed_choice = self.values[0]
        val = None if embed_choice == "none" else embed_choice
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('CREATE TABLE IF NOT EXISTS welcomer_config (guild_id INTEGER PRIMARY KEY, channel_id INTEGER, message TEXT, image_url TEXT, embed_name TEXT)')
            await db.execute('INSERT INTO welcomer_config (guild_id, embed_name) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET embed_name=?', (interaction.guild_id, val, val))
            await db.commit()
            
        await self.view.refresh_dashboard(interaction)

class WelcomerChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="Select a welcome channel...",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        selected_channel = self.values[0]
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('CREATE TABLE IF NOT EXISTS welcomer_config (guild_id INTEGER PRIMARY KEY, channel_id INTEGER, message TEXT, image_url TEXT, embed_name TEXT)')
            await db.execute('INSERT INTO welcomer_config (guild_id, channel_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET channel_id=?', (interaction.guild_id, selected_channel.id, selected_channel.id))
            await db.commit()
            
        await self.view.refresh_dashboard(interaction)

class WelcomerDashboardView(discord.ui.View):
    def __init__(self, author: discord.Member, bot_ref):
        super().__init__(timeout=180)
        self.author = author
        self.bot = bot_ref
        self.message = None
        
        self.add_item(WelcomerChannelSelect())
        
    async def load_embed_select(self):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT name FROM saved_embeds WHERE guild_id = ?', (self.author.guild.id,)) as cursor:
                rows = await cursor.fetchall()
            async with db.execute('SELECT embed_name FROM welcomer_config WHERE guild_id = ?', (self.author.guild.id,)) as cursor:
                config_row = await cursor.fetchone()
                
        saved_names = [r[0] for r in rows]
        current_embed = config_row[0] if config_row else None
        
        for item in list(self.children):
            if isinstance(item, WelcomerEmbedSelect):
                self.remove_item(item)
                
        self.add_item(WelcomerEmbedSelect(saved_names, current_embed))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(f"{get_emoji('error')} This dashboard is only for the command user.", ephemeral=True)
            return False
        return True

    async def build_dashboard_embed(self) -> discord.Embed:
        guild = self.author.guild
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT channel_id, message, image_url, embed_name FROM welcomer_config WHERE guild_id = ?', (guild.id,)) as cursor:
                row = await cursor.fetchone()
                
        if not row:
            status_text = f"{get_emoji('error')} **Not Configured**"
            channel_mention = "None"
            welcome_type = "Default"
            msg_text = "Default welcome text will be used."
            img_status = "None"
            embed_status = "None"
        else:
            channel_id, custom_message, image_url, embed_name = row
            channel = guild.get_channel(channel_id)
            channel_mention = channel.mention if channel else f"{get_emoji('warn')} Channel Deleted / Not Found"
            status_text = f"{get_emoji('success')} **Active**" if channel else f"{get_emoji('warn')} **Incomplete (No Channel)**"
            
            if embed_name:
                welcome_type = "Saved Embed"
                embed_status = f"`{embed_name}`"
            elif custom_message or image_url:
                welcome_type = "Custom Text/Image"
                embed_status = "None"
            else:
                welcome_type = "Default Text"
                embed_status = "None"
                
            msg_text = custom_message if custom_message else "Default welcome text will be used."
            img_status = f"[Link]({image_url})" if image_url else "None"
            
        embed = discord.Embed(
            title=f"{get_emoji('welcome')} Welcomer System Dashboard",
            description=(
                f"Welcome users to your server with style! Customize your channel, custom messages, "
                f"or link a custom embed crafted via `!embed` command.\n\n"
                f"**Dashboard Overview:**\n"
                f"• **Status:** {status_text}\n"
                f"• **Welcome Channel:** {channel_mention}\n"
                f"• **Mode:** `{welcome_type}`\n"
                f"• **Linked Embed:** {embed_status}\n"
                f"• **Welcome Image:** {img_status}\n\n"
                f"**💡 Placeholders Guide:**\n"
                f"• `{{user}}` / `{{user.mention}}` - Mentions the joining user\n"
                f"• `{{user.name}}` - The username of the user\n"
                f"• `{{user.id}}` - The Discord ID of the user\n"
                f"• `{{user.avatar}}` - The user's avatar image URL\n"
                f"• `{{guild}}` / `{{guild.name}}` - Server's name\n"
                f"• `{{guild.member_count}}` - Total member count\n"
                f"• `{{guild.icon}}` / `{{guild.banner}}` - Server icon/banner URL\n\n"
                f"**Welcome Text Preview:**\n```\n{msg_text}\n```"
            ),
            color=THEME_COLOR
        )
        embed.set_thumbnail(url=guild.icon.url if guild.icon else self.bot.user.display_avatar.url)
        embed.set_footer(text="Use options below to customize dynamically.")
        return embed

    async def refresh_dashboard(self, interaction: discord.Interaction):
        await self.load_embed_select()
        new_embed = await self.build_dashboard_embed()
        await interaction.message.edit(embed=new_embed, view=self)

    @discord.ui.button(label="Customize Message", emoji="✍️", style=discord.ButtonStyle.blurple, row=2)
    async def customize_message(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT message, image_url FROM welcomer_config WHERE guild_id = ?', (interaction.guild_id,)) as cursor:
                row = await cursor.fetchone()
        curr_msg = row[0] if row else ""
        curr_img = row[1] if row else ""
        
        modal = WelcomerCustomTextModal(curr_msg, curr_img)
        await interaction.response.send_modal(modal)
        await modal.wait()
        
        if modal.is_finished():
            msg_val = modal.welcome_msg.value or None
            img_val = modal.image_url.value or None
            
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute('CREATE TABLE IF NOT EXISTS welcomer_config (guild_id INTEGER PRIMARY KEY, channel_id INTEGER, message TEXT, image_url TEXT, embed_name TEXT)')
                await db.execute(
                    'INSERT INTO welcomer_config (guild_id, message, image_url) VALUES (?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET message=?, image_url=?',
                    (interaction.guild_id, msg_val, img_val, msg_val, img_val)
                )
                await db.commit()
                
            await self.refresh_dashboard(interaction)

    @discord.ui.button(label="Send Test Welcome", emoji="🚀", style=discord.ButtonStyle.green, row=2)
    async def send_test(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT channel_id FROM welcomer_config WHERE guild_id = ?', (interaction.guild_id,)) as cursor:
                row = await cursor.fetchone()
        if not row or not row[0]:
            return await interaction.followup.send(f"{get_emoji('error')} Welcomer channel is not set up yet! Please select a channel first.", ephemeral=True)
            
        channel = interaction.guild.get_channel(row[0])
        if not channel:
            return await interaction.followup.send(f"{get_emoji('error')} Welcome channel not found or deleted.", ephemeral=True)
            
        ctx = await self.bot.get_context(interaction.message)
        ctx.author = interaction.user
        await welcomer_test(ctx, interaction.user)
        await interaction.followup.send("🚀 Test welcome message has been sent to the welcome channel!", ephemeral=True)

    @discord.ui.button(label="Reset Settings", emoji="🔄", style=discord.ButtonStyle.red, row=2)
    async def reset_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('DELETE FROM welcomer_config WHERE guild_id = ?', (interaction.guild_id,))
            await db.commit()
        await self.refresh_dashboard(interaction)

@bot.hybrid_group(name="welcomer", aliases=["welcome"], invoke_without_command=True)
@commands.has_permissions(manage_guild=True)
async def welcomer(ctx):
    """Setup or toggle a basic server welcomer channel."""
    if ctx.invoked_subcommand is not None:
        return
        
    view = WelcomerDashboardView(ctx.author, bot)
    await view.load_embed_select()
    embed = await view.build_dashboard_embed()
    view.message = await ctx.send(embed=embed, view=view)

@welcomer.command(name="channel")
@commands.has_permissions(manage_guild=True)
async def welcomer_channel(ctx, channel: discord.TextChannel = None):
    """Setup or toggle a basic server welcomer channel."""
    if channel is None:
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Please specify a channel. Usage: `!welcomer channel #channel`", color=THEME_COLOR))
        
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('CREATE TABLE IF NOT EXISTS welcomer_config (guild_id INTEGER PRIMARY KEY, channel_id INTEGER, message TEXT, image_url TEXT, embed_name TEXT)')
        await db.execute('INSERT INTO welcomer_config (guild_id, channel_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET channel_id=?', (ctx.guild.id, channel.id, channel.id))
        await db.commit()
        
    embed = discord.Embed(title=f"{get_emoji('welcome')} Welcomer Setup", color=THEME_COLOR)
    embed.description = f"{get_emoji('success')} Welcomer channel set to {channel.mention}."
    await ctx.send(embed=embed)

@welcomer.command(name="message", aliases=["text"])
@commands.has_permissions(manage_guild=True)
async def welcomer_message(ctx, *, message: str = None):
    """Set a custom welcome message."""
    if message is None:
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Please specify a message. Usage: `!welcomer message <text>`", color=THEME_COLOR))
        
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('CREATE TABLE IF NOT EXISTS welcomer_config (guild_id INTEGER PRIMARY KEY, channel_id INTEGER, message TEXT, image_url TEXT, embed_name TEXT)')
        await db.execute('INSERT INTO welcomer_config (guild_id, message) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET message=?', (ctx.guild.id, message, message))
        await db.commit()
        
    embed = discord.Embed(title=f"{get_emoji('welcome')} Welcomer Setup", color=THEME_COLOR)
    embed.description = f"{get_emoji('success')} Welcomer message updated to:\n\n{message}"
    await ctx.send(embed=embed)

@welcomer.command(name="image", aliases=["gif", "pic"])
@commands.has_permissions(manage_guild=True)
async def welcomer_image(ctx, url: str = None):
    """Set a custom welcome image or gif URL."""
    if url is None:
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Please specify an image URL. Usage: `!welcomer image <url>`", color=THEME_COLOR))
        
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('CREATE TABLE IF NOT EXISTS welcomer_config (guild_id INTEGER PRIMARY KEY, channel_id INTEGER, message TEXT, image_url TEXT, embed_name TEXT)')
        await db.execute('INSERT INTO welcomer_config (guild_id, image_url) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET image_url=?', (ctx.guild.id, url, url))
        await db.commit()
        
    embed = discord.Embed(title=f"{get_emoji('welcome')} Welcomer Setup", color=THEME_COLOR)
    embed.description = f"{get_emoji('success')} Welcomer image updated!"
    embed.set_image(url=url)
    await ctx.send(embed=embed)

@welcomer.command(name="embed")
@commands.has_permissions(manage_guild=True)
async def welcomer_embed(ctx, embed_name: str = None):
    """Set a custom saved embed to be used for welcome messages."""
    if not embed_name:
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Please specify the embed name. Usage: `!welcomer embed <embed_name>` or `!welcomer embed none` to disable", color=THEME_COLOR))
    
    embed_name = embed_name.lower()
    if embed_name in ("none", "clear", "disable"):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('CREATE TABLE IF NOT EXISTS welcomer_config (guild_id INTEGER PRIMARY KEY, channel_id INTEGER, message TEXT, image_url TEXT, embed_name TEXT)')
            await db.execute('INSERT INTO welcomer_config (guild_id, embed_name) VALUES (?, NULL) ON CONFLICT(guild_id) DO UPDATE SET embed_name=NULL', (ctx.guild.id,))
            await db.commit()
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('success')} Custom welcomer embed disabled. Bot will fall back to standard text welcomer.", color=THEME_COLOR))

    # Check if embed exists
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT name FROM saved_embeds WHERE guild_id = ? AND name = ?', (ctx.guild.id, embed_name)) as cursor:
            row = await cursor.fetchone()
    
    if not row:
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} No embed named `{embed_name}` found. Create it first using `!embed create {embed_name}`.", color=THEME_COLOR))

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('CREATE TABLE IF NOT EXISTS welcomer_config (guild_id INTEGER PRIMARY KEY, channel_id INTEGER, message TEXT, image_url TEXT, embed_name TEXT)')
        await db.execute('INSERT INTO welcomer_config (guild_id, embed_name) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET embed_name=?', (ctx.guild.id, embed_name, embed_name))
        await db.commit()

    await ctx.send(embed=discord.Embed(description=f"{get_emoji('success')} Custom welcomer embed set to `{embed_name}`.", color=THEME_COLOR))

@welcomer.command(name="test")
@commands.has_permissions(manage_guild=True)
async def welcomer_test(ctx, member: discord.Member = None):
    """Test the welcomer message for a user."""
    member = member or ctx.author
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT channel_id, message, image_url, embed_name FROM welcomer_config WHERE guild_id = ?', (ctx.guild.id,)) as cursor:
            row = await cursor.fetchone()
    
    if not row:
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Welcomer is not set up on this server. Run `!welcomer channel #channel` first.", color=THEME_COLOR))

    channel_id, custom_message, image_url, embed_name = row
    channel = ctx.guild.get_channel(channel_id)
    if not channel:
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Configured welcome channel not found. Please re-setup `!welcomer channel #channel`.", color=THEME_COLOR))

    if embed_name:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT data FROM saved_embeds WHERE guild_id = ? AND name = ?', (ctx.guild.id, embed_name)) as embed_cursor:
                embed_row = await embed_cursor.fetchone()
        if embed_row:
            try:
                import json
                from cogs.embed import build_embed_from_data
                data = json.loads(embed_row[0])
                welcome_embed = build_embed_from_data(data, member)
                await channel.send(content=f"{member.mention} *(Welcomer Test)*", embed=welcome_embed)
                await ctx.send(embed=discord.Embed(description=f"{get_emoji('success')} Welcome preview sent to {channel.mention}.", color=THEME_COLOR))
                return
            except Exception as e:
                return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Error rendering custom welcome embed: `{e}`", color=THEME_COLOR))

    welcome_text = custom_message if custom_message else f"Hey {member.mention}, welcome to the server! You are member #{len(ctx.guild.members)}."
    welcome_embed = discord.Embed(
        title=f"Welcome to {ctx.guild.name}!",
        description=welcome_text,
        color=THEME_COLOR
    )
    welcome_embed.set_thumbnail(url=member.display_avatar.url)
    if image_url:
        welcome_embed.set_image(url=image_url)
    elif ctx.guild.banner:
        welcome_embed.set_image(url=ctx.guild.banner.url)

    await channel.send(content=f"{member.mention} *(Welcomer Test)*", embed=welcome_embed)
    await ctx.send(embed=discord.Embed(description=f"{get_emoji('success')} Welcome preview sent to {channel.mention}.", color=THEME_COLOR))

# ─── Warn System ───────────────────────────────────────────────────────────────

@bot.hybrid_command()
@commands.has_permissions(moderate_members=True)
async def warn(ctx, member: discord.Member, *, reason: str = None):
    """Warn a member. Usage: !warn @user [reason]"""
    if member.id in DEVELOPER_IDS or is_dev_or_main_owner(member.id):
        embed = discord.Embed(description=f"u cant't do {ctx.command.name} to the developer/owner , nigga", color=THEME_COLOR)
        return await ctx.send(embed=embed)

    if not is_dev_or_main_owner(ctx.author.id) and ctx.guild.owner != ctx.author and ctx.author.top_role <= member.top_role:
        embed = discord.Embed(description=f"{get_emoji('error')} You cannot moderate {member.mention} because their highest role is equal to or higher than yours.", color=THEME_COLOR)
        embed.set_author(name="Access Denied", icon_url=ctx.author.display_avatar.url)
        return await ctx.send(embed=embed)

    if member.bot:
        await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} You cannot warn a bot.", color=THEME_COLOR))
        return

    # Save warning to DB
    now = discord.utils.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            'INSERT INTO warnings (guild_id, user_id, moderator_id, reason, timestamp) VALUES (?, ?, ?, ?, ?)',
            (ctx.guild.id, member.id, ctx.author.id, reason or 'No reason provided', now)
        )
        warn_id = cursor.lastrowid
        # Get total warnings count
        async with db.execute('SELECT COUNT(*) FROM warnings WHERE guild_id=? AND user_id=?', (ctx.guild.id, member.id)) as c:
            total_warnings = (await c.fetchone())[0]
        await db.commit()

    # DM the member
    dm_sent = await send_mod_dm(member, 'warn', ctx.guild.name, ctx.author, reason=reason)

    embed = discord.Embed(
        title=f"{get_emoji('warn')} Member Warned",
        description=f"**{member}** has been warned. They now have **{total_warnings}** warning(s).",
        color=0xFEE75C
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Reason", value=f"`{reason or 'No reason provided'}`", inline=False)
    embed.add_field(name="Warning ID", value=f"`#{warn_id}`", inline=True)
    embed.add_field(name="DM Status", value=f"`{get_emoji('success') + ' Delivered' if dm_sent else get_emoji('error') + ' Failed'}`", inline=True)
    embed.set_footer(text=f"Moderated by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    embed.timestamp = discord.utils.utcnow()
    await ctx.send(embed=embed)

@bot.hybrid_command()
@commands.has_permissions(moderate_members=True)
async def warnings(ctx, member: discord.Member = None):
    """View warnings for a member. Usage: !warnings @user"""
    if member is None:
        await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Please mention a user. Usage: `!warnings @user`", color=THEME_COLOR))
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT id, moderator_id, reason, timestamp FROM warnings WHERE guild_id=? AND user_id=? ORDER BY id DESC',
            (ctx.guild.id, member.id)
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        embed = discord.Embed(description=f"{get_emoji('success')} **{member}** has no warnings.", color=THEME_COLOR)
        embed.set_author(name="Warnings", icon_url=member.display_avatar.url)
        return await ctx.send(embed=embed)

    lines = []
    for warn_id, mod_id, reason, timestamp in rows:
        # Parse timestamp for display
        try:
            dt = datetime.datetime.fromisoformat(timestamp)
            time_display = f"<t:{int(dt.timestamp())}:R>"
        except Exception:
            time_display = timestamp[:10] if timestamp else "Unknown"
        lines.append(f"> `#{warn_id}` — By <@{mod_id}> {time_display}\n>   Reason: **{reason}**")

    # Split into pages if too long
    page_text = "\n".join(lines)
    if len(page_text) > 1024:
        page_text = "\n".join(lines[:10]) + f"\n\n*... and {len(lines) - 10} more*"

    embed = discord.Embed(title=f"Warnings for {member.display_name}", color=THEME_COLOR)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name=f"Total Warnings: {len(rows)}", value=page_text, inline=False)
    embed.set_footer(text="Use !delwarn <ID> to remove a warning")
    embed.timestamp = discord.utils.utcnow()
    await ctx.send(embed=embed)

@bot.hybrid_command()
@commands.has_permissions(moderate_members=True)
async def delwarn(ctx, warn_id: int):
    """Delete a specific warning by ID. Usage: !delwarn <ID>"""
    async with aiosqlite.connect(DB_PATH) as db:
        # Verify the warning exists and belongs to this guild
        async with db.execute('SELECT user_id FROM warnings WHERE id=? AND guild_id=?', (warn_id, ctx.guild.id)) as cursor:
            row = await cursor.fetchone()
        if not row:
            embed = discord.Embed(description=f"{get_emoji('error')} Warning `#{warn_id}` not found in this server.", color=THEME_COLOR)
            embed.set_author(name="Delete Warning Failed", icon_url=ctx.author.display_avatar.url)
            return await ctx.send(embed=embed)

        user_id = row[0]
        await db.execute('DELETE FROM warnings WHERE id=? AND guild_id=?', (warn_id, ctx.guild.id))
        await db.commit()

    embed = discord.Embed(
        title=f"{get_emoji('delete')} Warning Deleted",
        description=f"Warning `#{warn_id}` for <@{user_id}> has been successfully removed.",
        color=THEME_COLOR
    )
    embed.set_footer(text=f"Moderated by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    embed.timestamp = discord.utils.utcnow()
    await ctx.send(embed=embed)

@bot.hybrid_command()
@commands.has_permissions(moderate_members=True)
async def clearwarnings(ctx, member: discord.Member = None):
    """Clear all warnings for a member. Usage: !clearwarnings @user"""
    if member is None:
        await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Please mention a user. Usage: `!clearwarnings @user`", color=THEME_COLOR))
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('DELETE FROM warnings WHERE guild_id=? AND user_id=?', (ctx.guild.id, member.id)) as cursor:
            deleted = cursor.rowcount
        await db.commit()

    if deleted > 0:
        embed = discord.Embed(
            title=f"{get_emoji('delete')} Warnings Cleared",
            description=f"Successfully cleared **{deleted}** warning(s) for **{member}**.",
            color=THEME_COLOR
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Moderated by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()
        await ctx.send(embed=embed)
    else:
        embed = discord.Embed(description=f"{get_emoji('general')} **{member}** has no warnings to clear.", color=0x5865F2)
        embed.set_author(name="Clear Warnings", icon_url=member.display_avatar.url)
        await ctx.send(embed=embed)

@bot.hybrid_command()
@commands.has_permissions(manage_messages=True)
@commands.bot_has_permissions(manage_messages=True)
async def pb(ctx):
    """Shortcut for !purge bot"""
    await ctx.message.delete()
    deleted = await ctx.channel.purge(limit=500, check=lambda m: m.author.bot)
    embed = discord.Embed(title="🧹  Purge Bot result:", color=THEME_COLOR)
    embed.add_field(name="Moderator:", value=ctx.author.mention, inline=False)
    embed.add_field(
        name="Details:",
        value=(
            f"{get_emoji('success')} **Successful Purge**\n"
            f"> Deleted **{len(deleted)}** bot message(s)\n\n"
            f"{get_emoji('error')} **Unsuccessful Purge**\n"
            f"No messages failed!"
        ),
        inline=False,
    )
    embed.timestamp = discord.utils.utcnow()
    msg = await ctx.send(embed=embed)
    await asyncio.sleep(5)
    try:
        await msg.delete()
    except Exception:
        pass

class SocialModal(discord.ui.Modal):
    def __init__(self, platform: str, placeholder: str):
        super().__init__(title=f"{platform} Account Setup")
        self.platform = platform
        
        self.username_input = discord.ui.TextInput(
            label=f"Enter your {platform} ID",
            placeholder=placeholder,
            required=True,
            max_length=50
        )
        self.add_item(self.username_input)

    async def on_submit(self, interaction: discord.Interaction):
        import os
        
        folder_path = "users_info"
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)
            
        file_path = os.path.join(folder_path, "socials.txt")
        
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(f"{self.platform.lower()} {interaction.user} = {self.username_input.value}\n")
            
        await interaction.response.send_message(embed=discord.Embed(description=f"{get_emoji('success')} Your {self.platform} ID `{self.username_input.value}` has been saved successfully!", color=THEME_COLOR), ephemeral=True)

class SocialView(discord.ui.View):
    def __init__(self, cmd_name: str, platform: str, placeholder: str):
        super().__init__(timeout=None)
        self.platform = platform
        self.placeholder = placeholder
        
        button = discord.ui.Button(label=f"Enter {platform} ID", style=discord.ButtonStyle.red, custom_id=f"{cmd_name}_enter_id")
        async def button_callback(interaction: discord.Interaction):
            await interaction.response.send_modal(SocialModal(self.platform, self.placeholder))
        button.callback = button_callback
        self.add_item(button)

def make_social_cmd(cmd_name: str, platform: str, placeholder: str):
    async def _social(ctx, member: discord.Member = None):
        target = member or ctx.author
        
        import os
        file_path = os.path.join("users_info", "socials.txt")
        saved_id = None
        
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                search_str = f"{platform.lower()} {target} = "
                for line in reversed(lines):
                    if line.startswith(search_str):
                        saved_id = line.split("=", 1)[1].strip()
                        break
                        
        embed = discord.Embed(
            title=f"{get_emoji('games')} {platform} Integration",
            color=0xFA4454
        )
        embed.set_author(name=target.display_name, icon_url=target.display_avatar.url)
        
        if saved_id:
            embed.description = f"**{target.mention}'s {platform} ID:**\n`{saved_id}`"
        else:
            if target == ctx.author:
                embed.description = f"You haven't linked your {platform} ID yet!\nClick the button below to link it (`{placeholder}`)."
            else:
                embed.description = f"{target.mention} hasn't linked their {platform} ID yet."
                
        view = SocialView(cmd_name, platform, placeholder)
        await ctx.send(embed=embed, view=view)
        
    _social.__name__ = cmd_name
    cmd = commands.HybridCommand(_social, name=cmd_name)
    cmd.help = f"Link or view the {platform} profile of a member."
    return cmd

SOCIAL_PLATFORMS = {
    "valorant": ("Valorant", "username#tag"),
    "xbox": ("Xbox", "gamertag"),
    "snapchat": ("Snapchat", "username"),
    "instagram": ("Instagram", "@username"),
    "twitter": ("Twitter/X", "@username"),
    "telegram": ("Telegram", "@username"),
    "steam": ("Steam", "steam_id or vanity_url"),
    "freefire": ("Free Fire", "player_id"),
    "roblox": ("Roblox", "username"),
    "coc": ("Clash of Clans", "#PlayerTag")
}

for cmd_name, (plat, ph) in SOCIAL_PLATFORMS.items():
    if bot.get_command(cmd_name) is None:
        try:
            bot.add_command(make_social_cmd(cmd_name, plat, ph))
        except discord.ext.commands.errors.CommandRegistrationError:
            pass



class AFKTypeView(discord.ui.View):
    def __init__(self, reason: str):
        super().__init__(timeout=60.0)
        self.reason = reason

    async def set_afk(self, interaction: discord.Interaction, is_global: int):
        import time
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                'INSERT OR REPLACE INTO afk_status (user_id, reason, guild_id, is_global, timestamp) VALUES (?, ?, ?, ?, ?)',
                (interaction.user.id, self.reason, interaction.guild.id if interaction.guild else None, is_global, time.time())
            )
            await db.commit()
        
        mode = "Globally" if is_global else "in this Server"
        embed = discord.Embed(
            description=f"{get_emoji('success')} You are now AFK {mode}: `{self.reason}`",
            color=THEME_COLOR
        )
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="Globally", emoji=get_ui_emoji("global"), style=discord.ButtonStyle.blurple)
    async def btn_global(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.set_afk(interaction, 1)

    @discord.ui.button(label="Server Only", emoji=get_ui_emoji("server"), style=discord.ButtonStyle.green)
    async def btn_server(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.set_afk(interaction, 0)

@bot.hybrid_command()
async def afk(ctx, *, reason: str = "AFK"):
    """Set your status to AFK (away from keyboard)."""
    view = AFKTypeView(reason)
    embed = discord.Embed(
        description=f"{ctx.author.mention}, where would you like to apply your AFK status?\nReason: `{reason}`",
        color=THEME_COLOR
    )
    await ctx.send(embed=embed, view=view)

@bot.hybrid_command()
@commands.has_permissions(manage_channels=True)
async def lock(ctx, channel: discord.TextChannel = None):
    """Lock a text channel to prevent messages."""
    channel = channel or ctx.channel
    await channel.set_permissions(ctx.guild.default_role, send_messages=False)
    embed = discord.Embed(
        title=f"{get_emoji('lock')} Channel Locked",
        description=f"**{channel.mention}** has been locked for the default role.",
        color=THEME_COLOR
    )
    embed.set_footer(text=f"Moderated by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    await ctx.send(embed=embed)

@bot.hybrid_command()
@commands.has_permissions(manage_channels=True)
async def unlock(ctx, channel: discord.TextChannel = None):
    """Unlock a text channel to allow messages."""
    channel = channel or ctx.channel
    await channel.set_permissions(ctx.guild.default_role, send_messages=None)
    embed = discord.Embed(
        title=f"{get_emoji('unlock')} Channel Unlocked",
        description=f"**{channel.mention}** has been unlocked for the default role.",
        color=THEME_COLOR
    )
    embed.set_footer(text=f"Moderated by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    await ctx.send(embed=embed)

@bot.hybrid_command()
@commands.has_permissions(manage_channels=True)
async def hide(ctx, channel: discord.TextChannel = None):
    """Hide a text channel from regular members."""
    channel = channel or ctx.channel
    await channel.set_permissions(ctx.guild.default_role, view_channel=False)
    embed = discord.Embed(
        title=f"{get_emoji('hide')} Channel Hidden",
        description=f"**{channel.mention}** is now hidden from regular members.",
        color=THEME_COLOR
    )
    embed.set_footer(text=f"Moderated by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    await ctx.send(embed=embed)

@bot.hybrid_command()
@commands.has_permissions(manage_channels=True)
async def unhide(ctx, channel: discord.TextChannel = None):
    """Unhide a text channel to make it visible."""
    channel = channel or ctx.channel
    await channel.set_permissions(ctx.guild.default_role, view_channel=None)
    embed = discord.Embed(
        title=f"{get_emoji('unhide')} Channel Visible",
        description=f"**{channel.mention}** is now visible again.",
        color=THEME_COLOR
    )
    embed.set_footer(text=f"Moderated by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    await ctx.send(embed=embed)

@bot.hybrid_command()
@commands.has_permissions(manage_channels=True)
async def lockall(ctx):
    """Lock all text channels in the server."""
    embed = discord.Embed(
        title=f"{get_emoji('lock')} Locking Channels",
        description="Locking all text channels, please wait...",
        color=THEME_COLOR
    )
    msg = await ctx.send(embed=embed)
    count = 0
    for channel in ctx.guild.text_channels:
        try:
            await channel.set_permissions(ctx.guild.default_role, send_messages=False)
            count += 1
        except Exception:
            pass
    embed.description = f"**{count}** text channels have been locked for the default role."
    embed.set_footer(text=f"Moderated by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    await msg.edit(embed=embed)

@bot.hybrid_command()
@commands.has_permissions(manage_channels=True)
async def unlockall(ctx):
    """Unlock all text channels in the server."""
    embed = discord.Embed(
        title=f"{get_emoji('unlock')} Unlocking Channels",
        description="Unlocking all text channels, please wait...",
        color=THEME_COLOR
    )
    msg = await ctx.send(embed=embed)
    count = 0
    for channel in ctx.guild.text_channels:
        try:
            await channel.set_permissions(ctx.guild.default_role, send_messages=None)
            count += 1
        except Exception:
            pass
    embed.description = f"**{count}** text channels have been unlocked for the default role."
    embed.set_footer(text=f"Moderated by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    await msg.edit(embed=embed)

@bot.hybrid_command()
@commands.has_permissions(manage_channels=True)
async def hideall(ctx):
    """Hide all text channels in the server."""
    embed = discord.Embed(
        title=f"{get_emoji('hide')} Hiding Channels",
        description="Hiding all text channels, please wait...",
        color=THEME_COLOR
    )
    msg = await ctx.send(embed=embed)
    count = 0
    for channel in ctx.guild.text_channels:
        try:
            await channel.set_permissions(ctx.guild.default_role, view_channel=False)
            count += 1
        except Exception:
            pass
    embed.description = f"**{count}** text channels have been hidden from regular members."
    embed.set_footer(text=f"Moderated by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    await msg.edit(embed=embed)

@bot.hybrid_command()
@commands.has_permissions(manage_channels=True)
async def unhideall(ctx):
    """Unhide all text channels in the server."""
    embed = discord.Embed(
        title=f"{get_emoji('unhide')} Unhiding Channels",
        description="Unhiding all text channels, please wait...",
        color=THEME_COLOR
    )
    msg = await ctx.send(embed=embed)
    count = 0
    for channel in ctx.guild.text_channels:
        try:
            await channel.set_permissions(ctx.guild.default_role, view_channel=None)
            count += 1
        except Exception:
            pass
    embed.description = f"**{count}** text channels have been made visible again."
    embed.set_footer(text=f"Moderated by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    await msg.edit(embed=embed)

GAME_PLATFORMS = {
    "valorant": "Valorant",
    "xbox": "Xbox",
    "steam": "Steam",
    "free fire": "Free Fire",
    "roblox": "Roblox",
    "clash of clans": "Clash of Clans",
}

SOCIAL_MEDIA_PLATFORMS = {
    "snapchat": "Snapchat",
    "instagram": "Instagram",
    "twitter/x": "Twitter/X",
    "telegram": "Telegram",
}

def _lookup_user_socials(target, platform_map):
    import os
    file_path = os.path.join("users_info", "socials.txt")
    results = {}
    if not os.path.exists(file_path):
        return results
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    target_str = str(target)
    for key, display in platform_map.items():
        search = f"{key} {target_str} = "
        for line in reversed(lines):
            if line.lower().startswith(search):
                results[display] = line.split("=", 1)[1].strip()
                break
    return results

@bot.hybrid_command()
async def viewgames(ctx, member: discord.Member = None):
    """View linked gaming profiles of a member."""
    target = member or ctx.author
    results = _lookup_user_socials(target, GAME_PLATFORMS)
    embed = discord.Embed(
        title=f"{get_emoji('games')} {target.display_name}'s Game Profiles",
        color=0xFA4454
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    if results:
        for platform, username in results.items():
            embed.add_field(name=platform, value=f"`{username}`", inline=True)
    else:
        embed.description = f"{target.mention} hasn't linked any game profiles yet."
    embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
    await ctx.send(embed=embed)

@bot.hybrid_command()
async def viewsocial(ctx, member: discord.Member = None):
    """View linked social media profiles of a member."""
    target = member or ctx.author
    results = _lookup_user_socials(target, SOCIAL_MEDIA_PLATFORMS)
    embed = discord.Embed(
        title=f"📱 {target.display_name}'s Social Media",
        color=0x5865F2
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    if results:
        for platform, username in results.items():
            embed.add_field(name=platform, value=f"`{username}`", inline=True)
    else:
        embed.description = f"{target.mention} hasn't linked any social media profiles yet."
    embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
    await ctx.send(embed=embed)

# --- Automod Commands ---
@bot.hybrid_group(invoke_without_command=True)
@commands.has_permissions(manage_guild=True)
async def antispam(ctx):
    """Configure and manage antispam system."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT antispam_enabled FROM automod_config WHERE guild_id = ?', (ctx.guild.id,)) as cursor:
            row = await cursor.fetchone()
    status = f"{get_emoji('success')} Enabled" if row and row[0] else f"{get_emoji('error')} Disabled"
    embed = discord.Embed(title=f"{get_emoji('antinuke')} Antispam Status", description=f"Status: **{status}**\n\nUse `!antispam enable` / `!antispam disable`\n`!antispam wl add <user/role>`", color=0x5865F2)
    await ctx.send(embed=embed)

@antispam.command(name="enable")
@commands.has_permissions(manage_guild=True)
async def antispam_enable(ctx):
    """Enable antispam detection."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('INSERT INTO automod_config (guild_id, antispam_enabled) VALUES (?, 1) ON CONFLICT(guild_id) DO UPDATE SET antispam_enabled = 1', (ctx.guild.id,))
        await db.commit()
    await ctx.send(embed=discord.Embed(description=f"{get_emoji('success')} Antispam has been **enabled**.", color=THEME_COLOR))

@antispam.command(name="disable")
@commands.has_permissions(manage_guild=True)
async def antispam_disable(ctx):
    """Disable antispam detection."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE automod_config SET antispam_enabled = 0 WHERE guild_id = ?', (ctx.guild.id,))
        await db.commit()
    await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Antispam has been **disabled**.", color=THEME_COLOR))

class AntispamConditionModal(discord.ui.Modal, title=f"{get_emoji('settings')} Antispam Condition"):
    max_messages = discord.ui.TextInput(
        label="Max Messages (before punishment)",
        placeholder="e.g. 5",
        required=True,
        max_length=3
    )
    interval = discord.ui.TextInput(
        label="Time Interval (seconds)",
        placeholder="e.g. 5",
        required=True,
        max_length=3
    )

    def __init__(self, guild_id: int):
        super().__init__()
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            max_msgs = int(self.max_messages.value)
            time_interval = int(self.interval.value)
        except ValueError:
            return await interaction.response.send_message(embed=discord.Embed(description=f"{get_emoji('error')} Please enter valid numbers.", color=THEME_COLOR), ephemeral=True)
        if max_msgs < 2 or max_msgs > 50:
            return await interaction.response.send_message(embed=discord.Embed(description=f"{get_emoji('error')} Max messages must be between 2 and 50.", color=THEME_COLOR), ephemeral=True)
        if time_interval < 1 or time_interval > 60:
            return await interaction.response.send_message(embed=discord.Embed(description=f"{get_emoji('error')} Time interval must be between 1 and 60 seconds.", color=THEME_COLOR), ephemeral=True)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('INSERT INTO automod_config (guild_id, spam_max_messages, spam_interval) VALUES (?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET spam_max_messages = ?, spam_interval = ?', (self.guild_id, max_msgs, time_interval, max_msgs, time_interval))
            await db.commit()
        await interaction.response.send_message(embed=discord.Embed(description=f"{get_emoji('success')} Antispam condition updated: **{max_msgs} messages** in **{time_interval} seconds** will trigger punishment.", color=THEME_COLOR), ephemeral=False)

@antispam.command(name="condition")
@commands.has_permissions(manage_guild=True)
async def antispam_condition(ctx):
    """Configure thresholds for antispam detection."""
    if hasattr(ctx, 'interaction') and ctx.interaction:
        await ctx.interaction.response.send_modal(AntispamConditionModal(ctx.guild.id))
        return
    # For prefix commands, use a button to trigger the modal
    class ConditionButton(discord.ui.View):
        def __init__(self, guild_id):
            super().__init__(timeout=60)
            self.guild_id = guild_id
        @discord.ui.button(label="Set Condition", emoji=get_ui_emoji("settings"), style=discord.ButtonStyle.blurple)
        async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.send_modal(AntispamConditionModal(self.guild_id))
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT spam_max_messages, spam_interval FROM automod_config WHERE guild_id = ?', (ctx.guild.id,)) as cursor:
            row = await cursor.fetchone()
    current_msgs = row[0] if row and row[0] else 5
    current_interval = row[1] if row and row[1] else 5
    embed = discord.Embed(
        title=f"{get_emoji('settings')} Antispam Condition",
        description=f"**Current Setting:** `{current_msgs}` messages in `{current_interval}` seconds\n\nClick the button below to change the condition.",
        color=0x5865F2
    )
    await ctx.send(embed=embed, view=ConditionButton(ctx.guild.id))

@antispam.group(name="wl", invoke_without_command=True)
@commands.has_permissions(manage_guild=True)
async def antispam_wl(ctx):
    """Manage the antispam whitelist."""
    await ctx.send(embed=discord.Embed(description="Usage: `!antispam wl add <user/role>` or `!antispam wl remove <user/role>` or `!antispam wl list`", color=THEME_COLOR))

@antispam_wl.command(name="add")
@commands.has_permissions(manage_guild=True)
async def antispam_wl_add(ctx, target: discord.Member | discord.Role = None):
    """Add a user or role to the antispam whitelist."""
    if target is None:
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Please mention a user or role.", color=THEME_COLOR))
    t_type = "role" if isinstance(target, discord.Role) else "user"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('INSERT OR IGNORE INTO automod_whitelist (guild_id, target_id, target_type, module) VALUES (?, ?, ?, ?)', (ctx.guild.id, target.id, t_type, 'antispam'))
        await db.commit()
    await ctx.send(embed=discord.Embed(description=f"{get_emoji('success')} {target.mention} has been whitelisted from antispam.", color=THEME_COLOR))

@antispam_wl.command(name="remove")
@commands.has_permissions(manage_guild=True)
async def antispam_wl_remove(ctx, target: discord.Member | discord.Role = None):
    """Remove a user or role from the antispam whitelist."""
    if target is None:
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Please mention a user or role.", color=THEME_COLOR))
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM automod_whitelist WHERE guild_id = ? AND target_id = ? AND module = ?', (ctx.guild.id, target.id, 'antispam'))
        await db.commit()
    await ctx.send(embed=discord.Embed(description=f"{get_emoji('success')} {target.mention} has been removed from antispam whitelist.", color=THEME_COLOR))

@antispam_wl.command(name="list")
@commands.has_permissions(manage_guild=True)
async def antispam_wl_list(ctx):
    """List all whitelisted users and roles for antispam."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT target_id, target_type FROM automod_whitelist WHERE guild_id = ? AND module = ?', (ctx.guild.id, 'antispam')) as cursor:
            rows = await cursor.fetchall()
    if not rows:
        return await ctx.send(embed=discord.Embed(description="No antispam whitelist entries.", color=THEME_COLOR))
    lines = []
    for tid, ttype in rows:
        if ttype == 'role':
            r = ctx.guild.get_role(tid)
            lines.append(f"{get_emoji('roles')} {r.mention if r else f'Unknown ({tid})'}") 
        else:
            lines.append(f"{get_emoji('profiles')} <@{tid}>")
    embed = discord.Embed(title=f"{get_emoji('antinuke')} Antispam Whitelist", description="\n".join(lines), color=0x5865F2)
    await ctx.send(embed=embed)

# --- Antilink Commands ---
@bot.hybrid_group(invoke_without_command=True)
@commands.has_permissions(manage_guild=True)
async def antilink(ctx):
    """Configure and manage antilink system."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT antilink_enabled FROM automod_config WHERE guild_id = ?', (ctx.guild.id,)) as cursor:
            row = await cursor.fetchone()
    status = f"{get_emoji('success')} Enabled" if row and row[0] else f"{get_emoji('error')} Disabled"
    embed = discord.Embed(title=f"{get_emoji('link')} Antilink Status", description=f"Status: **{status}**\n\nUse `!antilink enable` / `!antilink disable`\n`!antilink wl add <user/role>`", color=0x5865F2)
    await ctx.send(embed=embed)

@antilink.command(name="enable")
@commands.has_permissions(manage_guild=True)
async def antilink_enable(ctx):
    """Enable antilink detection."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('INSERT INTO automod_config (guild_id, antilink_enabled) VALUES (?, 1) ON CONFLICT(guild_id) DO UPDATE SET antilink_enabled = 1', (ctx.guild.id,))
        await db.commit()
    await ctx.send(embed=discord.Embed(description=f"{get_emoji('success')} Antilink has been **enabled**.", color=THEME_COLOR))

@antilink.command(name="disable")
@commands.has_permissions(manage_guild=True)
async def antilink_disable(ctx):
    """Disable antilink detection."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE automod_config SET antilink_enabled = 0 WHERE guild_id = ?', (ctx.guild.id,))
        await db.commit()
    await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Antilink has been **disabled**.", color=THEME_COLOR))

@antilink.group(name="wl", invoke_without_command=True)
@commands.has_permissions(manage_guild=True)
async def antilink_wl(ctx):
    """Manage the antilink whitelist."""
    await ctx.send(embed=discord.Embed(description="Usage: `!antilink wl add <user/role>` or `!antilink wl remove <user/role>` or `!antilink wl list`", color=THEME_COLOR))

@antilink_wl.command(name="add")
@commands.has_permissions(manage_guild=True)
async def antilink_wl_add(ctx, target: discord.Member | discord.Role = None):
    """Add a user or role to the antilink whitelist."""
    if target is None:
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Please mention a user or role.", color=THEME_COLOR))
    t_type = "role" if isinstance(target, discord.Role) else "user"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('INSERT OR IGNORE INTO automod_whitelist (guild_id, target_id, target_type, module) VALUES (?, ?, ?, ?)', (ctx.guild.id, target.id, t_type, 'antilink'))
        await db.commit()
    await ctx.send(embed=discord.Embed(description=f"{get_emoji('success')} {target.mention} has been whitelisted from antilink.", color=THEME_COLOR))

@antilink_wl.command(name="remove")
@commands.has_permissions(manage_guild=True)
async def antilink_wl_remove(ctx, target: discord.Member | discord.Role = None):
    """Remove a user or role from the antilink whitelist."""
    if target is None:
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Please mention a user or role.", color=THEME_COLOR))
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM automod_whitelist WHERE guild_id = ? AND target_id = ? AND module = ?', (ctx.guild.id, target.id, 'antilink'))
        await db.commit()
    await ctx.send(embed=discord.Embed(description=f"{get_emoji('success')} {target.mention} has been removed from antilink whitelist.", color=THEME_COLOR))

@antilink_wl.command(name="list")
@commands.has_permissions(manage_guild=True)
async def antilink_wl_list(ctx):
    """List all whitelisted users and roles for antilink."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT target_id, target_type FROM automod_whitelist WHERE guild_id = ? AND module = ?', (ctx.guild.id, 'antilink')) as cursor:
            rows = await cursor.fetchall()
    if not rows:
        return await ctx.send(embed=discord.Embed(description="No antilink whitelist entries.", color=THEME_COLOR))
    lines = []
    for tid, ttype in rows:
        if ttype == 'role':
            r = ctx.guild.get_role(tid)
            lines.append(f"{get_emoji('roles')} {r.mention if r else f'Unknown ({tid})'}") 
        else:
            lines.append(f"{get_emoji('profiles')} <@{tid}>")
    embed = discord.Embed(title=f"{get_emoji('link')} Antilink Whitelist", description="\n".join(lines), color=0x5865F2)
    await ctx.send(embed=embed)

# --- Antiword Commands ---
@bot.hybrid_group(invoke_without_command=True)
@commands.has_permissions(manage_guild=True)
async def antiword(ctx):
    """Configure and manage bad words filter."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT antiword_enabled FROM automod_config WHERE guild_id = ?', (ctx.guild.id,)) as cursor:
            row = await cursor.fetchone()
    status = f"{get_emoji('success')} Enabled" if row and row[0] else f"{get_emoji('error')} Disabled"
    embed = discord.Embed(title="🚫 Antiword Status", description=f"Status: **{status}**\n\nUse `!antiword enable` / `!antiword disable`\n`!antiword add <word>`\n`!antiword wl add <user/role>`", color=0x5865F2)
    await ctx.send(embed=embed)

@antiword.command(name="enable")
@commands.has_permissions(manage_guild=True)
async def antiword_enable(ctx):
    """Enable antiword detection."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('INSERT INTO automod_config (guild_id, antiword_enabled) VALUES (?, 1) ON CONFLICT(guild_id) DO UPDATE SET antiword_enabled = 1', (ctx.guild.id,))
        await db.commit()
    await ctx.send(embed=discord.Embed(description=f"{get_emoji('success')} Antiword has been **enabled**.", color=THEME_COLOR))

@antiword.command(name="disable")
@commands.has_permissions(manage_guild=True)
async def antiword_disable(ctx):
    """Disable antiword detection."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE automod_config SET antiword_enabled = 0 WHERE guild_id = ?', (ctx.guild.id,))
        await db.commit()
    await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Antiword has been **disabled**.", color=THEME_COLOR))

@antiword.command(name="add")
@commands.has_permissions(manage_guild=True)
async def antiword_add(ctx, *, word: str):
    """Add a bad word to the filter."""
    word = word.lower().strip()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('INSERT OR IGNORE INTO automod_badwords (guild_id, word) VALUES (?, ?)', (ctx.guild.id, word))
        await db.commit()
    await ctx.send(embed=discord.Embed(description=f"{get_emoji('success')} `{word}` has been added to the bad words list.", color=THEME_COLOR))

@antiword.command(name="remove")
@commands.has_permissions(manage_guild=True)
async def antiword_remove(ctx, *, word: str):
    """Remove a bad word from the filter."""
    word = word.lower().strip()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM automod_badwords WHERE guild_id = ? AND word = ?', (ctx.guild.id, word))
        await db.commit()
    await ctx.send(embed=discord.Embed(description=f"{get_emoji('success')} `{word}` has been removed from the bad words list.", color=THEME_COLOR))

@antiword.command(name="list")
@commands.has_permissions(manage_guild=True)
async def antiword_list(ctx):
    """List all bad words."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT word FROM automod_badwords WHERE guild_id = ?', (ctx.guild.id,)) as cursor:
            rows = await cursor.fetchall()
    if not rows:
        return await ctx.send(embed=discord.Embed(description="No bad words configured.", color=THEME_COLOR))
    words = [row[0] for row in rows]
    embed = discord.Embed(title="🚫 Banned Words", description=", ".join(f"`{w}`" for w in words), color=0x5865F2)
    await ctx.send(embed=embed)

@antiword.group(name="wl", invoke_without_command=True)
@commands.has_permissions(manage_guild=True)
async def antiword_wl(ctx):
    """Manage the antiword whitelist."""
    await ctx.send(embed=discord.Embed(description="Usage: `!antiword wl add <user/role>` or `!antiword wl remove <user/role>` or `!antiword wl list`", color=THEME_COLOR))

@antiword_wl.command(name="add")
@commands.has_permissions(manage_guild=True)
async def antiword_wl_add(ctx, target: discord.Member | discord.Role = None):
    """Add a user or role to the antiword whitelist."""
    if target is None:
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Please mention a user or role.", color=THEME_COLOR))
    t_type = "role" if isinstance(target, discord.Role) else "user"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('INSERT OR IGNORE INTO automod_whitelist (guild_id, target_id, target_type, module) VALUES (?, ?, ?, ?)', (ctx.guild.id, target.id, t_type, 'antiword'))
        await db.commit()
    await ctx.send(embed=discord.Embed(description=f"{get_emoji('success')} {target.mention} has been whitelisted from antiword.", color=THEME_COLOR))

@antiword_wl.command(name="remove")
@commands.has_permissions(manage_guild=True)
async def antiword_wl_remove(ctx, target: discord.Member | discord.Role = None):
    """Remove a user or role from the antiword whitelist."""
    if target is None:
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('error')} Please mention a user or role.", color=THEME_COLOR))
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM automod_whitelist WHERE guild_id = ? AND target_id = ? AND module = ?', (ctx.guild.id, target.id, 'antiword'))
        await db.commit()
    await ctx.send(embed=discord.Embed(description=f"{get_emoji('success')} {target.mention} has been removed from antiword whitelist.", color=THEME_COLOR))

@antiword_wl.command(name="list")
@commands.has_permissions(manage_guild=True)
async def antiword_wl_list(ctx):
    """List all whitelisted users and roles for antiword."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT target_id, target_type FROM automod_whitelist WHERE guild_id = ? AND module = ?', (ctx.guild.id, 'antiword')) as cursor:
            rows = await cursor.fetchall()
    if not rows:
        return await ctx.send(embed=discord.Embed(description="No antiword whitelist entries.", color=THEME_COLOR))
    lines = []
    for tid, ttype in rows:
        if ttype == 'role':
            r = ctx.guild.get_role(tid)
            lines.append(f"{get_emoji('roles')} {r.mention if r else f'Unknown ({tid})'}") 
        else:
            lines.append(f"{get_emoji('profiles')} <@{tid}>")
    embed = discord.Embed(title="🚫 Antiword Whitelist", description="\n".join(lines), color=0x5865F2)
    await ctx.send(embed=embed)

# --- Automod Punishment Commands ---
class MuteDurationModal(discord.ui.Modal, title="⏱️ Mute Duration"):
    duration = discord.ui.TextInput(
        label="Duration (in minutes)",
        placeholder="e.g. 5",
        required=True,
        max_length=4
    )

    def __init__(self, guild_id: int, module: str, action: str):
        super().__init__()
        self.guild_id = guild_id
        self.module = module
        self.action = action

    async def on_submit(self, interaction: discord.Interaction):
        try:
            mins = int(self.duration.value)
        except ValueError:
            return await interaction.response.send_message(embed=discord.Embed(description=f"{get_emoji('error')} Please enter a valid number.", color=THEME_COLOR), ephemeral=True)
        if mins < 1 or mins > 1440:
            return await interaction.response.send_message(embed=discord.Embed(description=f"{get_emoji('error')} Duration must be between 1 and 1440 minutes.", color=THEME_COLOR), ephemeral=True)
        async with aiosqlite.connect(DB_PATH) as db:
            if self.action == 'add':
                await db.execute('INSERT OR REPLACE INTO automod_punishments (guild_id, module, punishment, mute_duration) VALUES (?, ?, ?, ?)', (self.guild_id, self.module, 'mute', mins))
                await db.commit()
                await interaction.response.send_message(embed=discord.Embed(description=f"{get_emoji('success')} **Mute ({mins} min)** punishment added for `{self.module}`.", color=THEME_COLOR), ephemeral=False)
            else:
                await db.execute('DELETE FROM automod_punishments WHERE guild_id = ? AND module = ? AND punishment = ?', (self.guild_id, self.module, 'mute'))
                await db.commit()
                await interaction.response.send_message(embed=discord.Embed(description=f"{get_emoji('success')} **Mute** punishment removed from `{self.module}`.", color=THEME_COLOR), ephemeral=False)

class PunishmentView(discord.ui.View):
    def __init__(self, guild_id: int, module: str, action: str):
        super().__init__(timeout=60)
        self.guild_id = guild_id
        self.module = module
        self.action = action

    @discord.ui.button(label="Delete Message", emoji=get_ui_emoji("delete"), style=discord.ButtonStyle.grey)
    async def btn_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with aiosqlite.connect(DB_PATH) as db:
            if self.action == 'add':
                await db.execute('INSERT OR IGNORE INTO automod_punishments (guild_id, module, punishment) VALUES (?, ?, ?)', (self.guild_id, self.module, 'delete'))
            else:
                await db.execute('DELETE FROM automod_punishments WHERE guild_id = ? AND module = ? AND punishment = ?', (self.guild_id, self.module, 'delete'))
            await db.commit()
        verb = "added to" if self.action == 'add' else "removed from"
        await interaction.response.send_message(embed=discord.Embed(description=f"{get_emoji('success')} **Delete Message** punishment {verb} `{self.module}`.", color=THEME_COLOR), ephemeral=False)

    @discord.ui.button(label="Kick", emoji=get_ui_emoji("kick"), style=discord.ButtonStyle.red)
    async def btn_kick(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with aiosqlite.connect(DB_PATH) as db:
            if self.action == 'add':
                await db.execute('INSERT OR IGNORE INTO automod_punishments (guild_id, module, punishment) VALUES (?, ?, ?)', (self.guild_id, self.module, 'kick'))
            else:
                await db.execute('DELETE FROM automod_punishments WHERE guild_id = ? AND module = ? AND punishment = ?', (self.guild_id, self.module, 'kick'))
            await db.commit()
        verb = "added to" if self.action == 'add' else "removed from"
        await interaction.response.send_message(embed=discord.Embed(description=f"{get_emoji('success')} **Kick** punishment {verb} `{self.module}`.", color=THEME_COLOR), ephemeral=False)

    @discord.ui.button(label="Mute", emoji=get_ui_emoji("mute"), style=discord.ButtonStyle.blurple)
    async def btn_mute(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.action == 'add':
            await interaction.response.send_modal(MuteDurationModal(self.guild_id, self.module, self.action))
        else:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute('DELETE FROM automod_punishments WHERE guild_id = ? AND module = ? AND punishment = ?', (self.guild_id, self.module, 'mute'))
                await db.commit()
            await interaction.response.send_message(embed=discord.Embed(description=f"{get_emoji('success')} **Mute** punishment removed from `{self.module}`.", color=THEME_COLOR), ephemeral=False)

@bot.hybrid_group(invoke_without_command=True)
@commands.has_permissions(manage_guild=True)
async def automod(ctx):
    """Configure and manage automod punishments."""
    await ctx.send(embed=discord.Embed(description="Usage: `!automod punishment <antispam/antilink/antiword> <add/remove>`", color=THEME_COLOR))

@automod.group(name="punishment", invoke_without_command=True)
@commands.has_permissions(manage_guild=True)
async def automod_punishment(ctx):
    """Configure punishment actions for automod."""
    await ctx.send(embed=discord.Embed(description="Usage: `!automod punishment <antispam/antilink/antiword> <add/remove>`", color=THEME_COLOR))

@automod_punishment.command(name="antispam")
@commands.has_permissions(manage_guild=True)
async def automod_punishment_antispam(ctx, action: str = None):
    """Configure punishment actions for antispam."""
    if action not in ('add', 'remove'):
        # Show current punishments
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT punishment, mute_duration FROM automod_punishments WHERE guild_id = ? AND module = ?', (ctx.guild.id, 'antispam')) as cursor:
                rows = await cursor.fetchall()
        if rows:
            plist = ", ".join([f"`{p}` ({d} min)" if p == 'mute' else f"`{p}`" for p, d in rows])
        else:
            plist = "`delete`, `mute (1 min)` (defaults)"
        embed = discord.Embed(title=f"{get_emoji('antinuke')} Antispam Punishments", description=f"Current: {plist}\n\nUse `!automod punishment antispam add` or `remove`", color=0x5865F2)
        return await ctx.send(embed=embed)
    embed = discord.Embed(
        title=f"{get_emoji('antinuke')} {'Add' if action == 'add' else 'Remove'} Antispam Punishment",
        description="Select a punishment below:",
        color=0x5865F2
    )
    await ctx.send(embed=embed, view=PunishmentView(ctx.guild.id, 'antispam', action))

@automod_punishment.command(name="antilink")
@commands.has_permissions(manage_guild=True)
async def automod_punishment_antilink(ctx, action: str = None):
    """Configure punishment actions for antilink."""
    if action not in ('add', 'remove'):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT punishment, mute_duration FROM automod_punishments WHERE guild_id = ? AND module = ?', (ctx.guild.id, 'antilink')) as cursor:
                rows = await cursor.fetchall()
        if rows:
            plist = ", ".join([f"`{p}` ({d} min)" if p == 'mute' else f"`{p}`" for p, d in rows])
        else:
            plist = "`delete` (default)"
        embed = discord.Embed(title=f"{get_emoji('link')} Antilink Punishments", description=f"Current: {plist}\n\nUse `!automod punishment antilink add` or `remove`", color=0x5865F2)
        return await ctx.send(embed=embed)
    embed = discord.Embed(
        title=f"{get_emoji('link')} {'Add' if action == 'add' else 'Remove'} Antilink Punishment",
        description="Select a punishment below:",
        color=0x5865F2
    )
    await ctx.send(embed=embed, view=PunishmentView(ctx.guild.id, 'antilink', action))

@automod_punishment.command(name="antiword")
@commands.has_permissions(manage_guild=True)
async def automod_punishment_antiword(ctx, action: str = None):
    """Configure punishment actions for antiword."""
    if action not in ('add', 'remove'):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT punishment, mute_duration FROM automod_punishments WHERE guild_id = ? AND module = ?', (ctx.guild.id, 'antiword')) as cursor:
                rows = await cursor.fetchall()
        if rows:
            plist = ", ".join([f"`{p}` ({d} min)" if p == 'mute' else f"`{p}`" for p, d in rows])
        else:
            plist = "`delete` (default)"
        embed = discord.Embed(title="🚫 Antiword Punishments", description=f"Current: {plist}\n\nUse `!automod punishment antiword add` or `remove`", color=0x5865F2)
        return await ctx.send(embed=embed)
    embed = discord.Embed(
        title=f"🚫 {'Add' if action == 'add' else 'Remove'} Antiword Punishment",
        description="Select a punishment below:",
        color=0x5865F2
    )
    await ctx.send(embed=embed, view=PunishmentView(ctx.guild.id, 'antiword', action))
# --- Autoresponder Commands Relocated ---

# --- Server Info Command ---
@bot.hybrid_command(aliases=['si'])
async def serverinfo(ctx):
    """Show detailed information about this server."""
    guild = ctx.guild
    
    embed = discord.Embed(color=THEME_COLOR)
    embed.set_author(name=f"{guild.name}'s Information", icon_url=guild.icon.url if guild.icon else None)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
        
    # About Server
    created_dt = discord.utils.format_dt(guild.created_at, 'R')
    owner_str = guild.owner.mention if guild.owner else "Unknown"
    embed.add_field(
        name=f"{get_emoji('server')} __About Server__",
        value=f"> **Name :** {guild.name}\n"
              f"> **Server ID :** {guild.id}\n"
              f"> **Owner [ {get_emoji('owner')} ] :** {owner_str}\n"
              f"> **Created At :** {created_dt}\n"
              f"> **Total Members :** {guild.member_count}",
        inline=False
    )
    
    # Description
    if guild.description:
        embed.add_field(name=f"{get_emoji('rename')} __Description__", value=f"> {guild.description}", inline=False)
    
    # Features
    if guild.features:
        feat_str = "\n".join([f"> {get_emoji('settings')} {f.replace('_', ' ').title()}" for f in guild.features])
    else:
        feat_str = "> No special features."
    # Ensure features string isn't too long
    if len(feat_str) > 1024:
        feat_str = feat_str[:1020] + "..."
    embed.add_field(name="📎 __Features__", value=feat_str, inline=False)
    
    # Extras
    afk_chan = guild.afk_channel.name if guild.afk_channel else "None"
    afk_timeout = int(guild.afk_timeout / 60)
    sys_chan = guild.system_channel.mention if guild.system_channel else "None"
    embed.add_field(
        name=f"{get_emoji('settings')} __Extras__",
        value=f"> **Verification Level :** {str(guild.verification_level).title()}\n"
              f"> **AFK Channel :** {afk_chan}\n"
              f"> **AFK Timeout :** {afk_timeout} mins\n"
              f"> **System Channel :** {sys_chan}\n"
              f"> **NSFW level :** {str(guild.nsfw_level.name).title()}\n"
              f"> **Explicit Content Filter :** {str(guild.explicit_content_filter.name).replace('_', ' ').title()}\n"
              f"> **Max Talk Bitrate :** {guild.bitrate_limit} bps",
        inline=False
    )
    
    # Members
    humans = len([m for m in guild.members if not m.bot])
    bots = len([m for m in guild.members if m.bot])
    embed.add_field(
        name="👥 __Members__",
        value=f"> **Total Members :** {guild.member_count}\n"
              f"> **Humans :** {humans}\n"
              f"> **Bots :** {bots}",
        inline=False
    )
    
    # Channels
    stage_channels = len([c for c in guild.channels if isinstance(c, discord.StageChannel)])
    embed.add_field(
        name=f"{get_emoji('logging')} __Channels__",
        value=f"> **Categories :** {len(guild.categories)}\n"
              f"> **Text Channels :** {len(guild.text_channels)}\n"
              f"> **Voice Channels :** {len(guild.voice_channels)}\n"
              f"> **Stage Channels :** {stage_channels}",
        inline=False
    )
    
    # Emojis Info
    reg_emojis = len([e for e in guild.emojis if not e.animated])
    anim_emojis = len([e for e in guild.emojis if e.animated])
    stickers = len(guild.stickers)
    tot_emojis = len(guild.emojis) + stickers
    embed.add_field(
        name=f"{get_emoji('giveaway')} __Emojis__",
        value=f"> **Regular emojis :** {reg_emojis}\n"
              f"> **Animated emojis :** {anim_emojis}\n"
              f"> **Stickers :** {stickers}\n"
              f"> **Total :** {tot_emojis}",
        inline=False
    )
    
    # Boosts Status
    boosters = len(guild.premium_subscribers)
    booster_role = guild.premium_subscriber_role.mention if guild.premium_subscriber_role else "`None`"
    embed.add_field(
        name="🔮 __Boosts__",
        value=f"> **Boost Level :** {guild.premium_tier} Level\n"
              f"> **Boost count :** {guild.premium_subscription_count}\n"
              f"> **Boosters :** {boosters}\n"
              f"> **Booster Role :** {booster_role}",
        inline=False
    )
    
    # Roles
    roles = [role.mention for role in reversed(guild.roles) if not role.is_default()]
    if not roles:
        roles_val = "> `None`"
    elif len(roles) > 15:
        roles_val = f"> {', '.join(roles[:15])} and {len(roles) - 15} more"
    else:
        roles_val = f"> {', '.join(roles)}"
        
    embed.add_field(
        name=f"{get_emoji('moderation')} __Roles__",
        value=roles_val,
        inline=False
    )
    
    embed.set_footer(text=f"Requested by {ctx.author.name}", icon_url=ctx.author.display_avatar.url)
    embed.timestamp = discord.utils.utcnow()
    
    await ctx.send(embed=embed)

@bot.hybrid_command(aliases=['sicon', 'serveravatar', 'guildicon'])
async def servericon(ctx):
    """Show the server's icon with a download link."""
    guild = ctx.guild
    if not guild.icon:
        return await ctx.send(embed=discord.Embed(
            description=f"{get_emoji('error')} This server does not have an icon set.",
            color=THEME_COLOR
        ))
    
    guild.icon.url
    # Get high-quality versions
    png_url = guild.icon.replace(format='png', size=4096).url
    jpg_url = guild.icon.replace(format='jpg', size=4096).url
    webp_url = guild.icon.replace(format='webp', size=4096).url
    gif_url = guild.icon.replace(format='gif', size=4096).url if guild.icon.is_animated() else None
    
    embed = discord.Embed(
        title=f"{get_emoji('server')} {guild.name}'s Server Icon",
        color=THEME_COLOR
    )
    embed.set_image(url=guild.icon.replace(size=4096).url)
    
    # Download links in description
    links = f"[PNG]({png_url}) • [JPG]({jpg_url}) • [WEBP]({webp_url})"
    if gif_url:
        links += f" • [GIF]({gif_url})"
    embed.description = f"**Download:** {links}"
    
    embed.set_footer(text=f"Requested by {ctx.author.name} • Click a format above to download", icon_url=ctx.author.display_avatar.url)
    embed.timestamp = discord.utils.utcnow()
    await ctx.send(embed=embed)

@bot.hybrid_command(aliases=['sbanner', 'guildbanner'])
async def serverbanner(ctx):
    """Show the server's banner with a download link."""
    guild = ctx.guild
    if not guild.banner:
        return await ctx.send(embed=discord.Embed(
            description=f"{get_emoji('error')} This server does not have a banner set.",
            color=THEME_COLOR
        ))
    
    guild.banner.url
    # Get high-quality versions
    png_url = guild.banner.replace(format='png', size=4096).url
    jpg_url = guild.banner.replace(format='jpg', size=4096).url
    webp_url = guild.banner.replace(format='webp', size=4096).url
    gif_url = guild.banner.replace(format='gif', size=4096).url if guild.banner.is_animated() else None
    
    embed = discord.Embed(
        title=f"{get_emoji('server')} {guild.name}'s Server Banner",
        color=THEME_COLOR
    )
    embed.set_image(url=guild.banner.replace(size=4096).url)
    
    # Download links in description
    links = f"[PNG]({png_url}) • [JPG]({jpg_url}) • [WEBP]({webp_url})"
    if gif_url:
        links += f" • [GIF]({gif_url})"
    embed.description = f"**Download:** {links}"
    
    embed.set_footer(text=f"Requested by {ctx.author.name} • Click a format above to download", icon_url=ctx.author.display_avatar.url)
    embed.timestamp = discord.utils.utcnow()
    await ctx.send(embed=embed)
async def fetch_image_bytes(url_or_attachment, ctx=None):
    if ctx and hasattr(ctx, "message") and ctx.message and ctx.message.attachments:
        return await ctx.message.attachments[0].read()
    elif url_or_attachment:
        url = str(url_or_attachment).strip()
        if url.startswith(("http://", "https://")):
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        return await resp.read()
    return None

async def fetch_image(ctx, url_or_attachment):
    return await fetch_image_bytes(url_or_attachment, ctx)

async def get_custom_branding(guild_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT avatar_url, banner_url, description, nickname FROM custom_branding WHERE guild_id = ?', (guild_id,)) as cursor:
            row = await cursor.fetchone()
    if row:
        return {
            "avatar_url": row[0],
            "banner_url": row[1],
            "description": row[2],
            "nickname": row[3]
        }
    return {"avatar_url": None, "banner_url": None, "description": None, "nickname": None}

async def update_custom_branding_field(guild_id: int, field: str, value: str):
    allowed_fields = {"avatar_url", "banner_url", "description", "nickname"}
    if field not in allowed_fields:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f'''
            INSERT INTO custom_branding (guild_id, {field})
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET {field} = ?
        ''', (guild_id, value, value))
        await db.commit()

async def reset_custom_branding(guild_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM custom_branding WHERE guild_id = ?', (guild_id,))
        await db.commit()

def get_branding_status_embed(guild: discord.Guild, data: dict) -> discord.Embed:
    bot_member = guild.me
    embed = discord.Embed(
        title=f"🎨 Bot Custom Branding — {guild.name}",
        description=f"Server-specific custom branding status for **{guild.me.display_name}**.",
        color=THEME_COLOR
    )
    
    avatar_val = data.get("avatar_url") or (bot_member.display_avatar.url if bot_member.display_avatar else "Default Avatar")
    banner_val = data.get("banner_url") or "Default Banner"
    desc_val = data.get("description") or "*No custom description set.*"
    nick_val = data.get("nickname") or bot_member.nick or "*No custom nickname set.*"

    embed.add_field(name="🏷️ Nickname", value=f"`{nick_val}`" if nick_val != "*No custom nickname set.*" else nick_val, inline=True)
    embed.add_field(name="📝 Description / Bio", value=desc_val[:1024], inline=False)
    embed.add_field(name="🖼️ Avatar URL", value=f"[Avatar Link]({avatar_val})" if avatar_val.startswith("http") else avatar_val, inline=True)
    embed.add_field(name="🎨 Banner URL", value=f"[Banner Link]({banner_val})" if banner_val.startswith("http") else banner_val, inline=True)

    if avatar_val.startswith("http"):
        embed.set_thumbnail(url=avatar_val)
    elif bot_member.display_avatar:
        embed.set_thumbnail(url=bot_member.display_avatar.url)

    if banner_val.startswith("http"):
        embed.set_image(url=banner_val)

    embed.set_footer(text=f"Guild ID: {guild.id} • Use !customize to edit")
    return embed

class CustomBrandingAvatarModal(discord.ui.Modal, title="Set Server Bot Avatar"):
    avatar_url = discord.ui.TextInput(
        label="Avatar Image URL",
        placeholder="https://example.com/avatar.png",
        required=True,
        max_length=500
    )

    def __init__(self, view):
        super().__init__()
        self.view = view

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        url = self.avatar_url.value.strip()
        image_bytes = await fetch_image_bytes(url)
        if not image_bytes:
            return await interaction.followup.send(embed=discord.Embed(description=f"{get_emoji('warn')} Could not fetch image from provided URL.", color=0xFEE75C), ephemeral=True)
        
        try:
            await interaction.guild.me.edit(avatar=image_bytes)
        except Exception as e:
            logging.warning(f"Guild me avatar edit note: {e}")

        await update_custom_branding_field(interaction.guild.id, "avatar_url", url)
        await interaction.followup.send(embed=discord.Embed(description=f"{get_emoji('success')} Successfully updated server bot avatar!", color=THEME_COLOR), ephemeral=True)
        await self.view.refresh_dashboard(interaction)

class CustomBrandingBannerModal(discord.ui.Modal, title="Set Server Bot Banner"):
    banner_url = discord.ui.TextInput(
        label="Banner Image URL",
        placeholder="https://example.com/banner.png",
        required=True,
        max_length=500
    )

    def __init__(self, view):
        super().__init__()
        self.view = view

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        url = self.banner_url.value.strip()
        image_bytes = await fetch_image_bytes(url)
        if not image_bytes:
            return await interaction.followup.send(embed=discord.Embed(description=f"{get_emoji('warn')} Could not fetch image from provided URL.", color=0xFEE75C), ephemeral=True)
        
        try:
            await interaction.guild.me.edit(banner=image_bytes)
        except Exception as e:
            logging.warning(f"Guild me banner edit note: {e}")

        await update_custom_branding_field(interaction.guild.id, "banner_url", url)
        await interaction.followup.send(embed=discord.Embed(description=f"{get_emoji('success')} Successfully updated server bot banner!", color=THEME_COLOR), ephemeral=True)
        await self.view.refresh_dashboard(interaction)

class CustomBrandingDescriptionModal(discord.ui.Modal, title="Set Server Bot Description"):
    description_text = discord.ui.TextInput(
        label="Description / Bio",
        style=discord.TextStyle.paragraph,
        placeholder="Enter custom description or bio for this bot...",
        required=True,
        max_length=1000
    )

    def __init__(self, current_desc, view):
        super().__init__()
        self.description_text.default = current_desc or ""
        self.view = view

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        bio_text = self.description_text.value.strip()
        
        if len(bio_text) <= 190:
            try:
                await interaction.guild.me.edit(bio=bio_text)
            except Exception as e:
                logging.warning(f"Guild me bio edit note: {e}")

        await update_custom_branding_field(interaction.guild.id, "description", bio_text)
        await interaction.followup.send(embed=discord.Embed(description=f"{get_emoji('success')} Successfully updated server bot description!", color=THEME_COLOR), ephemeral=True)
        await self.view.refresh_dashboard(interaction)

class CustomBrandingNicknameModal(discord.ui.Modal, title="Set Server Bot Nickname"):
    nickname_text = discord.ui.TextInput(
        label="Bot Nickname",
        placeholder="e.g., Vireon Support",
        required=True,
        max_length=32
    )

    def __init__(self, current_nick, view):
        super().__init__()
        self.nickname_text.default = current_nick or ""
        self.view = view

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        nick_val = self.nickname_text.value.strip()
        try:
            await interaction.guild.me.edit(nick=nick_val)
        except Exception as e:
            logging.warning(f"Guild me nick edit note: {e}")

        await update_custom_branding_field(interaction.guild.id, "nickname", nick_val)
        await interaction.followup.send(embed=discord.Embed(description=f"{get_emoji('success')} Successfully updated server bot nickname to `{nick_val}`!", color=THEME_COLOR), ephemeral=True)
        await self.view.refresh_dashboard(interaction)

class CustomBrandingDashboardView(discord.ui.View):
    def __init__(self, author: discord.Member, message=None):
        super().__init__(timeout=180)
        self.author = author
        self.message = message

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(embed=discord.Embed(description=f"{get_emoji('error')} Only the command author can use this dashboard.", color=THEME_COLOR), ephemeral=True)
            return False
        return True

    async def refresh_dashboard(self, interaction: discord.Interaction):
        data = await get_custom_branding(interaction.guild.id)
        embed = get_branding_status_embed(interaction.guild, data)
        try:
            if self.message:
                await self.message.edit(embed=embed, view=self)
            elif interaction.message:
                await interaction.message.edit(embed=embed, view=self)
        except Exception:
            pass

    @discord.ui.button(label="Set Avatar", emoji="🖼️", style=discord.ButtonStyle.primary, row=0)
    async def set_avatar_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = CustomBrandingAvatarModal(self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Set Banner", emoji="🎨", style=discord.ButtonStyle.primary, row=0)
    async def set_banner_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = CustomBrandingBannerModal(self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Set Description", emoji="📝", style=discord.ButtonStyle.primary, row=0)
    async def set_desc_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = await get_custom_branding(interaction.guild.id)
        modal = CustomBrandingDescriptionModal(data.get("description"), self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Set Nickname", emoji="🏷️", style=discord.ButtonStyle.primary, row=1)
    async def set_nick_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = await get_custom_branding(interaction.guild.id)
        modal = CustomBrandingNicknameModal(data.get("nickname"), self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="View Status", emoji="🔍", style=discord.ButtonStyle.secondary, row=1)
    async def view_status_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = await get_custom_branding(interaction.guild.id)
        embed = get_branding_status_embed(interaction.guild, data)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Reset Branding", emoji="🔄", style=discord.ButtonStyle.danger, row=1)
    async def reset_branding_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            await interaction.guild.me.edit(avatar=None, banner=None, bio=None, nick=None)
        except Exception:
            pass
        await reset_custom_branding(interaction.guild.id)
        await interaction.followup.send(embed=discord.Embed(description=f"{get_emoji('success')} Successfully reset server bot branding!", color=THEME_COLOR), ephemeral=True)
        await self.refresh_dashboard(interaction)

@bot.hybrid_group(name="customize", invoke_without_command=True)
@commands.has_permissions(administrator=True)
async def customize(ctx):
    """Server-specific bot custom branding dashboard."""
    if ctx.invoked_subcommand is not None:
        return
    data = await get_custom_branding(ctx.guild.id)
    embed = get_branding_status_embed(ctx.guild, data)
    view = CustomBrandingDashboardView(ctx.author)
    view.message = await ctx.send(embed=embed, view=view)

@customize.command(name="banner")
@commands.has_permissions(administrator=True)
async def customize_banner(ctx, url: str = None):
    """Customize the bot's server-specific banner."""
    image_bytes = await fetch_image_bytes(url, ctx)
    if not image_bytes:
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('warn')} Please provide an image attachment or URL.", color=0xFEE75C))
    
    img_url = url
    if ctx.message and ctx.message.attachments:
        img_url = ctx.message.attachments[0].url

    try:
        await ctx.guild.me.edit(banner=image_bytes)
    except discord.Forbidden:
        pass
    except Exception as e:
        logging.warning(f"Guild me banner update note: {e}")

    await update_custom_branding_field(ctx.guild.id, "banner_url", img_url)
    await ctx.send(embed=discord.Embed(description=f"{get_emoji('success')} Successfully updated server bot banner!", color=THEME_COLOR))

@customize.command(name="avatar")
@commands.has_permissions(administrator=True)
async def customize_avatar(ctx, url: str = None):
    """Customize the bot's server-specific avatar."""
    image_bytes = await fetch_image_bytes(url, ctx)
    if not image_bytes:
        return await ctx.send(embed=discord.Embed(description=f"{get_emoji('warn')} Please provide an image attachment or URL.", color=0xFEE75C))
    
    img_url = url
    if ctx.message and ctx.message.attachments:
        img_url = ctx.message.attachments[0].url

    try:
        await ctx.guild.me.edit(avatar=image_bytes)
    except discord.Forbidden:
        pass
    except Exception as e:
        logging.warning(f"Guild me avatar update note: {e}")

    await update_custom_branding_field(ctx.guild.id, "avatar_url", img_url)
    await ctx.send(embed=discord.Embed(description=f"{get_emoji('success')} Successfully updated server bot avatar!", color=THEME_COLOR))

@customize.command(name="description", aliases=["bio"])
@commands.has_permissions(administrator=True)
async def customize_description(ctx, *, description_text: str):
    """Customize the bot's server-specific description or bio."""
    if len(description_text) <= 190:
        try:
            await ctx.guild.me.edit(bio=description_text)
        except Exception as e:
            logging.warning(f"Guild me bio update note: {e}")

    await update_custom_branding_field(ctx.guild.id, "description", description_text)
    await ctx.send(embed=discord.Embed(description=f"{get_emoji('success')} Successfully updated server bot description!", color=THEME_COLOR))

@customize.command(name="nickname", aliases=["nick", "name"])
@commands.has_permissions(administrator=True)
async def customize_nickname(ctx, *, nickname_text: str):
    """Customize the bot's per-server nickname."""
    try:
        await ctx.guild.me.edit(nick=nickname_text)
    except Exception as e:
        logging.warning(f"Guild me nick update note: {e}")

    await update_custom_branding_field(ctx.guild.id, "nickname", nickname_text)
    await ctx.send(embed=discord.Embed(description=f"{get_emoji('success')} Successfully updated server bot nickname to `{nickname_text}`!", color=THEME_COLOR))

@customize.command(name="show", aliases=["status", "view"])
@commands.has_permissions(administrator=True)
async def customize_show(ctx):
    """View current server-specific bot branding settings."""
    data = await get_custom_branding(ctx.guild.id)
    embed = get_branding_status_embed(ctx.guild, data)
    await ctx.send(embed=embed)

@customize.command(name="reset")
@commands.has_permissions(administrator=True)
async def customize_reset(ctx):
    """Reset server-specific bot branding to default."""
    try:
        await ctx.guild.me.edit(avatar=None, banner=None, bio=None, nick=None)
    except Exception:
        pass
    await reset_custom_branding(ctx.guild.id)
    await ctx.send(embed=discord.Embed(description=f"{get_emoji('success')} Successfully reset server profile!", color=THEME_COLOR))

@bot.hybrid_command(name="view", help="Shows all available view commands.")
async def view_cmd(ctx):
    embed = discord.Embed(
        title="🔍 View Commands",
        description="Here are all the available view commands:",
        color=THEME_COLOR
    )
    commands_list = [
        f"`{ctx.prefix or '!'}viewuser [user]` - View a user's profile and stats.",
        f"`{ctx.prefix or '!'}viewgames [user]` - View the games a user plays.",
        f"`{ctx.prefix or '!'}viewsocial [user]` - View a user's social links.",
        f"`{ctx.prefix or '!'}viewperms [user]` - View a user's permissions.",
        f"`{ctx.prefix or '!'}list admins` - View the server's administrators.",
        f"`{ctx.prefix or '!'}viewroles [user]` - View a user's roles."
    ]
    embed.add_field(name="Commands", value="\n".join(commands_list), inline=False)
    await ctx.send(embed=embed)

if __name__ == "__main__":
    if token is None:
        print("[ERROR] DISCORD_TOKEN not found in .env")
        sys.exit(1)

    async def main_startup():
        # Start webserver first
        await run_webserver()
        try:
            await bot.start(token)
        except discord.LoginFailure:
            print("[ERROR] Discord Login Failure: The token is invalid. Continuing to run webserver in interactions-only mode.")
            # Keep loop alive for the webserver
            while True:
                await asyncio.sleep(3600)
        finally:
            # --- Final backup on shutdown ---
            try:
                print("[BACKUP] Performing final backup before shutdown...")
                await database.backup_db_to_discord(bot)
            except Exception as e:
                print(f"[BACKUP] Final backup failed: {e}")

    try:
        asyncio.run(main_startup())
    except KeyboardInterrupt:
        print("\n[OK] Stopping server...")
