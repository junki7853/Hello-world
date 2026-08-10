"""CLI 파싱 스모크 테스트 (브라우저 없이)."""

import pytest

from crawler.cli import (
    build_arg_parser,
    load_targets_from_csv,
    parse_inline_target,
    resolve_targets,
)


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
