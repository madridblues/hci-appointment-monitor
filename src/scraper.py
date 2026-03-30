"""Scrapes the HCI London appointment page for available dates."""

import logging
from dataclasses import dataclass
from urllib.parse import urlencode

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

    The HCI London appointment calendar uses a table layout where:
    - Available dates are clickable links (<a> tags) inside table cells
    - Unavailable/past dates are plain text or greyed-out cells
    - The calendar is rendered as an HTML <table> with class "cal_table" or similar
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

    Detection strategies (tried in order):
    1. Look for clickable date links in calendar table cells (<td> with <a>)
    2. Look for cells with availability-indicating CSS classes (e.g., "available", "green", "open")
    3. Look for cells with onclick handlers that suggest booking capability
    """
    soup = BeautifulSoup(html, "html.parser")
    available: list[AvailableSlot] = []

    # Strategy 1: Find calendar tables and look for clickable date links
    for table in soup.find_all("table"):
        for td in table.find_all("td"):
            link = td.find("a")
            if not link:
                continue

            date_text = link.get_text(strip=True)
            if not date_text.isdigit():
                continue

            href = link.get("href", "")
            # Skip navigation links (prev/next month), only match date booking links
            if "month=" in href and "day=" not in href and "date=" not in href:
                continue

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

    # Strategy 2: Look for cells with availability CSS classes
    if not available:
        availability_classes = ["available", "green", "open", "active", "bookable"]
        for td in soup.find_all("td"):
            td_classes = " ".join(td.get("class", []))
            if any(cls in td_classes.lower() for cls in availability_classes):
                date_text = td.get_text(strip=True)
                if date_text.isdigit():
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

    # Strategy 3: Look for cells with onclick handlers
    if not available:
        for td in soup.find_all("td", onclick=True):
            date_text = td.get_text(strip=True)
            if date_text.isdigit():
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
        logger.info("Found %d available slot(s) for %s/%s", len(available), month, year)
    else:
        logger.info("No available slots for %s/%s", month, year)

    return available
