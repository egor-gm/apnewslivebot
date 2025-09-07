# hashtags.py
# Deterministic hashtag generator with a tiny, editable taxonomy.
# Primary goals:
# - 2..6 tags, readable CamelCase, no emojis, no punctuation.
# - Deterministic for a given story_id.
# - Canonical mapping via hashtags.yaml; synonyms collapse to the same tag.
# - Zero external calls; optional spaCy for better entities if available.

from __future__ import annotations
import os, re, collections, json
from typing import List, Dict, Optional, Tuple

try:
    import yaml  # pip install pyyaml
except Exception:
    yaml = None

try:
    import spacy  # optional quality boost
    _NLP = spacy.load("en_core_web_sm")
except Exception:
    _NLP = None

STOP = set("""
a an the and or but for nor is are was were be been being of to in on at by as with from up down out over under
this that these those it its they them he she her his we us you your i me my our their not no
""".split())

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-\.’']+")
_WS = re.compile(r"\s+")

def _norm(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"\s+", " ", s)
    return s

def _camelcase(phrase: str) -> str:
    tokens = re.split(r"[^A-Za-z0-9]+", phrase.strip())
    tokens = [t for t in tokens if t]
    return "".join(t[:1].upper() + t[1:] for t in tokens)

def load_config(path: str = "hashtags.yaml") -> Dict:
    cfg = {"canonical": [], "synonyms": {}, "blocked": [], "always": []}
    if yaml and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        for k in cfg:
            if k in loaded:
                cfg[k] = loaded[k]
    cfg["_canon_set"] = set(_norm(x) for x in cfg["canonical"])
    cfg["synonyms"] = {_norm(k): v for k, v in cfg["synonyms"].items()}
    cfg["_blocked_set"] = set(_norm(x) for x in cfg["blocked"])
    return cfg

def _extract_candidates_spacy(text: str) -> List[str]:
    doc = _NLP(text)
    spans = [ent.text for ent in doc.ents if ent.label_ in {"PERSON","ORG","GPE","LOC","EVENT","PRODUCT","LAW"}]
    spans += [nc.text for nc in doc.noun_chunks]
    out = []
    for s in spans:
        s = s.strip(" .,:;!?\"'()[]{}").lower()
        if not s or s in STOP or s.isdigit():
            continue
        out.append(s)
    return out

def _extract_candidates_fallback(text: str) -> List[str]:
    # frequency‑based unigrams + bigrams
    words = [w.lower() for w in _WORD.findall(text)]
    words = [w for w in words if w not in STOP and len(w) > 2 and not w.isdigit()]
    counts = collections.Counter(words)
    uni = [w for w, _ in counts.most_common(20)]
    toks = [t for t in re.split(r"[^A-Za-z0-9]+", text.lower()) if t]
    bigrams = collections.Counter(zip(toks, toks[1:]))
    bi = [" ".join(b) for b, _ in bigrams.most_common(20)]
    return uni + bi

def extract_candidates(text: str) -> List[str]:
    if _NLP:
        c = _extract_candidates_spacy(text)
    else:
        c = _extract_candidates_fallback(text)
    seen, out = set(), []
    for x in c:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out

def _map_to_canonical(span: str, cfg: Dict) -> Optional[str]:
    s = _norm(span)
    if s in cfg["_blocked_set"]:
        return None
    if s in cfg["synonyms"]:
        return cfg["synonyms"][s]
    if s in cfg["_canon_set"]:
        for c in cfg["canonical"]:
            if _norm(c) == s:
                return c
    if s in {"us","u.s","u.s.","united states"}:
        return "US"
    if len(s) <= 2:
        return None
    if any(ch.isalpha() for ch in s):
        return _camelcase(s)
    return None

def rank_hashtags(cands: List[str], cfg: Dict) -> List[Tuple[str,float]]:
    scores = {}
    for i, span in enumerate(cands):
        tag = _map_to_canonical(span, cfg)
        if not tag:
            continue
        base = 3.0 if _norm(tag) in cfg["_canon_set"] else 1.0
        pos = max(0.0, 1.5 - i*0.03)
        multi = 0.5 if " " in span else 0.0
        score = base + pos + multi
        # keep the best score per tag
        if scores.get(tag, -1) < score:
            scores[tag] = score
    for t in cfg.get("always", []):
        scores.setdefault(t, 0.2)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return ranked

def _format_hashtag(tag: str) -> Optional[str]:
    t = tag.strip().replace("#","")
    if not t:
        return None
    t = re.sub(r"[^A-Za-z0-9]", "", t)
    if not t or t.isdigit():
        return None
    if len(t) > 40:
        t = t[:40]
    return "#" + _camelcase(t)

def generate_hashtags(
    title: str,
    text: str,
    cfg_path: str = "hashtags.yaml",
    max_tags: int = 6,
    min_tags: int = 2,
    story_id: Optional[str] = None
) -> List[str]:
    cfg = load_config(cfg_path)
    seed_bias = (sum(ord(c) for c in story_id) % 17) if story_id else 0
    cands = extract_candidates(f"{title}\n{text}")
    ranked = rank_hashtags(cands, cfg)
    ranked = sorted(ranked, key=lambda kv: (-kv[1], (sum(ord(ch) for ch in kv[0]) + seed_bias)))
    tags = []
    for tag, _ in ranked:
        h = _format_hashtag(tag)
        if h and h not in tags:
            tags.append(h)
        if len(tags) >= max_tags:
            break
    # ensure "always" tags at the end
    for t in cfg.get("always", []):
        h = _format_hashtag(t)
        if h and h not in tags and len(tags) < max_tags:
            tags.append(h)
    # if still short, return what we have (deterministic) but never < min_tags unless empty text
    return tags[:max_tags]