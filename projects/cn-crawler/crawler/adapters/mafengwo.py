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

from playwright.sync_api import Response

from crawler.adapters.base import Adapter, extract_labeled_counts, navigate_and_settle
from crawler.core.schema import Metrics

logger = logging.getLogger(__name__)

_SETTLE_WAIT_MS = 6_000

# /i/24867879.html 또는 쿼리 iid=24867879
_PATH_ID_PATTERN = re.compile(r"/i/(\d+)\.html")

_CAPTCHA_MARKER = "captcha.qq.com"

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

    xhr_count_keys = {
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
            # 플랫폼 고유부: 캡차 리소스 감지 후 공통 JSON 수집으로 위임
            if _CAPTCHA_MARKER in resp.url:
                captured["captcha"] = True
                return
            self.capture_count_json(resp, captured)

        with self.session.page(url_for_cookies=url) as page:
            page.on("response", on_response)
            navigate_and_settle(page, url, _SETTLE_WAIT_MS)
            page_title = page.title()
            body_text = self.page_body_text(page)

        xhr = self.parse_captured_counts(captured)
        dom = extract_metrics_from_text(body_text)
        return self._build_metrics(
            article_id, url, page_title, dom, xhr, captured, body_text
        )

    # --- 병합 -------------------------------------------------------------

    @classmethod
    def _build_metrics(
        cls,
        article_id: str,
        url: str,
        page_title: str,
        dom: dict[str, object],
        xhr: dict[str, int],
        captured: dict[str, object],
        body_text: str,
    ) -> Metrics:
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
            views=cls.pick_metric(xhr, dom, "views"),
            likes=cls.pick_metric(xhr, dom, "likes"),
            collects=cls.pick_metric(xhr, dom, "collects"),
            comments=cls.pick_metric(xhr, dom, "comments"),
            shares=cls.pick_metric(xhr, dom, "shares"),
            upload_date=upload_date if isinstance(upload_date, str) else None,
            raw=raw,
        )


def extract_metrics_from_text(text: str) -> dict[str, object]:
    """렌더된 본문 텍스트에서 지표·업로드일을 정규식으로 추출한다."""
    out: dict[str, object] = dict(extract_labeled_counts(text, _DOM_PATTERNS))
    date_match = _UPLOAD_DATE_PATTERN.search(text)
    if date_match:
        out["upload_date"] = date_match.group(1).replace("/", "-").replace(".", "-")
    return out
