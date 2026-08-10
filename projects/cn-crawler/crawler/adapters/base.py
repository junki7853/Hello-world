"""어댑터 계약.

플랫폼 차이는 어댑터 안에만 격리한다.
내부 흐름: navigate → (셀렉터 대기 | XHR 응답 가로채기) → 추출 → 정규화.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from crawler.core.browser import BrowserSession
from crawler.core.schema import Metrics


class Adapter(ABC):
    """플랫폼별 수집기 인터페이스."""

    #: 이 어댑터가 처리하는 플랫폼 이름 (targets.csv 의 platform 열과 일치)
    platform: str = ""

    def __init__(self, session: BrowserSession) -> None:
        self.session = session

    @abstractmethod
    def parse_article_id(self, url: str) -> str:
        """URL 에서 게시물 id 를 파싱한다."""

    @abstractmethod
    def collect(self, url: str) -> Metrics:
        """URL 한 건을 수집해 정규화된 Metrics 로 반환한다."""
