import os
import json
import re
from typing import List

from openai import OpenAI

HASHTAG_RE = re.compile(r"^#[A-Za-z0-9]{2,40}$")

SYSTEM_TEXT = (
    "You generate 2-6 topical, readable hashtags for AP-style news posts. "
    "Use UpperCamelCase. No emojis, no spaces, no punctuation besides '#'. "
    "Prefer entities and locations from the text. Keep them concise. "
    "If nothing obvious, return fewer tags. Do not invent facts."
)

def _unique_preserve_order(tags: List[str]) -> List[str]:
    seen = set()
    out = []
    for t in tags:
        if t not in seen:
            out.append(t)
            seen.add(t)
    return out

def llm_hashtags(title: str, topic: str, when: str, url: str, *, model: str | None = None, timeout: float = 8.0) -> List[str]:
    """
    Ask the OpenAI model to return 2-6 hashtags using Structured Outputs with a strict schema.
    Returns an empty list on any error so the caller can fall back to the deterministic topic tag.
    """
    client = OpenAI()
    model = model or os.environ.get("HASHTAG_MODEL", "gpt-4.1-mini")

    messages = [
        {"role": "system", "content": SYSTEM_TEXT},
        {"role": "user", "content": f"Title: {title}\nTopicLine: {topic}\nWhen: {when}\nURL: {url}"},
    ]

    tools = [
        {
            "type": "function",
            "function": {
                "name": "return_hashtags",
                "description": "Return 2-6 concise hashtags for a news post.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "hashtags": {
                            "type": "array",
                            "minItems": 2,
                            "maxItems": 6,
                            "items": {"type": "string", "pattern": "^#[A-Za-z0-9]{2,40}$"},
                        }
                    },
                    "required": ["hashtags"],
                    "additionalProperties": False,
                },
            },
        }
    ]

    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=0.2,
            messages=messages,
            tools=tools,
            tool_choice={"type": "function", "function": {"name": "return_hashtags"}},
            parallel_tool_calls=False,
            timeout=timeout,
        )
        tool_args = resp.choices[0].message.tool_calls[0].function.arguments
        data = json.loads(tool_args)
        tags = data.get("hashtags")
        if not isinstance(tags, list):
            return []
        tags = [t for t in tags if isinstance(t, str) and HASHTAG_RE.fullmatch(t)]
        tags = _unique_preserve_order(tags)
        return tags[:6]
    except Exception:
        return []