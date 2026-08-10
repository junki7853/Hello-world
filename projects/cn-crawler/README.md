# cn-crawler — 중국 플랫폼 참여지표 크롤러

중국 여행/콘텐츠 플랫폼 게시물의 **참여지표(조회·좋아요·저장·댓글·공유)를 시계열로 수집**하는 크롤러다. **5개 플랫폼 전부 커버: 씨트립(携程)·마펑워(马蜂窝)·디엔핑(大众点评)·샤오홍슈(小红书)·도우인(抖音).**

핵심은 **안티봇 엔지니어링 데모**다. 플랫폼의 서명(sign) 알고리즘을 리버싱하지 않는다. 대신 **Playwright 헤드리스 브라우저로 실제 페이지를 렌더링**해서, 브라우저가 알아서 계산한 서명이 붙은 XHR 응답을 가로채거나 렌더된 DOM 텍스트를 읽는다. 플랫폼이 서명 로직을 바꿔도 우리 코드는 바꿀 필요가 없다는 것이 이 접근의 장점이다.

## 설계

```
crawler/
  core/
    browser.py    Playwright 세션 (모바일/데스크톱 컨텍스트·stealth, 쿠키/프록시 주입)
    schema.py     정규화 스키마 Metrics(dataclass) + 중국어 카운트 파서(만/억)
    store.py      SQLite append-only 시계열 저장소 (+구버전 DB 자동 마이그레이션)
    export.py     CSV export (마케팅 트래킹 컬럼 순서, UTF-8-BOM)
    ratelimit.py  요청 간 정중한 무작위 지연
    log.py        로깅 설정
  adapters/
    base.py       어댑터 계약 + 공통 헬퍼 (XHR 수집·카운트 BFS·id 매칭 서브트리)
    ctrip.py      씨트립 레퍼런스 어댑터 (첫 구현체)
    mafengwo.py   마펑워 어댑터 (캡차 감지 degrade)
    dianping.py   디엔핑 어댑터 (로그인월/verify 감지, PUA 난독화 플래그)
    xiaohongshu.py 샤오홍슈 어댑터 (보안월/캡차월/앱월 감지, XHR+SSR 병합)
    douyin.py     도우인 어댑터 (aweme_id 매칭 XHR + data-e2e DOM, verify 감지)
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

# 샤오홍슈 (노트 상세 — 공유링크의 xsec_token 쿼리는 붙은 그대로 넘길 것)
python -m crawler.cli --url "xiaohongshu=https://www.xiaohongshu.com/explore/68a1b2c3000000001f00a1b2?xsec_token=..."

# 도우인 (영상 상세 또는 v.douyin.com 공유 단축링크 — 단축링크는 자동 해석)
python -m crawler.cli --url "douyin=https://www.douyin.com/video/7661958907732004122"

# 옵션: --db data/crawler.db, --headed(창 띄우기), -v(디버그 로그)
```

배치 견고성: 어댑터가 모르는 URL 형식(진짜 미지원)은 경고 한 줄로 건너뛰고 나머지 타깃은 계속 수집한다. 지원 URL 인데 차단(로그인월/캡차/verify)에 막히면 행을 건너뛰지 않고 지표 `None` + `raw` 진단의 degrade 행을 남긴다.

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

예시 URL: `https://www.mafengwo.cn/i/<id>.html` (게시물 id 는 경로에서, `iid` 쿼리도 지원), `https://m.mafengwo.cn/mweng/wengdetailssr/weng?id=<id>` (모바일 웽 상세 — `id` 쿼리는 무관 URL 오수용을 막기 위해 weng 경로에서만 인정).

정찰 스파이크에서 관찰한 사실 (2026-08, 헤드리스·비중국 IP·비로그인 기준):

