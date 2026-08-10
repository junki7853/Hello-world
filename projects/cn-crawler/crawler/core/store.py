"""SQLite append-only 시계열 저장소.

같은 게시물을 반복 수집하면 매번 새 행이 쌓인다 → 지표 변화를 시계열로 추적.
"최신값"은 쿼리(latest)로 얻는다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from pathlib import Path

from crawler.core.schema import Metrics

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
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
    impressions INTEGER,
    followers INTEGER,
    upload_date TEXT,
    post_format TEXT,
    collected_at TEXT NOT NULL,
    raw TEXT
);
CREATE INDEX IF NOT EXISTS idx_snapshots_article
    ON snapshots (platform, article_id, collected_at);
"""

_COLUMNS = (
    "platform", "article_id", "url", "title", "author",
    "views", "likes", "collects", "comments", "shares",
    "impressions", "followers", "upload_date", "post_format",
    "collected_at", "raw",
)

# Phase 1 DB 파일에 없던 컬럼 → 타입. 열 때 ALTER 로 채운다 (기존 행은 NULL).
_MIGRATION_COLUMNS = {
    "impressions": "INTEGER",
    "followers": "INTEGER",
    "upload_date": "TEXT",
    "post_format": "TEXT",
}


class Store:
    """스냅샷 append 와 최신값 조회만 제공하는 얇은 SQLite 래퍼."""

    def __init__(self, db_path: str | Path) -> None:
        path = Path(db_path)
        if str(path.parent) != ".":
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Phase 1 DB 파일에 신규 컬럼을 추가한다 (기존 시계열 행 보존)."""
        existing = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(snapshots)")
        }
        for column, col_type in _MIGRATION_COLUMNS.items():
            if column not in existing:
                self._conn.execute(
                    f"ALTER TABLE snapshots ADD COLUMN {column} {col_type}"
                )
        self._conn.commit()

    def append(self, metrics: Metrics) -> int:
        """스냅샷 1행을 추가하고 row id 를 반환한다."""
        row = asdict(metrics)
        placeholders = ", ".join("?" for _ in _COLUMNS)
        sql = f"INSERT INTO snapshots ({', '.join(_COLUMNS)}) VALUES ({placeholders})"
        cursor = self._conn.execute(sql, tuple(row[c] for c in _COLUMNS))
        self._conn.commit()
        return cursor.lastrowid

    def latest(self, platform: str, article_id: str) -> Metrics | None:
        """해당 게시물의 가장 최근 스냅샷을 반환한다. 없으면 None."""
        row = self._conn.execute(
            "SELECT * FROM snapshots WHERE platform = ? AND article_id = ? "
            "ORDER BY collected_at DESC, id DESC LIMIT 1",
            (platform, article_id),
        ).fetchone()
        if row is None:
            return None
        return Metrics(**{c: row[c] for c in _COLUMNS})

    def history(self, platform: str, article_id: str) -> list[Metrics]:
        """해당 게시물의 스냅샷 전체를 시간순으로 반환한다."""
        rows = self._conn.execute(
            "SELECT * FROM snapshots WHERE platform = ? AND article_id = ? "
            "ORDER BY collected_at ASC, id ASC",
            (platform, article_id),
        ).fetchall()
        return [Metrics(**{c: row[c] for c in _COLUMNS}) for row in rows]

    def latest_all(self) -> list[Metrics]:
        """플랫폼·게시물별 최신 스냅샷 1행씩을 반환한다 (export 용)."""
        rows = self._conn.execute(
            "SELECT * FROM snapshots s WHERE id = ("
            "  SELECT id FROM snapshots s2"
            "  WHERE s2.platform = s.platform AND s2.article_id = s.article_id"
            "  ORDER BY collected_at DESC, id DESC LIMIT 1"
            ") ORDER BY platform, article_id",
        ).fetchall()
        return [Metrics(**{c: row[c] for c in _COLUMNS}) for row in rows]

    def all_snapshots(self) -> list[Metrics]:
        """전체 스냅샷 이력을 (플랫폼, 게시물, 시간) 순으로 반환한다 (export 용)."""
        rows = self._conn.execute(
            "SELECT * FROM snapshots ORDER BY platform, article_id, collected_at, id",
        ).fetchall()
        return [Metrics(**{c: row[c] for c in _COLUMNS}) for row in rows]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
