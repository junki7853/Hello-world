"""리드 CSV 내보내기 (UTF-8-BOM — 엑셀에서 한글 깨짐 방지)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from outreach.core.schema import Lead

_FIELDS = (
    "id", "product", "category", "name", "contact_email", "channels",
    "evidence_urls", "fit_score", "fit_reason", "status", "created_at",
)


def export_csv(leads: list[Lead], csv_path: str | Path) -> int:
    """리드 목록을 CSV 로 저장하고 행 수를 반환한다."""
    path = Path(csv_path)
    if str(path.parent) != ".":
        path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDS)
        writer.writeheader()
        for lead in leads:
            writer.writerow({
                "id": lead.id,
                "product": lead.product,
                "category": lead.category,
                "name": lead.name,
                "contact_email": lead.contact_email or "",
                "channels": json.dumps(lead.channels, ensure_ascii=False, sort_keys=True),
                "evidence_urls": " ".join(lead.evidence_urls),
                "fit_score": lead.fit_score,
                "fit_reason": lead.fit_reason,
                "status": lead.status,
                "created_at": lead.created_at,
            })
    return len(leads)
