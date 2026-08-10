"""씨트립 어댑터의 브라우저 없는 로직 스모크 테스트.

navigate/DOM 은 실브라우저가 필요하므로, 순수 파싱·병합 로직만 검증한다.
"""

import pytest

from crawler.adapters.ctrip import CtripAdapter, _find_dict_with_keys


@pytest.fixture
def adapter():
    # session 은 parse/추출 로직에서 쓰이지 않으므로 None 으로 충분
    return CtripAdapter(session=None)


def test_parse_article_id(adapter):
    url = "https://m.ctrip.com/webapp/you/community/detail?articleId=266207894"
    assert adapter.parse_article_id(url) == "266207894"


def test_parse_article_id_with_extra_params(adapter):
    url = "https://m.ctrip.com/x?foo=bar&articleId=123&baz=qux"
    assert adapter.parse_article_id(url) == "123"


def test_parse_article_id_missing_raises(adapter):
    with pytest.raises(ValueError):
        adapter.parse_article_id("https://m.ctrip.com/x?noId=1")


def test_parse_captured_extracts_comment_total(adapter):
    captured = {"comment_list": '{"totalCount": 6, "comments": []}'}
    parsed = adapter._parse_captured(captured)
    assert parsed["comments"] == 6


def test_parse_captured_ignores_malformed_json(adapter):
    captured = {"comment_list": "not json"}
    assert adapter._parse_captured(captured) == {}


def test_extract_detail_counts_from_nested_json(adapter):
    body = (
        '{"data": {"article": {"likeCount": 38, "collectCount": 14, '
        '"readCount": 999, "commentCount": 6, "shareCount": 2, '
        '"title": "제주 여행기", "author": "여행자"}}}'
    )
    out = adapter._extract_detail_counts(body)
    assert out["likes"] == 38
    assert out["collects"] == 14
    assert out["views"] == 999
    assert out["comments"] == 6
    assert out["shares"] == 2
    assert out["title"] == "제주 여행기"
    assert out["author"] == "여행자"


def test_extract_detail_counts_returns_empty_without_count_keys(adapter):
    body = '{"data": {"unrelated": 1}}'
    assert adapter._extract_detail_counts(body) == {}


def test_find_dict_with_keys_bfs():
    obj = {"a": [1, {"likeCount": 5, "collectCount": 2}], "b": 3}
    found = _find_dict_with_keys(obj, ("likeCount", "collectCount"))
    assert found["likeCount"] == 5


def test_find_dict_with_keys_returns_none_when_absent():
    assert _find_dict_with_keys({"a": 1}, ("likeCount",)) is None


def test_build_metrics_prefers_xhr_over_dom(adapter):
    dom = {"likes": 10, "collects": 20}
    xhr = {"likes": 38, "collects": 14, "comments": 6, "title": "제목"}
    m = adapter._build_metrics("123", "https://x", "페이지제목", dom, xhr, {"comment_list": "..."})
    assert m.likes == 38  # XHR 우선
    assert m.collects == 14
    assert m.comments == 6
    assert m.title == "제목"
    assert m.article_id == "123"


def test_build_metrics_falls_back_to_dom(adapter):
    """XHR 에 좋아요가 없으면 DOM 값을 쓴다 (안티봇 부분 차단 시나리오)."""
    dom = {"likes": 38, "collects": 14}
    xhr = {"comments": 6}  # 상세 XHR 차단, 댓글만 확보
    m = adapter._build_metrics("123", "https://x", "제목", dom, xhr, {})
    assert m.likes == 38
    assert m.collects == 14
    assert m.comments == 6
    assert m.views is None


def test_build_metrics_all_none_when_blocked(adapter):
    """상세·DOM 모두 막히면 지표는 None, raw 에 진단 정보."""
    m = adapter._build_metrics("123", "https://x", "哎呀，出错啦", {}, {}, {})
    assert m.likes is None
    assert m.collects is None
    assert m.comments is None
    assert "哎呀" in m.raw
