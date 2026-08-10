"""정중한 요청 간 지연.

플랫폼에 부담을 주지 않도록 요청 사이에 무작위 지연을 둔다.
기본 3~8초, 환경변수 CRAWLER_DELAY_MIN / CRAWLER_DELAY_MAX 로 조절.
"""

from __future__ import annotations

import os
import random
import time

DEFAULT_DELAY_MIN_S = 3.0
DEFAULT_DELAY_MAX_S = 8.0


def delay_range_from_env() -> tuple[float, float]:
    """환경변수에서 지연 범위를 읽는다. 잘못된 값이면 기본값."""
    try:
        low = float(os.environ.get("CRAWLER_DELAY_MIN") or DEFAULT_DELAY_MIN_S)
        high = float(os.environ.get("CRAWLER_DELAY_MAX") or DEFAULT_DELAY_MAX_S)
    except ValueError:
        return DEFAULT_DELAY_MIN_S, DEFAULT_DELAY_MAX_S
    if low < 0 or high < low:
        return DEFAULT_DELAY_MIN_S, DEFAULT_DELAY_MAX_S
    return low, high


def polite_sleep(low: float, high: float) -> float:
    """low~high 초 사이 무작위로 잠들고, 실제 잠든 시간을 반환한다."""
    seconds = random.uniform(low, high)
    time.sleep(seconds)
    return seconds
