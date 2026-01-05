import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, Request, Response


def get_env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


BOT_TOKEN = get_env("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable must be set.")

MESSAGE_TEXT = get_env("MESSAGE_TEXT", "Отметь атт https://lms.astanait.edu.kz/")

# Optional: for /tick_test only
ADMIN_CHAT_ID = get_env("ADMIN_CHAT_ID", "")

SUBSCRIBERS_PATH = Path("subscribers.json")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reminderbot")

app = FastAPI()


def load_subscribers() -> List[int]:
    if not SUBSCRIBERS_PATH.exists():
        return []
    try:
        with SUBSCRIBERS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        out: List[int] = []
        for x in data:
            if isinstance(x, int):
                out.append(x)
            elif isinstance(x, str) and x.lstrip("-").isdigit():
                out.append(int(x))
        # remove duplicates while preserving order
        seen = set()
        uniq = []
        for cid in out:
            if cid not in seen:
                seen.add(cid)
                uniq.append(cid)
        return uniq
    except Exception as exc:
        logger.warning("Failed to load subscribers: %s", exc)
        return []


def save_subscribers(chat_ids: List[int]) -> None:
    try:
        with SUBSCRIBERS_PATH.open("w", encoding="utf-8") as f:
            json.dump(chat_ids, f)
    except Exception as exc:
        logger.error("Failed to save subscribers: %s", exc)


def add_subscriber(chat_id: int) -> None:
    subs = load_subscribers()
    if chat_id not in subs:
        subs.append(chat_id)
        save_subscribers(subs)
        logger.info("Added subscriber %s", chat_id)


def remove_subscriber(chat_id: int) -> None:
    subs = load_subscribers()
    if chat_id in subs:
        subs.remove(chat_id)
        save_subscribers(subs)
        logger.info("Removed subscriber %s", chat_id)


def send_message(chat_id: int, text: str) -> bool:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        return True
    except Exception as exc:
        logger.error("Failed to send message to %s: %s", chat_id, exc)
        return False


def parse_message(update: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    msg = update.get("message") or update.get("edited_message")
    if isinstance(msg, dict):
        return msg
    return None


@app.post("/webhook")
async def webhook(request: Request) -> Dict[str, Any]:
    update = await request.json()
    msg = parse_message(update)
    if not msg:
        return {"ok": True}

    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return {"ok": True}

    text = msg.get("text") or ""
    if not isinstance(text, str) or not text:
        return {"ok": True}

    cid = int(chat_id)

    # Commands
    if text.startswith("/start"):
        add_subscriber(cid)
        send_message(cid, "Подписка активна. Напоминания будут приходить по расписанию.")
        return {"ok": True}

    if text.startswith("/stop"):
        remove_subscriber(cid)
        send_message(cid, "Вы отписались от напоминаний.")
        return {"ok": True}

    if text.startswith("/count"):
        subs = load_subscribers()
        send_message(cid, f"Подписчиков: {len(subs)}")
        return {"ok": True}

    if text.startswith("/whoami"):
        send_message(cid, f"Ваш chat_id: {cid}")
        return {"ok": True}

    return {"ok": True}


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"ok": True}


@app.get("/tick")
async def tick() -> Response:
    """
    Send reminder to ALL subscribers.

    IMPORTANT: Always return 204 No Content (empty body) to avoid cron-job.org
    failures like "output too large", even if something goes wrong.
    """
    try:
        subs = load_subscribers()
        if subs:
            for cid in subs:
                send_message(int(cid), MESSAGE_TEXT)
            logger.info("Tick sent to %d subscriber(s)", len(subs))
        else:
            logger.info("Tick: no subscribers")
    except Exception as exc:
        # Do not return error body; just log.
        logger.error("Tick failed: %s", exc)

    return Response(status_code=204)


@app.get("/tick_test")
async def tick_test() -> Response:
    """
    Send reminder ONLY to ADMIN_CHAT_ID (for testing).
    Returns empty response.
    """
    try:
        if ADMIN_CHAT_ID and ADMIN_CHAT_ID.lstrip("-").isdigit():
            send_message(int(ADMIN_CHAT_ID), MESSAGE_TEXT)
            return Response(status_code=204)
        return Response(status_code=400)
    except Exception as exc:
        logger.error("Tick_test failed: %s", exc)
        return Response(status_code=204)
