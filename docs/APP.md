# 함정노트 앱 (Expo) 운영 문서

공인중개사 시험 대비 학습 앱 「함정노트: 공인중개사 OX·숫자암기」(가칭)의 구조·실행·결제·배포·심사 준비를 정리한다.
기준일 2026-10-10. 확인하지 못한 것은 **[미확인]** 으로 표시했다.

- 스택: Expo SDK 57(expo 57.0.27, React Native 0.86.3, React 19.2.3), TypeScript strict, expo-router(파일 라우팅, `src/app`), npm
- 결제: RevenueCat `react-native-purchases`·`react-native-purchases-ui` 10.12.2(엔타이틀먼트 `premium`)
- 저장: 문항 팩은 Supabase Storage 공개 URL → 기기 문서 폴더 캐시(expo-file-system), 학습 기록은 SQLite(expo-sqlite)
- 광고 SDK 없음. 이름·연락처를 받지 않는다(익명 ID만).

---

## 1. 폴더 구조

```
app/
  app.config.ts        앱 설정(이름·scheme·패키지 ID·runtimeVersion fingerprint·updates.url·플러그인)
  eas.json             EAS 빌드(development / preview / production)와 제출 프로필
  .env.example         환경변수 예시(복사해서 .env)
  assets/images/       아이콘·적응형 아이콘·스플래시
  assets/seed/         내장 시드 팩(첫 실행·오프라인용, studio 팩 형식)
  scripts/pull-seed.mjs  검증된 팩으로 시드 교체
  src/app/             화면(expo-router)
    _layout.tsx          루트: 전역 상태·테마·에러 바운더리·온보딩 보호(Stack.Protected)
    onboarding.tsx       첫 실행: 고지 → 회차·D-day → 약한 과목 → 유입 경로(선택)
    (tabs)/index.tsx     오늘: D-day·무료 남은 문항·연속 학습·가채점·영상 문항·개정 알림·과목 진도
    (tabs)/review.tsx    오답노트·SRS 복습(유료)
    (tabs)/stats.tsx     약점 진단(유료, 무료는 전체 정답률만)
    (tabs)/more.tsx      메뉴
    quiz.tsx             문제 풀이(mode=daily|review|wrong|deck|subject)
    deck/[assetId].tsx   영상 연동 덱(딥링크 진입점)
    decks.tsx            덱 목록(매니페스트에 decks 가 있을 때만 메뉴에 보임)
    cards.tsx            숫자 암기 카드(유료, 무료는 첫 세트)
    mock/index.tsx, mock/run.tsx  자체 모의고사(유료)
    amendments/index.tsx, amendments/[id].tsx  개정 알림(제목 무료, 상세 유료)
    grading.tsx          시험 당일 가채점(무료)
    settings.tsx         글자 크기·고대비·다크모드·시험·구독 관리·복원·정책 링크·초기화
    paywall.tsx          이용권(결제 전 고지 + 상품)
  src/core/            순수 로직(단위 테스트 대상): srs, quota, time, exam, grading, mock, provisional,
                       referrer, outbox, stats, session, offer, copy, amendments, pack/{types,validate,sync}
  src/data/            입출력: db(SQLite), repo, packStore(파일·sha256), outboxStore(Supabase REST),
                       purchases(RevenueCat), telemetry(이벤트), attribution(설치 리퍼러), seed
  src/features/quiz/   문항 카드·유형별 입력·근거 배지·신고
  src/state/           AppProvider(설정·팩·유료 여부·한도·연속 학습)
  src/ui/              공통 부품·테마·에러 화면·유료 입구
scripts/deploy_app.sh  배포(/deploy)
.github/workflows/app-deploy.yml  수동 실행 배포
```

`.gitignore` 에 전역 `lib/`·`build/`·`dist/`·`*.spec` 패턴이 있으므로 app 안에서 그런 폴더·파일 이름을 쓰지 않는다
(테스트는 `*.test.ts`). `app/android`, `app/ios` 는 CNG(prebuild)가 매번 만든다.

