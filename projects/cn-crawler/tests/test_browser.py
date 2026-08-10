"""브라우저 세션의 순수 로직(쿠키 파싱·소스 우선순위·프록시) 테스트. 실브라우저 불필요."""

import pytest

from crawler.core.browser import (
    _parse_cookie_header,
    _registrable_domain,
    build_proxy_config,
    resolve_cookie_source,
)


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


# --- 쿠키 소스 우선순위 (플랫폼별 > 전역, storage_state 분기) ------------------

def test_cookie_source_none_when_nothing_set():
    assert resolve_cookie_source("xiaohongshu", environ={}) is None
    assert resolve_cookie_source(None, environ={}) is None


def test_cookie_source_global_fallback_backward_compat():
    env = {"CRAWLER_COOKIES": "sid=global"}
    # 플랫폼 지정이 없어도(기존 호출) 전역 헤더를 그대로 쓴다
    assert resolve_cookie_source(None, environ=env) == ("header", "sid=global")
    # 플랫폼별 설정이 없으면 전역으로 폴백한다
    assert resolve_cookie_source("douyin", environ=env) == ("header", "sid=global")


def test_cookie_source_platform_env_beats_global():
    env = {
        "CRAWLER_COOKIES": "sid=global",
        "CRAWLER_COOKIES_XIAOHONGSHU": "sid=xhs",
    }
    assert resolve_cookie_source("xiaohongshu", environ=env) == ("header", "sid=xhs")
    # 대소문자: 플랫폼명은 대문자 env 키로 매핑된다
    assert resolve_cookie_source("dianping", environ=env) == ("header", "sid=global")


def test_cookie_source_dir_json_is_storage_state(tmp_path):
    (tmp_path / "xiaohongshu.json").write_text('{"cookies": []}', encoding="utf-8")
    env = {"CRAWLER_COOKIES_DIR": str(tmp_path), "CRAWLER_COOKIES": "sid=global"}
    kind, value = resolve_cookie_source("xiaohongshu", environ=env)
    assert kind == "storage_state"
    assert value.endswith("xiaohongshu.json")


def test_cookie_source_dir_txt_is_header(tmp_path):
    (tmp_path / "dianping.txt").write_text("sid=fromfile\n", encoding="utf-8")
    env = {"CRAWLER_COOKIES_DIR": str(tmp_path)}
    assert resolve_cookie_source("dianping", environ=env) == ("header", "sid=fromfile")


def test_cookie_source_priority_env_beats_dir_json(tmp_path):
    (tmp_path / "xiaohongshu.json").write_text("{}", encoding="utf-8")
    env = {
        "CRAWLER_COOKIES_DIR": str(tmp_path),
        "CRAWLER_COOKIES_XIAOHONGSHU": "sid=env",
    }
    # 우선순위: 플랫폼 env 헤더 > 디렉터리 storage_state
    assert resolve_cookie_source("xiaohongshu", environ=env) == ("header", "sid=env")


def test_cookie_source_priority_json_beats_txt(tmp_path):
    (tmp_path / "dianping.json").write_text("{}", encoding="utf-8")
    (tmp_path / "dianping.txt").write_text("sid=txt", encoding="utf-8")
    env = {"CRAWLER_COOKIES_DIR": str(tmp_path)}
    kind, value = resolve_cookie_source("dianping", environ=env)
    assert kind == "storage_state"
    assert value.endswith("dianping.json")


def test_cookie_source_empty_txt_falls_through_to_global(tmp_path):
    (tmp_path / "dianping.txt").write_text("   \n", encoding="utf-8")
    env = {"CRAWLER_COOKIES_DIR": str(tmp_path), "CRAWLER_COOKIES": "sid=global"}
    # 빈 txt 는 소스로 치지 않고 전역으로 폴백
    assert resolve_cookie_source("dianping", environ=env) == ("header", "sid=global")


# --- 프록시 설정 (인증 파싱) --------------------------------------------------

def test_proxy_none_when_unset():
    assert build_proxy_config(environ={}) is None


def test_proxy_server_only_backward_compat():
    env = {"CRAWLER_PROXY": "http://proxy.example:8080"}
    assert build_proxy_config(environ=env) == {"server": "http://proxy.example:8080"}


def test_proxy_embedded_credentials_are_split_out():
    env = {"CRAWLER_PROXY": "http://user:pass@proxy.example:8080"}
    assert build_proxy_config(environ=env) == {
        "server": "http://proxy.example:8080",
        "username": "user",
        "password": "pass",
    }


def test_proxy_separate_env_credentials():
    env = {
        "CRAWLER_PROXY": "http://proxy.example:8080",
        "CRAWLER_PROXY_USERNAME": "u1",
        "CRAWLER_PROXY_PASSWORD": "p1",
    }
    assert build_proxy_config(environ=env) == {
        "server": "http://proxy.example:8080",
        "username": "u1",
        "password": "p1",
    }


def test_proxy_separate_env_overrides_embedded():
    env = {
        "CRAWLER_PROXY": "http://embed_u:embed_p@proxy.example:8080",
        "CRAWLER_PROXY_USERNAME": "override_u",
        "CRAWLER_PROXY_PASSWORD": "override_p",
    }
    assert build_proxy_config(environ=env) == {
        "server": "http://proxy.example:8080",
        "username": "override_u",
        "password": "override_p",
    }
