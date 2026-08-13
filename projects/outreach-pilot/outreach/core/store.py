"""SQLite 리드 저장소.

cn-crawler 의 store.py 를 참고하되, 이 프로젝트는 시계열이 아니라
리드 목록 관리이므로 (product, name) upsert 로 단순화했다.
channels / evidence_urls 는 JSON 텍스트로 저장한다.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from outreach.core.schema import STATUSES, Lead, dump_json

logger = logging.getLogger(__name__)

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

_COLUMNS = (
    "product", "category", "name", "contact_email", "channels",
    "evidence_urls", "fit_score", "fit_reason", "status", "created_at", "raw",
)


def _to_row(lead: Lead) -> tuple:
    return (
        lead.product, lead.category, lead.name, lead.contact_email,
        dump_json(lead.channels), dump_json(lead.evidence_urls),
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


def _from_row_safe(row: sqlite3.Row) -> Lead | None:
    """검증 실패 행은 None — 불량 행 하나 때문에 전체 조회가 죽지 않게."""
    try:
        return _from_row(row)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("leads id=%s 행이 손상되어 건너뜁니다: %s", row["id"], exc)
        return None


class Store:
    """리드 upsert 와 조회를 제공하는 얇은 SQLite 래퍼."""

    def __init__(self, db_path: str | Path) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def upsert(self, lead: Lead) -> tuple[int, bool]:
        """리드를 저장하고 (row id, 신규 여부)를 반환한다.

        (product, name) 이 이미 있으면 수집 필드만 갱신하고
        status / created_at 은 보존한다 (운영 중 상태를 덮어쓰지 않기 위함).
        커밋하지 않는다 — 호출측에서 배치 단위로 commit() 할 것
        (컨텍스트 매니저 종료 시에도 커밋된다).
        """
        row = self._conn.execute(
            f"INSERT INTO leads ({', '.join(_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in _COLUMNS)}) "
            "ON CONFLICT (product, name) DO NOTHING RETURNING id",
            _to_row(lead),
        ).fetchone()
        if row is not None:
            return row["id"], True

        row = self._conn.execute(
            "UPDATE leads SET category = ?, contact_email = ?, channels = ?, "
            "evidence_urls = ?, fit_score = ?, fit_reason = ?, raw = ? "
            "WHERE product = ? AND name = ? RETURNING id",
            (
                lead.category, lead.contact_email,
                dump_json(lead.channels), dump_json(lead.evidence_urls),
                lead.fit_score, lead.fit_reason, lead.raw,
                lead.product, lead.name,
            ),
        ).fetchone()
        return row["id"], False

    def commit(self) -> None:
        self._conn.commit()

    def get(self, product: str, name: str) -> Lead | None:
        row = self._conn.execute(
            "SELECT * FROM leads WHERE product = ? AND name = ?",
            (product, name),
        ).fetchone()
        return None if row is None else _from_row_safe(row)

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
        rows = self._conn.execute(sql, params).fetchall()
        return [lead for lead in map(_from_row_safe, rows) if lead is not None]

    def update_status(self, lead_id: int, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"status 는 {STATUSES} 중 하나여야 합니다: {status!r}")
        self._conn.execute(
            "UPDATE leads SET status = ? WHERE id = ?", (status, lead_id)
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
