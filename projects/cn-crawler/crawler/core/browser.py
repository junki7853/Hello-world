"""Playwright 헤드리스 브라우저 세션.

실제 페이지를 렌더링해 DOM 을 읽거나 XHR 응답을 가로챈다.
플랫폼 서명 알고리즘은 리버싱하지 않고 브라우저가 대신 계산하게 둔다.

가벼운 stealth 기본값(자동화 탐지 완화)과 쿠키/프록시 주입을 담당한다.
쿠키는 플랫폼별(플랫폼 env·쿠키 디렉터리) → 전역 순으로 고르고, 로그인 세션
전체를 담은 Playwright storage_state(json) 도 받는다.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Iterator
from urllib.parse import unquote, urlparse

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

# 컨텍스트 생성 인자 — 모바일/데스크톱 두 뷰. 전용(storage_state) 컨텍스트도
# 같은 인자를 재사용해 UA/뷰포트 일관성을 지킨다.
_MOBILE_CONTEXT_KWARGS: dict = {
    "user_agent": _MOBILE_USER_AGENT,
    "viewport": _MOBILE_VIEWPORT,
    "locale": "zh-CN",
    "is_mobile": True,
    "has_touch": True,
}
_DESKTOP_CONTEXT_KWARGS: dict = {
    "user_agent": _DESKTOP_USER_AGENT,
    "viewport": _DESKTOP_VIEWPORT,
    "locale": "zh-CN",
}

# navigator.webdriver 등 흔한 자동화 신호를 지우는 최소 stealth 스크립트.
# platform 은 UA 와 모순되면 오히려 봇 신호가 된다 → 컨텍스트별로 맞춘다
# (도우인에서 iPhone platform + 데스크톱 UA 조합이 빈 페이지로 이어지는 것을 실측).
_STEALTH_SCRIPT_BASE = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
window.chrome = window.chrome || {runtime: {}};
"""
_STEALTH_SCRIPT = (
    _STEALTH_SCRIPT_BASE
    + "Object.defineProperty(navigator, 'platform', {get: () => 'iPhone'});\n"
)
_DESKTOP_STEALTH_SCRIPT = (
    _STEALTH_SCRIPT_BASE
    + "Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});\n"
)


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


def resolve_cookie_source(
    platform: str | None, environ: Mapping[str, str] | None = None
) -> tuple[str, str] | None:
    """플랫폼 쿠키 소스를 우선순위대로 찾는다.

    우선순위: CRAWLER_COOKIES_<PLATFORM>(헤더 문자열) > <DIR>/<platform>.json
    (Playwright storage_state 파일) > <DIR>/<platform>.txt(헤더 문자열) >
    CRAWLER_COOKIES(전역 헤더, 하위호환). <DIR> 은 CRAWLER_COOKIES_DIR.

    반환값: ("header", 쿠키헤더문자열) | ("storage_state", json파일경로) | None.
    브라우저 없이 순수 계산이라 유닛 테스트로 우선순위를 검증한다.
    """
    env = os.environ if environ is None else environ
    if platform:
        env_val = env.get(f"CRAWLER_COOKIES_{platform.upper()}")
        if env_val:
            return ("header", env_val)
        cookie_dir = env.get("CRAWLER_COOKIES_DIR")
        if cookie_dir:
            base = Path(cookie_dir)
            json_path = base / f"{platform}.json"
            if json_path.is_file():
                return ("storage_state", str(json_path))
            txt_path = base / f"{platform}.txt"
            if txt_path.is_file():
                text = txt_path.read_text(encoding="utf-8").strip()
                if text:
                    return ("header", text)
    global_val = env.get("CRAWLER_COOKIES")
    if global_val:
        return ("header", global_val)
    return None


def build_proxy_config(environ: Mapping[str, str] | None = None) -> dict | None:
    """CRAWLER_PROXY(+선택 인증)를 Playwright proxy 딕셔너리로 만든다.

    server 는 http://host:port 형태로 정규화한다(인증정보는 server 에서 떼어낸다 —
    Playwright 는 username/password 를 별도 키로 받는다). 인증은 URL 임베드
    (http://user:pass@host:port) 또는 별도 CRAWLER_PROXY_USERNAME/PASSWORD 로 주며,
    별도 지정이 URL 임베드보다 우선한다. 인증 없는 server-only 형태도 그대로 동작.
    프록시 미설정이면 None.
    """
    env = os.environ if environ is None else environ
    server = env.get("CRAWLER_PROXY")
    if not server:
        return None
    parsed = urlparse(server)
    # URL 임베드 인증은 percent-encoding 될 수 있다(p%40ss → p@ss). urlparse 는
    # 디코드하지 않으므로 여기서 풀어 Playwright 에 원문 그대로 전달한다.
    embedded_user = unquote(parsed.username) if parsed.username else parsed.username
    embedded_pass = unquote(parsed.password) if parsed.password else parsed.password
    if parsed.hostname and (parsed.username or parsed.password):
        netloc = parsed.hostname
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        server = f"{parsed.scheme}://{netloc}"
    username = env.get("CRAWLER_PROXY_USERNAME") or embedded_user
    password = env.get("CRAWLER_PROXY_PASSWORD") or embedded_pass
    config: dict = {"server": server}
    if username:
        config["username"] = username
    if password:
        config["password"] = password
    return config


