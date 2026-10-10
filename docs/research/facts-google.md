# YouTube·Google 구현 사실 확인 보고서 (기준일 2026-10-10)

## 0. 조사 방법과 한계

- **WebFetch는 쓰지 못했습니다.** DNS 오류가 났고, 프록시가 다음 호스트를 정책상 차단했습니다(403). developers.google.com, support.google.com, docs.cloud.google.com, pexels.com, pixabay.com, elevenlabs.io, openai.com, azure.microsoft.com, learn.microsoft.com.
- **1차 출처로 직접 받은 것**은 다음과 같습니다. 필드 이름과 엔드포인트는 이 파일들에서 확정했습니다.
  - Google 공식 API 명세(Discovery 문서): YouTube Data v3(revision 20261006), YouTube Analytics v2(20261007), Cloud TTS v1(20260827)
  - cloud.google.com/text-to-speech/pricing 원문
  - GitHub raw에 있는 공식 샘플과 라이브러리 소스, GitHub Docs 원문
  - PyPI 메타데이터
- **나머지는 WebSearch 요약에 의존했습니다.** 검색이 공식 페이지를 요약한 경우 "공식(검색 요약)"으로 표시했습니다.
- **WebSearch 한도(턴당 200회, 공유)를 다 썼습니다.** 그래서 일부 항목은 "미확인"으로 남았습니다. 후속 메시지를 보내 주시면 이어서 확인할 수 있습니다.

---

## 1. 결론: 설계를 바꿔야 하는 사실 7가지

1. **감사 전에는 API 업로드가 비공개로 잠깁니다.** 2020-07-28 이후 만든 Cloud 프로젝트는 YouTube API Services 감사(audit)를 통과하기 전까지 videos.insert로 올린 영상이 비공개로 잠깁니다. 이 규정은 지금도 유효합니다. Google은 처리 기간(SLA)을 공개하지 않습니다.
   - 따라서 "일요일 12시 무인 공개"를 하려면 감사 통과가 전제입니다.
   - 통과 전에는 "업로드 패키지를 자동 생성하고, 사람이 Studio에서 예약 업로드" 모드로 운영하고, 통과 후 API 모드로 바꾸는 스위치가 필요합니다(제안).
2. **업로드 할당량 구조가 바뀌었습니다.**
   - 2025-12-04: 업로드 비용이 약 1,600 → 약 100단위로 바뀌었습니다.
   - 2026-06-01: `videos.insert`와 `search.list`가 각자 전용 버킷으로 분리됐습니다.
   - 현재 업로드는 하루 기본 100회이고, 1회에 1단위입니다(공식, 검색 요약). 주 15편은 넉넉합니다.
3. **고정 댓글은 API로 할 수 없습니다.** 2026-10-06 명세 전체에 pin 관련 메서드나 필드가 없습니다(직접 확인). 링크는 설명란(`snippet.description`)에 넣고, 고정은 사람이 하거나 포기해야 합니다.
4. **OAuth 동의 화면이 Testing 상태면 refresh token이 7일 만에 만료됩니다.** 무인 운영에는 쓸 수 없으므로 Production으로 전환해야 합니다.
   - 본인 1인 사용이라면 미검증 상태여도 경고 화면과 100명 상한만 있습니다.
   - OAuth 앱 검증과 YouTube API 감사는 **서로 다른 심사**입니다.
5. **쇼츠 판정 조건**: 정사각형이거나 세로이고 3분 이하이면 됩니다. API에 "쇼츠" 플래그는 없습니다. 1분을 넘는 쇼츠에 Content ID 소유권 주장이 걸리면 전 세계에서 차단되므로, 쇼츠는 60초 이하를 권장합니다.
6. **TTS 1순위는 Google Cloud TTS Chirp 3 HD**입니다. 매월 100만 자까지 무료이고, 이 운영량(추정 월 6~7만 자)은 무료 범위 안입니다. edge-tts는 비공식이고 상업 이용 시 약관 위반 소지가 있어 발행물에는 쓰지 않는 것이 맞습니다.
7. **합성 콘텐츠 공개 필드는 `status.containsSyntheticMedia`(boolean)입니다.** 2024-10-30에 추가됐습니다. 실존 인물이나 실제 사건을 사실적으로 꾸민 경우에만 true로 하면 됩니다. 일반 TTS 내레이션만으로는 의무가 아닌 것으로 해석됩니다.

---

## 2. YouTube Data API v3

### 2-1. videos.insert (재개 가능 업로드)

**확정 (Discovery 문서, revision 20261006)**
- 메서드는 `POST youtube/v3/videos`입니다.
- 업로드 경로:
  - 단순/멀티파트: `/upload/youtube/v3/videos`
  - 재개 가능: `/resumable/upload/youtube/v3/videos`
