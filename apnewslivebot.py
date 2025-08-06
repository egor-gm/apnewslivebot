import os
import time
import json
import logging
import re
import signal
import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Dict, Set, List, Optional, Tuple

import requests
import cloudscraper
from bs4 import BeautifulSoup
from upstash_redis import Redis


# ---------- HTTP scraper (Cloudflare-aware) ----------
scraper = cloudscraper.create_scraper(
    browser={"browser": "chrome", "platform": "windows", "mobile": False}
)

# ---------- Config via environment variables ----------
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID")  # e.g. @YourChannelUsername or numeric chat id
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL_SECONDS", "40"))  # seconds between loops

# Backoff config
LONG_INTERVAL = int(os.environ.get("LONG_CHECK_INTERVAL_SECONDS", "300"))  # 5 min default
NO_TOPICS_THRESHOLD_SECONDS = int(os.environ.get("NO_TOPICS_THRESHOLD_SECONDS", "3600"))  # 1 hour

# Timezone for message timestamps
TIMEZONE = os.environ.get("TIMEZONE", "Europe/Paris")

# Telegram send options
TELEGRAM_PARSE_MODE = os.environ.get("TELEGRAM_PARSE_MODE", "")  # "" (plain) | "MarkdownV2" | "HTML"
DISABLE_WEB_PAGE_PREVIEW = os.environ.get("DISABLE_WEB_PAGE_PREVIEW", "true").lower() == "true"
DISABLE_NOTIFICATION = os.environ.get("DISABLE_NOTIFICATION", "false").lower() == "true"

# Debug and test modes
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
SELF_TEST = os.environ.get("SELF_TEST", "false").lower() == "true"

HOMEPAGE_URL = "https://apnews.com"

if not BOT_TOKEN or not CHANNEL_ID:
    # Do not hard-exit in SELF_TEST
    if not SELF_TEST:
        raise SystemExit("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID env vars")

# ---------- Logging setup ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# ---------- Optional Upstash Redis ----------
REDIS_URL = os.environ.get("UPSTASH_REDIS_REST_URL")
REDIS_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN")
redis_client: Optional[Redis] = None
if REDIS_URL and REDIS_TOKEN:
    try:
        redis_client = Redis(url=REDIS_URL, token=REDIS_TOKEN)
        logging.info("Using Upstash Redis for state storage")
    except Exception as e:
        logging.warning(f"Redis init failed: {e}")
        redis_client = None

# ---------- Persistence (sent IDs and links) ----------
SENT_FILE = "sent.json"
sent_links: Set[str] = set()
sent_post_ids: Set[str] = set()  # track LiveBlogPost IDs that were sent


def load_sent() -> None:
    """Load sent IDs and links from Redis or local file."""
    global sent_links, sent_post_ids
    if redis_client:
        try:
            sent_links = set(redis_client.smembers("sent_links") or [])
            sent_post_ids = set(redis_client.smembers("sent_post_ids") or [])
            logging.info(
                f"Loaded {len(sent_links)} links and {len(sent_post_ids)} post_ids from Redis"
            )
            return
        except Exception as e:
            logging.warning(f"Could not load from Redis: {e}")
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
                f"Loaded {len(sent_links)} links and {len(sent_post_ids)} post_ids from file"
            )
        except Exception as e:
            logging.warning(f"Could not load {SENT_FILE}: {e}")


def save_sent() -> None:
    """Persist sent IDs and links to Redis and local file."""
    try:
        if redis_client:
            try:
                # Use two calls for broader compatibility
                redis_client.delete("sent_links")
                redis_client.delete("sent_post_ids")
                if sent_links:
                    redis_client.sadd("sent_links", *sent_links)
                if sent_post_ids:
                    redis_client.sadd("sent_post_ids", *sent_post_ids)
            except Exception as e:
                logging.warning(f"Could not save to Redis: {e}")
        with open(SENT_FILE, "w", encoding="utf-8") as f:
            json.dump({"links": list(sent_links), "post_ids": list(sent_post_ids)}, f)
    except Exception as e:
        logging.warning(f"Could not save {SENT_FILE}: {e}")


