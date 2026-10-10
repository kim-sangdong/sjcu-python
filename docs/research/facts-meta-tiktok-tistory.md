# Meta·TikTok·티스토리 구현 사실 확인 (기준일 2026-10-10)

## 0. 조사 한계: 먼저 읽을 것

공식 문서 페이지는 이번 실행에서 한 번도 읽지 못했다. 그래서 아래 내용 대부분은 기억(2026년 6월 이전 지식)에 기반한다.

- **WebSearch**: 이번 턴에서 모든 에이전트가 함께 쓰는 한도 200회가 이미 소진됐다. 검색을 0회 실행했다.
- **WebFetch**: `getaddrinfo ENOTFOUND` 오류가 났다. 대상은 developers.facebook.com, developers.tiktok.com, tistory.github.io다.
- **curl(에이전트 프록시 경유)**: `CONNECT 403`으로 거부됐다. 조직 egress 정책 때문이다. 대상은 developers.facebook.com, developers.tiktok.com, tistory.github.io, notice.tistory.com이다. 우회하지 않았다.
- **대신 직접 확인한 1차 자료는 두 가지다.**
  - (a) Meta 공식 Python Business SDK `facebook-business` 26.0.2. Graph API v26.0을 쓰고, PyPI 업로드일은 2026-09-21이다. 자동 생성된 소스 코드라서 **파라미터·필드 이름이 있는지**만 확인할 수 있다. 한도·절차·규격은 SDK에 없다.
    - https://pypi.org/project/facebook-business/26.0.2/
    - https://github.com/facebook/facebook-python-business-sdk
  - (b) 프로젝트 `.venv`에 들어 있는 Google API discovery 문서. YouTube Data v3는 revision 20260924, Blogger v3는 revision 20260707이다.
- **표기 규칙**
  - `[확인-SDK]`: Meta SDK v26 소스에서 이름이 있는지 확인함
  - `[확인-Discovery]`: Google discovery 문서에서 확인함
  - `[기억·미확인]`: 2026-06 이전 지식이며 현재 문서로 확인하지 못함
  - `[추측]`: 근거 없는 추정
  - `[설계 판단]`: 권고 사항
- **이어서 조사하려면**: 후속 메시지로 검색을 계속하거나, `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION`을 올리거나, 위 4개 호스트를 egress 허용 목록에 넣어야 한다. 7장에 구현 전에 꼭 확인할 URL을 정리했다.

---

## 1. Instagram 콘텐츠 게시 API

### 1.1 Instagram Login과 Facebook Login의 차이

| 항목 | Instagram API with **Instagram Login** | Instagram API with **Facebook Login** |
|---|---|---|
| 호스트 | `graph.instagram.com` | `graph.facebook.com` |
| 계정 | 인스타 프로페셔널 계정(비즈니스·크리에이터). FB 페이지 불필요 | 프로페셔널 계정과 **연결된 Facebook 페이지** 필요 |
| 권한 | `instagram_business_basic`, `instagram_business_content_publish`, `instagram_business_manage_insights`, `instagram_business_manage_comments`(+`_manage_messages`) | `instagram_basic`, `instagram_content_publish`, `instagram_manage_insights`, `instagram_manage_comments`, `pages_show_list`, `pages_read_engagement`(+`business_management`) |
| 토큰 | 인스타 사용자 토큰. 단기 1시간, 장기 60일, 갱신 가능 | FB 사용자 토큰에서 페이지 토큰을 받거나, 시스템 사용자 토큰 사용 |
| IG 사용자 ID | `GET /me?fields=user_id,username` | `GET /{page-id}?fields=instagram_business_account` |

- 위 표 전체가 `[기억·미확인]`이다.
- 옛 스코프 이름(`business_basic` 등)은 2025-01-27에 폐기된 것으로 기억한다. `[기억·미확인]`
- `[확인-SDK]` 인스타 전용 API의 토큰 교환·갱신 응답 객체가 SDK에 있다.
  - `IGAccessTokenForIGOnlyAPI`: `access_token`, `expires_in`, `token_type`
  - `IGRefreshAccessTokenForIGOnlyAPI`: 위 필드 + `permissions`
- `[기억·미확인]` 앱을 **개발 모드로 두고 본인 계정에 앱 역할(관리자·개발자·테스터)을 주면** App Review(Advanced Access) 없이 본인 계정 데이터에 접근할 수 있다.
  - Instagram Login 경로에서는 대시보드에서 "Instagram 테스터"를 추가하고 인스타에서 초대를 수락해야 한다.
  - 1인 운영이라면 이것이 핵심이다. 반드시 확인할 것.

### 1.2 릴스 게시 절차

