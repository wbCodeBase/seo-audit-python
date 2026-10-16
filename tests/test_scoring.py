import pytest

from geo.scoring import classify_intent, visibility_score


@pytest.mark.parametrize("rank,grounded,cited,expected", [
    (1, True, False, 100),
    (1, True, True, 100),     # +10 capped at 100
    (2, True, False, 88),
    (2, True, True, 98),
    (5, True, False, 52),
    (8, True, False, 16),
    (9, True, False, 5),      # 100 - 96 = 4 → floor 5
    (20, True, False, 5),
    (20, True, True, 15),
    (None, True, False, 0),   # searched, not found
    (0, True, True, 0),
    (3, False, True, None),   # never searched → no score at all
    (None, False, False, None),
])
def test_visibility_score_rule_is_unchanged(rank, grounded, cited, expected):
    assert visibility_score(rank, grounded, cited) == expected


@pytest.mark.parametrize("query,intent", [
    ("netsuite implementation partners in Noida", "local"),
    ("erp consultants near me", "local"),
    ("what is netsuite", "informational"),
    ("how to migrate erp data", "informational"),
    ("best erp for small business", "comparison"),
    ("netsuite vs sap", "comparison"),
    ("netsuite implementation partners", "transactional"),
    ("erp pricing", "transactional"),
    ("netsuite", "informational"),
])
def test_classify_intent(query, intent):
    assert classify_intent(query) == intent
