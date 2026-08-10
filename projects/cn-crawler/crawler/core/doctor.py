"""설정 검증(doctor): 쿠키·프록시가 실제로 월을 뚫는지 판정해 리포트한다.

각 플랫폼의 테스트 URL 로 실제 navigate 한 뒤, 어댑터가 남긴 진단(raw 의 월 플래그)
과 추출된 지표 유무로 상태를 판정한다. 지표값은 저장하지 않고 상태만 본다 —
사용자가 전량 수집 전에 쿠키/프록시 유효성을 즉시 확인하기 위한 것.

판정 로직(classify_metrics)은 브라우저 없이 순수 계산이라 유닛 테스트로 검증한다.
"""

from __future__ import annotations

import json
import logging

from crawler.adapters.base import Adapter, UnsupportedUrlError
from crawler.core.browser import BrowserSession
from crawler.core.schema import Metrics

logger = logging.getLogger(__name__)

# 상태 코드
OK = "OK"
LOGIN_WALL = "LOGIN_WALL"
CAPTCHA_WALL = "CAPTCHA_WALL"
APP_WALL = "APP_WALL"
REDIRECTED = "REDIRECTED"
NO_DATA = "NO_DATA"
NO_URL = "NO_URL"
NO_ADAPTER = "NO_ADAPTER"
BAD_URL = "BAD_URL"
ERROR = "ERROR"

# raw 진단의 월 플래그 → 상태. 위에서부터 먼저 걸리는 것이 이긴다(심각도 순).
_WALL_TO_STATUS: tuple[tuple[str, str], ...] = (
    ("security_wall", LOGIN_WALL),
    ("login_wall", LOGIN_WALL),
    ("captcha_wall", CAPTCHA_WALL),
    ("verify_wall", CAPTCHA_WALL),
    ("captcha_detected", CAPTCHA_WALL),  # 마펑워는 captcha_detected 로 기록
    ("app_wall", APP_WALL),
    ("redirected_away", REDIRECTED),
)

# 지표로 인정할 Metrics 필드 (하나라도 있으면 정상 렌더로 본다)
_VALUE_FIELDS = ("views", "likes", "collects", "comments", "shares", "followers")

_STATUS_HINT = {
    LOGIN_WALL: "로그인월 — 쿠키 없음/만료. CRAWLER_COOKIES_<PLATFORM> 또는 storage_state 필요",
    CAPTCHA_WALL: "캡차/verify — 쿠키로 우회 안 되면 사람이 세션 통과 후 storage_state 저장 필요",
    APP_WALL: "앱 전용 유도월 — 유효 쿠키/공유토큰 URL 필요",
    REDIRECTED: "타깃 이탈(리다이렉트) — 쿠키 만료 또는 지역차단 의심",
    NO_DATA: "차단 신호는 없으나 지표 미검출 — 프록시(지역)·쿠키·URL 확인",
    NO_URL: "테스트 URL 미제공 — --check-url <platform>=<공개 게시물 URL> 로 제공",
    NO_ADAPTER: "지원하지 않는 플랫폼",
    BAD_URL: "URL 형식을 어댑터가 해석하지 못함",
    ERROR: "수집 중 예외 — 프록시/네트워크 확인",
}


def classify_metrics(metrics: Metrics) -> tuple[str, str]:
    """수집된 Metrics 의 raw 월 플래그 + 지표 유무로 상태와 설명을 판정한다.

    반환: (상태코드, 사람이 읽을 설명). 지표값 자체는 판정에만 쓰고 저장하지 않는다.
    """
    raw: dict = {}
    if metrics.raw:
        try:
            parsed = json.loads(metrics.raw)
            if isinstance(parsed, dict):
                raw = parsed
        except json.JSONDecodeError:
            pass

    for flag, status in _WALL_TO_STATUS:
        if raw.get(flag):
            return status, _STATUS_HINT[status]

    present = {
        field: getattr(metrics, field)
        for field in _VALUE_FIELDS
        if getattr(metrics, field) is not None
    }
    if present:
        summary = ", ".join(f"{k}={v}" for k, v in present.items())
        detail = f"지표 렌더 정상 ({summary})"
        if raw.get("font_obfuscation_detected"):
            detail += " ⚠ 폰트 난독화 감지(숫자 신뢰 낮음)"
        return OK, detail
    return NO_DATA, _STATUS_HINT[NO_DATA]


def format_report(results: list[tuple[str, str, str]]) -> str:
    """(platform, status, detail) 목록을 정렬된 사람용 리포트 문자열로 만든다."""
    lines = ["플랫폼 쿠키/프록시 진단 (지표는 저장하지 않음)", ""]
    width = max((len(p) for p, _, _ in results), default=0)
    for platform, status, detail in results:
        lines.append(f"  {platform.ljust(width)}  {status.ljust(12)} {detail}")
    return "\n".join(lines)


def run_doctor(
    checks: list[tuple[str, str | None]],
    adapter_registry: dict[str, type[Adapter]],
    headless: bool = True,
) -> int:
    """각 (platform, url) 을 실제 navigate 해 상태를 판정하고 리포트를 출력한다.

    checks 는 (platform, url|None) 순서 목록. url 이 None 이면 NO_URL 로 스킵한다.
    모든 검사가 OK 면 0, 하나라도 아니면 1 을 반환한다(수집 전 준비완료 게이트).
    """
    results: list[tuple[str, str, str]] = []
    with BrowserSession(headless=headless) as session:
        for platform, url in checks:
            if not url:
                results.append((platform, NO_URL, _STATUS_HINT[NO_URL]))
                continue
            adapter_cls = adapter_registry.get(platform)
            if adapter_cls is None:
                results.append((platform, NO_ADAPTER, _STATUS_HINT[NO_ADAPTER]))
                continue
            adapter = adapter_cls(session)
            try:
                metrics = adapter.collect(url)
            except UnsupportedUrlError as exc:
                results.append((platform, BAD_URL, f"{_STATUS_HINT[BAD_URL]}: {exc}"))
                continue
            except Exception as exc:
                logger.debug("doctor 수집 예외 %s: %s", platform, exc)
                results.append((platform, ERROR, f"{_STATUS_HINT[ERROR]}: {exc}"))
                continue
            status, detail = classify_metrics(metrics)
            results.append((platform, status, detail))

    print(format_report(results))
    return 0 if results and all(s == OK for _, s, _ in results) else 1
