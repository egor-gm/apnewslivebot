import os
import re
import string
from html import unescape
from typing import Iterable

from rapidfuzz import fuzz
from openai import OpenAI


def canonize(text: str) -> str:
    """Normalize text for duplicate detection."""
    text = unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)  # strip HTML tags
    text = text.lower()
    text = re.sub(rf"[{re.escape(string.punctuation)}]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _llm_same_meaning(candidate: str, recent_list: Iterable[str], *, model: str | None = None, timeout: float = 8.0) -> bool:
    """Return True if an LLM judges candidate to convey the same info as any recent messages."""
    if not os.environ.get("OPENAI_API_KEY"):
        return False

    client = OpenAI()
    model = model or os.environ.get("DEDUPE_MODEL", "gpt-4.1-mini")

    recent_text = "\n".join(f"- {r}" for r in recent_list)
    messages = [
        {
            "role": "system",
            "content": (
                "Determine if the new post is essentially the same as any of the previous posts. "
                "Respond only with 'yes' or 'no'."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Previous posts:\n{recent_text}\n\nNew post:\n{candidate}\n"
                "Is the new post about the same news as any of the previous posts?"
            ),
        },
    ]

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
            max_tokens=1,
            timeout=timeout,
        )
        answer = (resp.choices[0].message.content or "").strip().lower()
        return answer.startswith("y")
    except Exception:
        return False


def is_near_duplicate(
    candidate: str,
    recent_list: Iterable[str],
    threshold: int = 90,
    *,
    model: str | None = None,
    timeout: float = 8.0,
) -> bool:
    """Return True if candidate is a near-duplicate.

    First, use RapidFuzz to check if any of the recent messages are >= ``threshold``
    similar. If not, fall back to an LLM to judge semantic equivalence.
    """
    recent_list = list(recent_list)
    cand = canonize(candidate)
    for recent in recent_list:
        score = fuzz.token_set_ratio(cand, canonize(recent))
        if score >= threshold:
            return True

    if not recent_list:
        return False

    return _llm_same_meaning(candidate, recent_list, model=model, timeout=timeout)