```http
# 1) 컨테이너 생성
POST https://graph.facebook.com/v26.0/{ig-user-id}/media      # IG Login이면 graph.instagram.com
  media_type=REELS
  video_url=https://<공개 URL>/w41_s03.mp4    # 또는 upload_type=resumable (1.3 참고)
  caption=...
  share_to_feed=true            # 프로필 그리드에도 노출
  cover_url=https://.../cover.jpg   # 또는 thumb_offset=<ms>
  audio_name=...                # 선택
  collaborators=["username"]    # 선택
  location_id=... / user_tags=[...]
  is_ai_generated=true|false    # 아래 설명 참고
  trial_params={...}            # 체험 릴스. 구조 미확인
  access_token=...
→ {"id": "<CONTAINER_ID>"}

# 2) 상태 폴링 (기억: 1분 간격, 최대 5분 권장)
GET /v26.0/{CONTAINER_ID}?fields=status_code,status,video_status,copyright_check_status
  status_code ∈ IN_PROGRESS | FINISHED | ERROR | EXPIRED | PUBLISHED

# 3) 게시
POST /v26.0/{ig-user-id}/media_publish?creation_id={CONTAINER_ID}
→ {"id": "<IG_MEDIA_ID>"}

GET /v26.0/{IG_MEDIA_ID}?fields=permalink,shortcode,media_product_type,timestamp
```

- `[확인-SDK]` `POST /{ig-user-id}/media`가 받는 파라미터 전체 목록:
  - `alt_text`, `audio_configuration`, `audio_name`, `branded_content_sponsor_ids`, `caption`
  - `children`, `collaborators`, `cover_url`, `image_url`, **`is_ai_generated`**
  - `is_carousel_item`, `is_comment_prompt_used`, `is_paid_partnership`, `is_poll_duration_extended`
  - `location_id`, `media_type`, `poll_attachment`, `product_tags`, `share_to_feed`
  - `thumb_offset`, `trial_params`, **`upload_type`**, `user_tags`, `video_url`
- `[확인-SDK]` `POST /media_publish`는 `creation_id`를 받는다.
- `[확인-SDK]` 컨테이너 필드는 `status`, `status_code`, `video_status`, `copyright_check_status`다.
- `[기억·미확인]` status_code 값 목록, 컨테이너가 24시간 뒤 만료된다는 점, `media_type` 값(`REELS`, `CAROUSEL`, `STORIES`, 캐러셀 항목의 `VIDEO`)은 기억이다. SDK에 enum이 정의돼 있지 않아 확인하지 못했다.
- `is_ai_generated`, `trial_params`는 파라미터가 있다는 것만 확인했다. 의미는 `[미확인]`이다.
- `[기억·미확인]` 릴스 규격:
  - 컨테이너는 MP4/MOV이고 moov atom이 앞에 와야 한다.
  - 영상은 H.264 또는 HEVC, 프로그레시브 스캔, closed GOP, 4:2:0, 23~60fps, 가로 최대 1920px, 9:16 권장, 최대 25Mbps.
  - 오디오는 AAC, 최대 48kHz, 128kbps.
  - 길이 3초~15분, 파일 최대 300MB.
- `[기억·미확인]` 캡션은 최대 2,200자, 해시태그 30개, @태그 20개까지다.
- `[기억·미확인]` **릴스·피드 캡션의 URL은 클릭되지 않는다.** UTM을 걸 곳은 프로필 링크(최대 5개)와 스토리 링크 스티커뿐이다.

### 1.3 video_url 공개 URL 필요 여부와 resumable 업로드

- `[기억·미확인]` `video_url`은 Meta 서버가 직접 내려받는다. 그래서 **누구나 접근할 수 있는 HTTPS URL**이어야 한다. 만료 시간이 충분한 서명 URL(R2·S3 presigned)도 가능할 것으로 본다 `[추측]`.
- `[확인-SDK]` `upload_type` 파라미터와 `IGResumableVideoUploadStatus` 객체(`uploading_phase`, `processing_phase`)가 있다. 즉 resumable 업로드 경로가 존재한다.
- `[기억·미확인]` 업로드 호스트와 헤더 이름, 그리고 Instagram Login 경로에서도 이 방식이 되는지는 확인하지 못했다.

```http
POST https://graph.facebook.com/v26.0/{ig-user-id}/media
  media_type=REELS&upload_type=resumable&caption=...&share_to_feed=true
→ {"id":"<CONTAINER_ID>","uri":"https://rupload.facebook.com/ig-api-upload/v26.0/<CONTAINER_ID>"}

POST https://rupload.facebook.com/ig-api-upload/v26.0/<CONTAINER_ID>
  Authorization: OAuth <ACCESS_TOKEN>
  offset: 0
  file_size: <BYTES>
  <binary body>                     # 로컬 파일 직접 업로드 → 공개 호스팅 불필요
  # 또는 헤더 file_url: https://...  (호스팅 파일)
→ 이후 2)~3) 동일
```

`[설계 판단]` 1인 운영이면 로컬 파일 resumable 업로드를 쓴다. CDN을 운영할 필요가 없어진다.

### 1.4 캐러셀(카드뉴스)

```http
# 각 장 (항목 컨테이너)
POST /v26.0/{ig-user-id}/media  image_url=https://.../01.jpg  is_carousel_item=true  alt_text=...
# 캐러셀 컨테이너
POST /v26.0/{ig-user-id}/media  media_type=CAROUSEL  children=<id1>,<id2>,...  caption=...
# 게시
POST /v26.0/{ig-user-id}/media_publish  creation_id=<CAROUSEL_CONTAINER_ID>
```

