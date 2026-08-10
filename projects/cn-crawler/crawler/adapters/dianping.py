"""따종디엔핑(大众点评) 노트/샵 페이지 어댑터.

정찰 결과 (2026-08, 헤드리스·비중국 IP·비로그인):
- 홈(m./www.)은 렌더되고 피드 XHR(growthlistfeeds)에 노트 contentId·제목·카운트가 보인다.
  홈 피드의 숫자는 평문이었다(PUA 난독화 아님).
- 노트 상세(m.dianping.com/ugcdetail/<id>)는 SMS 로그인월(maccount.dianping.com)로,
  목록/샵 페이지는 메이투안 verify(verify.meituan.com, 아이콘 클릭 캡차)로 리다이렉트된다.
  캡차 자동 돌파·로그인 우회는 하지 않는다.
- 디엔핑은 로그인 후 페이지에서 숫자를 커스텀 폰트(사설영역 PUA 글리프)로 난독화한
  이력이 있다. 이 환경에선 렌더된 숫자에 도달할 수 없어 OCR/폰트맵 디코드는 검증
  불가능한 코드가 되므로 넣지 않았다. 대신 지표 라벨(点赞/收藏/…) 인접에서 PUA
  글리프를 감지해 raw 에 기록한다(font_obfuscation_detected) — 아이콘 폰트(iconfont)도
  PUA 를 쓰므로 본문 전체가 아니라 라벨 주변만 검사해 오탐을 줄인다.
  Phase 3 에서 폰트맵/OCR 를 붙일 확장 지점.

동작: 로그인월/verify 감지 시 None + raw 진단으로 degrade.
정상 렌더 환경(중국 IP·유효 쿠키)에서는 DOM 텍스트 정규식 + XHR 카운트 키 탐색을 병합.
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Response

from crawler.adapters.base import (
    Adapter,
    UnsupportedUrlError,
    extract_labeled_counts,
    navigate_and_settle,
)
from crawler.core.schema import Metrics

logger = logging.getLogger(__name__)

_SETTLE_WAIT_MS = 6_000

# 노트 상세 /ugcdetail/3099928252, 피드 상세 /feeddetail/3099928252 (같은 숫자 id
# 체계 — 실사용 시트 URL 에서 관측, 정찰 결과 SMS 로그인월로 리다이렉트되므로
# detect_walls 가 degrade 처리), 샵 페이지 /shop/H8gTDqYy (영숫자 암호화 id)
_UGC_ID_PATTERN = re.compile(r"/ugcdetail/(\d+)")
_FEED_ID_PATTERN = re.compile(r"/feeddetail/(\d+)")
_SHOP_ID_PATTERN = re.compile(r"/shop/([A-Za-z0-9]+)")
# 정찰에서 관측된 쿼리 키만 (feed XHR 의 contentId). 광범위한 "id" 폴백은
# 무관한 URL 을 잘못 받아들이므로 두지 않는다.
_QUERY_ID_KEYS = ("contentId",)

_LOGIN_WALL_MARKERS = ("maccount.dianping.com", "/mlogin/")
_VERIFY_WALL_MARKER = "verify.meituan.com"

# 지표 라벨. 폰트 난독화 검사는 이 라벨들 주변에서만 한다.
_METRIC_LABEL_PATTERN = re.compile(r"点赞|收藏|浏览|阅读|评论|评价|赞")
# 사설영역(Private Use Area) 글리프 = 커스텀 폰트 숫자 난독화 신호
_PUA_PATTERN = re.compile("[\ue000-\uf8ff]")
# 라벨 앞뒤로 이 거리(문자) 안에서 PUA 가 보이면 난독화로 판정
_PUA_WINDOW = 8

# 렌더된 본문 텍스트 패턴 (라벨→숫자 먼저, 숫자→라벨은 줄을 넘지 않게)
_DOM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("views", re.compile(r"(?:浏览|阅读)\s*[:：]?\s*(\d[\d.,]*\s*[万亿]?)")),
    ("likes", re.compile(r"(?:点赞|赞)\s*[:：]?\s*(\d[\d.,]*\s*[万亿]?)")),
    ("collects", re.compile(r"收藏\s*[:：]?\s*(\d[\d.,]*\s*[万亿]?)")),
    ("comments", re.compile(r"(?:评论|评价)\s*[(（]?\s*(\d[\d.,]*\s*[万亿]?)")),
    ("views", re.compile(r"(\d[\d.,]*[^\S\n]*[万亿]?)[^\S\n]*(?:人|次)?[^\S\n]*(?:浏览|阅读)")),
    ("likes", re.compile(r"(\d[\d.,]*[^\S\n]*[万亿]?)[^\S\n]*(?:人)?[^\S\n]*(?:点赞|赞)")),
    ("collects", re.compile(r"(\d[\d.,]*[^\S\n]*[万亿]?)[^\S\n]*(?:人)?[^\S\n]*收藏")),
    ("comments", re.compile(r"(\d[\d.,]*[^\S\n]*[万亿]?)[^\S\n]*条?[^\S\n]*(?:评论|评价)")),
)


class DianpingAdapter(Adapter):
    platform = "dianping"

    xhr_count_keys = {
        "likeCount": "likes",
        "voteCount": "likes",
        "collectCount": "collects",
        "favCount": "collects",
        "favorCount": "collects",
        "viewCount": "views",
        "readCount": "views",
        "browseCount": "views",
        "commentCount": "comments",
        "reviewCount": "comments",
        "shareCount": "shares",
        "followerCount": "followers",
        "fansCount": "followers",
    }

    def parse_article_id(self, url: str) -> str:
        """/ugcdetail/<id>, /feeddetail/<id>, /shop/<id>, contentId 쿼리에서 id 를 파싱한다."""
        parsed = urlparse(url)
        for pattern in (_UGC_ID_PATTERN, _FEED_ID_PATTERN, _SHOP_ID_PATTERN):
            match = pattern.search(parsed.path)
            if match:
                return match.group(1)
        query = parse_qs(parsed.query)
        for key in _QUERY_ID_KEYS:
            values = query.get(key)
            if values and values[0]:
                return values[0]
        raise UnsupportedUrlError(f"URL 에서 디엔핑 게시물/샵 id 를 찾을 수 없습니다: {url}")

    def collect(self, url: str) -> Metrics:
        article_id = self.parse_article_id(url)
        captured: dict[str, object] = {"json_bodies": []}

        def on_response(resp: Response) -> None:
            self.capture_count_json(resp, captured)

        with self.session.page(url_for_cookies=url) as page:
            page.on("response", on_response)
            navigate_and_settle(page, url, _SETTLE_WAIT_MS)
            final_url = page.url
            page_title = page.title()
            body_text = self.page_body_text(page)

        walls = detect_walls(final_url)
        xhr = self.parse_captured_counts(captured)
        dom = extract_metrics_from_text(body_text) if not walls else {}
        return self._build_metrics(
            article_id, url, final_url, page_title, dom, xhr, captured, body_text, walls
        )

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
        obfuscated = detect_font_obfuscation(body_text)
        if walls:
            logger.warning("디엔핑 차단 감지 %s — 지표 없이 진단만 기록: %s", walls, url)
        if obfuscated:
            logger.warning("디엔핑 폰트 난독화(PUA 글리프) 감지 — DOM 숫자 신뢰 불가: %s", url)

        raw = json.dumps(
            {
                **walls,
                "font_obfuscation_detected": obfuscated,
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
            platform="dianping",
            article_id=article_id,
            url=url,
            title=page_title or None,
            views=cls.pick_metric(xhr, dom, "views"),
            likes=cls.pick_metric(xhr, dom, "likes"),
            collects=cls.pick_metric(xhr, dom, "collects"),
            comments=cls.pick_metric(xhr, dom, "comments"),
            shares=cls.pick_metric(xhr, dom, "shares"),
            followers=xhr.get("followers"),
            raw=raw,
        )


def detect_walls(final_url: str) -> dict[str, bool]:
    """리다이렉트된 최종 URL 로 로그인월/verify 차단을 감지한다."""
    walls: dict[str, bool] = {}
    if any(marker in final_url for marker in _LOGIN_WALL_MARKERS):
        walls["login_wall"] = True
    if _VERIFY_WALL_MARKER in final_url:
        walls["verify_wall"] = True
    return walls


def detect_font_obfuscation(text: str) -> bool:
    """지표 라벨 주변(_PUA_WINDOW 문자)에서만 PUA 글리프를 찾는다.

    아이콘 폰트(iconfont)도 PUA 영역을 쓰므로 본문 전체 검사는 정상 페이지를
    오탐한다. 숫자가 난독화됐다면 라벨 바로 옆에 글리프가 있을 것이다.
    """
    for match in _METRIC_LABEL_PATTERN.finditer(text):
        window = text[max(0, match.start() - _PUA_WINDOW): match.end() + _PUA_WINDOW]
        if _PUA_PATTERN.search(window):
            return True
    return False


def extract_metrics_from_text(text: str) -> dict[str, int]:
    """렌더된 본문 텍스트에서 지표를 정규식으로 추출한다.

    PUA 글리프로 난독화된 숫자는 \\d 에 걸리지 않으므로 자연스럽게 제외된다.
    """
    return extract_labeled_counts(text, _DOM_PATTERNS)
