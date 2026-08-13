"""SQLite 저장소 테스트."""

import pytest

from outreach.core.store import Store


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "test.db") as s:
        yield s


class TestUpsert:
    def test_신규_삽입(self, store, make_lead):
        lead_id, created = store.upsert(make_lead())
        assert created is True
        assert lead_id >= 1

    def test_중복은_갱신(self, store, make_lead):
        id1, _ = store.upsert(make_lead(fit_score=50))
        id2, created = store.upsert(make_lead(fit_score=90, fit_reason="더 적합"))
        assert created is False
        assert id2 == id1
        saved = store.get("테스트상품", "테스트파트너")
        assert saved.fit_score == 90
        assert saved.fit_reason == "더 적합"

    def test_갱신시_status와_created_at_보존(self, store, make_lead):
        lead_id, _ = store.upsert(make_lead(created_at="2026-01-01T00:00:00+00:00"))
        store.update_status(lead_id, "sent")
        store.upsert(make_lead(created_at="2026-08-14T00:00:00+00:00"))
        saved = store.get("테스트상품", "테스트파트너")
        assert saved.status == "sent"
        assert saved.created_at == "2026-01-01T00:00:00+00:00"

    def test_상품이_다르면_별도_리드(self, store, make_lead):
        store.upsert(make_lead(product="상품A"))
        _, created = store.upsert(make_lead(product="상품B"))
        assert created is True

    def test_dict_list_왕복_보존(self, store, make_lead):
        original = make_lead(
            channels={"instagram": "https://instagram.com/x", "website": "https://x.kr"},
            evidence_urls=["https://a.com", "https://b.com"],
        )
        store.upsert(original)
        saved = store.get("테스트상품", "테스트파트너")
        assert saved.channels == original.channels
        assert saved.evidence_urls == original.evidence_urls


class TestQuery:
    def test_get_없으면_None(self, store):
        assert store.get("없는상품", "없는이름") is None

    def test_list_전체(self, store, make_lead):
        store.upsert(make_lead(name="a"))
        store.upsert(make_lead(name="b"))
        assert len(store.list_leads()) == 2

    def test_list_product_필터(self, store, make_lead):
        store.upsert(make_lead(product="상품A", name="a"))
        store.upsert(make_lead(product="상품B", name="b"))
        result = store.list_leads(product="상품A")
        assert [lead.product for lead in result] == ["상품A"]

    def test_list_status_필터(self, store, make_lead):
        id1, _ = store.upsert(make_lead(name="a"))
        store.upsert(make_lead(name="b"))
        store.update_status(id1, "queued")
        result = store.list_leads(status="queued")
        assert [lead.name for lead in result] == ["a"]

    def test_list_fit_score_내림차순(self, store, make_lead):
        store.upsert(make_lead(name="낮음", fit_score=10))
        store.upsert(make_lead(name="높음", fit_score=90))
        assert [lead.name for lead in store.list_leads()] == ["높음", "낮음"]


class TestStatus:
    def test_update_status(self, store, make_lead):
        lead_id, _ = store.upsert(make_lead())
        store.update_status(lead_id, "replied")
        assert store.get("테스트상품", "테스트파트너").status == "replied"

    def test_잘못된_status_거부(self, store, make_lead):
        lead_id, _ = store.upsert(make_lead())
        with pytest.raises(ValueError, match="status"):
            store.update_status(lead_id, "oops")


class TestCorruptRow:
    """DB 에 범위 밖 값이 섞여도 조회 전체가 죽으면 안 된다."""

    @pytest.fixture
    def store_with_bad_row(self, store, make_lead):
        store.upsert(make_lead(name="정상"))
        store._conn.execute(
            "INSERT INTO leads (product, category, name, fit_score, created_at) "
            "VALUES ('테스트상품', '업체', '불량', 999, '2026-08-14T00:00:00+00:00')"
        )
        return store

    def test_list_leads는_불량_행_건너뜀(self, store_with_bad_row, caplog):
        import logging

        with caplog.at_level(logging.WARNING):
            leads = store_with_bad_row.list_leads()
        assert [lead.name for lead in leads] == ["정상"]
        assert "손상" in caplog.text

    def test_get은_불량_행이면_None(self, store_with_bad_row):
        assert store_with_bad_row.get("테스트상품", "불량") is None


class TestFile:
    def test_재오픈시_데이터_유지(self, tmp_path, make_lead):
        db = tmp_path / "persist.db"
        with Store(db) as s:
            s.upsert(make_lead())
        with Store(db) as s:
            assert len(s.list_leads()) == 1

    def test_부모_디렉터리_자동_생성(self, tmp_path, make_lead):
        db = tmp_path / "nested" / "dir" / "leads.db"
        with Store(db) as s:
            s.upsert(make_lead())
        assert db.exists()
