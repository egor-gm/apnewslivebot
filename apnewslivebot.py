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

# extra backoff config
LONG_INTERVAL = int(os.environ.get("LONG_CHECK_INTERVAL_SECONDS", "300"))  # 5 min default
NO_TOPICS_THRESHOLD_SECONDS = int(os.environ.get("NO_TOPICS_THRESHOLD_SECONDS", "3600"))  # 1 hour

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
sent_post_ids: Set[str] = set()  # track LiveBlogPost IDs that were sent

def load_sent():
    global sent_links, sent_post_ids
    if os.path.isfile(SENT_FILE):
        try:
            with open(SENT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                sent_links = set(data.get("links", []))
                sent_post_ids = set(data.get("post_ids", []))
            elif isinstance(data, list):
                # legacy format (only links)
                sent_links = set(data)
            logging.info(
                f"Loaded {len(sent_links)} links and {len(sent_post_ids)} post_ids"
            )
        except Exception as e:
            logging.warning(f"Could not load {SENT_FILE}: {e}")

def save_sent():
    try:
        with open(SENT_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {"links": list(sent_links), "post_ids": list(sent_post_ids)}, f
            )
    except Exception as e:
        logging.warning(f"Could not save {SENT_FILE}: {e}")

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
        parent = text_node.parent
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
            url = a["href"]
            if url.startswith("/"):
                url = HOMEPAGE_URL + url
            if name and name not in topics:
                topics[name] = url

    return topics

# ---------- Parse live topic page for article links ----------
ARTICLE_HREF_RE = re.compile(r"^(/article|https://apnews\.com/article)")

def normalize_url(href: str) -> str:
    return href if href.startswith("http") else HOMEPAGE_URL + href

def parse_live_page(topic_name: str, url: str) -> List[Tuple[str, str, str, str]]:
    """
    Scrape today's posts and return tuples:
        (pid, title, permalink, ts_iso)

    • Permalink comes from <bsp-copy-link data-link="…"> if present,
      else we fallback to liveURL#pid.
    • Stops when the date header switches to yesterday.
    """
    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")

    today_label = datetime.now(timezone.utc).strftime("%-d %B %Y").upper()
    feed = soup.find(attrs={"role": "feed"}) or soup
    new_items = []

    for post in feed.find_all("div", class_="LiveBlogPost"):  # nested allowed
        pid = post.get("id")
        if not pid or pid in sent_post_ids:
            continue

        date_hdr = post.find_previous("h3", class_="LiveBlogPage-dateGroup")
        if date_hdr and date_hdr.get_text(strip=True).upper() != today_label:
            break  # reached yesterday

        headline = post.find("h2", class_="LiveBlogPost-headline")
        if not headline:
            continue
        title = headline.get_text(" ", strip=True)

        # Preferred permalink from share button
        share_tag = post.find("bsp-copy-link", attrs={"data-link": True})
        if share_tag:
            permalink = share_tag["data-link"].split("?", 1)[0]
        else:
            permalink = url.split("#")[0] + f"#{pid}"

        ts_iso = extract_time(post)
        new_items.append((pid, title, permalink, ts_iso))

    return new_items

def extract_time(tag) -> str:
    time_tag = tag.find("time") or tag.find_previous("time")
    if time_tag and time_tag.get("datetime"):
        dt_attr = time_tag["datetime"]
        try:
            return datetime.fromisoformat(dt_attr.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
        except Exception:
            pass
    return datetime.now(timezone.utc).isoformat()

# ---------- Telegram send ----------
def send_telegram_message(text: str):
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    params = {"chat_id": CHANNEL_ID, "text": text, "parse_mode": "Markdown"}
    try:
        r = requests.post(api_url, data=params, timeout=15)
        if r.status_code != 200:
            logging.warning(f"Telegram send failed {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logging.warning(f"Telegram exception: {e}")

def format_message(topic: str, title: str, url: str, ts_iso: str) -> str:
    safe_title = (
        title.replace("_", "\\_")
        .replace("*", "\\*")
        .replace("[", "\\[")
        .replace("`", "\\`")
    )
    return f"📰 *{topic}* | {safe_title}\n{ts_iso}\n{url}"


# ---------- Main loop ----------
def main():
    load_sent()
    logging.info("Bot started")

    current_interval = CHECK_INTERVAL
    last_topics_seen_at = time.time()
    logging.info(f"Initial scan interval: {current_interval}s")

    while True:
        loop_start = time.time()
        try:
            topics = get_live_topics()

            # Adaptive interval logic
            if topics:
                last_topics_seen_at = time.time()
                if current_interval != CHECK_INTERVAL:
                    logging.info("LIVE topics returned – reverting interval")
                    current_interval = CHECK_INTERVAL
            else:
                if (
                    time.time() - last_topics_seen_at
                ) > NO_TOPICS_THRESHOLD_SECONDS and current_interval != LONG_INTERVAL:
                    logging.info("No LIVE topics for 1 hour – switching interval to 5 minutes")
                    current_interval = LONG_INTERVAL

            if not topics:
                logging.info("No live topics this cycle")

            for topic_name, topic_url in topics.items():
                logging.info(f"Checking {topic_name} -> {topic_url}")
                new_posts = parse_live_page(topic_name, topic_url)
                # Sort oldest → newest by timestamp (index 3)
                new_posts.sort(key=lambda t: t[3])

                for pid, title, link, ts_iso in new_posts:
                    if pid in sent_post_ids:
                        continue
                    msg = format_message(topic_name, title, link, ts_iso)
                    send_telegram_message(msg)
                    sent_post_ids.add(pid)
                    sent_links.add(link)  # still track URLs to avoid x‑topic dupes
                    logging.info(f"Sent: {title}")

                if new_posts:
                    save_sent()

        except Exception as e:
            logging.error(f"Cycle error: {e}")

        elapsed = time.time() - loop_start
        time.sleep(max(5, current_interval - elapsed))


if __name__ == "__main__":
    main()