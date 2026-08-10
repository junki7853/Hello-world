"""샤오홍슈(小红书) 노트 상세 페이지 어댑터.

정찰 결과 (2026-08, 헤드리스·비중국 IP·비로그인):
- 데스크톱 뷰 explore 는 즉시 보안검증(website-login/captcha, QR 스캔)으로 리다이렉트.
- 노트 상세는 유효한 xsec_token 없이는 /404/sec_* (xhs_sec_server) 보안 페이지로
  리다이렉트되거나 "当前内容仅支持在小红书 APP 内查看" 앱 유도월이 뜬다.
- 모바일 뷰 홈은 렌더되지만 노트 링크·id 가 DOM 에 노출되지 않는다.
- x-s/x-t 서명·슬라이드 캡차는 리버싱/자동돌파하지 않는다 → 감지 후 degrade.

정상 렌더 환경(중국 IP·유효 쿠키 CRAWLER_COOKIES·xsec_token 포함 공유링크)에서는:
- XHR /api/sns/web/v1/feed 류 JSON 의 interact_info 에 liked_count/collected_count/
  comment_count/share_count 가 문자열("1.2万" 포함)로 들어온다.
- SSR 초기상태(window.__INITIAL_STATE__)에도 같은 카운트가 camelCase(likedCount…)로
  들어 있어 함께 수집한다.
- 피드 응답에는 추천 노트 카운트도 섞이므로 타깃 note id 서브트리로 좁힌 뒤 탐색한다.

동작: 보안월/캡차월/앱월 감지 시 None + raw 진단으로 degrade.
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Page, Response

from crawler.adapters.base import (
    Adapter,
    extract_labeled_counts,
    find_counts,
    find_subtree_by_id,
)
from crawler.core.schema import Metrics

logger = logging.getLogger(__name__)

_NAV_TIMEOUT_MS = 60_000
_SETTLE_WAIT_MS = 8_000
# 도우인/샤오홍슈 상세 JSON 은 20K 를 훌쩍 넘는다 → 잘리면 파싱 불가라 캡을 키운다
_XHR_BODY_MAX_LEN = 400_000

# 노트 id: 24자리 hex. /explore/<id>, /discovery/item/<id>
_NOTE_ID_PATTERN = re.compile(r"/(?:explore|discovery/item|item)/([0-9a-f]{24})")
_QUERY_ID_KEYS = ("noteId", "note_id")
# 공유 단축링크 (xhslink.com/xxx) — 정적으로 id 를 알 수 없어 내비게이션 후 해석
_SHORT_LINK_HOST = "xhslink.com"

# JSON 서브트리에서 타깃 노트를 찾을 id 키 (API snake_case + SSR camelCase)
_ID_KEYS = ("note_id", "noteId", "id")

# 차단 감지 마커
_SECURITY_URL_MARKERS = ("/404/sec_", "xhs_sec_server")
_CAPTCHA_URL_MARKER = "website-login/captcha"
_LOGIN_URL_MARKER = "/website-login/"
_APP_WALL_TEXT = "仅支持在小红书 APP 内查看"
# 노트가 안 열리고 전면 로그인 화면으로 대체될 때의 본문 마커 (실측: 가짜/무토큰
# 노트 id 가 /explore 로그인 화면으로 리다이렉트됨)
_LOGIN_WALL_TEXTS = ("手机号登录", "登录后推荐更懂你的笔记")

# 렌더된 본문 텍스트 패턴. 라벨→숫자를 먼저 두는 관례에 더해, 구분자를
# [^\S\n](개행 제외 공백)로 제한해 어느 방향으로도 줄을 넘어 훔치지 않게 한다.
_DOM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("comments", re.compile(r"共\s*(\d[\d.,]*\s*[万亿]?)\s*条评论")),
    ("likes", re.compile(r"点赞[^\S\n]*[:：]?[^\S\n]*(\d[\d.,]*[^\S\n]*[万亿]?)")),
    ("collects", re.compile(r"收藏[^\S\n]*[:：]?[^\S\n]*(\d[\d.,]*[^\S\n]*[万亿]?)")),
    ("comments", re.compile(r"评论[^\S\n]*[:：]?[^\S\n]*(\d[\d.,]*[^\S\n]*[万亿]?)")),
    ("shares", re.compile(r"分享[^\S\n]*[:：]?[^\S\n]*(\d[\d.,]*[^\S\n]*[万亿]?)")),
    ("likes", re.compile(r"(\d[\d.,]*[^\S\n]*[万亿]?)[^\S\n]*人?[^\S\n]*点赞")),
    ("collects", re.compile(r"(\d[\d.,]*[^\S\n]*[万亿]?)[^\S\n]*人?[^\S\n]*收藏")),
)


class XiaohongshuAdapter(Adapter):
    platform = "xiaohongshu"

    xhr_count_keys = {
        # web API (snake_case)
        "liked_count": "likes",
        "collected_count": "collects",
        "comment_count": "comments",
        "share_count": "shares",
        # SSR __INITIAL_STATE__ (camelCase)
        "likedCount": "likes",
        "collectedCount": "collects",
        "commentCount": "comments",
        "shareCount": "shares",
    }

    def parse_article_id(self, url: str) -> str:
        """경로 /explore/<id> 등 또는 noteId 쿼리에서 노트 id 를 파싱한다.

        xhslink.com 단축링크는 정적으로 알 수 없다 → collect 가 리다이렉트 후 해석.
        """
        note_id = _static_note_id(url)
        if note_id is None:
            raise ValueError(f"URL 에서 샤오홍슈 노트 id 를 찾을 수 없습니다: {url}")
        return note_id

    def collect(self, url: str) -> Metrics:
        # 단축링크는 리다이렉트 후 최종 URL 에서 id 를 해석한다
        static_id = None if _is_short_link(url) else self.parse_article_id(url)
        captured: dict[str, object] = {"json_bodies": []}

        def on_response(resp: Response) -> None:
            self.capture_count_json(resp, captured, max_len=_XHR_BODY_MAX_LEN)

        with self.session.page(url_for_cookies=url, desktop=True) as page:
            page.on("response", on_response)
            page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
            page.wait_for_timeout(_SETTLE_WAIT_MS)
            final_url = page.url
            page_title = page.title()
            body_text = self.page_body_text(page)
            self._capture_initial_state(page, captured)

        article_id = static_id or _static_note_id(final_url) or _short_link_code(url)
        walls = detect_walls(final_url, body_text, expected_id=static_id)
        xhr = self._xhr_counts(captured, article_id)
        dom = extract_metrics_from_text(body_text) if not walls else {}
        return self._build_metrics(
            article_id, url, final_url, page_title, dom, xhr, captured, body_text, walls
        )

    # --- 플랫폼 고유부 -----------------------------------------------------

    @staticmethod
    def _capture_initial_state(page: Page, captured: dict[str, object]) -> None:
        """SSR 초기상태 JSON 을 json_bodies 에 합류시킨다 (카운트의 두 번째 소스)."""
        try:
            state = page.evaluate(
                "() => { try { return JSON.stringify(window.__INITIAL_STATE__ || null); }"
                " catch (e) { return null; } }"
            )
        except Exception as exc:
            logger.debug("__INITIAL_STATE__ 읽기 실패: %s", exc)
            return
        if state and state != "null":
            bodies = captured.setdefault("json_bodies", [])
            assert isinstance(bodies, list)
            bodies.append(state[:_XHR_BODY_MAX_LEN])

    def _xhr_counts(self, captured: dict[str, object], article_id: str) -> dict[str, int]:
        """수집한 JSON 들에서 타깃 노트 서브트리를 찾아 카운트를 병합한다.

        피드 응답의 추천 노트 카운트 오염을 막기 위해 id 매칭 서브트리 안에서만 찾는다.
        """
        result: dict[str, int] = {}
        bodies = captured.get("json_bodies")
        if not isinstance(bodies, list):
            return result
        for body in bodies:
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                continue
            subtree = find_subtree_by_id(data, _ID_KEYS, article_id)
            if subtree is None:
                continue
            for field, value in find_counts(subtree, self.xhr_count_keys).items():
                result.setdefault(field, value)
        return result

    # --- 병합 -------------------------------------------------------------

    @classmethod
    def _build_metrics(
        cls,
        article_id: str,
        url: str,
        final_url: str,
        page_title: str,
        dom: dict[str, int],
        xhr: dict[str, int],
        captured: dict[str, object],
        body_text: str,
        walls: dict[str, bool],
    ) -> Metrics:
        if walls:
            logger.warning("샤오홍슈 차단 감지 %s — 지표 없이 진단만 기록: %s", walls, url)

        raw = json.dumps(
            {
                **walls,
                "captured_json": len(captured.get("json_bodies") or []),
                "final_url": final_url[:200],
                "dom": dom,
                "xhr": xhr,
                "page_title": page_title,
                "body_head": body_text[:200],
            },
            ensure_ascii=False,
        )[:2000]

        return Metrics(
            platform="xiaohongshu",
            article_id=article_id,
            url=url,
            title=page_title or None,
            likes=cls.pick_metric(xhr, dom, "likes"),
            collects=cls.pick_metric(xhr, dom, "collects"),
            comments=cls.pick_metric(xhr, dom, "comments"),
            shares=cls.pick_metric(xhr, dom, "shares"),
            raw=raw,
        )


def _static_note_id(url: str) -> str | None:
    """URL 경로/쿼리에서 노트 id 를 찾는다. 없으면 None."""
    parsed = urlparse(url)
    match = _NOTE_ID_PATTERN.search(parsed.path)
    if match:
        return match.group(1)
    query = parse_qs(parsed.query)
    for key in _QUERY_ID_KEYS:
        values = query.get(key)
        if values and values[0]:
            return values[0]
    return None


def _is_short_link(url: str) -> bool:
    hostname = urlparse(url).hostname or ""
    return hostname == _SHORT_LINK_HOST or hostname.endswith("." + _SHORT_LINK_HOST)


def _short_link_code(url: str) -> str:
    """단축링크가 차단 페이지로 튕겨 id 해석이 불가하면 단축코드를 식별자로 쓴다."""
    path = urlparse(url).path.strip("/")
    if not path:
        raise ValueError(f"URL 에서 샤오홍슈 노트 id 를 찾을 수 없습니다: {url}")
    return f"xhslink:{path}"


def detect_walls(
    final_url: str, body_text: str, expected_id: str | None = None
) -> dict[str, bool]:
    """최종 URL·본문 텍스트로 보안월/캡차월/로그인월/앱월을 감지한다.

    expected_id 가 있는데 최종 URL 에서 사라졌다면(노트 대신 홈/로그인 화면으로
    리다이렉트) redirected_away 로 기록한다 — 실측된 차단 형태.
    """
    walls: dict[str, bool] = {}
    if any(marker in final_url for marker in _SECURITY_URL_MARKERS):
        walls["security_wall"] = True
    if _CAPTCHA_URL_MARKER in final_url:
        walls["captcha_wall"] = True
    elif _LOGIN_URL_MARKER in final_url or any(
        marker in body_text for marker in _LOGIN_WALL_TEXTS
    ):
        walls["login_wall"] = True
    if _APP_WALL_TEXT in body_text:
        walls["app_wall"] = True
    if expected_id and expected_id not in final_url:
        walls["redirected_away"] = True
    return walls


def extract_metrics_from_text(text: str) -> dict[str, int]:
    """렌더된 본문 텍스트에서 지표를 정규식으로 추출한다."""
    return extract_labeled_counts(text, _DOM_PATTERNS)
