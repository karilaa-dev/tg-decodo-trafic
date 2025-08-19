# Decodo Usage Telegram Bot (aiogram, single file)

Minimal Telegram bot that shows current Decodo usage. Implemented with aiogram v3 in a single Python file (`bot.py`).

## Commands
- /start — greet
- /usage — show traffic used/limit/remaining
- /chart — send a daily usage chart image for the subscription window (or current month)

## Configuration (env vars)
- DECODO_API_KEY — Decodo Public API key
- TELEGRAM_BOT_TOKEN — Telegram bot token from BotFather
- TELEGRAM_ALLOWED_CHAT_IDS — optional comma-separated chat IDs to allow
- DECODO_SERVICE_TYPE — optional service type (default: `mobile_proxies`)
- DECODO_SUBSCRIPTION_LIMIT_GB — optional plan traffic limit in GB
- DECODO_SUBSCRIPTION_START_DATE — optional anchor start date (`YYYY-MM-DD`)
- DECODO_SUBSCRIPTION_END_DATE — optional fixed end date (`YYYY-MM-DD`)

You can copy `.env.example` to `.env` and fill values; it's auto-loaded if present.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

# Docker Usage

To build and run the bot in Docker:

```sh
# Build the image
docker build -t decodo-stats-bot .

# Run the container (pass .env file if needed)
docker run --env-file .env decodo-stats-bot
```

You can also set environment variables directly in your deployment environment.

# GitHub Packages Docker Image

After each push to `main`, the Docker image is published to GitHub Packages:

```sh
docker pull ghcr.io/karilaa-dev/tg-decodo-trafic:latest
```

To run the image:
```sh
docker run --env-file .env ghcr.io/karilaa-dev/tg-decodo-trafic:latest
```
