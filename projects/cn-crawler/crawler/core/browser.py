"""Playwright 헤드리스 브라우저 세션.

실제 페이지를 렌더링해 DOM 을 읽거나 XHR 응답을 가로챈다.
플랫폼 서명 알고리즘은 리버싱하지 않고 브라우저가 대신 계산하게 둔다.

가벼운 stealth 기본값(자동화 탐지 완화)과 쿠키/프록시 주입을 담당한다.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

# 실제 모바일 브라우저에 가까운 UA (씨트립 m. 페이지는 모바일 뷰)
_MOBILE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)
_MOBILE_VIEWPORT = {"width": 390, "height": 844}

# navigator.webdriver 등 흔한 자동화 신호를 지우는 최소 stealth 스크립트
_STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
Object.defineProperty(navigator, 'platform', {get: () => 'iPhone'});
window.chrome = window.chrome || {runtime: {}};
"""


def _parse_cookie_header(cookie_str: str, url: str) -> list[dict]:
    """"k1=v1; k2=v2" 형태의 쿠키 문자열을 Playwright 쿠키 목록으로 변환한다."""
    from urllib.parse import urlparse

    domain = urlparse(url).hostname or ""
    cookies = []
    for part in cookie_str.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        cookies.append(
            {"name": name.strip(), "value": value.strip(), "domain": domain, "path": "/"}
        )
    return cookies


class BrowserSession:
    """페이지를 열어주는 얇은 컨텍스트 매니저.

    쿠키/프록시는 환경변수(CRAWLER_COOKIES, CRAWLER_PROXY)에서 읽는다.
    """

    def __init__(self, headless: bool = True) -> None:
        self.headless = headless
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    def __enter__(self) -> "BrowserSession":
        self._playwright = sync_playwright().start()
        launch_kwargs: dict = {"headless": self.headless}
        proxy = os.environ.get("CRAWLER_PROXY")
        if proxy:
            launch_kwargs["proxy"] = {"server": proxy}
        self._browser = self._playwright.chromium.launch(**launch_kwargs)
        self._context = self._browser.new_context(
            user_agent=_MOBILE_USER_AGENT,
            viewport=_MOBILE_VIEWPORT,
            locale="zh-CN",
            is_mobile=True,
            has_touch=True,
        )
        self._context.add_init_script(_STEALTH_SCRIPT)
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._context:
            self._context.close()
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    @contextmanager
    def page(self, url_for_cookies: str | None = None) -> Iterator[Page]:
        """새 페이지를 연다. 쿠키가 설정돼 있으면 해당 URL 도메인에 주입한다."""
        if self._context is None:
            raise RuntimeError("BrowserSession must be used as a context manager")
        cookie_str = os.environ.get("CRAWLER_COOKIES")
        if cookie_str and url_for_cookies:
            self._context.add_cookies(_parse_cookie_header(cookie_str, url_for_cookies))
        page = self._context.new_page()
        try:
            yield page
        finally:
            page.close()
