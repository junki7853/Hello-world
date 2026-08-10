# cn-crawler — 중국 플랫폼 참여지표 크롤러

중국 여행/콘텐츠 플랫폼(씨트립·마펑워·디엔핑·샤오홍슈·더우인 등) 게시물의 **참여지표(조회·좋아요·저장·댓글·공유)를 시계열로 수집**하는 크롤러다.

핵심은 **안티봇 엔지니어링 데모**다. 플랫폼의 서명(sign) 알고리즘을 리버싱하지 않는다. 대신 **Playwright 헤드리스 브라우저로 실제 페이지를 렌더링**해서, 브라우저가 알아서 계산한 서명이 붙은 XHR 응답을 가로채거나 렌더된 DOM 텍스트를 읽는다. 플랫폼이 서명 로직을 바꿔도 우리 코드는 바꿀 필요가 없다는 것이 이 접근의 장점이다.

## 설계

```
crawler/
  core/
    browser.py    Playwright 세션 (모바일 UA·stealth 기본, 쿠키/프록시 주입)
    schema.py     정규화 스키마 Metrics(dataclass) + 중국어 카운트 파서(만/억)
    store.py      SQLite append-only 시계열 저장소
    ratelimit.py  요청 간 정중한 무작위 지연
    log.py        로깅 설정
  adapters/
    base.py       어댑터 계약: collect(url) -> Metrics
    ctrip.py      씨트립 레퍼런스 어댑터 (첫 구현체)
  cli.py          CSV/인라인 타깃 → 순차 수집 → 저장
```

### 정규화 스키마 (`Metrics`)

플랫폼이 무엇이든 게시물 1건은 아래 한 행으로 정규화된다. 플랫폼에 없는 지표는 `None`.

`platform, article_id, url, title?, author?, views?, likes?, collects?, comments?, shares?, collected_at(UTC ISO), raw`

### 저장 (append-only 시계열)

같은 게시물을 반복 수집하면 매번 **새 행**이 쌓인다 → 조회수·좋아요 변화를 시계열로 추적한다. "최신값"은 덮어쓰지 않고 쿼리(`Store.latest`)로 얻는다. 인덱스: `(platform, article_id, collected_at)`.

### 어댑터 계약

플랫폼 차이는 어댑터 안에만 격리한다. 내부 흐름은 공통이다:

```
navigate → (셀렉터 대기 | XHR 응답 가로채기) → 추출 → 정규화(Metrics)
```

## 설치

```bash
pip install -r requirements.txt
python -m playwright install chromium   # 브라우저 바이너리 1회 설치
```

## 실행

```bash
# CSV 타깃 목록으로 (헤더: platform,url)
cp targets.example.csv targets.csv   # 실제 URL 채우기 (targets.csv 는 gitignore)
python -m crawler.cli --csv targets.csv

# 인라인 타깃으로 (반복 가능)
python -m crawler.cli --url "ctrip=https://m.ctrip.com/webapp/you/community/detail?articleId=266207894"

# 옵션: --db data/crawler.db, --headed(창 띄우기), -v(디버그 로그)
```

## 설정 (환경변수)

`.env.example` 를 `.env` 로 복사해 채운다 (`.env` 는 gitignore). 로그인이 필요 없으면 전부 비워도 된다.

| 변수 | 설명 |
|------|------|
| `CRAWLER_COOKIES` | 로그인 페이지용 쿠키 문자열 `"k1=v1; k2=v2"` |
| `CRAWLER_PROXY` | 프록시 `http://user:pass@host:port` (중국 지역 IP 필요 시) |
| `CRAWLER_DB` | SQLite 경로 (기본 `data/crawler.db`) |
| `CRAWLER_DELAY_MIN` / `CRAWLER_DELAY_MAX` | 요청 간 지연 범위(초, 기본 3~8) |

## 씨트립 어댑터 — 정찰 결과

예시 URL: `https://m.ctrip.com/webapp/you/community/detail?articleId=<id>` (쿼리 `articleId` = 게시물 id).

정찰 스파이크에서 관찰한 사실 (헤드리스·비중국 IP·비로그인 환경 기준):

- **댓글 수는 XHR 로 안정적으로 잡힌다.** `restapi/soa2/20725/json/ruleSortCommentList` 응답의 최상위 `totalCount` 가 실제 댓글 수(예 6, "共6条评论"과 일치).
- **좋아요/저장은 이 환경에서 막힌다.** 기사 상세 본문이 안티봇에 걸려 에러 페이지(`哎呀，出错啦`)로 렌더돼 `div.zan-container` DOM 이 나타나지 않는다. 중국 IP·유효 쿠키·앱 세션에서는 정상 렌더되어 DOM 경로로 좋아요(赞)/저장(收藏)을 읽을 수 있다.
- `relatedRecommend` XHR 의 카운트들은 "추천 기사"의 값이라 타깃과 무관 → 사용하지 않는다.

따라서 어댑터는 **XHR 우선 + DOM(zan-container) 보조**를 병합한다. 둘 다 막히면 해당 지표를 `None` 으로 두고 `raw` 필드에 진단 정보(잡힌 XHR 목록·페이지 제목)를 남긴다. 실제 수집 시 이 환경에서는 `comments` 는 실값, `likes/collects/views` 는 `None` 으로 저장된다.

> 좋아요/저장까지 채우려면 중국 지역 프록시(`CRAWLER_PROXY`) 또는 로그인 쿠키(`CRAWLER_COOKIES`)를 넣고 재수집하면 된다. 코드 변경은 필요 없다.

## 테스트

```bash
python -m pytest        # 브라우저 없이 되는 로직 (스키마·저장·CLI·어댑터 파싱) 39건
```

브라우저가 필요한 실수집(navigate/DOM)은 스모크 테스트에서 제외한다.

## 다음 Phase 확장 지점

새 플랫폼은 `crawler/adapters/<platform>.py` 에 `Adapter` 를 구현하고 `crawler/cli.py` 의 `ADAPTER_REGISTRY` 에 한 줄 등록하면 된다 — Phase 2: 마펑워·디엔핑·샤오홍슈·더우인 어댑터 추가.

## 회사 이관 체크리스트

- [x] 의존성 버전 고정 (`requirements.txt`)
- [x] 하드코딩된 개인 경로 없음, 설정은 환경변수/인자화
- [x] 시크릿·실제 타깃·수집 데이터는 커밋 제외 (`.gitignore`)
- [x] 설치·실행 방법 문서화 (이 README)
