import asyncio
import logging
import os
import random
import shutil
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
    raise SystemExit("[ERROR] DISCORD_TOKEN not found in .env")

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

# Vireon-style dark neutral theme.
THEME = 0x1B1D21
SUCCESS = 0x57F287
ERROR = 0xED4245
INFO = 0x5865F2
MUTED = 0x949BA4

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
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


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
        self.loop_mode = "off"  # off | song | queue
        self.volume = 0.80
        self.text_channel: Optional[discord.abc.Messageable] = None
        self.history: list[Track] = []
        self.previous_stack: list[Track] = []
        self.player_message: Optional[discord.Message] = None
        self.play_lock = asyncio.Lock()
        self.suppress_after = False


players: dict[int, GuildPlayer] = {}


def get_player(guild_id: int) -> GuildPlayer:
    return players.setdefault(guild_id, GuildPlayer(guild_id))


def fmt_duration(seconds: int) -> str:
    if not seconds:
        return "LIVE"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def base_embed(title: str, description: str = "", color: int = THEME, track: Track | None = None) -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=color)
    if track and track.thumbnail:
        e.set_thumbnail(url=track.thumbnail)
    e.set_footer(text="Vireon Music")
    return e


def error_embed(message: str) -> discord.Embed:
    return base_embed("❌  Something went wrong", message, ERROR)


def success_embed(message: str) -> discord.Embed:
    return base_embed("✓  Vireon Music", message, SUCCESS)


def info_embed(message: str) -> discord.Embed:
    return base_embed("i  Vireon Music", message, INFO)


def user_voice_channel(interaction: discord.Interaction) -> discord.VoiceChannel:
    voice_state = getattr(interaction.user, "voice", None)
    if not voice_state or not voice_state.channel:
        raise ValueError("Join a voice channel first.")
    return voice_state.channel


async def ensure_voice(interaction: discord.Interaction, p: GuildPlayer) -> None:
    channel = user_voice_channel(interaction)
    if p.voice and p.voice.is_connected():
        if p.voice.channel != channel:
            await p.voice.move_to(channel)
        return
    p.voice = await channel.connect()


def resolve_track(query: str, requester: discord.Member) -> Track:
    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
        info = ydl.extract_info(query, download=False)
        if info and "entries" in info:
            info = next((entry for entry in info["entries"] if entry), None)
        if not info:
            raise ValueError("No playable result was found.")
        webpage_url = info.get("webpage_url") or info.get("original_url") or query
        return Track(
            title=info.get("title", "Unknown title"),
            webpage_url=webpage_url,
            duration=info.get("duration") or 0,
            requester=requester,
            thumbnail=info.get("thumbnail"),
            stream_url=info.get("url"),
        )


def refresh_stream(track: Track) -> Track:
    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
        info = ydl.extract_info(track.webpage_url, download=False)
        if info and "entries" in info:
            info = next((entry for entry in info["entries"] if entry), None)
        if not info:
            raise ValueError("The track is no longer available.")
        track.stream_url = info.get("url")
        track.title = info.get("title", track.title)
        track.duration = info.get("duration") or track.duration
        track.thumbnail = info.get("thumbnail") or track.thumbnail
        return track


async def send_or_update_player(p: GuildPlayer) -> None:
    if not p.text_channel or not p.current:
        return
    e = build_player_embed(p)
    view = MusicView(p)
    try:
        if p.player_message:
            await p.player_message.edit(embed=e, view=view)
        else:
            p.player_message = await p.text_channel.send(embed=e, view=view)
    except (discord.NotFound, discord.HTTPException):
        p.player_message = await p.text_channel.send(embed=e, view=view)


def build_player_embed(p: GuildPlayer) -> discord.Embed:
    t = p.current
    if not t:
        return base_embed("Vireon Music", "Nothing is currently playing.", THEME)

    loop_label = {"off": "Off", "song": "Song", "queue": "Queue"}[p.loop_mode]
    queue_count = len(p.queue)
    status = "Paused" if p.voice and p.voice.is_paused() else "Playing"
    source = f"[Open track]({t.webpage_url})"

    e = base_embed("NOW PLAYING", color=THEME, track=t)
    e.description = (
        f"### [{discord.utils.escape_markdown(t.title)}]({t.webpage_url})\n"
        f"`{fmt_duration(t.duration)}`  •  {status}  •  Requested by {t.requester.mention}\n\n"
        f"**Queue:** `{queue_count}`  **Volume:** `{int(p.volume * 100)}%`  **Loop:** `{loop_label}`\n"
        f"{source}"
    )
    e.set_author(name="VIREON MUSIC")
    return e


