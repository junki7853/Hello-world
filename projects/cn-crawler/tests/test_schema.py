"""정규화 스키마 스모크 테스트."""

import pytest

from crawler.core.schema import Metrics, parse_count, utc_now_iso


@pytest.mark.parametrize(
    "text,expected",
    [
        ("38", 38),
        ("14", 14),
        ("共6条评论", 6),
        ("1.2万", 12_000),
        ("3万", 30_000),
        ("2.5w", 25_000),
        ("1亿", 100_000_000),
        ("赞 1024", 1024),
        ("1,024", 1024),
        ("1,234,567", 1_234_567),
        ("", None),
        (None, None),
        ("没有数字", None),
    ],
    ids=[
        "plain", "plain2", "comment-phrase", "wan-decimal", "wan-int",
        "w-lowercase", "yi", "prefixed", "thousands-comma", "millions-comma",
        "empty", "none", "no-digits",
    ],
)
def test_parse_count(text, expected):
    assert parse_count(text) == expected


def test_metrics_defaults_missing_to_none():
    m = Metrics(platform="ctrip", article_id="123", url="https://x")
    assert m.views is None
    assert m.likes is None
    assert m.collects is None
    assert m.comments is None
    assert m.shares is None
    assert m.title is None


def test_metrics_collected_at_is_iso_utc():
    m = Metrics(platform="ctrip", article_id="123", url="https://x")
    assert m.collected_at.endswith("+00:00")
    assert "T" in m.collected_at


def test_utc_now_iso_format():
    stamp = utc_now_iso()
    assert stamp.endswith("+00:00")
