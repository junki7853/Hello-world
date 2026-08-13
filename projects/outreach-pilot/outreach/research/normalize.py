"""Claude 응답 → Lead 정규화.

모델 출력은 신뢰하지 않는다: JSON 추출, 필드 검증, evidence 없는 항목 폐기,
fit_score 클램핑을 모두 여기서 처리한다.
"""

from __future__ import annotations

import json
import re

from outreach.core.schema import CATEGORIES, Lead, dump_json

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_DECODER = json.JSONDecoder()


def extract_json_array(text: str) -> list[dict]:
    """응답 텍스트에서 JSON 배열을 추출한다.

    코드펜스(```json ... ```) 안 → 본문 순으로, 각 '[' 위치에서
    raw_decode 를 시도해 dict 를 담은 첫 배열을 반환한다. 인용 표기
    "[1]" 처럼 앞선 배열이 파싱되더라도 dict 가 없으면 건너뛴다.
    배열을 찾지 못하면 빈 목록을 반환한다 (수집 0건으로 처리).
    """
    candidates = _FENCE_RE.findall(text)
    candidates.append(text)
    for candidate in candidates:
        start = candidate.find("[")
        while start >= 0:
            try:
                data, _ = _DECODER.raw_decode(candidate, start)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, list):
                items = [d for d in data if isinstance(d, dict)]
                if items:
                    return items
            start = candidate.find("[", start + 1)
    return []


def _clamp_score(value: object) -> int:
    try:
        score = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, score))


def _clean_urls(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        u.strip() for u in value
        if isinstance(u, str) and u.strip().startswith(("http://", "https://"))
    ]


def _clean_channels(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(k): str(v) for k, v in value.items()
        if isinstance(k, str) and isinstance(v, str) and v.strip()
    }


def to_leads(
    items: list[dict], product: str, category: str, created_at: str
) -> list[Lead]:
    """모델이 반환한 후보 목록을 Lead 로 변환한다.

    - name 이 없거나 evidence_urls(유효 URL)가 없는 항목은 폐기
    - category 는 요청한 카테고리로 강제 (모델의 임의 분류 무시)
    - (product, name) 기준 중복은 첫 항목만 유지
    """
    leads: list[Lead] = []
    seen: set[str] = set()
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name or name in seen:
            continue
        evidence_urls = _clean_urls(item.get("evidence_urls"))
        if not evidence_urls:
            continue
        seen.add(name)

        email = item.get("contact_email")
        email = email.strip() if isinstance(email, str) and "@" in email else None

        leads.append(Lead(
            product=product,
            category=category if category in CATEGORIES else "기타",
            name=name,
            contact_email=email,
            channels=_clean_channels(item.get("channels")),
            evidence_urls=evidence_urls,
            fit_score=_clamp_score(item.get("fit_score")),
            fit_reason=str(item.get("fit_reason") or "").strip(),
            status="new",
            created_at=created_at,
            raw=dump_json(item),
        ))
    return leads