---

## 2. 로컬 실행

```bash
cd app
npm ci
cp .env.example .env        # 값 채우기(없어도 앱은 시드 팩으로 동작)
npx expo start              # Expo Go 또는 개발 빌드로 열기
```

- **Expo Go**: 화면·학습 기능은 모두 동작한다. RevenueCat 은 Expo Go 에서 "Preview API Mode"(가짜 응답)라 실제 결제는 안 된다.
- **개발 빌드(결제·설치 리퍼러 확인용)**: `npx eas-cli build --profile development --platform android` → 설치 후 `npx expo start`.
- RevenueCat 키가 없으면 결제 버튼은 안내만 하고, 개발 빌드(`__DEV__`)에서는 설정 화면의 "유료 기능 미리보기" 스위치로
  유료 화면을 점검할 수 있다(출시 빌드에는 나타나지 않음).

### 환경변수

| 이름 | 쓰는 곳 | 설명 |
|---|---|---|
| `EXPO_PUBLIC_SUPABASE_URL` | 앱 | `https://<ref>.supabase.co` |
| `EXPO_PUBLIC_SUPABASE_ANON_KEY` | 앱 | **공개 키만**(legacy anon JWT 또는 `sb_publishable_…`). service_role/secret 금지 |
| `EXPO_PUBLIC_BANK_MANIFEST_URL` | 앱 | (선택) 기본 `<URL>/storage/v1/object/public/media/bank/latest/manifest.json` |
| `EXPO_PUBLIC_RC_IOS_KEY`, `EXPO_PUBLIC_RC_ANDROID_KEY` | 앱 | RevenueCat 공개 SDK 키 |
| `EXPO_PUBLIC_PAYWALL_MODE` | 앱 | `custom`(기본) 또는 `revenuecat`(대시보드 Paywall) |
| `EXPO_PUBLIC_PRIVACY_URL`, `EXPO_PUBLIC_TERMS_URL`, `EXPO_PUBLIC_SUPPORT_EMAIL` | 앱 | 정책 링크·문의 |
| `EAS_PROJECT_ID` | 빌드·CLI | `eas init` 이 만든 프로젝트 ID → `updates.url`, `extra.eas.projectId` |
| `APP_ANDROID_PACKAGE`, `APP_IOS_BUNDLE_ID` | 빌드·CLI | 기본 `kr.hamjeongnote.app` |
| `APP_DISPLAY_NAME` | 빌드 | 홈 화면 이름(기본 "함정노트"). 스토어 제목은 스토어 콘솔에서 따로 적는다 |
| `APP_LINK_DOMAIN` | 빌드 | (선택) https 유니버설 링크 도메인. 비우면 유니버설 링크를 넣지 않는다 |

`EXPO_PUBLIC_*` 는 번들에 그대로 들어가는 공개값이다. EAS 빌드에서는 `npx eas-cli env:create --environment production …`
으로 등록하고(빌드 프로필이 `environment` 를 지정), CI 는 GitHub Variables 로 넘긴다.

---

## 3. 데이터

### 3-1. 문항 팩

- 형식: **`studio/bank/pack_schema.json`**(`python -m studio bank export` 가 만들고 `bank upload` 가 올림).
  앱은 `src/core/pack/validate.ts` 로 다시 검사한다. 매니페스트가 틀리면 팩 전체를, 문항 하나가 틀리면 그 문항만 버린다.
  근거(조문: 법령명·조항·시행일 / 판례: 사건번호·선고일)가 없는 문항은 출제하지 않는다.
- 정답 형식: ox=boolean, mcq=보기 인덱스(0부터), blank=문자열, order=choices 인덱스(0부터) 순서, calc=숫자.
  화면의 보기 번호는 ①부터.
