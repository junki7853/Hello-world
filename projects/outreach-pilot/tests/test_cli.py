"""CLI 테스트 — 리서치 엔진은 monkeypatch 로 대체."""

import csv

import pytest

from outreach import cli
from outreach.core.store import Store


@pytest.fixture
def profile_yaml(tmp_path):
    path = tmp_path / "profile.yaml"
    path.write_text(
        'product: "유산균"\ndescription: "장 건강"\ncategories: [업체]\n',
        encoding="utf-8",
    )
    return path


@pytest.fixture
def fake_engine(monkeypatch, make_lead):
    """ResearchEngine 을 고정 결과를 반환하는 가짜로 교체한다."""

    class FakeEngine:
        def __init__(self, config=None, client=None):
            self.config = config

        def research_category(self, profile, category, max_leads=10):
            return [make_lead(product=profile.product, category=category, name="파트너A")]

    import outreach.research.engine as engine_module

    monkeypatch.setattr(engine_module, "ResearchEngine", FakeEngine)
    return FakeEngine


def test_research_저장까지(tmp_path, profile_yaml, fake_engine, capsys):
    db = tmp_path / "leads.db"
    rc = cli.main(["research", "--profile", str(profile_yaml), "--db", str(db)])
    assert rc == 0
    with Store(db) as store:
        leads = store.list_leads()
    assert [lead.name for lead in leads] == ["파트너A"]
    out = capsys.readouterr().out
    assert "신규 1건" in out


def test_research_재실행은_갱신(tmp_path, profile_yaml, fake_engine, capsys):
    db = tmp_path / "leads.db"
    cli.main(["research", "--profile", str(profile_yaml), "--db", str(db)])
    cli.main(["research", "--profile", str(profile_yaml), "--db", str(db)])
    out = capsys.readouterr().out
    assert "갱신 1건" in out
    with Store(db) as store:
        assert len(store.list_leads()) == 1


def test_export(tmp_path, make_lead, capsys):
    db = tmp_path / "leads.db"
    with Store(db) as store:
        store.upsert(make_lead(name="a"))
        store.upsert(make_lead(name="b"))
    out_csv = tmp_path / "out.csv"
    rc = cli.main(["export", "--csv", str(out_csv), "--db", str(db)])
    assert rc == 0
    with open(out_csv, encoding="utf-8-sig", newline="") as f:
        assert len(list(csv.DictReader(f))) == 2
    assert "2건" in capsys.readouterr().out


def test_export_product_필터(tmp_path, make_lead):
    db = tmp_path / "leads.db"
    with Store(db) as store:
        store.upsert(make_lead(product="상품A", name="a"))
        store.upsert(make_lead(product="상품B", name="b"))
    out_csv = tmp_path / "out.csv"
    cli.main(["export", "--csv", str(out_csv), "--db", str(db), "--product", "상품A"])
    with open(out_csv, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert [r["product"] for r in rows] == ["상품A"]


def test_잘못된_명령은_에러(capsys):
    with pytest.raises(SystemExit):
        cli.main(["unknown"])
