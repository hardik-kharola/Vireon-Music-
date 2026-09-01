import asyncio
import logging
import os
import random
import shutil
import time
import json
from urllib.parse import quote
from urllib.request import Request, urlopen
from collections import deque
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp


# ============================================================
# CONFIG
# ============================================================

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
FFMPEG_PATH = os.getenv("FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg"

# Railway Variable:
# NO_PREFIX_USERS=123456789012345678,987654321098765432
NO_PREFIX_USERS = {
    int(x.strip())
    for x in os.getenv("NO_PREFIX_USERS", "").split(",")
    if x.strip().isdigit()
}

if not TOKEN:
    raise SystemExit("[ERROR] DISCORD_TOKEN not found.")

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s"
)


# ============================================================
# COLORS / THEME
# ============================================================

THEME = 0x1B1D21
SUCCESS = 0x57F287
ERROR = 0xED4245
INFO = 0x5865F2


# ============================================================
# PREFIX SYSTEM
# ============================================================

def dynamic_prefix(bot, message):
    """
    Everyone can use:
        -play song

    Users listed in NO_PREFIX_USERS can also use:
        play song
    """

    if message.author.id in NO_PREFIX_USERS:
        return ["-", ""]

    return "-"


# ============================================================
# BOT
# ============================================================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix=dynamic_prefix,
    intents=intents,
    help_command=None
)


# ============================================================
# CUSTOM MUSIC EMOJIS
# ============================================================

EMOJI_DIR = Path(__file__).resolve().parent / "emojis"

MUSIC_EMOJI_FILES = {
    "play": "play.png",
    "back": "back.png",
    "pause": "pause.png",
    "skip": "skip.png",
    "loop": "loop.png",
    "volume_down": "volume_down.png",
    "rewind": "rewind.png",
    "favorite": "favorite.png",
    "forward": "forward.png",
    "volume_up": "volume_up.png",
    "voice": "voice.png",
    "shuffle": "shuffle.png",
    "stop": "stop.png",
    "clear": "clear.png",
    "playlist": "playlist.png",
}

MUSIC_EMOJIS_BY_GUILD: dict[int, dict[str, discord.PartialEmoji]] = {}


async def ensure_music_emojis(guild: discord.Guild):
    """Create/reuse the 15 player emojis. Never crash the bot if unavailable."""
    result = {}

    for name, filename in MUSIC_EMOJI_FILES.items():
        # Support both our generated names and manually named Discord emojis.
        emoji = (
            discord.utils.get(guild.emojis, name=f"vms_{name}")
            or discord.utils.get(guild.emojis, name=name)
        )

        if emoji is None:
            path = EMOJI_DIR / filename

            if not path.exists():
                logging.warning(
                    "[EMOJIS] Missing PNG: %s",
                    path
                )
                continue

            try:
                with path.open("rb") as fp:
                    emoji = await guild.create_custom_emoji(
                        name=f"vms_{name}",
                        image=fp.read(),
                        reason="Vireon Music player controls"
                    )

                logging.info(
                    "[EMOJIS] Created %s -> %s",
                    name,
                    emoji.id
                )

            except discord.HTTPException as exc:
                logging.warning(
                    "[EMOJIS] Could not create %s in %s: %s",
                    name,
                    guild.name,
                    exc
                )
                continue

            except Exception as exc:
                logging.warning(
                    "[EMOJIS] %s failed in %s: %s",
                    name,
                    guild.name,
                    exc
                )
                continue

        result[name] = discord.PartialEmoji(
            name=emoji.name,
            id=emoji.id,
            animated=emoji.animated
        )

    return result


# ============================================================
# YOUTUBE / FFMPEG
# ============================================================

# YouTube cookies are OPTIONAL.
# Set USE_YOUTUBE_COOKIES=true only when YOUTUBE_COOKIES contains
# a current, valid Netscape-format cookies.txt.
YOUTUBE_COOKIES = os.getenv("YOUTUBE_COOKIES", "").strip()
USE_YOUTUBE_COOKIES = os.getenv("USE_YOUTUBE_COOKIES", "false").strip().lower() in {"1", "true", "yes", "on"}
COOKIE_FILE = "/tmp/youtube_cookies.txt"

if USE_YOUTUBE_COOKIES and YOUTUBE_COOKIES:
    try:
        with open(COOKIE_FILE, "w", encoding="utf-8") as cookie_fp:
            cookie_fp.write(YOUTUBE_COOKIES)
        logging.info("[OK] YouTube cookies enabled and loaded.")
    except Exception as exc:
        logging.error("[ERROR] Could not create YouTube cookie file: %s", exc)

YDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch1",
    "source_address": "0.0.0.0",
    "extract_flat": False,

    **({"cookiefile": COOKIE_FILE} if USE_YOUTUBE_COOKIES and os.path.exists(COOKIE_FILE) else {}),

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
# DATA
# ============================================================

@dataclass
class Track:
    title: str
    webpage_url: str
    duration: int
    requester: discord.Member
    thumbnail: Optional[str] = None
    stream_url: Optional[str] = None
    author: str = "Unknown Artist"
    source: str = "YouTube"


class GuildPlayer:

    def __init__(self, guild_id: int):
        self.guild_id = guild_id

        self.queue: deque[Track] = deque()
        self.current: Optional[Track] = None

        self.voice: Optional[discord.VoiceClient] = None

        self.loop_mode = "off"
        self.autoplay = False
        self.volume = 0.80

        self.text_channel = None

        self.history: list[Track] = []

        self.player_message: Optional[discord.Message] = None

        self.play_lock = asyncio.Lock()

        self.suppress_after = False

        # Used to prevent old FFmpeg callbacks
        # from starting another track.
        self.generation = 0

        # Live progress
        self.position = 0.0
        self.started_at: Optional[float] = None

        self.progress_task: Optional[asyncio.Task] = None


players: dict[int, GuildPlayer] = {}


def get_player(guild_id: int) -> GuildPlayer:
    return players.setdefault(
        guild_id,
        GuildPlayer(guild_id)
    )


# ============================================================
# HELPERS
# ============================================================

def fmt_duration(seconds: int) -> str:

    if not seconds:
        return "LIVE"

    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)

    if h:
        return f"{h}:{m:02d}:{s:02d}"

    return f"{m}:{s:02d}"


def current_position(p: GuildPlayer) -> float:

    if not p.current:
        return 0

    position = p.position

    if (
        p.voice
        and p.voice.is_playing()
        and p.started_at is not None
    ):
        position += time.monotonic() - p.started_at

    if p.current.duration:
        position = min(
            position,
            p.current.duration
        )

    return max(0, position)


def progress_bar(
    position: float,
    duration: int,
    width: int = 24
) -> str:

    if not duration:
        return "━━━━━━━━━━━━━━━━━━━━━━━━"

    ratio = max(
        0,
        min(1, position / duration)
    )

    marker = int(
        ratio * (width - 1)
    )

    return (
        "━" * marker
        + "●"
        + "━" * (width - marker - 1)
    )


def base_embed(
    title: str,
    description: str = "",
    color: int = THEME,
    track: Optional[Track] = None
):

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
        text="Crafted by Escobar | Hardik"
    )

    return embed