- **홈/목록 페이지는 정상 렌더**되어 최신 게시물 id 가 노출된다.
- **게시물 상세는 텐센트 슬라이드 캡차로 차단.** `/i/<id>.html` 은 데스크톱(www)·모바일(m) 모두 `t.captcha.qq.com` 캡차가 뜨고 본문이 렌더되지 않는다. 캡차 자동 돌파는 시도하지 않는다.
- 과거 공개 pagelet JSON 엔드포인트(headOperateApi 등)는 404 로 제거됐다.
- 상세 페이지는 지속 폴링 때문에 `networkidle` 에 도달하지 않는다 → `domcontentloaded` + 고정 대기 사용.

어댑터 동작: 캡차(`t.captcha.qq.com`) 감지 시 지표를 `None` 으로 두고 `raw` 에 `captcha_detected` 진단을 남긴다. 정상 렌더 환경(중국 IP·유효 쿠키)에서는 **본문 텍스트 정규식**(浏览/顶/收藏/蜂评, 발표일 → `upload_date`) + **XHR JSON 카운트 키 탐색**(vote_num/reply_num 등)을 병합한다. 셀렉터 대신 텍스트 패턴을 써서 마크업 변경에 상대적으로 강하다.

**모바일 웽 상세는 예외적으로 캡차 없이 렌더된다** (2026-08 실측, 비중국 IP). 다만 지표 숫자에 라벨이 없어(하단 액션바 아이콘 옆 맨숫자) 텍스트 패턴으로 못 잡고, 각 버튼의 `data-exp-display-params` JSON 의 `item_name`(点赞/评论/收藏)으로 숫자의 의미를 판정해 수집한다 — 공개 예시 실수집으로 likes/collects 실값 확인. 삭제된 게시물은 "笔记不存在" 본문으로 렌더되며 degrade 행이 남는다.

## 디엔핑 어댑터 — 정찰 결과

예시 URL: `https://m.dianping.com/ugcdetail/<contentId>` (노트), `https://m.dianping.com/feeddetail/<id>` (피드 상세 — 같은 숫자 id 체계, SMS 로그인월로 리다이렉트되어 degrade 행이 남는다), `https://www.dianping.com/shop/<id>` (샵).

정찰 스파이크에서 관찰한 사실 (2026-08, 헤드리스·비중국 IP·비로그인 기준):

- **홈 피드는 정상 렌더**되고 피드 XHR(`growthlistfeeds`)에 노트 `contentId`·제목·카운트가 보인다. 홈 피드의 숫자는 평문이었다(PUA 난독화 아님).
- **노트 상세(`ugcdetail`)는 SMS 로그인월**(`maccount.dianping.com/mlogin/smslogin`)로, **목록/샵 페이지는 메이투안 verify**(`verify.meituan.com`, 아이콘 클릭 캡차)로 리다이렉트된다. 우회는 시도하지 않는다.
- 디엔핑은 로그인 후 페이지에서 숫자를 **커스텀 폰트(사설영역 PUA 글리프)로 난독화**한 이력이 유명하다. 이 환경에서는 렌더된 숫자에 도달할 수 없어 폰트맵/OCR 디코더는 검증 불가능한 코드가 되므로 **의도적으로 구현하지 않았다**. 대신 본문에서 PUA 글리프가 감지되면 `raw` 에 `font_obfuscation_detected` 를 기록하고 해당 숫자는 `None` 으로 남긴다(PUA 는 `\d` 에 안 걸려 오값이 저장되지 않는다).

어댑터 동작: 최종 URL 기반으로 `login_wall`/`verify_wall` 을 감지해 `None` + `raw` 진단으로 degrade. 정상 렌더 환경(중국 IP·유효 쿠키 `CRAWLER_COOKIES`)에서는 DOM 텍스트 정규식(点赞/收藏/浏览/评论) + XHR 카운트 키(likeCount/commentCount/followerCount 등, `followers` 매핑 포함)를 병합한다.

## 샤오홍슈 어댑터 — 정찰 결과

예시 URL: `https://www.xiaohongshu.com/explore/<noteId>` (24자리 hex, `xsec_token` 쿼리 포함 공유링크 권장), `/discovery/item/<noteId>`, `xhslink.com` 단축링크.

