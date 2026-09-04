import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional
    pass

TENANT_ID = os.getenv("POWERBI_TENANT_ID") or os.getenv("TENANT_ID") or ""
CLIENT_ID = os.getenv("POWERBI_CLIENT_ID") or os.getenv("CLIENT_ID") or ""
CLIENT_SECRET = os.getenv("POWERBI_CLIENT_SECRET") or os.getenv("CLIENT_SECRET") or ""

SCOPE = "https://analysis.windows.net/powerbi/api/.default"
API_URL = "https://api.powerbi.com/v1.0/myorg/admin/activityevents"
MAX_WINDOW_DAYS = 28
RETENTION_DAYS = 28  # activity log history limit; see Get Activity Events docs


def parse_date(value: str, field_name: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be in YYYY-MM-DD format.") from exc


def format_api_datetime(value: datetime):
    utc_value = value.astimezone(timezone.utc)
    return f"'{utc_value.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]}Z'"


def end_of_utc_day(value: datetime):
    """Last instant of the UTC day containing `value`, at millisecond resolution."""
    return value.replace(hour=23, minute=59, second=59, microsecond=999000)


def resolve_date_window(day_value: str | None, start_date_value: str | None, end_date_value: str | None):
    if day_value:
        start = parse_date(day_value, "day")
        end = end_of_utc_day(start)
    elif start_date_value or end_date_value:
        start_value = start_date_value or end_date_value
        end_value = end_date_value or start_date_value
        start = parse_date(start_value, "start date")
        end = end_of_utc_day(parse_date(end_value, "end date"))
        if end < start:
            raise ValueError("The end date must be on or after the start date.")
    else:
        # Default to the whole of yesterday (UTC). Today is skipped because the
        # activity log lags real time by up to a few hours.
        start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ) - timedelta(days=1)
        end = end_of_utc_day(start)

    delta_days = (end - start).total_seconds() / 86400
    if delta_days > MAX_WINDOW_DAYS:
        raise ValueError(f"Date window must be {MAX_WINDOW_DAYS} days or less.")

    oldest_allowed = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    if start < oldest_allowed.replace(hour=0, minute=0, second=0, microsecond=0):
        raise ValueError(
            f"The activity log only retains {RETENTION_DAYS} days of history. "
            f"Start date {start.date()} is outside that window."
        )

    return start, end


def iter_utc_days(start: datetime, end: datetime):
    """Split an arbitrary window into per-UTC-day windows.

    The activity events API rejects any request whose start and end fall on
    different UTC days, so every call has to be scoped to a single day.
    """
    day_start = start
    while day_start <= end:
        day_end = min(end_of_utc_day(day_start), end)
        yield day_start, day_end
        day_start = (day_start + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )


def require_env(name, value):
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            "Set POWERBI_TENANT_ID, POWERBI_CLIENT_ID, and POWERBI_CLIENT_SECRET before running this script."
        )
    return value


def get_token():
    resp = requests.post(
        f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope": SCOPE,
        },
        timeout=60,
    )
    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:
        print(f"Authentication failed: {resp.status_code} {resp.text}", file=sys.stderr)
        raise exc
    return resp.json()["access_token"]


def request_with_retry(method, url, *, retries=4, **kwargs):
    last_exception = None
    for attempt in range(retries + 1):
        try:
            response = requests.request(method, url, **kwargs)
            status_code = response.status_code

            if status_code in {429, 500, 502, 503, 504}:
                last_exception = requests.HTTPError(f"Transient HTTP status {status_code}")
                if attempt >= retries:
                    print(
                        f"Power BI API is still transiently failing (HTTP {status_code}). "
                        f"Response body: {response.text[:500]}",
                        file=sys.stderr,
                    )
                    response.raise_for_status()
                print(
                    f"Transient request failure ({method} {url}, HTTP {status_code}). Retrying in {2 ** attempt} seconds...",
                    file=sys.stderr,
                )
                time.sleep(2 ** attempt)
                continue

            response.raise_for_status()
            return response
        except requests.HTTPError as exc:
            last_exception = exc
            response = getattr(exc, "response", None)
            if response is not None and response.status_code in {400, 401, 403, 404}:
                print(
                    f"Non-transient Power BI error: HTTP {response.status_code}. Response: {response.text[:1000]}",
                    file=sys.stderr,
                )
                raise
            if attempt >= retries:
                raise
            delay = 2 ** attempt
            print(
                f"Transient request failure ({method} {url}). Retrying in {delay} seconds...",
                file=sys.stderr,
            )
            time.sleep(delay)
        except requests.RequestException as exc:
            last_exception = exc
            if attempt >= retries:
                raise
            delay = 2 ** attempt
            print(
                f"Transient request failure ({method} {url}). Retrying in {delay} seconds...",
                file=sys.stderr,
            )
            time.sleep(delay)
    raise last_exception


