import feedparser
import requests
import os
import time
import hashlib
import logging
import sys
import truststore
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv

# --- AYARLAR ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SEEN_FILE = os.path.join(BASE_DIR, "seen_gie.txt")
LOG_FILE = os.path.join(BASE_DIR, "log_gie.log")
URL = "https://iip.gie.eu/rss"
CHECK_INTERVAL = 120
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0 Safari/537.36"
}

# --- SETUP ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

logger = logging.getLogger("GIE_BOT")
logger.setLevel(logging.INFO)
handler = RotatingFileHandler(LOG_FILE, maxBytes=1024*1024, backupCount=2, encoding="utf-8")
handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(handler)
logger.addHandler(logging.StreamHandler(sys.stdout))

# Inject system trust store into requests
truststore.inject_into_ssl()

session = requests.Session()

FEED_STATE = {}

def get_seen():
    """Okunmuş haberleri dosyadan yükle"""
    if not os.path.exists(SEEN_FILE):
        open(SEEN_FILE, 'a').close()
        return set()
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def save_seen(uid):
    """Yeni ID'yi dosyaya kaydet"""
    try:
        with open(SEEN_FILE, "a", encoding="utf-8") as f:
            f.write(uid + "\n")
    except Exception as e:
        logger.error(f"File write error: {e}")

def send_msg(msg, retries=3, backoff=5):
    if not TELEGRAM_TOKEN:
        logger.error("Telegram Token could not be found!")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    for attempt in range(1, retries + 1):
        try:
            # Fresh session per attempt — avoids reusing a stale/dead socket
            response = requests.post(url, json=payload, timeout=10)

            if response.status_code == 200:
                return True

            elif response.status_code == 429:
                wait = int(response.json().get("parameters", {}).get("retry_after", 30))
                logger.warning(f"Rate limited by Telegram. Waiting {wait}s before retry...")
                time.sleep(wait)

            else:
                logger.error(f"Telegram Error: {response.status_code} - {response.text}")
                return False

        except Exception as e:
            logger.warning(f"Telegram attempt {attempt}/{retries} failed: {e}")
            # Purge the shared session's connection pool so next attempt opens a fresh socket
            session.close()
            if attempt < retries:
                wait = backoff * attempt
                logger.info(f"Retrying in {wait}s...")
                time.sleep(wait)

    logger.error(f"Telegram: All {retries} attempts failed. Message could not be sent.")
    return False

def generate_id(entry):
    base = entry.get("guid") or entry.get("link") or "NO_ID"
    date = entry.get("updated") or entry.get("published")
    if not date:
        date = hashlib.md5(entry.get("summary", "").encode()).hexdigest()
    return hashlib.md5(f"GIE_{base}_{date}".encode()).hexdigest()

def run():
    seen = get_seen()
    state = FEED_STATE.get(URL, {})
    headers = HEADERS.copy()

    if state.get('etag'):
        headers['If-None-Match'] = state['etag']
    if state.get('last_modified'):
        headers['If-Modified-Since'] = state['last_modified']

    try:
        r = session.get(URL, headers=headers, timeout=30)

        if r.status_code == 304:
            return

        if r.status_code == 200:
            FEED_STATE[URL] = {
                'etag': r.headers.get('ETag'),
                'last_modified': r.headers.get('Last-Modified')
            }

            feed = feedparser.parse(r.content)

            if not feed.entries:
                logger.info("Feed is empty.")
                return

            logger.info("Checking for new items...")
            new_count = 0

            # Eskiden yeniye doğru sırala ki sırayla gelsin
            for entry in reversed(feed.entries):
                uid = generate_id(entry)

                if uid in seen:
                    continue

                title = entry.get("title", "No Title")
                msg = f"🇪🇺 GIE UMM\n\n{title}\n🔗 {entry.get('link')}"

                if send_msg(msg):
                    save_seen(uid)
                    seen.add(uid)
                    logger.info(f"NEW MESSAGE SENT: {title[:30]}...")
                    new_count += 1
                    time.sleep(1)  # Telegram spam koruması
                else:
                    logger.error(f"Message could not be sent: {title[:30]}")

            if new_count == 0:
                logger.info("No news.")

        else:
            logger.warning(f"HTTP {r.status_code}")

    except Exception as e:
        logger.error(f"Error: {e}")

if __name__ == "__main__":
    logger.info(f"GIE Bot Started. Folder Location: {SEEN_FILE}")
    while True:
        run()
        time.sleep(CHECK_INTERVAL)
