# Energy Market RSS Watchdogs

Lightweight Python service that monitors RSS feeds from **GIE** (Gas Infrastructure Europe) and **IPEX** (Italian Power Exchange) for new market notices and dispatches real-time alerts to a Telegram channel.

---

## Watchdogs

| Module | Feed | Description |
|---|---|---|
| `gie_rss_watchdog.py` | `https://iip.gie.eu/rss` | GIE IIP Urgent Market Messages (UMMs) |
| `ipex_watchdog.py` | `https://pip.ipex.it/PipWa/Front/GetAcerFeedsMarketInformations` | IPEX PIP Market Information notices |

Both watchdogs poll their respective feeds every **120 seconds** and persist seen entry IDs to disk so no duplicate notifications are sent across restarts.

---

## Requirements

- Python 3.11+
- A Telegram bot token and target chat/channel ID

---

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/your-org/energy-market-watchdog.git
cd energy-market-watchdog
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

Copy the example file and fill in your credentials:

```bash
cp .env.example .env
```

`.env.example`:

```
TELEGRAM_TOKEN=your_bot_token_here
CHAT_ID=your_chat_id_here
```

### 4. Run

**Both watchdogs together (recommended):**

```bash
python run_all.py
```

**Individually:**

```bash
python gie_rss_watchdog.py
python ipex_watchdog.py
```

---

## Deployment (Heroku)

The included `Procfile` defines a single `worker` dyno:

```
worker: python run_all.py
```

Set the required config vars in the Heroku dashboard or via the CLI:

```bash
heroku config:set TELEGRAM_TOKEN=... CHAT_ID=...
```

---

## Architecture

```
run_all.py  (supervisor)
├── gie_rss_watchdog.py   → polls GIE RSS → Telegram
└── ipex_watchdog.py      → polls IPEX RSS → Telegram
```

The supervisor monitors both child processes and exits with a non-zero status code if either one dies unexpectedly, allowing the process manager to restart the entire suite automatically.

### Data directory

Both watchdogs write state and logs under a `data/` subdirectory relative to the project root:

```
data/
├── seen_gie.txt        # MD5 hashes of processed GIE entries
├── seen_ipex.txt       # Entry IDs of processed IPEX entries
├── gie_watchdog.log    # Rotating log (1 MB × 2 backups)
└── ipex_watchdog.log   # Rotating log (1 MB × 2 backups)
```

> **Note:** Add `data/` to your `.gitignore` to avoid committing runtime state.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_TOKEN` | ✅ | Bot API token from [@BotFather](https://t.me/BotFather) |
| `CHAT_ID` | ✅ | Telegram chat or channel ID to receive alerts |

---

## License

MIT