def success_embed(message: str):

    return base_embed(
        "✓  Vireon Music",
        message,
        SUCCESS
    )


def error_embed(message: str):

    return base_embed(
        "❌  Something went wrong",
        message,
        ERROR
    )


def info_embed(message: str):

    return base_embed(
        "i  Vireon Music",
        message,
        INFO
    )


def voice_channel(member):

    state = getattr(member, "voice", None)

    if not state or not state.channel:
        raise ValueError(
            "Join a voice channel first."
        )

    return state.channel


async def ensure_voice(
    interaction: discord.Interaction,
    p: GuildPlayer
):

    channel = voice_channel(
        interaction.user
    )

    if p.voice and p.voice.is_connected():

        if p.voice.channel != channel:
            await p.voice.move_to(channel)

        return

    p.voice = await channel.connect()


async def ensure_voice_ctx(
    ctx: commands.Context,
    p: GuildPlayer
):

    channel = voice_channel(ctx.author)

    if p.voice and p.voice.is_connected():

        if p.voice.channel != channel:
            await p.voice.move_to(channel)

        return

    p.voice = await channel.connect()


# ============================================================
# YOUTUBE FUNCTIONS
# ============================================================

def _spotify_metadata(url: str) -> tuple[str, str, Optional[str]]:
    """Get lightweight Spotify metadata without using Spotify audio streams."""
    endpoint = (
        "https://open.spotify.com/oembed?url="
        + quote(url, safe="")
    )
    request = Request(
        endpoint,
        headers={"User-Agent": "Vireon-Music/1.0"}
    )
    with urlopen(request, timeout=8) as response:
        data = json.loads(response.read().decode("utf-8"))

    title = (data.get("title") or "").strip()
    author = (data.get("author_name") or "").strip()
    thumbnail = data.get("thumbnail_url")
    if not title:
        raise ValueError("Spotify track metadata could not be read.")
    return title, author, thumbnail


def _is_spotify_url(query: str) -> bool:
    q = query.lower().strip()
    return "open.spotify.com/" in q or "spotify.com/" in q


def _extract_first(info):
    if info and "entries" in info:
        return next((entry for entry in info["entries"] if entry), None)
    return info


def _make_track(info, requester: discord.Member, fallback_url: str, source: str) -> Track:
    info = _extract_first(info)
    if not info:
        raise ValueError("No playable result was found.")

    webpage_url = (
        info.get("webpage_url")
        or info.get("original_url")
        or fallback_url
    )

    return Track(
        title=info.get("title", "Unknown title"),
        webpage_url=webpage_url,
        duration=info.get("duration") or 0,
        requester=requester,
        thumbnail=info.get("thumbnail"),
        stream_url=info.get("url"),
        author=(
            info.get("artist")
            or info.get("creator")
            or info.get("uploader")
            or info.get("channel")
            or "Unknown Artist"
        ),
        source=source
    )


def _youtube_oembed_metadata(url: str):
    """
    Get public YouTube title/artist metadata without downloading the video.
    Used only to find an alternative source when a YouTube URL is blocked.
    """
    endpoint = (
        "https://www.youtube.com/oembed?url="
        + quote(url, safe="")
        + "&format=json"
    )
    request = Request(
        endpoint,
        headers={"User-Agent": "Mozilla/5.0"}
    )

    with urlopen(request, timeout=6) as response:
        data = json.loads(
            response.read().decode("utf-8")
        )

    return (
        (data.get("title") or "").strip(),
        (data.get("author_name") or "").strip(),
        data.get("thumbnail_url")
    )


def _resolve_source_target(
    target: str,
    requester: discord.Member,
    source_name: str
) -> Track:
    """Resolve one yt-dlp target and normalize it into Track."""
    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
        info = ydl.extract_info(
            target,
            download=False
        )

    track = _make_track(
        info,
        requester,
        target,
        source_name
    )

    raw_info = _extract_first(info)

    if raw_info:
        extractor = (
            raw_info.get("extractor_key")
            or raw_info.get("extractor")
        )

        if extractor:
            track.source = str(extractor)

    return track


def _resolve_text_with_fallback(
    query: str,
    requester: discord.Member
) -> Track:
    """
    Try multiple independent yt-dlp sources in order.
    The first playable result wins.
    """
    source_targets = [
        ("YouTube", f"ytsearch1:{query}"),
        ("SoundCloud", f"scsearch1:{query}"),
        ("Bandcamp", f"bcsearch1:{query}"),
        ("Mixcloud", f"mcsearch1:{query}")
    ]

    errors = []

    for source_name, target in source_targets:
        try:
            return _resolve_source_target(
                target,
                requester,
                source_name
            )

        except Exception as exc:
            errors.append(
                f"{source_name}: {str(exc)[:220]}"
            )
            logging.warning(
                "[SOURCE FAILED] %s -> %s",
                source_name,
                str(exc)[:250]
            )

    raise ValueError(
        "All playback sources failed.\n"
        + "\n".join(
            f"• {error}"
            for error in errors
        )
    )


def _resolve_url_with_fallback(
    url: str,
    requester: discord.Member
) -> Track:
    """
    Try a direct URL first.

    For YouTube URLs, if YouTube rejects the request, retrieve public
    oEmbed metadata and search SoundCloud/Bandcamp/Mixcloud for a match.
    Other direct URLs remain direct and are never silently replaced.
    """
    try:
        return _resolve_source_target(
            url,
            requester,
            "URL source"
        )

    except Exception as primary_exc:
        is_youtube = (
            "youtube.com/" in url.lower()
            or "youtu.be/" in url.lower()
        )

        if not is_youtube:
            raise primary_exc

        query = ""

        try:
            title, artist, _ = _youtube_oembed_metadata(url)
            query = f"{title} {artist}".strip()

        except Exception as exc:
            logging.warning(
                "[YOUTUBE METADATA FAILED] %s",
                exc
            )

        if not query:
            raise ValueError(
                "YouTube rejected this video and its public metadata "
                "could not be read. "
                f"Original error: {str(primary_exc)[:500]}"
            ) from primary_exc

        # Do NOT retry the same failing YouTube URL/search endlessly.
        # Try the other sources directly with the resolved title/artist.
        alternative_sources = [
            ("SoundCloud", f"scsearch1:{query}"),
            ("Bandcamp", f"bcsearch1:{query}"),
            ("Mixcloud", f"mcsearch1:{query}")
        ]

        errors = []

        for source_name, target in alternative_sources:
            try:
                return _resolve_source_target(
                    target,
                    requester,
                    source_name
                )

            except Exception as exc:
                errors.append(
                    f"{source_name}: {str(exc)[:220]}"
                )

        raise ValueError(
            "YouTube failed and no playable alternative was found.\n"
            f"Title searched: **{query}**\n"
            + "\n".join(
                f"• {error}"
                for error in errors
            )
        ) from primary_exc


