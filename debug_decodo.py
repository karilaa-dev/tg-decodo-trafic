from __future__ import annotations

import asyncio
import datetime as dt
import os

from dotenv import load_dotenv

from bot import DecodoClient, _map_service_to_proxy_type, format_usage, _anchored_period_start


async def main() -> None:
    load_dotenv('.env')
    key = os.getenv('DECODO_API_KEY', '').strip()
    if not key:
        print('DECODO_API_KEY is missing in .env')
        return
    svc = os.getenv('DECODO_SERVICE_TYPE', 'mobile_proxies')
    t = _map_service_to_proxy_type(svc)

    # Optional env-provided subscription window and limit
    start_date = os.getenv('DECODO_SUBSCRIPTION_START_DATE')
    end_date = os.getenv('DECODO_SUBSCRIPTION_END_DATE')
    limit_str = os.getenv('DECODO_SUBSCRIPTION_LIMIT_GB')
    try:
        limit_gb = float(limit_str) if limit_str else None
    except ValueError:
        print("Invalid DECODO_SUBSCRIPTION_LIMIT_GB; ignoring")
        limit_gb = None

    client = DecodoClient(key)
    try:
        # Decide time window: env-provided custom range or current month
        def ts_or_date(d: str, end_of_day: bool = False) -> str:
            d = d.strip()
            if len(d) == 10 and d[4] == '-' and d[7] == '-':
                return f"{d} {'23:59:59' if end_of_day else '00:00:00'}"
            return d

        if start_date:
            # Rolling anchored window → now
            now = dt.datetime.now(dt.UTC).replace(microsecond=0)
            start_d = _anchored_period_start(start_date, now)
            from_date = f"{start_d.strftime('%Y-%m-%d')} 00:00:00" if start_d else ts_or_date(start_date)
            to_date = now.strftime('%Y-%m-%d %H:%M:%S')
            traffic = await client.get_traffic(
                from_date=from_date,
                to_date=to_date,
                type_=t,
                group_by='day',
            )
            subs = {
                'traffic_limit': limit_gb,
                'current_period_start': start_d.strftime('%Y-%m-%d') if start_d else start_date,
                'current_period_end': dt.datetime.now().strftime('%Y-%m-%d'),
            }
        elif end_date:
            # Fixed window ending at END (legacy)
            traffic = await client.get_traffic(
                from_date=None,
                to_date=ts_or_date(end_date, end_of_day=True),
                type_=t,
                group_by='day',
            )
            subs = {
                'traffic_limit': limit_gb,
                'current_period_start': None,
                'current_period_end': end_date,
            }
        else:
            traffic = await client.get_current_month_usage(type_=t)
            now = dt.datetime.now(dt.UTC)
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            subs = {
                'traffic_limit': limit_gb,
                'current_period_start': start.strftime('%Y-%m-%d'),
                'current_period_end': now.strftime('%Y-%m-%d'),
            }

        print('Summary:\n', format_usage(subs, traffic))
    finally:
        await client.aclose()


if __name__ == '__main__':
    asyncio.run(main())