async def start_next(p: GuildPlayer) -> None:
    async with p.play_lock:
        if not p.voice or not p.voice.is_connected():
            return
        if p.voice.is_playing() or p.voice.is_paused():
            return
        if not p.queue:
            p.current = None
            p.player_message = None
            return

        track = p.queue.popleft()
        p.current = track
        p.suppress_after = False

        try:
            track = await asyncio.to_thread(refresh_stream, track)
            p.current = track
            source = discord.PCMVolumeTransformer(
                discord.FFmpegPCMAudio(track.stream_url, executable=FFMPEG_PATH, **FFMPEG_OPTIONS),
                volume=p.volume,
            )
        except Exception as exc:
            logging.error("Playback preparation failed: %s", exc)
            p.current = None
            if p.text_channel:
                await p.text_channel.send(embed=error_embed(f"Couldn't start **{discord.utils.escape_markdown(track.title)}**.\n`{str(exc)[:700]}`"))
            if p.queue:
                bot.loop.call_soon_threadsafe(lambda: asyncio.create_task(start_next(p)))
            return

        # Keep a history without endlessly duplicating consecutive starts.
        p.history.append(track)
        if len(p.history) > 100:
            p.history.pop(0)

        def after(error: Optional[Exception]) -> None:
            if error:
                logging.error("Voice playback error in guild %s: %s", p.guild_id, error)
            if p.suppress_after:
                return
            if p.loop_mode == "song" and p.current:
                p.queue.appendleft(p.current)
            elif p.loop_mode == "queue" and p.current:
                p.queue.append(p.current)
            bot.loop.call_soon_threadsafe(lambda: asyncio.create_task(start_next(p)))

        p.voice.play(source, after=after)
        await send_or_update_player(p)


