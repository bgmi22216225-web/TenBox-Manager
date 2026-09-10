# Setup Instructions

## 1. Get Telegram credentials
1. `API_ID` / `API_HASH`: create an app at https://my.telegram.org/apps
2. `BOT_TOKEN`: create a bot via [@BotFather](https://t.me/BotFather), get the token
3. `SESSION_STRING`: generate a Pyrogram user session string once, locally:
   ```python
   from pyrogram import Client
   with Client("gen", api_id=API_ID, api_hash=API_HASH) as app:
       print(app.export_session_string())
   ```
   Log in with the userbot account when prompted (phone + OTP). Copy the printed
   string into `SESSION_STRING`. Do this once, locally — never on Railway.
4. Make sure:
   - The **bot** account is added as admin in `DESTINATION_CHANNEL_ID` (needs to post photos).
   - The **userbot** account (the one behind `SESSION_STRING`) is a member of
     `SOURCE_CHANNEL_ID` (needs to read videos) and has started a chat with
     `PROCESSING_BOT_USERNAME` (needs to forward to it and read its replies).

## 2. Get `MAIN_ADMIN_ID`
Send any message to [@userinfobot](https://t.me/userinfobot) (or similar) with the
account that should be the main admin, and copy its numeric ID.

## 3. Get channel IDs
Forward a message from each channel to [@JsonDumpBot](https://t.me/JsonDumpBot) (or
similar) to read `SOURCE_CHANNEL_ID` and `DESTINATION_CHANNEL_ID`. Channel IDs are
negative numbers, typically starting with `-100`.

## 4. Set up Neon PostgreSQL
1. Create a project at https://neon.tech
2. Copy the connection string shown in the dashboard into `DATABASE_URL`
   (it looks like `postgresql://user:password@host/dbname?sslmode=require`)
3. Nothing else to do — `database.py` creates `bot_settings` and `admin_users`
   automatically on first run.

## 5. Configure environment variables
Copy `.env.example` to `.env` locally for testing, or set the same keys directly
in Railway's **Variables** tab for deployment. All keys are required except the
three tunables at the bottom (`RESPONSE_TIMEOUT`, `QUEUE_WORKER_CONCURRENCY`,
`WORK_DIR`), which have working defaults.

## 6. Deploy to Railway
1. Push this project to a GitHub repo.
2. In Railway: **New Project → Deploy from GitHub repo**, pick the repo.
3. Railway auto-detects the `Dockerfile` and builds it (FFmpeg included, no extra
   buildpack config needed).
4. Add all the environment variables from step 5 in Railway's **Variables** tab.
5. Deploy. Check the logs for:
   ```
   Database pool initialized and schema verified.
   Bot and userbot clients started. Listening for videos...
   ```

## 7. Test it
1. Post a video in `SOURCE_CHANNEL_ID`.
2. Watch the logs — you should see it get enqueued, forwarded to the processing
   bot, a reply come back, a snapshot get captured, and a post land in
   `DESTINATION_CHANNEL_ID`.
3. As the main admin, DM the bot `/header Your header text 🔥` and `/footer Your
   footer text` to set the caption wrapper, then test again.
4. Try flooding the source channel with several videos back-to-back — they should
   process one at a time, each ending up with its own correct link (check
   `/status` to watch the queue depth while it drains).

## Notes on scaling beyond 1-at-a-time
`QUEUE_WORKER_CONCURRENCY` defaults to `1` specifically to guarantee zero
link/video mismatches, since it means only one video is ever "in flight" waiting
on the processing bot at once. If you need higher throughput and have confirmed
the processing bot replies with proper `reply_to_message_id` threading, you can
raise `QUEUE_WORKER_CONCURRENCY` — the matching logic in `queue_manager.py` will
use the reply-to id instead of the sequential fallback. Test carefully before
relying on this in production.
