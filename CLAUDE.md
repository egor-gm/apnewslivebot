# CLAUDE.md - AI Assistant Guide for AP News Live Bot

## Project Overview

**AP News Live Bot** is a Python-based Telegram bot that monitors the AP News website for live blog posts and relays them to a Telegram channel. The bot scrapes the AP News homepage, identifies active "LIVE:" topics, extracts individual posts from live blog pages, and sends formatted updates to Telegram with proper deduplication and permalink resolution.

### Key Characteristics
- **Language**: Python 3.8+
- **Primary Function**: Web scraping + Telegram notifications
- **Deployment Model**: Can run as single instance or distributed with Redis-based leader lock
- **State Management**: Dual persistence (local JSON file + optional Redis)
- **Scraping Strategy**: Cloudflare-aware scraping using cloudscraper + BeautifulSoup
- **Data Extraction**: JSON-LD structured data parsing with multiple fallback strategies

---

## Repository Structure

```
apnewslivebot/
├── apnewslivebot.py           # Main bot logic (~927 lines)
├── leader_lock.py             # Redis-based leader election for distributed deployments
├── requirements.txt           # Python dependencies
├── sent.json                  # Local persistence of sent post IDs (auto-generated)
├── README.md                  # User-facing documentation
└── tests/                     # Unit tests
    ├── test_main.py           # Integration tests for main loop
    ├── test_parse_live_page.py # Live page parsing tests
    ├── test_format_message.py  # Message formatting tests
    ├── test_delay.py          # Delay calculation tests
    └── ap_homepage.html       # Test fixture
```

---

## Architecture & Data Flow

### 1. Main Loop (`apnewslivebot.py:main()`)

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Load state from Redis/file (sent_post_ids, sent_links)  │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. Scrape homepage → get_live_topics() → dict[name, url]   │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. For each topic: parse_live_page() → list[(id,title,...)]│
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. Deduplication checks:                                    │
│    - Skip if post_id in sent_post_ids                       │
│    - Skip if title is >80% similar to recent posts (topic)  │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. format_message() → send_telegram_message()              │
│    - Add to sent_post_ids, sent_links                       │
│    - Remember title for similarity checks                   │
│    - save_sent() to persist state                           │
└─────────────────────────────────────────────────────────────┘
```

### 2. Adaptive Interval Logic
- **Normal mode**: Check every 40s (default `CHECK_INTERVAL_SECONDS`)
- **Long mode**: After 1 hour with no live topics, switch to 300s (`LONG_CHECK_INTERVAL_SECONDS`)
- **Purpose**: Reduce API/scraping load when no live coverage is active

### 3. Leader Lock (Distributed Mode)
- **File**: `leader_lock.py`
- **Mechanism**: Redis SET with NX (only if not exists) + TTL
- **Renewal**: Every 15s (default `LEADER_LOCK_RENEW`)
- **Use Case**: Multiple bot instances can run; only the leader executes the loop
- **Graceful Shutdown**: SIGINT/SIGTERM handlers release the lock before exit

---

## Core Components

### `apnewslivebot.py` Key Functions

#### Web Scraping
- **`fetch(url, timeout, retries, backoff)`** (line 135)
  - Uses `cloudscraper` to bypass Cloudflare protections
  - Retries on 403 by recreating scraper session
  - Exponential backoff on failures

- **`get_live_topics(html)`** (line 168)
  - Returns `dict[topic_name, url]` for all "LIVE:" topics on homepage
  - Two strategies: text node containing "live:" + following anchor, or anchors starting with "LIVE:"

- **`parse_live_page(topic_name, url, html)`** (line 438)
  - Extracts live blog posts from JSON-LD structured data
  - Returns `list[(post_id, title, permalink, timestamp)]`
  - Supports multiple JSON-LD formats: `blogPosts`, `liveBlogUpdate`, `updates`, `@graph` arrays

#### Permalink Resolution (Critical)
- **`resolve_post_permalink()`** (line 379)
  - **Problem**: JSON-LD post IDs don't always match DOM anchor fragments
  - **Solution**: 5-stage fallback strategy
    1. Use JSON-LD `post.url` if it contains `#fragment`
    2. Match via `<bsp-copy-link>` data-link mapping
    3. Match headline text → `<bsp-liveblog-post data-post-id>`
    4. Match timestamp → nearest `<bsp-liveblog-post data-posted-date-timestamp>`
    5. Fallback to base URL (no fragment)

