"""Claude API 기반 리드 리서치 엔진.

카테고리별로 웹서치 도구를 켠 Claude 요청을 보내 잠재 파트너 후보를
수집한다. 비용 제어는 두 겹: 요청당 웹서치 상한(max_uses)과
pause_turn 이어달리기 횟수 상한(max_continuations).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from outreach.research.normalize import extract_json_array, to_leads
from outreach.research.profile import ProductProfile

_DEFAULT_MODEL = "claude-opus-5"


class ResearchError(RuntimeError):
    """리서치 요청이 정상 완료되지 못했을 때."""


@dataclass
class ResearchConfig:
    model: str = field(
        default_factory=lambda: os.environ.get("OUTREACH_MODEL", _DEFAULT_MODEL)
    )
    max_tokens: int = 8192
    # 카테고리(요청) 1건당 웹서치 호출 상한 — 비용 제어의 1차 레버
    max_searches_per_category: int = 5
    # 서버측 도구 루프가 pause_turn 으로 끊겼을 때 이어달리는 횟수 상한
    max_continuations: int = 3


_PROMPT_TEMPLATE = """\
당신은 B2B 파트너 리서처입니다. 아래 상품의 아웃리치(제휴 제안) 대상이 될
"{category}" 카테고리의 잠재 파트너를 웹서치로 조사하세요.

상품명: {product}
상품 설명: {description}
지역: {region}

요구사항:
- 실제로 존재를 확인할 수 있는 곳만 포함하고, 각 항목에 근거 URL(evidence_urls)을 반드시 1개 이상 넣으세요. 근거 없는 항목은 넣지 마세요.
- 연락 수단(이메일, 인스타그램, 웹사이트 문의 페이지 등)을 찾으면 함께 기록하세요.
- fit_score(0~100)는 상품과의 적합도이며, fit_reason 에 근거를 한 문장으로 쓰세요.
- 최대 {max_leads}곳까지, 적합도 높은 순으로.

조사가 끝나면 결과를 아래 형식의 JSON 배열 하나로만 출력하세요(설명 문장 없이):
[
  {{
    "name": "파트너명",
    "contact_email": "이메일 또는 null",
    "channels": {{"website": "https://...", "instagram": "https://..."}},
    "evidence_urls": ["https://..."],
    "fit_score": 85,
    "fit_reason": "한 문장 근거"
  }}
]
"""


class ResearchEngine:
    """상품 프로필을 받아 카테고리별 리드를 수집한다."""

    def __init__(self, config: ResearchConfig | None = None, client: object | None = None) -> None:
        self._config = config or ResearchConfig()
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self._client = client

    def research(self, profile: ProductProfile, max_leads_per_category: int = 10) -> list:
        """프로필의 모든 카테고리를 조사해 Lead 목록을 반환한다."""
        leads = []
        for category in profile.categories:
            leads.extend(self.research_category(profile, category, max_leads_per_category))
        return leads

    def research_category(
        self, profile: ProductProfile, category: str, max_leads: int = 10
    ) -> list:
        """카테고리 1개를 조사해 Lead 목록을 반환한다."""
        prompt = _PROMPT_TEMPLATE.format(
            category=category,
            product=profile.product,
            description=profile.description or "(설명 없음)",
            region=profile.region,
            max_leads=max_leads,
        )
        response = self._run_turn(prompt)
        text = "".join(
            block.text for block in response.content if block.type == "text"
        )
        items = extract_json_array(text)
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return to_leads(items, profile.product, category, created_at)

    def _run_turn(self, prompt: str):
        """웹서치 도구를 켠 요청 1턴을 실행한다 (pause_turn 이어달리기 포함)."""
        cfg = self._config
        tools = [{
            "type": "web_search_20260209",
            "name": "web_search",
            "max_uses": cfg.max_searches_per_category,
        }]
        messages = [{"role": "user", "content": prompt}]

        for _ in range(cfg.max_continuations + 1):
            response = self._client.messages.create(
                model=cfg.model,
                max_tokens=cfg.max_tokens,
                tools=tools,
                messages=messages,
            )
            if response.stop_reason == "refusal":
                raise ResearchError("모델이 요청을 거부했습니다 (stop_reason=refusal)")
            if response.stop_reason != "pause_turn":
                return response
            # 서버측 도구 루프 일시정지 — 대화를 그대로 다시 보내면 이어서 진행
            messages = messages[:1] + [
                {"role": "assistant", "content": response.content}
            ]

        raise ResearchError(
            f"pause_turn 이 {cfg.max_continuations}회 연속 발생해 중단했습니다"
        )
