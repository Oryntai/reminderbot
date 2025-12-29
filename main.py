"""Simple Telegram reminder bot for Render free tier.

This script implements a minimal FastAPI application that works with a
Telegram bot.  People can subscribe to reminders by sending the `/start`
command to the bot.  The bot stores each subscriber's chat ID in a local
JSON file.  A scheduled HTTP job can trigger the `/tick` endpoint to send
reminder messages to all subscribers.  Because the bot is exposed via
HTTP, services like cron‑job.org can call the `/tick` endpoint at the
desired times (for example every Monday and Tuesday at 14:00 and 15:00,
and every Wednesday at 14:00) without the process needing to run a
persistent scheduler.  The `/health` endpoint responds with a basic
heartbeat for uptime monitoring.

To deploy this on Render:

1. Create a new **Web Service** and point it to this repository.  In the
   service settings set the **Start Command** to

       uvicorn reminder_bot.main:app --host 0.0.0.0 --port $PORT

   Render injects the `$PORT` environment variable which tells uvicorn
   which port to listen on.

2. Define environment variables in the Render dashboard:

   * `BOT_TOKEN` – your bot token from BotFather (e.g.
     `8093840214:AAGqrQQvr3LVhCVvATbpTmqewLwkbc4Gg0c`).
   * `MESSAGE_TEXT` – (optional) the message to send when the reminder
     triggers.  Defaults to "Отметь атт https://lms.astanait.edu.kz/".

3. Deploy the service.  After it is live you must tell Telegram where to
   deliver webhook updates.  Replace `<YOUR_DOMAIN>` with your Render
   service URL and `<TOKEN>` with your bot token, then run (in your
   browser or using `curl`):

       https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://<YOUR_DOMAIN>/webhook

   Telegram will now POST incoming updates (messages) to the `/webhook`
   endpoint of this service.

4. Visit https://cron-job.org and create cron jobs that call the
   `/tick` endpoint at the desired times.  Cron‑job.org can run jobs as
   frequently as once per minute【81002734074620†L16-L19】 and supports
   specifying days of the week and times, so you can schedule jobs for
   every Monday and Tuesday at 14:00 and 15:00, and every Wednesday at
   14:00.  Each job should send an HTTP GET request to
   `https://<YOUR_DOMAIN>/tick`.  When the job runs, the server wakes up
   (Render spins up the service on demand【257396064970489†L233-L238】) and
   sends the reminder to all subscribers.

5. Anyone can subscribe to the reminders by starting a chat with the bot
   and sending `/start`.  They can unsubscribe with `/stop`.  Group
   chats work the same way; if an administrator invites the bot to a
   group and sends `/start` in the group, that chat ID will receive
   reminders too.

Limitations:

* Because Render's free plan spins down services that are idle for
  fifteen minutes【257396064970489†L233-L238】, it is important to have
  scheduled calls (via cron‑job.org or a similar service) to wake up
  this app at the times when you want to send reminders.  Otherwise
  scheduled messages could be missed while the app is asleep.

* This implementation stores subscriber and state data in local files.
  Local files are persisted across restarts on Render's free plan, but
  they are not guaranteed to survive if the instance is rebuilt or
  redeployed.  For a more robust solution, consider using a small
  database or key‑value store.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

import requests
from fastapi import FastAPI, Request


def get_env(name: str, default: str = "") -> str:
    """Return the value of an environment variable or a default."""
    value = os.environ.get(name, default)
    return value


BOT_TOKEN = get_env("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN environment variable must be set. Set it to your Telegram bot token."
    )

MESSAGE_TEXT = get_env(
    "MESSAGE_TEXT",
    "Отметь атт https://lms.astanait.edu.kz/",
)

# Path to a JSON file used to persist the list of subscriber chat IDs.
SUBSCRIBERS_PATH = Path("subscribers.json")

# Configure basic logging.
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def load_subscribers() -> List[int]:
    """Load the list of subscriber chat IDs from the JSON file.

    Returns an empty list if the file does not exist or is invalid.
    """
    if not SUBSCRIBERS_PATH.exists():
        return []
    try:
        with SUBSCRIBERS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        # Ensure we only store integers to avoid security issues.
        return [int(x) for x in data if isinstance(x, int) or (isinstance(x, str) and x.lstrip("-").isdigit())]
    except Exception as exc:
        logger.warning("Failed to load subscribers from %s: %s", SUBSCRIBERS_PATH, exc)
        return []


def save_subscribers(chat_ids: List[int]) -> None:
    """Save the list of subscriber chat IDs to the JSON file."""
    try:
        with SUBSCRIBERS_PATH.open("w", encoding="utf-8") as f:
            json.dump(chat_ids, f)
    except Exception as exc:
        logger.error("Failed to save subscribers to %s: %s", SUBSCRIBERS_PATH, exc)


def add_subscriber(chat_id: int) -> None:
    """Add a chat ID to the subscriber list if it is not already present."""
    subscribers = load_subscribers()
    if chat_id not in subscribers:
        subscribers.append(chat_id)
        save_subscribers(subscribers)
        logger.info("Added subscriber %s", chat_id)


def remove_subscriber(chat_id: int) -> None:
    """Remove a chat ID from the subscriber list if present."""
    subscribers = load_subscribers()
    if chat_id in subscribers:
        subscribers.remove(chat_id)
        save_subscribers(subscribers)
        logger.info("Removed subscriber %s", chat_id)


def send_message(chat_id: int, text: str) -> None:
    """Send a message via Telegram Bot API to the specified chat ID.

    Any errors are logged but not raised because we don't want a failure
    sending to one subscriber to prevent sending to others.
    """
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        logger.info("Sent message to %s", chat_id)
    except Exception as exc:
        logger.error("Failed to send message to %s: %s", chat_id, exc)


app = FastAPI()


@app.post("/webhook")
async def telegram_webhook(request: Request) -> Dict[str, Any]:
    """Handle incoming Telegram updates via webhook.

    This endpoint expects a JSON payload from Telegram.  When a user
    sends `/start` the chat ID is stored and they begin receiving
    reminders.  When they send `/stop` the chat ID is removed.
    """
    update = await request.json()
    message: Dict[str, Any] | None = update.get("message") or update.get("edited_message")
    if not message:
        # Nothing to do for updates without a message.
        return {"ok": True}

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    if chat_id is None:
        return {"ok": True}

    text = message.get("text", "")
    if not text:
        # Ignore non‑text messages.
        return {"ok": True}

    if text.startswith("/start"):
        add_subscriber(int(chat_id))
        send_message(int(chat_id), "Вы успешно подписались на напоминания.")
    elif text.startswith("/stop"):
        remove_subscriber(int(chat_id))
        send_message(int(chat_id), "Вы успешно отписались от напоминаний.")

    return {"ok": True}


@app.get("/health")
async def health() -> Dict[str, str]:
    """Return a simple health check response."""
    return {"status": "ok"}


@app.get("/tick")
async def tick() -> Dict[str, Any]:
    """Send the reminder message to all current subscribers.

    This endpoint can be triggered by an external scheduler (cron‑job.org) at
    the desired times.  It loads the subscriber list from disk and sends
    the configured message to each chat ID.  It returns a simple JSON
    object indicating how many recipients were processed.
    """
    subscribers = load_subscribers()
    if not subscribers:
        logger.info("No subscribers to notify.")
        return {"sent": 0}
    for chat_id in subscribers:
        send_message(int(chat_id), MESSAGE_TEXT)
    logger.info("Sent reminder to %d subscriber(s)", len(subscribers))
    return {"sent": len(subscribers)}