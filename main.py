import asyncio
import logging
import os
import random
import shutil
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
FFMPEG_PATH = os.getenv("FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg"

if not TOKEN:
    raise SystemExit("[ERROR] DISCORD_TOKEN not found in environment variables.")

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s"
)

# ============================================================
# VIREON MUSIC - UI / BRANDING
# ============================================================

THEME = 0x1B1D21
SUCCESS = 0x57F287
ERROR = 0xED4245
INFO = 0x5865F2
MUTED = 0x949BA4

FOOTER = "Crafted by Escobar | Hardik"
PREFIX = "-"

# ============================================================
# NO-PREFIX USERS
# ============================================================

NO_PREFIX_USERS: set[int] = set()

for raw_id in os.getenv("NO_PREFIX_USERS", "").split(","):
    raw_id = raw_id.strip()

    if raw_id.isdigit():
        NO_PREFIX_USERS.add(int(raw_id))

# ============================================================
# AUTOPLAY
# ============================================================

AUTOPLAY_DEFAULT = os.getenv(
    "AUTOPLAY_DEFAULT",
    "true"
).lower() in {
    "1",
    "true",
    "yes",
    "on"
}

# ============================================================
# YOUTUBE / YT-DLP
# ============================================================

YDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch1",
    "source_address": "0.0.0.0",
    "extract_flat": False,

    "js_runtimes": {
        "node": {}
    },

    "extractor_args": {
        "youtubepot-bgutilhttp": {
            "base_url": "http://127.0.0.1:4416"
        }
    },
}

FFMPEG_OPTIONS = {
    "before_options": (
        "-reconnect 1 "
        "-reconnect_streamed 1 "
        "-reconnect_delay_max 5"
    ),
    "options": "-vn",
}

# ============================================================
# DISCORD
# ============================================================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix=PREFIX,
    intents=intents,
    help_command=None
)

# ============================================================
# DATA MODELS
# ============================================================

@dataclass
class Track:
    title: str
    webpage_url: str
    duration: int
    requester: discord.Member
    thumbnail: Optional[str] = None
    stream_url: Optional[str] = None


class GuildPlayer:

    def __init__(self, guild_id: int):

        self.guild_id = guild_id

        self.queue: deque[Track] = deque()

        self.current: Optional[Track] = None

        self.voice: Optional[discord.VoiceClient] = None

        self.loop_mode = "off"

        self.volume = 0.80

        self.autoplay = AUTOPLAY_DEFAULT

        self.text_channel: Optional[
            discord.abc.Messageable
        ] = None

        self.history: list[Track] = []

        self.previous_stack: list[Track] = []

        self.player_message: Optional[
            discord.Message
        ] = None

        self.play_lock = asyncio.Lock()

        self.suppress_after = False

        # Playback/progress tracking
        self.started_at = 0.0

        self.paused_at = 0.0

        self.paused_total = 0.0

        self.progress_task: Optional[
            asyncio.Task
        ] = None

        self.generation = 0


players: dict[int, GuildPlayer] = {}


def get_player(guild_id: int) -> GuildPlayer:

    return players.setdefault(
        guild_id,
        GuildPlayer(guild_id)
    )


# ============================================================
# EMBEDS
# ============================================================

def base_embed(
    title: str,
    description: str = "",
    color: int = THEME,
    track: Optional[Track] = None,
) -> discord.Embed:

    embed = discord.Embed(
        title=title,
        description=description,
        color=color
    )

    if track and track.thumbnail:
        embed.set_thumbnail(
            url=track.thumbnail
        )

    embed.set_footer(
        text=FOOTER
    )

    return embed


def error_embed(
    message: str
) -> discord.Embed:

    return base_embed(
        "❌  Something went wrong",
        message,
        ERROR
    )


def success_embed(
    message: str
) -> discord.Embed:

    return base_embed(
        "✓  Command Executed Successfully",
        message,
        SUCCESS
    )


def info_embed(
    message: str
) -> discord.Embed:

    return base_embed(
        "i  Vireon Music",
        message,
        INFO
    )


# ============================================================
# UTILITIES
# ============================================================