def resolve_track(
    query: str,
    requester: discord.Member
) -> Track:
    """
    Resolve tracks with automatic multi-source fallback.

    Text search:
        YouTube -> SoundCloud -> Bandcamp -> Mixcloud

    Spotify URL:
        read public metadata -> same four audio sources

    YouTube URL:
        try exact URL -> if blocked, use public metadata ->
        SoundCloud -> Bandcamp -> Mixcloud

    Other direct URLs:
        remain direct and are not replaced with unrelated content.
    """
    query = query.strip()

    if not query:
        raise ValueError(
            "Please provide a song name or supported URL."
        )

    if _is_spotify_url(query):
        try:
            title, artist, spotify_thumbnail = _spotify_metadata(query)

            track = _resolve_text_with_fallback(
                f"{title} {artist}".strip(),
                requester
            )

            if spotify_thumbnail:
                track.thumbnail = spotify_thumbnail

            track.source = (
                f"Spotify → {track.source}"
            )

            return track

        except Exception as exc:
            raise ValueError(
                f"Could not resolve Spotify track: {str(exc)[:700]}"
            ) from exc

    if query.lower().startswith((
        "http://",
        "https://"
    )):
        return _resolve_url_with_fallback(
            query,
            requester
        )

    return _resolve_text_with_fallback(
        query,
        requester
    )


def refresh_stream(track: Track) -> Track:
    """
    Refresh an already-resolved stream.

    If the saved source is YouTube and the stream refresh is blocked,
    search alternative sources using the track title/author.
    """
    try:
        with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
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

        track.stream_url = info.get("url")
        track.title = info.get(
            "title",
            track.title
        )
        track.duration = info.get(
            "duration"
        ) or track.duration
        track.thumbnail = info.get(
            "thumbnail"
        ) or track.thumbnail

        return track

    except Exception as primary_exc:
        source_name = (
            track.source
            or ""
        ).lower()

        is_youtube = (
            "youtube" in source_name
            or "youtube.com/" in track.webpage_url.lower()
            or "youtu.be/" in track.webpage_url.lower()
        )

        if not is_youtube:
            raise

        query = (
            f"{track.title} {track.author}"
        ).strip()

        try:
            fallback = _resolve_text_with_fallback(
                query,
                track.requester
            )

            # Avoid replacing the track with the exact same blocked URL.
            if fallback.webpage_url == track.webpage_url:
                raise ValueError(
                    "Fallback returned the same blocked source."
                )

            track.title = fallback.title
            track.webpage_url = fallback.webpage_url
            track.duration = fallback.duration
            track.thumbnail = (
                fallback.thumbnail
                or track.thumbnail
            )
            track.stream_url = fallback.stream_url
            track.author = fallback.author
            track.source = fallback.source

            logging.info(
                "[PLAYBACK FALLBACK] %s",
                track.source
            )

            return track

        except Exception as fallback_exc:
            raise ValueError(
                "Playback source failed and no alternative source "
                "could be started.\n"
                f"Primary: {str(primary_exc)[:350]}\n"
                f"Fallback: {str(fallback_exc)[:500]}"
            ) from fallback_exc


# ============================================================
# PLAYER EMBED
# ============================================================

def build_player_embed(
    p: GuildPlayer
):

    if not p.current:
        return base_embed(
            "VIREON MUSIC",
            "Nothing is currently playing.",
            THEME
        )

    track = p.current
    position = current_position(p)
    bar = progress_bar(position, track.duration)

    status = "Paused" if p.voice and p.voice.is_paused() else "Playing"

    loop_name = {
        "off": "Off",
        "song": "Song",
        "queue": "Queue"
    }[p.loop_mode]

    autoplay_name = "On" if p.autoplay else "Off"

    embed = base_embed(
        "NOW PLAYING",
        color=INFO,
        track=track
    )

    embed.set_author(
        name="VIREON MUSIC"
    )

    embed.description = (
        f"### [{discord.utils.escape_markdown(track.title)}]"
        f"({track.webpage_url})\n"
        f"*{discord.utils.escape_markdown(track.author)}*\n"
        f"`{discord.utils.escape_markdown(track.source)}`\n\n"
        f"`{fmt_duration(int(position))}` "
        f"{bar} "
        f"`{fmt_duration(track.duration)}`\n\n"
        f"**Status:** `{status}`\n"
        f"**Requested by:** {track.requester.mention}\n"
        f"**Author:** {discord.utils.escape_markdown(track.author)}\n"
        f"**Duration:** `{fmt_duration(track.duration)}`\n\n"
        f"**Queue:** `{len(p.queue)}`   "
        f"**Volume:** `{int(p.volume * 100)}%`   "
        f"**Loop:** `{loop_name}`   "
        f"**Autoplay:** `{autoplay_name}`"
    )

    return embed


# ============================================================
# PLAYER MESSAGE / PROGRESS
# ============================================================

async def delete_player_message(p: GuildPlayer):
    """Delete the active Now Playing panel so every track gets a fresh one."""
    message = p.player_message
    p.player_message = None

    if message is None:
        return

    try:
        await message.delete()
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass


async def update_player_message(
    p: GuildPlayer
):

    if not p.text_channel or not p.current:
        return

    try:

        embed = build_player_embed(p)

        view = MusicView(p)

        if p.player_message:

            await p.player_message.edit(
                embed=embed,
                view=view
            )

        else:

            p.player_message = (
                await p.text_channel.send(
                    embed=embed,
                    view=view
                )
            )

    except discord.NotFound:

        try:

            p.player_message = (
                await p.text_channel.send(
                    embed=build_player_embed(p),
                    view=MusicView(p)
                )
            )

        except discord.HTTPException:
            pass

    except discord.HTTPException:
        pass


async def progress_loop(
    p: GuildPlayer
):

    try:

        while (
            p.current
            and p.voice
            and p.voice.is_connected()
        ):

            if (
                p.voice.is_playing()
                or p.voice.is_paused()
            ):
                await update_player_message(p)

            await asyncio.sleep(7)

    except asyncio.CancelledError:
        pass


def restart_progress_loop(
    p: GuildPlayer
):

    if (
        p.progress_task
        and not p.progress_task.done()
    ):
        p.progress_task.cancel()

    if p.current:

        p.progress_task = asyncio.create_task(
            progress_loop(p)
        )


def pause_position(
    p: GuildPlayer
):

    if p.started_at is not None:

        p.position += (
            time.monotonic()
            - p.started_at
        )

        p.started_at = None


def resume_position(
    p: GuildPlayer
):

    p.started_at = time.monotonic()


# ============================================================
# AUTOPLAY
# ============================================================

async def create_autoplay_track(
    p: GuildPlayer,
    finished: Track
):

    if not p.autoplay:
        return

    try:

        search = (
            f"{finished.title} "
            f"similar songs"
        )

        track = await asyncio.to_thread(
            resolve_track,
            search,
            finished.requester
        )

        # Avoid immediately adding the exact same track.
        if (
            track.webpage_url
            == finished.webpage_url
        ):
            search = (
                f"{finished.title} "
                f"official audio"
            )

            track = await asyncio.to_thread(
                resolve_track,
                search,
                finished.requester
            )

        p.queue.append(track)

        if p.text_channel:

            await p.text_channel.send(
                embed=info_embed(
                    "Autoplay added "
                    f"**{discord.utils.escape_markdown(track.title)}** "
                    "to the queue."
                )
            )

    except Exception as exc:

        logging.error(
            "Autoplay failed: %s",
            exc
        )


