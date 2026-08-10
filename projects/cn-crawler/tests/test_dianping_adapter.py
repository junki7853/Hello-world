"""디엔핑 어댑터 파싱 로직 스모크 테스트 (브라우저 없이 가짜 픽스처로)."""

import json

import pytest

from crawler.adapters.dianping import (
    DianpingAdapter,
    _find_counts,
    detect_walls,
    extract_metrics_from_text,
)


# --- article_id 파싱 --------------------------------------------------------

@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://m.dianping.com/ugcdetail/3099928252?sceneType=0&bizType=29", "3099928252"),
        ("https://m.dianping.com/ugcdetail/123", "123"),
        ("https://www.dianping.com/shop/H8gTDqYyzeVStDrw", "H8gTDqYyzeVStDrw"),
        ("https://www.dianping.com/detail?contentId=555", "555"),
    ],
)
def test_parse_article_id(url, expected):
    adapter = DianpingAdapter.__new__(DianpingAdapter)  # 브라우저 세션 불필요
    assert adapter.parse_article_id(url) == expected


def test_parse_article_id_rejects_invalid():
    adapter = DianpingAdapter.__new__(DianpingAdapter)
    with pytest.raises(ValueError):
        adapter.parse_article_id("https://www.dianping.com/shanghai/ch10")


# --- 차단(월) 감지 ----------------------------------------------------------

def test_detects_login_wall_redirect():
    final = "https://maccount.dianping.com/mlogin/smslogin?redir=..."
    assert detect_walls(final) == {"login_wall": True}


def test_detects_verify_wall_redirect():
    final = "https://verify.meituan.com/v2/app/general_page?requestCode=x"
    assert detect_walls(final) == {"verify_wall": True}


def test_no_wall_on_normal_url():
    assert detect_walls("https://m.dianping.com/ugcdetail/123") == {}


# --- DOM 텍스트 추출 --------------------------------------------------------

def test_extract_from_rendered_text():
    text = "点赞 38\n收藏 14\n评论 (6)\n浏览 1.2万"
    out = extract_metrics_from_text(text)
    assert out == {"likes": 38, "collects": 14, "comments": 6, "views": 12000}


def test_extract_skips_pua_obfuscated_numbers():
    """PUA 글리프 숫자는 \\d 에 안 걸린다 → 잘못된 값 대신 누락."""
    text = "点赞   收藏 "
    assert extract_metrics_from_text(text) == {}


# --- XHR JSON 카운트 키 탐색 ------------------------------------------------

def test_find_counts_in_nested_json():
    data = {
        "code": 200,
        "result": {
            "note": {"likeCount": 38, "commentCount": 6, "viewCount": "1.2万"},
            "author": {"followerCount": 345},
        },
    }
    found = _find_counts(data)
    assert found == {"likes": 38, "comments": 6, "views": 12000, "followers": 345}


def test_find_counts_ignores_non_numeric():
    assert _find_counts({"likeCount": None, "viewCount": {"x": 1}}) == {}


# --- 병합/진단 --------------------------------------------------------------

def _build(dom=None, xhr=None, walls=None, body_text=""):
    return DianpingAdapter._build_metrics(
        article_id="3099928252",
        url="https://m.dianping.com/ugcdetail/3099928252",
        final_url="https://m.dianping.com/ugcdetail/3099928252",
        page_title="테스트",
        dom=dom or {},
        xhr=xhr or {},
        captured={"json_bodies": []},
        body_text=body_text,
        walls=walls or {},
    )


def test_build_metrics_degrades_on_login_wall():
    m = _build(walls={"login_wall": True})
    assert m.likes is None
    raw = json.loads(m.raw)
    assert raw["login_wall"] is True
    assert raw["font_obfuscation_detected"] is False


def test_build_metrics_records_font_obfuscation():
    m = _build(body_text="点赞 ")
    assert json.loads(m.raw)["font_obfuscation_detected"] is True


def test_build_metrics_xhr_overrides_dom_and_maps_followers():
    m = _build(dom={"likes": 5}, xhr={"likes": 38, "followers": 345})
    assert m.likes == 38
    assert m.followers == 345
    assert m.platform == "dianping"
