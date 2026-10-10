# 설치·계정 준비 안내서

이 문서는 개발 경험이 없어도 위에서부터 차례로 따라 하면 일요일 무인 실행까지 준비되도록 썼다.
각 단계 끝의 **확인** 항목이 통과하면 다음 단계로 넘어간다.

- 표기
  - **[미확인]**: 공식 문서로 아직 확인하지 못한 내용이다(조사 근거: `docs/research/`). 화면 메뉴 이름이나 수치가 다르면 실제 화면을 따른다.
  - `이렇게 쓴 것`: 터미널(명령 프롬프트)에 그대로 입력하는 명령이다.
- 비밀값(토큰·키)은 이 문서·채팅·이슈에 붙여 넣지 않는다. 넣는 곳은 두 군데뿐이다.
  - 내 컴퓨터의 `.env` 파일(저장소에 올라가지 않는다)
  - GitHub 저장소의 **Secrets**
- 언제든 `python -m studio doctor` 로 무엇이 빠졌는지 확인할 수 있다. 값은 출력되지 않고 "있음/없음/형식 오류"만 나온다.

---

<a id="overview"></a>
## 0. 전체 순서·예상 소요·비용

### 0-1. 순서와 시간

심사(감사)가 오래 걸리는 Google·TikTok 을 **가장 먼저** 신청해 두고, 기다리는 동안 나머지를 한다.

| 순서 | 할 일 | 직접 하는 시간 | 기다리는 시간 |
|---|---|---|---|
| 1 | Google: YouTube·Analytics·TTS API, OAuth, 감사 신청 | 1~2시간 | 감사 수 주~수 개월 [미확인 — 공식 SLA 없음] |
| 2 | Meta: 인스타 프로페셔널 계정, 페이스북 페이지, 개발자 앱, 토큰 | 1~2시간 | 없음(개발 모드·본인 계정) [미확인] |
| 3 | TikTok: 개발자 앱, Content Posting API, 감사 신청 | 1시간 | 감사 수일~수 주 [미확인] |
| 4 | 티스토리: 로그인 세션 저장 | 15분 | 없음 |
| 5 | Supabase: 프로젝트·DB·함수 배포 | 30~60분 | 없음 |
| 6 | RevenueCat: 프로젝트·웹훅 | 30분 | 앱 스토어 등록은 별도 |
| 7 | GitHub: Secrets 등록·Actions 켜기 | 30분 | 없음 |
| 8 | 첫 주 리허설 | 1~2시간 | 실행 1회 1~3시간 |

### 0-2. 비용(2026-10 기준, 운영량: 주 쇼츠 14·롱폼 1·카드뉴스 3·블로그 3)

| 항목 | 무료 범위 | 이 운영량에서 | 근거 |
|---|---|---|---|
| Google Cloud TTS (Chirp 3 HD) | 월 100만 자 무료, 이후 100만 자당 $30 | 월 약 6~7만 자 → 무료 | 공식 가격표(facts-google.md 8장). 결제 수단 등록은 필요 [미확인] |
| YouTube Data·Analytics API | 무료(일일 할당량) | 주 업로드 15편은 여유 | facts-google.md 2-3 |
| Supabase Free | 함수 호출 월 50만, 저장 1GB, 전송 5GB+5GB, 파일당 50MB | 쇼츠 영상 보관이 1GB 를 넘으면 오래된 파일 정리 필요 | facts-app-stack.md 5-3 |
| Supabase 주의 | 7일간 활동이 적으면 프로젝트 자동 정지 | 매시 발행 작업이 DB 를 쓰므로 보통 괜찮다. 경고 메일이 오면 대시보드에 한 번 접속 | 공식 문서 |
| GitHub Actions | 공개 저장소 무료. 비공개 저장소는 월 2,000분 무료 [미확인] | 비공개면 월 1,000~2,000분 예상(주간 실행 최대 4시간 포함) → 한도에 가까움 | — |
| GitHub 아티팩트 저장 | 비공개 저장소 Free 500MB [미확인] | 렌더 영상(롱폼 수백 MB)을 7일 보관 → 넘칠 수 있음 | — |
| Claude (Actions 안에서 실행) | API 키는 사용량 과금 / 구독 토큰은 구독 사용량 차감 | 주간 실행이 가장 큼. 워크플로의 `--max-turns` 가 상한 | facts-app-stack.md 6-1 |
| RevenueCat | 일정 매출까지 무료 [미확인] | 출시 초기는 무료 범위 | — |
| 스토어 등록 | Google Play 1회 $25, Apple 연 $99 [미확인] | 앱 출시 때 | — |

<a id="local"></a>
### 0-3. 내 컴퓨터 준비(한 번)

