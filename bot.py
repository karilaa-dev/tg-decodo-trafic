from __future__ import annotations

import asyncio
import datetime as dt
import logging
import calendar
import os
from typing import Any, Dict, Optional, Set
import io

import httpx
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.types import BufferedInputFile
from aiogram.utils.chat_action import ChatActionSender
from zoneinfo import ZoneInfo


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("decodo-bot")


# -----------------------------
# Env & config helpers
# -----------------------------
def load_env() -> None:
    """Load .env if present (no error if missing)."""
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except Exception:
        # dotenv is optional; ignore if not installed/available
        pass


def parse_allowed_chat_ids(value: Optional[str]) -> Optional[Set[int]]:
    if not value:
        return None
    items: Set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            items.add(int(part))
        except ValueError:
            raise ValueError("TELEGRAM_ALLOWED_CHAT_IDS must be comma-separated integers")
    return items or None


class Settings:
    def __init__(self) -> None:
        load_env()
        self.decodo_api_key: str = os.getenv("DECODO_API_KEY", "")
        self.telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_allowed_chat_ids: Optional[Set[int]] = parse_allowed_chat_ids(
            os.getenv("TELEGRAM_ALLOWED_CHAT_IDS")
        )
        self.decodo_service_type: Optional[str] = os.getenv("DECODO_SERVICE_TYPE", "mobile_proxies")

        # Optional: provide subscription details via env (no calls to subscriptions endpoint)
        limit_str = os.getenv("DECODO_SUBSCRIPTION_LIMIT_GB")
        try:
            self.subscription_limit_gb: Optional[float] = float(limit_str) if limit_str else None
        except ValueError:
            logger.warning("Invalid DECODO_SUBSCRIPTION_LIMIT_GB='%s' (expected number in GB)", limit_str)
            self.subscription_limit_gb = None
        self.subscription_start_date: Optional[str] = os.getenv("DECODO_SUBSCRIPTION_START_DATE") or None
        self.subscription_end_date: Optional[str] = os.getenv("DECODO_SUBSCRIPTION_END_DATE") or None

        # Timezone for display (not for API queries which stay in UTC)
        tz_name = os.getenv("TIMEZONE") or os.getenv("TZ") or "UTC"
        try:
            self.timezone: dt.tzinfo = ZoneInfo(tz_name)
        except Exception:
            logger.warning("Invalid TIMEZONE '%s'; falling back to UTC", tz_name)
            self.timezone = dt.UTC
        self.timezone_name: str = tz_name

    def ensure_valid(self) -> None:
        missing = []
        if not self.decodo_api_key:
            missing.append("DECODO_API_KEY")
        if not self.telegram_bot_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if missing:
            raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")


# -----------------------------
# Decodo API client (async)
# -----------------------------
DECODO_BASE = "https://api.decodo.com"


