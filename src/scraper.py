"""Scrapes the HCI London appointment page for available dates and time slots."""

import logging
import re
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

# Suppress SSL warnings when using proxy
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

logger = logging.getLogger(__name__)

BASE_URL = "https://appointment.hcilondon.gov.in/appointment.php"

_direct_ip: str = ""


def get_random_user_agent(token: str = "") -> str:
    """Get a random user agent from Crawlbase API, with fallback."""
    default = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
    if not token:
        return default
    try:
        resp = requests.get(
            f"https://api.crawlbase.com/user_agents?token={token}",
            timeout=5,
        )
        data = resp.json()
        if data.get("success") and data.get("agents"):
            return data["agents"][0]
    except Exception:
        pass
    return default


def get_direct_ip() -> str:
    """Detect the direct outgoing IP address."""
    global _direct_ip
    if _direct_ip:
        return _direct_ip
    try:
        resp = requests.get("https://api.ipify.org?format=json", timeout=10)
        _direct_ip = resp.json().get("ip", "unknown")
    except Exception:
        _direct_ip = "unknown"
    return _direct_ip


def get_proxy_ip(proxy_url: str) -> str:
    """Detect outgoing IP through the proxy."""
    try:
        proxies = {"http": proxy_url, "https": proxy_url}
        resp = requests.get("https://api.ipify.org?format=json", timeout=15,
                            proxies=proxies, verify=False)
        return resp.json().get("ip", "unknown")
    except Exception:
        return "unknown"


@dataclass
class TimeSlot:
    time: str         # e.g. "08:30 - 09:00"
    available: int    # number of slots available


@dataclass
class AvailableSlot:
    date: str
    month: str
    year: str
    apt_type: str
    location_id: str
    service_id: str
    url: str
    time_slots: list[TimeSlot] = field(default_factory=list)
    fetched_via: str = ""   # "proxy" or "direct"
    fetched_ip: str = ""    # IP address used
    page_snapshot: str = "" # raw HTML snapshot of the date page


def build_url(month: str, year: str, apt_type: str, location_id: str, service_id: str, date: str = "") -> str:
    params = {
        "month": month,
        "year": year,
        "apttype": apt_type,
        "locationid": location_id,
        "serviceid": service_id,
    }
    if date:
        params["date"] = date
    return f"{BASE_URL}?{urlencode(params)}"


class FetchResult:
    """Result of a page fetch with metadata."""
    def __init__(self, html: str, via: str, ip: str = ""):
        self.html = html
        self.via = via    # "proxy" or "direct"
        self.ip = ip      # IP used for the request


def _make_session(proxy_url: str = "", crawlbase_token: str = "") -> requests.Session:
    """Create a fresh browser-like session."""
    session = requests.Session()

    if proxy_url:
        # Blank User-Agent → Crawlbase auto-rotates it
        session.headers.update({
            "User-Agent": "",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.5",
        })
        session.proxies = {"http": proxy_url, "https": proxy_url}
    else:
        ua = get_random_user_agent(crawlbase_token)
        session.headers.update({
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        })
    return session


def _fetch_page(url: str, proxy_url: str = "", crawlbase_token: str = "") -> FetchResult:
    """Fetch a page via proxy (required) with retries. 180s timeout for slow sites."""

    for attempt in range(3):
        try:
            session = _make_session(proxy_url, crawlbase_token)
            verify_ssl = not bool(proxy_url)
            response = session.get(url, timeout=180, verify=verify_ssl)
            response.raise_for_status()
            ip = "rotating"
            if proxy_url:
                # Don't detect IP each time (slow) - just note it's proxied
                ip = "proxy-rotated"
            else:
                ip = get_direct_ip()
            session.close()
            via = "proxy" if proxy_url else "direct"
            logger.info("Fetched via %s (attempt %d)", via, attempt + 1)
            return FetchResult(html=response.text, via=via, ip=ip)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            if attempt == 2:
                raise
            logger.warning("Attempt %d failed (%s), retrying...", attempt + 1, e)
            continue
    raise requests.exceptions.ConnectionError("All fetch attempts failed")


def _parse_time_slots(html: str) -> list[TimeSlot]:
    """
    Parse available time slots from a date-specific page.

    Available slots: <a> tags with onclick setting apttime, containing text like
        "08:30 - 09:00\n(only 5 slot(s) available)"
    Booked slots: <td> with text-decoration:line-through (ignored)
    """
    soup = BeautifulSoup(html, "html.parser")
    time_table = soup.find("table", id="time_cal")
    if not time_table:
        return []

    slots: list[TimeSlot] = []
    for link in time_table.find_all("a", href="#t"):
        onclick = link.get("onclick", "")
        if "apttime" not in onclick:
            continue

        text = link.get_text(separator="\n", strip=True)
        lines = text.split("\n")
        if not lines:
            continue

        time_str = lines[0].strip()  # e.g. "08:30 - 09:00"

        # Extract available count from "(only X slot(s) available)"
        available_count = 0
        for line in lines:
            match = re.search(r'(\d+)\s+slot', line)
            if match:
                available_count = int(match.group(1))
                break

        if available_count > 0:
            slots.append(TimeSlot(time=time_str, available=available_count))

    return slots