- `[확인-SDK]` `children`, `is_carousel_item`, `alt_text`, `image_url` 파라미터가 있다.
- `[기억·미확인]` API로는 **최대 10장**이다. 인스타 앱은 2024-08에 20장으로 늘렸는데, API에 반영됐는지는 **미확인**이다.
- `[기억·미확인]` 이미지 규격:
  - 형식은 **JPEG만** 된다. MPO/JPS 같은 확장 JPEG는 안 된다.
  - 최대 8MB, 비율 4:5~1.91:1, 너비 320~1440px, sRGB.
  - 모든 장이 첫 장 비율로 잘린다.
- `[설계 판단]` 카드뉴스는 1080×1350(4:5) JPEG, sRGB로 만든다.

### 1.5 24시간 게시 한도

```http
GET /v26.0/{ig-user-id}/content_publishing_limit?fields=quota_usage,config
→ quota_usage, config.quota_total, config.quota_duration
```

- `[확인-SDK]` 엔드포인트, `quota_usage`·`config` 필드, `since` 파라미터가 있다.
- `[기억·미확인]` 한도는 **이동 24시간 동안 API 게시물 100건**이다(과거 25건에서 50건, 다시 100건으로 늘었다). 캐러셀은 1건으로 센다.
- 주 14편 쇼츠와 카드뉴스를 합쳐도 여유가 있다.

### 1.6 장기 액세스 토큰 갱신

```http
# Instagram Login [기억·미확인]
GET  https://www.instagram.com/oauth/authorize?client_id=..&redirect_uri=..&response_type=code&scope=instagram_business_basic,instagram_business_content_publish,instagram_business_manage_insights,instagram_business_manage_comments
POST https://api.instagram.com/oauth/access_token   (client_id, client_secret, grant_type=authorization_code, redirect_uri, code)  → 단기(1h)
GET  https://graph.instagram.com/access_token?grant_type=ig_exchange_token&client_secret=..&access_token=<단기>      → 장기(60일)
GET  https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token=<장기>             → 새 60일 (발급 24h 이후, 만료 전에만)

# Facebook Login [기억·미확인]
GET /v26.0/oauth/access_token?grant_type=fb_exchange_token&client_id=..&client_secret=..&fb_exchange_token=<단기 사용자 토큰>  → 장기 사용자 토큰(~60일)
GET /v26.0/me/accounts?fields=id,name,access_token,tasks,instagram_business_account   → 장기 사용자 토큰으로 받은 페이지 토큰은 만료 없음
#   또는 비즈니스 포트폴리오의 시스템 사용자 토큰 (만료 없음 선택 가능)
GET /v26.0/debug_token?input_token=<token>   → expires_at, scopes 점검
```

### 1.7 인사이트 API 지표 이름

```http
# 미디어(릴스)
GET /v26.0/{ig-media-id}/insights?metric=views,reach,likes,comments,shares,saved,total_interactions,ig_reels_avg_watch_time,ig_reels_video_view_total_time,reels_skip_rate
# 계정
GET /v26.0/{ig-user-id}/insights?metric=reach,views,follows,profile_visits,link_clicks,total_interactions&period=day&metric_type=total_value&since=<unix>&until=<unix>
# 미디어 필드로 바로 읽기
GET /v26.0/{ig-media-id}?fields=like_count,comments_count,view_count,shares_count,saved_count,reposts_count,total_views_count,is_ai_generated
```

- `[확인-SDK]` `InstagramInsightsResult.Metric` 값 전체:
  - `comments`, `crossposted_views`, `facebook_views`, `follows`
  - `ig_reels_avg_watch_time`, `ig_reels_video_view_total_time`, `impressions`, `likes`
  - `link_clicks`, `navigation`, `profile_activity`, `profile_visits`, `reach`
  - `reels_skip_rate`, `replies`, `reposts`, `saved`, `shares`
  - `total_comments`, `total_interactions`, `total_likes`, `total_views`, `views`
- `[확인-SDK]` 그 밖의 파라미터 값:
  - breakdown: `action_type`, `follow_type`, `story_navigation_action_type`, `surface_type`
  - period: `day`, `week`, `days_28`, `month`, `lifetime`, `total_over_range`
  - metric_type: `default`, `time_series`, `total_value`
  - timeframe: `last_14_days`, `last_30_days`, `last_90_days`, `prev_month`, `this_month`, `this_week`
- `[확인-SDK]` 미디어 필드로 `view_count`, `shares_count`, `saved_count`, `reposts_count`, `total_views_count`, `is_ai_generated` 등이 있다.
- 주의 1: SDK enum은 미디어용과 계정용이 섞인 전체 목록이다. **어떤 지표가 어떤 객체·미디어 유형에 유효한지는 미확인**이다.
- 주의 2 `[기억·미확인]`:
  - `impressions`, `plays`, `clips_replays_count`, `ig_reels_aggregated_all_plays_count`는 v22(2025-04)에서 폐기되고 `views`로 대체됐다.
  - 문서에 있던 `follower_count`, `accounts_engaged`, `profile_links_taps`는 SDK enum에 없다. 현재 유효한지 확인이 필요하다.
  - 데이터는 최대 48시간 지연된다.

