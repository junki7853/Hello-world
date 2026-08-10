"""SQLite append-only 시계열 저장소 스모크 테스트."""

from crawler.core.schema import Metrics
from crawler.core.store import Store


def _metrics(likes: int, collected_at: str) -> Metrics:
    return Metrics(
        platform="ctrip",
        article_id="266207894",
        url="https://m.ctrip.com/x?articleId=266207894",
        likes=likes,
        collects=14,
        comments=6,
        collected_at=collected_at,
    )


def test_append_returns_incrementing_ids(tmp_path):
    db = tmp_path / "t.db"
    with Store(db) as store:
        id1 = store.append(_metrics(38, "2026-08-10T00:00:00+00:00"))
        id2 = store.append(_metrics(39, "2026-08-10T01:00:00+00:00"))
    assert id1 == 1
    assert id2 == 2


def test_append_only_keeps_every_snapshot(tmp_path):
    """같은 게시물을 반복 수집하면 행이 누적되어 시계열이 된다."""
    db = tmp_path / "t.db"
    with Store(db) as store:
        store.append(_metrics(38, "2026-08-10T00:00:00+00:00"))
        store.append(_metrics(40, "2026-08-10T01:00:00+00:00"))
        store.append(_metrics(45, "2026-08-10T02:00:00+00:00"))
        history = store.history("ctrip", "266207894")
    assert [m.likes for m in history] == [38, 40, 45]


def test_latest_returns_most_recent_snapshot(tmp_path):
    db = tmp_path / "t.db"
    with Store(db) as store:
        store.append(_metrics(38, "2026-08-10T00:00:00+00:00"))
        store.append(_metrics(50, "2026-08-10T05:00:00+00:00"))
        store.append(_metrics(45, "2026-08-10T03:00:00+00:00"))
        latest = store.latest("ctrip", "266207894")
    assert latest is not None
    assert latest.likes == 50  # collected_at 이 가장 늦은 행


def test_latest_returns_none_when_absent(tmp_path):
    db = tmp_path / "t.db"
    with Store(db) as store:
        assert store.latest("ctrip", "does-not-exist") is None


def test_creates_parent_directory(tmp_path):
    db = tmp_path / "nested" / "dir" / "t.db"
    with Store(db) as store:
        store.append(_metrics(38, "2026-08-10T00:00:00+00:00"))
    assert db.exists()


def test_roundtrip_preserves_none_metrics(tmp_path):
    db = tmp_path / "t.db"
    m = Metrics(platform="ctrip", article_id="1", url="https://x", comments=6)
    with Store(db) as store:
        store.append(m)
        latest = store.latest("ctrip", "1")
    assert latest.comments == 6
    assert latest.likes is None
    assert latest.views is None
