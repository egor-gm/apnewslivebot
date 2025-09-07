import re
import string
from html import unescape
from typing import Iterable

from rapidfuzz import fuzz


def canonize(text: str) -> str:
    """Normalize text for duplicate detection."""
    text = unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)  # strip HTML tags
    text = text.lower()
    text = re.sub(rf"[{re.escape(string.punctuation)}]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def is_near_duplicate(candidate: str, recent_list: Iterable[str], threshold: int = 92) -> bool:
    """Return True if candidate is a near-duplicate of any text in recent_list."""
    cand = canonize(candidate)
    for recent in recent_list:
        score = fuzz.token_set_ratio(cand, canonize(recent))
        if score >= threshold:
            return True
    return False
