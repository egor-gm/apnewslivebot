import os
import json
import logging
from typing import List, Optional

from upstash_redis import Redis

APP_ENV = os.environ.get("APP_ENV", "staging")
KEY_PREFIX = os.environ.get("KEY_PREFIX", "stg")

redis: Optional[Redis]
try:
    redis = Redis.from_env()
    logging.info(
        f"Using Upstash Redis for state storage (env={APP_ENV}, prefix={KEY_PREFIX})"
    )
except Exception as e:  # pragma: no cover - best effort init
    logging.warning(f"Redis init failed: {e}")
    redis = None


def k(suffix: str) -> str:
    """Namespace Redis keys."""
    return f"{KEY_PREFIX}:{suffix}"


def acquire_lock(story_key: str, ttl: int = 30) -> bool:
    """Attempt to acquire a lock for the given story key."""
    if not redis:
        return True
    try:
        return bool(redis.set(k(f"lock:{story_key}"), "1", ex=ttl, nx=True))
    except Exception as e:  # pragma: no cover
        logging.warning(f"Could not acquire lock {story_key}: {e}")
        return False


def release_lock(story_key: str) -> None:
    """Release a previously acquired lock."""
    if not redis:
        return
    try:
        redis.delete(k(f"lock:{story_key}"))
    except Exception as e:  # pragma: no cover
        logging.warning(f"Could not release lock {story_key}: {e}")


def get_recent(n: int) -> List[str]:
    """Return the most recent bodies."""
    if not redis:
        return []
    try:
        return redis.lrange(k("recent_bodies"), 0, n - 1) or []
    except Exception as e:  # pragma: no cover
        logging.warning(f"Could not fetch recent bodies: {e}")
        return []


def stage_pending(story_key: str, body: str, hashtags: List[str]) -> None:
    """Store a pending post and track recent bodies."""
    if not redis:
        return
    try:
        redis.hset(
            k(f"post:{story_key}"),
            {"body": body, "hashtags": json.dumps(hashtags)},
        )
        redis.lpush(k("recent_bodies"), body)
        redis.ltrim(k("recent_bodies"), 0, 4)
    except Exception as e:  # pragma: no cover
        logging.warning(f"Could not stage pending post {story_key}: {e}")


def finalize_sent(story_key: str, body: str) -> None:
    """Mark a story as posted and keep recent list trimmed."""
    if not redis:
        return
    try:
        redis.sadd(k("posted_ids"), story_key)
        redis.ltrim(k("recent_bodies"), 0, 4)
    except Exception as e:  # pragma: no cover
        logging.warning(f"Could not finalize post {story_key}: {e}")
