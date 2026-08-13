"""상품 프로필 YAML 로더."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from outreach.core.schema import CATEGORIES


@dataclass
class ProductProfile:
    """리서치 입력: 어떤 상품을 누구에게 소개할지."""

    product: str
    description: str
    categories: list[str] = field(default_factory=lambda: ["업체"])
    region: str = "국내"

    def __post_init__(self) -> None:
        if not self.product:
            raise ValueError("product 는 필수입니다")
        if not self.categories:
            raise ValueError("categories 는 최소 1개 필요합니다")
        unknown = [c for c in self.categories if c not in CATEGORIES]
        if unknown:
            raise ValueError(
                f"알 수 없는 카테고리 {unknown} — 사용 가능: {CATEGORIES}"
            )


def load_profile(path: str | Path) -> ProductProfile:
    """YAML 파일에서 상품 프로필을 읽는다."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"프로필 형식이 잘못되었습니다: {path}")
    known = {"product", "description", "categories", "region"}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"알 수 없는 프로필 키: {sorted(unknown)} (사용 가능: {sorted(known)})")
    return ProductProfile(
        product=data.get("product", ""),
        description=data.get("description", ""),
        categories=list(data.get("categories") or ["업체"]),
        region=data.get("region") or "국내",
    )