def fetch_activity_events_for_day(token: str, day_start: datetime, day_end: datetime, headers, page_budget: int):
    """Fetch every page for a single UTC day. Returns (events, pages_used)."""
    events = []
    pages_used = 0
    continuation = None

    while pages_used < page_budget:
        if continuation:
            # Continuation requests must carry the token on its own. Repeating
            # startDateTime/endDateTime alongside it is rejected with HTTP 400.
            params = {"continuationToken": f"'{continuation}'"}
        else:
            params = {
                "startDateTime": format_api_datetime(day_start),
                "endDateTime": format_api_datetime(day_end),
            }

        resp = request_with_retry(
            "GET",
            API_URL,
            headers=headers,
            params=params,
            timeout=120,
        )
        page = resp.json()
        events.extend(page.get("activityEventEntities") or [])
        pages_used += 1

        continuation = page.get("continuationToken")
        if not continuation:
            break

    return events, pages_used


def fetch_all_activity_events(token: str, start: datetime, end: datetime, max_pages: int = 50):
    all_events = []
    page_count = 0
    headers = {"Authorization": f"Bearer {token}"}

    for day_start, day_end in iter_utc_days(start, end):
        remaining = max_pages - page_count
        if remaining <= 0:
            print(
                f"Page limit of {max_pages} reached; stopping before {day_start.date()}.",
                file=sys.stderr,
            )
            break

        print(f"fetching {day_start.date()}...", file=sys.stderr)
        day_events, pages_used = fetch_activity_events_for_day(
            token, day_start, day_end, headers, remaining
        )
        all_events.extend(day_events)
        page_count += pages_used

    return all_events, page_count


def write_output(events, output_format: str, output_file: str | None):
    if output_format == "none":
        return

    if not output_file:
        output_file = f"activity_events.{output_format}"

    if output_format == "json":
        with open(output_file, "w", encoding="utf-8") as file:
            json.dump(events, file, indent=2, ensure_ascii=False)
        return

    if output_format == "csv":
        fieldnames = sorted({key for event in events for key in (event or {}).keys()})
        with open(output_file, "w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=fieldnames)
            writer.writeheader()
            for event in events:
                row = {}
                for key in fieldnames:
                    value = event.get(key)
                    if isinstance(value, (dict, list)):
                        row[key] = json.dumps(value, ensure_ascii=False)
                    else:
                        row[key] = value
                writer.writerow(row)
        return

    raise ValueError(f"Unsupported output format: {output_format}")


def parse_args():
    parser = argparse.ArgumentParser(description="Fetch Power BI admin activity events.")
    parser.add_argument("--day", default=os.getenv("POWERBI_DAY"), help="Single day to query in YYYY-MM-DD format.")
    parser.add_argument("--start-date", default=os.getenv("POWERBI_START_DATE"), help="Start date in YYYY-MM-DD format.")
    parser.add_argument("--end-date", default=os.getenv("POWERBI_END_DATE"), help="End date in YYYY-MM-DD format.")
    parser.add_argument("--output-format", choices=["json", "csv", "none"], default=os.getenv("POWERBI_OUTPUT_FORMAT", "none"), help="Output file format.")
    parser.add_argument("--output-file", default=os.getenv("POWERBI_OUTPUT_FILE"), help="Destination file for JSON or CSV output.")
    parser.add_argument("--max-pages", type=int, default=50, help="Maximum number of pages to fetch.")
    return parser.parse_args()


def main():
    args = parse_args()
    require_env("POWERBI_TENANT_ID", TENANT_ID)
    require_env("POWERBI_CLIENT_ID", CLIENT_ID)
    require_env("POWERBI_CLIENT_SECRET", CLIENT_SECRET)
    start, end = resolve_date_window(args.day, args.start_date, args.end_date)
    token = get_token()
    print(f"token acquired, length: {len(token)}")
    print(f"date window: {start.isoformat()} to {end.isoformat()}")

    events, pages = fetch_all_activity_events(token, start, end, max_pages=args.max_pages)
    print(f"pages fetched: {pages}")
    print(f"events returned: {len(events)}")

    if events:
        print(json.dumps(events[0], indent=2, ensure_ascii=False))

    write_output(events, args.output_format, args.output_file)
    if args.output_file:
        print(f"output written to: {args.output_file}")


if __name__ == "__main__":
    main()
