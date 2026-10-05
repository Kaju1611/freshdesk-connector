import pytest
from common import ValidationError
from app.query_builder import build_search_query as b


def test_combinations():
    assert b(status=[2]) == "status:2"
    assert b(status=[2, 3], tag="vip") == "(status:2 OR status:3) AND tag:'vip'"
    assert b(created_after="2026-01-01", created_before="2026-02-01") == \
        "created_at:>'2026-01-01' AND created_at:<'2026-02-01'"
    assert b(query="tag:'a' OR tag:'b'", priority=[4]) == "(tag:'a' OR tag:'b') AND priority:4"


@pytest.mark.parametrize("kw", [
    {}, {"query": '"x"'}, {"tag": "a'b"}, {"created_after": "yesterday"}, {"query": "x" * 600},
    {"status": [2], "type": "x'; DROP"},
])
def test_rejects_bad_input(kw):
    with pytest.raises(ValidationError):
        b(**kw)