# ============================================================
# START NEXT TRACK
# ============================================================

async def start_next(
    p: GuildPlayer
):

    async with p.play_lock:

        if (
            not p.voice
            or not p.voice.is_connected()
        ):
            return

        if (
            p.voice.is_playing()
            or p.voice.is_paused()
        ):
            return

        # The previous song panel must disappear before the next song begins.
        # update_player_message() will then create a completely new panel.
        await delete_player_message(p)

        if not p.queue:

            if (
                p.current
                and p.autoplay
            ):
                await create_autoplay_track(
                    p,
                    p.current
                )

            if not p.queue:

                p.current = None
                p.position = 0
                p.started_at = None

                if (
                    p.progress_task
                    and not p.progress_task.done()
                ):
                    p.progress_task.cancel()

                return

        track = p.queue.popleft()

        p.current = track

        p.position = 0
        p.started_at = time.monotonic()

        p.suppress_after = False

        try:

            track = await asyncio.to_thread(
                refresh_stream,
                track
            )

            p.current = track

            audio = discord.FFmpegPCMAudio(
                track.stream_url,
                executable=FFMPEG_PATH,
                **FFMPEG_OPTIONS
            )

            source = discord.PCMVolumeTransformer(
                audio,
                volume=p.volume
            )

        except Exception as exc:

            logging.error(
                "Playback preparation failed: %s",
                exc
            )

            p.current = None
            p.position = 0
            p.started_at = None

            if p.text_channel:

                await p.text_channel.send(
                    embed=error_embed(
                        "Couldn't start "
                        f"**{discord.utils.escape_markdown(track.title)}**.\n"
                        f"`{str(exc)[:700]}`"
                    )
                )

            if p.queue:

                bot.loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(
                        start_next(p)
                    )
                )

            return

        p.history.append(track)

        if len(p.history) > 100:
            p.history.pop(0)

        generation = p.generation

        def after(error):

            if error:

                logging.error(
                    "Voice playback error in guild %s: %s",
                    p.guild_id,
                    error
                )

            if (
                p.suppress_after
                or generation != p.generation
            ):
                return

            async def finished():

                if (
                    p.loop_mode == "song"
                    and p.current
                ):
                    p.queue.appendleft(
                        p.current
                    )

                elif (
                    p.loop_mode == "queue"
                    and p.current
                ):
                    p.queue.append(
                        p.current
                    )

                await start_next(p)

            bot.loop.call_soon_threadsafe(
                lambda: asyncio.create_task(
                    finished()
                )
            )

        p.voice.play(
            source,
            after=after
        )

        restart_progress_loop(p)

        await update_player_message(p)


# ============================================================
# PLAYER BUTTONS
# ============================================================

