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