정찰 스파이크에서 관찰한 사실 (2026-08, 헤드리스·비중국 IP·비로그인 기준):

- **차단이 가장 강한 플랫폼.** 데스크톱 explore 는 즉시 **QR 보안검증**(`website-login/captcha`)으로, 유효한 `xsec_token` 없는 노트 상세는 **보안 404**(`/404/sec_*`, `xhs_sec_server`) 또는 **전면 로그인 화면**(手机号登录)으로 리다이렉트된다. 모바일 뷰는 "APP 에서만 열람" 앱 유도월. x-s/x-t 서명 리버싱·캡차 돌파는 하지 않는다.
- 어댑터 동작: `security_wall`/`captcha_wall`/`login_wall`/`app_wall`/`redirected_away` 를 감지해 `None` + `raw` 진단으로 degrade. **이 환경의 실수집 결과는 `login_wall + redirected_away` 기록** — 실값 수집에는 중국 IP + 로그인 쿠키가 필요하다.
- 정상 렌더 환경용 경로: **XHR**(`/api/sns/web/v1/feed` 류의 `interact_info`: `liked_count`/`collected_count`/`comment_count`/`share_count`, "1.2万" 문자열 포함) + **SSR 초기상태**(`window.__INITIAL_STATE__`, camelCase 키) + **DOM 텍스트 정규식** 병합. 피드 응답에 추천 노트 카운트가 섞이므로 **타깃 노트 id 서브트리로 좁힌 뒤에만** 카운트를 찾는다.

## 도우인 어댑터 — 정찰 결과

예시 URL: `https://www.douyin.com/video/<aweme_id>` (15~20자리 숫자), `/note/<id>`(이미지 게시물), `?modal_id=<aweme_id>` 쿼리형, `v.douyin.com` 공유 단축링크(자동 해석 — 아래 참조).

**단축링크 해석** (2026-08 실측): `v.douyin.com/<code>` 는 302 체인 `v.douyin.com → www.iesdouyin.com/share/video/<aweme_id>/ → www.douyin.com/video/<aweme_id>` 로 풀린다. 어댑터는 최종 URL 뿐 아니라 **메인 프레임 내비게이션 체인(302 홉 포함) 전체**에서 `aweme_id` 를 재해석하므로 최종 랜딩이 홈/verify 로 튕겨도 중간 홉에서 id 를 복원한다. 랜딩이 공유월이라 detail XHR 이 안 뜨면 표준 상세(`/video/<id>`)로 재진입해 동일하게 수집한다(`raw` 에 `short_resolved`/`renavigated` 진단). 공개 예시 단축링크 실수집으로 실값 확인.

정찰 스파이크에서 관찰한 사실 (2026-08, 헤드리스·비중국 IP·비로그인, 데스크톱 뷰 기준):

- **5개 중 유일하게 이 환경에서 실값이 수집된다.** 홈·영상 상세가 정상 렌더되고, XHR `aweme/v1/web/aweme/detail/` 의 `statistics` 에서 좋아요(`digg_count`)·저장(`collect_count`)·댓글·공유, `author.follower_count`(팬)·`create_time`·`desc`(제목)를 얻는다. **재생수(`play_count`)는 웹 API 가 0 으로 숨긴다** → 0 은 지표로 저장하지 않는다(`views=None`).
- **추천 오염 방어가 필수.** 피드/시리즈 XHR 에 다른 영상들의 카운트가 섞이고, `/video/<id>` 가 `jingxuan?modal_id=<다른 영상>` 으로 리다이렉트되는 사례를 실측 → XHR 은 **타깃 `aweme_id` 매칭 서브트리**에서만, DOM 은 타깃 id 가 최종 URL 에 남아 있을 때만 쓴다(`redirected_away` 감지).
- DOM 은 `data-e2e` 속성(`video-player-digg`/`-collect`/`-share`, `feed-comment-icon`, `user-info` 의 粉丝, `detail-video-publish-time`)으로 읽는다. XHR 정밀값이 DOM 반올림값("2.7万")보다 우선.
- a_bogus/msToken 서명은 리버싱하지 않는다(브라우저가 계산). verify 슬라이더(拖动滑块)가 뜨면 감지 후 degrade. verify SDK 정적 JS 는 정상 페이지에도 로드되므로 마커로 쓰지 않는다.
- 검증 수집 실측값(공개 게시물): likes=26,890 / collects=6,498 / comments=794 / shares=7,482 / followers=1,765,004 / upload_date=2026-07-13.

