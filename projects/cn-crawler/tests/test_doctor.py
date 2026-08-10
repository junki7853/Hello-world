"""doctor 판정 로직(월 감지→상태 매핑, 지표 유무) 테스트. 실브라우저 불필요."""

import io
import json

from crawler.core.doctor import (
    APP_WALL,
    CAPTCHA_WALL,
    LOGIN_WALL,
    NO_DATA,
    OK,
    REDIRECTED,
    classify_metrics,
    emit_report,
    format_report,
)
from crawler.core.schema import Metrics


def _metrics(raw=None, **values):
    return Metrics(
        platform="x",
        article_id="1",
        url="https://x",
        raw=json.dumps(raw) if raw is not None else None,
        **values,
    )


def test_login_wall_maps_to_login_status():
    m = _metrics(raw={"login_wall": True})
    status, _ = classify_metrics(m)
    assert status == LOGIN_WALL


def test_security_wall_maps_to_login_status():
    status, _ = classify_metrics(_metrics(raw={"security_wall": True}))
    assert status == LOGIN_WALL


def test_captcha_wall_maps_to_captcha_status():
    status, _ = classify_metrics(_metrics(raw={"captcha_wall": True}))
    assert status == CAPTCHA_WALL


def test_verify_wall_maps_to_captcha_status():
    status, _ = classify_metrics(_metrics(raw={"verify_wall": True}))
    assert status == CAPTCHA_WALL


def test_mafengwo_captcha_detected_key_maps_to_captcha():
    # 마펑워는 *_wall 대신 captcha_detected 로 기록한다
    status, _ = classify_metrics(_metrics(raw={"captcha_detected": True}))
    assert status == CAPTCHA_WALL


def test_app_wall_maps_to_app_status():
    status, _ = classify_metrics(_metrics(raw={"app_wall": True}))
    assert status == APP_WALL


def test_redirected_away_maps_to_redirected():
    status, _ = classify_metrics(_metrics(raw={"redirected_away": True}))
    assert status == REDIRECTED


def test_wall_takes_priority_over_present_values():
    # 월이 감지되면 (엉뚱한) 지표값이 있어도 월 상태가 이긴다
    status, _ = classify_metrics(_metrics(raw={"login_wall": True}, likes=10))
    assert status == LOGIN_WALL


def test_values_present_without_wall_is_ok():
    status, detail = classify_metrics(_metrics(raw={}, likes=123, comments=45))
    assert status == OK
    assert "likes=123" in detail and "comments=45" in detail


def test_ok_notes_font_obfuscation_warning():
    status, detail = classify_metrics(
        _metrics(raw={"font_obfuscation_detected": True}, likes=5)
    )
    assert status == OK
    assert "난독화" in detail


def test_no_wall_no_values_is_no_data():
    status, _ = classify_metrics(_metrics(raw={}))
    assert status == NO_DATA


def test_missing_or_bad_raw_defaults_to_no_data():
    assert classify_metrics(_metrics(raw=None))[0] == NO_DATA
    m = Metrics(platform="x", article_id="1", url="https://x", raw="not-json")
    assert classify_metrics(m)[0] == NO_DATA


def test_format_report_lists_each_platform():
    report = format_report(
        [("douyin", OK, "정상"), ("xiaohongshu", LOGIN_WALL, "쿠키 필요")]
    )
    assert "douyin" in report and "xiaohongshu" in report
    assert OK in report and LOGIN_WALL in report


def test_emit_report_survives_encoding_limited_stream():
    # Windows 콘솔(cp949) 재현: 인코딩이 제한된 스트림에 비-ASCII(em-dash·⚠·한글)를
    # 써도 UnicodeEncodeError 로 크래시하지 않아야 한다.
    report = format_report(
        [("douyin", OK, "정상 렌더 — ⚠ 폰트 난독화 감지")]
    )
    for encoding in ("ascii", "cp949"):
        stream = io.TextIOWrapper(
            io.BytesIO(), encoding=encoding, errors="strict", newline=""
        )
        emit_report(report, stream=stream)  # 예외가 나면 테스트 실패
        stream.flush()
        stream.seek(0)
        # 출력이 실제로 기록됐는지(플랫폼명은 두 인코딩 모두 표현 가능)
        assert "douyin" in stream.buffer.getvalue().decode(encoding, errors="replace")
