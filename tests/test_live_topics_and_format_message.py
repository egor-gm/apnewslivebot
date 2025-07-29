import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
os.environ.setdefault("TELEGRAM_CHANNEL_ID", "test")

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import apnewslivebot


# Read sample homepage HTML fixture
with open(os.path.join(os.path.dirname(__file__), "ap_homepage.html"), "r", encoding="utf-8") as f:
    HOMEPAGE_HTML = f.read()


def test_get_live_topics(monkeypatch):
    def mock_fetch(url, timeout=15, retries=3, backoff=3):
        return HOMEPAGE_HTML

    monkeypatch.setattr(apnewslivebot, "fetch", mock_fetch)

    topics = apnewslivebot.get_live_topics()

    assert topics == {
        "Topic One": "https://apnews.com/live/topic-one",
        "Topic Two": "https://apnews.com/live/topic-two",
        "Topic Three": "https://apnews.com/live/topic-three",
    }


def test_format_message_plain():
    msg = apnewslivebot.format_message(
        "Tech",
        "Hello_world [update] *bold* `code`",
        "https://example.com/a",
        "2024-01-01T00:00:00Z",
    )

    expected_date = (
        datetime(2024, 1, 1, tzinfo=timezone.utc)
        .astimezone(ZoneInfo("Europe/Paris"))
        .strftime("%m/%d/%y %H:%M")
    )
    expected = (
        "Hello_world [update] *bold* `code`\n\n"
        f"📰 Tech - {expected_date} CET\n\n"
        "https://example.com/a"
    )
    assert msg == expected
