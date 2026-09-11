# Telegram Video-Link Relay Bot

A Railway-deployable Telegram automation bot that watches a source channel
for videos, relays each one to a third-party "link-generating" bot,
captures a snapshot of the returned video, and posts the snapshot with a
composed header/link/footer caption to a destination channel.

## Architecture

| File | Purpose |
|---|---|
| `main.py` | Core logic: Pyrogram clients, source-channel listener, `asyncio.Queue` worker, snapshot generation, posting, admin commands |
| `database.py` | Async PostgreSQL (Neon) layer — admins table + header/footer templates table |
| `requirements.txt` | Pinned dependencies |
| `README.md` | This file |

Two Telegram sessions are used:

- **Userbot** (`SESSION_STRING`) — joins/reads `SOURCE_CHANNEL_ID`, forwards
  videos to `TARGET_BOT_USERNAME`, and receives that bot's replies. A
  userbot session is used here because reliable bot-to-bot interaction and
  channel history access are not guaranteed via the plain Bot API.
- **Bot** (`BOT_TOKEN`) — handles all `/admin` commands and posts final
  snapshots to `DESTINATION_CHANNEL_ID`.

Videos are processed one at a time through a single `asyncio.Queue`
worker, so a reply from the target bot is always correlated with the
video that triggered it — no race conditions even if several videos
land in the source channel at once.

## Environment Variables

| Variable | Description |
|---|---|
| `API_ID` | Telegram API ID from [my.telegram.org](https://my.telegram.org) |
| `API_HASH` | Telegram API Hash from [my.telegram.org](https://my.telegram.org) |
| `BOT_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) |
| `SESSION_STRING` | Pyrogram string session for the userbot account (see below) |
| `TARGET_BOT_USERNAME` | Username (without `@`) of the bot that returns links |
| `SOURCE_CHANNEL_ID` | Numeric chat ID of the channel to monitor for videos |
| `DESTINATION_CHANNEL_ID` | Numeric chat ID to post final snapshots to |
| `DATABASE_URL` | Neon PostgreSQL connection string, e.g. `postgresql://user:pass@host/db?sslmode=require` |
| `SUPER_ADMIN_ID` | Your numeric Telegram user ID — auto-added as the first admin on startup |
| `TARGET_REPLY_TIMEOUT` | *(optional)* Seconds to wait for the target bot's reply before giving up. Default `180`. |

The userbot account and the bot account must both have access to
`SOURCE_CHANNEL_ID` and `DESTINATION_CHANNEL_ID` as appropriate (the
userbot needs read access to the source; the bot needs post/admin rights
in the destination).

### Generating `SESSION_STRING`

Run this once locally (not on Railway) with Pyrogram installed:

```python
from pyrogram import Client

with Client("session_gen", api_id=API_ID, api_hash=API_HASH, in_memory=True) as app:
    print(app.export_session_string())
```

Log in when prompted, then copy the printed string into the
`SESSION_STRING` environment variable.

## Deployment on Railway

1. Push these four files to a GitHub repository.
2. Create a new Railway project → **Deploy from GitHub repo**.
3. Railway will detect Python automatically. Set the **Start Command** to:
   ```
   python main.py
   ```
4. Add a build step (or use a Nixpacks config) that ensures `ffmpeg` is
   available on the image — Railway's default Python image includes it
   via Nixpacks' apt packages; if it does not, add an `apt.txt` file
   containing:
   ```
   ffmpeg
   ```
5. Add all environment variables listed above under **Variables**.
6. Provision a Neon Postgres database and paste its connection string
   into `DATABASE_URL` (include `?sslmode=require`).
7. Deploy. On first boot the bot will create the required tables
   (`admins`, `templates`) automatically and register `SUPER_ADMIN_ID`
   as the first admin.

## Commands Reference

All commands are sent to the bot in a private chat and are restricted to
authorized admins (except `/addadmin` and `/deladmin`, which are
restricted to `SUPER_ADMIN_ID` only).

| Command | Access | Description |
|---|---|---|
| `/start` | Everyone | Basic status message |
| `/setheader <text>` | Admin | Set/update the caption header |
| `/setfooter <text>` | Admin | Set/update the caption footer |
| `/delheader` | Admin | Remove the header |
| `/delfooter` | Admin | Remove the footer |
| `/viewtemplate` | Admin | Preview current header, footer, and a sample composed caption |
| `/addadmin <user_id>` | Super admin | Grant admin access to a user |
| `/deladmin <user_id>` | Super admin | Revoke a user's admin access |
| `/admins` | Admin | List all current admins |

Caption is composed as:

```
{HEADER}

{LINK}

{FOOTER}
```

Empty header or footer sections are omitted cleanly (no stray blank
lines).

## Error Handling & Reliability

- Both Pyrogram clients start with exponential-backoff retries on
  connection failure.
- `FloodWait` errors while forwarding to the target bot are respected
  (the bot sleeps for the required duration and retries once).
- If the target bot doesn't reply within `TARGET_REPLY_TIMEOUT` seconds,
  that video is logged as failed and the worker moves on to the next
  queued video — it will not hang indefinitely.
- All downloaded videos and generated thumbnails are written to a
  per-job temp directory under `/tmp/bot_workdir` and deleted
  (`shutil.rmtree`) immediately after each job finishes, success or
  failure, to prevent disk overflow on Railway's ephemeral filesystem.
- Unexpected exceptions in the worker are caught and logged without
  crashing the queue loop, so one bad video doesn't stop the pipeline.

## Notes

- Ensure the account behind `SESSION_STRING` has already started a
  conversation with `TARGET_BOT_USERNAME` at least once (send `/start`
  manually) so Telegram allows the automated forward/reply exchange.
- `DESTINATION_CHANNEL_ID` and `SOURCE_CHANNEL_ID` are numeric IDs
  (e.g. `-1001234567890` for channels/supergroups), not usernames.