def check_appointments(
    month: str,
    year: str,
    apt_type: str = "Submission",
    location_id: str = "8",
    service_id: str = "29",
    proxy_url: str = "",
    crawlbase_token: str = "",
) -> list[AvailableSlot]:
    """
    Fetch the appointment page, find bookable dates, then fetch each date's
    page to get available time slots. Only returns dates that have at least
    one bookable time slot. Tries proxy first, falls back to direct.
    """
    url = build_url(month, year, apt_type, location_id, service_id)
    logger.info("Checking appointments at: %s", url)

    result = _fetch_page(url, proxy_url, crawlbase_token)
    bookable_dates = _parse_bookable_dates(result.html, month)

    if not bookable_dates:
        logger.info("No bookable dates for %s/%s (via %s, IP: %s)",
                     month, year, result.via, result.ip)
        return []

    logger.info("Found %d bookable date(s) for %s/%s (via %s, IP: %s), checking time slots...",
                len(bookable_dates), month, year, result.via, result.ip)

    available: list[AvailableSlot] = []
    for date_str in bookable_dates:
        date_url = build_url(month, year, apt_type, location_id, service_id, date=date_str)
        try:
            date_result = _fetch_page(date_url, proxy_url, crawlbase_token)
            time_slots = _parse_time_slots(date_result.html)

            if time_slots:
                slot_summary = ", ".join(f"{ts.time} ({ts.available})" for ts in time_slots)
                logger.info("  Date %s: %s (via %s, IP: %s)",
                            date_str, slot_summary, date_result.via, date_result.ip)
                available.append(
                    AvailableSlot(
                        date=date_str,
                        month=month,
                        year=year,
                        apt_type=apt_type,
                        location_id=location_id,
                        service_id=service_id,
                        url=date_url,
                        time_slots=time_slots,
                        fetched_via=date_result.via,
                        fetched_ip=date_result.ip,
                        page_snapshot=date_result.html,
                    )
                )
            else:
                logger.info("  Date %s: no available time slots (fully booked)", date_str)
        except Exception:
            logger.exception("  Error checking time slots for date %s", date_str)

    if available:
        logger.info("Found %d date(s) with available time slots for %s/%s",
                     len(available), month, year)
    else:
        logger.info("No available time slots for %s/%s", month, year)

    return available


def _parse_bookable_dates(html: str, month: str) -> list[str]:
    """
    Parse the calendar to find bookable dates (green + a_full class).

    Available (green):  <a style="color:#28B913; font-weight:bold;" href="appointment.php?date=X&...">
                          <li class="a_full">X</li></a>
    Unavailable (red):  <a href="#d" style="color:red;"><li>X</li></a>
    Not yet opened:     <li class="a_disable">
    Fully booked:       <li> with text-decoration: line-through
    """
    soup = BeautifulSoup(html, "html.parser")
    dates: list[str] = []
    seen: set[str] = set()

    calendar = soup.find("div", id="calendar")
    if not calendar:
        calendar = soup

    for link in calendar.find_all("a"):
        href = link.get("href", "")
        style = link.get("style", "")

        # Skip non-booking links (red/unavailable link to "#d")
        if href == "#d" or not href or "date=" not in href:
            continue

        # Must be green
        if "#28b913" not in style.lower() and "green" not in style.lower():
            continue

        li = link.find("li")
        if not li:
            continue

        date_text = li.get_text(strip=True)
        if not date_text or not date_text.isdigit():
            continue

        li_classes = " ".join(li.get("class", []))

        # Skip disabled dates (grey - not yet opened)
        if "a_disable" in li_classes:
            continue

        # Skip fully booked (strikethrough)
        li_style = li.get("style", "")
        if "line-through" in li_style:
            continue

        # Skip padding cells (date=0 or date=32)
        qs = parse_qs(urlparse(href).query)
        href_date = qs.get("date", [None])[0]
        if href_date in ("0", "32"):
            continue

        if date_text not in seen:
            seen.add(date_text)
            dates.append(date_text)

    return dates


SNAPSHOTS_DIR = Path(__file__).resolve().parent.parent / "data" / "snapshots"


def save_snapshot(slot: 'AvailableSlot') -> str:
    """Save the HTML snapshot of a found slot. Returns the snapshot filename."""
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"loc{slot.location_id}_{slot.date}_{slot.month}_{slot.year}_{ts}"
    filepath = SNAPSHOTS_DIR / f"{name}.html"
    if slot.page_snapshot:
        filepath.write_text(slot.page_snapshot, encoding="utf-8")
        logger.info("Saved snapshot: %s", name)
    return name