class BrowserSession:
    """페이지를 열어주는 얇은 컨텍스트 매니저.

    쿠키/프록시는 환경변수에서 읽는다: 프록시는 CRAWLER_PROXY(+인증), 쿠키는
    플랫폼별(CRAWLER_COOKIES_<PLATFORM>·CRAWLER_COOKIES_DIR) → 전역 CRAWLER_COOKIES.
    """

    def __init__(self, headless: bool = True) -> None:
        self.headless = headless
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._desktop_context: BrowserContext | None = None
        # (platform, desktop) → storage_state 로 시드된 전용 컨텍스트
        self._storage_contexts: dict[tuple[str, bool], BrowserContext] = {}

    def __enter__(self) -> "BrowserSession":
        self._playwright = sync_playwright().start()
        try:
            launch_kwargs: dict = {"headless": self.headless}
            proxy = build_proxy_config()
            if proxy:
                launch_kwargs["proxy"] = proxy
            self._browser = self._playwright.chromium.launch(**launch_kwargs)
            self._context = self._browser.new_context(**_MOBILE_CONTEXT_KWARGS)
            self._context.add_init_script(_STEALTH_SCRIPT)
        except Exception:
            # 부분 초기화 롤백: 열린 자원을 정리하고 예외를 다시 던진다
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc_info: object) -> None:
        # 앞 단계에서 예외가 나도 뒤 정리가 반드시 실행되도록 각각 격리한다
        for context in self._storage_contexts.values():
            with suppress(Exception):
                context.close()
        self._storage_contexts = {}
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
            self._desktop_context = self._browser.new_context(**_DESKTOP_CONTEXT_KWARGS)
            self._desktop_context.add_init_script(_DESKTOP_STEALTH_SCRIPT)
        return self._desktop_context

    def _get_storage_state_context(
        self, platform: str, desktop: bool, storage_state_path: str
    ) -> BrowserContext:
        """플랫폼 storage_state 로 로그인 세션을 담은 전용 컨텍스트(캐시)를 만든다.

        storage_state 는 쿠키+localStorage 를 함께 담으므로 공유 컨텍스트에
        add_cookies 로 섞지 않고 (platform, desktop)별 전용 컨텍스트를 쓴다.
        """
        key = (platform, desktop)
        context = self._storage_contexts.get(key)
        if context is None:
            assert self._browser is not None
            kwargs = _DESKTOP_CONTEXT_KWARGS if desktop else _MOBILE_CONTEXT_KWARGS
            context = self._browser.new_context(
                storage_state=storage_state_path, **kwargs
            )
            context.add_init_script(
                _DESKTOP_STEALTH_SCRIPT if desktop else _STEALTH_SCRIPT
            )
            self._storage_contexts[key] = context
        return context

    @contextmanager
    def page(
        self,
        url_for_cookies: str | None = None,
        desktop: bool = False,
        platform: str | None = None,
    ) -> Iterator[Page]:
        """새 페이지를 연다. 플랫폼/전역 쿠키가 설정돼 있으면 주입한다.

        쿠키 소스는 resolve_cookie_source 우선순위(플랫폼별 > 전역)를 따른다.
        storage_state(json)면 로그인 세션 전체를 담은 전용 컨텍스트를 쓰고,
        헤더 문자열이면 기존처럼 URL 도메인에 스코핑해 주입한다.
        desktop=True 면 데스크톱 UA/뷰포트 컨텍스트를 쓴다 (도우인·샤오홍슈 웹).
        platform 미지정이면 전역 CRAWLER_COOKIES 만 적용 — 기존 호출과 호환된다.
        """
        if self._context is None:
            raise RuntimeError("BrowserSession must be used as a context manager")
        source = resolve_cookie_source(platform)
        if source is not None and source[0] == "storage_state":
            assert platform is not None  # storage_state 는 platform 경로에서만 나온다
            context = self._get_storage_state_context(platform, desktop, source[1])
        else:
            context = self._get_desktop_context() if desktop else self._context
            if source is not None and url_for_cookies:
                context.add_cookies(_parse_cookie_header(source[1], url_for_cookies))
        page = context.new_page()
        try:
            yield page
        finally:
            page.close()
