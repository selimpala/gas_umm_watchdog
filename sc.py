import os
import json
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

# ================== CONFIG ==================

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

STATE_FILE = "last_message_id.txt"

URL = "https://pip.ipex.it/PipWa/Front/LoadDataMarketInformations"

PARAMS = {
    "page": 1,
    "pageSize": 50,
    "sortColumn": "Published",
    "sortDirection": "desc",
    "eventStatus": "Active"
}

HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://pip.ipex.it/PipWa/Front/",
    "User-Agent": "Mozilla/5.0"
}

# ================== TELEGRAM ==================

def send_telegram(msg: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": msg,
        "disable_web_page_preview": True
    }
    r = requests.post(url, json=payload, timeout=15)
    r.raise_for_status()

# ================== STATE ==================

def load_last_id():
    if not os.path.exists(STATE_FILE):
        return 0
    try:
        with open(STATE_FILE, "r") as f:
            return int(f.read().strip())
    except:
        return 0

def save_last_id(mid: int):
    with open(STATE_FILE, "w") as f:
        f.write(str(mid))

# ================== MAIN ==================

def main():
    now = datetime.now(timezone.utc).isoformat()
    print(f"\n⏱️ Kontrol zamanı: {now}")

    r = requests.get(URL, headers=HEADERS, params=PARAMS, timeout=30)
    r.raise_for_status()

    data = r.json()

    items = data.get("marketinformationsList", [])
    print(f"📦 Toplam kayıt: {len(items)}")

    if not items:
        print("ℹ️ Feed boş.")
        return

    last_seen = load_last_id()
    max_id = last_seen
    new_items = []

    for item in items:
        mid = int(item.get("MessageId", 0))
        if mid > last_seen:
            new_items.append(item)
            max_id = max(max_id, mid)

    if not new_items:
        print("ℹ️ Yeni haber yok.")
        return

    print(f"🚨 {len(new_items)} YENİ HABER VAR!")

    new_items.sort(key=lambda x: x["MessageId"])

    for it in new_items:
        published_raw = it.get("Published", "")
        text = it.get("Remarks", "").strip()
        operator = it.get("OperatoreCodeMask", "N/A")
        mid = it.get("MessageId")

        msg = (
            f"🚨 IPEX NEW MARKET INFO\n\n"
            f"🆔 ID: {mid}\n"
            f"🏢 Operator: {operator}\n"
            f"📝 {text}"
        )

        send_telegram(msg)

    save_last_id(max_id)
    print("✅ Telegram gönderildi, state güncellendi.")

# ================== RUN ==================

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("❌ Hata:", e)
