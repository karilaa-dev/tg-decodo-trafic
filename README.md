# Decodo Usage Telegram Bot (aiogram, single file)

Minimal Telegram bot that shows current Decodo usage. Implemented with aiogram v3 in a single Python file (`bot.py`).

## Commands
- /start — greet
- /usage — show traffic used/limit/remaining
- /chart — send a daily usage chart image for the subscription window (or current month)

## Configuration (env vars)
- DECODO_API_KEY — Decodo Public API key (passed as raw value in `Authorization` header)
- TELEGRAM_BOT_TOKEN — Telegram bot token from BotFather
- TELEGRAM_ALLOWED_CHAT_IDS — optional comma-separated chat IDs to allow (e.g. `123,456`)
- DECODO_SERVICE_TYPE — optional service type (examples: `mobile_proxies`, `residential_proxies`, `rtc_universal_proxies`, `rtc_universal_core_proxies`, `rtc_site_unblocker_proxies`, `rtc_site_unblocker_req_proxies`, `datacenter_proxies`). It is mapped to Decodo statistics `proxyType` and fallbacks will be tried automatically. Default: `mobile_proxies`.
 - DECODO_SUBSCRIPTION_LIMIT_GB — optional number; your plan's total traffic limit in GB (used for Remaining calculation)
 - DECODO_SUBSCRIPTION_START_DATE — optional anchor start date; accepts `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS` (UTC). When set, the bot automatically calculates a rolling monthly window anchored to this day-of-month and ending at "now". You no longer need to update END every month.
 - DECODO_SUBSCRIPTION_END_DATE — optional fixed end date; accepts `YYYY-MM-DD` or timestamp `YYYY-MM-DD HH:MM:SS` (UTC). Used only when START is not set.

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

## Notes
- aiogram docs: https://docs.aiogram.dev/en/latest/
- Decodo Public API calls:
	- Traffic stats: `POST https://api.decodo.com/api/v2/statistics/traffic`
		- Body keys: `startDate`, `endDate`, `proxyType`, `groupBy` (required)
		- Dates are treated as UTC; format `YYYY-MM-DD HH:MM:SS`
		- `proxyType` examples: `residential_proxies`, `mobile_proxies`, `rtc_universal_proxies`, `rtc_universal_core_proxies`, `rtc_site_unblocker_proxies`, `rtc_site_unblocker_req_proxies`, `datacenter_proxies`.
	- Auth: pass your API key as plain value in the `Authorization` header (no `Bearer`).

### Daily chart
The bot includes a “Daily chart” button (and `/chart` command) that sends a PNG bar chart of GB per day for the configured subscription window (via `DECODO_SUBSCRIPTION_START_DATE`/`END_DATE`) or for the current month when not configured. Images are generated with matplotlib and uploaded using aiogram’s BufferedInputFile. Matplotlib is listed in `requirements.txt`.

### Subscription window behavior
If `DECODO_SUBSCRIPTION_START_DATE` is set, the bot auto-advances the window each month:
- The period starts on the anchor day-of-month (clamped to the month's last day if needed, e.g., 31 → Feb 28/29).
- The period ends at the current time (UTC) for API queries and is displayed as today's date in your configured timezone.
- `DECODO_SUBSCRIPTION_END_DATE` is ignored when a START is present.

If `DECODO_SUBSCRIPTION_START_DATE` is not set and `DECODO_SUBSCRIPTION_END_DATE` is provided, the bot uses a fixed window ending at END. The daily chart spans that calendar month up to END.

### About subscriptions endpoint
This bot no longer calls subscription-related endpoints (`/v2/subscriptions`, `/v2/sub-users`, `/v2/allocated-traffic-limit`). If you want a limit and period shown, provide them via env: `DECODO_SUBSCRIPTION_LIMIT_GB`, `DECODO_SUBSCRIPTION_START_DATE`, and `DECODO_SUBSCRIPTION_END_DATE`. If those are omitted, the bot shows current month usage and will include Remaining only if a limit is set.

### Troubleshooting 400 on /usage
- Traffic 400: ensure `.env` `DECODO_SERVICE_TYPE` maps to your product. The bot will also try sensible fallbacks automatically. Confirm the date range (bot uses current month, UTC) and that `groupBy` is set (the bot defaults to `day`).
- Subscriptions 400: this can happen for non-residential products. The bot will proceed without subscription info and compute usage from traffic bytes. Limits/remaining will be omitted in that case.
