import json
import re
from typing import List

from openai import OpenAI

HASHTAG_RE = re.compile(r"^#[A-Za-z0-9]{2,40}$")


def llm_hashtags(title: str, topic: str, when: str, url: str) -> List[str]:
    prompt = (
        f"title: {title}\n"
        f"topic: {topic}\n"
        f"when: {when}\n"
        f"url: {url}"
    )
    client = OpenAI()
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.2,
        messages=[{"role": "user", "content": prompt}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "return_hashtags",
                    "description": "Return 2-6 hashtags for the post",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "hashtags": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "pattern": "^#[A-Za-z0-9]{2,40}$",
                                },
                                "minItems": 2,
                                "maxItems": 6,
                            }
                        },
                        "required": ["hashtags"],
                    },
                },
                "strict": True,
            }
        ],
        tool_choice={"type": "function", "function": {"name": "return_hashtags"}},
    )
    try:
        tool_args = resp.choices[0].message.tool_calls[0].function.arguments
        data = json.loads(tool_args)
        tags = data.get("hashtags")
        if (
            isinstance(tags, list)
            and 2 <= len(tags) <= 6
            and all(isinstance(t, str) and HASHTAG_RE.fullmatch(t) for t in tags)
        ):
            seen = set()
            unique = []
            for t in tags:
                if t not in seen:
                    unique.append(t)
                    seen.add(t)
            return unique
    except Exception:
        return []
    return []
