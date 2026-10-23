import json

import pytest

from research.local_codegen import proposed_files


def test_exact_original_edits_are_validated_before_replacement():
    original = {"src/module.py": "left = 1\nright = 2\n"}
    result = proposed_files(json.dumps({"edits": [
        {"path": "src/module.py", "old": "left = 1", "new": "left = 100"},
        {"path": "src/module.py", "old": "right = 2", "new": "right = 200"}]}), original)
    assert result["src/module.py"] == "left = 100\nright = 200\n"
    assert original["src/module.py"] == "left = 1\nright = 2\n"


@pytest.mark.parametrize("edit", [
    {"path": "../outside.py", "old": "x", "new": "y"},
    {"path": "tests/test_code.py", "old": "x", "new": "y"},
    {"path": "src/module.py", "old": "", "new": "y"},
    {"path": "src/module.py", "old": "missing", "new": "y"},
    {"path": "src/module.py", "old": "x", "new": "y"},
])
def test_external_draft_cannot_escape_or_guess_source(edit):
    with pytest.raises(ValueError):
        proposed_files(json.dumps({"edits": [edit]}), {"src/module.py": "xx"})


def test_overlapping_original_spans_are_rejected():
    with pytest.raises(ValueError):
        proposed_files(json.dumps({"edits": [
            {"path": "src/module.py", "old": "abcd", "new": "x"},
            {"path": "src/module.py", "old": "cdef", "new": "y"}]}), {"src/module.py": "abcdef"})
