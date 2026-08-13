# outreach-pilot

영업/마케팅 아웃리치 자동화 베타. 상품 프로필(YAML)을 입력하면 Claude API의
웹서치 도구로 관련 잠재 파트너(판매업체·인플루언서·물류·마케팅 대행 등)를
수집·정규화해 SQLite 에 저장하고 CSV 로 내보낸다.

**Phase 1 범위**: 리드 리서치 엔진까지. 발송(Campaign)·응대(Thread)는
Phase 2~3 에서 확장 예정이며 현재는 스키마 골격만 있다.

## 구조

```
outreach-pilot/
├── outreach/
│   ├── cli.py              # CLI 진입점 (research / export)
│   ├── core/
│   │   ├── schema.py       # Lead·Campaign·Thread 스키마
│   │   ├── store.py        # SQLite 저장소 (upsert: product+name)
│   │   └── export.py       # CSV 내보내기 (UTF-8-BOM)
│   └── research/
│       ├── profile.py      # 상품 프로필 YAML 로더
│       ├── engine.py       # Claude API + 웹서치 리서치 엔진
│       └── normalize.py    # 응답 → Lead 정규화 (근거 없는 항목 폐기)
├── profiles/example.yaml   # 상품 프로필 예시 (건강식품)
├── tests/                  # pytest (API 호출은 mock, 실호출은 -m live)
└── data/                   # 수집 DB (gitignore)
```

## 설치

```bash
cd projects/outreach-pilot
python -m venv .venv
.venv\Scripts\activate        # Windows (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env        # 이후 .env 에 ANTHROPIC_API_KEY 입력
```

`.env` 는 절대 커밋하지 않는다 (저장소 루트 `.gitignore` 에 포함됨).

## 실행

```bash
# 1) 상품 프로필로 잠재 파트너 수집 → data/leads.db 저장
python -m outreach research --profile profiles/example.yaml

# 옵션: 카테고리당 수집 건수·웹서치 상한(비용 제어)
python -m outreach research --profile profiles/example.yaml --max-leads 5 --max-searches 3

# 2) CSV 내보내기 (UTF-8-BOM, 엑셀에서 바로 열림)
python -m outreach export --csv leads.csv
python -m outreach export --csv leads.csv --product "프리미엄 유산균 밸런스" --status new
```

### 상품 프로필 형식

```yaml
product: "상품명"            # 필수
description: "상품 설명"      # 프롬프트에 그대로 들어감
categories: [업체, 인플루언서, 물류, 마케팅, 기타]  # 이 중에서 선택
region: 국내                 # 기본 국내
```

## 비용 제어

- `--max-searches N`: 카테고리(요청) 1건당 웹서치 호출 상한 (기본 5, 웹서치는 1,000건당 $10)
- `--max-leads N`: 카테고리당 수집 건수 상한 (기본 10)
- `OUTREACH_MODEL` 환경변수로 모델 교체 가능 (기본 `claude-opus-5`)

## 테스트

```bash
pytest                # 단위 테스트 (Claude API 는 mock)
pytest -m live        # 실제 API 호출 테스트 (ANTHROPIC_API_KEY 필요, 비용 발생)
```

## 리드 상태

`new`(수집됨) → `queued`(발송 대기) → `sent` → `replied` → `manual`(수동 개입) / `closed`(종료)

같은 (상품, 파트너명) 리드를 재수집하면 조사 필드만 갱신되고 status 는 보존된다.
