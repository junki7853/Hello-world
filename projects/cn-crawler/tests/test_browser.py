"""브라우저 세션의 순수 로직(쿠키 파싱·도메인 스코핑) 테스트. 실브라우저 불필요."""

import pytest

from crawler.core.browser import _parse_cookie_header, _registrable_domain


@pytest.mark.parametrize(
    "hostname,expected",
    [
        ("m.ctrip.com", ".ctrip.com"),
        ("www.mafengwo.cn", ".mafengwo.cn"),
        ("www.mafengwo.com.cn", ".mafengwo.com.cn"),  # 2차 접미사 + ccTLD
        ("ctrip.com", ".ctrip.com"),
        ("localhost", "localhost"),  # 애매 → 정확 호스트
        ("192.168.0.1", "192.168.0.1"),  # IP → 정확 호스트
    ],
)
def test_registrable_domain(hostname, expected):
    assert _registrable_domain(hostname) == expected


def test_parse_cookie_header_scopes_to_registrable_domain():
    cookies = _parse_cookie_header(
        "sid=abc; token=xyz", "https://m.ctrip.com/webapp/you?articleId=1"
    )
    assert {c["name"] for c in cookies} == {"sid", "token"}
    assert all(c["domain"] == ".ctrip.com" for c in cookies)


def test_parse_cookie_header_skips_malformed_pairs():
    cookies = _parse_cookie_header("good=1; broken; =nokey", "https://m.ctrip.com/x")
    assert [c["name"] for c in cookies] == ["good"]