---

## 2. Facebook 페이지 릴스 게시 (`/{page-id}/video_reels`)

```http
# 0) 페이지 토큰
GET /v26.0/me/accounts?fields=id,name,access_token,tasks

# 1) 업로드 세션 시작
POST https://graph.facebook.com/v26.0/{page-id}/video_reels
  upload_phase=start  access_token=<PAGE_TOKEN>
→ {"video_id":"...","upload_url":"https://rupload.facebook.com/video-upload/v26.0/<video_id>"}

# 2) 바이너리 업로드
POST https://rupload.facebook.com/video-upload/v26.0/{video_id}
  Authorization: OAuth <PAGE_TOKEN>
  offset: 0
  file_size: <BYTES>
  <binary body>                    # 또는 헤더 file_url: https://... (호스팅 파일)

# 3) 처리 상태
GET /v26.0/{video_id}?fields=status
  → status.video_status / uploading_phase / processing_phase / publishing_phase

# 4) 게시
POST /v26.0/{page-id}/video_reels
  upload_phase=finish  video_id=<id>
  video_state=PUBLISHED          # DRAFT | PUBLISHED | SCHEDULED(+scheduled_publish_time)
  description="..."  title="..."  is_ai_generated=false  place=<page-id>
→ {"success": true}

# 5) 목록·인사이트
GET /v26.0/{page-id}/video_reels
GET /v26.0/{video_id}/video_insights?metric=...&period=lifetime
```

- `[확인-SDK]` `Page.create_video_reel`이 받는 파라미터:
  - `description`, `feed_targeting`, **`is_ai_generated`**, `place`, `scheduled_publish_time`
  - `targeting`, `title`, `upload_phase`, `video_id`, `video_state`
- `[확인-SDK]` enum 값: `upload_phase`는 `start|transfer|finish|cancel`, `video_state`는 `DRAFT|PUBLISHED|SCHEDULED`.
- `[확인-SDK]` `GET /video_reels` 엣지가 있다. `/video_insights` 엣지는 `metric`, `period`, `since`, `until`을 받는다.
- `[기억·미확인]` 응답 필드(`video_id`, `upload_url`), rupload 호스트와 헤더 이름, 릴스 흐름에서는 start/finish만 쓴다는 점은 기억이다.
- `[기억·미확인]` 권한과 토큰:
  - 권한은 `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`이고, 비즈니스 소유 페이지면 `business_management`를 더한다.
  - 페이지에서 `CREATE_CONTENT` 작업 권한이 있어야 한다.
  - **페이지 액세스 토큰**이 필요하다.
- `[기억·미확인]` 규격과 한도:
  - MP4, 9:16, 최소 540×960(1080×1920 권장), 24~60fps, **3~90초**.
  - **페이지당 24시간에 API 릴스 30건**까지.
  - 앱에서는 더 긴 릴스가 가능해졌는데, API 상한이 바뀌었는지는 미확인이다. 쇼츠가 60초 이하면 안전하다.
- `[기억·미확인]` 인사이트 지표: `blue_reels_play_count`, `fb_reels_total_plays`, `fb_reels_replay_count`, `post_impressions_unique`, `post_video_avg_time_watched`, `post_video_view_time`, `post_video_followers`, `post_video_social_actions`.
- `[설계 판단]` FB 페이지 릴스가 어차피 필요하다. 그래서 **Facebook Login 경로 하나로 IG와 FB를 묶고 시스템 사용자 토큰을 쓰는 방식**을 권장한다. 토큰을 하나만 관리하면 되고 만료도 없다.

---

## 3. TikTok Content Posting API

### 3.1 OAuth와 스코프 `[기억·미확인]`

```http
GET  https://www.tiktok.com/v2/auth/authorize/?client_key=..&scope=user.info.basic,video.publish,video.upload,video.list&response_type=code&redirect_uri=..&state=..
POST https://open.tiktokapis.com/v2/oauth/token/   (x-www-form-urlencoded)
     client_key, client_secret, code, grant_type=authorization_code, redirect_uri
→ access_token(24h), refresh_token(365일), open_id, scope, expires_in, refresh_expires_in
POST https://open.tiktokapis.com/v2/oauth/token/   grant_type=refresh_token&refresh_token=..&client_key=..&client_secret=..
```

- 스코프별 용도:
  - `video.publish`: Direct Post
  - `video.upload`: 받은편지함·초안 업로드
  - `video.list`: Display API 영상 목록

### 3.2 Direct Post 절차 `[기억·미확인]`

