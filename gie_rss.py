"""
gie_rss_watchdog.py
-------------------
Monitors the GIE (Gas Infrastructure Europe) IIP RSS feed for new Urgent
Market Messages (UMMs) and forwards them to a Telegram channel.

Environment variables (via .env or system):
    TELEGRAM_TOKEN  – Bot API token issued by @BotFather
    CHAT_ID         – Target Telegram chat / channel ID
"""

import feedparser
import hashlib
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler

import requests
import truststore
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(BASE_DIR, exist_ok=True)

SEEN_FILE       = os.path.join(BASE_DIR, "seen_gie.txt")
LOG_FILE        = os.path.join(BASE_DIR, "gie_watchdog.log")
FEED_URL        = "https://iip.gie.eu/rss"
CHECK_INTERVAL  = 120  # seconds between feed polls
REQUEST_TIMEOUT = 30   # seconds

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36"
    )
}

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID        = os.getenv("CHAT_ID")

# Inject the system's trusted CA store so corporate proxies are handled
# transparently without disabling SSL verification.
truststore.inject_into_ssl()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("GIE_WATCHDOG")
logger.setLevel(logging.INFO)

_fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")

_file_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=1_048_576, backupCount=2, encoding="utf-8"
)
_file_handler.setFormatter(_fmt)

_stream_handler = logging.StreamHandler(sys.stdout)
_stream_handler.setFormatter(_fmt)

logger.addHandler(_file_handler)
logger.addHandler(_stream_handler)

# ---------------------------------------------------------------------------
# HTTP session (shared across calls for connection reuse)
# ---------------------------------------------------------------------------

session = requests.Session()

# Per-URL conditional-GET state  {url: {"etag": ..., "last_modified": ...}}
_feed_state: dict = {}

# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def load_seen_ids() -> set:
    """Return the set of already-processed entry IDs from disk."""
    if not os.path.exists(SEEN_FILE):
        open(SEEN_FILE, "a").close()
        return set()
    with open(SEEN_FILE, "r", encoding="utf-8") as fh:
        return {line.strip() for line in fh if line.strip()}


def persist_seen_id(uid: str) -> None:
    """Append a single processed entry ID to the seen file."""
    try:
        with open(SEEN_FILE, "a", encoding="utf-8") as fh:
            fh.write(uid + "\n")
    except OSError as exc:
        logger.error("Failed to write seen-ID to disk: %s", exc)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------


def send_telegram(message: str, retries: int = 3, backoff: int = 5) -> bool:
    """
    Send *message* to the configured Telegram chat.

    Retries up to *retries* times with linear back-off on transient failures.
    Respects Telegram's ``retry_after`` directive on HTTP 429 responses.

    Returns ``True`` on success, ``False`` if all attempts are exhausted.
    """
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN is not set. Cannot dispatch notification.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(url, json=payload, timeout=10)

            if resp.status_code == 200:
                return True

            if resp.status_code == 429:
                retry_after = int(
                    resp.json().get("parameters", {}).get("retry_after", 30)
                )
                logger.warning(
                    "Telegram rate-limit hit. Waiting %ds before retry.", retry_after
                )
                time.sleep(retry_after)
                continue

            logger.error(
                "Telegram returned HTTP %d: %s", resp.status_code, resp.text
            )
            return False

        except requests.RequestException as exc:
            logger.warning(
                "Telegram dispatch attempt %d/%d failed: %s", attempt, retries, exc
            )
            # Reset the connection pool so the next attempt opens a fresh socket.
            session.close()
            if attempt < retries:
                wait = backoff * attempt
                logger.info("Retrying in %ds…", wait)
                time.sleep(wait)

    logger.error("All %d Telegram dispatch attempts failed. Message dropped.", retries)
    return False


# ---------------------------------------------------------------------------
# Feed utilities
# ---------------------------------------------------------------------------


def build_entry_id(entry: dict) -> str:
    """
    Derive a stable, unique identifier for a feed entry.

    Priority: ``guid`` → ``link`` → MD5 of ``summary``.
    The result is salted with the publication/update timestamp to detect
    edits to the same item.
    """
    base = entry.get("guid") or entry.get("link") or "NO_ID"
    date = entry.get("updated") or entry.get("published")
    if not date:
        date = hashlib.md5(entry.get("summary", "").encode()).hexdigest()
    return hashlib.md5(f"GIE_{base}_{date}".encode()).hexdigest()


# ---------------------------------------------------------------------------
# Main polling cycle
# ---------------------------------------------------------------------------


def run_once() -> None:
    """Fetch the GIE RSS feed once and dispatch alerts for new entries."""
    seen = load_seen_ids()
    state = _feed_state.get(FEED_URL, {})

    headers = HEADERS.copy()
    if state.get("etag"):
        headers["If-None-Match"] = state["etag"]
    if state.get("last_modified"):
        headers["If-Modified-Since"] = state["last_modified"]

    try:
        resp = session.get(FEED_URL, headers=headers, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        logger.error("Feed request failed: %s", exc)
        return

    if resp.status_code == 304:
        logger.info("Feed unchanged (HTTP 304). No action required.")
        return

    if resp.status_code != 200:
        logger.warning("Unexpected HTTP status %d from feed.", resp.status_code)
        return

    # Store conditional-GET validators for the next poll.
    _feed_state[FEED_URL] = {
        "etag": resp.headers.get("ETag"),
        "last_modified": resp.headers.get("Last-Modified"),
    }

    feed = feedparser.parse(resp.content)

    if not feed.entries:
        logger.info("Feed returned no entries.")
        return

    logger.info("Feed fetched successfully. Scanning for new entries…")
    dispatched = 0

    # Iterate oldest-first so notifications arrive in chronological order.
    for entry in reversed(feed.entries):
        uid = build_entry_id(entry)
        if uid in seen:
            continue

        title = entry.get("title", "(no title)")
        link  = entry.get("link", "")
        text  = f"🇪🇺 <b>GIE UMM</b>\n\n{title}\n🔗 {link}"

        if send_telegram(text):
            persist_seen_id(uid)
            seen.add(uid)
            logger.info("Notification dispatched: %.60s", title)
            dispatched += 1
            time.sleep(1)  # Brief pause to respect Telegram rate limits.
        else:
            logger.error("Failed to dispatch notification for: %.60s", title)

    if dispatched == 0:
        logger.info("No new entries found.")
    else:
        logger.info("%d new notification(s) dispatched.", dispatched)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info(
        "GIE RSS Watchdog started. Seen-IDs file: %s | Poll interval: %ds",
        SEEN_FILE,
        CHECK_INTERVAL,
    )
    while True:
        run_once()
        time.sleep(CHECK_INTERVAL)