- **`_build_article_index()`** (line 305)
  - Maps normalized headline text → post/article ID
  - Supports both `<bsp-liveblog-post>` (AP live blogs) and generic `<article>` blocks

- **`_find_article_id_by_time()`** (line 343)
  - Time-based matching with 12-hour tolerance
  - Prefers `<bsp-liveblog-post data-posted-date-timestamp>` over generic `<time>` tags

#### Deduplication
- **`check_recent_post_similarity(topic, title)`** (line 242)
  - Uses `difflib.SequenceMatcher` for string similarity (0.0-1.0 ratio)
  - Default threshold: 80% (`DEDUP_SIMILARITY_THRESHOLD`)
  - Tracks last 20 titles per topic (`DEDUP_RECENT_PER_TOPIC`)
  - **Important**: Replaced LLM-based deduplication in commit `3f8498a`

- **`_norm_text(s)`** (line 216)
  - Normalizes text for comparison: NFKC normalization, lowercase, whitespace collapse
  - Converts curly apostrophes to straight quotes

#### Telegram Integration
- **`send_telegram_message(text)`** (line 614)
  - Fallback strategy: Try with `TELEGRAM_PARSE_MODE`, fallback to plain text on 400 errors
  - Respects `DISABLE_WEB_PAGE_PREVIEW`, `DISABLE_NOTIFICATION`
  - Supports `DRY_RUN` mode for testing

- **`format_message(topic, title, url, timestamp)`** (line 652)
  - Converts ISO timestamp → local timezone (default: Europe/Paris)
  - Strips HTML tags from title
  - Format: `<clean_title>\n\n📰 <topic> - <date> <tz>\n\n<url>`

#### State Persistence
- **`load_sent()`** (line 83)
  - Tries Redis first (`{PREFIX}:sent_links`, `{PREFIX}:sent_post_ids` sets)
  - Falls back to local `sent.json` file
  - Supports legacy format (list of links only)

- **`save_sent()`** (line 113)
  - Persists to both Redis and local file for redundancy
  - Uses two separate calls for Redis compatibility

### `leader_lock.py` Functions

- **`run_with_lock(loop_once)`** (line 26)
  - Wraps a single-iteration function in leader election logic
  - Acquires lock with `SET key pid NX EX ttl`
  - Renews every 15s by checking `GET key == pid` then `EXPIRE key ttl`
  - Releases lock on SIGINT/SIGTERM

---

## Environment Variables

### Required
| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Telegram bot API token |
| `TELEGRAM_CHANNEL_ID` | Target channel (e.g., `@mychannel` or numeric ID) |

### Redis (Optional, for distributed mode)
| Variable | Description |
|----------|-------------|
| `UPSTASH_REDIS_REST_URL` | Redis REST endpoint |
| `UPSTASH_REDIS_REST_TOKEN` | Redis auth token |

### Timing & Intervals
| Variable | Default | Description |
|----------|---------|-------------|
| `CHECK_INTERVAL_SECONDS` | 40 | Normal scan interval |
| `LONG_CHECK_INTERVAL_SECONDS` | 300 | Interval when no topics found |
| `NO_TOPICS_THRESHOLD_SECONDS` | 3600 | Time before switching to long interval |

### Deduplication
| Variable | Default | Description |
|----------|---------|-------------|
| `DEDUP_SIMILARITY_THRESHOLD` | 0.8 | Similarity ratio (0.0-1.0) to consider duplicates |
| `DEDUP_RECENT_PER_TOPIC` | 20 | Number of recent titles to track per topic |

