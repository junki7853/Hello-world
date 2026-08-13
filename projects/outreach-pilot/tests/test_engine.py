"""리서치 엔진 테스트 — Claude API 는 mock 클라이언트로 대체."""

import json
from types import SimpleNamespace

import pytest

from outreach.research.engine import ResearchConfig, ResearchEngine, ResearchError
from outreach.research.profile import ProductProfile


def _text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def _response(text: str, stop_reason: str = "end_turn"):
    return SimpleNamespace(stop_reason=stop_reason, content=[_text_block(text)])


def _items_json(*names: str) -> str:
    return json.dumps([
        {
            "name": name,
            "contact_email": f"{i}@example.com",
            "channels": {"website": f"https://{i}.kr"},
            "evidence_urls": [f"https://{i}.kr/about"],
            "fit_score": 80,
            "fit_reason": "적합",
        }
        for i, name in enumerate(names)
    ], ensure_ascii=False)


class FakeClient:
    """messages.create 호출을 기록하고 준비된 응답을 차례로 반환한다."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


@pytest.fixture
def profile():
    return ProductProfile(
        product="유산균", description="장 건강", categories=["업체", "인플루언서"]
    )


class TestResearchCategory:
    def test_정상_수집(self, profile):
        client = FakeClient([_response(_items_json("파트너A", "파트너B"))])
        engine = ResearchEngine(client=client)
        leads = engine.research_category(profile, "업체")
        assert [lead.name for lead in leads] == ["파트너A", "파트너B"]
        assert all(lead.product == "유산균" for lead in leads)
        assert all(lead.category == "업체" for lead in leads)
        assert all(lead.created_at for lead in leads)

    def test_요청에_웹서치_도구와_상한_포함(self, profile):
        client = FakeClient([_response(_items_json("A"))])
        config = ResearchConfig(model="claude-opus-5", max_searches_per_category=3)
        ResearchEngine(config=config, client=client).research_category(profile, "업체")
        call = client.calls[0]
        assert call["model"] == "claude-opus-5"
        assert call["tools"] == [{
            "type": "web_search_20260209",
            "name": "web_search",
            "max_uses": 3,
        }]

    def test_프롬프트에_상품정보_포함(self, profile):
        client = FakeClient([_response(_items_json("A"))])
        ResearchEngine(client=client).research_category(profile, "인플루언서", max_leads=7)
        prompt = client.calls[0]["messages"][0]["content"]
        assert "유산균" in prompt
        assert "장 건강" in prompt
        assert "인플루언서" in prompt
        assert "7곳" in prompt

    def test_JSON_없는_응답은_빈_목록(self, profile):
        client = FakeClient([_response("적합한 파트너를 찾지 못했습니다.")])
        assert ResearchEngine(client=client).research_category(profile, "업체") == []

    def test_텍스트_블록만_취합(self, profile):
        """웹서치 결과 블록 등 text 아닌 블록이 섞여 있어도 파싱된다."""
        response = SimpleNamespace(stop_reason="end_turn", content=[
            SimpleNamespace(type="server_tool_use", text=None),
            SimpleNamespace(type="web_search_tool_result", text=None),
            _text_block("결과: " + _items_json("A")),
        ])
        client = FakeClient([response])
        leads = ResearchEngine(client=client).research_category(profile, "업체")
        assert [lead.name for lead in leads] == ["A"]


class TestPauseTurn:
    def test_pause_turn_이어달리기(self, profile):
        client = FakeClient([
            _response("검색 중...", stop_reason="pause_turn"),
            _response(_items_json("A")),
        ])
        leads = ResearchEngine(client=client).research_category(profile, "업체")
        assert len(leads) == 1
        assert len(client.calls) == 2
        # 두 번째 요청은 [원래 user, 직전 assistant] 로 이어붙인다
        second = client.calls[1]["messages"]
        assert second[0]["role"] == "user"
        assert second[1]["role"] == "assistant"

    def test_상한_초과시_에러(self, profile):
        config = ResearchConfig(max_continuations=2)
        client = FakeClient([_response("...", stop_reason="pause_turn")] * 3)
        with pytest.raises(ResearchError, match="pause_turn"):
            ResearchEngine(config=config, client=client).research_category(profile, "업체")
        assert len(client.calls) == 3


class TestRefusal:
    def test_거부시_에러(self, profile):
        client = FakeClient([_response("", stop_reason="refusal")])
        with pytest.raises(ResearchError, match="거부"):
            ResearchEngine(client=client).research_category(profile, "업체")


class TestResearch:
    def test_모든_카테고리_순회(self, profile):
        client = FakeClient([
            _response(_items_json("업체A")),
            _response(_items_json("인플루언서B")),
        ])
        leads = ResearchEngine(client=client).research(profile)
        assert [(lead.category, lead.name) for lead in leads] == [
            ("업체", "업체A"), ("인플루언서", "인플루언서B"),
        ]
        assert len(client.calls) == 2


class TestConfig:
    def test_모델_환경변수_오버라이드(self, monkeypatch):
        monkeypatch.setenv("OUTREACH_MODEL", "claude-haiku-4-5")
        assert ResearchConfig().model == "claude-haiku-4-5"

    def test_모델_기본값(self, monkeypatch):
        monkeypatch.delenv("OUTREACH_MODEL", raising=False)
        assert ResearchConfig().model == "claude-opus-5"


@pytest.mark.live
class TestLive:
    """실제 Claude API 호출 (기본 실행 제외, `pytest -m live` 로 실행)."""

    def test_실제_리서치_1건(self):
        import os

        if not os.environ.get("ANTHROPIC_API_KEY"):
            pytest.skip("ANTHROPIC_API_KEY 없음")
        profile = ProductProfile(
            product="프리미엄 유산균 밸런스",
            description="장 건강 프로바이오틱스 건강기능식품",
            categories=["업체"],
        )
        config = ResearchConfig(max_searches_per_category=2)
        leads = ResearchEngine(config=config).research_category(profile, "업체", max_leads=3)
        for lead in leads:
            assert lead.evidence_urls
            assert 0 <= lead.fit_score <= 100