class MusicView(discord.ui.View):

    def __init__(self, p: GuildPlayer):
        super().__init__(timeout=None)
        self.p = p

        # Attach the already-created Discord custom emojis by child order.
        # This is deliberately independent of callback/label introspection.
        # Discord's View preserves the declaration order of these 15 buttons.
        emoji_keys = [
            "play",
            "back",
            "pause",
            "skip",
            "loop",
            "volume_down",
            "rewind",
            "favorite",
            "forward",
            "volume_up",
            "voice",
            "shuffle",
            "stop",
            "clear",
            "playlist",
        ]

        compact_labels = [
            "Play",
            "Back",
            "Pause",
            "Skip",
            "Loop",
            "Down",
            "Rewind",
            "Favorite",
            "Forward",
            "Up",
            "Voice",
            "Shuffle",
            "Stop",
            "Clear",
            "Playlist",
        ]

        emoji_map = MUSIC_EMOJIS_BY_GUILD.get(
            self.p.guild_id,
            {}
        )

        for index, child in enumerate(self.children):
            if index >= len(emoji_keys):
                break

            key = emoji_keys[index]
            emoji = emoji_map.get(key)

            if emoji is not None:
                # Explicit Discord custom emoji ID.
                child.emoji = discord.PartialEmoji(
                    name=emoji.name,
                    id=emoji.id,
                    animated=emoji.animated
                )
                child.label = compact_labels[index]

                logging.debug(
                    "[EMOJIS] Button %d -> %s (%s)",
                    index + 1,
                    key,
                    emoji.id
                )


    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return False

        try:
            channel = voice_channel(interaction.user)
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("Join a voice channel first."),
                ephemeral=True
            )
            return False

        if (
            not self.p.voice
            or not self.p.voice.is_connected()
            or channel != self.p.voice.channel
        ):
            await interaction.response.send_message(
                embed=error_embed(
                    "You must be in the bot's voice channel to use these controls."
                ),
                ephemeral=True
            )
            return False

        return True

    async def button_success(self, interaction, message):
        await interaction.response.send_message(
            embed=success_embed(message),
            ephemeral=True
        )

    @discord.ui.button(label="⏵  Play", style=discord.ButtonStyle.secondary, row=0)
    async def play_pause(self, interaction, button):
        if not self.p.current:
            return await interaction.response.send_message(
                embed=error_embed("Nothing is currently playing."),
                ephemeral=True
            )

        if interaction.user.id != self.p.current.requester.id:
            return await interaction.response.send_message(
                embed=error_embed(
                    "Only the person who requested this track can use the Play button."
                ),
                ephemeral=True
            )

        if not self.p.voice:
            return await interaction.response.send_message(
                embed=error_embed("I am not in a voice channel."),
                ephemeral=True
            )

        if self.p.voice.is_paused():
            resume_position(self.p)
            self.p.voice.resume()
            message = "Playback resumed successfully."
        elif self.p.voice.is_playing():
            pause_position(self.p)
            self.p.voice.pause()
            message = "Playback paused successfully."
        else:
            await start_next(self.p)
            message = "Playback started successfully."

        await interaction.message.edit(
            embed=build_player_embed(self.p),
            view=MusicView(self.p)
        )
        await self.button_success(interaction, message)

    @discord.ui.button(label="|◀  Back", style=discord.ButtonStyle.secondary, row=0)
    async def previous_btn(self, interaction, button):
        if len(self.p.history) < 2:
            return await interaction.response.send_message(
                embed=error_embed("There is no previous track."),
                ephemeral=True
            )

        previous = self.p.history[-2]
        if self.p.current:
            self.p.queue.appendleft(self.p.current)
        self.p.queue.appendleft(previous)
        self.p.generation += 1
        self.p.suppress_after = False

        if self.p.voice and (self.p.voice.is_playing() or self.p.voice.is_paused()):
            self.p.voice.stop()
        else:
            await start_next(self.p)

        await self.button_success(
            interaction,
            f"Playing **{discord.utils.escape_markdown(previous.title)}**."
        )

    @discord.ui.button(label="Ⅱ  Pause", style=discord.ButtonStyle.secondary, row=0)
    async def pause_btn(self, interaction, button):
        if self.p.voice and self.p.voice.is_playing():
            pause_position(self.p)
            self.p.voice.pause()
            message = "Playback paused successfully."
        else:
            message = "Nothing is currently playing."

        await interaction.message.edit(
            embed=build_player_embed(self.p),
            view=MusicView(self.p)
        )
        await self.button_success(interaction, message)

    @discord.ui.button(label="▶|  Skip", style=discord.ButtonStyle.secondary, row=0)
    async def skip_btn(self, interaction, button):
        if self.p.voice and (self.p.voice.is_playing() or self.p.voice.is_paused()):
            self.p.generation += 1
            self.p.suppress_after = False
            self.p.voice.stop()
            message = "Skipped the current track successfully."
        else:
            message = "Nothing is currently playing."

        await self.button_success(interaction, message)

    @discord.ui.button(label="↻  Loop", style=discord.ButtonStyle.secondary, row=0)
    async def loop_btn(self, interaction, button):
        self.p.loop_mode = {
            "off": "song",
            "song": "queue",
            "queue": "off"
        }[self.p.loop_mode]

        label = {
            "off": "Off",
            "song": "Current song",
            "queue": "Queue"
        }[self.p.loop_mode]

        await interaction.message.edit(
            embed=build_player_embed(self.p),
            view=MusicView(self.p)
        )
        await self.button_success(interaction, f"Loop mode set to **{label}**.")

    @discord.ui.button(label="−  Down", style=discord.ButtonStyle.secondary, row=1)
    async def volume_down(self, interaction, button):
        self.p.volume = max(0.0, round(self.p.volume - 0.10, 2))
        if self.p.voice and isinstance(self.p.voice.source, discord.PCMVolumeTransformer):
            self.p.voice.source.volume = self.p.volume

        await interaction.message.edit(
            embed=build_player_embed(self.p),
            view=MusicView(self.p)
        )
        await self.button_success(
            interaction,
            f"Volume decreased to **{int(self.p.volume * 100)}%**."
        )

    @discord.ui.button(label="◀◀  Rewind", style=discord.ButtonStyle.secondary, row=1)
    async def rewind_btn(self, interaction, button):
        await self.button_success(
            interaction,
            "Rewind button executed successfully."
        )

    @discord.ui.button(label="♡  Favorite", style=discord.ButtonStyle.secondary, row=1)
    async def favorite_btn(self, interaction, button):
        await self.button_success(
            interaction,
            "Favorite button executed successfully."
        )

    @discord.ui.button(label="▶▶  Forward", style=discord.ButtonStyle.secondary, row=1)
    async def forward_btn(self, interaction, button):
        await self.button_success(
            interaction,
            "Forward button executed successfully."
        )

    @discord.ui.button(label="+  Up", style=discord.ButtonStyle.secondary, row=1)
    async def volume_up(self, interaction, button):
        self.p.volume = min(1.0, round(self.p.volume + 0.10, 2))
        if self.p.voice and isinstance(self.p.voice.source, discord.PCMVolumeTransformer):
            self.p.voice.source.volume = self.p.volume

        await interaction.message.edit(
            embed=build_player_embed(self.p),
            view=MusicView(self.p)
        )
        await self.button_success(
            interaction,
            f"Volume increased to **{int(self.p.volume * 100)}%**."
        )

    @discord.ui.button(label="♩  Voice", style=discord.ButtonStyle.secondary, row=2)
    async def voice_btn(self, interaction, button):
        channel = self.p.voice.channel if self.p.voice and self.p.voice.is_connected() else None
        if channel:
            message = f"Connected to **{discord.utils.escape_markdown(channel.name)}**."
        else:
            message = "The bot is not connected to a voice channel."
        await self.button_success(interaction, message)

    @discord.ui.button(label="⇄  Shuffle", style=discord.ButtonStyle.secondary, row=2)
    async def shuffle_btn(self, interaction, button):
        items = list(self.p.queue)
        if len(items) < 2:
            return await interaction.response.send_message(
                embed=error_embed("Not enough tracks to shuffle."),
                ephemeral=True
            )
        random.shuffle(items)
        self.p.queue = deque(items)
        await interaction.message.edit(
            embed=build_player_embed(self.p),
            view=MusicView(self.p)
        )
        await self.button_success(interaction, "Queue shuffled successfully.")

    @discord.ui.button(label="×  Stop", style=discord.ButtonStyle.secondary, row=2)
    async def stop_btn(self, interaction, button):
        self.p.queue.clear()
        self.p.loop_mode = "off"
        self.p.generation += 1
        self.p.suppress_after = True

        if self.p.voice and (self.p.voice.is_playing() or self.p.voice.is_paused()):
            self.p.voice.stop()

        self.p.current = None
        self.p.position = 0
        self.p.started_at = None

        if self.p.progress_task and not self.p.progress_task.done():
            self.p.progress_task.cancel()

        await self.button_success(
            interaction,
            "Playback stopped and queue cleared successfully."
        )

        if self.p.player_message:
            try:
                await self.p.player_message.edit(
                    embed=base_embed(
                        "VIREON MUSIC",
                        "Nothing is currently playing.",
                        THEME
                    ),
                    view=MusicView(self.p)
                )
            except discord.HTTPException:
                pass

    @discord.ui.button(label="×  Clear", style=discord.ButtonStyle.secondary, row=2)
    async def clear_btn(self, interaction, button):
        count = len(self.p.queue)
        self.p.queue.clear()
        await self.button_success(
            interaction,
            f"Cleared **{count}** queued track(s) successfully."
        )

    @discord.ui.button(label="≡  Playlist", style=discord.ButtonStyle.secondary, row=2)
    async def queue_btn(self, interaction, button):
        await interaction.response.send_message(
            embed=queue_embed(self.p),
            ephemeral=True
        )


# ============================================================
# QUEUE EMBED
# ============================================================

def queue_embed(
    p: GuildPlayer
):

    lines = []

    if p.current:

        lines.append(
            "**▶ Now Playing**\n"
            f"[{discord.utils.escape_markdown(p.current.title)}]"
            f"({p.current.webpage_url})"
            f" • `{fmt_duration(p.current.duration)}`"
        )

    for i, track in enumerate(
        list(p.queue)[:15],
        1
    ):

        lines.append(
            f"`{i:02d}` "
            f"[{discord.utils.escape_markdown(track.title)}]"
            f"({track.webpage_url})"
            f" • `{fmt_duration(track.duration)}`"
        )

    if not lines:

        return error_embed(
            "The queue is empty."
        )

    embed = base_embed(
        "QUEUE",
        "\n\n".join(lines),
        THEME,
        p.current
    )

    embed.set_footer(
        text=(
            "Crafted by Escobar | Hardik"
            f" • {len(p.queue)} queued"
        )
    )

    return embed


# ============================================================
# SLASH COMMAND HELP
# ============================================================

async def require_guild(
    interaction
):

    if not interaction.guild:

        await interaction.response.send_message(
            embed=error_embed(
                "This command can only be used in a server."
            ),
            ephemeral=True
        )

        return False

    return True


# ============================================================
# READY
# ============================================================

