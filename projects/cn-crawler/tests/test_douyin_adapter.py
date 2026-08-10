"""도우인 어댑터 파싱 로직 스모크 테스트 (브라우저 없이 가짜 픽스처로)."""

import json

import pytest

from crawler.adapters.douyin import (
    DouyinAdapter,
    _format_create_time,
    _is_short_link,
    _short_link_code,
    _static_aweme_id,
    detect_walls,
    extract_followers,
    extract_publish_time,
)

_AWEME_ID = "7672040398223710459"


# --- article_id 파싱 --------------------------------------------------------

@pytest.mark.parametrize(
    "url,expected",
    [
        (f"https://www.douyin.com/video/{_AWEME_ID}", _AWEME_ID),
        (f"https://www.douyin.com/note/{_AWEME_ID}", _AWEME_ID),
        (f"https://www.douyin.com/discover?modal_id={_AWEME_ID}", _AWEME_ID),
    ],
)
def test_parse_article_id(url, expected):
    adapter = DouyinAdapter.__new__(DouyinAdapter)  # 브라우저 세션 불필요
    assert adapter.parse_article_id(url) == expected


def test_parse_article_id_rejects_invalid():
    adapter = DouyinAdapter.__new__(DouyinAdapter)
    with pytest.raises(ValueError):
        adapter.parse_article_id("https://www.douyin.com/jingxuan")


def test_short_link_detected_and_resolved_from_final_url():
    """v.douyin.com 단축링크는 리다이렉트된 최종 URL 에서 aweme_id 를 해석한다."""
    short = "https://v.douyin.com/AbCdEfG/"
    assert _is_short_link(short) is True
    assert _static_aweme_id(short) is None
    final = f"https://www.douyin.com/video/{_AWEME_ID}?previous_page=app_code_link"
    assert _static_aweme_id(final) == _AWEME_ID
    # 최종 URL 에서도 못 얻으면(verify 리다이렉트) 단축코드가 식별자
    assert _short_link_code(short) == "douyin-short:AbCdEfG"


# --- verify 감지 ------------------------------------------------------------

def test_detects_verify_slider_in_body():
    assert detect_walls(
        f"https://www.douyin.com/video/{_AWEME_ID}", "拖动滑块完成拼图"
    ) == {"verify_wall": True}


def test_detects_verify_redirect_url():
    assert detect_walls("https://www.douyin.com/verify?from=video", "") == {
        "verify_wall": True
    }


def test_no_wall_on_normal_page():
    """verify SDK 정적 JS 는 정상 페이지에도 로드된다 → 본문/URL 마커만 판정."""
    assert detect_walls(f"https://www.douyin.com/video/{_AWEME_ID}", "全部评论") == {}


# --- XHR 타깃 매칭 ----------------------------------------------------------

def _detail_body(aweme_id=_AWEME_ID, play_count=0):
    return json.dumps(
        {
            "aweme_detail": {
                "aweme_id": aweme_id,
                "desc": "예시 영상 설명 #태그",
                "create_time": 1786286104,
                "statistics": {
                    "aweme_id": aweme_id,
                    "digg_count": 121277,
                    "collect_count": 39679,
                    "comment_count": 4007,
                    "share_count": 9744,
                    "play_count": play_count,
                },
                "author": {"nickname": "작성자", "follower_count": 56500},
            },
            "status_code": 0,
        }
    )


def test_xhr_counts_from_matching_detail():
    adapter = DouyinAdapter.__new__(DouyinAdapter)
    detail = adapter._target_detail({"json_bodies": [_detail_body()]}, _AWEME_ID)
    assert detail is not None
    counts = adapter._xhr_counts(detail)
    assert counts == {
        "likes": 121277,
        "collects": 39679,
        "comments": 4007,
        "shares": 9744,
        "followers": 56500,
    }


def test_xhr_hidden_play_count_zero_is_dropped():
    """웹 API 는 play_count=0 으로 숨긴다 → 0 을 실측값으로 저장하지 않는다."""
    adapter = DouyinAdapter.__new__(DouyinAdapter)
    detail = adapter._target_detail({"json_bodies": [_detail_body()]}, _AWEME_ID)
    assert "views" not in adapter._xhr_counts(detail)
    detail2 = adapter._target_detail(
        {"json_bodies": [_detail_body(play_count=1234)]}, _AWEME_ID
    )
    assert adapter._xhr_counts(detail2)["views"] == 1234


def test_detail_with_wrong_aweme_id_is_rejected():
    """연속재생으로 다른 영상의 detail 이 온 사례 실측 → id 불일치면 버린다."""
    adapter = DouyinAdapter.__new__(DouyinAdapter)
    captured = {"json_bodies": [_detail_body(aweme_id="7647057168335785254")]}
    assert adapter._target_detail(captured, _AWEME_ID) is None
    assert adapter._xhr_counts(None) == {}


# --- DOM 텍스트 추출 --------------------------------------------------------

def test_extract_followers_from_user_info():
    assert extract_followers("拾一颗甜柚粉丝5.6万获赞79.8万关注") == {"followers": 56000}


def test_extract_followers_missing():
    assert extract_followers("获赞79.8万") == {}


def test_extract_publish_time():
    assert extract_publish_time("发布时间：2026-08-09 23:35") == {
        "upload_date": "2026-08-09 23:35"
    }
    assert extract_publish_time("발행시간 없음") == {}


def test_format_create_time():
    assert _format_create_time(1786286104) == "2026-08-09"
    assert _format_create_time(None) is None
    assert _format_create_time(0) is None


# --- 병합/진단 --------------------------------------------------------------

def _build(dom=None, xhr=None, detail=None, walls=None):
    return DouyinAdapter._build_metrics(
        article_id=_AWEME_ID,
        url=f"https://www.douyin.com/video/{_AWEME_ID}",
        final_url=f"https://www.douyin.com/video/{_AWEME_ID}",
        page_title="在抖音记录美好生活 - 抖音",
        dom=dom or {},
        xhr=xhr or {},
        detail=detail,
        captured={"json_bodies": []},
        walls=walls or {},
    )


def test_build_metrics_degrades_on_verify_wall():
    m = _build(walls={"verify_wall": True})
    assert m.likes is None
    raw = json.loads(m.raw)
    assert raw["verify_wall"] is True
    assert raw["detail_matched"] is False


def test_build_metrics_prefers_xhr_and_uses_desc_title():
    detail = {"desc": "예시 영상 설명", "create_time": 1786286104}
    m = _build(
        dom={"likes": 121000, "upload_date": "2026-08-09 23:35", "followers": 56000},
        xhr={"likes": 121277, "followers": 56500},
        detail=detail,
    )
    assert m.platform == "douyin"
    assert m.likes == 121277  # XHR 우선
    assert m.followers == 56500
    assert m.title == "예시 영상 설명"
    assert m.upload_date == "2026-08-09 23:35"  # DOM 발행시간 우선
    assert m.post_format == "video"


def test_build_metrics_upload_date_falls_back_to_create_time():
    m = _build(detail={"desc": "", "create_time": 1786286104})
    assert m.upload_date == "2026-08-09"
    assert m.title == "在抖音记录美好生活 - 抖音"  # desc 비면 페이지 제목
