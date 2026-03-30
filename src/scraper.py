"""Scrapes the HCI London appointment page for available dates and time slots."""

import logging
import re
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
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

BATCH_SIZE = 5  # Number of green dates to check concurrently per batch
HEALTH_CHECK_TIMEOUT = 90  # Health check timeout (proxy can be slow)


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


@dataclass
class CheckResult:
    """Result of checking a month for appointments."""
    slots: list[AvailableSlot]
    fetched_via: str
    fetched_ip: str
    response_snippet: str = ""  # first 500 chars for debugging
    green_dates_found: int = 0
    green_dates_list: list[str] = field(default_factory=list)
    dates_checked: int = 0      # how many date pages we actually fetched


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
        self.via = via
        self.ip = ip


def _make_session(proxy_url: str) -> requests.Session:
    """Create a fresh browser-like session routed through proxy."""
    session = requests.Session()
    # Blank User-Agent → Crawlbase auto-rotates it
    session.headers.update({
        "User-Agent": "",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.5",
    })
    session.proxies = {"http": proxy_url, "https": proxy_url}
    return session


def _detect_proxy_ip(proxy_url: str) -> str:
    """Quick IP detection through proxy via ipify."""
    try:
        session = requests.Session()
        session.proxies = {"http": proxy_url, "https": proxy_url}
        resp = session.get("https://api.ipify.org?format=text", timeout=15, verify=False)
        session.close()
        return resp.text.strip()
    except Exception:
        return "unknown"


def health_check(proxy_url: str, location_id: str = "3", month: str = "04",
                 year: str = "2026") -> dict:
    """Quick health ping to the actual appointment URL via proxy.
    Returns dict with status, response_time, http_code, blocked, error."""
    url = build_url(month, year, "Submission", location_id, "29")
    start = time.time()
    result = {
        "url": url,
        "status": "unknown",
        "response_time": 0,
        "http_code": 0,
        "blocked": None,
        "error": None,
        "proxy_ip": "unknown",
    }
    try:
        # Try up to 2 times on 5xx (520 is common Cloudflare fluke)
        response = None
        for hc_attempt in range(2):
            session = _make_session(proxy_url)
            response = session.get(url, timeout=HEALTH_CHECK_TIMEOUT, verify=False)
            session.close()
            if response.status_code < 500:
                break
            if hc_attempt == 0:
                logger.info("Health check got HTTP %d, retrying with new proxy IP...", response.status_code)
                time.sleep(2)

        result["response_time"] = round(time.time() - start, 1)
        result["http_code"] = response.status_code
        result["proxy_ip"] = _detect_proxy_ip(proxy_url)

        # Check Cloudflare block
        cf_block = _is_cloudflare_blocked(response)
        if cf_block:
            result["status"] = "blocked"
            result["blocked"] = cf_block
            return result

        if response.status_code >= 500:
            # 5xx after retry — still report as server_error but mark as "degraded"
            # so monitor can decide to try checks anyway
            result["status"] = "server_error"
            result["error"] = f"HTTP {response.status_code}"
            return result

        if response.status_code == 200:
            body_lower = response.text.lower()
            # Check for maintenance page
            if "under maintenance" in body_lower or "we'll be back" in body_lower or "maintenance" in response.text[:500].lower():
                result["status"] = "maintenance"
                result["error"] = "Site is under maintenance"
                return result
            # Verify it's actually the appointment page
            if "calendar" in body_lower or "appointment" in body_lower:
                result["status"] = "up"
            else:
                result["status"] = "unexpected_content"
                result["error"] = f"Unexpected page (first 100 chars: {response.text[:100]})"
            return result

        result["status"] = "error"
        result["error"] = f"HTTP {response.status_code}"
        return result

    except requests.exceptions.Timeout:
        result["response_time"] = round(time.time() - start, 1)
        result["status"] = "timeout"
        result["error"] = f"Timed out after {HEALTH_CHECK_TIMEOUT}s"
        return result
    except requests.exceptions.ConnectionError as e:
        result["response_time"] = round(time.time() - start, 1)
        result["status"] = "connection_error"
        result["error"] = str(e)[:100]
        return result
    except Exception as e:
        result["response_time"] = round(time.time() - start, 1)
        result["status"] = "error"
        result["error"] = str(e)[:100]
        return result


CLOUDFLARE_BLOCK_SIGNATURES = [
    "attention required",
    "cf-browser-verification",
    "cloudflare ray id",
    "enable javascript and cookies to continue",
    "checking your browser",
    "please turn javascript on",
    "cf-challenge-platform",
    "just a moment",
]


def _is_cloudflare_blocked(response) -> str | None:
    """Check if response is a Cloudflare block/challenge page.
    Returns block reason string or None if not blocked."""
    # 403 with CF challenge
    if response.status_code == 403:
        body = response.text.lower()
        for sig in CLOUDFLARE_BLOCK_SIGNATURES:
            if sig in body:
                return f"CF-403: {sig}"
        return "CF-403: forbidden"

    # 503 with CF challenge (JS challenge page)
    if response.status_code == 503:
        body = response.text.lower()
        for sig in CLOUDFLARE_BLOCK_SIGNATURES:
            if sig in body:
                return f"CF-503: {sig}"

    # Check headers for CF block indicators
    if "cf-mitigated" in response.headers.get("server", "").lower():
        return "CF-mitigated"

    return None