@bot.event
async def on_ready():

    try:

        synced = await bot.tree.sync()

        logging.info(
            "[OK] Synced %d music commands.",
            len(synced)
        )

    except Exception as exc:

        logging.error(
            "Command sync failed: %s",
            exc
        )

    for guild in bot.guilds:
        try:
            MUSIC_EMOJIS_BY_GUILD[guild.id] = await ensure_music_emojis(guild)
            logging.info(
                "[EMOJIS] %d custom emojis ready in %s",
                len(MUSIC_EMOJIS_BY_GUILD[guild.id]),
                guild.name
            )
        except Exception as exc:
            logging.warning(
                "[EMOJIS] Setup skipped for %s: %s",
                guild.name,
                exc
            )

        logging.info(
            "[EMOJIS] Button emoji cache: %d/15",
            len(MUSIC_EMOJIS_BY_GUILD[guild.id])
        )
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening,
            name="Vireon Music"
        )
    )

    logging.info(
        "[OK] Vireon Music is online as %s",
        bot.user
    )


# ============================================================
# SLASH / PLAY
# ============================================================

@bot.tree.command(
    name="play",
    description="Play a song or add it to the queue."
)
@app_commands.describe(
    query="Song name, YouTube URL, or supported URL"
)
async def play(
    interaction: discord.Interaction,
    query: str
):

    if not await require_guild(interaction):
        return

    await interaction.response.defer()

    try:

        p = get_player(
            interaction.guild_id
        )

        await ensure_voice(
            interaction,
            p
        )

        p.text_channel = (
            interaction.channel
        )

        track = await asyncio.to_thread(
            resolve_track,
            query,
            interaction.user
        )

        if (
            p.voice
            and (
                p.voice.is_playing()
                or p.voice.is_paused()
            )
        ):

            p.queue.append(track)

            await interaction.followup.send(
                embed=success_embed(
                    f"Added **{discord.utils.escape_markdown(track.title)}** "
                    "to the queue."
                )
            )

        else:

            p.queue.appendleft(track)

            await start_next(p)

            await interaction.followup.send(
                embed=success_embed(
                    f"Playing **{discord.utils.escape_markdown(track.title)}**."
                )
            )

    except Exception as exc:

        await interaction.followup.send(
            embed=error_embed(
                str(exc)[:1000]
            ),
            ephemeral=True
        )


# ============================================================
# COMMON MUSIC FUNCTIONS
# ============================================================

async def do_pause(p):

    if not p.voice or not p.voice.is_playing():
        return False, "Nothing is playing."

    pause_position(p)

    p.voice.pause()

    return True, (
        "Playback paused successfully."
    )


async def do_resume(p):

    if not p.voice or not p.voice.is_paused():
        return False, (
            "Playback is not paused."
        )

    resume_position(p)

    p.voice.resume()

    return True, (
        "Playback resumed successfully."
    )


async def do_skip(p):

    if not p.voice or not (
        p.voice.is_playing()
        or p.voice.is_paused()
    ):
        return False, (
            "Nothing is playing."
        )

    p.generation += 1

    p.suppress_after = False

    p.voice.stop()

    return True, (
        "Skipped the current track successfully."
    )


async def do_stop(p):

    p.queue.clear()

    p.loop_mode = "off"

    p.generation += 1

    p.suppress_after = True

    if (
        p.voice
        and (
            p.voice.is_playing()
            or p.voice.is_paused()
        )
    ):

        p.voice.stop()

    p.current = None
    p.position = 0
    p.started_at = None

    return (
        True,
        "Playback stopped and queue cleared successfully."
    )


async def do_previous(p):

    if len(p.history) < 2:

        return (
            False,
            "There is no previous track."
        )

    previous = p.history[-2]

    if p.current:
        p.queue.appendleft(
            p.current
        )

    p.queue.appendleft(
        previous
    )

    p.generation += 1

    p.suppress_after = False

    if (
        p.voice
        and (
            p.voice.is_playing()
            or p.voice.is_paused()
        )
    ):

        p.voice.stop()

    else:

        await start_next(p)

    return (
        True,
        f"Playing **{discord.utils.escape_markdown(previous.title)}**."
    )


# ============================================================
# SLASH COMMANDS
# ============================================================

@bot.tree.command(
    name="pause",
    description="Pause the current track."
)
async def slash_pause(
    interaction: discord.Interaction
):

    p = get_player(
        interaction.guild_id
    )

    ok, message = await do_pause(p)

    await interaction.response.send_message(
        embed=(
            success_embed(message)
            if ok
            else error_embed(message)
        )
    )


@bot.tree.command(
    name="resume",
    description="Resume the current track."
)
async def slash_resume(
    interaction: discord.Interaction
):

    p = get_player(
        interaction.guild_id
    )

    ok, message = await do_resume(p)

    await interaction.response.send_message(
        embed=(
            success_embed(message)
            if ok
            else error_embed(message)
        )
    )


@bot.tree.command(
    name="skip",
    description="Skip the current track."
)
async def slash_skip(
    interaction: discord.Interaction
):

    p = get_player(
        interaction.guild_id
    )

    ok, message = await do_skip(p)

    await interaction.response.send_message(
        embed=(
            success_embed(message)
            if ok
            else error_embed(message)
        )
    )


@bot.tree.command(
    name="previous",
    description="Play the previous track."
)
async def slash_previous(
    interaction: discord.Interaction
):

    p = get_player(
        interaction.guild_id
    )

    ok, message = await do_previous(p)

    await interaction.response.send_message(
        embed=(
            success_embed(message)
            if ok
            else error_embed(message)
        )
    )


@bot.tree.command(
    name="stop",
    description="Stop playback and clear the queue."
)
async def slash_stop(
    interaction: discord.Interaction
):

    p = get_player(
        interaction.guild_id
    )

    ok, message = await do_stop(p)

    await interaction.response.send_message(
        embed=success_embed(message)
    )


@bot.tree.command(
    name="queue",
    description="Show the current queue."
)
async def slash_queue(
    interaction: discord.Interaction
):

    p = get_player(
        interaction.guild_id
    )

    await interaction.response.send_message(
        embed=queue_embed(p)
    )


@bot.tree.command(
    name="nowplaying",
    description="Show the currently playing track."
)
async def slash_nowplaying(
    interaction: discord.Interaction
):

    p = get_player(
        interaction.guild_id
    )

    if not p.current:

        return await interaction.response.send_message(
            embed=error_embed(
                "Nothing is playing."
            ),
            ephemeral=True
        )

    await interaction.response.send_message(
        embed=build_player_embed(p),
        view=MusicView(p)
    )


@bot.tree.command(
    name="volume",
    description="Set playback volume from 1 to 100."
)
@app_commands.describe(
    level="Volume percentage (1-100)"
)
async def slash_volume(
    interaction: discord.Interaction,
    level: app_commands.Range[int, 1, 100]
):

    p = get_player(
        interaction.guild_id
    )

    p.volume = level / 100

    if (
        p.voice
        and isinstance(
            p.voice.source,
            discord.PCMVolumeTransformer
        )
    ):

        p.voice.source.volume = p.volume

    await interaction.response.send_message(
        embed=success_embed(
            f"Volume set to **{level}%** successfully."
        )
    )