## 운영 노트 — 정상 수집엔 중국 IP·쿠키가 필요하다

이 크롤러는 차단을 **우회하지 않고 감지 후 기록**한다(`raw` 의 `*_wall`/`captcha_detected` 진단). 월 플래그(`login_wall`/`verify_wall`/`redirected_away`)가 떠도 **타깃 id 정확매칭 XHR 로 채워진 지표값은 신뢰할 수 있다** — 엉뚱한 게시물의 값은 매칭에서 걸러져 `None` 으로 빠진다. 비중국 IP·비로그인 환경에서 기대할 수 있는 것:

| 플랫폼 | 이 환경의 결과 | 실값 수집 조건 |
|--------|----------------|----------------|
| 씨트립 | comments 실값, 나머지 차단 | 중국 IP 또는 쿠키 |
| 마펑워 | 캡차 차단 (`captcha_detected`) | 중국 IP + 쿠키 |
| 디엔핑 | 로그인월/verify 차단 | 중국 IP + 로그인 쿠키 |
| 샤오홍슈 | 로그인월/보안월 차단 | 중국 IP + 로그인 쿠키 + `xsec_token` 공유링크 |
| 도우인 | **전 지표 실값** (재생수 제외) | — (현 환경으로 충분) |

**프록시/쿠키 주입 지점** (코드 변경 불필요):

- `CRAWLER_PROXY` — 중국 지역 프록시 (`crawler/core/browser.py` 가 Playwright launch 에 전달)
- `CRAWLER_COOKIES` — 로그인 쿠키 문자열 (타깃 URL 의 등록도메인으로 자동 스코핑되어 XHR 에도 전송)

둘 다 `.env` 로 관리하며, 주입 후 같은 CLI 명령을 재실행하면 된다. 어댑터의 DOM/XHR 병합 경로는 정상 렌더 환경을 전제로 이미 구현돼 있다.

## 테스트

```bash
python -m pytest        # 브라우저 없이 되는 로직 (스키마·저장·export·CLI·어댑터 파싱) 130건
```

브라우저가 필요한 실수집(navigate/DOM)은 스모크 테스트에서 제외한다.

## 다음 Phase 확장 지점

- **중국 IP/쿠키 실렌더 검증**: 프록시·쿠키 확보 시 씨트립 좋아요-저장 교차 검증, 마펑워/디엔핑/샤오홍슈 DOM 경로 실렌더 검증 (위 운영 노트의 주입 지점 사용).
- **디엔핑 폰트 난독화 디코드**: 로그인 쿠키·중국 IP 로 실제 렌더가 확보되면, `font_obfuscation_detected` 가 참인 페이지에서 `@font-face` 폰트를 받아 글리프→숫자 맵을 만들거나 숫자 영역 스크린샷+OCR 을 붙인다 (진입점: `adapters/dianping.py` 의 `extract_metrics_from_text`).
- **도우인 재생수**: 웹 API 는 `play_count` 를 숨기므로, 크리에이터 계정 쿠키로 크리에이터 센터 API 를 읽는 별도 경로가 필요하다.

## 회사 이관 체크리스트

- [x] 의존성 버전 고정 (`requirements.txt`)
- [x] 하드코딩된 개인 경로 없음, 설정은 환경변수/인자화
- [x] 시크릿·실제 타깃·수집 데이터는 커밋 제외 (`.gitignore`)
- [x] 설치·실행 방법 문서화 (이 README)
