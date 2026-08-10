"""수집 결과 CSV export (마케팅 트래킹 시트용).

컬럼 순서는 트래킹 시트 관례를 따르고, 엑셀 한글 호환을 위해 UTF-8-BOM 으로 쓴다.
"""

from __future__ import annotations

import csv
from pathlib import Path

from crawler.core.store import Store

# 마케팅 트래킹용 컬럼 순서 (Metrics 필드의 부분집합)
EXPORT_COLUMNS = (
    "platform", "article_id", "url", "author", "post_format", "upload_date",
    "followers", "impressions", "views", "likes", "collects", "comments",
    "shares", "collected_at",
)


def export_csv(store: Store, out_path: str | Path, *, latest_only: bool = True) -> int:
    """DB 내용을 CSV 로 내보낸다. 쓴 데이터 행 수를 반환한다.

    latest_only=True 면 플랫폼·게시물별 최신 스냅샷만, False 면 전체 이력.
    """
    rows = store.latest_all() if latest_only else store.all_snapshots()
    path = Path(out_path)
    if str(path.parent) != ".":
        path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig: 엑셀이 한글을 올바르게 인식하도록 BOM 을 붙인다
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(EXPORT_COLUMNS)
        for m in rows:
            writer.writerow([getattr(m, c) for c in EXPORT_COLUMNS])
    return len(rows)