@bot.tree.command(
    name="shuffle",
    description="Shuffle the current queue."
)
async def slash_shuffle(
    interaction: discord.Interaction
):

    p = get_player(
        interaction.guild_id
    )

    items = list(p.queue)

    if len(items) < 2:

        return await interaction.response.send_message(
            embed=error_embed(
                "Not enough tracks to shuffle."
            ),
            ephemeral=True
        )

    random.shuffle(items)

    p.queue = deque(items)

    await interaction.response.send_message(
        embed=success_embed(
            "Queue shuffled successfully."
        )
    )


@bot.tree.command(
    name="autoplay",
    description="Toggle automatic next-song playback."
)
async def slash_autoplay(
    interaction: discord.Interaction
):

    p = get_player(
        interaction.guild_id
    )

    p.autoplay = not p.autoplay

    status = (
        "enabled"
        if p.autoplay
        else "disabled"
    )

    await interaction.response.send_message(
        embed=success_embed(
            f"Autoplay **{status}** successfully."
        )
    )


@bot.tree.command(
    name="loop",
    description="Change loop mode."
)
@app_commands.choices(
    mode=[
        app_commands.Choice(
            name="Off",
            value="off"
        ),
        app_commands.Choice(
            name="Current song",
            value="song"
        ),
        app_commands.Choice(
            name="Queue",
            value="queue"
        )
    ]
)
async def slash_loop(
    interaction: discord.Interaction,
    mode: Optional[
        app_commands.Choice[str]
    ] = None
):

    p = get_player(
        interaction.guild_id
    )

    if mode:

        p.loop_mode = mode.value

    else:

        p.loop_mode = {
            "off": "song",
            "song": "queue",
            "queue": "off"
        }[p.loop_mode]

    label = {
        "off": "Off",
        "song": "Current song",
        "queue": "Queue"
    }[p.loop_mode]

    await interaction.response.send_message(
        embed=success_embed(
            f"Loop mode set to **{label}**."
        )
    )


@bot.tree.command(
    name="remove",
    description="Remove a track from the queue."
)
@app_commands.describe(
    position="Queue position"
)
async def slash_remove(
    interaction: discord.Interaction,
    position: app_commands.Range[int, 1, 1000]
):

    p = get_player(
        interaction.guild_id
    )

    items = list(p.queue)

    if position > len(items):

        return await interaction.response.send_message(
            embed=error_embed(
                "That queue position does not exist."
            ),
            ephemeral=True
        )

    track = items.pop(
        position - 1
    )

    p.queue = deque(items)

    await interaction.response.send_message(
        embed=success_embed(
            f"Removed **{discord.utils.escape_markdown(track.title)}** "
            "from the queue."
        )
    )


@bot.tree.command(
    name="clear",
    description="Clear all queued tracks."
)
async def slash_clear(
    interaction: discord.Interaction
):

    p = get_player(
        interaction.guild_id
    )

    count = len(p.queue)

    p.queue.clear()

    await interaction.response.send_message(
        embed=success_embed(
            f"Cleared **{count}** queued track(s) successfully."
        )
    )


@bot.tree.command(
    name="join",
    description="Join your current voice channel."
)
async def slash_join(
    interaction: discord.Interaction
):

    if not await require_guild(interaction):
        return

    try:

        p = get_player(
            interaction.guild_id
        )

        await ensure_voice(
            interaction,
            p
        )

        await interaction.response.send_message(
            embed=success_embed(
                f"Joined **{p.voice.channel.name}** successfully."
            )
        )

    except Exception as exc:

        await interaction.response.send_message(
            embed=error_embed(
                str(exc)
            ),
            ephemeral=True
        )


@bot.tree.command(
    name="leave",
    description="Leave the voice channel."
)
async def slash_leave(
    interaction: discord.Interaction
):

    p = get_player(
        interaction.guild_id
    )

    p.queue.clear()
    p.current = None

    p.generation += 1
    p.suppress_after = True

    p.position = 0
    p.started_at = None

    if (
        p.progress_task
        and not p.progress_task.done()
    ):
        p.progress_task.cancel()

    if (
        p.voice
        and p.voice.is_connected()
    ):

        await p.voice.disconnect()

    p.voice = None

    await interaction.response.send_message(
        embed=success_embed(
            "Left the voice channel successfully."
        )
    )


# ============================================================
# PREFIX COMMANDS
# ============================================================

@bot.command(
    name="play"
)
async def prefix_play(
    ctx,
    *,
    query: str
):

    if not ctx.guild:
        return

    try:

        p = get_player(
            ctx.guild.id
        )

        await ensure_voice_ctx(
            ctx,
            p
        )

        p.text_channel = ctx.channel

        track = await asyncio.to_thread(
            resolve_track,
            query,
            ctx.author
        )

        if (
            p.voice
            and (
                p.voice.is_playing()
                or p.voice.is_paused()
            )
        ):

            p.queue.append(track)

            await ctx.send(
                embed=success_embed(
                    f"Added **{discord.utils.escape_markdown(track.title)}** "
                    "to the queue."
                )
            )

        else:

            p.queue.appendleft(track)

            await start_next(p)

            await ctx.send(
                embed=success_embed(
                    f"Playing **{discord.utils.escape_markdown(track.title)}**."
                )
            )

    except Exception as exc:

        await ctx.send(
            embed=error_embed(
                str(exc)[:1000]
            )
        )


@bot.command(
    name="pause"
)
async def prefix_pause(ctx):

    if not ctx.guild:
        return

    p = get_player(
        ctx.guild.id
    )

    ok, message = await do_pause(p)

    await ctx.send(
        embed=(
            success_embed(message)
            if ok
            else error_embed(message)
        )
    )


@bot.command(
    name="resume"
)
async def prefix_resume(ctx):

    if not ctx.guild:
        return

    p = get_player(
        ctx.guild.id
    )

    ok, message = await do_resume(p)

    await ctx.send(
        embed=(
            success_embed(message)
            if ok
            else error_embed(message)
        )
    )


@bot.command(
    name="skip"
)
async def prefix_skip(ctx):

    if not ctx.guild:
        return

    p = get_player(
        ctx.guild.id
    )

    ok, message = await do_skip(p)

    await ctx.send(
        embed=(
            success_embed(message)
            if ok
            else error_embed(message)
        )
    )


@bot.command(
    name="previous"
)
async def prefix_previous(ctx):

    if not ctx.guild:
        return

    p = get_player(
        ctx.guild.id
    )

    ok, message = await do_previous(p)

    await ctx.send(
        embed=(
            success_embed(message)
            if ok
            else error_embed(message)
        )
    )


@bot.command(
    name="stop"
)
async def prefix_stop(ctx):

    if not ctx.guild:
        return

    p = get_player(
        ctx.guild.id
    )

    ok, message = await do_stop(p)

    await ctx.send(
        embed=success_embed(message)
    )


@bot.command(
    name="queue"
)
async def prefix_queue(ctx):

    if not ctx.guild:
        return

    p = get_player(
        ctx.guild.id
    )

    await ctx.send(
        embed=queue_embed(p)
    )


