"""응답 정규화 테스트."""

import json

from outreach.research.normalize import extract_json_array, to_leads

CREATED_AT = "2026-08-14T00:00:00+00:00"


def _item(**overrides) -> dict:
    base = {
        "name": "파트너A",
        "contact_email": "a@example.com",
        "channels": {"website": "https://a.kr"},
        "evidence_urls": ["https://a.kr/about"],
        "fit_score": 85,
        "fit_reason": "타깃 고객층 일치",
    }
    base.update(overrides)
    return base


class TestExtractJsonArray:
    def test_순수_JSON_배열(self):
        text = json.dumps([_item()], ensure_ascii=False)
        assert extract_json_array(text)[0]["name"] == "파트너A"

    def test_코드펜스_안_JSON(self):
        text = "결과입니다.\n```json\n" + json.dumps([_item()]) + "\n```\n끝."
        assert len(extract_json_array(text)) == 1

    def test_언어표시_없는_코드펜스(self):
        text = "```\n" + json.dumps([_item()]) + "\n```"
        assert len(extract_json_array(text)) == 1

    def test_설명_문장_사이의_배열(self):
        text = "조사 결과 다음과 같습니다:\n" + json.dumps([_item(), _item(name="B")]) + "\n이상입니다."
        assert len(extract_json_array(text)) == 2

    def test_문자열_안_대괄호_무시(self):
        item = _item(fit_reason='리뷰에 "best [2026]" 언급')
        text = json.dumps([item], ensure_ascii=False)
        result = extract_json_array(text)
        assert result[0]["fit_reason"] == '리뷰에 "best [2026]" 언급'

    def test_인용표기_선행_텍스트(self):
        """앞선 '[1]' 같은 인용 표기가 있어도 본 배열을 찾는다 (회귀: 첫 '['만 시도)."""
        text = "조사 결과[1] 다음과 같습니다[2]:\n" + json.dumps([_item()], ensure_ascii=False)
        result = extract_json_array(text)
        assert len(result) == 1
        assert result[0]["name"] == "파트너A"

    def test_숫자_배열_선행해도_본_배열_추출(self):
        text = "[1, 2, 3] 순위입니다.\n" + json.dumps([_item()], ensure_ascii=False)
        assert extract_json_array(text)[0]["name"] == "파트너A"

    def test_배열_없으면_빈_목록(self):
        assert extract_json_array("적합한 파트너를 찾지 못했습니다.") == []

    def test_깨진_JSON은_빈_목록(self):
        assert extract_json_array('[{"name": "x", ]') == []

    def test_dict가_아닌_원소_제거(self):
        assert extract_json_array('["문자열", 3, {"name": "x"}]') == [{"name": "x"}]

    def test_빈_텍스트(self):
        assert extract_json_array("") == []


class TestToLeads:
    def test_정상_변환(self):
        leads = to_leads([_item()], "상품", "업체", CREATED_AT)
        assert len(leads) == 1
        lead = leads[0]
        assert lead.product == "상품"
        assert lead.category == "업체"
        assert lead.name == "파트너A"
        assert lead.contact_email == "a@example.com"
        assert lead.fit_score == 85
        assert lead.created_at == CREATED_AT
        assert lead.status == "new"
        assert json.loads(lead.raw)["name"] == "파트너A"

    def test_evidence_없으면_폐기(self):
        items = [_item(), _item(name="근거없음", evidence_urls=[])]
        leads = to_leads(items, "상품", "업체", CREATED_AT)
        assert [lead.name for lead in leads] == ["파트너A"]

    def test_URL_형식_아닌_evidence는_무효(self):
        items = [_item(name="가짜근거", evidence_urls=["출처: 뉴스 기사", "javascript:x"])]
        assert to_leads(items, "상품", "업체", CREATED_AT) == []

    def test_유효_URL만_남김(self):
        items = [_item(evidence_urls=["메모", "https://real.kr/page", "http://old.kr"])]
        leads = to_leads(items, "상품", "업체", CREATED_AT)
        assert leads[0].evidence_urls == ["https://real.kr/page", "http://old.kr"]

    def test_name_없으면_폐기(self):
        assert to_leads([_item(name="")], "상품", "업체", CREATED_AT) == []
        assert to_leads([_item(name=None)], "상품", "업체", CREATED_AT) == []

    def test_같은_name_중복은_첫_항목만(self):
        items = [_item(fit_score=90), _item(fit_score=10)]
        leads = to_leads(items, "상품", "업체", CREATED_AT)
        assert len(leads) == 1
        assert leads[0].fit_score == 90

    def test_fit_score_클램핑(self):
        assert to_leads([_item(fit_score=150)], "상품", "업체", CREATED_AT)[0].fit_score == 100
        assert to_leads([_item(fit_score=-5)], "상품", "업체", CREATED_AT)[0].fit_score == 0

    def test_fit_score_비정상_타입은_0(self):
        assert to_leads([_item(fit_score="높음")], "상품", "업체", CREATED_AT)[0].fit_score == 0
        assert to_leads([_item(fit_score=None)], "상품", "업체", CREATED_AT)[0].fit_score == 0

    def test_잘못된_이메일은_None(self):
        leads = to_leads([_item(contact_email="문의 페이지 참조")], "상품", "업체", CREATED_AT)
        assert leads[0].contact_email is None

    def test_channels_비정상_타입_정리(self):
        items = [_item(channels={"website": "https://a.kr", "count": 3, "empty": ""})]
        leads = to_leads(items, "상품", "업체", CREATED_AT)
        assert leads[0].channels == {"website": "https://a.kr"}

    def test_channels_dict_아니면_빈_dict(self):
        leads = to_leads([_item(channels="https://a.kr")], "상품", "업체", CREATED_AT)
        assert leads[0].channels == {}

    def test_모르는_카테고리는_기타로(self):
        leads = to_leads([_item()], "상품", "이상한값", CREATED_AT)
        assert leads[0].category == "기타"

    def test_빈_입력(self):
        assert to_leads([], "상품", "업체", CREATED_AT) == []
