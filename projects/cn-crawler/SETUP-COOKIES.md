# 쿠키·프록시 설정 가이드

로그인월/지역차단 플랫폼(샤오홍슈·디엔핑 등)은 **중국 지역 프록시**와 **로그인 쿠키/세션**이 있어야 정상 수집된다. 이 크롤러는 차단을 우회하지 않고, 사용자가 확보한 프록시·쿠키를 주입받아 브라우저가 정상 렌더하게 한다. **코드 수정 없이 `.env` 설정만으로** 동작한다.

> ⚠️ 실제 쿠키·프록시·비밀번호·사내 URL 은 절대 커밋하지 않는다. `.env`·쿠키 파일·storage_state 는 모두 `.gitignore` 대상이다. 이 문서의 값은 전부 더미다.

전체 절차: **쿠키·프록시 확보 → `.env` 설정 → `--check` 로 검증 → 전량 수집**

---

## 1. 쿠키·세션 확보 (브라우저에서 추출)

로그인이 필요한 플랫폼은 **본인 계정으로 정상 로그인한 브라우저**에서 세션을 꺼내 온다. 두 가지 형식을 지원한다.

### 방법 A — 쿠키 헤더 문자열 (간단)

1. Chrome/Edge 에서 해당 플랫폼에 로그인한다 (예: `xiaohongshu.com`).
2. `F12` → **Application(애플리케이션)** 탭 → 좌측 **Storage → Cookies** → 해당 도메인 선택.
3. 쿠키들을 `이름=값; 이름2=값2` 형태의 한 줄 문자열로 만든다.
   - 빠른 방법: **Network** 탭에서 아무 문서 요청이나 클릭 → **Request Headers** 의 `cookie:` 값을 통째로 복사(로그인 상태의 요청이어야 한다).
4. 이 문자열을 `.env` 의 `CRAWLER_COOKIES_<PLATFORM>` 에 넣거나, `$CRAWLER_COOKIES_DIR/<platform>.txt` 파일로 저장한다.

### 방법 B — Playwright storage_state (권장, 세션 통째)

쿠키뿐 아니라 `localStorage` 까지 담아 로그인 세션을 더 온전히 재현한다. 서명 토큰을 `localStorage` 에 두는 플랫폼에 유리하다.

로그인한 브라우저 상태를 한 번 저장해 두는 스크립트:

```python
# save_session.py  (일회성, 커밋하지 않음)
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto("https://www.xiaohongshu.com")
    input("브라우저에서 로그인(필요시 QR/문자인증)을 마치고 Enter…")
    ctx.storage_state(path="cookies/xiaohongshu.json")  # ← storage_state 저장
    browser.close()
```

- 저장 경로를 `$CRAWLER_COOKIES_DIR/<platform>.json` 규칙에 맞춘다 (예: `cookies/xiaohongshu.json`).
- `cookies/` 디렉터리는 `.gitignore` 되어 있으니 그 안에 두면 안전하다.
- 캡차/문자인증이 걸리는 플랫폼(마펑워·디엔핑)은 이 단계에서 **사람이 직접 통과**한 뒤 세션을 저장한다. 크롤러는 캡차를 자동 돌파하지 않는다.

### 쿠키 소스 우선순위

한 플랫폼에 여러 소스가 있으면 아래 순서로 하나를 고른다(위가 우선):

1. `CRAWLER_COOKIES_<PLATFORM>` (헤더 문자열, env)
2. `$CRAWLER_COOKIES_DIR/<platform>.json` (storage_state)
3. `$CRAWLER_COOKIES_DIR/<platform>.txt` (헤더 문자열, 파일)
4. `CRAWLER_COOKIES` (전역 헤더 문자열, 하위호환)

플랫폼명은 소문자: `xiaohongshu` / `dianping` / `douyin` / `ctrip` / `mafengwo`. env 키는 대문자(`CRAWLER_COOKIES_XIAOHONGSHU`).

> 🔒 **쿠키/세션 파일은 반드시 `.gitignore` 된 위치에 둔다.** 기본 권장 위치는 저장소의 `cookies/` 디렉터리로, `.gitignore` 가 `cookies/` 와 실제 파일명(`<platform>.json`/`.txt`)을 위치 무관하게 무시한다. 로컬 개발이면 `CRAWLER_COOKIES_DIR=./cookies` 처럼 이 안을 가리켜 실제 로그인 세션이 공개 저장소로 새지 않게 한다.
>
> 📦 **컨테이너/서비스 배포 시** `CRAWLER_COOKIES_DIR` 은 CWD 의존을 피해 **절대경로 또는 마운트 볼륨**을 쓴다(예: `/run/secrets/cn-crawler-cookies`, `/data/cookies`). 상대경로는 실행 디렉터리에 따라 파일을 못 찾을 수 있다.