- 받는 순서: `bank/latest/manifest.json`(캐시 우회) → 버전이 바뀌었으면 과목 파일을 `bank/<version>/<코드>.json`
  (없으면 `latest/`)에서 받아 **sha256 대조** → 구조 검사 → `문서폴더/bank/v/<version>/` 에 쓰고 마지막에
  `current.json` 을 바꾼다(중간에 꺼져도 이전 팩 유지). 같은 sha256 의 과목 파일은 다시 받지 않는다.
- 갱신 시점: 앱 시작 직후(화면은 캐시로 먼저 뜸), 그리고 6시간 넘게 지나 앱이 다시 앞으로 올 때.
- 매니페스트 `free_daily`(무료 하루 문항 수), `exam.papers`(모의고사·가채점 과목 묶음), `disclaimer`, 문항 `alerts`
  (시험 전 개정 예정 → 문항 경고 + 개정 알림 목록)를 쓴다.
- 스키마에 아직 없는 선택 필드(있으면 쓰고 없으면 숨김): `decks`(asset_id → 문항 id 배열), `min_app_version`,
  `extra.{amendments, number_cards, grading, exam_rules, disabled_ids}`. → 문제은행 담당에 추가를 요청해 둠.

### 3-2. 내장 시드 팩

- `app/assets/seed/` — 첫 실행·오프라인 대비. **지금 들어 있는 시드는 근거 대조 전 점검용(`extra.unverified: true`)** 이라
  앱이 "점검용" 배너를 띄우고 배지에 시행일 대신 "시행일 대조 전"을 쓴다. `scripts/deploy_app.sh` 는 이 상태로 배포를 막는다.
- 출시 전: `python -m studio bank export` → `cd app && npm run seed:pull`(기본 무료 문항만, `--all` 전체, `--from <폴더|URL>`).
  sha256 을 대조하고 `src/data/seed.ts` 를 다시 만든다. 그다음 `npx jest src/core/__tests__/seed.test.ts`.

### 3-3. 학습 기록(기기 로컬, SQLite `hamjeong.db`)

| 테이블 | 내용 |
|---|---|
| `answers` | 응답 이력(item_id, correct, mode, answered_at, pack_version) |
| `srs` | 문항별 복습 상태(EF, 반복, 간격, 기한, 실수 횟수, 최근 오답) |
| `mock_results` | 모의고사 결과(세트, 점수, 과락 여부, 문항별 정오) |
| `outbox` | Supabase 로 보낼 행(재전송 큐) |
| `kv` | 설정, 무료 한도 카운터, 연속 학습, 익명 ID, 유료 상태 캐시, 가채점 답안 |

- **SRS**: SM-2 변형(`src/core/srs.ts` 주석에 공식·근거). 오답 → 10분 뒤 재출제 후 1일부터, 정답 → 1일 → 6일 → ×EF.
  "맞혔지만 헷갈렸어요"는 q=3. 간격은 시험일까지 남은 일수의 절반을 넘지 않는다.
- **무료 한도**: 하루 `free_daily`(기본 20)문항. **KST 자정**에 초기화(기기 시간대와 무관). 영상 덱 문항은 한도에서 빠진다.
- 데이터 초기화(설정)는 학습 기록·설정을 지우고 익명 ID·설치 보고 여부·이용권 캐시는 남긴다.

---

## 4. Supabase 연동(공개 키, INSERT 만)

anon 역할은 `app_installs`·`app_events` 에 INSERT 만 할 수 있다(계약). 앱은 `POST /rest/v1/<table>` 에
`apikey` 헤더(옛 JWT 키면 `Authorization: Bearer` 도), `Prefer: return=minimal` 로 보낸다.
계약에 없는 열은 빼고 보내며, 실패하면 SQLite outbox 에 남겨 30초부터 2배씩(최대 1시간) 재전송한다.
409(고유키 중복)는 "이미 들어감"으로 본다. 20회 또는 14일이 지나면 버린다.

| 테이블 | 보내는 값 |
|---|---|
| `app_installs` | app_user_id(RevenueCat appUserID), os, utm_source/medium/campaign/content, raw_referrer(500자), app_version |
| `app_events` | app_user_id, name, props(jsonb: 아래 + 공통 ts·app_version·os·pack_version) |