- 허용 형식은 `video/*`, `application/octet-stream`이고, 최대 크기는 274,877,906,944바이트(256GB)입니다.
- 쿼리 파라미터: `part`(필수), `notifySubscribers`(기본 true), `onBehalfOfContentOwner`, `onBehalfOfContentOwnerChannel`, `autoLevels`, `stabilize`
- 쓸 수 있는 범위: `youtube`, `youtube.force-ssl`, `youtube.upload`, `youtubepartner`

**재개 가능 업로드 절차**
- 첫 POST에서 Location 헤더로 세션 URI를 받고, PUT으로 바이트를 보냅니다. 응답 308이면 Range 헤더 다음 바이트부터 다시 보냅니다.
- 출처: [Resumable Uploads 공식 가이드](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol?hl=ko)(검색 요약), [rapidevelopers](https://www.rapidevelopers.com/md/api-automations/how-to-automate-youtube-video-uploads-using-the-api)
- 세션 URI 유효기간 1주는 **미확인**입니다(서드파티 주장).

**라이브러리**
- python 클라이언트의 `MediaFileUpload` 기본 chunksize는 `100*1024*1024`입니다. `chunksize=-1`이면 한 번에 보냅니다.
- 출처: [googleapiclient/http.py](https://github.com/googleapis/google-api-python-client/blob/main/googleapiclient/http.py)

**주의: 공식 샘플은 그대로 쓰면 안 됩니다.**
- 공식 샘플 [upload_video.py](https://github.com/youtube/api-samples/blob/master/python/upload_video.py)는 Python 2(`httplib`)와 `flow.run_console()`을 씁니다.
- 현재 google-auth-oauthlib 1.5.0의 flow.py에는 `run_console`이 **없습니다**(직접 확인). `run_local_server`를 써야 합니다.

```http
POST https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status
Authorization: Bearer <ACCESS_TOKEN>
Content-Type: application/json; charset=UTF-8
X-Upload-Content-Length: <bytes>
X-Upload-Content-Type: video/mp4
→ 200, Location: <SESSION_URI>
PUT <SESSION_URI>   (Content-Length, Content-Type: video/mp4, 본문=바이트)
상태 조회: PUT <SESSION_URI>  Content-Range: bytes */<TOTAL>  Content-Length: 0  → 308 + Range: bytes=0-N
```

```python
# pip install google-api-python-client==2.201.0 google-auth-oauthlib==1.5.0  (PyPI 2026-09-30/29 최신)
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

body = {
  "snippet": {
    "title": "...", "description": "...앱 링크 ?utm_source=youtube&utm_medium=shorts&utm_campaign=wk41",
    "tags": ["..."], "categoryId": "27",          # 카테고리 ID는 videoCategories.list(regionCode="KR")로 조회 후 고정
    "defaultLanguage": "ko", "defaultAudioLanguage": "ko"
  },
  "status": {
    "privacyStatus": "private",                   # publishAt 쓰려면 반드시 private
    "publishAt": "2026-10-11T03:00:00Z",          # = 2026-10-11(일) 12:00 KST
    "selfDeclaredMadeForKids": False,
    "containsSyntheticMedia": False,
    "license": "youtube", "embeddable": True
  }
}
yt = build("youtube", "v3", credentials=creds)
req = yt.videos().insert(part="snippet,status", body=body, notifySubscribers=True,
                         media_body=MediaFileUpload("out.mp4", mimetype="video/mp4",
                                                    chunksize=8*1024*1024, resumable=True))
resp = None
while resp is None:
    status, resp = req.next_chunk()   # 5xx면 지수 백오프 후 재시도
video_id = resp["id"]
```

### 2-2. status 필드 (Discovery `VideoStatus` 원문)

| 필드 | 타입/값 | 비고 |
|---|---|---|
| `privacyStatus` | `public` \| `unlisted` \| `private` | |
| `publishAt` | string(ISO 8601) | "It can be set only if the privacy status of the video is private" |
| `containsSyntheticMedia` | boolean | "Indicates if the video contains altered or synthetic media." insert와 update에서 설정 가능. 2024-10-30 추가 ([revision history](https://developers.google.com/youtube/v3/revision_history)) |
| `selfDeclaredMadeForKids` | boolean | 명세에 설명은 없습니다. 아동용이 아니면 False |
| `madeForKids` | boolean | YouTube가 판정하는 값이며 읽기 전용입니다(기존 지식, 원문 미열람) |
| `license` | `youtube` \| `creativeCommon` | |
| `embeddable`, `publicStatsViewable` | boolean | |
| `uploadStatus` / `failureReason` / `rejectionReason` | enum | 업로드 후 `videos.list(part="status")`로 확인. `rejectionReason`에 `duplicate`가 있음 |

- 그 밖에 `paidProductPlacementDetails.hasPaidProductPlacement`(boolean)도 있습니다. 자체 앱 홍보는 제3자 유료 광고가 아니므로 False로 해석했습니다(**추측**).
- 제목 100자, 설명 5,000바이트, 태그 합계 500자 제한은 **미확인**입니다(기존 지식, 공식 페이지 미열람).

### 2-3. 할당량(quota)

```text
기본 할당 (공식, getting-started 검색 요약):
  일반 버킷 10,000 units/day, 자정 PT 리셋
  읽기 ≈ 1, 쓰기(생성/수정/삭제) ≈ 50

2026-06-01부터 전용 버킷 (revision history, 검색 요약):
  videos.insert  : "Video Uploads" 버킷, 기본 100 calls/day, 1 unit/call
                   (videos.insert 공식 페이지, 검색 요약)
  search.list    : 전용 버킷, 기본 100 calls/day (서드파티)
  2025-12-04     : 업로드 비용 ~1,600 → ~100으로 변경 (revision history)

메서드별 비용:
  captions.insert       400   (공식 captions/insert 페이지)
  playlistItems.insert   50   (공식 playlistItems/insert 페이지)
  thumbnails.set         50   (서드파티 다수)
  comments.insert        50   (서드파티)
  commentThreads.insert  50   (쓰기 일반 규칙. 명세의 part 설명에는 "snippet part has a quota cost of 2 units"라는 옛 문구가 남아 있음 → 미확인)
  commentThreads.list     1   (서드파티/읽기 규칙)
  videos.list             1
  videos.update          50
```

- 출처: [getting-started](https://developers.google.com/youtube/v3/getting-started), [determine_quota_cost](https://developers.google.com/youtube/v3/determine_quota_cost), [revision_history](https://developers.google.com/youtube/v3/revision_history), [videos.insert](https://developers.google.com/youtube/v3/docs/videos/insert), [captions.insert](https://developers.google.com/youtube/v3/docs/captions/insert), [playlistItems.insert](https://developers.google.com/youtube/v3/docs/playlistItems/insert), [Blotato](https://www.blotato.com/blog/youtube-api-pricing), [bundle.social](https://bundle.social/blog/youtube-api-quota-exceeded-limits-fixes), [GitHub issue ClipFlow#453](https://github.com/Oghenefega/ClipFlow/issues/453)
- 할당량은 채널이 아니라 Cloud 프로젝트 단위입니다. 한 API 클라이언트에 프로젝트를 여러 개 쪼개 쓰는 것은 금지입니다(Developer Policies, 검색 요약).

**주간 예산 추정 (이 시스템 기준)**
- 쇼츠 14편과 롱폼 1편 기준입니다: 업로드 15/100(전용 버킷), playlistItems 15×50=750, 링크 댓글 15×50=750, 롱폼 썸네일 50, 롱폼 자막 400, 상태 조회 약 30. 일반 버킷 합계 약 2,000/10,000입니다.
- 쇼츠 15편 모두에 `captions.insert`를 하면 6,000이 추가되어 위험합니다. 쇼츠는 자막을 영상에 입히고 API 자막은 롱폼만 올리는 것을 권장합니다.
- 일요일 12:00 KST는 PT로 토요일 20:00(PDT)입니다. 업로드는 평일에 나눠 하고 `publishAt`으로 공개 시각을 맞추면 할당량과 GitHub Actions 지연 위험이 함께 분산됩니다(제안).

### 2-4. thumbnails.set / playlistItems.insert / captions.insert

```text
thumbnails.set     POST /upload/youtube/v3/thumbnails/set?videoId=<ID>
                   accept: image/jpeg, image/png, application/octet-stream
                   명세상 maxSize 50MB (공식 페이지의 2MB 제한은 미확인)
                   범위: youtube | youtube.force-ssl | youtube.upload | youtubepartner
                   * 맞춤 썸네일은 채널 전화번호 인증 필요(기존 지식, 미확인)
                   * 쇼츠 피드에 맞춤 썸네일이 표시되는지 미확인
playlistItems.insert  POST youtube/v3/playlistItems?part=snippet
                   body: {"snippet":{"playlistId":"PL...","resourceId":{"kind":"youtube#video","videoId":"<ID>"},"position":0}}
                   범위: youtube | youtube.force-ssl | youtubepartner   (youtube.upload만으로는 불가)
captions.insert    POST /upload/youtube/v3/captions?part=snippet[&sync=true]
                   body: {"snippet":{"videoId":"<ID>","language":"ko","name":"한국어","isDraft":false}}
                   media: SRT/VTT 등, 명세상 maxSize 100MB
                   범위: youtube.force-ssl | youtubepartner
```

### 2-5. 댓글: commentThreads.list / commentThreads.insert / comments.insert

```text
commentThreads.list  GET youtube/v3/commentThreads
  part=snippet,replies
  allThreadsRelatedToChannelId=<CHANNEL_ID> | videoId=<ID>
  moderationStatus=published|heldForReview|likelySpam   (id 필터와 함께 쓸 수 없음)
  order=time|relevance   textFormat=plainText|html   maxResults   pageToken   searchTerms
  범위: youtube.force-ssl 뿐
comments.list        GET youtube/v3/comments?part=snippet&parentId=<TOP_COMMENT_ID>   (답글 전부 조회)
comments.insert(답글) POST youtube/v3/comments?part=snippet
  body: {"snippet":{"parentId":"<TOP_LEVEL_COMMENT_ID>","textOriginal":"답글 내용"}}
commentThreads.insert(새 최상위 댓글) POST youtube/v3/commentThreads?part=snippet
  body: {"snippet":{"channelId":"<CH>","videoId":"<ID>","topLevelComment":{"snippet":{"textOriginal":"..."}}}}
comments.setModerationStatus  POST youtube/v3/comments/setModerationStatus?id=..&moderationStatus=published|heldForReview|rejected[&banAuthor=true]
고정(pin): API 없음 (명세 2026-10-06 전체 검색, pin 메서드·필드 0건). Studio에서 사람이 고정.
```

- 응답 주요 필드: `snippet.topLevelComment.id`, `snippet.topLevelComment.snippet.textOriginal`, `authorChannelId`, `likeCount`, `publishedAt`, `snippet.totalReplyCount`, `snippet.canReply`
- `comments.markAsSpam`은 지원이 끝났습니다. 출처: [comments 구현 가이드](https://developers.google.com/youtube/v3/guides/implementation/comments)(검색 요약)
- 링크가 들어간 댓글이 `heldForReview`로 잡힐 수 있다는 점은 **미확인**입니다.

---

## 3. 미검증 프로젝트 비공개 잠금과 감사(audit)

- **현재도 유효합니다.** 2020-07-28 이후 생성된 미검증 프로젝트에서 videos.insert로 올린 영상은 private으로 제한되고, 해제하려면 감사를 통과해야 합니다.
  - 출처: [videos.insert](https://developers.google.com/youtube/v3/docs/videos/insert)(검색 요약), [upload-post](https://www.upload-post.com/youtube-api/)
  - 규정이 바뀌었다는 자료는 찾지 못했습니다.
  - API 호출 자체는 성공하고, `privacyStatus=public`을 지정해도 잠금은 풀리지 않는다고 보고됩니다(서드파티).
  - 잠긴 영상을 Studio에서 수동으로 공개로 바꿀 수 있는지는 **미확인**입니다. 커뮤니티 보고상 불가로 기억합니다.
- **해제 절차**: "YouTube API Services – Audit and Quota Extension Form"을 제출해 감사를 받습니다. 최근 12개월 안에 감사를 받은 프로젝트는 별도 양식을 씁니다.
  - 출처: [Quota and Compliance Audits](https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits?hl=en)
  - 폼 URL `https://support.google.com/youtube/contact/yt_api_form`은 기존 지식이며 접속 차단으로 **미확인**입니다.
- **준비물**: 개인정보처리방침 URL(YouTube 약관과 Google 개인정보처리방침 인용), 앱 설명, API 사용 화면이나 시연 등이 필요합니다. 기존 지식이라 **원문 재확인이 필요**합니다.
- **소요 기간**: Google 공식 SLA는 없습니다. 서드파티 추정은 "수 주 ~ 수 개월"이고 승인이 보장되지 않습니다([adaptlypost](https://adaptlypost.com/blog/youtube-api-quota-extension), [Blotato](https://www.blotato.com/blog/youtube-api-pricing)).
- **설계 반영(제안)**: `YT_API_AUDITED=false`인 동안 `/weekly-publish`는 영상, 메타데이터 JSON, 썸네일, 자막을 "Studio 업로드 패키지"로 만들고 사람이 예약 업로드합니다. 감사를 통과하면 true로 바꿔 무인 업로드를 켭니다.

---

## 4. OAuth (데스크톱 앱, 헤드리스)

### 사실
- **Testing 상태**(External): refresh token이 7일 뒤 만료됩니다. 기본 프로필 범위만 쓰는 경우는 예외인데, YouTube 범위는 해당하지 않습니다.
  - 출처: [dev.to](https://dev.to/ko-hi/googles-oauth-testing-mode-expires-refresh-tokens-in-7-days-publish-the-consent-screen-before-24hm), [nango](https://nango.dev/blog/google-oauth-invalid-grant-token-has-been-expired-or-revoked/), [unified.to](https://unified.to/blog/why_google_oauth_returns)
  - 모두 공식 문서를 인용한 2차 자료이고, 원문은 미열람입니다.
- **Production 전환**: 7일 제한이 없어집니다. 다만 Testing 때 발급한 토큰은 전환 후 다시 발급받아야 한다고 봅니다(**추정**: 전환 후에도 7일 만료를 겪었다는 보고가 있음).
- **민감 범위 검증**: 민감하거나 제한된 범위를 쓰는 앱은 보통 검증 대상입니다. 검증하지 않으면 "확인되지 않은 앱" 화면이 뜨고 **신규 사용자 100명 상한**이 걸립니다. 본인 1명만 쓰면 운영 가능합니다.
  - 출처: [Unverified apps](https://support.google.com/cloud/answer/7454865?hl=en), [Sensitive scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification)
  - YouTube 범위가 전부 sensitive로 분류된다는 것은 서드파티 주장이라 **미확인**입니다([Phyllo](https://www.getphyllo.com/post/youtube-oauth-scopes)).
- **기타 만료 사유**: 6개월 미사용, 사용자 철회, 계정·클라이언트당 refresh token 100개 초과 시 가장 오래된 토큰 폐기(2차 자료 인용).
- **google-auth-oauthlib 1.5.0**: `run_local_server(host="localhost", port=8080, open_browser=True, ...)`를 씁니다. `authorization_url()`은 `access_type="offline"`을 기본값으로 넣습니다(소스 직접 확인: [flow.py](https://github.com/googleapis/google-cloud-python/blob/main/packages/google-auth-oauthlib/google_auth_oauthlib/flow.py)).
- **최소 범위** (명세의 메서드별 scopes에서 직접 확인):
  - `youtube.force-ssl` 하나로 insert, thumbnails, playlistItems, captions, comments를 모두 처리할 수 있습니다.
  - 여기에 `yt-analytics-monetary.readonly`를 더합니다.

```python
# 1회: 로컬 PC에서 발급 (client_secret.json = OAuth 클라이언트 유형 "데스크톱 앱")
from google_auth_oauthlib.flow import InstalledAppFlow
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl",
          "https://www.googleapis.com/auth/yt-analytics-monetary.readonly"]
flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0, prompt="consent")   # prompt=consent → refresh_token 확실히 받기
print(creds.refresh_token)  # → GitHub Secret YT_REFRESH_TOKEN 에 저장
```

```python
# GitHub Actions (헤드리스)
import os
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
creds = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"],
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=os.environ["GOOGLE_CLIENT_ID"],
                    client_secret=os.environ["GOOGLE_CLIENT_SECRET"], scopes=SCOPES)
creds.refresh(Request())   # invalid_grant면 토큰 재발급 필요 → 알림 보내고 중단
```

```yaml
# .github/workflows/weekly.yml : 일요일 12:00 KST = 03:00 UTC
on:
  schedule: [{cron: "0 3 * * 0"}]
  workflow_dispatch: {}
env:
  YT_REFRESH_TOKEN: ${{ secrets.YT_REFRESH_TOKEN }}
  GOOGLE_CLIENT_ID: ${{ secrets.GOOGLE_CLIENT_ID }}
  GOOGLE_CLIENT_SECRET: ${{ secrets.GOOGLE_CLIENT_SECRET }}
```

- GitHub Secrets 한도: 저장소당 100개, 개당 48KB(원문 확인: [github/docs secrets.md](https://github.com/github/docs/blob/main/content/actions/reference/security/secrets.md)).
- Google이 refresh 시 refresh token을 교체(rotation)하지 않는다는 점은 기존 지식이라 **미확인**입니다.
- Actions의 schedule 실행이 지연될 수 있다는 점도 **미확인**입니다. 그래서 정시 공개는 `publishAt`에 맡기는 것을 권장합니다.

---

## 5. YouTube Analytics API v2

- **엔드포인트**: `GET https://youtubeanalytics.googleapis.com/v2/reports` (명세 확인)
  - 파라미터: `ids`, `startDate`, `endDate`(YYYY-MM-DD, 필수), `metrics`, `dimensions`, `filters`, `sort`, `maxResults`, `startIndex`, `currency`(기본 USD), `includeHistoricalChannelData`
- **범위** (명세): `yt-analytics.readonly`, `yt-analytics-monetary.readonly`(수익 포함, 상위 범위), `youtube`, `youtube.readonly`, `youtubepartner` 중 하나
  - 수익 지표는 monetary 범위가 필요합니다([Analytics reference](https://developer.google.com/youtube/analytics/reference), 검색 요약).
  - "youtube.readonly도 추가로 필수"라는 서드파티 주장은 **미확인**입니다.
- **지표 이름**: `views`, `estimatedMinutesWatched`, `subscribersGained`, `estimatedRevenue`는 공식 Metrics 페이지에서 확인했습니다([Metrics](https://developers.google.com/youtube/analytics/metrics)).
  - `estimatedAdRevenue`, `grossRevenue`, `cpm`은 Channel Reports에 있습니다([Channel Reports](https://developers.google.com/youtube/analytics/channel_reports)).
  - `averageViewDuration`, `averageViewPercentage`, `subscribersLost`는 기존 지식이라 **미확인**입니다.
- **트래픽 소스 차원**: `insightTrafficSourceType`. 값 예시는 `YT_SEARCH`, `SUGGESTED`, `BROWSE`, `EXT_URL`, `SHORTS` 등입니다.
  - 공식 샘플에 `dimensions=insightTrafficSourceType&metrics=views,estimatedMinutesWatched` 예가 있습니다([sample-requests](https://developers.google.com/youtube/analytics/sample-requests)).
  - 값 목록 중 `SHORTS`는 **미확인**입니다.
- **쇼츠와 롱폼 구분 차원** `creatorContentType`: 콘텐츠 소유자 보고서에서는 확인했습니다([content_owner_reports](https://developers.google.com/youtube/analytics/content_owner_reports)). 채널 보고서에서 쓸 수 있는지와 값(SHORTS/LONG_FORM 등)은 **미확인**이라 실제 호출로 검증해야 합니다.
- **기타**: 데이터 지연(보통 1~3일)과 Analytics API 할당량은 **미확인**입니다.

```text
# 동영상별 주간 성과
GET https://youtubeanalytics.googleapis.com/v2/reports?ids=channel==MINE&startDate=2026-10-04&endDate=2026-10-10
    &metrics=views,estimatedMinutesWatched,averageViewDuration,subscribersGained,subscribersLost
    &dimensions=video&sort=-views&maxResults=200
# 동영상별 트래픽 소스
...&dimensions=insightTrafficSourceType&filters=video==<VIDEO_ID>&metrics=views,estimatedMinutesWatched
# 수익 (YPP 가입 채널, monetary 범위)
...&metrics=estimatedRevenue,estimatedAdRevenue,cpm&dimensions=day
```

```python
yta = build("youtubeAnalytics", "v2", credentials=creds)
rep = yta.reports().query(ids="channel==MINE", startDate="2026-10-04", endDate="2026-10-10",
      metrics="views,estimatedMinutesWatched,subscribersGained", dimensions="video",
      sort="-views", maxResults=200).execute()
```

---

## 6. 쇼츠 분류 조건

- **조건**: 2024-10-15 이후 업로드된 정사각형 또는 세로 영상으로, **최대 3분**이면 쇼츠로 분류됩니다. API에 별도 필드는 없고 자동으로 판정됩니다([Understand three-minute YouTube Shorts](https://support.google.com/youtube/answer/15424877?hl=en), 검색 요약).
- **Content ID**: 1분을 넘는 쇼츠에 Content ID 소유권 주장(수동 포함)이 걸리면 전 세계에서 차단되고 수익화도 막힙니다. 1분 이하에는 이 차단 규칙이 적용되지 않습니다(같은 출처).
- **3분 쇼츠 업로드 경로**: 모바일 쇼츠 카메라로는 만들 수 없고, Studio나 API로 업로드합니다.

```text
렌더 규격 권장: 1080x1920 (9:16), duration ≤ 60s (Content ID 위험 회피), 하드 상한 180s
업로드: 일반 videos.insert와 동일, 별도 파라미터 없음
"#Shorts" 태그 필요 여부: 미확인 (불필요하다는 것이 일반적 설명)
```

---

## 7. 검사기 설계에 직접 걸리는 정책

- **합성 콘텐츠 공개 의무**는 사실적인 경우에만 해당합니다. 실존 인물이 하지 않은 말이나 행동을 하는 것처럼 보이게 하거나, 실제 사건·장소 영상을 변형하거나, 일어나지 않은 장면을 사실적으로 생성한 경우입니다.
  - 명백히 비현실적인 콘텐츠, 애니메이션, 제작 보조용 AI 사용은 면제입니다.
  - 출처: [Help 14328491](https://support.google.com/youtube/answer/14328491), [YouTube 블로그](https://blog.youtube/news-and-events/disclosing-ai-generated-content)
  - 판정 규칙(제안): `realistic_ai_footage or voice_clone_of_real_person` → `containsSyntheticMedia=True`
- **YPP "inauthentic content" 정책**: 2025-07-15에 기존 "repetitious" 표현을 "inauthentic"으로 바꾼 명확화 조치입니다. 대량생산되거나 반복적인 저노력 콘텐츠는 수익화 대상이 아닙니다. 공식 기준 목록은 공개되지 않았습니다.
  - 출처: [SEJ](https://www.searchenginejournal.com/youtube-targets-mass-produced-content-in-monetization-update/550337/), [Social Media Today](https://www.socialmediatoday.com/news/youtube-clarifies-monetization-update-inauthentic-repeated-content/752892/), [Music Ally](https://musically.com/2025/07/10/youtube-updates-mass-produced-and-repetitious-content-policy/)
  - YouTube 원문 정책 페이지는 **미열람**입니다.
- **검사기에 남길 기록(제안)**: 스톡 자산 ID(Pexels/Pixabay), TTS 음성 이름, 대본 지문(n-gram/임베딩), 썸네일 pHash, BGM ID. 최근 N주 안에 재사용되면 차단합니다.

---

## 8. 한국어 TTS 비교

| 항목 | Google Cloud TTS (Chirp 3 HD) | Azure Speech (Neural) | ElevenLabs | OpenAI TTS | edge-tts (비공식) |
|---|---|---|---|---|---|
| ko-KR | 지원. 2025-03 추가 ([release notes](https://docs.cloud.google.com/text-to-speech/docs/release-notes), 검색 요약) | 지원(예: `ko-KR-SunHiNeural`, `ko-KR-InJoonNeural`, 이름은 기존 지식) | 다국어 모델로 지원(**미확인**) | Whisper 기반 언어 지원(**미확인**) | Edge 음성 목록에 ko-KR 포함 |
| 가격 | **월 100만 자 무료**, 이후 $30/100만 자 (pricing 원문 2026-10-10) | 무료 F0 월 50만 자, 유료 약 $15~16/100만 자(정확한 값 **미확인**) | Free는 상업 불가에 출처 표기 의무. Starter $5~6부터 상업 가능, Creator $22/121k 크레딧(서드파티) | gpt-4o-mini-tts 입력 $0.60/1M 토큰, 출력 $12/1M 토큰, 분당 약 $0.015 추정 ([모델 페이지](https://developers.openai.com/api/docs/models/gpt-4o-mini-tts)) | 무료 |
| 상업·수익화 | Cloud 유료 서비스라 가능하다고 봄(약관 원문 **미확인**) | Azure 구독이면 가능하다고 봄(**미확인**) | 유료 플랜 이상만 상업 가능 ([bigvu](https://bigvu.tv/blog/elevenlabs-pricing-2026-plans-credits-commercial-rights-api-costs/), [cloudzero](https://www.cloudzero.com/blog/elevenlabs-pricing/)) | 최종 사용자에게 "AI 음성"임을 명확히 알려야 함 ([OpenAI 포럼](https://community.openai.com/t/where-is-the-usage-policy-for-ai-voiceovers/679737), [Rest of World](https://restofworld.org/about/ai-narrations/)) | Azure 구독 없이 상업 이용 시 약관 위반 소지(MS 답변, [Microsoft Q&A](https://learn.microsoft.com/en-us/answers/questions/2088770/are-opensource-edge-tts-free-for-commercial-use)) |
| Python | `google-cloud-texttospeech` 2.38.0 | `azure-cognitiveservices-speech` 1.52.0 | `elevenlabs` 2.71.0 | `openai` 3.28.0 | `edge-tts` 7.2.8 (2026-03-22). 사용자 정의 SSML 제거됨([README](https://github.com/rany2/edge-tts)) |

**같은 Google API의 참고 단가** (pricing 원문 2026-10-10)
- Gemini 2.5 Flash TTS: 입력 $0.50/1M, 출력 $10/1M 토큰. 오디오 1초에 25토큰입니다.
- WaveNet: 월 400만 자 무료, $4/1M
- Neural2: 월 100만 자 무료, $16/1M
- Standard: 월 400만 자 무료, $4/1M

**월 사용량 추정**
- 한국어 내레이션을 분당 약 350자로 잡았습니다(**추정**).
- 주간: 쇼츠 14편×1분 약 4,900자 + 롱폼 27분 약 9,500자 ≈ 14,400자
- 월간 약 6.3만 자이므로, 5번 다시 녹음해도 Chirp 3 HD 무료 100만 자 안에 들어갑니다.

**추천**
- 1순위는 **Google Cloud TTS Chirp 3 HD**입니다. YouTube API와 같은 GCP 프로젝트·과금 체계를 쓰고, 운영량 전체가 무료 구간에 들어갑니다.
- 무료 폴백은 **같은 API의 WaveNet/Neural2 ko-KR**(공급사 장애가 아닐 때)입니다. 공급사 장애 대비로는 **Azure F0**(월 50만 자 무료, 공식 상업 서비스)를 둡니다.
- edge-tts는 개발 중 미리듣기 용도로만 쓰고 발행물에는 금지합니다(제안).

```python
# Google Cloud TTS (명세 필드: input.text|ssml|markup, voice.languageCode|name|modelName, audioConfig.audioEncoding|speakingRate|pitch|volumeGainDb|sampleRateHertz)
from google.cloud import texttospeech as tts
c = tts.TextToSpeechClient()
print([v.name for v in c.list_voices(language_code="ko-KR").voices if "Chirp3-HD" in v.name])  # 실제 이름 확인
r = c.synthesize_speech(
    input=tts.SynthesisInput(text="안녕하세요..."),           # Chirp 3 HD 전용 'markup' 필드도 있음
    voice=tts.VoiceSelectionParams(language_code="ko-KR", name="ko-KR-Chirp3-HD-<Voice>"),
    audio_config=tts.AudioConfig(audio_encoding=tts.AudioEncoding.LINEAR16, speaking_rate=1.0))
open("n.wav","wb").write(r.audio_content)
# REST: POST https://texttospeech.googleapis.com/v1/text:synthesize
# 긴 오디오: POST v1/{parent=projects/*/locations/*}:synthesizeLongAudio
# 요청당 입력 한도(5,000바이트, 한글은 약 1,600자)는 미확인 → 문장 단위로 나눠 합성할 것
```

```python
# Azure 폴백
import azure.cognitiveservices.speech as sdk
cfg = sdk.SpeechConfig(subscription=KEY, region="koreacentral")
cfg.speech_synthesis_voice_name = "ko-KR-SunHiNeural"
cfg.set_speech_synthesis_output_format(sdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm)
sdk.SpeechSynthesizer(speech_config=cfg, audio_config=sdk.audio.AudioOutputConfig(filename="n.wav")).speak_text_async(text).get()
```

```python
# ElevenLabs (SDK 2.x 시그니처는 미확인. 설치 후 확인할 것)
from elevenlabs.client import ElevenLabs
audio = ElevenLabs(api_key=K).text_to_speech.convert(voice_id=V, model_id="eleven_multilingual_v2", text=t, output_format="mp3_44100_128")
# OpenAI (openai 3.x 시그니처는 미확인)
client.audio.speech.create(model="gpt-4o-mini-tts", voice="<voice>", input=t, instructions="차분한 안내 톤", response_format="wav")
```

---

## 9. 무료 상업용 스톡 API

**Pexels**
- 한도: 기본 시간당 200회, 월 20,000회입니다. 한도 상향은 **현재 중단** 상태입니다. 성공 응답에만 한도 헤더가 붙습니다. 24시간 캐시를 권장합니다.
  - 출처: [API docs](https://www.pexels.com/api/documentation/), [Help: unlimited requests](https://help.pexels.com/hc/en-us/articles/900005852323-How-do-I-get-unlimited-requests)
- 라이선스: 상업 이용 가능하고 표기는 필수가 아닙니다. 다만 API 이용 지침상 Pexels와 작가 표기를 요구합니다(한도 상향 조건에 명시).
- 금지: 수정하지 않은 원본 판매, 식별 가능한 인물을 나쁘게 묘사하거나 보증하는 것처럼 쓰는 것, 상표에 사용하는 것. 상업적 사용에서 인물 초상권 확인 책임은 사용자에게 있습니다.
  - 출처: [Terms](https://pexels.com/terms-of-service), [license](https://www.pexels.com/license/)

**Pixabay**
- 한도: 키당 60초에 100회이고, 초과하면 429입니다. 24시간 캐시가 **의무**입니다. 이미지 영구 핫링크는 금지이므로 내려받아 써야 합니다.
  - 출처: [API docs](https://pixabay.com/api/docs/). 검색 인덱스상 해당 페이지가 오래돼 원문 재확인을 권장합니다.
- 라이선스: Pixabay Content License로 상업 이용 가능, 표기 불필요(서드파티 요약). 세부 금지 조항은 **미확인**입니다.

```text
Pexels  GET https://api.pexels.com/videos/search?query=<q>&orientation=portrait&size=medium&per_page=40&page=1
        GET https://api.pexels.com/v1/search?query=<q>&orientation=portrait&locale=ko-KR
        Header: Authorization: <PEXELS_API_KEY>
        응답: videos[].id, .url, .user.name, .video_files[].{quality,width,height,link}
        한도 헤더: X-Ratelimit-Limit / X-Ratelimit-Remaining / X-Ratelimit-Reset (이름 미확인)
Pixabay GET https://pixabay.com/api/?key=<KEY>&q=<q>&lang=ko&image_type=photo&orientation=vertical&safesearch=true&per_page=50
        GET https://pixabay.com/api/videos/?key=<KEY>&q=<q>&video_type=film&per_page=50
        응답: hits[].id, .pageURL, .user, .videos.{large,medium,small,tiny}.{url,width,height}
→ 검사기: (provider, asset_id)를 사용 이력 DB에 기록하고 N주 안 재사용 금지
```

---

## 10. 미확인 목록 (구현 전 직접 확인할 것)

1. 공식 페이지 원문 대조: `videos.insert` 1단위/100회 수치, `thumbnails.set` 50, `comments.insert` 50 (determine_quota_cost)
2. 감사 폼 URL, 준비 서류, 잠긴 영상을 수동으로 공개할 수 있는지
3. OAuth 7일 규정 원문, Production 전환 후 기존 토큰 재발급 필요 여부, YouTube 범위의 sensitive/restricted 분류
4. Analytics: `creatorContentType`이 채널 보고서에서 되는지와 그 값, `subscribersLost`·`averageViewDuration`, 데이터 지연, 할당량
5. 제목·설명·태그 길이 제한, 썸네일 2MB 제한, 쇼츠 맞춤 썸네일 표시 여부
6. Chirp 3 HD ko-KR 실제 음성 이름(`list_voices`로 확인), 요청당 입력 한도
7. Azure 현재 단가, ElevenLabs·OpenAI의 한국어 품질과 약관 원문, Pixabay 라이선스 세부 조항

조사 중 받은 파일은 `/tmp/claude-0/-home-user-sjcu-python/51908c08-0703-53d5-87fc-b6b14754da05/scratchpad/dl/`에 있습니다(yt_v3.json, yta_v2.json, tts_v1.json, tts_pricing.html, flow.py, http.py, upload_video.py, secrets.md, edge_readme.md).