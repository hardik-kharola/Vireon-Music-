# Vireon

Clean Python Discord bot source.

## Setup
1. Copy `.env.example` to `.env`.
2. Put your NEW Discord bot token in `DISCORD_TOKEN=`.
3. Put the Discord public key in `DISCORD_PUBLIC_KEY=` if your interaction webserver needs it.
4. `VIREON_OWNER_ID` is preconfigured with the two retained Vireon owners (ESCOBAR and Oewe).
5. Install dependencies: `python -m pip install -r requirements.txt`
6. Start: `python main.py`

Never share `.env` or a bot token.

## Included customization
- Existing Python Vireon cogs and commands from the clean source.
- Owner/developer configuration.
- No-prefix system.
- UPI/LTC/payment systems.
- Ticket system with the existing panel editor plus an additional Pre-Open Questions button.
- Pre-open questions are asked before a ticket channel is created and answers are placed in the ticket welcome embed.
- Footer defaults used by the ticket system: `Crafted by Escobar | Hardik`.
