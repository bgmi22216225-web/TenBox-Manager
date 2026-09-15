# Bot 'V' — Telegram Video Snapshot & Link Automation

Python + Pyrogram automation bot. Monitors a source channel for videos,
forwards each one to a secondary "link generator" bot, extracts a
`tenbox` link from its reply, takes an FFmpeg snapshot of the video, and
posts **snapshot + link (with a dynamic header/footer)** to a destination
channel. Header/footer are stored in Neon PostgreSQL and editable live via
admin commands.

## How it works

```
SOURCE_CHANNEL ──(video)──▶  Job Queue (FIFO, asyncio.Queue)
                                   │
                                   ▼
                     send video → SECONDARY_BOT_USERNAME
                                   │
                          await reply (single in-flight job
                          → no cross-video link mixing)
                                   │
                     reply link contains "tenbox"? ──No──▶ drop, no post
                                   │ Yes
                                   ▼
              download original video → ffmpeg snapshot (1 frame)
                                   │
                     caption = header + link + footer  (Markdown as typed)
                                   │
                                   ▼
                       send snapshot photo → DESTINATION_CHANNEL
```

**Why no race conditions:** the worker only ever processes one job at a
time — it fully awaits the secondary bot's reply for job *N* before
dequeuing job *N+1*. There is exactly one "pending future" at any moment,
so a link can never be attached to the wrong video, even under bulk
posting.

## Project structure

```
V/
├── main.py                      # entry point
├── config.py                    # env var loading & validation
├── database.py                  # Neon Postgres (asyncpg) — header/footer
├── handlers/
│   ├── admin.py                 # /setheader /setfooter /viewformat /delheader /delfooter
│   ├── channel_listener.py      # source channel + secondary bot listeners
│   └── video_processor.py       # queue, pipeline, snapshot + post logic
├── utils/
│   ├── logger.py
│   └── snapshot.py               # ffmpeg frame extraction
├── requirements.txt
├── Procfile                     # Railway (nixpacks) process
├── nixpacks.toml                # ffmpeg + python for nixpacks builds
├── Dockerfile                   # alternative: Docker-based deploy
└── .env.example
```

## Environment variables

| Variable | Required | Notes |
|---|---|---|
| `API_ID`, `API_HASH` | ✅ | From https://my.telegram.org |
| `BOT_TOKEN` | one of these two | Use if running as a bot account |
| `SESSION_STRING` | one of these two | Use if running as a userbot (needed if the secondary bot won't accept messages from a bot account) |
| `DATABASE_URL` | ✅ | Neon Postgres connection string (`sslmode=require`) |
| `SOURCE_CHANNEL_ID` | ✅ | Channel to monitor for videos |
| `DESTINATION_CHANNEL_ID` | ✅ | Channel to post snapshot + link |
| `SECONDARY_BOT_USERNAME` | ✅ | Bot that returns the tenbox link |
| `ADMIN_IDS` | ✅ | Comma-separated Telegram user IDs |
| `SECONDARY_BOT_TIMEOUT` | optional | Seconds to wait for a reply (default 90) |
| `LINK_FILTER_KEYWORD` | optional | Default `tenbox` |
| `TEMP_DIR` | optional | Default `/tmp/v_bot` |

## Getting a SESSION_STRING (if using userbot mode)

```python
from pyrogram import Client
with Client("v_session", api_id=API_ID, api_hash=API_HASH) as app:
    print(app.export_session_string())
```
Run this once locally, log in with the account that will act as the userbot,
copy the printed string into `SESSION_STRING`.

## Local run

```bash
git clone <your-repo-url>
cd V
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in the values
# ffmpeg must be installed locally: `sudo apt install ffmpeg` / `brew install ffmpeg`
python main.py
```

## Deploying — Neon PostgreSQL

1. Create a project at https://neon.tech
2. Copy the connection string (Dashboard → Connection Details), make sure
   it includes `?sslmode=require`.
3. Paste it into `DATABASE_URL`. The bot creates its own `bot_config`
   table automatically on first startup — no manual migration needed.

## Deploying — Railway

1. Push this project to GitHub.
2. On https://railway.app: **New Project → Deploy from GitHub repo**.
3. Railway auto-detects either `Dockerfile` or `nixpacks.toml` — both are
   included; `nixpacks.toml` is used by default and installs `ffmpeg`
   alongside Python. Delete whichever one you don't want, or leave both
   (Railway prefers Dockerfile if present).
4. In **Variables**, add every key from `.env.example` with real values.
5. Deploy. Railway runs the `Procfile`'s `worker: python main.py` process.
   No public port is needed — this is a background worker, not a web
   service, so you can ignore any "no healthcheck" warning.
6. Check **Deployments → Logs** for `Bot 'V' is up and running.`

## Admin commands (DM the bot; ADMIN_IDS only)

| Command | Effect |
|---|---|
| `/setheader <text>` | Overwrites the header (Markdown allowed) |
| `/setfooter <text>` | Overwrites the footer (Markdown allowed) |
| `/viewformat` | Preview current header + `[TENBOX_LINK]` + footer |
| `/delheader` | Clears the header |
| `/delfooter` | Clears the footer |

Non-admins get no response from these commands (silently ignored).

## Notes & tuning

- The FFmpeg snapshot is taken at `00:00:02` by default — change the
  `timestamp` argument in `utils/snapshot.py:extract_snapshot()` if you
  want a different frame (e.g. a percentage of duration).
- If the secondary bot doesn't reply within `SECONDARY_BOT_TIMEOUT`
  seconds, the job is dropped and logged — nothing gets posted.
- Only messages whose reply contains a link with `tenbox` (case-insensitive)
  are ever posted; anything else is silently dropped per the filter rule.