def _fetch_page(url: str, proxy_url: str) -> FetchResult:
    """Fetch a page via proxy only. 3 retries with 150s timeout.
    Each retry gets a new proxy IP. Detects Cloudflare blocks."""
    last_error = None
    for attempt in range(3):
        try:
            session = _make_session(proxy_url)
            response = session.get(url, timeout=150, verify=False)

            # Check for Cloudflare block
            cf_block = _is_cloudflare_blocked(response)
            if cf_block:
                session.close()
                last_error = f"BLOCKED: {cf_block}"
                logger.warning("Attempt %d: Cloudflare blocked (%s), retrying with new proxy IP...",
                               attempt + 1, cf_block)
                if attempt == 2:
                    raise requests.exceptions.ConnectionError(last_error)
                continue

            # Retry on 5xx server errors (520, etc.)
            if response.status_code >= 500:
                session.close()
                last_error = f"HTTP {response.status_code}"
                if attempt == 2:
                    response.raise_for_status()
                logger.warning("Attempt %d: HTTP %d, retrying with new proxy IP...",
                               attempt + 1, response.status_code)
                continue

            response.raise_for_status()
            session.close()
            # Get proxy IP from response headers if available, else "rotated"
            proxy_ip = response.headers.get("X-Crawlbase-IP", "proxy-rotated")
            logger.info("Fetched via proxy IP %s (attempt %d): %s",
                        proxy_ip, attempt + 1, url[:80])
            return FetchResult(html=response.text, via="proxy", ip=proxy_ip)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_error = str(e)
            if attempt == 2:
                raise
            logger.warning("Attempt %d failed (%s), retrying with new proxy IP...",
                           attempt + 1, type(e).__name__)
            continue
    raise requests.exceptions.ConnectionError(f"All fetch attempts failed: {last_error}")


def _fetch_date_page(date_str: str, month: str, year: str, apt_type: str,
                     location_id: str, service_id: str, proxy_url: str) -> AvailableSlot | None:
    """Fetch a single date page and parse time slots. Returns AvailableSlot or None."""
    date_url = build_url(month, year, apt_type, location_id, service_id, date=date_str)
    try:
        result = _fetch_page(date_url, proxy_url)
        time_slots = _parse_time_slots(result.html)

        if time_slots:
            slot_summary = ", ".join(f"{ts.time} ({ts.available})" for ts in time_slots)
            logger.info("  Date %s: %s", date_str, slot_summary)
            return AvailableSlot(
                date=date_str, month=month, year=year,
                apt_type=apt_type, location_id=location_id,
                service_id=service_id, url=date_url,
                time_slots=time_slots,
                fetched_via=result.via, fetched_ip=result.ip,
                page_snapshot=result.html,
            )
        else:
            logger.info("  Date %s: no available time slots", date_str)
            return None
    except Exception:
        logger.exception("  Error checking date %s", date_str)
        return None


def check_appointments(
    month: str,
    year: str,
    apt_type: str = "Submission",
    location_id: str = "8",
    service_id: str = "29",
    proxy_url: str = "",
) -> CheckResult:
    """
    Fetch calendar, find green dates, then check them in batches of 5
    concurrently via proxy. Stops after first batch that finds slots.
    """
    url = build_url(month, year, apt_type, location_id, service_id)
    logger.info("Checking calendar: %s", url)

    # Step 1: Fetch calendar page
    result = _fetch_page(url, proxy_url)
    snippet = result.html[:500] if result.html else ""
    bookable_dates = _parse_bookable_dates(result.html, month)

    if not bookable_dates:
        logger.info("No green dates for %s/%s (via %s)", month, year, result.via)
        return CheckResult(
            slots=[], fetched_via=result.via, fetched_ip=result.ip,
            response_snippet=snippet, green_dates_found=0,
        )

    logger.info("Found %d green date(s) for %s/%s, checking in batches of %d...",
                len(bookable_dates), month, year, BATCH_SIZE)

    # Step 2: Check dates in batches of BATCH_SIZE, all concurrent within batch
    all_available: list[AvailableSlot] = []
    dates_checked = 0

    for batch_start in range(0, len(bookable_dates), BATCH_SIZE):
        batch = bookable_dates[batch_start:batch_start + BATCH_SIZE]
        batch_num = (batch_start // BATCH_SIZE) + 1
        logger.info("  Batch %d: checking dates %s", batch_num, batch)

        # Fetch all dates in this batch concurrently
        with ThreadPoolExecutor(max_workers=len(batch)) as executor:
            futures = {
                executor.submit(
                    _fetch_date_page, date_str, month, year,
                    apt_type, location_id, service_id, proxy_url,
                ): date_str
                for date_str in batch
            }
            for future in as_completed(futures):
                dates_checked += 1
                slot = future.result()
                if slot:
                    all_available.append(slot)

        # If we found slots in this batch, stop (no need to check more)
        if all_available:
            logger.info("  Found %d date(s) with slots in batch %d, stopping",
                        len(all_available), batch_num)
            break
        else:
            logger.info("  Batch %d: no slots found, trying next batch", batch_num)

    if all_available:
        logger.info("Found %d date(s) with time slots for %s/%s",
                     len(all_available), month, year)
    else:
        logger.info("No available time slots across %d dates for %s/%s",
                     dates_checked, month, year)

    return CheckResult(
        slots=all_available, fetched_via=result.via, fetched_ip=result.ip,
        response_snippet=snippet, green_dates_found=len(bookable_dates),
        green_dates_list=bookable_dates, dates_checked=dates_checked,
    )


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


def save_snapshot(slot: AvailableSlot) -> str:
    """Save the HTML snapshot of a found slot. Returns the snapshot filename."""
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"loc{slot.location_id}_{slot.date}_{slot.month}_{slot.year}_{ts}"
    filepath = SNAPSHOTS_DIR / f"{name}.html"
    if slot.page_snapshot:
        filepath.write_text(slot.page_snapshot, encoding="utf-8")
        logger.info("Saved snapshot: %s", name)
    return name
