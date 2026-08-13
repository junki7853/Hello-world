"""상품 프로필 로더 테스트."""

import pytest

from outreach.research.profile import ProductProfile, load_profile


def _write(tmp_path, text: str):
    path = tmp_path / "profile.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_정상_로드(tmp_path):
    path = _write(tmp_path, (
        'product: "유산균"\n'
        'description: "장 건강 프로바이오틱스"\n'
        "categories:\n  - 업체\n  - 인플루언서\n"
        "region: 국내\n"
    ))
    profile = load_profile(path)
    assert profile.product == "유산균"
    assert profile.categories == ["업체", "인플루언서"]
    assert profile.region == "국내"


def test_기본값_categories_region(tmp_path):
    profile = load_profile(_write(tmp_path, 'product: "유산균"\ndescription: "설명"\n'))
    assert profile.categories == ["업체"]
    assert profile.region == "국내"


def test_예시_프로필_로드(tmp_path):
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / "profiles" / "example.yaml"
    profile = load_profile(example)
    assert profile.product
    assert profile.categories


def test_product_없으면_에러(tmp_path):
    with pytest.raises(ValueError, match="product"):
        load_profile(_write(tmp_path, 'description: "설명만"\n'))


def test_모르는_카테고리_에러(tmp_path):
    with pytest.raises(ValueError, match="카테고리"):
        load_profile(_write(tmp_path, 'product: "p"\ncategories: [외계인]\n'))


def test_모르는_키_에러(tmp_path):
    with pytest.raises(ValueError, match="알 수 없는 프로필 키"):
        load_profile(_write(tmp_path, 'product: "p"\nprice: 10000\n'))


def test_dict_아닌_YAML_에러(tmp_path):
    with pytest.raises(ValueError, match="형식"):
        load_profile(_write(tmp_path, "- 항목1\n- 항목2\n"))


def test_직접_생성_빈_categories_에러():
    with pytest.raises(ValueError, match="categories"):
        ProductProfile(product="p", description="d", categories=[])
