"""신규 스키마 필드 · store 마이그레이션 · CSV export 스모크 테스트."""

import sqlite3

from crawler.cli import main
from crawler.core.export import EXPORT_COLUMNS, export_csv
from crawler.core.schema import Metrics
from crawler.core.store import Store


def _metrics(article_id: str = "1", collected_at: str = "2026-08-10T00:00:00+00:00", **kw) -> Metrics:
    return Metrics(
        platform="ctrip",
        article_id=article_id,
        url=f"https://example.com/x?articleId={article_id}",
        collected_at=collected_at,
        **kw,
    )


# --- 신규 스키마 필드 -------------------------------------------------------

def test_new_fields_default_none():
    m = _metrics()
    assert m.impressions is None
    assert m.followers is None
    assert m.upload_date is None
    assert m.post_format is None


def test_new_fields_roundtrip_via_store(tmp_path):
    db = tmp_path / "t.db"
    with Store(db) as store:
        store.append(_metrics(
            impressions=1200, followers=345,
            upload_date="2026-08-01", post_format="영상",
        ))
        latest = store.latest("ctrip", "1")
    assert latest.impressions == 1200
    assert latest.followers == 345
    assert latest.upload_date == "2026-08-01"
    assert latest.post_format == "영상"


# --- Phase 1 DB 마이그레이션 -----------------------------------------------

_PHASE1_SCHEMA = """
CREATE TABLE snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    article_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    author TEXT,
    views INTEGER,
    likes INTEGER,
    collects INTEGER,
    comments INTEGER,
    shares INTEGER,
    collected_at TEXT NOT NULL,
    raw TEXT
);
"""


def test_migrates_phase1_db_preserving_rows(tmp_path):
    """구버전 DB 파일을 열면 신규 컬럼이 ALTER 되고 기존 행은 보존된다."""
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(_PHASE1_SCHEMA)
    conn.execute(
        "INSERT INTO snapshots (platform, article_id, url, likes, collected_at) "
        "VALUES ('ctrip', '99', 'https://x', 38, '2026-08-09T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    with Store(db) as store:
        old = store.latest("ctrip", "99")
        assert old.likes == 38  # 기존 행 보존
        assert old.impressions is None  # 신규 컬럼은 NULL 로 채워짐
        # 마이그레이션된 DB 에 신규 필드 저장도 동작
        store.append(_metrics(article_id="99", followers=10,
                              collected_at="2026-08-10T00:00:00+00:00"))
        assert store.latest("ctrip", "99").followers == 10


def test_migration_is_idempotent_on_reopen(tmp_path):
    """이미 마이그레이션된 DB 를 다시 열어도 ALTER 재실행/에러 없이 행이 보존된다."""
    db = tmp_path / "t.db"
    with Store(db) as store:
        store.append(_metrics(article_id="7", followers=10))
    with Store(db) as store:  # 재오픈 1
        assert store.latest("ctrip", "7").followers == 10
    with Store(db) as store:  # 재오픈 2 — 여전히 정상
        store.append(_metrics(article_id="7", followers=11,
                              collected_at="2026-08-11T00:00:00+00:00"))
        assert store.latest("ctrip", "7").followers == 11
        assert len(store.history("ctrip", "7")) == 2


# --- latest_all / all_snapshots -------------------------------------------

def test_latest_all_picks_newest_per_article(tmp_path):
    db = tmp_path / "t.db"
    with Store(db) as store:
        store.append(_metrics("a", "2026-08-09T00:00:00+00:00", likes=1))
        store.append(_metrics("a", "2026-08-10T00:00:00+00:00", likes=5))
        store.append(_metrics("b", "2026-08-10T00:00:00+00:00", likes=7))
        rows = store.latest_all()
    assert [(m.article_id, m.likes) for m in rows] == [("a", 5), ("b", 7)]


def test_all_snapshots_returns_full_history(tmp_path):
    db = tmp_path / "t.db"
    with Store(db) as store:
        store.append(_metrics("a", "2026-08-09T00:00:00+00:00", likes=1))
        store.append(_metrics("a", "2026-08-10T00:00:00+00:00", likes=5))
        rows = store.all_snapshots()
    assert [m.likes for m in rows] == [1, 5]


# --- CSV export -------------------------------------------------------------

def test_export_csv_writes_bom_and_header(tmp_path):
    db = tmp_path / "t.db"
    out = tmp_path / "out.csv"
    with Store(db) as store:
        store.append(_metrics(likes=3, post_format="이미지"))
        written = export_csv(store, out)
    assert written == 1
    data = out.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM (엑셀 한글 호환)
    lines = data.decode("utf-8-sig").splitlines()
    assert lines[0] == ",".join(EXPORT_COLUMNS)
    assert lines[0].split(",")[0] == "platform"
    assert lines[0].split(",")[-1] == "collected_at"


def test_export_csv_latest_only_vs_all(tmp_path):
    db = tmp_path / "t.db"
    with Store(db) as store:
        store.append(_metrics("a", "2026-08-09T00:00:00+00:00", likes=1))
        store.append(_metrics("a", "2026-08-10T00:00:00+00:00", likes=5))
        latest_n = export_csv(store, tmp_path / "latest.csv", latest_only=True)
        all_n = export_csv(store, tmp_path / "all.csv", latest_only=False)
    assert latest_n == 1
    assert all_n == 2
    latest_lines = (tmp_path / "latest.csv").read_text(encoding="utf-8-sig").splitlines()
    assert len(latest_lines) == 2  # 헤더 + 최신 1행
    assert ",5," in latest_lines[1] or latest_lines[1].endswith(",5")


def test_export_csv_none_becomes_empty_cell(tmp_path):
    db = tmp_path / "t.db"
    out = tmp_path / "out.csv"
    with Store(db) as store:
        store.append(_metrics(comments=6))  # 나머지 지표 None
        export_csv(store, out)
    row = out.read_text(encoding="utf-8-sig").splitlines()[1].split(",")
    cols = dict(zip(EXPORT_COLUMNS, row))
    assert cols["comments"] == "6"
    assert cols["likes"] == ""


def test_export_csv_escapes_formula_cells(tmp_path):
    """스크랩된 값이 =/+/@/- 로 시작하면 엑셀 수식 실행을 막게 이스케이프한다."""
    db = tmp_path / "t.db"
    out = tmp_path / "out.csv"
    with Store(db) as store:
        store.append(_metrics(
            author="=HYPERLINK(\"https://evil.example\")",
            post_format="-video",
            upload_date="+2026",
        ))
        export_csv(store, out)
    import csv as csv_mod
    with open(out, encoding="utf-8-sig", newline="") as f:
        row = dict(zip(EXPORT_COLUMNS, list(csv_mod.reader(f))[1]))
    assert row["author"].startswith("'=")
    assert row["post_format"] == "'-video"
    assert row["upload_date"] == "'+2026"
    assert row["platform"] == "ctrip"  # 일반 값은 그대로


# --- CLI --export -----------------------------------------------------------

def test_cli_export_flag(tmp_path):
    db = tmp_path / "t.db"
    out = tmp_path / "out.csv"
    with Store(db) as store:
        store.append(_metrics(likes=3))
    code = main(["--export", str(out), "--db", str(db)])
    assert code == 0
    assert out.exists()
    lines = out.read_text(encoding="utf-8-sig").splitlines()
    assert len(lines) == 2


def test_cli_export_empty_db_returns_nonzero(tmp_path):
    db = tmp_path / "empty.db"
    out = tmp_path / "out.csv"
    code = main(["--export", str(out), "--db", str(db)])
    assert code == 1
