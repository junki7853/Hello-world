"""스키마 검증 테스트."""

import pytest

from outreach.core.schema import CATEGORIES, STATUSES, Campaign, Lead, Thread


class TestLead:
    def test_기본값(self, make_lead):
        lead = make_lead()
        assert lead.status == "new"
        assert lead.channels == {}
        assert lead.contact_email is None
        assert lead.id is None

    def test_product_필수(self):
        with pytest.raises(ValueError, match="product"):
            Lead(product="", category="업체", name="x")

    def test_name_필수(self):
        with pytest.raises(ValueError, match="name"):
            Lead(product="p", category="업체", name="")

    def test_잘못된_category(self, make_lead):
        with pytest.raises(ValueError, match="category"):
            make_lead(category="외계인")

    def test_잘못된_status(self, make_lead):
        with pytest.raises(ValueError, match="status"):
            make_lead(status="unknown")

    @pytest.mark.parametrize("score", [-1, 101])
    def test_fit_score_범위(self, make_lead, score):
        with pytest.raises(ValueError, match="fit_score"):
            make_lead(fit_score=score)

    @pytest.mark.parametrize("score", [0, 50, 100])
    def test_fit_score_경계값_허용(self, make_lead, score):
        assert make_lead(fit_score=score).fit_score == score

    @pytest.mark.parametrize("category", CATEGORIES)
    def test_모든_카테고리_허용(self, make_lead, category):
        assert make_lead(category=category).category == category

    @pytest.mark.parametrize("status", STATUSES)
    def test_모든_상태_허용(self, make_lead, status):
        assert make_lead(status=status).status == status


class TestPhase23골격:
    def test_campaign_생성(self):
        c = Campaign(product="p", name="가을 프로모션")
        assert c.status == "draft"
        assert c.id is None

    def test_thread_생성(self):
        t = Thread(lead_id=1)
        assert t.channel == "email"
        assert t.status == "open"
