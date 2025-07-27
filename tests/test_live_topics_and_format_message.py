import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
os.environ.setdefault("TELEGRAM_CHANNEL_ID", "test")

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


def test_format_message_markdown():
    msg = apnewslivebot.format_message(
        "Tech",
        "Hello_world [update] *bold* `code`",
        "https://example.com/a",
        "2024-01-01T00:00:00Z",
    )

    expected_title = "Hello\\_world \\[update] \\*bold\\* \\`code\\`"
    expected = (
        f"📰 *Tech* | {expected_title}\n"
        "2024-01-01T00:00:00Z\nhttps://example.com/a"
    )
    assert msg == expected