```http
# 1) 크리에이터 정보 (UX에 반드시 표시)
POST https://open.tiktokapis.com/v2/post/publish/creator_info/query/
  Authorization: Bearer <token>
  Content-Type: application/json; charset=UTF-8
→ data.creator_avatar_url, creator_username, creator_nickname,
  privacy_level_options[], comment_disabled, duet_disabled, stitch_disabled,
  max_video_post_duration_sec

# 2) 초기화
POST https://open.tiktokapis.com/v2/post/publish/video/init/
{
  "post_info": {
    "title": "캡션 #태그",                // 최대 2200 UTF-16
    "privacy_level": "PUBLIC_TO_EVERYONE", // privacy_level_options 중 하나:
                                           // PUBLIC_TO_EVERYONE | MUTUAL_FOLLOW_FRIENDS | FOLLOWER_OF_CREATOR | SELF_ONLY
    "disable_duet": false,
    "disable_comment": false,
    "disable_stitch": false,
    "video_cover_timestamp_ms": 1000,
    "brand_content_toggle": false,
    "brand_organic_toggle": false,
    "is_aigc": true
  },
  "source_info": {
    "source": "FILE_UPLOAD",
    "video_size": 50000123,
    "chunk_size": 10000000,
    "total_chunk_count": 5
  }
}
→ data.publish_id, data.upload_url   (upload_url 유효 1시간)
# PULL_FROM_URL: "source_info": {"source":"PULL_FROM_URL","video_url":"https://<검증된 도메인>/v.mp4"}

# 3) 업로드 (청크를 순서대로)
PUT {upload_url}
  Content-Type: video/mp4
  Content-Length: <이 청크 바이트>
  Content-Range: bytes 0-9999999/50000123
→ 206(진행 중) / 201(전체 완료)

# 4) 상태
POST https://open.tiktokapis.com/v2/post/publish/status/fetch/
{"publish_id": "<publish_id>"}
→ data.status ∈ PROCESSING_UPLOAD | PROCESSING_DOWNLOAD | SEND_TO_USER_INBOX | PUBLISH_COMPLETE | FAILED
  data.fail_reason, data.publicaly_available_post_id[]  (API 철자 그대로), uploaded_bytes, downloaded_bytes
```

- **FILE_UPLOAD 청크 규칙** `[기억·미확인]`:
  - 청크 하나는 5MB 이상 64MB 이하다. 마지막 청크는 나머지를 흡수해서 최대 128MB까지 된다.
  - 5MB 미만 파일은 한 번에 올린다(`chunk_size=video_size`, `total_chunk_count=1`).
  - `total_chunk_count = floor(video_size / chunk_size)`이고, 최대 1000개다.
  - 청크는 반드시 순서대로 올린다.
- **영상 규격** `[기억·미확인]`: MP4(권장)/WebM/MOV, H.264(권장)/H.265/VP8/VP9, 23~60fps, 해상도 360~4096px, 최대 4GB, 길이는 `max_video_post_duration_sec` 이하.
- **호출 한도** `[기억·미확인]`:
  - 토큰당 creator_info 20회/분, video/init 6회/분, status/fetch 30회/분.
  - Direct Post는 크리에이터당 하루 약 15건이고, 모든 API 클라이언트가 이 한도를 나눠 쓴다. 정확한 수치는 **미확인**이다.
- **주요 오류 코드** `[기억·미확인]`: `spam_risk_too_many_posts`, `spam_risk_user_banned_from_posting`, `reached_active_user_cap`, `unaudited_client_can_only_post_to_private_accounts`, `url_ownership_unverified`, `privacy_level_option_mismatch`.

### 3.3 PULL_FROM_URL 도메인 검증 `[기억·미확인]`

- 개발자 포털에서 앱의 "Manage URL properties"를 연다.
  - **도메인** 방식: DNS TXT 레코드(`tiktok-developers-site-verification=...`)를 등록한다.
  - **URL 접두사** 방식: 서명 파일을 해당 경로에 올린다.
- HTTPS만 되고 리다이렉트는 안 된다. 다운로드는 1시간 안에 끝나야 한다.
- 검증하지 않은 URL을 쓰면 `url_ownership_unverified` 오류가 난다.
- `[설계 판단]` 영상은 FILE_UPLOAD를 쓴다. 도메인이 필요 없다.

### 3.4 사진(캐러셀) 게시 `[기억·미확인]`

```http
POST https://open.tiktokapis.com/v2/post/publish/content/init/
{
  "post_info": {"title":"..", "description":"..", "privacy_level":"..",
                "disable_comment":false, "auto_add_music":true,
                "brand_content_toggle":false, "brand_organic_toggle":false},
  "source_info": {"source":"PULL_FROM_URL", "photo_cover_index":0,
                  "photo_images":["https://<검증 도메인>/1.jpg", "..."]},
  "post_mode": "DIRECT_POST",     // 또는 MEDIA_UPLOAD (앱 받은편지함)
  "media_type": "PHOTO"
}
```

- 사진은 **PULL_FROM_URL만** 되는 것으로 기억한다. 그래서 도메인 검증이 필수다.
- 최대 35장, JPG/JPEG/WEBP, 장당 20MB 이하, title 90자, description 4000자.
- 사진 게시에 `is_aigc`가 지원되는지는 미확인이다.

### 3.5 미감사(unaudited) 클라이언트 제약과 감사 `[기억·미확인]`: 이 프로젝트의 최대 리스크

