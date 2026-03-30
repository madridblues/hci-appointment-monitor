"""Scrapes the HCI London appointment page for available dates."""

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlencode, parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://appointment.hcilondon.gov.in/appointment.php"


@dataclass
class AvailableSlot:
    date: str
    month: str
    year: str
    apt_type: str
    location_id: str
    service_id: str
    url: str


def build_url(month: str, year: str, apt_type: str, location_id: str, service_id: str) -> str:
    params = {
        "month": month,
        "year": year,
        "apttype": apt_type,
        "locationid": location_id,
        "serviceid": service_id,
    }
    return f"{BASE_URL}?{urlencode(params)}"


def check_appointments(
    month: str,
    year: str,
    apt_type: str = "Submission",
    location_id: str = "8",
    service_id: str = "29",
    proxy_url: str = "",
) -> list[AvailableSlot]:
    """
    Fetch the appointment page and parse for available dates.

    The HCI London calendar uses <div id="calendar"> with <ul class="dates">
    containing <a> wrapping <li> elements:
    - Available (green): <a style="color:#28B913; font-weight:bold;" href="appointment.php?date=X&...">
                           <li class="a_full">X</li></a>
    - Unavailable (red): <a href="#d" style="color:red;">
                           <li>X</li></a>
    - Grey/disabled:     <li class="a_disable">
    - Fully booked:      <li> with text-decoration: line-through
    """
    url = build_url(month, year, apt_type, location_id, service_id)
    logger.info("Checking appointments at: %s", url)

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
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}
        logger.info("Using proxy: %s", proxy_url.split("@")[-1] if "@" in proxy_url else proxy_url)

    response = requests.get(url, headers=headers, timeout=30, proxies=proxies)
    response.raise_for_status()

    return _parse_available_slots(response.text, month, year, apt_type, location_id, service_id, url)


def _parse_available_slots(
    html: str,
    month: str,
    year: str,
    apt_type: str,
    location_id: str,
    service_id: str,
    url: str,
) -> list[AvailableSlot]:
    """
    Parse available appointment slots from the HTML.

    Available dates are identified by:
    1. <a> tags with style containing color:#28B913 (green) and a real href (not "#d")
    2. The <a> href contains "date=X" with a valid day number
    3. The inner <li> has class "a_full" (not "a_disable") and contains a digit
    """
    soup = BeautifulSoup(html, "html.parser")
    available: list[AvailableSlot] = []
    seen_dates: set[str] = set()

    # Find the calendar div
    calendar = soup.find("div", id="calendar")
    if not calendar:
        calendar = soup  # fallback to searching whole page

    # Find all <a> tags in the calendar dates list
    for link in calendar.find_all("a"):
        href = link.get("href", "")
        style = link.get("style", "")

        # Skip non-booking links (red/unavailable dates link to "#d")
        if href == "#d" or not href or "date=" not in href:
            continue

        # Check for green color in style (available appointments)
        is_green = "#28B913" in style.upper() or "#28b913" in style.lower()
        if not is_green:
            # Also check for other green-ish colors
            green_pattern = re.search(r'color\s*:\s*#(?:28B913|009900|00[89A-F][0-9A-F]00|green)', style, re.IGNORECASE)
            if not green_pattern and "green" not in style.lower():
                continue

        # Get the date text from inner <li> or direct text
        li = link.find("li")
        if li:
            date_text = li.get_text(strip=True)
            li_classes = " ".join(li.get("class", []))
            # Skip disabled dates (grey - not yet opened)
            if "a_disable" in li_classes:
                continue
            # Skip fully booked (strikethrough)
            li_style = li.get("style", "")
            if "line-through" in li_style:
                continue
        else:
            date_text = link.get_text(strip=True)

        # Validate it's a real date number
        if not date_text or not date_text.isdigit():
            continue

        # Extract date from href as fallback validation
        parsed = urlparse(href)
        qs = parse_qs(parsed.query) if parsed.query else {}
        href_date = qs.get("date", [None])[0]
        if href_date and href_date == "0":
            continue  # padding cells have date=0

        # Avoid duplicates
        if date_text in seen_dates:
            continue
        seen_dates.add(date_text)

        available.append(
            AvailableSlot(
                date=date_text,
                month=month,
                year=year,
                apt_type=apt_type,
                location_id=location_id,
                service_id=service_id,
                url=url,
            )
        )

    if available:
        logger.info("Found %d available slot(s) for %s/%s: dates %s",
                     len(available), month, year,
                     ", ".join(s.date for s in available))
    else:
        logger.info("No available slots for %s/%s", month, year)

    return available