# ---------- HTTP helper with retries ----------

def fetch(url: str, timeout: int = 15, retries: int = 3, backoff: int = 3) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (MonitoringBot; +https://github.com/you/yourbot)",
        "Accept": "text/html,application/xhtml+xml",
    }
    global scraper
    for attempt in range(1, retries + 1):
        try:
            resp = scraper.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 403:
                logging.warning(
                    f"403 for {url} attempt {attempt} - recreating scraper"
                )
                scraper = cloudscraper.create_scraper(
                    browser={"browser": "chrome", "platform": "windows", "mobile": False}
                )
                if attempt == retries:
                    resp.raise_for_status()
                else:
                    time.sleep(backoff * attempt)
                    continue
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            logging.warning(f"Fetch error {url} attempt {attempt}: {e}")
            if attempt == retries:
                raise
            time.sleep(backoff * attempt)
    return ""


# ---------- Live topics and posts parsing ----------

def get_live_topics(html: Optional[str] = None) -> Dict[str, str]:
    """Return dict topic_name -> full_url for each live topic in nav.

    Strategy:
      1. Find any text containing 'live:' and look for following anchor.
      2. Also scan anchors whose text starts with 'LIVE:'.
    If html is provided, parse it instead of fetching the homepage.
    """
    if html is None:
        html = fetch(HOMEPAGE_URL)
    soup = BeautifulSoup(html, "html.parser")
    topics: Dict[str, str] = {}

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


def normalize_url(href: str) -> str:
    return href if href.startswith("http") else HOMEPAGE_URL + href


