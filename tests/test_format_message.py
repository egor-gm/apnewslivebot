import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
os.environ.setdefault("TELEGRAM_CHANNEL_ID", "test")

import apnewslivebot


def test_format_message_escapes_additional_chars():
    title = "chars ( ) ~ > #"
    msg = apnewslivebot.format_message(
        "Topic", title, "https://example.com", "2024-01-01T00:00:00Z"
    )

    assert "\\(" in msg
    assert "\\)" in msg
    assert "\\~" in msg
    assert "\\>" in msg
    assert "\\#" in msg