- 미감사 클라이언트가 올린 게시물은 **전부 SELF_ONLY(나만 보기)로 강제**된다.
- 24시간에 게시할 수 있는 사용자는 최대 5명이다.
- 오류 코드 `unaudited_client_can_only_post_to_private_accounts`가 있다고 기억한다. 맞다면 **게시하는 순간 계정 자체가 비공개여야** 한다.
  - 사실이면 미감사 상태로 공개 운영하는 것은 사실상 불가능하다.
- 감사 절차:
  - 포털에서 Content Posting API의 Direct Post를 켜고 감사를 신청한다.
  - 전체 UX 데모 영상과 3.6 가이드라인 준수 증빙을 낸다.
  - 앱 심사 자체에도 개인정보처리방침·약관이 있는 웹사이트가 필요하다.
- **소요 기간은 미확인**이다. 수일에서 수주로 기억한다.
- **강한 주의**: 감사 가이드라인에 "본인이나 팀이 관리하는 계정에 올리는 유틸리티·내부용 도구"는 승인하지 않는다는 취지의 문구가 있다고 기억한다. **미확인**이지만, 1인이 본인 계정만 자동화하는 이 시스템에는 치명적이다. 감사를 신청하기 전에 원문을 꼭 확인할 것.
- 대안: 받은편지함 업로드. 스코프는 `video.upload`이고 감사가 필요한지는 미확인이다.

```http
POST https://open.tiktokapis.com/v2/post/publish/inbox/video/init/
{"source_info": {"source":"FILE_UPLOAD","video_size":..,"chunk_size":..,"total_chunk_count":..}}
→ publish_id, upload_url → (업로드) → status/fetch = SEND_TO_USER_INBOX
→ 운영자가 TikTok 앱 알림에서 편집·게시 (AI 라벨도 앱에서 직접 켤 수 있음)
```

### 3.6 필수 UX 요구사항(Content Sharing Guidelines) `[기억·미확인]`

- creator_info에서 받은 **닉네임·아바타를 표시**하고 어느 계정에 올리는지 보여준다.
- `max_video_post_duration_sec`를 넘는 영상은 막는다.
- **공개 범위는 기본값 없이** `privacy_level_options` 중에서 사용자가 직접 고르게 한다.
- 댓글·듀엣·스티치 토글은 기본으로 꺼 둔다. 크리에이터 설정에서 막혀 있으면 비활성화로 표시한다.
- 상업 콘텐츠 공개 토글은 기본 OFF다.
  - "Your brand"를 고르면 "Promotional content" 라벨, "Branded content"를 고르면 "Paid partnership" 라벨이 붙는다.
  - 브랜디드 콘텐츠는 SELF_ONLY로 올릴 수 없다.
- 게시 전에 동의 문구를 보여준다: "By posting, you agree to TikTok's Music Usage Confirmation". 브랜디드면 Branded Content Policy도 함께 적는다.
- 미리보기를 보여주고, 캡션은 사용자가 끝까지 편집할 수 있어야 한다. 앱 워터마크나 홍보 문구를 덧붙이면 안 된다.
- 업로드 전에 명시적 동의를 받고, 처리에 몇 분 걸린다는 점을 안내한다.

### 3.7 AI 생성 표시 필드

- `post_info.is_aigc` (bool) `[기억·미확인]`

### 3.8 통계 조회(Display API) `[기억·미확인]`

```http
GET  https://open.tiktokapis.com/v2/user/info/?fields=open_id,union_id,avatar_url,display_name,bio_description,profile_deep_link,is_verified,follower_count,following_count,likes_count,video_count
     # 스코프 user.info.basic / user.info.profile / user.info.stats
POST https://open.tiktokapis.com/v2/video/list/?fields=id,title,video_description,create_time,duration,cover_image_url,share_url,embed_link,like_count,comment_count,share_count,view_count
     {"max_count": 20, "cursor": <int>}   → data.videos[], data.cursor, data.has_more   # 스코프 video.list
POST https://open.tiktokapis.com/v2/video/query/?fields=...
     {"filters": {"video_ids": ["..."]}}   # 최대 20개
```

- Display API에서는 **조회수·좋아요·댓글·공유 수만** 나온다. 시청 지속시간, 완주율, 유입 경로는 TikTok API for Business(비즈니스 계정)에서만 받을 수 있다. 공개 영상만 반환된다.
- 프로필 링크는 비즈니스 계정이거나 팔로워 1,000명 이상이어야 달 수 있다. 캡션 URL은 클릭되지 않는다.

---

## 4. 티스토리

### 4.1 오픈 API 종료 `[기억·미확인]`

- 카카오가 2023년에 종료를 공지했고, **2024년 2월에 글쓰기를 포함한 Open API가 완전히 종료**된 것으로 기억한다.
- 공식 문서 페이지 https://tistory.github.io/document-tistory-apis/ 상단에 종료 안내가 있었다. 정확한 날짜와 공지 원문은 **미확인**이다.
- 2026-10 현재는 **사용할 수 없다고 보고** 설계한다.

### 4.2 Playwright와 본인 세션(storageState)으로 발행하기

아래 셀렉터는 **모두 [추측]**이다. 커뮤니티 코드에서 본 기억이고, 구현할 때 `playwright codegen`으로 실제 DOM을 다시 확인해야 한다.