def fmt_duration(
    seconds: int
) -> str:

    if not seconds:
        return "LIVE"

    h, rem = divmod(
        int(seconds),
        3600
    )

    m, s = divmod(
        rem,
        60
    )

    if h:
        return f"{h}:{m:02d}:{s:02d}"

    return f"{m}:{s:02d}"


def elapsed_seconds(
    p: GuildPlayer
) -> int:

    if not p.current or not p.started_at:
        return 0

    now = (
        p.paused_at
        if p.paused_at
        else time.monotonic()
    )

    elapsed = max(
        0.0,
        now
        - p.started_at
        - p.paused_total
    )

    if p.current.duration:
        elapsed = min(
            elapsed,
            p.current.duration
        )

    return int(elapsed)


def progress_bar(
    elapsed: int,
    duration: int,
    width: int = 22
) -> str:

    if not duration:
        return (
            "🔘"
            + "─" * (width - 1)
        )

    ratio = max(
        0.0,
        min(
            1.0,
            elapsed / duration
        )
    )

    position = min(
        width - 1,
        int(
            ratio
            * (width - 1)
        )
    )

    chars = ["─"] * width

    chars[position] = "●"

    return "".join(chars)


def playback_line(
    p: GuildPlayer
) -> str:

    if not p.current:
        return ""

    elapsed = elapsed_seconds(p)

    duration = p.current.duration

    if duration:

        return (
            f"`{fmt_duration(elapsed)}` "
            f"{progress_bar(elapsed, duration)} "
            f"`{fmt_duration(duration)}`"
        )

    return (
        f"`{fmt_duration(elapsed)}` "
        f"{progress_bar(elapsed, 0)} "
        f"`LIVE`"
    )


def command_success(
    action: str,
    interaction: Optional[
        discord.Interaction
    ] = None,
) -> discord.Embed:

    if interaction:

        return success_embed(
            f"**{action}**\n\n"
            f"Requested by {interaction.user.mention}"
        )

    return success_embed(action)


# ============================================================
# VOICE
# ============================================================

def user_voice_channel(
    interaction: discord.Interaction
) -> discord.VoiceChannel:

    voice_state = getattr(
        interaction.user,
        "voice",
        None
    )

    if (
        not voice_state
        or not voice_state.channel
    ):
        raise ValueError(
            "Join a voice channel first."
        )

    return voice_state.channel


async def ensure_voice(
    interaction: discord.Interaction,
    p: GuildPlayer,
) -> None:

    channel = user_voice_channel(
        interaction
    )

    if (
        p.voice
        and p.voice.is_connected()
    ):

        if p.voice.channel != channel:
            await p.voice.move_to(
                channel
            )

        return

    p.voice = await channel.connect()


# ============================================================
# YOUTUBE RESOLUTION
# ============================================================

def resolve_track(
    query: str,
    requester: discord.Member
) -> Track:

    with yt_dlp.YoutubeDL(
        YDL_OPTIONS
    ) as ydl:

        info = ydl.extract_info(
            query,
            download=False
        )

        if info and "entries" in info:

            info = next(
                (
                    entry
                    for entry in info["entries"]
                    if entry
                ),
                None
            )

        if not info:
            raise ValueError(
                "No playable result was found."
            )

        webpage_url = (
            info.get("webpage_url")
            or info.get("original_url")
            or query
        )

        return Track(
            title=info.get(
                "title",
                "Unknown title"
            ),
            webpage_url=webpage_url,
            duration=info.get(
                "duration"
            ) or 0,
            requester=requester,
            thumbnail=info.get(
                "thumbnail"
            ),
            stream_url=info.get(
                "url"
            ),
        )


def refresh_stream(
    track: Track
) -> Track:

    with yt_dlp.YoutubeDL(
        YDL_OPTIONS
    ) as ydl:

        info = ydl.extract_info(
            track.webpage_url,
            download=False
        )

        if info and "entries" in info:

            info = next(
                (
                    entry
                    for entry in info["entries"]
                    if entry
                ),
                None
            )

        if not info:
            raise ValueError(
                "The track is no longer available."
            )

        track.stream_url = info.get(
            "url"
        )

        track.title = info.get(
            "title",
            track.title
        )

        track.duration = info.get(
            "duration"
        ) or track.duration

        track.thumbnail = (
            info.get("thumbnail")
            or track.thumbnail
        )

        return track
