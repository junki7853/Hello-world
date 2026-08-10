"""씨트립(Ctrip) 여행 커뮤니티 상세 페이지 어댑터.

정찰 결과(2026-08, 헤드리스·비중국 환경):
- 기사 상세 본문은 안티봇에 막혀 에러 페이지("哎呀，出错啦")로 뜨는 경우가 있다.
  이때 div.zan-container(좋아요/저장) DOM 은 렌더되지 않는다.
- 반면 댓글 목록 XHR `restapi/soa2/20725/json/ruleSortCommentList` 응답의
  최상위 `totalCount` 는 안정적으로 실제 댓글 수를 준다.
- 관련추천 XHR `relatedRecommend` 의 카운트들은 "추천 기사"의 값이라 타깃과 무관 → 쓰지 않는다.

따라서 두 경로를 모두 시도하고 병합한다:
  1) XHR 가로채기 — 댓글 수(ruleSortCommentList.totalCount), 그리고 기사 상세 응답이
     잡히면 좋아요/저장/조회/제목/작성자.
  2) DOM 셀렉터(zan-container) — 페이지가 정상 렌더될 때(중국 IP/유효 쿠키) 좋아요·저장.
  둘 다 실패하면 해당 지표는 None 으로 두고 raw 에 진단 정보를 남긴다.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Page, Response

from crawler.adapters.base import Adapter
from crawler.core.schema import Metrics, parse_count

logger = logging.getLogger(__name__)

_COMMENT_LIST_MARKER = "ruleSortCommentList"
# 기사 상세 카운트가 담길 법한 응답 마커 (정상 렌더 시 등장)
_DETAIL_MARKERS = ("articleDetail", "getArticle", "tripshoot", "contentDetail")

_NAV_TIMEOUT_MS = 60_000
_SELECTOR_TIMEOUT_MS = 8_000


class CtripAdapter(Adapter):
    platform = "ctrip"

    def parse_article_id(self, url: str) -> str:
        """URL 쿼리스트링의 articleId 를 게시물 id 로 파싱한다."""
        query = parse_qs(urlparse(url).query)
        values = query.get("articleId")
        if not values or not values[0]:
            raise ValueError(f"URL 에서 articleId 를 찾을 수 없습니다: {url}")
        return values[0]

    def collect(self, url: str) -> Metrics:
        article_id = self.parse_article_id(url)
        captured: dict[str, object] = {}

        def on_response(resp: Response) -> None:
            self._capture_xhr(resp, article_id, captured)

        with self.session.page(url_for_cookies=url) as page:
            page.on("response", on_response)
            page.goto(url, wait_until="networkidle", timeout=_NAV_TIMEOUT_MS)
            self._wait_for_content(page)
            dom = self._extract_from_dom(page)
            page_title = page.title()

        xhr = self._parse_captured(captured)
        return self._build_metrics(article_id, url, page_title, dom, xhr, captured)

    # --- XHR 경로 ---------------------------------------------------------

    def _capture_xhr(
        self, resp: Response, article_id: str, captured: dict[str, object]
    ) -> None:
        """관심 있는 XHR 응답 본문을 저장한다. 파싱은 나중에."""
        url = resp.url
        try:
            if _COMMENT_LIST_MARKER in url:
                captured["comment_list"] = resp.text()
            elif any(marker in url for marker in _DETAIL_MARKERS):
                # 상세 응답이 타깃 기사의 것인지 본문으로 확인
                body = resp.text()
                if article_id in body:
                    captured["detail"] = body
        except Exception as exc:  # 응답이 이미 사라졌거나 바이너리인 경우
            logger.debug("XHR 본문 읽기 실패 %s: %s", url[:80], exc)

    def _parse_captured(self, captured: dict[str, object]) -> dict[str, int | str | None]:
        """저장된 XHR 본문에서 지표를 추출한다."""
        result: dict[str, int | str | None] = {}

        comment_body = captured.get("comment_list")
        if isinstance(comment_body, str):
            try:
                data = json.loads(comment_body)
                total = data.get("totalCount")
                if isinstance(total, int):
                    result["comments"] = total
            except (json.JSONDecodeError, AttributeError) as exc:
                logger.debug("댓글 목록 JSON 파싱 실패: %s", exc)

        detail_body = captured.get("detail")
        if isinstance(detail_body, str):
            result.update(self._extract_detail_counts(detail_body))

        return result

    @staticmethod
    def _extract_detail_counts(body: str) -> dict[str, int | str | None]:
        """기사 상세 JSON 에서 좋아요/저장/조회/제목/작성자를 최선껏 추출한다.

        Ctrip 응답 구조는 버전에 따라 다르므로, 알려진 키를 얕게 탐색한다.
        """
        out: dict[str, int | str | None] = {}
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return out

        # 상세 객체가 어디 있든 재귀로 찾되, 카운트 키가 함께 있는 dict 를 채택
        target = _find_dict_with_keys(data, ("likeCount", "collectCount"))
        if target is None:
            return out
        for src, dst in (
            ("likeCount", "likes"),
            ("collectCount", "collects"),
            ("readCount", "views"),
            ("viewCount", "views"),
            ("commentCount", "comments"),
            ("shareCount", "shares"),
        ):
            value = target.get(src)
            if isinstance(value, int):
                out[dst] = value
        for src, dst in (("title", "title"), ("articleTitle", "title"), ("author", "author")):
            value = target.get(src)
            if isinstance(value, str) and value:
                out.setdefault(dst, value)
        return out

    # --- DOM 경로 ---------------------------------------------------------

    def _wait_for_content(self, page: Page) -> None:
        """좋아요/저장 컨테이너가 렌더될 때까지 잠깐 기다린다(없으면 그냥 진행)."""
        try:
            page.wait_for_selector("div.zan-container", timeout=_SELECTOR_TIMEOUT_MS)
        except Exception:
            logger.debug("zan-container 미출현 — DOM 경로 생략 가능성")

    @staticmethod
    def _extract_from_dom(page: Page) -> dict[str, int | None]:
        """div.zan-container 에서 좋아요(赞)/저장(收藏) 수를 읽는다.

        디자인 피드백 실측: span.icon-title.icon-right = 좋아요, span.icon-title = 저장.
        """
        out: dict[str, int | None] = {}
        container = page.query_selector("div.zan-container")
        if container is None:
            return out
        like_el = container.query_selector("span.icon-title.icon-right")
        if like_el:
            out["likes"] = parse_count(like_el.inner_text())
        # 저장: icon-right 가 아닌 icon-title (좋아요를 제외한 나머지)
        for span in container.query_selector_all("span.icon-title"):
            classes = (span.get_attribute("class") or "").split()
            if "icon-right" not in classes:
                out["collects"] = parse_count(span.inner_text())
                break
        return out

    # --- 병합 -------------------------------------------------------------

    @staticmethod
    def _build_metrics(
        article_id: str,
        url: str,
        page_title: str,
        dom: dict[str, int | None],
        xhr: dict[str, int | str | None],
        captured: dict[str, object],
    ) -> Metrics:
        """XHR 우선, DOM 보조로 지표를 병합한다."""

        def pick(key: str) -> int | None:
            xhr_val = xhr.get(key)
            if isinstance(xhr_val, int):
                return xhr_val
            dom_val = dom.get(key)
            return dom_val if isinstance(dom_val, int) else None

        title = xhr.get("title")
        raw = json.dumps(
            {
                "captured_xhr": sorted(captured.keys()),
                "dom": dom,
                "xhr": {k: v for k, v in xhr.items() if k != "title"},
                "page_title": page_title,
            },
            ensure_ascii=False,
        )[:2000]

        return Metrics(
            platform="ctrip",
            article_id=article_id,
            url=url,
            title=title if isinstance(title, str) else None,
            author=xhr.get("author") if isinstance(xhr.get("author"), str) else None,
            views=pick("views"),
            likes=pick("likes"),
            collects=pick("collects"),
            comments=pick("comments"),
            shares=pick("shares"),
            raw=raw,
        )


def _find_dict_with_keys(obj: object, keys: tuple[str, ...]) -> dict | None:
    """중첩 구조에서 주어진 키를 모두 가진 첫 dict 를 BFS 로 찾는다."""
    queue: list[object] = [obj]
    while queue:
        current = queue.pop(0)
        if isinstance(current, dict):
            if all(k in current for k in keys):
                return current
            queue.extend(current.values())
        elif isinstance(current, list):
            queue.extend(current)
    return None
