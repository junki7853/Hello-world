"""어댑터 계약 + 어댑터 공통 헬퍼.

플랫폼 차이는 어댑터 안에만 격리한다.
내부 흐름: navigate → (셀렉터 대기 | XHR 응답 가로채기) → 추출 → 정규화.

여러 어댑터가 똑같이 쓰는 로직(XHR JSON 수집, 카운트 키 BFS 탐색, body 텍스트,
XHR→DOM 폴백 병합)은 여기서 공유하고, 어댑터에는 플랫폼 고유부(키맵·패턴·
캡차/로그인월 감지)만 남긴다.
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod

from playwright.sync_api import Page, Response

from crawler.core.browser import BrowserSession
from crawler.core.schema import Metrics, parse_count

logger = logging.getLogger(__name__)


class Adapter(ABC):
    """플랫폼별 수집기 인터페이스."""

    #: 이 어댑터가 처리하는 플랫폼 이름 (targets.csv 의 platform 열과 일치)
    platform: str = ""

    #: XHR JSON 에서 찾을 카운트 키 → Metrics 필드. 어댑터가 재정의한다.
    xhr_count_keys: dict[str, str] = {}

    def __init__(self, session: BrowserSession) -> None:
        self.session = session

    @abstractmethod
    def parse_article_id(self, url: str) -> str:
        """URL 에서 게시물 id 를 파싱한다."""

    @abstractmethod
    def collect(self, url: str) -> Metrics:
        """URL 한 건을 수집해 정규화된 Metrics 로 반환한다."""

    # --- 공통 헬퍼 --------------------------------------------------------

    def capture_count_json(
        self, resp: Response, captured: dict[str, object], max_len: int = 20_000
    ) -> None:
        """카운트 키가 들어있을 법한 JSON XHR 본문을 captured["json_bodies"] 에 모은다.

        max_len 을 넘는 본문은 잘린다 — 잘린 JSON 은 파싱 단계에서 자연히 무시되므로,
        큰 상세 응답(도우인 detail 등)을 쓰는 어댑터는 max_len 을 키워서 호출한다.
        """
        if "json" not in resp.headers.get("content-type", ""):
            return
        try:
            body = resp.text()
        except Exception as exc:  # 응답이 이미 사라졌거나 바이너리인 경우
            logger.debug("XHR 본문 읽기 실패 %s: %s", resp.url[:80], exc)
            return
        if any(key in body for key in self.xhr_count_keys):
            bodies = captured.setdefault("json_bodies", [])
            assert isinstance(bodies, list)
            bodies.append(body[:max_len])

    def parse_captured_counts(self, captured: dict[str, object]) -> dict[str, int]:
        """모아둔 XHR JSON 들에서 카운트 키를 탐색해 병합한다. 먼저 찾은 값 우선."""
        result: dict[str, int] = {}
        bodies = captured.get("json_bodies")
        if not isinstance(bodies, list):
            return result
        for body in bodies:
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                continue
            for field, value in find_counts(data, self.xhr_count_keys).items():
                result.setdefault(field, value)
        return result

    @staticmethod
    def page_body_text(page: Page) -> str:
        """렌더된 body 텍스트를 읽는다 (실패 시 빈 문자열)."""
        try:
            return page.inner_text("body")[:50_000]
        except Exception as exc:
            logger.debug("body 텍스트 읽기 실패: %s", exc)
            return ""

    @staticmethod
    def pick_metric(xhr: dict, dom: dict, key: str) -> int | None:
        """XHR 값 우선, 없으면 DOM 값으로 폴백한다."""
        xhr_val = xhr.get(key)
        if isinstance(xhr_val, int):
            return xhr_val
        dom_val = dom.get(key)
        return dom_val if isinstance(dom_val, int) else None


def find_counts(obj: object, key_map: dict[str, str]) -> dict[str, int]:
    """중첩 JSON 에서 key_map 의 카운트 키를 BFS 로 찾는다. 먼저 찾은 값을 채택.

    값이 "1.2万" 같은 문자열이면 parse_count 로 정수화한다.
    """
    found: dict[str, int] = {}
    queue: list[object] = [obj]
    while queue:
        current = queue.pop(0)
        if isinstance(current, dict):
            for src, dst in key_map.items():
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


def find_subtree_by_id(
    obj: object, id_keys: tuple[str, ...], target_id: str
) -> dict | None:
    """중첩 JSON 에서 id 키가 target_id 와 일치하는 dict 서브트리를 BFS 로 찾는다.

    피드/추천 응답에는 다른 게시물의 카운트도 섞여 있으므로(씨트립 relatedRecommend,
    도우인 tab/feed 에서 실측), 카운트 탐색 전에 타깃 게시물의 서브트리로 먼저
    좁힐 때 쓴다. 못 찾으면 None — 호출부는 오염된 값을 쓰지 말고 포기해야 한다.
    """
    queue: list[object] = [obj]
    while queue:
        current = queue.pop(0)
        if isinstance(current, dict):
            for key in id_keys:
                value = current.get(key)
                if value is not None and str(value) == target_id:
                    return current
            queue.extend(current.values())
        elif isinstance(current, list):
            queue.extend(current)
    return None


def extract_labeled_counts(
    text: str, patterns: tuple[tuple[str, re.Pattern[str]], ...]
) -> dict[str, int]:
    """렌더된 본문 텍스트에서 (필드, 패턴) 목록으로 지표를 추출한다.

    같은 필드는 먼저 매칭된 패턴이 이긴다 — 라벨→숫자 패턴을 앞에 두어
    숫자→라벨 패턴이 앞 지표의 숫자를 훔치는 오매칭을 막는 관례를 따른다.
    """
    out: dict[str, int] = {}
    for field, pattern in patterns:
        if field in out:
            continue
        match = pattern.search(text)
        if match:
            value = parse_count(match.group(1))
            if value is not None:
                out[field] = value
    return out