# --- Permalink resolution helpers ---
GUID_LIKE_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _norm_text(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u2019", "'")  # curly apostrophe to straight
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def _build_article_index(soup: BeautifulSoup) -> Dict[str, str]:
    """Map normalized heading text -> article id for GUID-like ids.
    Looks for <article id="..."> with an <h1|h2|h3> inside.
    """
    index: Dict[str, str] = {}
    for art in soup.find_all("article"):
        aid = (art.get("id") or "").strip()
        if not aid:
            continue
        # keep ids that look like GUIDs (00000198-... is also GUID-like)
        if not GUID_LIKE_RE.match(aid) and len(aid.split("-")) != 5:
            continue
        h = art.find(["h1", "h2", "h3"]) or art.find(class_=re.compile(r"headline|title", re.I))
        if not h:
            continue
        heading = h.get_text(" ", strip=True)
        key = _norm_text(heading)
        if key and key not in index:
            index[key] = aid
    return index


def _find_article_id_by_time(soup: BeautifulSoup, ts_iso: str) -> Optional[str]:
    """Heuristic: find the <article> whose <time datetime> is closest to ts_iso.
    Returns its id if it looks GUID-like.
    """
    try:
        target = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    except Exception:
        return None

    closest: Tuple[float, Optional[str]] = (float("inf"), None)
    for t in soup.find_all("time"):
        dt_attr = t.get("datetime") or t.get("data-datetime")
        if not dt_attr:
            continue
        try:
            dt_val = datetime.fromisoformat(dt_attr.replace("Z", "+00:00"))
        except Exception:
            continue
        diff = abs((dt_val - target).total_seconds())
        art = t.find_parent("article")
        aid = (art.get("id") if art else None) or None
        if not aid:
            continue
        if not GUID_LIKE_RE.match(aid) and len(aid.split("-")) != 5:
            continue
        if diff < closest[0]:
            closest = (diff, aid)

    # accept if within 12 hours to be safe; tune as needed
    return closest[1] if closest[0] <= 12 * 3600 else None


def resolve_post_permalink(soup: BeautifulSoup,
                           live_url: str,
                           copy_links: Dict[str, str],
                           post_id: Optional[str],
                           post_url: Optional[str],
                           title: str,
                           ts_iso: str) -> str:
    """Return the best permalink for a post with a fragment that matches UI copy-link.
    Preference order:
      0) If JSON-LD post_url already contains a #fragment, trust it
      1) Exact match via bsp-copy-link mapping (by id or its fragment)
      2) Match article heading text to get its <article id>
      3) Match by nearest <time datetime> to get parent <article id>
      4) Fallback: live_url (no fragment) to avoid wrong fragments
    """
    # 0) If JSON-LD already provides a URL with a fragment, prefer it
    if post_url:
        pu = str(post_url).strip()
        if pu.startswith("#"):
            return f"{live_url}{pu}"
        if "#" in pu:
            return normalize_url(pu)

    # 1) use explicit copy-link mapping if available
    if post_id:
        frag = str(post_id).split("#")[-1]
        if frag in copy_links:
            return copy_links[frag]
        if post_id in copy_links:
            return copy_links[post_id]

    # 2) match by heading text
    idx = _build_article_index(soup)
    key = _norm_text(title)
    if key and key in idx:
        return f"{live_url}#{idx[key]}"

    # 3) match by nearest timestamp
    aid = _find_article_id_by_time(soup, ts_iso)
    if aid:
        return f"{live_url}#{aid}"

    # 4) fallback: live page without fragment
    logging.warning("Falling back to live_url without fragment; no anchor could be resolved for title '%s'", title)
    return live_url


def parse_live_page(topic_name: str, url: str, html: Optional[str] = None) -> List[Tuple[str, str, str, str]]:
    """Scrape via the JSON-LD <script type="application/ld+json"> of type LiveBlogPosting.

    It extracts a list of tuples: (post_id, title, permalink, ts_iso).
    If html is provided, parse it instead of fetching from the url.
    """
    if html is None:
        html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")

    # Map post id -> full share/permalink from multiple sources
    copy_links: Dict[str, str] = {}

    # 1) <bsp-copy-link data-link="...#fragment">
    for cl in soup.find_all("bsp-copy-link"):
        data_link = cl.get("data-link")
        if not data_link:
            continue
        # normalize to absolute and extract fragment
        full_link = normalize_url(data_link) if not data_link.startswith("#") else f"{url}{data_link}"
        m = re.search(r"#([^#]+)$", full_link)
        if m:
            copy_links[m.group(1)] = full_link
        # also map the parent article id if available
        parent = cl.find_parent("article")
        if parent and parent.get("id"):
            copy_links[parent["id"]] = full_link

    # 2) Any element with data-clipboard-text that looks like a URL with a #fragment
    for el in soup.find_all(attrs={"data-clipboard-text": True}):
        raw = (el.get("data-clipboard-text") or "").strip()
        if not raw or "#" not in raw:
            continue
        full_link = normalize_url(raw) if not raw.startswith("#") else f"{url}{raw}"
        m = re.search(r"#([^#]+)$", full_link)
        if m:
            copy_links[m.group(1)] = full_link

    # 3) <a href="...#fragment"> anywhere on the page (including inside articles)
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if "#" not in href:
            continue
        full_link = normalize_url(href) if not href.startswith("#") else f"{url}{href}"
        m = re.search(r"#([^#]+)$", full_link)
        if m:
            copy_links[m.group(1)] = full_link

    # 4) Seed known <article id> values so direct id matches can resolve immediately
    for art in soup.find_all("article"):
        aid = (art.get("id") or "").strip()
        if not aid:
            continue
        # keep ids that look like GUIDs (00000198-... is also GUID-like)
        if not GUID_LIKE_RE.match(aid) and len(aid.split("-")) != 5:
            continue
        url_with_frag = f"{url}#{aid}"
        # do not overwrite an explicit copy/share link if we already captured one
        copy_links.setdefault(aid, url_with_frag)

    # Find the JSON-LD block for the live blog, including inside @graph arrays
    ld_json = None
    for script in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            raw_text = script.get_text(strip=True)
            if not raw_text:
                continue
            raw = json.loads(raw_text)
        except Exception:
            continue
        # If raw is a dict and has @graph, search inside it
        if isinstance(raw, dict) and "@graph" in raw and isinstance(raw["@graph"], list):
            for entry in raw["@graph"]:
                if not isinstance(entry, dict):
                    continue
                typ = entry.get("@type")
                if isinstance(typ, str):
                    if typ == "LiveBlogPosting":
                        ld_json = entry
                        break
                elif isinstance(typ, list):
                    if "LiveBlogPosting" in typ:
                        ld_json = entry
                        break
            if ld_json:
                break
        # Otherwise, treat as normal
        entries = raw if isinstance(raw, list) else [raw]
        for entry in entries:
            typ = entry.get("@type")
            if isinstance(typ, str):
                if typ == "LiveBlogPosting":
                    ld_json = entry
                    break
            elif isinstance(typ, list):
                if "LiveBlogPosting" in typ:
                    ld_json = entry
                    break
        if ld_json:
            break

    if not ld_json:
        logging.warning(f"No LiveBlogPosting JSON-LD found for {topic_name}")
        return []

    # Attempt to find update arrays by known keys
    posts: List[dict] = []
    for key in ("blogPosts", "liveBlogUpdate", "updates"):
        val = ld_json.get(key)
        if isinstance(val, list):
            posts = val
            break
        elif isinstance(val, dict):
            posts = [val]
            break
    # Fallback: any list of dicts in ld_json
    if not posts:
        for val in ld_json.values():
            if isinstance(val, list) and val and isinstance(val[0], dict):
                posts = val
                break

    if not posts:
        logging.warning(
            f"No update list found in JSON-LD for {topic_name}: keys={list(ld_json.keys())}"
        )
        return []

    new_items: List[Tuple[str, str, str, str]] = []
    for post in posts:
        pid = (
            post.get("@id")
            or post.get("url")
            or f"{post.get('headline')}_{post.get('datePublished', post.get('dateModified', ''))}"
        )
        title = post.get("headline", "").strip() or post.get("name", "").strip()
        ts_iso = post.get("datePublished") or post.get("dateModified") or datetime.now(timezone.utc).isoformat()
        post_url = post.get("url") or post.get("mainEntityOfPage")

        # Resolve the most accurate permalink with a correct fragment
        permalink = resolve_post_permalink(
            soup=soup,
            live_url=url,
            copy_links=copy_links,
            post_id=str(pid) if pid else None,
            post_url=post_url,
            title=title,
            ts_iso=ts_iso,
        )

        if pid and pid not in sent_post_ids:
            new_items.append((str(pid), title, permalink, ts_iso))

    # Sort oldest -> newest by timestamp
    new_items.sort(key=lambda t: t[3] or "")
    return new_items


# ---------- Telegram send ----------

def _telegram_api_send(text: str, parse_mode: str = "") -> requests.Response:
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    params = {
        "chat_id": CHANNEL_ID,
        "text": text,
    }
    if parse_mode:
        params["parse_mode"] = parse_mode
    if DISABLE_WEB_PAGE_PREVIEW:
        params["disable_web_page_preview"] = True
    if DISABLE_NOTIFICATION:
        params["disable_notification"] = True
    return requests.post(api_url, data=params, timeout=15)


def send_telegram_message(text: str) -> None:
    """Send message to Telegram with a safe fallback.

    To avoid 400 parse errors, default to plain text unless TELEGRAM_PARSE_MODE is set.
    If a 400 occurs with a parse mode, retry once without parse mode.
    """
    # Truncate if necessary to avoid hitting Telegram 4096 char limit
    if len(text) > 4000:
        text = text[:4000] + "\n…"

    if DRY_RUN:
        logging.info(f"[DRY_RUN] Would send to Telegram:\n{text}")
        return

    try:
        # Prefer plain text unless user explicitly opts in
        if not TELEGRAM_PARSE_MODE:
            r = _telegram_api_send(text, parse_mode="")
            if r.status_code != 200:
                logging.warning(f"Telegram send failed {r.status_code}: {r.text[:200]}")
            return

        # Try with requested parse mode first
        r = _telegram_api_send(text, parse_mode=TELEGRAM_PARSE_MODE)
        if r.status_code == 200:
            return
        # If parse error, retry as plain text once
        if r.status_code == 400 and "can't parse entities" in r.text.lower():
            logging.warning("Parse error - retrying as plain text")
            r2 = _telegram_api_send(text, parse_mode="")
            if r2.status_code != 200:
                logging.warning(f"Telegram send failed after fallback {r2.status_code}: {r2.text[:200]}")
        else:
            logging.warning(f"Telegram send failed {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logging.warning(f"Telegram exception: {e}")


def format_message(topic: str, title: str, url: str, ts_iso: str) -> str:
    """Format a post into a Telegram-friendly message."""
    # Parse the ISO timestamp and convert to configured timezone
    try:
        dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
        local_tz = ZoneInfo(TIMEZONE)
        local_dt = dt.astimezone(local_tz)
        date_str = local_dt.strftime("%m/%d/%y %H:%M")
        tz_abbr = local_dt.tzname() or TIMEZONE
    except Exception:
        date_str = ts_iso  # fallback to raw timestamp
        tz_abbr = TIMEZONE

    # Clean any HTML tags in the title
    clean_title = BeautifulSoup(title or "", "html.parser").get_text()

    # Build the message (use plain text friendly formatting)
    lines = [
        clean_title,
        "",
        f"📰 {topic} - {date_str} {tz_abbr}",
        "",
        url,
    ]
    return "\n".join(lines).strip()


# ---------- Delay calculation ----------

def calculate_delay(current_interval: float, elapsed: float) -> float:
    """Return remaining delay before next cycle.

    Ensures the loop does not wait extra time if processing exceeded the
    configured interval.
    """
    return max(0, current_interval - elapsed)


# ---------- Graceful shutdown ----------

def _install_signal_handlers() -> None:
    def _handle(sig, frame):
        logging.info(f"Signal {sig} received - saving state and exiting")
        save_sent()
        raise SystemExit(0)

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(s, _handle)
        except Exception:
            pass


# ---------- Self test helpers ----------

def _self_test() -> None:
    """Run offline tests against local HTML samples."""
    logging.info("Running SELF_TEST")

    # Fake homepage with a LIVE link
    homepage_html = """
    <html><body>
      <nav>
        <span>Live:</span> <a href="/live/world-news/foobar">LIVE: World updates</a>
      </nav>
    </body></html>
    """
    topics = get_live_topics(homepage_html)
    assert topics, "Expected at least one topic from fake homepage"
    fake_url = list(topics.values())[0]

    # Fake live page with JSON-LD and bsp-copy-link
    live_html = """
    <html><body>
      <bsp-copy-link data-link="/live/world-news/foobar#post-123"></bsp-copy-link>
      <script type="application/ld+json">
      {
        "@context": "https://schema.org",
        "@type": "LiveBlogPosting",
        "blogPosts": [
          {
            "@id": "post-123",
            "headline": "Test headline with _markdown_ chars",
            "datePublished": "2025-08-05T20:03:00Z"
          },
          {
            "@id": "post-124",
            "headline": "Second headline",
            "datePublished": "2025-08-05T21:03:00Z"
          }
        ]
      }
      </script>
    </body></html>
    """
    items = parse_live_page("World updates", fake_url, html=live_html)
    assert len(items) == 2, f"Expected 2 items, got {len(items)}"

    # Ensure sort is oldest -> newest
    assert items[0][0].endswith("123") and items[1][0].endswith("124"), "Sorting failed"

    # Ensure formatting does not crash on markdown special chars
    msg = format_message("World updates", items[0][1], items[0][2], items[0][3])
    assert isinstance(msg, str) and len(msg) > 0, "format_message returned empty"

    # Simulate send (DRY_RUN recommended when running SELF_TEST)
    if DRY_RUN:
        send_telegram_message(msg)

    # Case A: JSON-LD supplies absolute post.url with fragment; no DOM anchors present
    live_html_only_ld = """
    <html><body>
      <script type="application/ld+json">
      {
        "@context": "https://schema.org",
        "@type": "LiveBlogPosting",
        "liveBlogUpdate": [
          {
            "@id": "abc-1",
            "headline": "Headline A",
            "datePublished": "2025-08-05T22:00:00Z",
            "url": "https://apnews.com/live/world-news/foobar#post-aaa"
          },
          {
            "@id": "abc-2",
            "headline": "Headline B",
            "datePublished": "2025-08-05T23:00:00Z",
            "url": "/live/world-news/foobar#post-bbb"
          }
        ]
      }
      </script>
    </body></html>
    """
    items2 = parse_live_page("World updates", fake_url, html=live_html_only_ld)
    assert items2 and items2[0][2].endswith("#post-aaa"), f"Expected fragment from absolute url, got {items2}"
    assert items2[1][2].endswith("#post-bbb"), f"Expected fragment from relative url, got {items2}"

    # Case B: JSON-LD using @graph and @type as list
    live_html_graph = """
    <html><body>
      <script type=\"application/ld+json\">
      {
        "@context": "https://schema.org",
        "@graph": [
          {"@type": ["BreadcrumbList"]},
          {
            "@type": ["NewsArticle", "LiveBlogPosting"],
            "updates": [
              {
                "@id": "g1",
                "headline": "Graph Headline",
                "datePublished": "2025-08-05T22:30:00Z",
                "url": "#graph-post"
              }
            ]
          }
        ]
      }
      </script>
    </body></html>
    """
    items3 = parse_live_page("World updates", fake_url, html=live_html_graph)
    assert items3 and items3[0][2].endswith("#graph-post"), f"Expected fragment from graph url, got {items3}"

    logging.info("SELF_TEST passed")


# ---------- Main loop ----------

def main() -> None:
    load_sent()
    _install_signal_handlers()
    logging.info("Bot started")

    if SELF_TEST:
        # DRY_RUN is recommended for self test
        _self_test()
        return

    # Notify channel on start, but do not fail on error
    try:
        send_telegram_message("🔔 AP News Live Bot started")
    except Exception as e:
        logging.warning(f"Startup notification failed: {e}")

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
                    logging.info("LIVE topics returned - reverting interval")
                    current_interval = CHECK_INTERVAL
            else:
                if (
                    time.time() - last_topics_seen_at
                ) > NO_TOPICS_THRESHOLD_SECONDS and current_interval != LONG_INTERVAL:
                    logging.info("No LIVE topics for 1 hour - switching interval to 5 minutes")
                    current_interval = LONG_INTERVAL

            if not topics:
                logging.info("No live topics this cycle")

            for topic_name, topic_url in topics.items():
                logging.info(f"Checking {topic_name} -> {topic_url}")
                new_posts = parse_live_page(topic_name, topic_url)

                for pid, title, link, ts_iso in new_posts:
                    if pid in sent_post_ids:
                        continue
                    msg = format_message(topic_name, title, link, ts_iso)
                    send_telegram_message(msg)
                    sent_post_ids.add(pid)
                    sent_links.add(link)
                    save_sent()
                    logging.info(f"Sent: {title}")

        except Exception as e:
            logging.error(f"Cycle error: {e}")

        elapsed = time.time() - loop_start
        # Sleep only for the remaining time left in the interval. If the loop
        # took longer than the interval, start the next iteration immediately
        # instead of adding extra delay.
        time.sleep(calculate_delay(current_interval, elapsed))


if __name__ == "__main__":
    main()
