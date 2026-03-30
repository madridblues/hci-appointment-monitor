"""Scrapes the HCI London appointment page for available dates and time slots."""

import logging
import re
import warnings
from dataclasses import dataclass, field
from urllib.parse import urlencode, parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

# Suppress SSL warnings when using proxy
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

logger = logging.getLogger(__name__)

BASE_URL = "https://appointment.hcilondon.gov.in/appointment.php"

# Cache the proxy IP
_proxy_ip_cache: str = ""


def get_proxy_ip(proxy_url: str = "") -> str:
    """Detect the outgoing IP address (through proxy if configured)."""
    global _proxy_ip_cache
    if _proxy_ip_cache:
        return _proxy_ip_cache
    try:
        proxies = None
        if proxy_url:
            proxies = {"http": proxy_url, "https": proxy_url}
        resp = requests.get("https://api.ipify.org?format=json", timeout=15,
                            proxies=proxies, verify=not bool(proxy_url))
        _proxy_ip_cache = resp.json().get("ip", "unknown")
    except Exception:
        _proxy_ip_cache = "unknown"
    return _proxy_ip_cache


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


def _fetch_page(url: str, proxy_url: str = "") -> str:
    """Fetch a page and return its HTML."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    proxies = None
    verify_ssl = True
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}
        verify_ssl = False  # proxy may intercept SSL

    for attempt in range(3):
        try:
            response = requests.get(url, headers=headers, timeout=120, proxies=proxies, verify=verify_ssl)
            response.raise_for_status()
            break
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            if attempt == 2:
                raise
            logger.warning("Attempt %d timed out, retrying...", attempt + 1)
            continue
    return response.text


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
) -> list[AvailableSlot]:
    """
    Fetch the appointment page, find bookable dates, then fetch each date's
    page to get available time slots. Only returns dates that have at least
    one bookable time slot.
    """
    url = build_url(month, year, apt_type, location_id, service_id)
    logger.info("Checking appointments at: %s", url)

    if proxy_url:
        logger.info("Using proxy: %s", proxy_url.split("@")[-1] if "@" in proxy_url else proxy_url)

    html = _fetch_page(url, proxy_url)
    bookable_dates = _parse_bookable_dates(html, month)

    if not bookable_dates:
        logger.info("No bookable dates for %s/%s", month, year)
        return []

    logger.info("Found %d bookable date(s) for %s/%s, checking time slots...",
                len(bookable_dates), month, year)

    available: list[AvailableSlot] = []
    for date_str in bookable_dates:
        date_url = build_url(month, year, apt_type, location_id, service_id, date=date_str)
        try:
            date_html = _fetch_page(date_url, proxy_url)
            time_slots = _parse_time_slots(date_html)

            if time_slots:
                slot_summary = ", ".join(f"{ts.time} ({ts.available})" for ts in time_slots)
                logger.info("  Date %s: %s", date_str, slot_summary)
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