class MusicView(discord.ui.View):
    """Icon-first player controls. Unicode glyphs are used instead of Discord emoji characters."""

    def __init__(self, p: GuildPlayer):
        super().__init__(timeout=None)
        self.p = p

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return False
        try:
            channel = user_voice_channel(interaction)
        except ValueError:
            await interaction.response.send_message(embed=error_embed("Join a voice channel first."), ephemeral=True)
            return False
        if not self.p.voice or not self.p.voice.is_connected() or channel != self.p.voice.channel:
            await interaction.response.send_message(embed=error_embed("You must be in the bot's voice channel to use these controls."), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, row=0)
    async def play_pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.p.voice:
            return await interaction.response.send_message(embed=error_embed("I am not in a voice channel."), ephemeral=True)
        if self.p.voice.is_paused():
            self.p.voice.resume()
        elif self.p.voice.is_playing():
            self.p.voice.pause()
        else:
            await start_next(self.p)
        await interaction.response.edit_message(embed=build_player_embed(self.p), view=MusicView(self.p))

    @discord.ui.button(label="|◀", style=discord.ButtonStyle.secondary, row=0)
    async def previous_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if len(self.p.history) < 2:
            return await interaction.response.send_message(embed=error_embed("There is no previous track."), ephemeral=True)
        current = self.p.current
        previous = self.p.history[-2]
        if current:
            self.p.queue.appendleft(current)
        self.p.queue.appendleft(previous)
        if self.p.voice and (self.p.voice.is_playing() or self.p.voice.is_paused()):
            self.p.suppress_after = False
            self.p.voice.stop()
        await interaction.response.send_message(embed=success_embed(f"Playing **{discord.utils.escape_markdown(previous.title)}**."), ephemeral=True)

    @discord.ui.button(label="Ⅱ", style=discord.ButtonStyle.secondary, row=0)
    async def pause_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.p.voice and self.p.voice.is_playing():
            self.p.voice.pause()
            msg = "Playback paused."
        else:
            msg = "Nothing is currently playing."
        await interaction.response.edit_message(embed=build_player_embed(self.p), view=MusicView(self.p))

    @discord.ui.button(label="▶|", style=discord.ButtonStyle.secondary, row=0)
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.p.voice and (self.p.voice.is_playing() or self.p.voice.is_paused()):
            self.p.suppress_after = False
            self.p.voice.stop()
            msg = "Skipped the current track."
        else:
            msg = "Nothing is currently playing."
        await interaction.response.send_message(embed=success_embed(msg), ephemeral=True)

    @discord.ui.button(label="↻", style=discord.ButtonStyle.secondary, row=0)
    async def loop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.p.loop_mode = {"off": "song", "song": "queue", "queue": "off"}[self.p.loop_mode]
        await interaction.response.edit_message(embed=build_player_embed(self.p), view=MusicView(self.p))

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, row=1)
    async def volume_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.p.volume = max(0.0, round(self.p.volume - 0.10, 2))
        if self.p.voice and isinstance(self.p.voice.source, discord.PCMVolumeTransformer):
            self.p.voice.source.volume = self.p.volume
        await interaction.response.edit_message(embed=build_player_embed(self.p), view=MusicView(self.p))

    @discord.ui.button(label="◀◀", style=discord.ButtonStyle.secondary, row=1)
    async def rewind_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=info_embed("Seek controls are available through `/volume`, while exact stream seeking depends on the current source."), ephemeral=True)

    @discord.ui.button(label="♡", style=discord.ButtonStyle.secondary, row=1)
    async def favorite_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=info_embed("Favorites are reserved for a future Vireon Music library module."), ephemeral=True)

    @discord.ui.button(label="▶▶", style=discord.ButtonStyle.secondary, row=1)
    async def forward_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=info_embed("Fast-forward controls are reserved for the next audio-engine upgrade."), ephemeral=True)

    @discord.ui.button(label="▶|", style=discord.ButtonStyle.secondary, row=1)
    async def volume_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.p.volume = min(1.0, round(self.p.volume + 0.10, 2))
        if self.p.voice and isinstance(self.p.voice.source, discord.PCMVolumeTransformer):
            self.p.voice.source.volume = self.p.volume
        await interaction.response.edit_message(embed=build_player_embed(self.p), view=MusicView(self.p))

    @discord.ui.button(label="×", style=discord.ButtonStyle.secondary, row=2)
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.p.queue.clear()
        self.p.loop_mode = "off"
        self.p.suppress_after = True
        if self.p.voice and (self.p.voice.is_playing() or self.p.voice.is_paused()):
            self.p.voice.stop()
        self.p.current = None
        await interaction.response.send_message(embed=success_embed("Playback stopped and queue cleared."), ephemeral=True)

    @discord.ui.button(label="⇄", style=discord.ButtonStyle.secondary, row=2)
    async def shuffle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        items = list(self.p.queue)
        if len(items) < 2:
            return await interaction.response.send_message(embed=error_embed("Not enough tracks to shuffle."), ephemeral=True)
        random.shuffle(items)
        self.p.queue = deque(items)
        await interaction.response.edit_message(embed=build_player_embed(self.p), view=MusicView(self.p))

    @discord.ui.button(label="⌁", style=discord.ButtonStyle.secondary, row=2)
    async def clear_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        count = len(self.p.queue)
        self.p.queue.clear()
        await interaction.response.send_message(embed=success_embed(f"Cleared **{count}** queued track(s)."), ephemeral=True)

    @discord.ui.button(label="≡", style=discord.ButtonStyle.secondary, row=2)
    async def queue_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=queue_embed(self.p), ephemeral=True)

    @discord.ui.button(label="♪", style=discord.ButtonStyle.secondary, row=2)
    async def status_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=build_player_embed(self.p), ephemeral=True)


