import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dedupe import canonize, is_near_duplicate


def test_punctuation_only_edit_blocked():
    recent = ["Breaking news about economy"]
    candidate = "Breaking news about economy!!!"
    assert canonize(candidate) == canonize(recent[0])
    assert is_near_duplicate(candidate, recent)


def test_substantive_update_passes():
    recent = ["Breaking news about economy"]
    candidate = "Breaking news: economy improves drastically"
    assert canonize(candidate) != canonize(recent[0])
    assert not is_near_duplicate(candidate, recent)


def test_llm_semantic_duplicate(monkeypatch):
    recent = ["Breaking news about economy"]
    candidate = "Economy shows growth according to report"

    def fake_llm(cand, recents, model=None, timeout=8.0):
        assert cand == candidate
        assert recents == recent
        return True

    monkeypatch.setattr("dedupe._llm_same_meaning", fake_llm)
    assert is_near_duplicate(candidate, recent)
