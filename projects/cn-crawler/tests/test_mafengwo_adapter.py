"""마펑워 어댑터 파싱 로직 스모크 테스트 (브라우저 없이 가짜 픽스처로)."""

import json

import pytest

from crawler.adapters.mafengwo import (
    MafengwoAdapter,
    _find_counts,
    extract_metrics_from_text,
)


# --- article_id 파싱 --------------------------------------------------------

@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.mafengwo.cn/i/24867879.html", "24867879"),
        ("https://m.mafengwo.cn/i/12345.html", "12345"),
        ("https://www.mafengwo.cn/i/24867879.html?from=share", "24867879"),
        ("https://m.mafengwo.cn/nb/detail?iid=999", "999"),
    ],
)
def test_parse_article_id(url, expected):
    adapter = MafengwoAdapter.__new__(MafengwoAdapter)  # 브라우저 세션 불필요
    assert adapter.parse_article_id(url) == expected


def test_parse_article_id_rejects_invalid():
    adapter = MafengwoAdapter.__new__(MafengwoAdapter)
    with pytest.raises(ValueError):
        adapter.parse_article_id("https://www.mafengwo.cn/gonglve/")


# --- DOM 텍스트 정규식 추출 -------------------------------------------------

def test_extract_from_rendered_text():
    text = "行程 · 2026\n浏览 1.2万 · 顶 38 · 收藏 14\n共 6 条蜂评\n发表于 2026-08-01"
    out = extract_metrics_from_text(text)
    assert out["views"] == 12000
    assert out["likes"] == 38
    assert out["collects"] == 14
    assert out["comments"] == 6
    assert out["upload_date"] == "2026-08-01"


def test_extract_supports_label_before_number():
    text = "阅读: 3400  赞: 56  评论 (12)"
    out = extract_metrics_from_text(text)
    assert out["views"] == 3400
    assert out["likes"] == 56
    assert out["comments"] == 12


def test_extract_returns_empty_on_captcha_page():
    """캡차 페이지엔 지표 텍스트가 없다 → 빈 dict (None 으로 degrade)."""
    out = extract_metrics_from_text("请完成安全验证 拖动滑块")
    assert out == {}


def test_extract_normalizes_date_separators():
    out = extract_metrics_from_text("发表于 2026/08/01")
    assert out["upload_date"] == "2026-08-01"


# --- XHR JSON 카운트 키 탐색 ------------------------------------------------

def test_find_counts_in_nested_json():
    data = {
        "code": 0,
        "data": {
            "note": {"vote_num": 38, "reply_num": 6, "browse_num": "1.2万"},
            "extra": {"fav_num": 14},
        },
    }
    found = _find_counts(data)
    assert found == {"likes": 38, "comments": 6, "views": 12000, "collects": 14}


def test_find_counts_first_match_wins():
    data = [{"vote_num": 10}, {"vote_num": 99}]
    assert _find_counts(data)["likes"] == 10


def test_find_counts_ignores_non_numeric():
    assert _find_counts({"vote_num": None, "reply_num": [1]}) == {}


# --- 병합/진단 --------------------------------------------------------------

def test_build_metrics_degrades_with_captcha_diagnostics():
    metrics = MafengwoAdapter._build_metrics(
        article_id="24867879",
        url="https://www.mafengwo.cn/i/24867879.html",
        page_title="",
        dom={},
        xhr={},
        captured={"captcha": True, "json_bodies": []},
        body_text="请完成安全验证",
    )
    assert metrics.platform == "mafengwo"
    assert metrics.likes is None
    assert metrics.views is None
    raw = json.loads(metrics.raw)
    assert raw["captcha_detected"] is True


def test_build_metrics_xhr_overrides_dom():
    metrics = MafengwoAdapter._build_metrics(
        article_id="1",
        url="https://www.mafengwo.cn/i/1.html",
        page_title="어느 여행기",
        dom={"likes": 5, "views": 100, "upload_date": "2026-08-01"},
        xhr={"likes": 38},
        captured={"captcha": False, "json_bodies": ["x"]},
        body_text="",
    )
    assert metrics.likes == 38  # XHR 우선
    assert metrics.views == 100  # DOM 보조
    assert metrics.upload_date == "2026-08-01"
    assert metrics.title == "어느 여행기"
