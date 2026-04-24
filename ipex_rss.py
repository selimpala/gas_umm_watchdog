import os
import time
import requests
import xml.etree.ElementTree as ET
import re
import html
from datetime import datetime, timezone
from dotenv import load_dotenv

# ================== ENV ==================

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# ================== CONFIG ==================

# DOSYA YOLUNU GARANTİYE ALIYORUZ: Script neredeyse, txt dosyası da tam orada olacak.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "last_seen_ids.txt")

CHECK_INTERVAL = 120  # seconds
RSS_URL = "https://pip.ipex.it/PipWa/Front/GetAcerFeedsMarketInformations"

HEADERS = {
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://pip.ipex.it/PipWa/Front/",
}

# ================== HELPERS ==================

def log(msg):
    print(f"{datetime.now(timezone.utc).isoformat()} | {msg}")

def send_telegram(msg: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    r = requests.post(url, json=payload, timeout=20)
    if r.status_code != 200:
        log(f"⚠️ Telegram Gönderim Hatası: {r.text}")
    r.raise_for_status()

def load_seen_ids() -> set:
    if not os.path.exists(STATE_FILE):
        return set()
    try:
        with open(STATE_FILE, "r") as f:
            return set(line.strip() for line in f if line.strip())
    except:
        return set()

def save_seen_ids(ids: set):
    with open(STATE_FILE, "w") as f:
        for id_ in ids:
            f.write(id_ + "\n")

def parse_rss(xml_text: str) -> list:
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log(f"⚠️ XML parse hatası: {e}")
        return items

    for item in root.findall(".//item"):
        entry = {
            "id":          (item.findtext("guid") or item.findtext("link") or "").strip(),
            "title":       (item.findtext("title") or "").strip(),
            "description": (item.findtext("description") or "").strip(),
            "link":        (item.findtext("link") or "").strip(),
            "pubDate":     (item.findtext("pubDate") or "").strip(),
        }
        if entry["id"]:
            items.append(entry)
    return items

# ================== CORE ==================

def check_once():
    log("🔍 IPEX RSS kontrol ediliyor...")

    r = requests.get(RSS_URL, headers=HEADERS, timeout=(10, 60))
    r.raise_for_status()

    content_type = r.headers.get("Content-Type", "")
    
    if "json" in content_type:
        log("⚠️ JSON yanıt geldi, RSS bekleniyor. URL'i kontrol et.")
        return
        
    items = parse_rss(r.text)
    log(f"📦 Feed'deki toplam kayıt: {len(items)}")

    if not items:
        log("⚠️ Feed boş veya parse edilemedi.")
        return

    # Dosyadaki ID'leri oku ve kaç tane olduğunu yazdır
    seen_ids = load_seen_ids()
    log(f"🧠 Hafızada (txt dosyasında) bilinen {len(seen_ids)} kayıt var.")

    new_items = [it for it in items if it["id"] not in seen_ids]

    if not new_items:
        log("ℹ️ Yeni haber yok.")
        return

    log(f"🚨 {len(new_items)} YENİ HABER!")

    for it in new_items:
        raw_desc = it['description']
        
        # XML formatındaki mesajdan asıl metni çıkar
        match = re.search(r'<ns1:remarks[^>]*>(.*?)</ns1:remarks>', raw_desc, re.IGNORECASE | re.DOTALL)
        if match:
            clean_desc = match.group(1).strip()
        else:
            clean_desc = re.sub(r'<[^>]+>', '', raw_desc).replace('<![CDATA[', '').replace(']]>', '').strip()
            
        safe_title = html.escape(it['title'])
        safe_desc = html.escape(clean_desc)

        msg = (
            f"🚨 <b>IPEX NEW MARKET INFO</b>\n\n"
            f"📌 <b>{safe_title}</b>\n\n"
            f"📝 {safe_desc}\n\n"
            f"📅 {it['pubDate']}"
        )
        if it["link"]:
            msg += f"\n🔗 {it['link']}"
            
        send_telegram(msg)
        log(f"✅ Gönderildi: {it['title'][:60]}...")

    # Gönderilen tüm ID'leri kaydet
    all_ids = seen_ids | {it["id"] for it in items}
    save_seen_ids(all_ids)

# ================== LOOP ==================

if __name__ == "__main__":
    log("🟢 IPEX RSS WATCH STARTED (CTRL+C ile durdur)")
    log(f"📂 Kayıt Dosyası Konumu: {STATE_FILE}")

    while True:
        try:
            check_once()
        except requests.exceptions.Timeout:
            log("⏱️ Timeout — sunucu cevap vermedi.")
        except requests.exceptions.ConnectionError as e:
            log(f"🔌 Bağlantı hatası: {e}")
        except Exception as e:
            log(f"❌ Hata: {e}")

        time.sleep(CHECK_INTERVAL)
