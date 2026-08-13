"""CSV 내보내기 테스트."""

import csv

from outreach.core.export import export_csv


def test_UTF8_BOM(tmp_path, make_lead):
    path = tmp_path / "out.csv"
    export_csv([make_lead()], path)
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_행수_반환과_헤더(tmp_path, make_lead):
    path = tmp_path / "out.csv"
    count = export_csv([make_lead(name="a"), make_lead(name="b")], path)
    assert count == 2
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    assert set(rows[0]) == {
        "id", "product", "category", "name", "contact_email", "channels",
        "evidence_urls", "fit_score", "fit_reason", "status", "created_at",
    }


def test_한글_왕복(tmp_path, make_lead):
    path = tmp_path / "out.csv"
    export_csv([make_lead(name="한글 파트너", fit_reason="타깃 일치")], path)
    with open(path, encoding="utf-8-sig", newline="") as f:
        row = next(csv.DictReader(f))
    assert row["name"] == "한글 파트너"
    assert row["fit_reason"] == "타깃 일치"


def test_복수_URL_공백_구분(tmp_path, make_lead):
    path = tmp_path / "out.csv"
    export_csv([make_lead(evidence_urls=["https://a.kr", "https://b.kr"])], path)
    with open(path, encoding="utf-8-sig", newline="") as f:
        row = next(csv.DictReader(f))
    assert row["evidence_urls"] == "https://a.kr https://b.kr"


def test_이메일_없으면_빈_문자열(tmp_path, make_lead):
    path = tmp_path / "out.csv"
    export_csv([make_lead(contact_email=None)], path)
    with open(path, encoding="utf-8-sig", newline="") as f:
        row = next(csv.DictReader(f))
    assert row["contact_email"] == ""


def test_빈_목록도_헤더는_생성(tmp_path):
    path = tmp_path / "empty.csv"
    assert export_csv([], path) == 0
    with open(path, encoding="utf-8-sig", newline="") as f:
        assert csv.DictReader(f).fieldnames is not None


def test_부모_디렉터리_자동_생성(tmp_path, make_lead):
    path = tmp_path / "sub" / "dir" / "out.csv"
    export_csv([make_lead()], path)
    assert path.exists()
