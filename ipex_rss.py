"""
ipex_watchdog.py
----------------
Monitors the IPEX (Italian Power Exchange) PIP RSS feed for new Market
Information notices and forwards them to a Telegram channel.

Environment variables (via .env or system):
    TELEGRAM_TOKEN  – Bot API token issued by @BotFather
    CHAT_ID         – Target Telegram chat / channel ID
"""

import html
import logging
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from logging.handlers import RotatingFileHandler

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(BASE_DIR, exist_ok=True)

STATE_FILE      = os.path.join(BASE_DIR, "seen_ipex.txt")
LOG_FILE        = os.path.join(BASE_DIR, "ipex_watchdog.log")
FEED_URL        = "https://pip.ipex.it/PipWa/Front/GetAcerFeedsMarketInformations"
CHECK_INTERVAL  = 120  # seconds between feed polls
REQUEST_TIMEOUT = (10, 60)  # (connect timeout, read timeout) in seconds

HEADERS = {
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36"
    ),
    "Referer": "https://pip.ipex.it/PipWa/Front/",
}

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID        = os.getenv("CHAT_ID")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("IPEX_WATCHDOG")
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
# Persistence helpers
# ---------------------------------------------------------------------------


def load_seen_ids() -> set:
    """Return the set of already-processed entry IDs from disk."""
    if not os.path.exists(STATE_FILE):
        return set()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            return {line.strip() for line in fh if line.strip()}
    except OSError as exc:
        logger.error("Failed to read seen-IDs file: %s", exc)
        return set()


def save_seen_ids(ids: set) -> None:
    """Overwrite the seen-IDs file with the current complete set."""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as fh:
            for entry_id in ids:
                fh.write(entry_id + "\n")
    except OSError as exc:
        logger.error("Failed to write seen-IDs file: %s", exc)


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
            if attempt < retries:
                wait = backoff * attempt
                logger.info("Retrying in %ds…", wait)
                time.sleep(wait)

    logger.error("All %d Telegram dispatch attempts failed. Message dropped.", retries)
    return False


# ---------------------------------------------------------------------------
# Feed parsing
# ---------------------------------------------------------------------------


def parse_feed(xml_text: str) -> list[dict]:
    """
    Parse an RSS XML payload and return a list of entry dicts.

    Each dict contains: ``id``, ``title``, ``description``, ``link``,
    ``pubDate``.  Entries without an ID are silently discarded.
    """
    entries = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.error("XML parse error: %s", exc)
        return entries

    for item in root.findall(".//item"):
        entry = {
            "id":          (item.findtext("guid") or item.findtext("link") or "").strip(),
            "title":       (item.findtext("title") or "").strip(),
            "description": (item.findtext("description") or "").strip(),
            "link":        (item.findtext("link") or "").strip(),
            "pubDate":     (item.findtext("pubDate") or "").strip(),
        }
        if entry["id"]:
            entries.append(entry)

    return entries


def extract_description(raw: str) -> str:
    """
    Extract human-readable text from the RSS description field.

    Attempts to pull content from ``<ns1:remarks>`` elements first;
    falls back to stripping all XML/HTML tags.
    """
    match = re.search(
        r"<ns1:remarks[^>]*>(.*?)</ns1:remarks>", raw, re.IGNORECASE | re.DOTALL
    )
    if match:
        return match.group(1).strip()

    # Strip tags and clean CDATA wrappers.
    clean = re.sub(r"<[^>]+>", "", raw)
    clean = clean.replace("<![CDATA[", "").replace("]]>", "")
    return clean.strip()


# ---------------------------------------------------------------------------
# Main polling cycle
# ---------------------------------------------------------------------------


def run_once() -> None:
    """Fetch the IPEX RSS feed once and dispatch alerts for new entries."""
    logger.info("Polling IPEX feed…")

    resp = requests.get(FEED_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()

    content_type = resp.headers.get("Content-Type", "")
    if "json" in content_type:
        logger.warning(
            "Feed endpoint returned JSON instead of XML. "
            "Verify the feed URL is still correct."
        )
        return

    entries = parse_feed(resp.text)
    logger.info("Feed contains %d item(s).", len(entries))

    if not entries:
        logger.warning("Feed is empty or could not be parsed.")
        return

    seen_ids  = load_seen_ids()
    logger.info("%d previously seen ID(s) loaded from disk.", len(seen_ids))

    new_entries = [e for e in entries if e["id"] not in seen_ids]

    if not new_entries:
        logger.info("No new entries found.")
        return

    logger.info("%d new entry/entries detected. Dispatching notifications…", len(new_entries))

    for entry in new_entries:
        description = extract_description(entry["description"])
        safe_title  = html.escape(entry["title"])
        safe_desc   = html.escape(description)

        message = (
            f"🚨 <b>IPEX NEW MARKET INFO</b>\n\n"
            f"📌 <b>{safe_title}</b>\n\n"
            f"📝 {safe_desc}\n\n"
            f"📅 {entry['pubDate']}"
        )
        if entry["link"]:
            message += f"\n🔗 {entry['link']}"

        if send_telegram(message):
            logger.info("Notification dispatched: %.60s", entry["title"])
            time.sleep(1)  # Brief pause to respect Telegram rate limits.
        else:
            logger.error(
                "Failed to dispatch notification for: %.60s", entry["title"]
            )

    # Persist the union of previously known and newly seen IDs.
    all_ids = seen_ids | {e["id"] for e in entries}
    save_seen_ids(all_ids)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info(
        "IPEX RSS Watchdog started. Seen-IDs file: %s | Poll interval: %ds",
        STATE_FILE,
        CHECK_INTERVAL,
    )
    while True:
        try:
            run_once()
        except requests.exceptions.Timeout:
            logger.warning("Feed request timed out. Will retry on next cycle.")
        except requests.exceptions.ConnectionError as exc:
            logger.error("Connection error while polling feed: %s", exc)
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Unexpected error: %s", exc)

        time.sleep(CHECK_INTERVAL)