| 이벤트 name | props |
|---|---|
| `session_start` | premium |
| `paywall_view` | source(진입 화면), mode, configured |
| `purchase_success` | product_id, source (클라이언트 관측값 — 매출 집계는 RevenueCat 웹훅이 기준) |
| `deck_open` | asset_id, found, items, utm_* (딥링크 쿼리) |
| `report` | item_id, reason(wrong_answer·wrong_basis·law_changed·typo·other), note(300자), pack_version |
| `answers_batch` | items: [{id, correct}] × 20 (오답 랭킹 쇼츠 데이터) |
| `attribution_survey` | source(온보딩 "어디서 알게 되셨나요") |
| `grading_open` | status(before·waiting·open), round |

---

## 5. 어트리뷰션

- **Android**: 첫 실행에 한 번 `expo-application` `getInstallReferrerAsync()` → `utm_*` 파싱(한 번 더 URL 인코딩된 값도 처리)
  → `app_installs` + RevenueCat 속성 `$mediaSource`(utm_source)·`$campaign`·`$adGroup`(utm_medium)·`$ad`(utm_content)·
  `$keyword`(utm_term) 과 커스텀 `utm_source`… 속성.
  Play 링크: `https://play.google.com/store/apps/details?id=kr.hamjeongnote.app&referrer=utm_source%3Dyt_shorts%26utm_medium%3Dpinned_comment%26utm_campaign%3D2026w42`
  (`referrer=` 뒤를 URL 인코딩하는 관행 — 공식 문서 원문 [미확인]).
- **iOS**: 앱이 캠페인 값을 읽을 수 없어 `os=ios` 로만 기록. App Store 캠페인 링크(`ct=`)는 App Analytics 집계로만 본다.
  온보딩 설문 답을 `survey_source` 속성(리퍼러가 없으면 `$mediaSource=survey_<값>`)으로 넣어 보완한다.
- `utm_source` 값은 채널 규칙 `yt_shorts | yt_long | ig_reels | ig_card | fb_reels | tiktok | tistory` 를 쓴다.
- `collectDeviceIdentifiers()`(광고 ID 등)는 부르지 않는다. Android `AD_ID` 권한은 막아 두었다.

---

## 6. 딥링크

- 스킴: `hamjeongnote://deck/<asset_id>?utm_source=yt_shorts&utm_campaign=2026w42` → 영상 덱 화면(온보딩 전에도 열림).
- https 링크: `APP_LINK_DOMAIN` 을 넣고 빌드하면 iOS `applinks:<도메인>`, Android `autoVerify` 인텐트 필터(`/deck` 경로)가 들어간다.
  그 도메인에 `/.well-known/apple-app-site-association`(팀 ID·번들 ID, `/deck/*`)과 `/.well-known/assetlinks.json`
  (패키지·서명 SHA-256, `npx eas-cli credentials -p android` 에서 확인)을 올려야 한다. Supabase Functions 도메인은 루트
  `.well-known` 을 둘 수 없으므로 별도 도메인이 필요하다 [미확인: 도메인 미정].
- 매니페스트에 `decks` 가 없으면 덱 메뉴는 숨기고, 덱 링크는 "문항을 찾지 못했습니다"로 안내한다.

---

## 7. RevenueCat·스토어 상품 설정 순서

### 7-1. 상품 ID 규칙(제안)

