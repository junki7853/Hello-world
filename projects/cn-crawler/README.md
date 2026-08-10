# cn-crawler — 중국 플랫폼 참여지표 크롤러

중국 여행/콘텐츠 플랫폼(씨트립·마펑워·디엔핑·샤오홍슈·더우인 등) 게시물의 **참여지표(조회·좋아요·저장·댓글·공유)를 시계열로 수집**하는 크롤러다.

핵심은 **안티봇 엔지니어링 데모**다. 플랫폼의 서명(sign) 알고리즘을 리버싱하지 않는다. 대신 **Playwright 헤드리스 브라우저로 실제 페이지를 렌더링**해서, 브라우저가 알아서 계산한 서명이 붙은 XHR 응답을 가로채거나 렌더된 DOM 텍스트를 읽는다. 플랫폼이 서명 로직을 바꿔도 우리 코드는 바꿀 필요가 없다는 것이 이 접근의 장점이다.

## 설계

```
crawler/
  core/
    browser.py    Playwright 세션 (모바일 UA·stealth 기본, 쿠키/프록시 주입)
    schema.py     정규화 스키마 Metrics(dataclass) + 중국어 카운트 파서(만/억)
    store.py      SQLite append-only 시계열 저장소 (+구버전 DB 자동 마이그레이션)
    export.py     CSV export (마케팅 트래킹 컬럼 순서, UTF-8-BOM)
    ratelimit.py  요청 간 정중한 무작위 지연
    log.py        로깅 설정
  adapters/
    base.py       어댑터 계약: collect(url) -> Metrics
    ctrip.py      씨트립 레퍼런스 어댑터 (첫 구현체)
    mafengwo.py   마펑워 어댑터 (캡차 감지 degrade)
    dianping.py   디엔핑 어댑터 (로그인월/verify 감지, PUA 난독화 플래그)
  cli.py          CSV/인라인 타깃 → 순차 수집 → 저장 / --export CSV 내보내기
```

### 정규화 스키마 (`Metrics`)

플랫폼이 무엇이든 게시물 1건은 아래 한 행으로 정규화된다. 플랫폼에 없는 지표는 `None`.

`platform, article_id, url, title?, author?, views?, likes?, collects?, comments?, shares?, impressions?, followers?, upload_date?, post_format?, collected_at(UTC ISO), raw`

Phase 1 에서 만든 DB 파일은 열 때 자동으로 신규 컬럼이 `ALTER` 되며 기존 시계열 행은 그대로 보존된다.

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

### CSV export (마케팅 트래킹 시트용)

```bash
# 게시물별 최신 스냅샷만 (기본)
python -m crawler.cli --export out.csv

# 전체 시계열 이력
python -m crawler.cli --export out.csv --all

# DB 경로 지정
python -m crawler.cli --export out.csv --db data/crawler.db
```

컬럼 순서: `platform, article_id, url, author, post_format, upload_date, followers, impressions, views, likes, collects, comments, shares, collected_at`. 엑셀에서 한글이 깨지지 않도록 **UTF-8-BOM** 으로 저장된다. 값이 없는 지표는 빈 칸.

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

> ⚠️ DOM 좋아요(赞)/저장(收藏) 셀렉터 매핑(`span.icon-title.icon-right` = 좋아요, 나머지 `span.icon-title` = 저장)은 디자인 피드백 실측에 기반하나, 이 개발 환경은 비중국 IP라 실제 렌더로 검증하지 못했다. **첫 중국 IP 수집 시 두 값이 뒤바뀌지 않았는지 좋아요-저장 교차 검증이 필요하다.**

## 마펑워 어댑터 — 정찰 결과

예시 URL: `https://www.mafengwo.cn/i/<id>.html` (게시물 id 는 경로에서, `iid` 쿼리도 지원).

정찰 스파이크에서 관찰한 사실 (2026-08, 헤드리스·비중국 IP·비로그인 기준):

- **홈/목록 페이지는 정상 렌더**되어 최신 게시물 id 가 노출된다.
- **게시물 상세는 텐센트 슬라이드 캡차로 차단.** `/i/<id>.html` 은 데스크톱(www)·모바일(m) 모두 `t.captcha.qq.com` 캡차가 뜨고 본문이 렌더되지 않는다. 캡차 자동 돌파는 시도하지 않는다.
- 과거 공개 pagelet JSON 엔드포인트(headOperateApi 등)는 404 로 제거됐다.
- 상세 페이지는 지속 폴링 때문에 `networkidle` 에 도달하지 않는다 → `domcontentloaded` + 고정 대기 사용.

