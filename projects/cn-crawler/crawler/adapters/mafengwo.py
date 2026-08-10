"""마펑워(马蜂窝) 게시물(游记/攻略) 상세 페이지 어댑터.

정찰 결과 (2026-08, 헤드리스·비중국 IP·비로그인):
- 홈/목록 페이지는 정상 렌더되지만, 게시물 상세(`/i/<id>.html`)는 데스크톱·모바일
  모두 텐센트 슬라이드 캡차(t.captcha.qq.com)로 차단된다. 캡차 자동 돌파는 하지 않는다.
- 과거 공개 pagelet JSON 엔드포인트(headOperateApi 등)는 404 로 제거됐다.
- 상세가 networkidle 에 도달하지 않아(지속 폴링) domcontentloaded + 고정 대기를 쓴다.

따라서 세 갈래로 최선껏 수집하고, 캡차에 막히면 None + raw 진단으로 degrade 한다:
  1) XHR 가로채기 — JSON 응답에서 알려진 카운트 키(vote_num/reply_num/…)를 탐색.
  2) DOM 텍스트 정규식 — 정상 렌더 환경(중국 IP/유효 쿠키)에서 "浏览 N"/"顶 N" 등.
     클래스 셀렉터 대신 텍스트 패턴이라 마크업 변경에 상대적으로 강하다.
  3) 캡차 감지 — t.captcha.qq.com 리소스가 잡히면 raw 에 captcha_detected 로 기록.
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Page, Response

from crawler.adapters.base import Adapter
from crawler.core.schema import Metrics, parse_count

logger = logging.getLogger(__name__)

_NAV_TIMEOUT_MS = 60_000
_SETTLE_WAIT_MS = 6_000

# /i/24867879.html 또는 쿼리 iid=24867879
_PATH_ID_PATTERN = re.compile(r"/i/(\d+)\.html")

_CAPTCHA_MARKER = "captcha.qq.com"

# XHR JSON 에서 찾을 카운트 키 → Metrics 필드
_XHR_COUNT_KEYS = {
    "vote_num": "likes",
    "like_num": "likes",
    "fav_num": "collects",
    "collect_num": "collects",
    "reply_num": "comments",
    "comment_num": "comments",
    "browse_num": "views",
    "read_num": "views",
    "share_num": "shares",
}

# 렌더된 본문 텍스트에서 지표를 찾는 패턴.
# 라벨→숫자 패턴을 먼저 시도한다: 숫자→라벨 패턴이 앞 지표의 숫자를 훔치는
# 오매칭("阅读: 3400  赞" 에서 likes=3400)을 막기 위해서다.
# 숫자→라벨 패턴의 구분자는 [^\S\n](개행 제외 공백)로 제한해 줄을 넘지 않는다.
_DOM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("views", re.compile(r"(?:浏览|阅读)\s*[:：]?\s*(\d[\d.,]*\s*[万亿]?)")),
    ("likes", re.compile(r"[顶赞]\s*[:：]?\s*(\d[\d.,]*\s*[万亿]?)")),
    ("collects", re.compile(r"收藏\s*[:：]?\s*(\d[\d.,]*\s*[万亿]?)")),
    ("comments", re.compile(r"(?:蜂评|回复|评论)\s*[(（]?\s*(\d[\d.,]*\s*[万亿]?)")),
    ("views", re.compile(r"(\d[\d.,]*[^\S\n]*[万亿]?)[^\S\n]*(?:人|次)?[^\S\n]*(?:浏览|阅读)")),
    ("likes", re.compile(r"(\d[\d.,]*[^\S\n]*[万亿]?)[^\S\n]*[人个]?[^\S\n]*[顶赞]")),
    ("collects", re.compile(r"(\d[\d.,]*[^\S\n]*[万亿]?)[^\S\n]*[人个]?[^\S\n]*收藏")),
    ("comments", re.compile(r"(\d[\d.,]*[^\S\n]*[万亿]?)[^\S\n]*条?[^\S\n]*(?:蜂评|回复|评论)")),
)

_UPLOAD_DATE_PATTERN = re.compile(r"发表于\s*(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})")


class MafengwoAdapter(Adapter):
    platform = "mafengwo"

    def parse_article_id(self, url: str) -> str:
        """경로 /i/<id>.html 또는 쿼리 iid 에서 게시물 id 를 파싱한다."""
        parsed = urlparse(url)
        match = _PATH_ID_PATTERN.search(parsed.path)
        if match:
            return match.group(1)
        values = parse_qs(parsed.query).get("iid")
        if values and values[0]:
            return values[0]
        raise ValueError(f"URL 에서 마펑워 게시물 id 를 찾을 수 없습니다: {url}")

    def collect(self, url: str) -> Metrics:
        article_id = self.parse_article_id(url)
        captured: dict[str, object] = {"json_bodies": [], "captcha": False}

        def on_response(resp: Response) -> None:
            self._capture_xhr(resp, captured)

        with self.session.page(url_for_cookies=url) as page:
            page.on("response", on_response)
            # 상세는 폴링 때문에 networkidle 에 도달하지 않는다 → 고정 대기
            page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
            page.wait_for_timeout(_SETTLE_WAIT_MS)
            page_title = page.title()
            body_text = self._page_body_text(page)

        xhr = self._parse_captured(captured)
        dom = extract_metrics_from_text(body_text)
        return self._build_metrics(
            article_id, url, page_title, dom, xhr, captured, body_text
        )

    # --- XHR 경로 ---------------------------------------------------------

    @staticmethod
    def _capture_xhr(resp: Response, captured: dict[str, object]) -> None:
        url = resp.url
        if _CAPTCHA_MARKER in url:
            captured["captcha"] = True
            return
        if "json" not in resp.headers.get("content-type", ""):
            return
        try:
            body = resp.text()
        except Exception as exc:
            logger.debug("XHR 본문 읽기 실패 %s: %s", url[:80], exc)
            return
        # 카운트 키가 들어있을 법한 응답만 저장 (진단 부담 축소)
        if any(key in body for key in _XHR_COUNT_KEYS):
            bodies = captured["json_bodies"]
            assert isinstance(bodies, list)
            bodies.append(body[:20_000])

    @staticmethod
    def _parse_captured(captured: dict[str, object]) -> dict[str, int]:
        """저장된 XHR JSON 들에서 알려진 카운트 키를 탐색한다."""
        result: dict[str, int] = {}
        bodies = captured.get("json_bodies")
        if not isinstance(bodies, list):
            return result
        for body in bodies:
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                continue
            found = _find_counts(data)
            for field, value in found.items():
                result.setdefault(field, value)
        return result

    # --- DOM 경로 ---------------------------------------------------------

    @staticmethod
    def _page_body_text(page: Page) -> str:
        try:
            return page.inner_text("body")[:50_000]
        except Exception as exc:
            logger.debug("body 텍스트 읽기 실패: %s", exc)
            return ""

    # --- 병합 -------------------------------------------------------------

    @staticmethod
    def _build_metrics(
        article_id: str,
        url: str,
        page_title: str,
        dom: dict[str, object],
        xhr: dict[str, int],
        captured: dict[str, object],
        body_text: str,
    ) -> Metrics:
        def pick(key: str) -> int | None:
            xhr_val = xhr.get(key)
            if isinstance(xhr_val, int):
                return xhr_val
            dom_val = dom.get(key)
            return dom_val if isinstance(dom_val, int) else None

        captcha = bool(captured.get("captcha"))
        if captcha:
            logger.warning("마펑워 캡차 감지 — 지표 없이 진단만 기록: %s", url)

        upload_date = dom.get("upload_date")
        raw = json.dumps(
            {
                "captcha_detected": captcha,
                "captured_json": len(captured.get("json_bodies") or []),
                "dom": {k: v for k, v in dom.items() if k != "upload_date"},
                "xhr": xhr,
                "page_title": page_title,
                "body_head": body_text[:200],
            },
            ensure_ascii=False,
        )[:2000]

        return Metrics(
            platform="mafengwo",
            article_id=article_id,
            url=url,
            title=page_title or None,
            views=pick("views"),
            likes=pick("likes"),
            collects=pick("collects"),
            comments=pick("comments"),
            shares=pick("shares"),
            upload_date=upload_date if isinstance(upload_date, str) else None,
            raw=raw,
        )


def extract_metrics_from_text(text: str) -> dict[str, object]:
    """렌더된 본문 텍스트에서 지표·업로드일을 정규식으로 추출한다."""
    out: dict[str, object] = {}
    for field, pattern in _DOM_PATTERNS:
        if field in out:
            continue
        match = pattern.search(text)
        if match:
            value = parse_count(match.group(1))
            if value is not None:
                out[field] = value
    date_match = _UPLOAD_DATE_PATTERN.search(text)
    if date_match:
        out["upload_date"] = date_match.group(1).replace("/", "-").replace(".", "-")
    return out


def _find_counts(obj: object) -> dict[str, int]:
    """중첩 JSON 에서 알려진 카운트 키를 BFS 로 찾는다. 먼저 찾은 값을 채택."""
    found: dict[str, int] = {}
    queue: list[object] = [obj]
    while queue:
        current = queue.pop(0)
        if isinstance(current, dict):
            for src, dst in _XHR_COUNT_KEYS.items():
                value = current.get(src)
                if dst not in found:
                    if isinstance(value, int):
                        found[dst] = value
                    elif isinstance(value, str):
                        parsed = parse_count(value)
                        if parsed is not None:
                            found[dst] = parsed
            queue.extend(current.values())
        elif isinstance(current, list):
            queue.extend(current)
    return found