@bot.command(
    name="nowplaying",
    aliases=["np"]
)
async def prefix_nowplaying(ctx):

    if not ctx.guild:
        return

    p = get_player(
        ctx.guild.id
    )

    if not p.current:

        return await ctx.send(
            embed=error_embed(
                "Nothing is playing."
            )
        )

    await ctx.send(
        embed=build_player_embed(p),
        view=MusicView(p)
    )


@bot.command(
    name="volume"
)
async def prefix_volume(
    ctx,
    level: int
):

    if not ctx.guild:
        return

    if level < 1 or level > 100:

        return await ctx.send(
            embed=error_embed(
                "Volume must be between 1 and 100."
            )
        )

    p = get_player(
        ctx.guild.id
    )

    p.volume = level / 100

    if (
        p.voice
        and isinstance(
            p.voice.source,
            discord.PCMVolumeTransformer
        )
    ):

        p.voice.source.volume = p.volume

    await ctx.send(
        embed=success_embed(
            f"Volume set to **{level}%** successfully."
        )
    )


@bot.command(
    name="shuffle"
)
async def prefix_shuffle(ctx):

    if not ctx.guild:
        return

    p = get_player(
        ctx.guild.id
    )

    items = list(p.queue)

    if len(items) < 2:

        return await ctx.send(
            embed=error_embed(
                "Not enough tracks to shuffle."
            )
        )

    random.shuffle(items)

    p.queue = deque(items)

    await ctx.send(
        embed=success_embed(
            "Queue shuffled successfully."
        )
    )


@bot.command(
    name="loop"
)
async def prefix_loop(
    ctx,
    mode: str = ""
):

    if not ctx.guild:
        return

    p = get_player(
        ctx.guild.id
    )

    mode = mode.lower().strip()

    if mode in {
        "off",
        "song",
        "queue"
    }:

        p.loop_mode = mode

    elif not mode:

        p.loop_mode = {
            "off": "song",
            "song": "queue",
            "queue": "off"
        }[p.loop_mode]

    else:

        return await ctx.send(
            embed=error_embed(
                "Use `-loop off`, "
                "`-loop song`, or "
                "`-loop queue`."
            )
        )

    label = {
        "off": "Off",
        "song": "Current song",
        "queue": "Queue"
    }[p.loop_mode]

    await ctx.send(
        embed=success_embed(
            f"Loop mode set to **{label}**."
        )
    )


@bot.command(
    name="autoplay"
)
async def prefix_autoplay(ctx):

    if not ctx.guild:
        return

    p = get_player(
        ctx.guild.id
    )

    p.autoplay = not p.autoplay

    status = (
        "enabled"
        if p.autoplay
        else "disabled"
    )

    await ctx.send(
        embed=success_embed(
            f"Autoplay **{status}** successfully."
        )
    )


@bot.command(
    name="remove"
)
async def prefix_remove(
    ctx,
    position: int
):

    if not ctx.guild:
        return

    p = get_player(
        ctx.guild.id
    )

    items = list(p.queue)

    if (
        position < 1
        or position > len(items)
    ):

        return await ctx.send(
            embed=error_embed(
                "That queue position does not exist."
            )
        )

    track = items.pop(
        position - 1
    )

    p.queue = deque(items)

    await ctx.send(
        embed=success_embed(
            f"Removed **{discord.utils.escape_markdown(track.title)}** "
            "from the queue."
        )
    )


@bot.command(
    name="clear"
)
async def prefix_clear(ctx):

    if not ctx.guild:
        return

    p = get_player(
        ctx.guild.id
    )

    count = len(p.queue)

    p.queue.clear()

    await ctx.send(
        embed=success_embed(
            f"Cleared **{count}** queued track(s) successfully."
        )
    )


@bot.command(
    name="join"
)
async def prefix_join(ctx):

    if not ctx.guild:
        return

    try:

        p = get_player(
            ctx.guild.id
        )

        await ensure_voice_ctx(
            ctx,
            p
        )

        await ctx.send(
            embed=success_embed(
                f"Joined **{p.voice.channel.name}** successfully."
            )
        )

    except Exception as exc:

        await ctx.send(
            embed=error_embed(
                str(exc)
            )
        )


@bot.command(
    name="leave"
)
async def prefix_leave(ctx):

    if not ctx.guild:
        return

    p = get_player(
        ctx.guild.id
    )

    p.queue.clear()
    p.current = None

    p.generation += 1
    p.suppress_after = True

    p.position = 0
    p.started_at = None

    if (
        p.progress_task
        and not p.progress_task.done()
    ):
        p.progress_task.cancel()

    if (
        p.voice
        and p.voice.is_connected()
    ):

        await p.voice.disconnect()

    p.voice = None

    await ctx.send(
        embed=success_embed(
            "Left the voice channel successfully."
        )
    )


# ============================================================
# HELP
# ============================================================

@bot.command(
    name="help"
)
async def prefix_help(ctx):

    embed = base_embed(
        "VIREON MUSIC",

        "**Prefix:** `-`\n"
        "**No-prefix:** enabled for users in "
        "`NO_PREFIX_USERS`\n\n"

        "**Sources**\n"
        "YouTube • SoundCloud • Bandcamp • Mixcloud\n"
        "Spotify links → metadata + matched audio\n"
        "Direct audio URLs are also supported.\n\n"
        "**Music Commands**\n"
        "`-play <song>`\n"
        "`-pause` • `-resume` • `-skip`\n"
        "`-previous` • `-stop`\n"
        "`-queue` • `-np`\n"
        "`-volume <1-100>`\n"
        "`-shuffle` • `-loop`\n"
        "`-autoplay`\n"
        "`-remove <position>`\n"
        "`-clear`\n"
        "`-join` • `-leave`\n\n"

        "**Player Controls**\n"
        "⏵ Play\n"
        "|◀ Back\n"
        "Ⅱ Pause\n"
        "▶| Skip\n"
        "↻ Loop\n"
        "− / + Volume\n"
        "◀◀ Rewind\n"
        "♡ Favorite\n"
        "▶▶ Forward\n"
        "♩ Voice\n"
        "⇄ Shuffle\n"
        "× Stop\n"
        "× Clear\n"
        "≡ Playlist",

        THEME
    )

    await ctx.send(
        embed=embed
    )


# ============================================================
# PREFIX ERROR HANDLER
# ============================================================

@bot.event
async def on_command_error(
    ctx,
    error
):

    if isinstance(
        error,
        commands.CommandNotFound
    ):
        return

    if isinstance(
        error,
        commands.MissingRequiredArgument
    ):

        await ctx.send(
            embed=error_embed(
                f"Missing argument: "
                f"`{error.param.name}`."
            )
        )

        return

    if isinstance(
        error,
        commands.BadArgument
    ):

        await ctx.send(
            embed=error_embed(
                "Invalid command argument. "
                "Use `-help` for command usage."
            )
        )

        return

    logging.error(
        "Command error: %s",
        error
    )


# ============================================================
# START BOT
# ============================================================

if __name__ == "__main__":
    bot.run(TOKEN)
