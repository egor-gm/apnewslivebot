import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
os.environ.setdefault("TELEGRAM_CHANNEL_ID", "test")

import apnewslivebot


def test_main_skips_duplicate_links(monkeypatch):
    messages = []

    def mock_get_live_topics():
        return {"A": "urlA", "B": "urlB"}

    def mock_parse_live_page(topic_name, url):
        return [(topic_name, "Title", "https://example.com/shared", "2024-01-01T00:00:00Z")]

    def mock_send(msg):
        messages.append(msg)

    def stop_sleep(seconds):
        raise SystemExit

    monkeypatch.setattr(apnewslivebot, "get_live_topics", mock_get_live_topics)
    monkeypatch.setattr(apnewslivebot, "parse_live_page", mock_parse_live_page)
    monkeypatch.setattr(apnewslivebot, "send_telegram_message", mock_send)
    monkeypatch.setattr(apnewslivebot.time, "sleep", stop_sleep)
    monkeypatch.setattr(apnewslivebot, "save_sent", lambda: None)
    monkeypatch.setattr(apnewslivebot, "llm_hashtags", lambda *a, **k: [])

    apnewslivebot.sent_links.clear()
    apnewslivebot.sent_post_ids.clear()

    try:
        apnewslivebot.main()
    except SystemExit:
        pass

    # first message is the start notification
    assert len(messages) == 3
    article_messages = [m for m in messages if "https://example.com/shared" in m]
    assert len(article_messages) == 2