| 이용권 | App Store Connect | Google Play | RevenueCat 패키지 |
|---|---|---|---|
| 월 9,900원 자동 갱신 | 자동 갱신 구독 `kr.hamjeongnote.premium.monthly` (그룹 "함정노트 이용권", 1개월) | 구독 `premium` / 기본 요금제 `monthly`(P1M, 자동 갱신) | `$rc_monthly` |
| 연 69,000원 자동 갱신, 체험 14일 | `kr.hamjeongnote.premium.yearly` (같은 그룹, 1년, 도입 혜택: 무료 14일) | 구독 `premium` / 기본 요금제 `yearly`(P1Y) + 혜택 `trial14`(무료 체험 14일) | `$rc_annual` |
| 막판 패스 29,000원 비갱신 | 비갱신 구독 `kr.hamjeongnote.pass.60d` | 구독 `pass` / **선불(prepaid) 기본 요금제** `p60d` [미확인: 선불 요금제에 60일 기간이 있는지. 없으면 2개월(P2M)로 하고 앱 고지문은 스토어 기간을 그대로 표시] | 커스텀 `pass_60d` |

- 엔타이틀먼트: `premium` 하나에 세 상품을 모두 붙인다. 오퍼링: `default`(current)에 세 패키지.
- 패스는 앱이 **매년 8월 1일 ~ 시험일(KST)** 에만 보여 준다(`isPassOnSale`). 판매 중단은 오퍼링에서 패키지를 빼도 된다.
- iOS 비갱신 구독은 스토어가 만료일을 주지 않는다. RevenueCat 쪽 기간 설정 가능 여부 [미확인] — 앱은 만료일이 없으면
  마지막 구매일 + 60일로 판정하는 방어 코드를 둔다(`premiumFromCustomerInfo`).
- 가격 A/B(연 69,000 vs 79,000, 체험 7일 vs 14일)는 RevenueCat Experiments 로 오퍼링을 바꿔 한다. 앱은 오퍼링의
  `priceString`·기간·체험 기간을 그대로 보여 주므로 코드 변경이 없다.

### 7-2. 순서

1. App Store Connect: 앱 생성(번들 ID) → 유료 앱 계약·세금·은행 → 구독 그룹·상품·가격(대한민국) → 도입 혜택(무료 14일)
   → 심사용 스크린숏·설명. Small Business Program 신청(수수료 15%).
2. Google Play Console: 앱 생성(패키지) → 결제 프로필 → 정기 결제 상품(위 표) → 라이선스 테스터 등록.
   Play 는 앱 번들을 한 번 올려야 상품을 만들 수 있다 → 먼저 내부 테스트 트랙에 빌드 1개 업로드.
3. RevenueCat: 프로젝트 → iOS 앱(App Store Connect API In-App Purchase 키) / Android 앱(Play 서비스 계정 JSON) 연결
   → 상품 가져오기 → 엔타이틀먼트 `premium` → 오퍼링 `default` → 공개 SDK 키를 `EXPO_PUBLIC_RC_IOS_KEY`/`…ANDROID_KEY` 로.
4. (선택) RevenueCat Paywall 을 만들면 `EXPO_PUBLIC_PAYWALL_MODE=revenuecat`. 그때도 대시보드 Paywall 안에
   자동 갱신·해지 문구를 넣어야 한다. 기본(custom) 화면은 상품마다 가격 옆에 고지문을 앱이 직접 그린다.
5. 웹훅(RevenueCat → Supabase `purchases`)은 supabase 담당 함수가 받는다. 앱은 `purchase_success` 를 관측용으로만 보낸다.

### 7-3. 결제 전 고지(앱 `src/core/offer.ts`)

- 자동 갱신 상품: 스토어 가격·주기, "자동 결제", iOS "기간 종료 24시간 전까지 해지하지 않으면 갱신·24시간 안 청구",
  Android "해지해도 결제한 기간까지 이용", 해지 경로(iOS 설정 > 내 이름 > 구독 / Play 스토어 > 프로필 > 결제 및 정기 결제).
- 체험: 체험 일수, 체험 후 결제 금액, 해지 시점. 패스: 1회 결제·자동 갱신 안 됨·이용 기간.
- 공통: 스토어 계정 청구, 환불은 스토어 정책, 구매 복원 안내, 이용약관·개인정보처리방침 링크, "구독 관리·해지"·"구매 복원" 버튼.

---

## 8. EAS 첫 빌드·제출