### Telegram Formatting
| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_PARSE_MODE` | "" (plain) | "MarkdownV2", "HTML", or "" |
| `DISABLE_WEB_PAGE_PREVIEW` | true | Disable link previews |
| `DISABLE_NOTIFICATION` | false | Send silently |
| `TIMEZONE` | Europe/Paris | Timezone for timestamps |

### Leader Lock
| Variable | Default | Description |
|----------|---------|-------------|
| `LEADER_LOCK_KEY` | apnewsbot:leader | Redis key for lock |
| `LEADER_LOCK_TTL` | 45 | Lock TTL in seconds |
| `LEADER_LOCK_RENEW` | 15 | Renewal interval in seconds |

### Debugging
| Variable | Default | Description |
|----------|---------|-------------|
| `DRY_RUN` | false | Don't send to Telegram, log instead |
| `SELF_TEST` | false | Run offline tests against fixtures |
| `KEY_PREFIX` | dev | Redis key prefix for multi-environment support |

---

## Development Workflows

### Running Locally
```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set environment variables
export TELEGRAM_BOT_TOKEN="your_token"
export TELEGRAM_CHANNEL_ID="@yourchannel"

# 3. Optional: Enable dry run for testing
export DRY_RUN="true"

# 4. Run the bot
python apnewslivebot.py
```

### Running Tests
```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_parse_live_page.py

# Run with verbose output
pytest -v
```

### Self-Test Mode
```bash
# Run built-in tests against fixtures (no API calls)
export SELF_TEST="true"
export DRY_RUN="true"
python apnewslivebot.py
```

### Distributed Deployment with Leader Lock
```python
# main.py (example wrapper)
import leader_lock
from apnewslivebot import main

def loop_once():
    # Your single iteration logic here
    # (Would require refactoring main() to extract one iteration)
    pass

if __name__ == "__main__":
    leader_lock.run_with_lock(loop_once)
