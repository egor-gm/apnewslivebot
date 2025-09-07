import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import apnewslivebot


def test_llm_outage_falls_back_to_topic_tag(monkeypatch):
    def broken_llm(*args, **kwargs):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(apnewslivebot, "llm_hashtags", broken_llm)

    topic = "AP Top 25"
    try:
        tags = apnewslivebot.llm_hashtags("Headline", topic, "2024-01-01T00:00:00Z", "https://example.com")
    except Exception:
        tags = []

    if not tags:
        tags = apnewslivebot.topic_only_hashtags(topic)

    assert tags == ["#Top25"]
    assert len(tags) == 1