```python
# 1회: 사람이 headful로 카카오 로그인(2단계 인증 포함) 후 세션 저장
ctx = browser.new_context(); page = ctx.new_page()
page.goto("https://www.tistory.com/auth/login")      # 카카오계정 로그인 수동
ctx.storage_state(path="secrets/tistory_state.json")  # .gitignore 필수

# 발행 작업
ctx = browser.new_context(storage_state="secrets/tistory_state.json")
page.goto(f"https://{blog}.tistory.com/manage/newpost/")
# - "임시저장 글 이어쓰기" confirm 다이얼로그 → page.on("dialog", lambda d: d.dismiss())
# - 제목:      textarea#post-title-inp
# - HTML 모드: #editor-mode-layer-btn-open → #editor-mode-html (전환 confirm → accept)
# - HTML 본문: page.evaluate("h => document.querySelector('.CodeMirror').CodeMirror.setValue(h)", html)
#   (기본 모드 본문은 iframe#editor-tistory_ifr 안의 body#tinymce)
# - 카테고리:  #category-btn → #category-list 의 항목
# - 태그:      input#tagText 입력 후 Enter
# - 이미지:    툴바 첨부 → input[type=file] 에 set_input_files
# - 발행:      #publish-layer-btn → 공개범위 #open20(공개)/#open15(보호)/#open0(비공개) → #publish-btn
```

- 기술 리스크 `[추측]`: 세션 만료, 새 기기 인증, 캡차, headless 탐지, 에디터 DOM 변경.
- 약관 리스크 `[기억·미확인]`:
  - 카카오 통합서비스약관과 티스토리 운영정책은 자동화 수단을 쓴 비정상 이용과 스팸·대량 자동 생성 게시를 제한한다. 조항 원문은 미확인이다.
  - 티스토리가 AI로 대량 발행하는 블로그를 제재한 사례가 있었다고 기억한다.
  - 카카오 계정 제재가 다른 카카오 서비스로 번질 수 있다 `[추측]`.
- `[설계 판단]` **임시저장까지만 자동화하고 발행 버튼은 사람이 누르는** 반자동 방식을 쓴다. 주간 발행량은 적게 유지한다.

### 4.3 대안 비교

| 항목 | 티스토리 + Playwright | WordPress 설치형 | WordPress.com | **Blogger API v3** | 네이버 블로그 |
|---|---|---|---|---|---|
| 공식 글쓰기 API | 없음(종료) | `POST /wp-json/wp/v2/posts` + Application Passwords `[기억]` | `public-api.wordpress.com` OAuth2 `[기억]` | `POST https://blogger.googleapis.com/v3/blogs/{blogId}/posts` | 없음 `[기억·미확인]` |
| 약관 리스크 | 높음 | 없음 | 낮음 | 낮음(스팸 정책만) | — |
| 한국어 검색 노출 | 다음·구글 `[기억]` | 구글 | 구글 | 구글(다음 약함 `[추측]`) | 네이버 |
| 비용 | 무료 | 호스팅비 | 무료/유료 | 무료 | 무료 |

- `[확인-Discovery]` Blogger v3 `posts.insert`:
  - `POST v3/blogs/{blogId}/posts`
  - 파라미터 `blogId`, `fetchBody`, `fetchImages`, `isDraft`
  - 스코프 `https://www.googleapis.com/auth/blogger`
  - 프로젝트 `.venv`에 google-api-python-client가 이미 있고, YouTube OAuth와 함께 쓸 수 있다.

---

## 5. 플랫폼별 AI 생성 콘텐츠 표시 의무

| 플랫폼 | API 필드 | 정책 |
|---|---|---|
| YouTube | `status.containsSyntheticMedia` (videos.insert/update) `[확인-Discovery: rev 20260924, "Indicates if the video contains altered or synthetic media."]` | 사실처럼 보이는 변형·합성 콘텐츠는 공개해야 함 `[기억]` https://support.google.com/youtube/answer/14328491 . YPP의 "반복적 콘텐츠"가 2025-07-15에 "inauthentic content"로 이름이 바뀜 `[기억·미확인]` https://support.google.com/youtube/answer/1311392 |
| Instagram | `POST /{ig-user-id}/media` 의 `is_ai_generated`, 미디어 필드 `is_ai_generated` `[확인-SDK]` | 사실적 영상·음성을 디지털로 만들거나 변형했으면 "AI 정보" 라벨로 공개. C2PA/IPTC 메타데이터를 감지해 자동 라벨 `[기억·미확인]` https://about.fb.com/news/2024/04/metas-approach-to-labeling-ai-generated-content-and-manipulated-media/ |
| Facebook | `video_reels` 의 `is_ai_generated` `[확인-SDK]` | Instagram과 같은 Meta 정책 `[기억]`. 2025년 비독창적·재업로드 콘텐츠 수익화 제한 강화 `[기억·미확인]` |
| TikTok | `post_info.is_aigc` `[기억·미확인]` | 사실적인 AIGC는 라벨 의무. C2PA 자동 라벨 `[기억]` https://www.tiktok.com/community-guidelines/en/integrity-authenticity |
| 티스토리·블로그 | 없음 | 플랫폼 의무는 미확인 |
| 한국 법 | — | 「인공지능 발전과 신뢰 기반 조성 등에 관한 기본법」 2026-01-22 시행, 제31조 생성형 AI 결과물 표시 `[기억·미확인]`. 의무 대상은 '인공지능사업자'이고, 단순 이용자(크리에이터)가 해당하는지는 시행령·가이드라인 해석이 필요함. 과태료 계도기간이 있었던 것으로 기억 https://www.law.go.kr/법령/인공지능발전과신뢰기반조성등에관한기본법 |

