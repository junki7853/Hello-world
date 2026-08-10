"""마펑워 어댑터 파싱 로직 스모크 테스트 (브라우저 없이 가짜 픽스처로)."""

import json
from contextlib import contextmanager

import pytest

from crawler.adapters.base import find_counts
from crawler.adapters.mafengwo import MafengwoAdapter, extract_metrics_from_text


# --- article_id 파싱 --------------------------------------------------------

@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.mafengwo.cn/i/24867879.html", "24867879"),
        ("https://m.mafengwo.cn/i/12345.html", "12345"),
        ("https://www.mafengwo.cn/i/24867879.html?from=share", "24867879"),
        ("https://m.mafengwo.cn/nb/detail?iid=999", "999"),
        # 실사용 시트에서 관측된 모바일 웽 상세 URL 형 (쿼리 id 는 weng 경로만 인정)
        (
            "https://m.mafengwo.cn/mweng/wengdetailssr/weng?id=1776024644987370",
            "1776024644987370",
        ),
        ("https://m.mafengwo.cn/mweng/wengdetailssr/weng?id=42&from=share", "42"),
    ],
)
def test_parse_article_id(url, expected):
    adapter = MafengwoAdapter.__new__(MafengwoAdapter)  # 브라우저 세션 불필요
    assert adapter.parse_article_id(url) == expected


def test_parse_article_id_rejects_invalid():
    adapter = MafengwoAdapter.__new__(MafengwoAdapter)
    with pytest.raises(ValueError):
        adapter.parse_article_id("https://www.mafengwo.cn/gonglve/")


def test_query_id_only_accepted_on_weng_path():
    """weng 경로가 아닌 URL 의 광범위한 id 쿼리는 게시물 id 로 받지 않는다."""
    adapter = MafengwoAdapter.__new__(MafengwoAdapter)
    with pytest.raises(ValueError):
        adapter.parse_article_id("https://www.mafengwo.cn/search?id=123")


# --- 웽 액션바 (라벨 없는 숫자 → item_name 매핑) -----------------------------

class _FakeButton:
    def __init__(self, params, text):
        self._params = params
        self._text = text

    def get_attribute(self, name):
        return self._params

    def inner_text(self):
        return self._text


def _btn(item_name, text):
    return _FakeButton(json.dumps({"item_name": item_name}), text)


class _FakeWengPage:
    """collect 가 쓰는 Page 표면만 흉내낸다 (브라우저 없음)."""

    def __init__(self, body, buttons, url):
        self.url = url
        self._body = body
        self._buttons = buttons

    def on(self, event, handler):
        pass

    def goto(self, url, wait_until=None, timeout=None):
        pass

    def wait_for_timeout(self, ms):
        pass

    def title(self):
        return "여행노트-마펑워"

    def inner_text(self, selector):
        return self._body

    def query_selector_all(self, selector):
        return self._buttons


class _FakeSession:
    def __init__(self, page):
        self._page = page

    @contextmanager
    def page(self, url_for_cookies=None, desktop=False):
        yield self._page


def test_weng_action_bar_maps_item_names_to_fields():
    page = _FakeWengPage(
        "", [_btn("点赞", "24"), _btn("评论", "3"), _btn("收藏", "1.2万")], ""
    )
    assert MafengwoAdapter._read_weng_action_bar(page) == {
        "likes": 24,
        "comments": 3,
        "collects": 12000,
    }


def test_weng_action_bar_skips_broken_buttons():
    """속성 없음·JSON 깨짐·모르는 item_name·숫자 없음은 건너뛰고, 중복은 첫 값 유지."""
    page = _FakeWengPage(
        "",
        [
            _FakeButton(None, "24"),  # data-exp-display-params 없음
            _FakeButton("{not json", "10"),  # JSON 깨짐
            _btn("分享", "7"),  # 매핑에 없는 item_name
            _btn("点赞", "点赞"),  # 숫자 없는 텍스트
            _btn("点赞", "24"),
            _btn("点赞", "99"),  # 중복 버튼 — setdefault 로 첫 값 유지
        ],
        "",
    )
    assert MafengwoAdapter._read_weng_action_bar(page) == {"likes": 24}


def test_collect_weng_counts_fill_gaps_but_do_not_override_text():
    """액션바 값은 본문 텍스트/XHR 이 못 채운 지표만 보충한다 (setdefault 병합)."""
    url = "https://m.mafengwo.cn/mweng/wengdetailssr/weng?id=42"
    body = "浏览 100 · 顶 38\n发表于 2026-08-01"
    page = _FakeWengPage(body, [_btn("点赞", "999"), _btn("评论", "3")], url)
    metrics = MafengwoAdapter(_FakeSession(page)).collect(url)
    assert metrics.article_id == "42"
    assert metrics.likes == 38  # 본문 텍스트 값 유지 — 액션바 999 로 덮지 않는다
    assert metrics.views == 100
    assert metrics.comments == 3  # 텍스트에 없던 지표만 액션바로 보충
    assert metrics.upload_date == "2026-08-01"


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
    found = find_counts(data, MafengwoAdapter.xhr_count_keys)
    assert found == {"likes": 38, "comments": 6, "views": 12000, "collects": 14}


def test_find_counts_first_match_wins():
    data = [{"vote_num": 10}, {"vote_num": 99}]
    assert find_counts(data, MafengwoAdapter.xhr_count_keys)["likes"] == 10


def test_find_counts_ignores_non_numeric():
    assert find_counts(
        {"vote_num": None, "reply_num": [1]}, MafengwoAdapter.xhr_count_keys
    ) == {}


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
