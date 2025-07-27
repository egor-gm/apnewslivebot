import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
os.environ.setdefault("TELEGRAM_CHANNEL_ID", "test")
import apnewslivebot

# Minimal HTML snippet with JSON-LD containing both blogPosts and liveBlogUpdate
LD_JSON = {
    "@context": "https://schema.org",
    "@type": "LiveBlogPosting",
    "blogPosts": [
        {
            "@id": "p3",
            "headline": "Third",
            "url": "https://example.com/3",
            "datePublished": "2024-01-01T10:00:00Z"
        },
        {
            "@id": "p1",
            "headline": "First",
            "url": "https://example.com/1",
            "datePublished": "2024-01-01T08:00:00Z"
        },
        {
            "@id": "p2",
            "headline": "Second",
            "url": "https://example.com/2",
            "datePublished": "2024-01-01T09:00:00Z"
        }
    ],
    "liveBlogUpdate": [
        {
            "@id": "p0",
            "headline": "Ignored",
            "url": "https://example.com/0",
            "datePublished": "2023-12-31T23:00:00Z"
        }
    ]
}

HTML_SNIPPET = f"""
<html>
<head>
<script type='application/ld+json'>
{json.dumps(LD_JSON)}
</script>
</head>
<body></body>
</html>
"""

REL_LD_JSON = {
    "@context": "https://schema.org",
    "@type": "LiveBlogPosting",
    "blogPosts": [
        {
            "@id": "r1",
            "headline": "Relative",
            "url": "/article/rel1",
            "datePublished": "2024-01-01T12:00:00Z",
        }
    ],
}

REL_SNIPPET = f"""
<html>
<head>
<script type='application/ld+json'>
{json.dumps(REL_LD_JSON)}
</script>
</head>
<body></body>
</html>
"""


def test_parse_live_page_chronological(monkeypatch):
    def mock_fetch(url, timeout=15, retries=3, backoff=3):
        return HTML_SNIPPET

    monkeypatch.setattr(apnewslivebot, "fetch", mock_fetch)
    apnewslivebot.sent_post_ids.clear()

    posts = apnewslivebot.parse_live_page("topic", "https://example.com/live")
    titles = [p[1] for p in posts]

    assert titles == ["First", "Second", "Third"]


def test_parse_live_page_relative_urls(monkeypatch):
    def mock_fetch(url, timeout=15, retries=3, backoff=3):
        return REL_SNIPPET

    monkeypatch.setattr(apnewslivebot, "fetch", mock_fetch)
    apnewslivebot.sent_post_ids.clear()

    posts = apnewslivebot.parse_live_page("topic", "https://example.com/live")

    assert len(posts) == 1
    assert posts[0][2] == apnewslivebot.HOMEPAGE_URL + "/article/rel1"
