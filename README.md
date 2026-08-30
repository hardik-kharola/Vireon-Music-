# Vireon Music

Standalone Discord music bot with a Vireon dark, icon-first player UI.

## Commands

- `/play <query>`
- `/pause`
- `/resume`
- `/skip`
- `/previous`
- `/stop`
- `/queue`
- `/nowplaying`
- `/volume <1-100>`
- `/shuffle`
- `/loop [mode]`
- `/remove <position>`
- `/clear`
- `/join`
- `/leave`

## Setup

1. Install Python 3.11+.
2. Install FFmpeg and make sure `ffmpeg -version` works, or set `FFMPEG_PATH` in `.env`.
3. Copy `.env.example` to `.env`.
4. Put your Discord bot token in `.env` as `DISCORD_TOKEN=...`.
5. Install dependencies: `py -m pip install -r requirements.txt`.
6. Start: `py main.py`.

The bot uses yt-dlp for supported music search/stream resolution and FFmpeg for Discord voice playback.

## UI

The player uses text glyphs such as `▶`, `Ⅱ`, `|◀`, `▶|`, `↻`, `◀◀`, `⇄`, `×`, and `≡` rather than colorful emoji characters. Discord controls are rendered with native Discord button styling, so exact CSS from a screenshot cannot be reproduced inside Discord.