---

## 2. 중국 프록시 설정

지역차단 플랫폼은 중국 지역 출구 IP 프록시가 필요하다. `.env` 에 넣는다.

```dotenv
# 인증 없는 프록시
CRAWLER_PROXY=http://proxy.example.com:8080

# 인증을 URL 에 임베드
CRAWLER_PROXY=http://user:pass@proxy.example.com:8080

# 또는 인증을 분리 (URL 임베드보다 우선 적용)
CRAWLER_PROXY=http://proxy.example.com:8080
CRAWLER_PROXY_USERNAME=myuser
CRAWLER_PROXY_PASSWORD=mypass
```

`server` 는 `http://host:port` 로 정규화되고, 인증정보는 Playwright 의 `username`/`password` 로 분리 전달된다. URL 임베드 인증의 특수문자는 percent-encoding 해야 한다(`@`→`%40`, `:`→`%3A`). **비밀번호에 `@`·`:`·`/` 같은 특수문자가 있으면 URL 임베드 대신 `CRAWLER_PROXY_USERNAME`/`CRAWLER_PROXY_PASSWORD` 분리 지정을 권장한다** — 인코딩 실수로 인한 인증 실패를 피할 수 있다.

---

## 3. `.env` 설정

`.env.example` 을 복사해 채운다. 필요한 플랫폼만 채우면 된다.

```bash
cp .env.example .env
```

예시(더미값):

```dotenv
CRAWLER_PROXY=http://user:pass@proxy.example.com:8080
CRAWLER_COOKIES_XIAOHONGSHU=web_session=aaaa; a1=bbbb; webId=cccc
CRAWLER_COOKIES_DIR=./cookies          # cookies/dianping.json (storage_state) 등
```

---

## 4. `--check` 로 검증 (전량 수집 전에)

전량 수집 전에 쿠키/프록시가 실제로 월을 뚫는지 진단한다. 테스트 URL 은 각 플랫폼의 **공개 게시물 URL** 을 `--check-url` 로 준다(지표는 저장하지 않고 상태만 판정).

```bash
# 전체 플랫폼 진단 (URL 을 준 플랫폼만 실제 검사)
python -m crawler.cli --check all \
  --check-url douyin=https://www.douyin.com/video/<id> \
  --check-url xiaohongshu=https://www.xiaohongshu.com/explore/<id>?xsec_token=... \
  --check-url dianping=https://m.dianping.com/ugcdetail/<id>

# 한 플랫폼만
python -m crawler.cli --check xiaohongshu --check-url xiaohongshu=https://www.xiaohongshu.com/explore/<id>?xsec_token=...
```

리포트 상태:

| 상태 | 의미 | 조치 |
|------|------|------|
| `OK` | 지표 렌더 정상 (쿠키/프록시 유효) | 수집 진행 가능 |
| `LOGIN_WALL` | 로그인월 — 쿠키 없음/만료 | 쿠키/세션 재확보 |
| `CAPTCHA_WALL` | 캡차/verify 차단 | 사람이 세션 통과 후 storage_state 저장 |
| `APP_WALL` | 앱 전용 유도월 | 유효 쿠키 / 공유토큰 URL 사용 |
| `REDIRECTED` | 타깃 이탈(리다이렉트) | 쿠키 만료·지역차단 의심(프록시 점검) |
| `NO_DATA` | 차단 신호 없이 지표 미검출 | 프록시(지역)·쿠키·URL 확인 |
| `NO_URL` | 테스트 URL 미제공 | `--check-url` 로 URL 제공 |

모든 검사가 `OK` 면 종료코드 0, 하나라도 아니면 1 이다(스크립트 게이트로 활용 가능).

> 쿠키 없이 돌리면 도우인 같은 공개 수집 플랫폼은 `OK`, 샤오홍슈·디엔핑은 `LOGIN_WALL` 로 나오는 게 정상이다. 여기서 `LOGIN_WALL` 이 뜬 플랫폼에 쿠키를 넣고 다시 `--check` 해서 `OK` 로 바뀌는지 확인한 뒤 전량 수집으로 넘어간다.

---

## 5. 전량 수집

`--check` 가 목표 플랫폼에서 `OK` 를 주면 수집한다. 프록시·쿠키는 `.env` 에서 자동 적용되므로 명령은 동일하다.

```bash
python -m crawler.cli --csv targets.csv
# 또는
python -m crawler.cli --url "xiaohongshu=https://www.xiaohongshu.com/explore/<id>?xsec_token=..."
```
