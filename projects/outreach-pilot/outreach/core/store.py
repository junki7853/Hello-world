"""SQLite 리드 저장소.

cn-crawler 의 store.py(자동 마이그레이션 패턴)를 참고하되, 이 프로젝트는
시계열이 아니라 리드 목록 관리이므로 (product, name) upsert 로 단순화했다.
channels / evidence_urls 는 JSON 텍스트로 저장한다.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from outreach.core.schema import STATUSES, Lead

_SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product TEXT NOT NULL,
    category TEXT NOT NULL,
    name TEXT NOT NULL,
    contact_email TEXT,
    channels TEXT NOT NULL DEFAULT '{}',
    evidence_urls TEXT NOT NULL DEFAULT '[]',
    fit_score INTEGER NOT NULL DEFAULT 0,
    fit_reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL,
    raw TEXT,
    UNIQUE (product, name)
);
"""

# 이후 Phase 에서 컬럼이 늘면 여기에 추가한다 (기존 DB 파일은 열 때 ALTER).
_MIGRATION_COLUMNS: dict[str, str] = {}

_COLUMNS = (
    "product", "category", "name", "contact_email", "channels",
    "evidence_urls", "fit_score", "fit_reason", "status", "created_at", "raw",
)


def _to_row(lead: Lead) -> tuple:
    return (
        lead.product, lead.category, lead.name, lead.contact_email,
        json.dumps(lead.channels, ensure_ascii=False, sort_keys=True),
        json.dumps(lead.evidence_urls, ensure_ascii=False),
        lead.fit_score, lead.fit_reason, lead.status, lead.created_at, lead.raw,
    )


def _from_row(row: sqlite3.Row) -> Lead:
    return Lead(
        id=row["id"],
        product=row["product"],
        category=row["category"],
        name=row["name"],
        contact_email=row["contact_email"],
        channels=json.loads(row["channels"]),
        evidence_urls=json.loads(row["evidence_urls"]),
        fit_score=row["fit_score"],
        fit_reason=row["fit_reason"],
        status=row["status"],
        created_at=row["created_at"],
        raw=row["raw"],
    )


class Store:
    """리드 upsert 와 조회를 제공하는 얇은 SQLite 래퍼."""

    def __init__(self, db_path: str | Path) -> None:
        path = Path(db_path)
        if str(path.parent) != ".":
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        existing = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(leads)")
        }
        for column, col_type in _MIGRATION_COLUMNS.items():
            if column not in existing:
                self._conn.execute(f"ALTER TABLE leads ADD COLUMN {column} {col_type}")
        self._conn.commit()

    def upsert(self, lead: Lead) -> tuple[int, bool]:
        """리드를 저장하고 (row id, 신규 여부)를 반환한다.

        (product, name) 이 이미 있으면 수집 필드만 갱신하고
        status / created_at 은 보존한다 (운영 중 상태를 덮어쓰지 않기 위함).
        """
        existing = self.get(lead.product, lead.name)
        if existing is None:
            cursor = self._conn.execute(
                f"INSERT INTO leads ({', '.join(_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in _COLUMNS)})",
                _to_row(lead),
            )
            self._conn.commit()
            return cursor.lastrowid, True

        self._conn.execute(
            "UPDATE leads SET category = ?, contact_email = ?, channels = ?, "
            "evidence_urls = ?, fit_score = ?, fit_reason = ?, raw = ? "
            "WHERE id = ?",
            (
                lead.category, lead.contact_email,
                json.dumps(lead.channels, ensure_ascii=False, sort_keys=True),
                json.dumps(lead.evidence_urls, ensure_ascii=False),
                lead.fit_score, lead.fit_reason, lead.raw, existing.id,
            ),
        )
        self._conn.commit()
        return existing.id, False

    def get(self, product: str, name: str) -> Lead | None:
        row = self._conn.execute(
            "SELECT * FROM leads WHERE product = ? AND name = ?",
            (product, name),
        ).fetchone()
        return None if row is None else _from_row(row)

    def list_leads(
        self, product: str | None = None, status: str | None = None
    ) -> list[Lead]:
        sql = "SELECT * FROM leads"
        conditions, params = [], []
        if product is not None:
            conditions.append("product = ?")
            params.append(product)
        if status is not None:
            conditions.append("status = ?")
            params.append(status)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY fit_score DESC, id ASC"
        return [_from_row(r) for r in self._conn.execute(sql, params).fetchall()]

    def update_status(self, lead_id: int, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"status 는 {STATUSES} 중 하나여야 합니다: {status!r}")
        self._conn.execute(
            "UPDATE leads SET status = ? WHERE id = ?", (status, lead_id)
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