```bash
cd app
npx eas-cli login
npx eas-cli init                         # 프로젝트 생성 → 출력된 ID 를 EAS_PROJECT_ID 로(app/.env, GitHub Variables, EAS env)
npx eas-cli env:create --name EAS_PROJECT_ID --value <ID> --environment production --environment preview --environment development --visibility plaintext
npx eas-cli env:create --name EXPO_PUBLIC_SUPABASE_URL --value https://<ref>.supabase.co --environment production --visibility plaintext
#  … EXPO_PUBLIC_SUPABASE_ANON_KEY, EXPO_PUBLIC_RC_IOS_KEY, EXPO_PUBLIC_RC_ANDROID_KEY, EXPO_PUBLIC_PRIVACY_URL, EXPO_PUBLIC_TERMS_URL 도 같은 방식
npx eas-cli build --platform android --profile production   # 첫 빌드는 대화형으로(키스토어 생성)
npx eas-cli build --platform ios --profile production       # Apple 로그인·인증서·프로비저닝 생성
```

- `eas.json`: `cli.appVersionSource: remote` + production `autoIncrement` → versionCode·buildNumber 를 EAS 가 올린다.
  채널: development / preview / production. `runtimeVersion.policy = fingerprint` → 네이티브가 바뀌면 런타임이 바뀌어
  옛 바이너리에 맞지 않는 OTA 가 가지 않는다.
- 제출 자격 증명은 EAS 서버에 한 번 등록한다: `npx eas-cli credentials -p android`(Google 서비스 계정 키, Play 제출용) /
  `npx eas-cli credentials -p ios`(App Store Connect API 키).
- `eas.json` `submit.production.ios.ascAppId` 의 `0000000000` 을 App Store Connect 앱 ID 로 바꾼다(배포 스크립트가 검사).
- Android 제출은 `track: internal`, `releaseStatus: draft` — Play Console 에서 검토 후 프로덕션으로 승격한다.
- Play 의 첫 업로드는 API 로 안 되는 경우가 있어 첫 번째 AAB 는 콘솔에서 직접 올린다 [미확인].

---

## 9. 배포 — `/deploy`

```bash
scripts/deploy_app.sh ["메시지"] [--dry-run] [--force-build] [--platform all|android|ios] [--wait]
```

1. `npm ci`(필요 시) → 시드 팩 검사(점검용 시드면 중단, 내부 테스트는 `--allow-unverified-seed`) → `tsc --noEmit` → `jest --ci`
2. `EAS_PROJECT_ID`·`EXPO_TOKEN`(또는 `eas login`) 확인
3. 플랫폼별 `eas fingerprint:generate --platform <p> --environment production --build-profile production --json`
   → `.hash` → `eas build:list --platform <p> --status finished --build-profile production --fingerprint-hash <hash> --limit 1 --json`
4. 같은 지문의 완료 빌드가 있으면 `eas update --channel production --environment production --message … --platform …`,
   없거나 `--force-build` 면 `eas build --platform … --profile production --auto-submit --no-wait`(기본, CI 시간 절약)
5. `--dry-run` 은 위 계획만 출력한다(토큰이 있으면 읽기 전용 조회로 실제 판정까지 보여 줌).

- Claude Code 에서 `/deploy 메시지` → `.claude/skills/deploy` 가 이 스크립트를 부른다.
- GitHub Actions: **Actions → app-deploy → Run workflow**(message, force_build, platform, dry_run).
  Secret `EXPO_TOKEN`, Variables `EAS_PROJECT_ID`(선택 `EAS_OWNER`, `APP_ANDROID_PACKAGE`, `APP_IOS_BUNDLE_ID`, `APP_LINK_DOMAIN`).
- OTA 로 되는 것: JS·문구·화면·로직. 안 되는 것: 네이티브 의존성 추가, 권한, 아이콘·스플래시·scheme·플러그인 변경, SDK 업그레이드
  → 스크립트가 지문으로 자동 판정한다.
- 롤백: `npx eas-cli update:rollback`.

