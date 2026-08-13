"""데이터 스키마.

Phase 1 은 Lead 가 중심. Campaign / Thread 는 Phase 2~3(발송·응대)에서 쓸
최소 골격만 정의한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


def dump_json(value: object) -> str:
    """channels/evidence_urls/raw 직렬화 공용 규약 (저장·내보내기 동일 표기)."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


# 잠재 파트너 분류. 프로필/응답 정규화 시 이 목록 밖 값은 "기타"로 수렴한다.
CATEGORIES = ("업체", "인플루언서", "물류", "마케팅", "기타")

# 리드 생애주기. new(수집됨) → queued(발송 대기) → sent → replied →
# manual(수동 개입 필요) / closed(종료)
STATUSES = ("new", "queued", "sent", "replied", "manual", "closed")


@dataclass
class Lead:
    """수집된 잠재 파트너 1건. (product, name) 이 논리적 유일키."""

    product: str
    category: str
    name: str
    contact_email: str | None = None
    channels: dict[str, str] = field(default_factory=dict)
    evidence_urls: list[str] = field(default_factory=list)
    fit_score: int = 0
    fit_reason: str = ""
    status: str = "new"
    created_at: str = ""
    raw: str | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        if not self.product or not self.name:
            raise ValueError("product 와 name 은 필수입니다")
        if self.category not in CATEGORIES:
            raise ValueError(f"category 는 {CATEGORIES} 중 하나여야 합니다: {self.category!r}")
        if self.status not in STATUSES:
            raise ValueError(f"status 는 {STATUSES} 중 하나여야 합니다: {self.status!r}")
        if not 0 <= self.fit_score <= 100:
            raise ValueError(f"fit_score 는 0~100 이어야 합니다: {self.fit_score}")


@dataclass
class Campaign:
    """아웃리치 캠페인 골격 (Phase 2에서 확장)."""

    product: str
    name: str
    message_template: str = ""
    status: str = "draft"
    created_at: str = ""
    id: int | None = None


@dataclass
class Thread:
    """리드와의 대화 스레드 골격 (Phase 3에서 확장)."""

    lead_id: int
    campaign_id: int | None = None
    channel: str = "email"
    last_message_at: str = ""
    status: str = "open"
    id: int | None = None
