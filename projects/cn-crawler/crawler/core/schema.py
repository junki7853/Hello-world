"""플랫폼 공통 정규화 스키마.

모든 어댑터는 수집 결과를 Metrics 한 행으로 정규화한다.
플랫폼에 없는 지표는 None 으로 둔다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone


def utc_now_iso() -> str:
    """UTC 현재 시각을 ISO 8601 문자열로 반환한다."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# 중국 플랫폼 표기: "1.2万" = 12,000 / "3亿" = 300,000,000
_COUNT_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*([万亿wW]?)")
_UNIT_MULTIPLIER = {"": 1, "万": 10_000, "w": 10_000, "W": 10_000, "亿": 100_000_000}


def parse_count(text: str | None) -> int | None:
    """'38', '1.2万', '共6条评论' 같은 표기에서 정수 카운트를 추출한다.

    숫자를 찾지 못하면 None.
    """
    if not text:
        return None
    text = text.replace(",", "")  # 천단위 콤마 제거: "1,024" -> "1024"
    match = _COUNT_PATTERN.search(text)
    if not match:
        return None
    value = float(match.group(1)) * _UNIT_MULTIPLIER[match.group(2)]
    return int(value)


@dataclass
class Metrics:
    """게시물 1건의 참여지표 스냅샷 (플랫폼 공통 1행)."""

    platform: str
    article_id: str
    url: str
    title: str | None = None
    author: str | None = None
    views: int | None = None
    likes: int | None = None
    collects: int | None = None
    comments: int | None = None
    shares: int | None = None
    impressions: int | None = None  # 노출수 (조회수와 구분되는 플랫폼용)
    followers: int | None = None  # 작성자 팔로워 수
    upload_date: str | None = None  # 게시물 업로드일 (플랫폼 표기 그대로 or ISO)
    post_format: str | None = None  # 게시물 형식: 이미지/영상 등
    collected_at: str = field(default_factory=utc_now_iso)
    raw: str | None = None  # 수집 원본 일부 (디버깅용)
