import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
os.environ.setdefault("TELEGRAM_CHANNEL_ID", "test")

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import apnewslivebot


def test_format_message_plain_text():
    title = "chars ( ) ~ > #"
    msg = apnewslivebot.format_message(
        "Topic",
        title,
        "https://example.com",
        "2024-01-01T00:00:00Z",
    )

    expected_date = (
        datetime(2024, 1, 1, tzinfo=timezone.utc)
        .astimezone(ZoneInfo("Europe/Paris"))
        .strftime("%m/%d/%y %H:%M")
    )
    expected = f"{title}\n\n📰 Topic - {expected_date} CET\n\nhttps://example.com"
    assert msg == expected


def test_format_message_strips_html():
    title = "<b>AP poll tracker: Trump's disapproval</b>"
    msg = apnewslivebot.format_message(
        "Topic",
        title,
        "https://example.com",
        "2024-01-01T00:00:00Z",
    )

    expected_date = (
        datetime(2024, 1, 1, tzinfo=timezone.utc)
        .astimezone(ZoneInfo("Europe/Paris"))
        .strftime("%m/%d/%y %H:%M")
    )
    expected_title = "AP poll tracker: Trump's disapproval"
    expected = f"{expected_title}\n\n📰 Topic - {expected_date} CET\n\nhttps://example.com"
    assert msg == expected

