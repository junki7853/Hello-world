"""도우인(抖音) 영상 상세 페이지 어댑터.

정찰 결과 (2026-08, 헤드리스·비중국 IP·비로그인, 데스크톱 뷰):
- 홈(www.douyin.com)·영상 상세(/video/<aweme_id>) 모두 정상 렌더됐다.
- XHR `aweme/v1/web/aweme/detail/` 응답의 aweme_detail.statistics 에
  digg_count(좋아요)/collect_count(저장)/comment_count/share_count 가 들어온다.
  play_count 는 웹 API 에서 0 으로 숨겨진다 → 0 이면 지표로 쓰지 않는다.
  author.follower_count(팬 수)·create_time(업로드 유닉스시각)·desc(제목)도 여기서 얻는다.
- tab/feed·series XHR 에는 추천 영상들의 statistics 가 섞여 있고, detail XHR 이
  타깃과 다른 aweme_id 를 반환하는 경우도 실측됐다(연속재생) → 반드시 타깃
  aweme_id 매칭 서브트리 안에서만 카운트를 찾는다.
- DOM 은 data-e2e 속성으로 안정적으로 읽힌다: video-player-digg(좋아요)·
  video-player-collect(저장)·video-player-share(공유)·feed-comment-icon(댓글)·
  detail-video-publish-time(발행시간)·user-info(粉丝 N).
- a_bogus/msToken 서명은 리버싱하지 않는다(브라우저가 계산). verify 슬라이더가
  뜨면(拖动滑块/验证码中间页) 자동돌파 없이 감지 → None + raw degrade.
  verify SDK 정적 JS(rc-verifycenter)는 정상 페이지에도 로드되므로 마커로 쓰지 않는다.

단축링크(v.douyin.com/<code>) 정찰 실측 (2026-08):
- 302 체인: v.douyin.com/<code> → www.iesdouyin.com/share/video/<aweme_id>/?…
  → www.douyin.com/video/<aweme_id>?previous_page=web_code_link (detail XHR 발화).
- 최종 랜딩이 홈/verify 로 튕기는 변형에 대비해 최종 URL 뿐 아니라 리다이렉트
  체인 전체에서 aweme_id 를 재해석하고, 랜딩이 공유월이라 detail XHR 이 안 뜨면
  표준 상세(/video/<id>)로 재진입해 modal_id 경로와 동일하게 수집한다.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from playwright.sync_api import Page, Response

from crawler.adapters.base import (
    XHR_BODY_MAX_LEN,
    Adapter,
    UnsupportedUrlError,
    detect_redirect_away,
    find_counts,
    find_subtree_by_id,
    is_short_link,
    navigate_and_settle,
    short_link_code,
    static_id_from_url,
)
from crawler.core.schema import Metrics, parse_count

logger = logging.getLogger(__name__)

_SETTLE_WAIT_MS = 10_000

# aweme_id: 15~20자리 숫자. /video/<id>, /note/<id>(이미지 게시물)
_AWEME_ID_PATTERN = re.compile(r"/(?:video|note)/(\d{15,20})")
_QUERY_ID_KEYS = ("aweme_id", "modal_id", "vid")
# 공유 단축링크 — 정적으로 id 를 알 수 없어 내비게이션 후 최종 URL 에서 해석
_SHORT_LINK_HOSTS = ("v.douyin.com", "iesdouyin.com")
_SHORT_CODE_PREFIX = "douyin-short:"

# detail XHR 만 수집한다 (tab/feed·series 의 추천 영상 카운트 오염 차단 1차 방어)
_DETAIL_XHR_MARKER = "/aweme/detail/"
# detail XHR 실측에서 관측된 id 키만 (camelCase 는 관측된 바 없어 두지 않는다)
_ID_KEYS = ("aweme_id",)

# verify 슬라이더/중간페이지 감지 (본문 텍스트·최종 URL)
_VERIFY_TEXT_MARKERS = ("拖动滑块", "验证码中间页", "请完成下列验证")
_VERIFY_URL_MARKER = "douyin.com/verify"

_PUBLISH_TIME_PATTERN = re.compile(
    r"发布时间[:：]\s*(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)"
)
_FOLLOWERS_PATTERN = re.compile(r"粉丝\s*[:：]?\s*(\d[\d.,]*\s*[万亿]?)")

# DOM data-e2e 셀렉터 → Metrics 필드 (정찰 실측)
_DOM_SELECTORS: tuple[tuple[str, str], ...] = (
    ("likes", '[data-e2e="video-player-digg"]'),
    ("collects", '[data-e2e="video-player-collect"]'),
    ("shares", '[data-e2e="video-player-share"]'),
    ("comments", '[data-e2e="feed-comment-icon"]'),
)
_USER_INFO_SELECTOR = '[data-e2e="user-info"]'
_PUBLISH_TIME_SELECTOR = '[data-e2e="detail-video-publish-time"]'


class DouyinAdapter(Adapter):
    platform = "douyin"

    xhr_count_keys = {
        "digg_count": "likes",
        "collect_count": "collects",
        "comment_count": "comments",
        "share_count": "shares",
        "play_count": "views",
        "follower_count": "followers",
    }

    def parse_article_id(self, url: str) -> str:
        """경로 /video/<id>·/note/<id> 또는 modal_id 쿼리에서 aweme_id 를 파싱한다.

        v.douyin.com 단축링크는 정적으로 알 수 없다 → collect 가 리다이렉트 후 해석.
        """
        aweme_id = _static_aweme_id(url)
        if aweme_id is None:
            raise UnsupportedUrlError(f"URL 에서 도우인 aweme_id 를 찾을 수 없습니다: {url}")
        return aweme_id

    def collect(self, url: str) -> Metrics:
        short = is_short_link(url, _SHORT_LINK_HOSTS)
        static_id = None if short else self.parse_article_id(url)
        captured: dict[str, object] = {"json_bodies": []}
        nav_urls: list[str] = []
        diag: dict[str, object] = {}

        with self.session.page(url_for_cookies=url, desktop=True) as page:

            def on_response(resp: Response) -> None:
                # 플랫폼 고유부: detail XHR 만 수집 (피드/시리즈 응답 오염 차단)
                if _DETAIL_XHR_MARKER in resp.url:
                    self.capture_count_json(resp, captured, max_len=XHR_BODY_MAX_LEN)
                # 단축링크 해석용: 메인 프레임 내비게이션(302 홉 포함) URL 기록
                try:
                    if resp.request.is_navigation_request() and resp.frame == page.main_frame:
                        nav_urls.append(resp.url)
                except Exception as exc:  # 응답/프레임이 이미 사라진 경우 등 메타접근 방어
                    logger.debug("도우인 내비게이션 메타 읽기 실패 %s: %s", resp.url[:80], exc)

            page.on("response", on_response)
            navigate_and_settle(page, url, _SETTLE_WAIT_MS)
            resolved_id = static_id or _resolve_aweme_id(page.url, nav_urls)
            if short:
                diag["short_resolved"] = resolved_id is not None
            if (
                short
                and resolved_id is not None
                and self._target_detail(captured, resolved_id) is None
            ):
                # 랜딩이 공유월/중간 페이지라 detail XHR 이 안 떴다 →
                # 표준 상세로 재진입해 modal_id 경로와 동일하게 수집한다
                diag["renavigated"] = True
                navigate_and_settle(
                    page,
                    _canonical_detail_url(resolved_id, [page.url, *nav_urls]),
                    _SETTLE_WAIT_MS,
                )
            final_url = page.url
            page_title = page.title()
            body_text = self.page_body_text(page)
            dom = self._read_dom(page)

        article_id = resolved_id or short_link_code(url, _SHORT_CODE_PREFIX)
        walls = detect_walls(final_url, body_text)
        walls.update(detect_redirect_away(final_url, article_id))
        if walls:
            # redirected_away 포함: DOM 은 리다이렉트된 다른 영상의 지표일 수
            # 있으므로(진선 modal 리다이렉트 실측) 통째로 버린다
            dom = {}
        detail = self._target_detail(captured, article_id)
        xhr = self._xhr_counts(detail)
        return self._build_metrics(
            article_id, url, final_url, page_title, dom, xhr, detail, captured, walls,
            diag=diag,
        )

    # --- 플랫폼 고유부 -----------------------------------------------------

    @staticmethod
    def _read_dom(page: Page) -> dict[str, object]:
        """data-e2e 셀렉터로 카운트·팬수·발행시간을 읽는다 (없으면 건너뜀)."""
        dom: dict[str, object] = {}
        for field, selector in _DOM_SELECTORS:
            try:
                element = page.query_selector(selector)
                text = element.inner_text() if element else ""
            except Exception as exc:
                logger.debug("도우인 DOM 읽기 실패 %s: %s", selector, exc)
                continue
            value = parse_count(text)
            if value is not None:
                dom[field] = value
        for selector, extractor in (
            (_USER_INFO_SELECTOR, extract_followers),
            (_PUBLISH_TIME_SELECTOR, extract_publish_time),
        ):
            try:
                element = page.query_selector(selector)
                text = element.inner_text() if element else ""
            except Exception as exc:
                logger.debug("도우인 DOM 읽기 실패 %s: %s", selector, exc)
                continue
            dom.update(extractor(text))
        return dom

    def _target_detail(
        self, captured: dict[str, object], article_id: str
    ) -> dict | None:
        """수집한 detail JSON 중 타깃 aweme_id 와 일치하는 서브트리를 찾는다.

        detail XHR 이 다른 영상(연속재생)을 반환한 사례가 실측됐으므로 id 매칭이
        안 되면 버린다 — 엉뚱한 영상의 지표를 저장하는 것보다 None 이 낫다.
        """
        bodies = captured.get("json_bodies")
        if not isinstance(bodies, list):
            return None
        for body in bodies:
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                continue
            subtree = find_subtree_by_id(data, _ID_KEYS, article_id)
            if isinstance(subtree, dict) and "statistics" in subtree:
                return subtree
        return None

    def _xhr_counts(self, detail: dict | None) -> dict[str, int]:
        """타깃 aweme_detail 서브트리에서 카운트를 찾는다.

        웹 API 는 play_count 를 0 으로 숨긴다 → 0 은 실측값이 아니므로 버린다.
        """
        if detail is None:
            return {}
        counts = find_counts(detail, self.xhr_count_keys)
        if counts.get("views") == 0:
            del counts["views"]
        return counts

    # --- 병합 -------------------------------------------------------------

    @classmethod
    def _build_metrics(
        cls,
        article_id: str,
        url: str,
        final_url: str,
        page_title: str,
        dom: dict[str, object],
        xhr: dict[str, int],
        detail: dict | None,
        captured: dict[str, object],
        walls: dict[str, bool],
        diag: dict[str, object] | None = None,
    ) -> Metrics:
        if walls:
            logger.warning("도우인 차단 감지 %s — 지표 없이 진단만 기록: %s", walls, url)

        desc = (detail or {}).get("desc")
        create_time = (detail or {}).get("create_time")
        upload_date = dom.get("upload_date")
        if not isinstance(upload_date, str):
            upload_date = _format_create_time(create_time)

        dom_counts = {k: v for k, v in dom.items() if isinstance(v, int)}
        raw = json.dumps(
            {
                **walls,
                **(diag or {}),
                "captured_json": len(captured.get("json_bodies") or []),
                "detail_matched": detail is not None,
                "final_url": final_url[:200],
                "dom": dom_counts,
                "xhr": xhr,
                "page_title": page_title,
            },
            ensure_ascii=False,
        )[:2000]

        return Metrics(
            platform="douyin",
            article_id=article_id,
            url=url,
            title=(desc if isinstance(desc, str) and desc else page_title) or None,
            views=cls.pick_metric(xhr, dom_counts, "views"),
            likes=cls.pick_metric(xhr, dom_counts, "likes"),
            collects=cls.pick_metric(xhr, dom_counts, "collects"),
            comments=cls.pick_metric(xhr, dom_counts, "comments"),
            shares=cls.pick_metric(xhr, dom_counts, "shares"),
            followers=cls.pick_metric(xhr, dom_counts, "followers"),
            upload_date=upload_date,
            post_format="video" if "/video/" in (final_url or url) else None,
            raw=raw,
        )


def _static_aweme_id(url: str) -> str | None:
    """URL 경로/쿼리에서 aweme_id 를 찾는다. 없으면 None (숫자 id 만 인정)."""
    aweme_id = static_id_from_url(url, _AWEME_ID_PATTERN, _QUERY_ID_KEYS)
    return aweme_id if aweme_id and aweme_id.isdigit() else None


def _resolve_aweme_id(final_url: str, nav_urls: list[str]) -> str | None:
    """최종 URL → 내비게이션 체인(최근 홉 우선)에서 aweme_id 를 찾는다.

    단축링크는 iesdouyin 공유 페이지(/share/video/<id>)를 거치므로, 최종 랜딩이
    홈/verify 로 튕겨도 중간 홉 URL 에 id 가 남아 있다 → 체인 전체를 본다.
    """
    for candidate in (final_url, *reversed(nav_urls)):
        aweme_id = _static_aweme_id(candidate)
        if aweme_id:
            return aweme_id
    return None


def _canonical_detail_url(aweme_id: str, seen_urls: list[str]) -> str:
    """재진입할 표준 상세 URL. 체인에 /note/<id> 가 있었으면 이미지 게시물이다."""
    kind = "note" if any(f"/note/{aweme_id}" in u for u in seen_urls) else "video"
    return f"https://www.douyin.com/{kind}/{aweme_id}"


def _format_create_time(create_time: object) -> str | None:
    """aweme_detail.create_time(유닉스 초) → 'YYYY-MM-DD' (UTC)."""
    if not isinstance(create_time, int) or create_time <= 0:
        return None
    return datetime.fromtimestamp(create_time, tz=timezone.utc).strftime("%Y-%m-%d")


def detect_walls(final_url: str, body_text: str) -> dict[str, bool]:
    """최종 URL·본문 텍스트로 verify 슬라이더/중간페이지를 감지한다.

    타깃 이탈(실측: /video/<id> 가 jingxuan?modal_id=<다른 id> 로 리다이렉트,
    이때 DOM 은 엉뚱한 영상의 지표)은 base.detect_redirect_away 로 별도 판정한다.
    """
    walls: dict[str, bool] = {}
    if _VERIFY_URL_MARKER in final_url or any(
        marker in body_text for marker in _VERIFY_TEXT_MARKERS
    ):
        walls["verify_wall"] = True
    return walls


def extract_followers(text: str) -> dict[str, object]:
    """user-info 텍스트("粉丝5.6万获赞79.8万")에서 팬 수를 추출한다."""
    match = _FOLLOWERS_PATTERN.search(text)
    if not match:
        return {}
    value = parse_count(match.group(1))
    return {"followers": value} if value is not None else {}


def extract_publish_time(text: str) -> dict[str, object]:
    """publish-time 텍스트("发布时间：2026-08-09 23:35")에서 발행시각을 추출한다."""
    match = _PUBLISH_TIME_PATTERN.search(text)
    return {"upload_date": match.group(1)} if match else {}