def queue_embed(p: GuildPlayer) -> discord.Embed:
    lines = []
    if p.current:
        lines.append(f"**▶ Now Playing**\n[{discord.utils.escape_markdown(p.current.title)}]({p.current.webpage_url}) • `{fmt_duration(p.current.duration)}`")
    for i, track in enumerate(list(p.queue)[:15], 1):
        lines.append(f"`{i:02d}` [{discord.utils.escape_markdown(track.title)}]({track.webpage_url}) • `{fmt_duration(track.duration)}`")
    if not lines:
        return error_embed("The queue is empty.")
    e = base_embed("QUEUE", "\n\n".join(lines), THEME, p.current)
    e.set_footer(text=f"Vireon Music • {len(p.queue)} queued")
    return e


async def require_guild(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        await interaction.response.send_message(embed=error_embed("This command can only be used in a server."), ephemeral=True)
        return False
    return True


@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        logging.info("[OK] Synced %d music commands.", len(synced))
    except Exception as exc:
        logging.error("Command sync failed: %s", exc)
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="Vireon Music"))
    logging.info("[OK] Vireon Music is online as %s", bot.user)


@bot.tree.command(name="play", description="Play a song or add it to the queue.")
@app_commands.describe(query="Song name, YouTube URL, or supported URL")
async def play(interaction: discord.Interaction, query: str):
    if not await require_guild(interaction):
        return
    await interaction.response.defer()
    try:
        p = get_player(interaction.guild_id)
        await ensure_voice(interaction, p)
        p.text_channel = interaction.channel
        track = await asyncio.to_thread(resolve_track, query, interaction.user)
        if p.voice and (p.voice.is_playing() or p.voice.is_paused()):
            p.queue.append(track)
            await interaction.followup.send(embed=success_embed(f"Added **{discord.utils.escape_markdown(track.title)}** to the queue."))
        else:
            p.queue.appendleft(track)
            await start_next(p)
            await interaction.followup.send(embed=success_embed(f"Playing **{discord.utils.escape_markdown(track.title)}**."))
    except Exception as exc:
        await interaction.followup.send(embed=error_embed(str(exc)[:1000]), ephemeral=True)


@bot.tree.command(name="pause", description="Pause the current track.")
async def pause(interaction: discord.Interaction):
    p = get_player(interaction.guild_id)
    if not p.voice or not p.voice.is_playing():
        return await interaction.response.send_message(embed=error_embed("Nothing is playing."), ephemeral=True)
    p.voice.pause()
    await interaction.response.send_message(embed=success_embed("Playback paused."))


@bot.tree.command(name="resume", description="Resume the current track.")
async def resume(interaction: discord.Interaction):
    p = get_player(interaction.guild_id)
    if not p.voice or not p.voice.is_paused():
        return await interaction.response.send_message(embed=error_embed("Playback is not paused."), ephemeral=True)
    p.voice.resume()
    await interaction.response.send_message(embed=success_embed("Playback resumed."))


@bot.tree.command(name="skip", description="Skip the current track.")
async def skip(interaction: discord.Interaction):
    p = get_player(interaction.guild_id)
    if not p.voice or not (p.voice.is_playing() or p.voice.is_paused()):
        return await interaction.response.send_message(embed=error_embed("Nothing is playing."), ephemeral=True)
    p.generation += 1
    p.voice.stop()
    await interaction.response.send_message(embed=success_embed("Skipped the current track."))


@bot.tree.command(name="previous", description="Play the previous track.")
async def previous(interaction: discord.Interaction):
    p = get_player(interaction.guild_id)
    if len(p.history) < 2:
        return await interaction.response.send_message(embed=error_embed("There is no previous track."), ephemeral=True)
    previous_track = p.history[-2]
    if p.current:
        p.queue.appendleft(p.current)
    p.queue.appendleft(previous_track)
    if p.voice and (p.voice.is_playing() or p.voice.is_paused()):
        p.generation += 1
        p.voice.stop()
    await interaction.response.send_message(embed=success_embed(f"Playing **{discord.utils.escape_markdown(previous_track.title)}**."))


@bot.tree.command(name="stop", description="Stop playback and clear the queue.")
async def stop(interaction: discord.Interaction):
    p = get_player(interaction.guild_id)
    p.queue.clear()
    p.loop_mode = "off"
    p.suppress_after = True
    if p.voice and (p.voice.is_playing() or p.voice.is_paused()):
        p.voice.stop()
    p.current = None
    await interaction.response.send_message(embed=success_embed("Playback stopped and queue cleared."))