```

---

## Testing Strategy

### Test Files
- **`test_main.py`**: Integration tests for deduplication logic
- **`test_parse_live_page.py`**: JSON-LD parsing, permalink resolution, edge cases
- **`test_format_message.py`**: Message formatting, timezone conversion
- **`test_delay.py`**: Interval calculation logic

### Mocking Patterns
Tests use `monkeypatch` to mock:
- `get_live_topics()` → return hardcoded topics
- `parse_live_page()` → return synthetic posts
- `send_telegram_message()` → capture messages
- `time.sleep()` → raise `SystemExit` to stop main loop

### Key Test Cases
1. **Duplicate link handling**: Same URL from different topics should send twice
2. **Similarity deduplication**: >80% similar titles should be skipped
3. **Permalink resolution**: Tests for all 5 fallback strategies
4. **JSON-LD formats**: Tests for `blogPosts`, `liveBlogUpdate`, `@graph` arrays
5. **Timestamp matching**: Tests for `<bsp-liveblog-post>` timestamps

---

## AI Assistant Guidelines

### When Modifying Code

#### 1. Permalink Resolution is Critical
- **DO NOT** simplify the multi-stage fallback in `resolve_post_permalink()`
- **Reason**: AP News uses inconsistent HTML structures; each fallback catches specific edge cases
- **Recent Fix**: Commit `a609a77` fixed bugs by improving anchor capture and avoiding fake fragments
- **Test Changes**: Always run `pytest tests/test_parse_live_page.py` after modifying this logic

#### 2. Deduplication Strategy
- **Current**: Local string similarity using `SequenceMatcher`
- **History**: Replaced LLM-based deduplication in commit `3f8498a` for cost/reliability
- **DO NOT** re-introduce external API calls for deduplication without explicit approval
- **Tuning**: Adjust `DEDUP_SIMILARITY_THRESHOLD` and `DEDUP_RECENT_PER_TOPIC` instead

#### 3. State Persistence
- **Dual Writes**: Always save to both Redis (if available) and local file
- **Reason**: Redundancy for recovery; local file survives Redis downtime
- **Key Prefix**: Use `PREFIX` env var for multi-environment Redis instances

#### 4. Error Handling Philosophy
- **Non-blocking**: Individual cycle errors should log and continue (line 916-917)
- **Graceful**: Signal handlers save state before exit (line 693-696)
- **Retries**: HTTP fetches retry 3x with exponential backoff (line 141-162)

#### 5. Scraping Fragility
- **Cloudflare**: Recreate scraper on 403 errors (line 148-150)
- **HTML Structure**: AP News changes DOM frequently; rely on JSON-LD when possible
- **Fallbacks**: Always maintain multiple parsing strategies (e.g., line 182-203 for topic detection)

### When Adding Features

#### Feature Request Checklist
1. **Does it require new environment variables?**
   - Add to top of `apnewslivebot.py` with `os.environ.get()`
   - Document in README.md and this file
   - Provide sensible defaults

2. **Does it change state persistence?**
   - Update both `load_sent()` and `save_sent()`
   - Maintain backward compatibility with existing `sent.json` format
   - Add migration logic if needed

3. **Does it affect message formatting?**
   - Update `format_message()` (line 652)
   - Handle Telegram's 4096 character limit
   - Test with `TELEGRAM_PARSE_MODE=""` and `"MarkdownV2"`

4. **Does it add new scraping logic?**
   - Use BeautifulSoup's `.find_all()` with defensive checks
   - Add try/except blocks for parsing errors
   - Write tests with HTML fixtures (see `tests/ap_homepage.html`)

### Common Pitfalls to Avoid

❌ **DON'T**: Use `git commit --amend` unless explicitly requested
❌ **DON'T**: Remove the `save_sent()` call after each message (causes data loss on crashes)
❌ **DON'T**: Make HTTP requests synchronous without retries
❌ **DON'T**: Assume JSON-LD structure is consistent (always check for multiple keys)
❌ **DON'T**: Hard-code URLs or paths (use `os.path.join()` or `os.environ`)

✅ **DO**: Test against the `SELF_TEST` fixtures before running live
✅ **DO**: Log warnings for recoverable errors, errors for cycle failures
✅ **DO**: Use `_norm_text()` for all string comparisons
✅ **DO**: Check if Redis is available before calling Redis methods
✅ **DO**: Maintain Python 3.8+ compatibility (no walrus operator in critical paths)

---

## Recent Changes & Context

### Commit History (Last 10)
- `b46f7a8`: Merge PR #26 - Rewrite dedupe checking function
- `3f8498a`: **Replace LLM dedupe check with local similarity** (major change)
- `9ac3d1c`: Merge PR #25 - Leader lock, drain switch, and prefixed dedupe keys
- `91aa685`: Add leader lock, drain switch, and prefixed dedupe keys
- `72e5df2`: Anchor fix
- `6858bf4`: Fix HTML parser error
- `8bc1a67`: Expand anchor capture for better fragment resolution
- `a609a77`: **Add resolver for exact header fragments** (fixes bugged permalinks)
- `e1e4f4f`: Refactor apnewslivebot.py for readability/maintainability
- `1193dfe`: Merge PR #16 - Refactor HTTP client for Cloudflare bypass

### Key Changes to Be Aware Of
1. **Deduplication**: Now local-only (no LLM costs), uses `difflib.SequenceMatcher`
2. **Permalink Resolution**: Multi-stage fallback to match UI "copy link" behavior
3. **Leader Lock**: Supports distributed deployments with Redis coordination
4. **Key Prefixing**: `KEY_PREFIX` env var allows dev/prod Redis separation

---

## Debugging Tips

### "Wrong permalink in Telegram message"
- **Symptom**: Link doesn't scroll to correct post on AP News
- **Cause**: Permalink resolution fallback order may be incorrect
- **Fix**: Check `resolve_post_permalink()` logic and `_build_article_index()` mapping
- **Test**: Run `pytest tests/test_parse_live_page.py -k permalink -v`

### "Duplicate posts being sent"
- **Symptom**: Same headline sent multiple times
- **Cause 1**: `sent_post_ids` not persisting (check Redis/file writes)
- **Cause 2**: Similarity threshold too high (lower `DEDUP_SIMILARITY_THRESHOLD`)
- **Debug**: Check logs for "Skipping '%s' for topic %s (similarity X%)"

### "Bot stops after 403 error"
- **Symptom**: Bot exits after Cloudflare blocks
- **Cause**: Scraper recreation not working (line 148)
- **Fix**: Check `cloudscraper` version compatibility, inspect headers
- **Workaround**: Add more user agents, increase retry backoff

### "No live topics found" (but they exist on homepage)
- **Symptom**: `get_live_topics()` returns empty dict
- **Cause**: AP News changed DOM structure
- **Fix**: Inspect homepage HTML, update line 182-203 selectors
- **Test**: Save homepage HTML to `tests/ap_homepage.html` and add test case

---

## File Reference Quick Links

### Main Logic
- Main loop: `apnewslivebot.py:850-924`
- Topic detection: `apnewslivebot.py:168-204`
- Live page parsing: `apnewslivebot.py:438-594`
- Permalink resolution: `apnewslivebot.py:379-435`
- Deduplication: `apnewslivebot.py:242-258`

### Utilities
- HTTP fetching: `apnewslivebot.py:135-163`
- Text normalization: `apnewslivebot.py:216-222`
- Message formatting: `apnewslivebot.py:652-676`
- State persistence: `apnewslivebot.py:83-130`

### Testing
- Integration tests: `tests/test_main.py:11-62`
- Parsing tests: `tests/test_parse_live_page.py` (multiple test cases)

---

## Dependencies & Versions

From `requirements.txt`:
```
requests==2.32.4         # HTTP client
aiohttp==3.9.5           # Async HTTP (not currently used, legacy?)
beautifulsoup4==4.12.3   # HTML parsing
upstash-redis==1.4.0     # Redis client for Upstash
cloudscraper==1.2.71     # Cloudflare bypass
pytest==8.2.1            # Testing framework
```

### Important Notes
- **aiohttp**: Listed but not imported in main code (consider removing if unused)
- **cloudscraper**: Critical for bypassing Cloudflare; keep version pinned
- **upstash-redis**: Uses REST API (not traditional Redis protocol)

---

## Architecture Decisions

### Why JSON-LD over DOM scraping?
- **Reliability**: JSON-LD is more stable than CSS selectors
- **Completeness**: Contains all posts even if DOM is paginated
- **Performance**: Single parse vs. traversing entire DOM tree

### Why dual persistence (Redis + file)?
- **Reliability**: Redis may be unavailable (network, quota)
- **Recovery**: Local file survives container restarts
- **Development**: Can run without Redis locally

### Why separate `leader_lock.py`?
- **Reusability**: Can be imported by other scripts
- **Testing**: Easier to test locking logic in isolation
- **Deployment**: Optional feature (not required for single-instance)

### Why adaptive intervals?
- **Cost**: Reduce API calls when no live coverage
- **Responsiveness**: Fast checks (40s) when live events are active
- **Balance**: 5min interval is still reasonable for quiet periods

---

## Future Considerations

### Potential Improvements
1. **Async HTTP**: Leverage `aiohttp` for concurrent topic fetching
2. **Metrics**: Track message send rate, error rates, topic activity
3. **Health Checks**: Expose HTTP endpoint for monitoring
4. **Configuration File**: Move env vars to YAML/TOML for easier management
5. **Database**: Migrate from JSON file to SQLite for better state management

### Known Limitations
1. **No edit support**: If AP News edits a post, we send it again (different post_id)
2. **Single channel**: Can't route different topics to different channels
3. **No media**: Only sends text; ignores images/videos from posts
4. **Timezone hardcoded**: Should support per-user timezone preferences
5. **No web UI**: All configuration via environment variables

---

## Contributing

### Before Making Changes
1. Read this entire CLAUDE.md file
2. Run `pytest` to ensure existing tests pass
3. Check recent commits for context on modified areas
4. Set `DRY_RUN=true` when testing Telegram integration

### Pull Request Guidelines
1. **Commit Messages**: Use conventional commits (feat:, fix:, chore:, etc.)
2. **Tests**: Add tests for new functionality
3. **Documentation**: Update README.md and this file
4. **Backward Compatibility**: Don't break existing `sent.json` format
5. **Environment Variables**: Document new vars in both files

### Code Style
- **Formatting**: Follow existing style (no formatter currently enforced)
- **Line Length**: Aim for 100 characters max
- **Type Hints**: Use for function signatures (already partially implemented)
- **Docstrings**: Required for complex functions (see `get_live_topics()` example)

---

## Contact & Support

- **Repository**: https://github.com/egor-gm/apnewslivebot (inferred from git log)
- **Issues**: Use GitHub Issues for bug reports
- **Questions**: Check README.md first, then open a discussion

---

**Last Updated**: 2025-11-14 (Auto-generated by Claude AI)
**Based on Commit**: `b46f7a8` (Merge pull request #26)