`[설계 판단]` TTS 음성, AI 이미지·영상을 쓰면 위 플래그를 모두 `true`로 두는 것을 기본값으로 한다. 실존 인물과 사실처럼 보이는 인물은 합성하지 않는다. 반복 콘텐츠 검사기 결과와 함께 발행 기록(manifest)에 남긴다.

---

## 6. 구현 결론 `[설계 판단]`

1. **Meta**
   - 앱 하나로 Facebook Login 경로를 쓰고, 페이지와 IG를 연결한다. 토큰은 시스템 사용자 토큰을 쓴다.
   - 앱 역할을 주고 개발 모드로 본인 계정만 쓰는 방식이 되는지 먼저 확인한다(1.1).
   - 업로드는 rupload 로컬 파일 방식으로 해서 공개 호스팅을 없앤다.
   - 한도는 IG 100건, FB 30건(24시간 기준)이라 하루 2편은 충분하다.
2. **TikTok**
   - 감사 승인 여부가 불확실하다.
   - 기본은 **받은편지함 업로드 + 앱에서 수동 게시**로 주 14번 탭하는 방식이다.
   - 이와 별개로 Direct Post 감사를 신청하되, 먼저 감사 가이드라인 원문에서 개인용 도구가 거절 대상인지 확인한다.
   - 미감사 상태의 SELF_ONLY 운영은 쓰지 않는다.
3. **티스토리**
   - Playwright로 임시저장까지만 자동화하고 발행은 사람이 누른다.
   - 약관 리스크를 받아들일 수 없으면 SEO 롱테일 채널을 Blogger API로 바꾼다.
4. **유입 경로(UTM)**: 클릭되는 링크는 YouTube 고정댓글·설명란과 블로그 본문에만 있다. IG·TikTok·FB는 프로필 링크에 채널별 단축 UTM을 단다.
5. **주간 분석에서 쓸 지표**
   - IG: `views`, `reach`, `ig_reels_avg_watch_time`, `reels_skip_rate`, `shares`, `saved`, `follows`, `link_clicks`
   - FB: `video_insights`
   - TikTok: 카운트 4종만 나옴
   - 설치·결제는 앱 쪽 기여 분석으로 잡는다.

## 7. 구현 전 반드시 확인할 공식 URL (위 기억 항목 검증용)

- Instagram
  - 콘텐츠 게시: https://developers.facebook.com/docs/instagram-platform/content-publishing/
  - Instagram Login: https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/
  - Facebook Login: https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login/
  - 게시 한도: https://developers.facebook.com/docs/instagram-platform/reference/instagram-user/content_publishing_limit/
  - 미디어 인사이트: https://developers.facebook.com/docs/instagram-platform/reference/instagram-media/insights/
  - 계정 인사이트: https://developers.facebook.com/docs/instagram-platform/reference/instagram-user/insights/
  - 토큰 갱신: https://developers.facebook.com/docs/instagram-platform/reference/refresh_access_token/
- Facebook 릴스: https://developers.facebook.com/docs/video-api/guides/reels-publishing/
- 앱 모드·접근 수준: https://developers.facebook.com/docs/development/build-and-test/app-modes/
- TikTok
  - 시작하기: https://developers.tiktok.com/doc/content-posting-api-get-started
  - Direct Post: https://developers.tiktok.com/doc/content-posting-api-reference-direct-post
  - 미디어 전송 가이드: https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide
  - 사진 게시: https://developers.tiktok.com/doc/content-posting-api-reference-photo-post
  - 업로드(받은편지함): https://developers.tiktok.com/doc/content-posting-api-reference-upload-video
  - 공유 가이드라인: https://developers.tiktok.com/doc/content-sharing-guidelines
  - 토큰 관리: https://developers.tiktok.com/doc/oauth-user-access-token-management
  - 영상 목록: https://developers.tiktok.com/doc/tiktok-api-v2-video-list
- 티스토리
  - API 문서: https://tistory.github.io/document-tistory-apis/
  - 공지: https://notice.tistory.com/

로컬 검증 자료:
- `/tmp/claude-0/-home-user-sjcu-python/51908c08-0703-53d5-87fc-b6b14754da05/scratchpad/fbsdk/x/facebook_business/adobjects/` (iguser.py, igmedia.py, page.py, advideo.py, instagraminsightsresult.py, shadowigmediabuilder.py)
- `/home/user/sjcu-python/.venv/lib/python3.13/site-packages/googleapiclient/discovery_cache/documents/` (youtube.v3.json, blogger.v3.json)