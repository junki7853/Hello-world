"""Claude 응답 → Lead 정규화.

모델 출력은 신뢰하지 않는다: JSON 추출, 필드 검증, evidence 없는 항목 폐기,
fit_score 클램핑을 모두 여기서 처리한다.
"""

from __future__ import annotations

import json
import re

from outreach.core.schema import CATEGORIES, Lead

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json_array(text: str) -> list[dict]:
    """응답 텍스트에서 JSON 배열을 추출한다.

    코드펜스(```json ... ```) 안 → 텍스트 내 첫 '['~짝 ']' 순으로 시도.
    배열을 찾지 못하면 빈 목록을 반환한다 (수집 0건으로 처리).
    """
    candidates = _FENCE_RE.findall(text)
    candidates.append(text)
    for candidate in candidates:
        start = candidate.find("[")
        if start < 0:
            continue
        # 첫 '[' 부터 짝이 맞는 ']' 를 찾는다 (문자열 리터럴 내 괄호 무시)
        depth, in_str, escape = 0, False, False
        for i, ch in enumerate(candidate[start:], start):
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = in_str
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(candidate[start : i + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(data, list):
                        return [d for d in data if isinstance(d, dict)]
                    break
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
            raw=json.dumps(item, ensure_ascii=False, sort_keys=True),
        ))
    return leads