@bot.tree.command(name="queue", description="Show the current queue.")
async def queue(interaction: discord.Interaction):
    p = get_player(interaction.guild_id)
    await interaction.response.send_message(embed=queue_embed(p))


@bot.tree.command(name="nowplaying", description="Show the currently playing track.")
async def nowplaying(interaction: discord.Interaction):
    p = get_player(interaction.guild_id)
    if not p.current:
        return await interaction.response.send_message(embed=error_embed("Nothing is playing."), ephemeral=True)
    await interaction.response.send_message(embed=build_player_embed(p), view=MusicView(p))


@bot.tree.command(name="volume", description="Set playback volume from 1 to 100.")
@app_commands.describe(level="Volume percentage (1-100)")
async def volume(interaction: discord.Interaction, level: app_commands.Range[int, 1, 100]):
    p = get_player(interaction.guild_id)
    p.volume = level / 100
    if p.voice and isinstance(p.voice.source, discord.PCMVolumeTransformer):
        p.voice.source.volume = p.volume
    await interaction.response.send_message(embed=success_embed(f"Volume set to **{level}%**."))


@bot.tree.command(name="shuffle", description="Shuffle the current queue.")
async def shuffle(interaction: discord.Interaction):
    p = get_player(interaction.guild_id)
    items = list(p.queue)
    if len(items) < 2:
        return await interaction.response.send_message(embed=error_embed("Not enough tracks to shuffle."), ephemeral=True)
    random.shuffle(items)
    p.queue = deque(items)
    await interaction.response.send_message(embed=success_embed("Queue shuffled."))


@bot.tree.command(name="loop", description="Cycle loop mode: off, current song, or queue.")
@app_commands.choices(mode=[
    app_commands.Choice(name="Off", value="off"),
    app_commands.Choice(name="Current song", value="song"),
    app_commands.Choice(name="Queue", value="queue"),
])
async def loop(interaction: discord.Interaction, mode: Optional[app_commands.Choice[str]] = None):
    p = get_player(interaction.guild_id)
    if mode:
        p.loop_mode = mode.value
    else:
        p.loop_mode = {"off": "song", "song": "queue", "queue": "off"}[p.loop_mode]
    label = {"off": "Off", "song": "Current song", "queue": "Queue"}[p.loop_mode]
    await interaction.response.send_message(embed=success_embed(f"Loop mode: **{label}**."))


@bot.tree.command(name="remove", description="Remove a track from the queue by position.")
@app_commands.describe(position="Queue position")
async def remove(interaction: discord.Interaction, position: app_commands.Range[int, 1, 1000]):
    p = get_player(interaction.guild_id)
    items = list(p.queue)
    if position > len(items):
        return await interaction.response.send_message(embed=error_embed("That queue position does not exist."), ephemeral=True)
    track = items.pop(position - 1)
    p.queue = deque(items)
    await interaction.response.send_message(embed=success_embed(f"Removed **{discord.utils.escape_markdown(track.title)}** from the queue."))


@bot.tree.command(name="clear", description="Clear all queued tracks without stopping the current song.")
async def clear(interaction: discord.Interaction):
    p = get_player(interaction.guild_id)
    count = len(p.queue)
    p.queue.clear()
    await interaction.response.send_message(embed=success_embed(f"Cleared **{count}** queued track(s)."))


@bot.tree.command(name="join", description="Join your current voice channel.")
async def join(interaction: discord.Interaction):
    if not await require_guild(interaction):
        return
    try:
        p = get_player(interaction.guild_id)
        await ensure_voice(interaction, p)
        await interaction.response.send_message(embed=success_embed(f"Joined **{p.voice.channel.name}**."))
    except Exception as exc:
        await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)


@bot.tree.command(name="leave", description="Leave the voice channel and clear playback.")
async def leave(interaction: discord.Interaction):
    p = get_player(interaction.guild_id)
    p.queue.clear()
    p.current = None
    p.generation += 1
    if p.voice and p.voice.is_connected():
        await p.voice.disconnect()
    p.voice = None
    await interaction.response.send_message(embed=success_embed("Left the voice channel."))


if __name__ == "__main__":
    bot.run(TOKEN)