어댑터 동작: 캡차(`t.captcha.qq.com`) 감지 시 지표를 `None` 으로 두고 `raw` 에 `captcha_detected` 진단을 남긴다. 정상 렌더 환경(중국 IP·유효 쿠키)에서는 **본문 텍스트 정규식**(浏览/顶/收藏/蜂评, 발표일 → `upload_date`) + **XHR JSON 카운트 키 탐색**(vote_num/reply_num 등)을 병합한다. 셀렉터 대신 텍스트 패턴을 써서 마크업 변경에 상대적으로 강하다.

## 디엔핑 어댑터 — 정찰 결과

예시 URL: `https://m.dianping.com/ugcdetail/<contentId>` (노트), `https://www.dianping.com/shop/<id>` (샵).

정찰 스파이크에서 관찰한 사실 (2026-08, 헤드리스·비중국 IP·비로그인 기준):

- **홈 피드는 정상 렌더**되고 피드 XHR(`growthlistfeeds`)에 노트 `contentId`·제목·카운트가 보인다. 홈 피드의 숫자는 평문이었다(PUA 난독화 아님).
- **노트 상세(`ugcdetail`)는 SMS 로그인월**(`maccount.dianping.com/mlogin/smslogin`)로, **목록/샵 페이지는 메이투안 verify**(`verify.meituan.com`, 아이콘 클릭 캡차)로 리다이렉트된다. 우회는 시도하지 않는다.
- 디엔핑은 로그인 후 페이지에서 숫자를 **커스텀 폰트(사설영역 PUA 글리프)로 난독화**한 이력이 유명하다. 이 환경에서는 렌더된 숫자에 도달할 수 없어 폰트맵/OCR 디코더는 검증 불가능한 코드가 되므로 **의도적으로 구현하지 않았다**. 대신 본문에서 PUA 글리프가 감지되면 `raw` 에 `font_obfuscation_detected` 를 기록하고 해당 숫자는 `None` 으로 남긴다(PUA 는 `\d` 에 안 걸려 오값이 저장되지 않는다).

어댑터 동작: 최종 URL 기반으로 `login_wall`/`verify_wall` 을 감지해 `None` + `raw` 진단으로 degrade. 정상 렌더 환경(중국 IP·유효 쿠키 `CRAWLER_COOKIES`)에서는 DOM 텍스트 정규식(点赞/收藏/浏览/评论) + XHR 카운트 키(likeCount/commentCount/followerCount 등, `followers` 매핑 포함)를 병합한다.

## 테스트

```bash
python -m pytest        # 브라우저 없이 되는 로직 (스키마·저장·export·CLI·어댑터 파싱) 88건
```

브라우저가 필요한 실수집(navigate/DOM)은 스모크 테스트에서 제외한다.

## 다음 Phase 확장 지점 (Phase 3)

- **샤오홍슈(小红书)·더우인(抖音) 어댑터**: `crawler/adapters/<platform>.py` 에 `Adapter` 를 구현하고 `crawler/cli.py` 의 `ADAPTER_REGISTRY` 에 한 줄 등록. 두 플랫폼 모두 로그인월·서명 파라미터가 강한 편이라 쿠키 주입(`CRAWLER_COOKIES`) 전제로 설계할 것.
- **디엔핑 폰트 난독화 디코드**: 로그인 쿠키·중국 IP 로 실제 렌더가 확보되면, `font_obfuscation_detected` 가 참인 페이지에서 `@font-face` 폰트를 받아 글리프→숫자 맵을 만들거나 숫자 영역 스크린샷+OCR 을 붙인다 (진입점: `adapters/dianping.py` 의 `extract_metrics_from_text`).
- **마펑워/씨트립 중국 IP 검증**: 프록시 확보 시 DOM 경로(정규식·셀렉터) 실렌더 검증.

## 회사 이관 체크리스트

- [x] 의존성 버전 고정 (`requirements.txt`)
- [x] 하드코딩된 개인 경로 없음, 설정은 환경변수/인자화
- [x] 시크릿·실제 타깃·수집 데이터는 커밋 제외 (`.gitignore`)
- [x] 설치·실행 방법 문서화 (이 README)
