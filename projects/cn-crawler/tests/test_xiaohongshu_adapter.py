"""샤오홍슈 어댑터 파싱 로직 스모크 테스트 (브라우저 없이 가짜 픽스처로)."""

import json

import pytest

from crawler.adapters.base import find_subtree_by_id
from crawler.adapters.xiaohongshu import (
    XiaohongshuAdapter,
    _is_short_link,
    _short_link_code,
    _static_note_id,
    detect_walls,
    extract_metrics_from_text,
)

_NOTE_ID = "68a1b2c3000000001f00a1b2"


# --- article_id 파싱 --------------------------------------------------------

@pytest.mark.parametrize(
    "url,expected",
    [
        (f"https://www.xiaohongshu.com/explore/{_NOTE_ID}?xsec_token=AB12&xsec_source=pc_feed", _NOTE_ID),
        (f"https://www.xiaohongshu.com/discovery/item/{_NOTE_ID}", _NOTE_ID),
        (f"https://www.xiaohongshu.com/detail?noteId={_NOTE_ID}", _NOTE_ID),
    ],
)
def test_parse_article_id(url, expected):
    adapter = XiaohongshuAdapter.__new__(XiaohongshuAdapter)  # 브라우저 세션 불필요
    assert adapter.parse_article_id(url) == expected


def test_parse_article_id_rejects_invalid():
    adapter = XiaohongshuAdapter.__new__(XiaohongshuAdapter)
    with pytest.raises(ValueError):
        adapter.parse_article_id("https://www.xiaohongshu.com/explore")


def test_short_link_detected_and_resolved_from_final_url():
    """단축링크는 정적 파싱 불가 → 리다이렉트된 최종 URL 에서 해석하는 경로."""
    short = "https://xhslink.com/a/AbCd12"
    assert _is_short_link(short) is True
    assert _static_note_id(short) is None
    final = f"https://www.xiaohongshu.com/explore/{_NOTE_ID}?xsec_token=XY"
    assert _static_note_id(final) == _NOTE_ID
    # 최종 URL 에서도 못 얻으면(차단 리다이렉트) 단축코드가 식별자
    assert _short_link_code(short) == "xhslink:a/AbCd12"


# --- 차단(월) 감지 ----------------------------------------------------------

def test_detects_security_wall_redirect():
    final = (
        "https://www.xiaohongshu.com/404/sec_HFVfghbi?source=xhs_sec_server"
        "&originalUrl=http%3A%2F%2Fwww.xiaohongshu.com%2Fexplore%2Fabc"
    )
    assert detect_walls(final, "") == {"security_wall": True}


def test_detects_captcha_wall_redirect():
    final = "https://www.xiaohongshu.com/website-login/captcha?redirectPath=..."
    assert detect_walls(final, "") == {"captcha_wall": True}


def test_detects_app_wall_from_body():
    body = "App内打开\n当前内容仅支持在小红书 APP 内查看\n打开 APP 查看"
    assert detect_walls("https://www.xiaohongshu.com/explore/abc", body) == {
        "app_wall": True
    }


def test_no_wall_on_normal_page():
    assert detect_walls(f"https://www.xiaohongshu.com/explore/{_NOTE_ID}", "点赞 38") == {}


# --- DOM 텍스트 추출 --------------------------------------------------------

def test_extract_from_rendered_text():
    text = "点赞 1.2万\n收藏 3456\n共 678 条评论\n分享 90"
    assert extract_metrics_from_text(text) == {
        "likes": 12000,
        "collects": 3456,
        "comments": 678,
        "shares": 90,
    }


def test_extract_number_before_label():
    text = "38 人点赞\n14 人收藏"
    assert extract_metrics_from_text(text) == {"likes": 38, "collects": 14}


# --- XHR/SSR JSON 타깃 서브트리 탐색 ----------------------------------------

def _feed_body(note_id=_NOTE_ID):
    """웹 feed API 모양: 타깃 노트 + 추천 노트(오염원)가 함께 온다."""
    return json.dumps(
        {
            "code": 0,
            "data": {
                "items": [
                    {
                        "id": note_id,
                        "model_type": "note",
                        "note_card": {
                            "interact_info": {
                                "liked_count": "1.2万",
                                "collected_count": "3456",
                                "comment_count": "678",
                                "share_count": "90",
                            }
                        },
                    },
                    {
                        "id": "ffffffffffffffffffffffff",
                        "note_card": {
                            "interact_info": {"liked_count": "999999"}
                        },
                    },
                ]
            },
        }
    )


def test_xhr_counts_only_from_target_note():
    adapter = XiaohongshuAdapter.__new__(XiaohongshuAdapter)
    captured = {"json_bodies": [_feed_body()]}
    counts = adapter._xhr_counts(captured, _NOTE_ID)
    assert counts == {"likes": 12000, "collects": 3456, "comments": 678, "shares": 90}


def test_xhr_counts_ignores_other_notes_when_target_missing():
    """타깃 노트가 응답에 없으면(차단 등) 추천 노트 값을 훔치지 않는다."""
    adapter = XiaohongshuAdapter.__new__(XiaohongshuAdapter)
    captured = {"json_bodies": [_feed_body(note_id="eeeeeeeeeeeeeeeeeeeeeeee")]}
    assert adapter._xhr_counts(captured, _NOTE_ID) == {}


def test_xhr_counts_reads_camelcase_initial_state():
    """SSR __INITIAL_STATE__ 는 camelCase 키를 쓴다."""
    state = json.dumps(
        {
            "note": {
                "noteDetailMap": {
                    _NOTE_ID: {
                        "note": {
                            "noteId": _NOTE_ID,
                            "interactInfo": {
                                "likedCount": "38",
                                "collectedCount": "14",
                                "commentCount": "6",
                            },
                        }
                    }
                }
            }
        }
    )
    adapter = XiaohongshuAdapter.__new__(XiaohongshuAdapter)
    counts = adapter._xhr_counts({"json_bodies": [state]}, _NOTE_ID)
    assert counts == {"likes": 38, "collects": 14, "comments": 6}


def test_find_subtree_by_id_missing_returns_none():
    assert find_subtree_by_id({"a": [{"id": "x"}]}, ("id",), "y") is None


# --- 병합/진단 --------------------------------------------------------------

def _build(dom=None, xhr=None, walls=None):
    return XiaohongshuAdapter._build_metrics(
        article_id=_NOTE_ID,
        url=f"https://www.xiaohongshu.com/explore/{_NOTE_ID}",
        final_url=f"https://www.xiaohongshu.com/explore/{_NOTE_ID}",
        page_title="테스트 노트",
        dom=dom or {},
        xhr=xhr or {},
        captured={"json_bodies": []},
        body_text="",
        walls=walls or {},
    )


def test_build_metrics_degrades_on_security_wall():
    m = _build(walls={"security_wall": True})
    assert m.likes is None
    assert json.loads(m.raw)["security_wall"] is True


def test_build_metrics_xhr_overrides_dom():
    m = _build(dom={"likes": 5}, xhr={"likes": 12000, "collects": 3456})
    assert m.platform == "xiaohongshu"
    assert m.likes == 12000
    assert m.collects == 3456