1. 설치: [Python 3.12 이상](https://www.python.org/downloads/), [Git](https://git-scm.com/downloads), [Node.js LTS](https://nodejs.org/)(Supabase CLI 용), 크롬 브라우저.
   영상 렌더를 내 컴퓨터에서도 해 보려면 ffmpeg 도 설치한다(없어도 GitHub Actions 에서는 자동 설치된다).
2. 저장소를 받고 파이썬 환경을 만든다.
   ```bash
   git clone <내 저장소 주소>
   cd <저장소 폴더>
   python -m venv .venv
   # Windows: .venv\Scripts\activate    /   Mac·Linux: source .venv/bin/activate
   pip install -e ".[dev]"
   python -m studio setup fonts
   cp .env.example .env          # Windows: copy .env.example .env
   ```
3. `.env` 를 메모장으로 열어 두고, 아래 단계에서 받는 값을 하나씩 채운다. `.env` 와 `.secrets/` 는 `.gitignore` 에 있어 올라가지 않는다.
4. **확인**: `python -m studio doctor` 가 실행되면 된다(지금은 비밀값이 비어 있어 `[X]` 가 많다).

---

<a id="google-cloud"></a>
## 1. Google (YouTube 업로드·댓글·분석, 내레이션 음성)

### 1-1. 프로젝트 만들고 API 켜기

1. <https://console.cloud.google.com/> 에 채널 소유 Google 계정으로 로그인한다.
2. 위쪽 프로젝트 선택 → **새 프로젝트** → 이름(예: `studio`) → 만들기.
3. **API 및 서비스 → 라이브러리** 에서 아래 셋을 검색해 각각 **사용** 을 누른다.
   - YouTube Data API v3
   - YouTube Analytics API
   - Cloud Text-to-Speech API
4. TTS 는 결제 계정 연결이 필요하다 [미확인]. **결제** 메뉴에서 결제 계정을 연결한다(무료 범위 안이면 청구되지 않는다).

<a id="youtube-oauth"></a>
### 1-2. OAuth 동의 화면(반드시 "프로덕션")과 데스크톱 클라이언트

> **Testing(테스트) 상태로 두면 refresh token 이 7일 뒤 만료**되어 일요일 무인 실행이 멈춘다. 반드시 Production 으로 바꾼 뒤 토큰을 발급한다(facts-google.md 4장).

1. **Google 인증 플랫폼(OAuth 동의 화면)** 으로 간다 [미확인: 메뉴 이름이 "OAuth 동의 화면"일 수 있음].
   - 사용자 유형: **외부(External)**. 앱 이름·지원 이메일·개발자 연락처를 넣는다.
2. **대상(Audience)** 에서 **앱 게시(Publish app)** 를 눌러 상태를 **In production(프로덕션)** 으로 바꾼다.
   - 본인 계정만 쓰면 Google 검증 없이도 동의할 때 "확인되지 않은 앱" 경고만 뜬다. **고급 → (앱 이름)(으)로 이동** 을 누르면 된다.
   - Testing 일 때 받은 토큰은 전환 뒤 **다시 발급**한다.
3. **클라이언트(사용자 인증 정보) → 클라이언트 만들기 → OAuth 클라이언트 ID → 애플리케이션 유형: 데스크톱 앱**.
4. 만들어진 **클라이언트 ID** 와 **클라이언트 보안 비밀번호** 를 `.env` 의 `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` 에 넣는다.
5. 토큰 발급(브라우저가 열린다):
   ```bash
   python -m studio auth youtube
   ```
   - 채널 소유 계정으로 로그인하고 **모든 권한 체크박스를 켠 채** 허용한다.
   - 결과는 `.secrets/youtube_token.json` 에 저장된다. GitHub 에 넣을 refresh token 을 화면에 그대로 보려면 `--show` 를 붙인다(화면 공유 중이면 쓰지 않는다).
   - 채널이 수익 창출(YPP)에 들어간 뒤 수익 지표까지 보려면 `--monetary` 를 붙여 다시 발급한다.
6. `.env` 의 `YOUTUBE_REFRESH_TOKEN` 에 넣는다. `YOUTUBE_CHANNEL_ID`(UC 로 시작)는 선택이다(없으면 토큰으로 조회).
7. **확인**: `python -m studio doctor` 의 YouTube 줄이 모두 `[O]`.

<a id="youtube-audit"></a>
### 1-3. YouTube API 감사 신청과 "감사 전 패키지 모드"

2020-07-28 이후 만든 Cloud 프로젝트는 **감사를 통과하기 전까지 API 로 올린 영상이 비공개로 잠긴다**(facts-google.md 3장).

1. "YouTube API Services – Audit and Quota Extension Form" 을 제출한다 [미확인: 양식 주소 `https://support.google.com/youtube/contact/yt_api_form`].
   - 준비물 [미확인]: 개인정보처리방침 주소, 이 도구의 사용 목적(본인 채널에 본인 콘텐츠를 예약 업로드·댓글 응대·분석), 사용 화면 설명.
   - 처리 기간은 공개돼 있지 않다(수 주~수 개월이라는 보고, 승인 보장 없음).
2. **감사 전(`channel.yaml` → `platforms.youtube.audited: false`, 기본값)** 에는 이렇게 돈다.
   - 일요일 실행이 영상·썸네일·자막·제목/설명(metadata.json)·업로드 안내를 `out/weeks/<주차>/youtube-package/<콘텐츠ID>/` 에 만든다.
   - GitHub → **Actions → weekly-publish → 해당 실행 → Artifacts → `out-<주차>-…`** 를 내려받는다(7일 보관).
   - YouTube Studio 에서 `업로드안내.md` 대로 **예약 업로드**하고, 영상 주소를 연결한다.
     ```bash
     python -m studio publish mark --week 2026-W42 --asset 2026W42-L1 --platform youtube --remote-id https://youtu.be/XXXXXXXXXXX --status scheduled
     ```
     (연결해야 댓글 응대·분석이 그 영상을 안다. 연결 결과는 `content/weeks/<주차>/queue.json` 에 기록되니 커밋·푸시한다.)
3. 감사를 통과하면 `platforms.youtube.audited: true` 로 바꾼다. 그 뒤로는 자동 예약 업로드된다.
4. 고정 댓글은 API 가 없다. 고정할 문구(원고의 `pinned_comment`)는 Studio 에서 직접 달고 고정한다.

<a id="tts"></a>
### 1-4. Cloud TTS 서비스 계정(내레이션 음성)

1. **IAM 및 관리자 → 서비스 계정 → 서비스 계정 만들기** → 이름(예: `tts`) → 완료. 역할은 주지 않아도 된다 [미확인].
2. 만든 계정 → **키 → 키 추가 → 새 키 만들기 → JSON** → 파일이 내려받아진다.
   - 조직 정책으로 키 생성이 막혀 있으면 [미확인] 개인 계정(조직 없음) 프로젝트에서 만든다.
3. 그 파일을 저장소 밖 안전한 곳(예: `~/keys/tts.json`)에 두고 `.env` 에 경로를 넣는다.
   ```
   GOOGLE_APPLICATION_CREDENTIALS=/Users/나/keys/tts.json
   ```
4. GitHub 에는 경로가 아니라 **파일 내용 전체**를 `GOOGLE_TTS_CREDENTIALS_JSON` 으로 넣는다(7장). 워크플로가 실행 중에 파일로 바꿔 쓴다.
5. 음성 이름은 `channel.yaml` 의 `tts.voice`(기본 `ko-KR-Chirp3-HD-Aoede`)다. 실제 사용 가능한 이름은 API 로 확인해야 한다 [미확인].
6. **확인**: `python -m studio doctor` 의 Google Cloud TTS 줄이 `[O]`.

---

<a id="meta"></a>
## 2. Meta (인스타그램 릴스·카드뉴스, 페이스북 페이지 릴스)

모든 Meta 항목은 공식 문서를 열지 못해 [미확인]이 많다(facts-meta-tiktok-tistory.md 1~2장). 화면이 다르면 화면을 따른다.

### 2-1. 계정 준비

1. 인스타그램 앱 → 설정 → 계정 유형 → **프로페셔널 계정(비즈니스 또는 크리에이터)** 으로 전환한다. 게시 API 는 프로페셔널 계정만 쓴다.
2. 페이스북 **페이지**를 만들고(이미 있으면 그것), 인스타 계정을 페이지에 연결한다.
3. 릴스·피드 캡션의 링크는 눌리지 않는다 [미확인]. 인스타 **프로필 링크**에 앱 추적 링크를 넣는다(5장 뒤에 주소가 생긴다).
   예: `https://<project-ref>.supabase.co/functions/v1/go?s=instagram&m=profile`

<a id="meta-app"></a>
### 2-2. Meta 개발자 앱과 테스터 등록

1. <https://developers.facebook.com/> → **내 앱 → 앱 만들기**. 사용 사례에서 Instagram API(Instagram 로그인)와 페이지 관리를 고른다 [미확인: 메뉴 구성].
2. 앱은 **개발 모드**로 둔다. 개발 모드에서는 앱 역할(관리자·개발자·테스터)이 있는 본인 계정의 데이터에만 접근할 수 있고, 1인 운영에는 이것으로 충분하다 [미확인].
3. **앱 역할 → Instagram 테스터** 에 본인 인스타 계정을 추가하고, 인스타 앱(또는 웹) **설정 → 앱 및 웹사이트 → 테스터 초대** 에서 수락한다 [미확인].
4. 앱 설정에서 **앱 ID**·**앱 시크릿**(페이스북용)과 **Instagram 앱 시크릿**(인스타 로그인용, 다른 값)을 확인해 `.env` 의 `META_APP_ID`, `META_APP_SECRET`, `INSTAGRAM_APP_SECRET` 에 넣는다. 이 셋은 토큰 교환에만 쓰고 GitHub 에는 넣지 않는다.

<a id="instagram"></a>
### 2-3. 인스타그램 토큰

1. 개발자 앱의 Instagram API 설정 화면에서 테스터 계정의 **액세스 토큰 생성** 을 누르고 토큰을 복사한다 [미확인].
2. 장기(60일) 토큰으로 바꾸고 계정 ID 를 확인한다(토큰은 붙여 넣기로 입력받아 셸 기록에 남지 않는다).
   ```bash
   python -m studio auth instagram
   ```
3. 출력된 `INSTAGRAM_USER_ID`, `INSTAGRAM_TOKEN` 을 `.env` 와 GitHub Secrets 에 넣는다(토큰을 그대로 보려면 `--show`).
4. 그 뒤 토큰은 발행 작업이 50일쯤마다 스스로 갱신해 **토큰 보관소**(CI 에서는 Supabase `oauth_tokens` 표의 `instagram`)에 저장한다. 그래서 GitHub Actions 는 `STUDIO_TOKEN_BACKEND=supabase` 로 돈다(워크플로에 이미 들어 있다).
   - 갱신에 실패해 만료되면(Graph 오류 code 190) 1~3 을 다시 한다. Secret 을 새 값으로 바꾸면 새 값이 우선한다.
5. 페이스북 로그인 경로(페이지 토큰으로 인스타에 게시)를 쓰려면 `channel.yaml` 의 `platforms.instagram` 에 `login: facebook` 을 넣는다. 이때는 2-4 의 페이지 토큰을 쓴다.

<a id="facebook"></a>
### 2-4. 페이스북 페이지 토큰

1. <https://developers.facebook.com/tools/explorer/> (Graph API 탐색기) → 내 앱 선택 → **사용자 액세스 토큰** → 권한
   `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`(비즈니스 소유 페이지면 `business_management` 도) → 토큰 생성·복사 [미확인].
2. 장기 사용자 토큰 → 만료 없는 페이지 토큰으로 바꾼다.
   ```bash
   python -m studio auth facebook          # META_APP_ID/SECRET 는 .env 에서 읽는다
   ```
3. 출력된 `FACEBOOK_PAGE_ID`, `FACEBOOK_PAGE_TOKEN` 을 `.env` 와 GitHub Secrets 에 넣는다.
4. **확인**: `python -m studio doctor` 의 Instagram·Facebook 줄이 `[O]`.
   - 한도 [미확인]: 인스타 API 게시 24시간 100건, 페이지 릴스 24시간 30건 — 이 운영량은 여유가 있다.

---

<a id="tiktok"></a>
## 3. TikTok (Content Posting API)

> **가장 큰 위험**: 감사(audit)를 받기 전 앱이 올린 게시물은 **모두 "나만 보기(SELF_ONLY)"로 강제**된다 [미확인]. 감사 가이드라인이 "본인이 관리하는 계정용 내부 도구"를 거절한다는 기억이 있으니, **신청 전에 원문을 꼭 읽는다** [미확인] (facts-meta-tiktok-tistory.md 3.5).

1. <https://developers.tiktok.com/> → 로그인 → **Manage apps → Create app**.
2. 제품 추가: **Login Kit**, **Content Posting API**(Direct Post 켜기). 범위: `user.info.basic`, `video.publish`, `video.upload`, `video.list` [미확인].
3. **Redirect URI** 를 등록한다(Login Kit 설정). 개인정보처리방침·이용약관이 있는 웹사이트 주소도 요구된다 [미확인].
4. **Client key**, **Client secret** 을 `.env` 의 `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET` 에 넣는다.
5. 토큰 발급(2단계):
   ```bash
   python -m studio auth tiktok --redirect-uri <등록한 리디렉트 URI>      # 인가 주소가 출력된다 → 브라우저로 열어 허용
   python -m studio auth tiktok --code "<허용 뒤 이동한 주소 전체>"          # refresh token 저장·출력(--show 로 그대로 보기)
   ```
   - 데스크톱 앱 유형이면 첫 명령에 `--pkce` 를 붙인다.
6. `TIKTOK_REFRESH_TOKEN` 을 `.env` 와 GitHub Secrets 에 넣는다. 이후 회전되는 토큰은 토큰 보관소 `tiktok` 에 저장된다.
7. 감사 전 운영 방법(`channel.yaml` → `platforms.tiktok`):
   - `audited: false`(기본): 게시는 되지만 나만 보기로 올라간다 [미확인].
   - `mode: inbox`: 영상을 틱톡 앱 **받은편지함**으로 보내고, 사람이 앱에서 확인·게시한다(감사 필요 여부 [미확인]).
8. 감사를 신청하고(데모 영상·가이드라인 준수 증빙 [미확인]), 통과하면 `audited: true`.
9. 프로필 링크는 비즈니스 계정이거나 팔로워 1,000명 이상이어야 달 수 있다 [미확인].

---

<a id="tistory"></a>
## 4. 티스토리 (로그인 세션 저장)

> **약관 위험 — 먼저 읽는다.** 글쓰기를 포함한 티스토리 Open API 는 종료됐다 [미확인: 2024-02]. 그래서 브라우저 자동화(Playwright)로 본인 세션을 써서 글을 올린다.
> 카카오 통합서비스약관·티스토리 운영정책은 자동화 수단을 이용한 비정상 이용과 스팸성 대량 게시를 제한한다 [미확인].
> 제재가 같은 카카오계정의 다른 서비스로 번질 수 있다 [추측].
> - 본인 블로그에 본인 글만, 적은 양(주 3편, 실행당 최대 2편)으로 올린다.
> - 더 안전하게 하려면 `channel.yaml` → `platforms.tistory.mode: draft` — **임시저장까지만 자동**으로 하고 발행 버튼은 사람이 누른다.
>   발행 뒤 `python -m studio publish mark --week <주차> --asset <ID> --platform tistory --remote-id <글 주소>` 로 연결한다.
> - 이 위험을 받아들일 수 없으면 블로그 채널을 공식 API 가 있는 Blogger 로 바꾸는 것을 검토한다.

1. `channel.yaml` 의 `platforms.tistory.blog_name`(https://**이름**.tistory.com 의 이름), `category`(카테고리 이름 그대로)를 채운다.
2. 내 컴퓨터(화면 있는 곳)에서:
   ```bash
   python -m playwright install chromium
   python -m studio auth tistory
   ```
   열린 브라우저에서 **카카오 로그인(2단계 인증 포함)** 을 마치면 창이 닫히고 두 파일이 생긴다.
   - `.secrets/tistory_state.json` — 내 컴퓨터용
   - `.secrets/tistory_state.b64` — 이 파일 **내용 전체**를 GitHub Secret `TISTORY_STORAGE_STATE_B64` 로 넣는다
3. **확인**: `python -m studio auth tistory --check` 가 글쓰기 화면이 열린다고 알려 주면 된다.
4. 세션이 만료되면(발행 실패 메시지에 "로그인 페이지") 2 를 다시 하고 Secret 을 바꾼다. 만료 주기는 [미확인].

---

<a id="supabase"></a>
## 5. Supabase (추적 링크·결제 기록·토큰 보관·미디어 저장)

### 5-1. 프로젝트 만들기와 키

1. <https://supabase.com/dashboard> → **New project** → 지역은 서울(Northeast Asia) 권장 → DB 비밀번호를 정해 안전한 곳에 적어 둔다.
2. **Project Settings → API Keys** 에서 값을 확인한다.
   - 프로젝트 주소 `https://<project-ref>.supabase.co` → `SUPABASE_URL`
   - 서버 비밀 키 → `SUPABASE_SERVICE_ROLE_KEY`: **Legacy API Keys 탭의 `service_role`** 키를 권장한다. 파이썬 도구가 이 키를 `apikey` 와 `Authorization: Bearer` 헤더에 함께 보내는데, 새 `sb_secret_…` 키를 Bearer 로 보낼 때의 동작은 [미확인]이다(401 이 나면 service_role 로 바꾼다).
   - 공개 키 `anon` 또는 `sb_publishable_…` → `SUPABASE_ANON_KEY`(앱 빌드용. 앱에는 이 키만 넣는다)
   - **서버 비밀 키는 앱·공개 저장소·채팅에 절대 넣지 않는다.** `python -m studio doctor` 가 두 키를 바꿔 넣었는지 검사한다.

### 5-2. DB(테이블·권한·버킷) 만들기

`supabase/migrations/20261010000000_init.sql` 이 표 5개(`link_clicks`, `app_installs`, `app_events`, `purchases`, `oauth_tokens`), 행 수준 보안, 공개 버킷 `media` 를 만든다.
- 앱(anon 키)은 `app_installs`·`app_events` 에 **쓰기만** 할 수 있고 읽을 수 없다. 나머지 표는 서버 키만 접근한다.

방법 A(권장, 5-3 과 한 번에): 다음 절의 배포 스크립트.
방법 B(명령줄 없이): 대시보드 **SQL Editor → New query** 에 위 파일 내용을 그대로 붙여 넣고 **Run**. 다시 실행해도 안전하다.

<a id="supabase-functions"></a>
### 5-3. 함수 비밀값과 배포

1. `supabase/functions/.env.example` 을 복사해 `supabase/functions/.env` 를 만들고(커밋 금지) 채운다.
   - `REVENUECAT_WEBHOOK_AUTH`, `IP_HASH_SALT`: 긴 무작위 문자열. 만드는 법:
     ```bash
     python -c "import secrets; print(secrets.token_urlsafe(32))"
     ```
     `IP_HASH_SALT` 는 한 번 정하면 바꾸지 않는다(바꾸면 같은 사람의 클릭이 다른 해시가 된다).
   - `ANDROID_PACKAGE`: 앱 패키지명(`channel.yaml` 의 `app.android_package`, 비어 있으면 `app.android_package_default`).
   - `IOS_APP_ID`, `APPLE_PROVIDER_TOKEN`: iOS 출시 뒤. pt 값은 App Store Connect → 앱 분석 → 캠페인에서 링크를 처음 만들 때 생긴다(앱에 데이터가 쌓인 뒤에만 만들 수 있다 — Apple 문서 2026-10-10 확인).
   - `LANDING_URL`: PC 에서 누른 사람을 보낼 소개 페이지(https). 없으면 Play 스토어 웹 페이지로 보낸다.
2. 배포(내 컴퓨터, Node.js 필요):
   ```bash
   npx supabase login                       # 브라우저에서 한 번 로그인
   bash scripts/supabase_deploy.sh <project-ref>
   ```
   스크립트가 프로젝트 연결 → `db push`(5-2) → `secrets set` → 함수 2개 배포(`go`, `revenuecat-webhook`, JWT 검사 끔)를 차례로 한다.
   Windows 는 Git Bash 에서 실행한다.
3. `channel.yaml` 의 `app.link_base` 에 `https://<project-ref>.supabase.co/functions/v1/go` 를 넣는다. 영상 설명란의 앱 링크가 이 주소로 만들어진다.

### 5-4. 확인

```bash
curl -sI -A "Mozilla/5.0 (Linux; Android 14)" "https://<project-ref>.supabase.co/functions/v1/go?s=test&m=setup&c=2026-W42&a=TEST-1"
```
- `HTTP/2 302` 와 `location: https://play.google.com/store/apps/details?id=…&referrer=utm_source%3Dtest…` 가 보이면 된다.
- 대시보드 **Table Editor → link_clicks** 에 한 줄이 생긴다(IP 는 해시만 저장). User-Agent 가 봇·curl 기본값이면 기록하지 않는다.
- `503` 이면 `ANDROID_PACKAGE`·`IOS_APP_ID`·`LANDING_URL` 이 모두 비어 있는 것이다.
- **Storage** 에 공개 버킷 `media` 가 있다. 여기 올린 파일은 누구나 볼 수 있으니 비공개 파일을 올리지 않는다.

---

<a id="revenuecat"></a>
## 6. RevenueCat (결제 → purchases 표)

1. <https://app.revenuecat.com/> → 프로젝트 만들기 → 앱 추가(Play Store: 패키지명·서비스 계정 키 / App Store: 번들 ID·App Store Connect 키) [미확인: 메뉴 구성].
2. 앱의 공개 SDK 키는 앱 쪽 설정(`app/.env` 의 `EXPO_PUBLIC_RC_*`)에 넣는다. 서버 비밀 키와 다르다.
3. **Integrations → Webhooks → 새 웹훅** [미확인: 메뉴 이름]
   - URL: `https://<project-ref>.supabase.co/functions/v1/revenuecat-webhook`
   - **Authorization header value**: 5-3 에서 만든 `REVENUECAT_WEBHOOK_AUTH` 와 **똑같은 값**
   - 환경: 운영과 샌드박스 모두 보낸다(분석은 기본적으로 샌드박스를 매출에서 뺀다)
4. **Send test event** → Supabase **Table Editor → purchases** 에 `event_type = TEST` 한 줄이 생기면 끝.
   - 401: Authorization 값이 다르다. 500 `not_configured`: 함수 비밀값을 안 넣었다(`supabase secrets list` 로 확인).
   - 같은 이벤트를 다시 받아도 `event_id` 로 한 번만 저장된다.
5. `REVENUECAT_SECRET_KEY`(REST API 비밀 키)는 선택이다. 지금 분석은 웹훅 기록으로 계산한다.

---

<a id="github-secrets"></a>
## 7. GitHub Secrets 등록과 Actions 켜기

### 7-1. 저장소

- 저장소를 GitHub 에 올린다. **공개 저장소면 Actions 가 무료**이고, 비공개면 월 무료 시간 안에서 돈다(0-2). 공개해도 비밀값은 Secrets 에만 있어 보이지 않는다. 다만 원고·보고서(content/·reports/)는 공개된다.
- 기본 브랜치(예: `main`)에 **보호 규칙(PR 필수)** 이 있으면 봇의 결과 커밋이 거절된다. 규칙을 두려면 GitHub Actions 를 예외로 허용한다.

### 7-2. Secrets 목록

**Settings → Secrets and variables → Actions → New repository secret** 에 아래 이름 그대로 넣는다.
켜 둔 플랫폼 기준 목록은 이 명령이 보여 준다(값은 출력하지 않는다).
```bash
python -m studio.secrets_spec --github
```

| 이름 | 필수 | 어디서 받나 |
|---|---|---|
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | 필수 | 5-1 |
| `SUPABASE_ANON_KEY` | 선택 | 5-1 (앱 빌드용) |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN` | 필수 | 1-2 |
| `YOUTUBE_CHANNEL_ID` | 선택 | 1-2 |
| `GOOGLE_TTS_CREDENTIALS_JSON` | 필수 | 1-4 (JSON 파일 **내용 전체**를 붙여 넣는다) |
| `INSTAGRAM_TOKEN` (`INSTAGRAM_USER_ID` 선택) | 필수 | 2-3 |
| `FACEBOOK_PAGE_ID`, `FACEBOOK_PAGE_TOKEN` | 필수 | 2-4 |
| `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REFRESH_TOKEN` | 필수 | 3 |
| `TISTORY_STORAGE_STATE_B64` | 필수 | 4 (`.secrets/tistory_state.b64` 내용) |
| `LAW_API_OC` | 필수(시험 대비 주제) | 9 |
| `ANTHROPIC_API_KEY` **또는** `CLAUDE_CODE_OAUTH_TOKEN` | 둘 중 하나 | 7-3 |
| `REVENUECAT_SECRET_KEY`, `EXPO_TOKEN` | 선택 | 6, 7-4 |

- 끈 플랫폼(`channel.yaml` → `platforms.<이름>.enabled: false`)의 값은 넣지 않아도 된다.
- `STUDIO_TOKEN_BACKEND=supabase` 는 워크플로에 이미 들어 있다. GitHub Actions 는 실행마다 새 컴퓨터라서, 회전하는 토큰(인스타·틱톡)을 Supabase `oauth_tokens` 에 저장해야 다음 실행이 이어 쓴다.
- Supabase 함수 비밀값(`REVENUECAT_WEBHOOK_AUTH`, `IP_HASH_SALT` 등)은 GitHub 이 아니라 5-3 에서 Supabase 에 넣는다.

<a id="claude"></a>
### 7-3. Claude 인증(둘 중 하나만)

- **API 키**: <https://console.anthropic.com/> → API Keys → 키 만들기 → `ANTHROPIC_API_KEY`. 사용량만큼 과금된다.
- **구독 토큰**(Pro·Max 등): 내 컴퓨터에서 `claude setup-token` → 출력된 값을 `CLAUDE_CODE_OAUTH_TOKEN` 에. 구독 사용량에서 차감되고 만든 사람의 구독에 묶인다.
- 둘 다 넣지 않는다(어느 쪽이 쓰일지 헷갈린다).
- 모델을 고정하려면 **Settings → Secrets and variables → Actions → Variables** 에 `CLAUDE_MODEL`(예: `claude-opus-5-5`)을 만든다. 없으면 Claude Code 기본 모델을 쓴다.
- 워크플로는 Claude 가 쓸 수 있는 도구를 `python -m studio …` 명령과 파일 읽기·쓰기로 제한하고(`--allowedTools`), 대화 턴 수 상한(`--max-turns`)을 둔다.

<a id="expo"></a>
### 7-4. Expo 토큰(앱 배포용, 선택)

<https://expo.dev/settings/access-tokens> 에서 토큰을 만들어 `EXPO_TOKEN` 에 넣는다. 앱 배포(`/deploy`) 때만 쓴다.

### 7-5. Actions 켜기와 정기 실행 시각

1. 저장소의 **Actions** 탭 → 워크플로 사용 허락 버튼이 보이면 누른다.
2. 결과 커밋이 403 으로 거절되면 **Settings → Actions → General → Workflow permissions → Read and write permissions** 로 바꾼다(워크플로 파일이 필요한 권한을 직접 요청하지만, 저장소·조직 설정이 막을 수 있다 [미확인]).
3. 정기 실행(시각은 UTC 로 적혀 있다 — 한국은 +9시간):

| 워크플로 | 언제(KST) | 하는 일 |
|---|---|---|
| `weekly-publish` | 일 12:00 | 기획→원고→렌더→독창성 검사→예약 발행(`/weekly-publish start`), 결과 커밋, 렌더 결과 7일 보관 |
| `publish-due` | 매시 7분 | 공개 시각이 된 인스타·틱톡·티스토리 올리기, queue.json 커밋. 할 일이 없으면 바로 끝난다 |
| `comments` | 매일 21:00 | 유튜브 새 댓글 답글(`/yt-comment-reply`) |
| `weekly-report` | 토 23:30 | 지표 수집→매출 보고→광고 판단→다음 주 전략(reports/ 커밋) |
| `ci` | 푸시·PR | 파이썬 테스트, Edge Function 테스트 |

- 정기 실행은 **기본 브랜치에서만** 돈다. 혼잡할 때 몇 분~수십 분 늦을 수 있다 [미확인]. 그래서 정시 공개는 플랫폼 예약 기능(유튜브 publishAt 등)에 맡긴다.
- 공개 저장소는 60일 동안 활동이 없으면 정기 실행이 꺼진다(봇 커밋이 활동으로 잡히는지 [미확인]). 꺼지면 Actions 탭에서 다시 켠다.
- 실패하면 `automation-failure` 라벨이 붙은 이슈가 자동으로 생긴다(같은 실패가 반복되면 같은 이슈의 본문만 갱신된다). 고친 뒤 **Run workflow** 로 다시 돌리고 이슈를 닫는다.

---

<a id="rehearsal"></a>
## 8. 첫 주 리허설

리허설은 **실제 업로드·유료 음성 합성 없이** 한 주를 끝까지 돌려 보는 것이다(`STUDIO_DRY_RUN=1` + `--tts silent`).

### 8-1. 내 컴퓨터에서

```bash
python -m studio doctor                    # [X] 를 줄인다(리허설은 비밀값 없이도 대부분 돈다)
```
저장소 폴더에서 Claude Code 를 열고:
```
/weekly-publish start --dry-run
```
- 원고(content/weeks/<주차>/)·렌더(out/weeks/<주차>/)·독창성 결과(originality.md)·실행 보고서(RUN_REPORT.md)가 생긴다.
- 발행 단계는 보낼 요청만 기록한다(`out/weeks/<주차>/queue.dryrun.json`). queue.json 은 바뀌지 않는다.
- 리허설로 만든 원고를 실제 주차에 쓰지 않으려면 커밋하지 말고 `content/weeks/<주차>/` 를 지운다.

### 8-2. GitHub Actions 에서

1. **Actions → weekly-publish → Run workflow** → `dry_run` 체크 → 실행. 리허설은 커밋하지 않는다.
2. 끝나면 실행 화면 **Artifacts** 에서 `out-<주차>-…`(영상·카드뉴스·블로그)과 `content-<주차>-rehearsal-…`(원고·보고서)를 내려받아 본다.
   - 확인할 것: 영상 길이·자막·한글 폰트, 카드뉴스 글자 넘침, 블로그 표, `originality.md` 의 차단·경고, `RUN_REPORT.md` 의 "사람이 할 일".
3. **publish-due** 를 `dry_run` 으로, **comments** 를 `dry_run` 으로, **weekly-report** 를 `fake` 로 한 번씩 돌려 본다.
4. 실행 요약(Summary)에 doctor 결과가 있다. 실제 운영 전에는 `[X]` 가 없어야 한다.

### 8-3. 첫 실제 주 체크리스트

- [ ] `channel.yaml`: 채널 이름·주제, `app.link_base`, `platforms.*.enabled`, 유튜브 `audited`, 틱톡 `audited`/`mode`, 티스토리 `blog_name`/`mode`
- [ ] `python -m studio doctor` 가 `문제 없음`
- [ ] 쇼츠 수: 처음 4주는 `schedule.shorts_per_week: 7` 로 낮춰 검사기 통과율·시청 지속률을 보고 올리는 것을 권장
- [ ] 일요일 실행 후: 유튜브 감사 전이면 업로드 패키지를 Studio 에서 예약 업로드하고 `publish mark` 로 연결(1-3)
- [ ] 월요일: Actions 의 `publish-due` 기록과 각 플랫폼에 실제로 올라갔는지 확인
- [ ] 토요일 밤 보고서(`reports/<주차>/channel-revenue.md`) 확인 → 다음 날 기획에 자동 반영된다

---

<a id="law-api"></a>
## 9. 국가법령정보 API (시험 대비 주제에서 문제은행 검증용)

`channel.yaml` 에 `exam:` 절이 있으면 주간 실행이 문항의 근거 조문을 법령 원문과 대조한다.

1. <https://open.law.go.kr/> 에 가입하고 **OPEN API 활용 신청**을 한다. 신청할 때 호출할 서버의 IP·도메인을 등록하라고 한다.
2. `LAW_API_OC` 는 가입 이메일의 `@` 앞부분이다(예: `g4c@korea.kr` → `g4c`). `.env` 와 GitHub Secrets 에 넣는다.
3. GitHub Actions 러너는 실행마다 IP 가 바뀐다. 이런 호출을 허용하는지는 [미확인]이다. 막히면
   - 워크플로가 `out/cache`(법령 응답 캐시, 7일)를 복원하므로 최근에 받은 조문은 그대로 쓴다.
   - 내 컴퓨터에서 `python -m studio bank verify` 를 돌려 결과를 커밋하는 방식으로 운영한다.

---

## 10. 문제 해결

| 증상(로그·이슈에 보이는 말) | 원인 | 할 일 |
|---|---|---|
| `[설정 필요] 환경변수 X 가 없습니다` | Secret 이 없거나 이름이 다름 | 7-2 표의 이름 그대로 다시 등록 |
| 유튜브 `invalid_grant` | 동의 화면이 Testing 이라 7일 만료, 또는 권한 취소 | 1-2 의 Production 전환 확인 → `auth youtube` 다시 → Secret 교체 |
| 인스타 `code 190` | 토큰 만료 | 2-3 다시 |
| 틱톡 `unaudited_client_can_only_post_to_private_accounts` [미확인] | 감사 전 | 3-7 의 `mode: inbox` 또는 감사 신청 |
| 티스토리 "로그인 페이지" | 세션 만료 | 4-2 다시 → `TISTORY_STORAGE_STATE_B64` 교체 |
| 결과 커밋 단계 `rebase 충돌` | 두 실행이 같은 파일을 동시에 고침 | 실패 이슈를 보고 해당 워크플로를 다시 실행 |
| 결과 커밋 단계 403 | 쓰기 권한·브랜치 보호 | 7-1, 7-5 의 2 |
| `/go` 가 503 | 함수 비밀값의 목적지가 비어 있음 | 5-3 의 `ANDROID_PACKAGE` 등 |
| 웹훅 401 | RevenueCat 의 Authorization 값과 `REVENUECAT_WEBHOOK_AUTH` 가 다름 | 6-3 |
| Supabase 요청이 모두 실패 | 무료 프로젝트 자동 정지 | 대시보드에서 **Resume project** |
| `doctor` 가 "공개 키 자리에 서버 비밀 키" | 키를 바꿔 넣음 | 즉시 키를 바꿔 넣고, 노출됐다면 Supabase 에서 키를 재발급 |
