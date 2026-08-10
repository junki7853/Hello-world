"""Playwright 헤드리스 브라우저 세션.

실제 페이지를 렌더링해 DOM 을 읽거나 XHR 응답을 가로챈다.
플랫폼 서명 알고리즘은 리버싱하지 않고 브라우저가 대신 계산하게 둔다.

가벼운 stealth 기본값(자동화 탐지 완화)과 쿠키/프록시 주입을 담당한다.
"""

from __future__ import annotations

import os
from contextlib import contextmanager, suppress
from typing import Iterator
from urllib.parse import urlparse

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

# 국가코드 TLD 밑에서 흔히 쓰는 2차 접미사 (xxx.com.cn 형태 처리용)
_SECOND_LEVEL_SUFFIXES = {"com", "net", "org", "gov", "edu", "co"}

# 실제 모바일 브라우저에 가까운 UA (씨트립 m. 페이지는 모바일 뷰)
_MOBILE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)
_MOBILE_VIEWPORT = {"width": 390, "height": 844}

# 데스크톱 UA (도우인·샤오홍슈 웹은 데스크톱 뷰가 표준 — 모바일 UA 는 앱 유도월로 빠진다)
_DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_DESKTOP_VIEWPORT = {"width": 1440, "height": 900}

# navigator.webdriver 등 흔한 자동화 신호를 지우는 최소 stealth 스크립트
_STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
Object.defineProperty(navigator, 'platform', {get: () => 'iPhone'});
window.chrome = window.chrome || {runtime: {}};
"""


def _registrable_domain(hostname: str) -> str:
    """서브도메인 XHR 에도 쿠키가 전송되도록 상위 등록도메인을 최선껏 계산한다.

    'm.ctrip.com' -> '.ctrip.com', 'www.mafengwo.com.cn' -> '.mafengwo.com.cn'.
    전체 Public Suffix List 없이 보수적 휴리스틱만 쓴다: 판단이 애매하면
    (라벨 부족·IP 주소) 정확 호스트를 그대로 반환해 과도한 확장을 피한다.
    """
    labels = hostname.split(".")
    if len(labels) < 2 or any(label.isdigit() for label in labels):
        return hostname  # 단일 라벨(localhost)·IP 등 애매하면 정확 호스트 유지
    # xxx.com.cn 처럼 2차 접미사 + 2글자 ccTLD 면 마지막 3라벨을 등록도메인으로
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL_SUFFIXES and len(labels[-1]) == 2:
        base = ".".join(labels[-3:])
    else:
        base = ".".join(labels[-2:])
    return "." + base


def _parse_cookie_header(cookie_str: str, url: str) -> list[dict]:
    """"k1=v1; k2=v2" 형태의 쿠키 문자열을 Playwright 쿠키 목록으로 변환한다."""
    hostname = urlparse(url).hostname or ""
    domain = _registrable_domain(hostname) if hostname else ""
    cookies = []
    for part in cookie_str.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        if not name:  # "=nokey" 처럼 이름 없는 항목은 건너뛴다
            continue
        cookies.append(
            {"name": name, "value": value.strip(), "domain": domain, "path": "/"}
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
        self._desktop_context: BrowserContext | None = None

    def __enter__(self) -> "BrowserSession":
        self._playwright = sync_playwright().start()
        try:
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
        except Exception:
            # 부분 초기화 롤백: 열린 자원을 정리하고 예외를 다시 던진다
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc_info: object) -> None:
        # 앞 단계에서 예외가 나도 뒤 정리가 반드시 실행되도록 각각 격리한다
        if self._desktop_context:
            with suppress(Exception):
                self._desktop_context.close()
            self._desktop_context = None
        if self._context:
            with suppress(Exception):
                self._context.close()
            self._context = None
        if self._browser:
            with suppress(Exception):
                self._browser.close()
            self._browser = None
        if self._playwright:
            with suppress(Exception):
                self._playwright.stop()
            self._playwright = None

    def _get_desktop_context(self) -> BrowserContext:
        """데스크톱 뷰 컨텍스트를 첫 사용 시 만들어 재사용한다."""
        if self._desktop_context is None:
            assert self._browser is not None
            self._desktop_context = self._browser.new_context(
                user_agent=_DESKTOP_USER_AGENT,
                viewport=_DESKTOP_VIEWPORT,
                locale="zh-CN",
            )
            self._desktop_context.add_init_script(_STEALTH_SCRIPT)
        return self._desktop_context

    @contextmanager
    def page(
        self, url_for_cookies: str | None = None, desktop: bool = False
    ) -> Iterator[Page]:
        """새 페이지를 연다. 쿠키가 설정돼 있으면 해당 URL 도메인에 주입한다.

        desktop=True 면 데스크톱 UA/뷰포트 컨텍스트를 쓴다 (도우인·샤오홍슈 웹).
        """
        if self._context is None:
            raise RuntimeError("BrowserSession must be used as a context manager")
        context = self._get_desktop_context() if desktop else self._context
        cookie_str = os.environ.get("CRAWLER_COOKIES")
        if cookie_str and url_for_cookies:
            context.add_cookies(_parse_cookie_header(cookie_str, url_for_cookies))
        page = context.new_page()
        try:
            yield page
        finally:
            page.close()
