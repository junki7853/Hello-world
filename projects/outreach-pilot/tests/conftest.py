"""공용 픽스처."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 프로젝트 루트를 import 경로에 추가 (pip install -e 없이 실행 가능하게)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from outreach.core.schema import Lead  # noqa: E402


@pytest.fixture
def make_lead():
    """기본값이 채워진 Lead 팩토리."""

    def _make(**overrides) -> Lead:
        defaults = dict(
            product="테스트상품",
            category="업체",
            name="테스트파트너",
            evidence_urls=["https://example.com"],
            fit_score=80,
            fit_reason="적합",
            created_at="2026-08-14T00:00:00+00:00",
        )
        defaults.update(overrides)
        return Lead(**defaults)

    return _make
