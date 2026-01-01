"""Simple Telegram reminder bot for Render free tier.

This script implements a minimal FastAPI application that works with a
Telegram bot. People can subscribe to reminders by sending the `/start`
command to the bot. The bot stores each subscriber's chat ID in a local
JSON file.

A scheduled HTTP job can trigger the `/tick` endpoint to send reminder
messages to all subscribers. Because the bot is exposed via HTTP, services
like cron-job.org can call the `/tick` endpoint at the desired times.

Endpoints:
- POST /webhook   Telegram webhook receiver
- GET  /tick      Send reminder to ALL subscribers (returns {"ok": true})
- GET  /tick_test Send reminder ONLY to ADMIN_CHAT_ID (returns {"ok": true})
- GET  /health    Basic health check
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
    return os.environ.get(name, default)


BOT_TOKEN = get_env("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable must be set.")

MESSAGE_TEXT = get_env(
    "MESSAGE_TEXT",
    "Отметь атт https://lms.astanait.edu.kz/",
)

# Admin chat id for test-only sending (optional, but required for /tick_test).
ADMIN_CHAT_ID = get_env("ADMIN_CHAT_ID", "")

# Path to a JSON file used to persist the list of subscriber chat IDs.
SUBSCRIBERS_PATH = Path("subscribers.json")

# Configure basic logging.
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def load_subscribers() -> List[int]:
    """Load subscriber chat IDs from JSON file."""
    if not SUBSCRIBERS_PATH.exists():
        return []
    try:
        with SUBSCRIBERS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        # Ensure we only store integers.
        out: List[int] = []
        for x in data:
            if isinstance(x, int):
                out.append(x)
            elif isinstance(x, str) and x.lstrip("-").isdigit():
                out.append(int(x))
        return out
    except Exception as exc:
        logger.warning("Failed to load subscribers from %s: %s", SUBSCRIBERS_PATH, exc)
        return []


def save_subscribers(chat_ids: List[int]) -> None:
    """Save subscriber chat IDs to JSON file."""
    try:
        with SUBSCRIBERS_PATH.open("w", encoding="utf-8") as f:
            json.dump(chat_ids, f)
    except Exception as exc:
        logger.error("Failed to save subscribers to %s: %s", SUBSCRIBERS_PATH, exc)


def add_subscriber(chat_id: int) -> None:
    """Add chat_id if not present."""
    subscribers = load_subscribers()
    if chat_id not in subscribers:
        subscribers.append(chat_id)
        save_subscribers(subscribers)
        logger.info("Added subscriber %s", chat_id)


def remove_subscriber(chat_id: int) -> None:
    """Remove chat_id if present."""
    subscribers = load_subscribers()
    if chat_id in subscribers:
        subscribers.remove(chat_id)
        save_subscribers(subscribers)
        logger.info("Removed subscriber %s", chat_id)


def send_message(chat_id: int, text: str) -> None:
    """Send a Telegram message."""
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
    """Handle incoming Telegram webhook updates."""
    update = await request.json()
    message: Dict[str, Any] | None = update.get("message") or update.get("edited_message")
    if not message:
        return {"ok": True}

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    if chat_id is None:
        return {"ok": True}

    text = message.get("text", "")
    if not text:
        return {"ok": True}

    if text.startswith("/start"):
        add_subscriber(int(chat_id))
        send_message(int(chat_id), "Вы успешно подписались на напоминания.")
    elif text.startswith("/stop"):
        remove_subscriber(int(chat_id))
        send_message(int(chat_id), "Вы успешно отписались от напоминаний.")

    return {"ok": True}


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"ok": True}


@app.get("/tick")
async def tick() -> Dict[str, Any]:
    """Send reminder to all subscribers.

    IMPORTANT: Return minimal JSON to keep cron-job.org happy.
    """
    subscribers = load_subscribers()
    if not subscribers:
        logger.info("No subscribers to notify.")
        return {"ok": True}

    for chat_id in subscribers:
        send_message(int(chat_id), MESSAGE_TEXT)

    logger.info("Sent reminder to %d subscriber(s)", len(subscribers))
    return {"ok": True}


@app.get("/tick_test")
async def tick_test() -> Dict[str, Any]:
    """Send reminder only to admin (for testing)."""
    if not ADMIN_CHAT_ID or not ADMIN_CHAT_ID.lstrip("-").isdigit():
        logger.error("ADMIN_CHAT_ID is not set or invalid.")
        return {"ok": False}

    send_message(int(ADMIN_CHAT_ID), MESSAGE_TEXT)
    return {"ok": True}
