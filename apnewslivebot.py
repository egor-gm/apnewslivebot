import os
import time
import json
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Set

import requests
from bs4 import BeautifulSoup

# ---------- Config via environment variables ----------
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID")  # e.g. @YourChannelUsername or numeric chat id
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL_SECONDS", "40"))  # seconds between loops
HOMEPAGE_URL = "https://apnews.com"

if not BOT_TOKEN or not CHANNEL_ID:
    raise SystemExit("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID env vars")

# ---------- Logging setup ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# ---------- Persistence (sent links) ----------
SENT_FILE = "sent.json"
sent_links: Set[str] = set()

def load_sent():
    global sent_links
    if os.path.isfile(SENT_FILE):
        try:
            with open(SENT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                sent_links = set(data)
            logging.info(f"Loaded {len(sent_links)} previously sent links")
        except Exception as e:
            logging.warning(f"Could not load sent.json: {e}")

def save_sent():
    try:
        with open(SENT_FILE, "w", encoding="utf-8") as f:
            json.dump(list(sent_links), f)
    except Exception as e:
        logging.warning(f"Could not save sent.json: {e}")

# ---------- HTTP helper with retries ----------
def fetch(url: str, timeout=15, retries=3, backoff=3) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (MonitoringBot; +https://github.com/you/yourbot)",
        "Accept": "text/html,application/xhtml+xml",
    }
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            logging.warning(f"Fetch error {url} attempt {attempt}: {e}")
            if attempt == retries:
                raise
            time.sleep(backoff * attempt)
    return ""  # never reached

# ---------- Extract live topic links from homepage ----------
def get_live_topics() -> Dict[str, str]:
    """
    Returns dict topic_name -> full_url for each live topic in nav.
    Strategy:
      1. Find any text containing 'live:' and look for following anchor.
      2. Also scan anchors whose text starts with 'LIVE:' (defensive).
    """
    html = fetch(HOMEPAGE_URL)
    soup = BeautifulSoup(html, "html.parser")
    topics = {}

    # Approach 1: text node containing 'live:'
    for text_node in soup.find_all(string=lambda t: t and "live:" in t.lower()):
        # We only want the ones that are label markers, not within articles
        parent = text_node.parent
        # Grab next anchor
        a = parent.find_next("a")
        if a and a.get("href"):
            name = a.get_text(strip=True)
            url = a["href"]
            if url.startswith("/"):
                url = HOMEPAGE_URL + url
            if name and url not in topics.values():
                topics[name] = url

    # Approach 2: anchors that include leading LIVE:
    for a in soup.find_all("a"):
        txt = a.get_text(" ", strip=True)
        if txt.lower().startswith("live:") and a.get("href"):
            name = txt.replace("LIVE:", "").replace("Live:", "").strip()
            # sometimes anchor itself is the live page
            url = a["href"]
            if url.startswith("/"):
                url = HOMEPAGE_URL + url
            if name and name not in topics:
                topics[name] = url

    return topics

# ---------- Parse live topic page for article links ----------
ARTICLE_HREF_RE = re.compile(r"^(/article|https://apnews\.com/article)")

def parse_live_page(topic_name: str, url: str) -> List[Tuple[str, str, str]]:
    """
    Returns list of (title, url, ts_iso) for new articles.
    Heuristic:
      - collect all anchors matching ARTICLE_HREF_RE
      - title = anchor text stripped
      - ts extracted from nearby <time>, data attributes, or fallback to now
    """
    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")
    new_items = []
    anchors = soup.find_all("a", href=True)
    for a in anchors:
        href = a["href"]
        if not ARTICLE_HREF_RE.match(href):
            continue
        full = href if href.startswith("http") else HOMEPAGE_URL + href
        # Basic normalize remove tracking queries
        if "?" in full:
            full = full.split("?", 1)[0]
        if full in sent_links:
            continue
        title = a.get_text(" ", strip=True)
        # Filter out empty or nav duplicates
        if not title or title.lower().startswith("live:"):
            continue

        ts_iso = extract_time(a)  # try from page
        new_items.append((title, full, ts_iso))
    return dedupe_order_preserving(new_items)

def dedupe_order_preserving(items: List[Tuple[str, str, str]]) -> List[Tuple[str, str, str]]:
    seen = set()
    out = []
    for t in items:
        if t[1] not in seen:
            seen.add(t[1])
            out.append(t)
    return out

def extract_time(a_tag) -> str:
    """
    Try to find a timestamp related to an anchor:
      - <time datetime="...">
      - sibling span/time elements with datetime/time attributes
      - fallback: now UTC
    """
    # direct <time> descendant or sibling
    time_tag = a_tag.find("time")
    if not time_tag:
        # check next siblings
        for sib in a_tag.parent.find_all(["time", "span"], limit=4):
            if sib.name == "time":
                time_tag = sib
                break
            # look for data attributes like data-source or datetime text patterns
    if time_tag:
        dt_attr = time_tag.get("datetime")
        if dt_attr:
            try:
                # standardize
                return datetime.fromisoformat(dt_attr.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
            except Exception:
                pass
        # fallback to text parse if looks like time (skip for brevity)
    # fallback now
    return datetime.now(timezone.utc).isoformat()

# ---------- Telegram send ----------
def send_telegram_message(text: str):
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    params = {
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(api_url, data=params, timeout=15)
        if r.status_code != 200:
            logging.warning(f"Telegram send failed {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logging.warning(f"Telegram exception: {e}")

def format_message(topic: str, title: str, url: str, ts_iso: str) -> str:
    # sanitize markdown special chars in title
    safe_title = title.replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")
    return f"📰 *{topic}* | {safe_title}\n{ts_iso}\n{url}"

# ---------- Main loop ----------
def main():
    load_sent()
    logging.info("Bot started")
    while True:
        loop_start = time.time()
        try:
            topics = get_live_topics()
            if not topics:
                logging.info("No live topics found this cycle")
            for topic_name, topic_url in topics.items():
                logging.info(f"Checking live topic: {topic_name} -> {topic_url}")
                new_articles = parse_live_page(topic_name, topic_url)
                # Sort oldest to newest by timestamp (ts_iso lexical works for ISO)
                new_articles.sort(key=lambda t: t[2])
                for title, link, ts_iso in new_articles:
                    if link in sent_links:
                        continue
                    msg = format_message(topic_name, title, link, ts_iso)
                    send_telegram_message(msg)
                    sent_links.add(link)
                    logging.info(f"Sent: {title} ({link})")
                if new_articles:
                    save_sent()
        except Exception as e:
            logging.error(f"Cycle error: {e}")
        # Sleep remaining time
        elapsed = time.time() - loop_start
        to_sleep = max(5, CHECK_INTERVAL - elapsed)
        time.sleep(to_sleep)

if __name__ == "__main__":
    main()