class DecodoClient:
    def __init__(self, api_key: str, *, timeout: float = 15.0):
        # Public API expects raw API key in Authorization header (no Bearer prefix)
        auth_value = api_key.strip()
        self._headers = {
            "Accept": "application/json",
            "Authorization": auth_value,
        }
        self._client = httpx.AsyncClient(headers=self._headers, timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_subscriptions(self) -> Dict[str, Any]:
        """GET /v2/subscriptions"""
        url = f"{DECODO_BASE}/v2/subscriptions"
        r = await self._client.get(url)
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            # This endpoint may be unsupported for some accounts; keep logs quiet.
            if e.response is not None:
                logger.debug("Subscriptions error %s: %s", e.response.status_code, e.response.text)
            else:
                logger.debug("Subscriptions error: %s", e)
            raise
        return r.json()

    async def get_sub_users(self, *, service_type: Optional[str] = None) -> Dict[str, Any] | list[Dict[str, Any]]:
        """GET /v2/sub-users — available for Residential subscriptions.
        Optionally pass service_type=residential_proxies.
        """
        url = f"{DECODO_BASE}/v2/sub-users"
        params: Dict[str, Any] = {}
        if service_type:
            params["service_type"] = service_type
        r = await self._client.get(url, params=params)
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response is not None:
                logger.debug("Sub-users error %s: %s", e.response.status_code, e.response.text)
            else:
                logger.debug("Sub-users error: %s", e)
            raise
        return r.json()

    async def get_sub_user_traffic(
        self,
        sub_user_id: str,
        *,
        type_: str = "month",
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        service_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """GET /v2/sub-users/{sub_user_id}/traffic — traffic of a specified sub user.
        type_: one of 24h, 7days, month, custom; for custom you may pass from/to (yyyy-mm-dd).
        """
        url = f"{DECODO_BASE}/v2/sub-users/{sub_user_id}/traffic"
        params: Dict[str, Any] = {"type": type_}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        if service_type:
            params["service_type"] = service_type
        r = await self._client.get(url, params=params)
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response is not None:
                logger.debug("Sub-user traffic error %s: %s", e.response.status_code, e.response.text)
            else:
                logger.debug("Sub-user traffic error: %s", e)
            raise
        return r.json()

    async def get_allocated_traffic_limit(self, *, service_type: Optional[str] = None) -> Dict[str, Any]:
        """GET /v2/allocated-traffic-limit — allocated traffic across all sub users (Residential)."""
        url = f"{DECODO_BASE}/v2/allocated-traffic-limit"
        params: Dict[str, Any] = {}
        if service_type:
            params["service_type"] = service_type
        r = await self._client.get(url, params=params)
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response is not None:
                logger.debug("Allocated traffic error %s: %s", e.response.status_code, e.response.text)
            else:
                logger.debug("Allocated traffic error: %s", e)
            raise
        return r.json()

    async def get_traffic(
        self,
        *,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        type_: Optional[str] = None,
        group_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /api/v2/statistics/traffic
        Docs: https://help.decodo.com/reference/get-traffic
        Body keys must be startDate/endDate/proxyType/groupBy.
        """
        url = f"{DECODO_BASE}/api/v2/statistics/traffic"
        payload: Dict[str, Any] = {}
        # Decodo expects 'proxyType' values like 'residential_proxies', 'mobile_proxies',
        # or RTC variants (e.g., 'rtc_universal_proxies').
        if type_:
            payload["proxyType"] = type_
        if from_date:
            payload["startDate"] = from_date
        if to_date:
            payload["endDate"] = to_date
        if group_by:
            payload["groupBy"] = group_by
        else:
            # Decodo requires groupBy; default to 'day' for month-to-date summaries
            payload["groupBy"] = "day"
        r = await self._client.post(url, json=payload)
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            # Log response details to diagnose 400s from API (e.g., required fields, wrong enums)
            logger.error(
                "Decodo traffic error %s: payload=%s response=%s",
                e.response.status_code,
                payload,
                e.response.text,
            )
            raise
        return r.json()

    async def get_current_month_usage(self, *, type_: Optional[str] = None) -> Dict[str, Any]:
        # Use UTC to match Decodo docs (timestamps are treated as UTC)
        now = dt.datetime.now(dt.UTC).replace(microsecond=0)
        start_dt = now.replace(day=1, hour=0, minute=0, second=0)
        def fmt(ts: dt.datetime) -> str:
            # Format without timezone per API examples (timestamps are UTC)
            return ts.strftime("%Y-%m-%d %H:%M:%S")
        return await self.get_traffic(from_date=fmt(start_dt), to_date=fmt(now), type_=type_)


def _map_service_to_proxy_type(value: Optional[str]) -> Optional[str]:
    """Map env DECODO_SERVICE_TYPE to Decodo statistics 'proxyType' values.

    Accepted examples (per docs/examples):
    - residential_proxies
    - mobile_proxies
    - rtc_universal_proxies / rtc_universal_core_proxies (Web Scraping API)
    - rtc_site_unblocker_proxies / rtc_site_unblocker_req_proxies (Site Unblocker)
    - datacenter_proxies (not explicitly documented in stats ref; may not return data for all accounts)
    """
    if not value:
        return None
    v = value.strip().lower()
    mapping = {
        # Proxies
        "residential": "residential_proxies",
        "residential_proxies": "residential_proxies",
        "mobile": "mobile_proxies",
        "mobile_proxies": "mobile_proxies",
        "datacenter": "datacenter_proxies",
        "datacenter_proxies": "datacenter_proxies",
        # Web Scraping API (RTC)
        "rtc_universal_proxies": "rtc_universal_proxies",
        "rtc_universal_core_proxies": "rtc_universal_core_proxies",
        # Site Unblocker
        "site_unblocker": "rtc_site_unblocker_proxies",
        "rtc_site_unblocker_proxies": "rtc_site_unblocker_proxies",
        "rtc_site_unblocker_req_proxies": "rtc_site_unblocker_req_proxies",
    }
    return mapping.get(v, v)

def _first_mapping_candidate(obj: Any) -> Optional[Dict[str, Any]]:
    """Return the first dict-like node from various response shapes."""
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        return obj[0]
    return None


def _extract_subs_info(subscriptions: Dict[str, Any] | list[Dict[str, Any]] | None) -> Dict[str, Any]:
    """Best-effort extraction for subscription details across possible schemas."""
    info: Dict[str, Any] = {
        "limit": None,
        "used": None,
        "period_start": None,
        "period_end": None,
        "plan": None,
    }
    if not isinstance(subscriptions, (dict, list)):
        return info

    roots: list[Any] = [subscriptions]
    if isinstance(subscriptions, dict):
        for k in ("data", "subscription", "result"):
            if k in subscriptions:
                roots.append(subscriptions[k])

    candidates: list[Dict[str, Any]] = []
    for r in roots:
        n = _first_mapping_candidate(r)
        if n:
            candidates.append(n)

    keys_limit = (
        "traffic_limit",
        "limit",
        "data_limit",
        "max_traffic",
        "max_usage",
        "max",
    )
    keys_used = (
        "traffic_per_period",
        "used",
        "usage",
        "traffic_used",
        "data_used",
    )
    keys_start = (
        "current_period_start",
        "period_start",
        "start_date",
        "cycle_start",
        "from",
        "startAt",
        "start_at",
    )
    keys_end = (
        "current_period_end",
        "period_end",
        "end_date",
        "cycle_end",
        "to",
        "endAt",
        "end_at",
        "next_billing_date",
        "renews_at",
        "valid_until",
        "expires_at",
    )
    keys_plan = ("plan", "name", "subscription_plan", "package_name")

    for node in candidates:
        if info["limit"] is None:
            for k in keys_limit:
                if k in node and node[k] is not None:
                    try:
                        info["limit"] = float(node[k])
                    except (TypeError, ValueError):
                        info["limit"] = node[k]
                    break
        if info["used"] is None:
            for k in keys_used:
                if k in node and node[k] is not None:
                    try:
                        info["used"] = float(node[k])
                    except (TypeError, ValueError):
                        info["used"] = node[k]
                    break
        if info["period_start"] is None:
            for k in keys_start:
                if k in node and node[k]:
                    info["period_start"] = str(node[k])
                    break
        if info["period_end"] is None:
            for k in keys_end:
                if k in node and node[k]:
                    info["period_end"] = str(node[k])
                    break
        if info["plan"] is None:
            for k in keys_plan:
                if k in node and node[k]:
                    info["plan"] = str(node[k])
                    break

    return info


def format_usage(
    subscriptions: Dict[str, Any] | list[Dict[str, Any]] | None,
    traffic: Dict[str, Any],
    *,
    timeframe_label: Optional[str] = None,
    proxy_type: Optional[str] = None,
    tz: Optional[dt.tzinfo] = None,
) -> str:
    limit = None
    used = None

    if isinstance(subscriptions, dict):
        limit = subscriptions.get("traffic_limit") or subscriptions.get("limit")
        used_period = subscriptions.get("traffic_per_period")
        if used_period is not None and used is None:
            try:
                used = float(used_period)
            except (TypeError, ValueError):
                used = None
        for key in ("data", "subscription", "result"):
            if key in subscriptions and isinstance(subscriptions[key], dict):
                limit = limit or subscriptions[key].get("traffic_limit")
                used_period = subscriptions[key].get("traffic_per_period")
                if used is None and used_period is not None:
                    try:
                        used = float(used_period)
                    except (TypeError, ValueError):
                        used = None
    elif isinstance(subscriptions, list) and subscriptions:
        first = subscriptions[0]
        if isinstance(first, dict):
            limit = first.get("traffic_limit")
            used_period = first.get("traffic_per_period")
            if used_period is not None:
                try:
                    used = float(used_period)
                except (TypeError, ValueError):
                    used = None

    # Prefer totals in bytes from metadata; fallback to summing rx_tx_bytes
    total_used_gb: Optional[float] = None
    try:
        totals = traffic.get("metadata", {}).get("totals", {}) if isinstance(traffic, dict) else {}
        total_rx_tx = totals.get("total_rx_tx")
        if isinstance(total_rx_tx, (int, float)):
            total_used_gb = float(total_rx_tx) / 1_000_000_000.0  # bytes -> GB (decimal)
    except Exception:
        total_used_gb = None
    if total_used_gb is None:
        try:
            if isinstance(traffic, dict) and isinstance(traffic.get("data"), list):
                total_bytes = 0
                for item in traffic["data"]:
                    if isinstance(item, dict):
                        total_bytes += int(item.get("rx_tx_bytes", 0))
                total_used_gb = float(total_bytes) / 1_000_000_000.0
        except Exception:
            total_used_gb = None

    # If subscription-provided used is present (likely already in GB), prefer it; else use computed GB
    used_final = used if used is not None else total_used_gb
    if used_final is None and limit is None:
        return "Couldn't determine usage from API response."

    subs_info = _extract_subs_info(subscriptions)

    limit_final: Optional[float] = None
    if limit is not None:
        try:
            limit_final = float(limit)
        except (TypeError, ValueError):
            limit_final = None
    if limit_final is None and subs_info.get("limit") is not None:
        try:
            limit_final = float(subs_info["limit"])  # type: ignore[arg-type]
        except (TypeError, ValueError):
            limit_final = None

    def _date_only_text(val: Optional[str]) -> Optional[str]:
        if not val:
            return None
        s = str(val).strip()
        # quick ISO-like cleanup
        s = s.replace("T", " ").replace("Z", "").split(".")[0]
        # prefer first 10 chars if they look like YYYY-MM-DD
        if len(s) >= 10 and s[4] == "-" and s[7] == "-" and s[:10].replace("-", "").isdigit():
            return s[:10]
        # fallback: return original string
        return s

    lines: list[str] = []
    title = "Decodo Usage"
    if proxy_type:
        title += f" — {proxy_type}"
    lines.append(title)

    if timeframe_label:
        # Allow caller to pass in already formatted date-only label
        lines.append(f"Period: {timeframe_label}")
    elif subs_info.get("period_start") or subs_info.get("period_end"):
        ps = _date_only_text(subs_info.get("period_start")) or "?"
        pe = _date_only_text(subs_info.get("period_end")) or "?"
        lines.append(f"Subscription period: {ps} → {pe}")

    if subs_info.get("plan"):
        lines.append(f"Plan: {subs_info['plan']}")

    if used_final is not None and limit_final is not None:
        remaining = max(limit_final - used_final, 0.0)
        lines.append(f"Usage: {used_final:.2f} GB of {limit_final:.2f} GB")
        lines.append(f"Remaining: {remaining:.2f} GB")
    elif used_final is not None:
        lines.append(f"Used: {used_final:.2f} GB")
    elif limit_final is not None:
        lines.append(f"Limit: {limit_final:.2f} GB")

    now = dt.datetime.now(tz or dt.UTC).replace(microsecond=0)
    tz_name = now.tzname() or "UTC"
    lines.append("")
    lines.append(f"As of: {now.strftime('%Y-%m-%d %H:%M:%S')} {tz_name}")

    return "\n".join(lines)


# -----------------------------
# Bot handlers
# -----------------------------
router = Router()


# Simple reply keyboard to avoid typing commands
MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Usage"), KeyboardButton(text="Daily chart")]],
    resize_keyboard=True,
    input_field_placeholder="Choose an action",
)


def _is_allowed(chat_id: int, allowed: Optional[Set[int]]) -> bool:
    return allowed is None or chat_id in allowed


def _build_proxy_type_candidates(env_value: Optional[str]) -> list[Optional[str]]:
    """Return an ordered list of proxyType candidates to try.

    Priority:
    - mapped env value (if provided)
    - None (let API default; often residential_proxies)
    - mobile_proxies, residential_proxies
    - rtc_universal_proxies, rtc_universal_core_proxies (Scraping APIs)
    - datacenter_proxies (may not be supported by statistics)
    - site unblocker variants
    """
    first = _map_service_to_proxy_type(env_value)
    candidates: list[Optional[str]] = []
    if first and first not in candidates:
        candidates.append(first)
    if None not in candidates:
        candidates.append(None)
    for v in (
        "mobile_proxies",
        "residential_proxies",
        "rtc_universal_proxies",
        "rtc_universal_core_proxies",
        "datacenter_proxies",
        "rtc_site_unblocker_proxies",
        "rtc_site_unblocker_req_proxies",
    ):
        if v != first:
            candidates.append(v)
    return candidates


async def _fetch_month_usage_with_fallback(client: DecodoClient, *, proxy_types: list[Optional[str]]) -> Dict[str, Any]:
    """Try month-to-date traffic with several proxyType values until one succeeds."""
    last_err: Optional[Exception] = None
    for pt in proxy_types:
        try:
            return await client.get_current_month_usage(type_=pt)
        except httpx.HTTPStatusError as e:
            # For 400 (bad proxyType or bad request), continue trying
            if e.response is not None and e.response.status_code == 400:
                logger.warning("Decodo traffic 400 with proxyType=%s; trying next candidate. body=%s", pt, e.response.text)
                last_err = e
                continue
            # Other status codes: propagate immediately
            raise
        except Exception as e:
            last_err = e
            logger.warning("Decodo traffic error with proxyType=%s: %s", pt, e)
            continue
    assert last_err is not None
    raise last_err


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer("Hi. Use the button below or send /usage to get Decodo usage.", reply_markup=MAIN_KB)


async def _handle_usage(message: Message, bot: Bot) -> None:
    chat_id = message.chat.id

    # Settings are read on each command to reflect any env changes without restart
    settings = Settings()
    if not _is_allowed(chat_id, settings.telegram_allowed_chat_ids):
        await message.answer("Not authorized.")
        return

    async with ChatActionSender.typing(chat_id=chat_id, bot=bot):
        try:
            client = DecodoClient(settings.decodo_api_key)
            try:
                # Build optional timeframe from env provided subscription dates
                env_from = settings.subscription_start_date
                env_to = settings.subscription_end_date

                def to_ts(date_str: str, end_of_day: bool = False) -> str:
                    # Accept YYYY-MM-DD and expand to full timestamp in UTC (no TZ suffix per API examples)
                    date_str = date_str.strip()
                    if len(date_str) == 10 and date_str[4] == '-' and date_str[7] == '-':
                        return f"{date_str} {'23:59:59' if end_of_day else '00:00:00'}"
                    # If already a timestamp-like string, pass through
                    return date_str

                traffic: Dict[str, Any]
                if env_from:
                    # Rolling monthly window anchored to provided start date; end at cycle end (cap to now)
                    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
                    start_d = _anchored_period_start(env_from, now)
                    end_d = _anchored_period_end(env_from, start_d) if start_d else None
                    from_date = f"{start_d.strftime('%Y-%m-%d')} 00:00:00" if start_d else to_ts(env_from)
                    if end_d:
                        end_dt = dt.datetime(end_d.year, end_d.month, end_d.day, 23, 59, 59, tzinfo=dt.UTC)
                        to_dt = min(now, end_dt)
                    else:
                        to_dt = now
                    to_date = to_dt.strftime("%Y-%m-%d %H:%M:%S")
                    # Query with proxyType fallbacks
                    last_err: Optional[Exception] = None
                    for pt in _build_proxy_type_candidates(settings.decodo_service_type):
                        try:
                            traffic = await client.get_traffic(
                                from_date=from_date,
                                to_date=to_date,
                                type_=pt,
                                group_by="day",
                            )
                            break
                        except httpx.HTTPStatusError as e:
                            if e.response is not None and e.response.status_code == 400:
                                logger.warning("Decodo traffic 400 with proxyType=%s; trying next candidate. body=%s", pt, e.response.text)
                                last_err = e
                                continue
                            raise
                        except Exception as e:
                            last_err = e
                            logger.warning("Decodo traffic error with proxyType=%s: %s", pt, e)
                            continue
                    else:
                        assert last_err is not None
                        raise last_err
                elif env_to:
                    # Fixed window ending at provided end date (legacy behavior)
                    from_date = None
                    to_date = to_ts(env_to, end_of_day=True)
                    last_err: Optional[Exception] = None
                    for pt in _build_proxy_type_candidates(settings.decodo_service_type):
                        try:
                            traffic = await client.get_traffic(
                                from_date=from_date,
                                to_date=to_date,
                                type_=pt,
                                group_by="day",
                            )
                            break
                        except httpx.HTTPStatusError as e:
                            if e.response is not None and e.response.status_code == 400:
                                logger.warning("Decodo traffic 400 with proxyType=%s; trying next candidate. body=%s", pt, e.response.text)
                                last_err = e
                                continue
                            raise
                        except Exception as e:
                            last_err = e
                            logger.warning("Decodo traffic error with proxyType=%s: %s", pt, e)
                            continue
                    else:
                        assert last_err is not None
                        raise last_err
                else:
                    # Default: current month usage with proxyType fallbacks
                    traffic = await _fetch_month_usage_with_fallback(
                        client,
                        proxy_types=_build_proxy_type_candidates(settings.decodo_service_type),
                    )
            finally:
                await client.aclose()

            # Build subscription info from env, and a label
            subs_env: Dict[str, Any] | None = None
            label: Optional[str] = None
            if settings.subscription_start_date:
                # Show anchored cycle start → cycle end (labels)
                now_utc = dt.datetime.now(dt.UTC)
                start_d = _anchored_period_start(settings.subscription_start_date, now_utc)
                end_d = _anchored_period_end(settings.subscription_start_date, start_d) if start_d else None
                start_label = start_d.strftime('%Y-%m-%d') if start_d else settings.subscription_start_date
                end_label = end_d.strftime('%Y-%m-%d') if end_d else dt.datetime.now(settings.timezone).date().strftime('%Y-%m-%d')
                label = f"{start_label} → {end_label}"
                subs_env = {
                    "traffic_limit": settings.subscription_limit_gb,
                    "current_period_start": start_label,
                    "current_period_end": end_label,
                }
            elif settings.subscription_end_date:
                ps = "?"
                pe = settings.subscription_end_date.strip()
                def d_only(s: str) -> str:
                    s = s.replace('T', ' ').replace('Z', '').split('.')[0]
                    return s[:10] if len(s) >= 10 and s[4] == '-' and s[7] == '-' else s
                label = f"{d_only(ps)} → {d_only(pe)}"
                subs_env = {
                    "traffic_limit": settings.subscription_limit_gb,
                    "current_period_start": None,
                    "current_period_end": settings.subscription_end_date,
                }
            else:
                # Default label: current month in user's timezone (date-only)
                now_local = dt.datetime.now(settings.timezone).replace(microsecond=0)
                start_dt_local = now_local.replace(day=1, hour=0, minute=0, second=0)
                label = f"{start_dt_local.strftime('%Y-%m-%d')} → {now_local.strftime('%Y-%m-%d')}"
                subs_env = {
                    "traffic_limit": settings.subscription_limit_gb,
                    "current_period_start": start_dt_local.strftime('%Y-%m-%d'),
                    "current_period_end": now_local.strftime('%Y-%m-%d'),
                }

            text = format_usage(
                subs_env,
                traffic,
                timeframe_label=label,
                proxy_type=_map_service_to_proxy_type(settings.decodo_service_type),
                tz=settings.timezone,
            )
            await message.answer(text, reply_markup=MAIN_KB)
        except httpx.HTTPStatusError as e:
            logger.exception("Decodo API error: %s", e)
            await message.answer(f"Error fetching usage: HTTP {e.response.status_code}")
        except Exception as e:
            logger.exception("Failed to fetch usage")
            await message.answer(f"Error fetching usage: {e}")


def _parse_date_guess(s: str) -> Optional[dt.date]:
    s = s.strip()
    if not s:
        return None
    try:
        # Try full timestamp first
        s2 = s.replace("T", " ").replace("Z", "").split(".")[0]
        return dt.datetime.strptime(s2, "%Y-%m-%d %H:%M:%S").date()
    except Exception:
        pass
    try:
        return dt.datetime.strptime(s[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _anchored_period_start(anchor_date_str: str, now_utc: dt.datetime) -> Optional[dt.date]:
    """Compute current cycle start date from an anchor start date.

    The anchor day-of-month defines the billing boundary. For months shorter than
    the anchor day (e.g., 31st), clamp to the last day of that month.
    """
    anchor = _parse_date_guess(anchor_date_str)
    if not anchor:
        return None
    anchor_day = anchor.day
    today = now_utc.date()

    # Candidate in current month
    ld = _last_day_of_month(today.year, today.month)
    day = min(anchor_day, ld)
    candidate = dt.date(today.year, today.month, day)
    if today >= candidate:
        return candidate
    # Otherwise previous month
    if today.month == 1:
        py, pm = today.year - 1, 12
    else:
        py, pm = today.year, today.month - 1
    ld_prev = _last_day_of_month(py, pm)
    pday = min(anchor_day, ld_prev)
    return dt.date(py, pm, pday)


def _anchored_period_end(anchor_date_str: str, start: dt.date) -> Optional[dt.date]:
    """Compute the cycle end date (next boundary) based on anchor day-of-month.

    End is the next month boundary using the anchor day-of-month, clamped to month length.
    """
    anchor = _parse_date_guess(anchor_date_str)
    if not anchor:
        return None
    anchor_day = anchor.day
    # next month
    if start.month == 12:
        year, month = start.year + 1, 1
    else:
        year, month = start.year, start.month + 1
    ld = _last_day_of_month(year, month)
    return dt.date(year, month, min(anchor_day, ld))


def _build_date_span(start: dt.date, end: dt.date) -> list[dt.date]:
    if end < start:
        start, end = end, start
    days = (end - start).days
    return [start + dt.timedelta(days=i) for i in range(days + 1)]


def _daily_bytes_from_traffic(traffic: Dict[str, Any]) -> Dict[str, int]:
    """Extract mapping YYYY-MM-DD -> bytes for each day from API response.

    Handles multiple container and field name variants seen across products.
    """
    result: Dict[str, int] = {}
    if not isinstance(traffic, dict):
        return result

    # Try common containers
    containers: list[Any] = []
    for key in ("data", "result", "records", "rows", "items"):
        val = traffic.get(key)
        if isinstance(val, list):
            containers.append(val)
        elif isinstance(val, dict) and isinstance(val.get("items"), list):
            containers.append(val.get("items"))
    if not containers:
        return result

    def extract_bytes(it: Dict[str, Any]) -> int:
        def parse_unit_value(v: Any) -> Optional[int]:
            # Parse values that may include units like '123.4GB' or '567MB'
            if isinstance(v, (int, float)):
                return int(v)
            if isinstance(v, str):
                s = v.strip().lower()
                try:
                    if s.endswith("gb"):
                        return int(float(s[:-2].strip()) * 1_000_000_000)
                    if s.endswith("mb"):
                        return int(float(s[:-2].strip()) * 1_000_000)
                    if s.endswith("kb"):
                        return int(float(s[:-2].strip()) * 1_000)
                    if s.endswith("b"):
                        return int(float(s[:-1].strip()))
                    # plain number string -> bytes
                    return int(float(s))
                except Exception:
                    return None
            return None

        # Direct fields (snake/camel)
        for k in (
            "rx_tx_bytes",
            "rxTxBytes",
            "rx_tx",
            "rxTx",
            "traffic_bytes",
            "trafficBytes",
            "bytes",
        ):
            v = it.get(k)
            if isinstance(v, (int, float)):
                return int(v)
            pv = parse_unit_value(v)
            if pv is not None:
                return pv
        # Sum rx/tx (snake/camel)
        rx = it.get("rx_bytes") or it.get("rxBytes")
        tx = it.get("tx_bytes") or it.get("txBytes")
        try:
            if isinstance(rx, (int, float)) or isinstance(tx, (int, float)):
                return int(rx or 0) + int(tx or 0)
        except Exception:
            pass
        # Traffic/usage fields with explicit units
        for k in ("traffic_gb", "trafficGB", "usage_gb", "usageGB"):
            v = it.get(k)
            if isinstance(v, (int, float)):
                return int(float(v) * 1_000_000_000)
        for k in ("traffic_mb", "trafficMB", "usage_mb", "usageMB"):
            v = it.get(k)
            if isinstance(v, (int, float)):
                return int(float(v) * 1_000_000)
        # Generic 'traffic' or 'usage'
        for k in ("traffic", "usage"):
            pv = parse_unit_value(it.get(k))
            if pv is not None:
                return pv
        # Totals container
        totals = it.get("totals")
        if isinstance(totals, dict):
            for k in ("rx_tx_bytes", "rxTxBytes", "rx_tx", "rxTx", "bytes", "total_rx_tx"):
                v = totals.get(k)
                if isinstance(v, (int, float)):
                    return int(v)
            rx2 = totals.get("rx_bytes") or totals.get("rxBytes")
            tx2 = totals.get("tx_bytes") or totals.get("txBytes")
            try:
                if isinstance(rx2, (int, float)) or isinstance(tx2, (int, float)):
                    return int(rx2 or 0) + int(tx2 or 0)
            except Exception:
                pass
        # Check common nested maps
        for nest_key in ("value", "metrics", "stat", "stats"):
            m = it.get(nest_key)
            if isinstance(m, dict):
                # Recurse lightly: try the same keys in the nested map
                vv = extract_bytes(m)
                if vv:
                    return vv
        return 0

    def extract_date_str(it: Dict[str, Any]) -> Optional[str]:
        # direct keys
        for k in (
            "date",
            "day",
            "timestamp",
            "time",
            "bucket",
            "startDate",
            "grouping_key",
            "groupingValue",
            "group",
            "key",
        ):
            v = it.get(k)
            if v:
                return str(v)
        # nested under grouping_key object
        for k in ("grouping", "groupKey", "grouping_key"):
            v = it.get(k)
            if isinstance(v, dict):
                for dk in ("date", "day", "timestamp", "time", "bucket", "startDate", "value"):
                    dv = v.get(dk)
                    if dv:
                        return str(dv)
        return None

    for data in containers:
        for item in data:
            if not isinstance(item, dict):
                continue
            date_str = extract_date_str(item)
            if not date_str:
                continue
            d = _parse_date_guess(date_str)
            if not d:
                continue
            b = extract_bytes(item)
            key = d.strftime("%Y-%m-%d")
            result[key] = result.get(key, 0) + b
    return result


def _render_daily_chart(dates: list[dt.date], values_gb: list[float], *, title: str) -> bytes:
    # Lazy import matplotlib to avoid overhead when not used
    import matplotlib
    # Ensure a non-interactive backend
    try:
        matplotlib.use("Agg", force=False)
    except Exception:
        pass
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(max(6, min(14, len(dates) * 0.4)), 4))
    x = [d.strftime("%Y-%m-%d") for d in dates]
    ax.bar(x, values_gb, color="#3b82f6", edgecolor="#1e40af")
    ax.set_title(title)
    ax.set_ylabel("GB per day")
    ax.set_xlabel("Date")
    ax.grid(axis="y", linestyle=":", alpha=0.6)
    # Show at most ~12 x-ticks to keep readable
    step = max(1, len(x) // 12)
    ax.set_xticks(range(0, len(x), step))
    ax.set_xticklabels([x[i] for i in range(0, len(x), step)], rotation=45, ha="right", fontsize=9)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=160)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


async def _handle_chart(message: Message, bot: Bot) -> None:
    chat_id = message.chat.id
    settings = Settings()
    if not _is_allowed(chat_id, settings.telegram_allowed_chat_ids):
        await message.answer("Not authorized.")
        return

    async with ChatActionSender.typing(chat_id=chat_id, bot=bot):
        try:
            client = DecodoClient(settings.decodo_api_key)
            try:
                # Determine date window
                env_from = settings.subscription_start_date
                env_to = settings.subscription_end_date

                def to_ts(date_str: str, end_of_day: bool = False) -> str:
                    date_str = date_str.strip()
                    if len(date_str) == 10 and date_str[4] == '-' and date_str[7] == '-':
                        return f"{date_str} {'23:59:59' if end_of_day else '00:00:00'}"
                    return date_str

                if env_from:
                    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
                    start_d = _anchored_period_start(env_from, now)
                    end_d = _anchored_period_end(env_from, start_d) if start_d else None
                    from_date = f"{start_d.strftime('%Y-%m-%d')} 00:00:00" if start_d else to_ts(env_from)
                    if end_d:
                        end_dt = dt.datetime(end_d.year, end_d.month, end_d.day, 23, 59, 59, tzinfo=dt.UTC)
                        to_dt = min(now, end_dt)
                    else:
                        to_dt = now
                    to_date = to_dt.strftime("%Y-%m-%d %H:%M:%S")
                elif env_to:
                    from_date = None
                    to_date = to_ts(env_to, end_of_day=True)
                else:
                    # current month window
                    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
                    start_dt = now.replace(day=1, hour=0, minute=0, second=0)
                    from_date = start_dt.strftime("%Y-%m-%d %H:%M:%S")
                    to_date = now.strftime("%Y-%m-%d %H:%M:%S")

                # Fetch traffic with groupBy day and proxyType fallbacks
                last_err: Optional[Exception] = None
                traffic: Dict[str, Any]
                for pt in _build_proxy_type_candidates(settings.decodo_service_type):
                    try:
                        traffic = await client.get_traffic(
                            from_date=from_date,
                            to_date=to_date,
                            type_=pt,
                            group_by="day",
                        )
                        break
                    except httpx.HTTPStatusError as e:
                        if e.response is not None and e.response.status_code == 400:
                            logger.warning("Decodo traffic 400 with proxyType=%s; trying next candidate. body=%s", pt, e.response.text)
                            last_err = e
                            continue
                        raise
                    except Exception as e:
                        last_err = e
                        logger.warning("Decodo traffic error with proxyType=%s: %s", pt, e)
                        continue
                else:
                    assert last_err is not None
                    raise last_err
            finally:
                await client.aclose()

            # Build daily series covering the full window
            if settings.subscription_start_date:
                now_utc = dt.datetime.now(dt.UTC)
                today_local = dt.datetime.now(settings.timezone).date()
                sd = _anchored_period_start(settings.subscription_start_date, now_utc) or _parse_date_guess(settings.subscription_start_date) or today_local.replace(day=1)
                # For display, use the cycle end date; data beyond 'now' will be zero
                ed_cycle = _anchored_period_end(settings.subscription_start_date, sd) if sd else None
                ed = ed_cycle or today_local
            elif settings.subscription_end_date:
                ed = _parse_date_guess(settings.subscription_end_date) or dt.datetime.now(settings.timezone).date()
                # If only END provided, chart span covers that calendar month up to END
                sd = ed.replace(day=1)
            else:
                # derive from current month
                now_local = dt.datetime.now(settings.timezone).date()
                sd = now_local.replace(day=1)
                ed = now_local
            days = _build_date_span(sd, ed)
            per_day_bytes = _daily_bytes_from_traffic(traffic)
            # Fallback: if empty, try fetching hourly and aggregate by day
            if not per_day_bytes:
                try:
                    last_err2: Optional[Exception] = None
                    for pt in _build_proxy_type_candidates(settings.decodo_service_type):
                        try:
                            traffic_hour = await client.get_traffic(
                                from_date=from_date,
                                to_date=to_date,
                                type_=pt,
                                group_by="hour",
                            )
                            break
                        except httpx.HTTPStatusError as e:
                            if e.response is not None and e.response.status_code == 400:
                                last_err2 = e
                                continue
                            raise
                        except Exception as e:
                            last_err2 = e
                            continue
                    else:
                        traffic_hour = None  # type: ignore[assignment]
                    if traffic_hour:
                        # Reuse parser; hours will map to their date part
                        per_day_bytes = _daily_bytes_from_traffic(traffic_hour)
                except Exception:
                    pass
            y_gb = [max(0.0, float(per_day_bytes.get(d.strftime('%Y-%m-%d'), 0)) / 1_000_000_000.0) for d in days]

            # Title and label
            label = f"{days[0].strftime('%Y-%m-%d')} → {days[-1].strftime('%Y-%m-%d')}"
            proxy_label = _map_service_to_proxy_type(settings.decodo_service_type) or ""
            title = f"Daily usage (GB) — {proxy_label} — {label}" if proxy_label else f"Daily usage (GB) — {label}"

            # If no data at all, add an annotation on the chart
            if sum(y_gb) == 0.0:
                # Simple single-bar with annotation to avoid empty look
                ann_days = days if len(days) <= 7 else days[:7]
                ann_vals = [0.0 for _ in ann_days]
                img_bytes = _render_daily_chart(ann_days, ann_vals, title=title + " (no data)")
            else:
                img_bytes = _render_daily_chart(days, y_gb, title=title)
            await message.answer_photo(photo=BufferedInputFile(img_bytes, filename="daily_usage.png"), reply_markup=MAIN_KB)
        except httpx.HTTPStatusError as e:
            logger.exception("Decodo API error: %s", e)
            await message.answer(f"Error fetching chart: HTTP {e.response.status_code}")
        except Exception as e:
            logger.exception("Failed to generate chart")
            await message.answer(f"Error generating chart: {e}")


@router.message(Command("usage"))
async def cmd_usage(message: Message, bot: Bot) -> None:
    await _handle_usage(message, bot)


@router.message(Command("chart"))
async def cmd_chart(message: Message, bot: Bot) -> None:
    await _handle_chart(message, bot)


@router.message()
async def on_text_buttons(message: Message, bot: Bot) -> None:
    # Handle simple text buttons from reply keyboard
    if not message.text:
        return
    if message.text.strip().lower() == "usage":
        await _handle_usage(message, bot)
    elif message.text.strip().lower() in ("daily chart", "chart", "stats image", "statistic image", "daily usage"):
        await _handle_chart(message, bot)


async def main() -> None:
    settings = Settings()
    settings.ensure_valid()

    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()
    dp.include_router(router)

    logger.info("Bot started (aiogram)")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