---

## 10. 스토어 심사 체크리스트

**공통**
- [ ] 앱 설명·스크린숏·앱 안(온보딩·설정·하단 문구)에 "한국산업인력공단·큐넷과 무관한 비공식 학습 앱" 고지
- [ ] 금지 표현 없음(합격 보장·적중·공식·국가공인·큐넷 제공 등) — 앱 문구는 `copy-offer.test.ts` 가 자동 검사
- [ ] 공단 로고·큐넷 화면 모방 없음, 실존 강사·합격자 이름·사진 없음
- [ ] 모의고사 점수는 "예상 점수(참고용)", 합격 예측 문구 없음 / 가채점은 "가답안 기준" 표기
- [ ] 시드 팩을 검증된 팩으로 교체(`npm run seed:pull`) — 점검용 배너가 안 보여야 함
- [ ] 개인정보처리방침·이용약관 URL(앱 안 설정·결제 화면, 스토어 등록 정보 양쪽)
- [ ] 문의 이메일(`EXPO_PUBLIC_SUPPORT_EMAIL`)

**구독(Apple 3.1.2 / Play 정기 결제 정책)**
- [ ] 결제 버튼 근처에 가격·주기·자동 갱신·해지 방법·체험 조건 표시(앱 결제 화면 충족)
- [ ] "구매 복원" 버튼(결제 화면·설정)
- [ ] "구독 관리·해지" 버튼(설정 → 스토어 구독 관리)
- [ ] 심사용 샌드박스 계정으로 구매·복원 시나리오 확인, 심사 메모에 "로그인 없음, 결제는 설정 > 이용권"
- [ ] 무료로 쓸 수 있는 기능이 충분한지(하루 20문항·영상 덱·D-day·가채점) — 결제 없이도 앱이 동작

**개인정보**
- [ ] App Store 개인정보 라벨: 식별자(익명 사용자 ID — 앱 기능·분석, 추적 아님), 구매 내역(RevenueCat), 사용 데이터(정답 여부·화면 이용)
      / 추적(ATT) 없음
- [ ] Play 데이터 보안 양식: 같은 항목, 전송 중 암호화(HTTPS), 사용자가 데이터 삭제 요청 가능(설정 초기화 + 문의) [미확인: 서버 행 삭제 절차 마련 필요]
- [ ] Play "광고 ID" 선언: 사용 안 함(`AD_ID` 권한 제거됨)
- [ ] iOS 수출 규정: `ITSAppUsesNonExemptEncryption=false`(HTTPS 만)
- [ ] 계정 기능이 없으므로 계정 삭제 요건 해당 없음

**Google Play 개인 개발자 계정**
- [ ] 2023-11 이후 만든 개인 계정은 프로덕션 출시 전 **비공개 테스트 12명 이상 · 14일 연속 참여** 요건 [미확인: 현재 기준 인원·기간 재확인 필요].
      12월 중순 출시 목표면 늦어도 11월 하순에 비공개 테스트 트랙을 시작해야 한다(테스터는 채널 시청자·지인 모집).
- [ ] 콘텐츠 등급 설문(교육), 대상 연령, 뉴스 앱 아님, 정부 앱 아님(비공식 고지)
- [ ] Play Billing Library 8 요구(신규 앱·업데이트) — RevenueCat 10.x 충족 여부 [미확인]

**기타 [미확인]**
- 앱 이름 상표(KIPRIS)·스토어 중복, 원격평생교육시설 신고 대상 여부

---

## 11. 검증 명령

```bash
cd app
npx tsc --noEmit                      # 타입 검사
npx jest                              # 핵심 로직 단위 테스트(srs·한도·팩 검증·리퍼러·채점·가채점·모의고사·큐·고지문·금지 표현·시드)
npx expo export --platform android    # JS 번들 생성 확인(Hermes)
npx expo-doctor                       # 일부 검사는 exp.host·React Native Directory 접속 필요
bash ../scripts/deploy_app.sh --dry-run
```
