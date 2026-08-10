"""CLI 파싱 스모크 테스트 (브라우저 없이)."""

import pytest

import crawler.cli as cli
from crawler.adapters.base import UnsupportedUrlError
from crawler.cli import (
    build_arg_parser,
    collect_targets,
    load_targets_from_csv,
    parse_inline_target,
    resolve_targets,
)
from crawler.core.schema import Metrics


def test_parse_inline_target():
    platform, url = parse_inline_target("ctrip=https://m.ctrip.com/x?articleId=1")
    assert platform == "ctrip"
    assert url == "https://m.ctrip.com/x?articleId=1"


def test_parse_inline_target_rejects_missing_equals():
    with pytest.raises(ValueError):
        parse_inline_target("no-equals-sign")


def test_load_targets_from_csv(tmp_path):
    csv_path = tmp_path / "targets.csv"
    csv_path.write_text(
        "platform,url\nctrip,https://m.ctrip.com/x?articleId=1\n"
        "mafengwo,https://www.mafengwo.cn/i/2.html\n",
        encoding="utf-8",
    )
    targets = load_targets_from_csv(csv_path)
    assert targets == [
        ("ctrip", "https://m.ctrip.com/x?articleId=1"),
        ("mafengwo", "https://www.mafengwo.cn/i/2.html"),
    ]


def test_load_targets_skips_blank_rows(tmp_path):
    csv_path = tmp_path / "targets.csv"
    csv_path.write_text(
        "platform,url\nctrip,https://x\n,\n  ,  \n", encoding="utf-8"
    )
    assert load_targets_from_csv(csv_path) == [("ctrip", "https://x")]


def test_load_targets_rejects_bad_header(tmp_path):
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text("name,link\nctrip,https://x\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_targets_from_csv(csv_path)


def test_resolve_targets_from_inline():
    args = build_arg_parser().parse_args(
        ["--url", "ctrip=https://x?articleId=1", "--url", "ctrip=https://y?articleId=2"]
    )
    assert resolve_targets(args) == [
        ("ctrip", "https://x?articleId=1"),
        ("ctrip", "https://y?articleId=2"),
    ]


def test_parser_requires_a_source():
    with pytest.raises(SystemExit):
        build_arg_parser().parse_args([])


# --- 배치 견고성 ------------------------------------------------------------

class _FakeSession:
    """BrowserSession 대역 — 브라우저를 띄우지 않는다."""

    def __init__(self, headless=True):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        pass


class _FakeAdapter:
    """'bad' 는 미지원 URL, 'buggy' 는 수집부의 일반 ValueError,
    'boom' 은 예기치 못한 실패, 나머지는 성공."""

    def __init__(self, session):
        pass

    def collect(self, url):
        if "bad" in url:
            raise UnsupportedUrlError(f"URL 에서 id 를 찾을 수 없습니다: {url}")
        if "buggy" in url:
            raise ValueError("본문 파싱부의 예기치 못한 ValueError")
        if "boom" in url:
            raise RuntimeError("navigation crashed")
        return Metrics(platform="fake", article_id="1", url=url)


def test_collect_targets_one_bad_url_does_not_skip_batch(
    monkeypatch, tmp_path, caplog
):
    """실패 종류와 무관하게 다음 타깃을 계속하되, 미지원 URL(UnsupportedUrlError)만
    스택 없는 경고로 구분하고 일반 ValueError 는 수집 실패(스택 경로)로 남긴다."""
    monkeypatch.setenv("CRAWLER_DELAY_MIN", "0")
    monkeypatch.setenv("CRAWLER_DELAY_MAX", "0")
    monkeypatch.setattr(cli, "BrowserSession", _FakeSession)
    monkeypatch.setitem(cli.ADAPTER_REGISTRY, "fake", _FakeAdapter)
    targets = [
        ("fake", "https://x/bad"),
        ("fake", "https://x/buggy"),
        ("fake", "https://x/boom"),
        ("fake", "https://x/ok"),
    ]
    with caplog.at_level("WARNING"):
        saved = collect_targets(targets, db_path=str(tmp_path / "t.db"), headless=True)
    assert saved == 1
    assert caplog.text.count("지원하지 않는 URL 형식") == 1  # bad 만 미지원으로
    assert caplog.text.count("수집 실패") == 2  # buggy(일반 ValueError)·boom
