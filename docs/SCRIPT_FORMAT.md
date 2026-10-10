# 원고 JSON 규격

Claude Code(스킬)가 쓰고 `python -m studio plan validate` 가 검사하는 파일 형식이다.
정식 정의는 `studio/models.py` 이고, 이 문서는 쓰는 사람을 위한 설명이다.

```
content/weeks/<주차>/
  plan.json                주간 계획 (WeekPlan)
  brief.md                 기획 입력 자료 (자동 생성: 지난주 보고서 + 발행 이력)
  longform/<id>.json       롱폼 대본 (VideoScript, format=longform)
  shorts/<id>.json         쇼츠 대본 (VideoScript, format=short)
  cardnews/<id>.json       카드뉴스 (CardNews)
  blog/<id>.json           블로그 글 (BlogPost)
  manifest.json            렌더 결과 (자동)
  originality.json / .md   독창성 검사 결과 (자동)
  queue.json               발행 대기열 (자동)
  RUN_REPORT.md            주간 실행 보고서
```

ID 규칙: 주차 `2026-W42` → 롱폼 `2026W42-L1`, 쇼츠 `2026W42-S01`~`S14`, 카드뉴스 `2026W42-C01`~, 블로그 `2026W42-B01`~.
ID 는 채널별 추적 링크의 `utm_content` 값이 되어, 어떤 콘텐츠가 설치·결제를 만들었는지 잇는 열쇠가 된다.

## plan.json (WeekPlan)

```json
{
  "week": "2026-W42",
  "theme": "이번 주 주제",
  "summary": "한두 문장",
  "brief_applied": "지난주 보고서에서 반영한 점",
  "items": [
    {"id": "2026W42-L1", "kind": "longform", "topic": "...", "angle": "지난 회차와 다른 점", "keyword": "", "source_id": null, "publish_at": "2026-10-11T19:00:00+09:00"}
  ]
}
```
`id`, `kind`, `publish_at` 은 `plan init` 이 정한다. 바꾸지 않는다.

## VideoScript (롱폼·쇼츠)

```json
{
  "id": "2026W42-S03",
  "week": "2026-W42",
  "format": "short",
  "title": "제목 (100자 이하, 연번 금지)",
  "description": "설명 (앱 링크·AI 고지·해시태그는 발행 때 자동으로 붙는다)",
  "tags": ["태그", "..."],
  "hashtags": ["해시태그"],
  "chapters": [
    {"title": "챕터 제목", "scenes": [
      {"id": "s1", "kind": "quiz", "narration": ["문장1", "문장2"], "params": {}, "hold_seconds": 5}
    ]}
  ],
  "thumbnail": {"headline": "썸네일 큰 글씨", "subline": "작은 글씨", "style": "default", "params": {}},
  "pinned_comment": "",
  "cta": "",
  "synthetic_media": true,
  "made_for_kids": false,
  "notes": "형식: 1분 1문제"
}
```

- 쇼츠는 챕터 1개면 충분하다. 롱폼은 챕터 5~8개.
- `narration` 은 문장 단위 목록이다. 문장마다 음성 한 클립과 자막 한 줄이 만들어지고, 화면 요소가 하나씩 드러나는 "단계"가 된다.
- `hold_seconds` 는 내레이션이 끝난 뒤 화면을 더 보여 주는 시간(문제 풀이 시간 등). 최대 180초.

### 장면 종류(kind)와 params

| kind | params | 단계(문장) 배분 |
|---|---|---|
| `title` | `title`, `subtitle?` | 문장 수만큼 머문다 |
| `chapter` | `title`, `index?` | 장 구분 카드 |
| `text` | `heading?`, `body` | 본문 고정 |
| `bullets` | `heading?`, `items[]` | 문장 하나에 항목 하나씩 공개 |
| `quiz` | `question`, `choices[2~5]`, `answer`(0부터), `explanation`, `basis?`, `countdown?` | 문제·보기 → (hold 동안 카운트다운) → 정답 공개 → 해설. 문장 수 = 문제 읽기 1 + 해설 1~3 권장 |
| `compare` | `left_title`, `right_title`, `rows: [[왼쪽, 오른쪽], ...]` | 문장 하나에 한 행씩 |
| `sequence` | `items[]`(정답 순서), `shuffled?`(처음 보여 줄 순서) | 섞인 카드 → 정렬 |
| `timeline` | `events: [{"label", "text"}]` | 문장 하나에 사건 하나씩 |
| `flow` | `steps[]` | 문장 하나에 단계 하나씩 |
| `bars` | `title`, `items: [{"label", "value"}]`, `unit?` | 막대가 차례로 |
| `case` | `title?`, `body`, `reconstructed?`(true 면 "재구성 사례" 표기) | 본문 고정 |
| `quote` | `text`, `source?` | 인용 고정 |
| `image` | `path`(저장소 기준), `caption?` | 이미지 고정 |
| `voice` | `audio`(저장소 기준 사람 녹음 파일) | TTS 대신 녹음 사용, narration 은 자막으로만 |
| `cta` | `headline`, `body?` | 앱 안내 |
| `outro` | `headline?` | 끝맺음 |

시험 대비용 장면 `ox`, `table_blank`, `relations`, `calc`, `diff`, `ranking`, `flow` 의 `hidden`, 모든 장면 공통 `basis`(근거 배지)와
각 kind 의 정확한 단계 배분(예: quiz 는 `reveal_line` 또는 '정답'이 처음 나오는 문장에서 공개)은 **`docs/SCENES.md`** 에 있다.
실제 렌더러 동작은 `studio/video/scenes/` 가 기준이다. 이 표와 다르면 SCENES.md 와 렌더러 쪽을 따른다.

- 퀴즈·OX·빈칸·계산·순서 장면은 검증된 문제은행 문항에서 가져오고 `params.bank_id` 에 문항 ID 를 적는다(`docs/CONTENT_GUIDE.md` 5장).
  문항이 여럿이면(빈칸표의 행들) 목록 `["Q-CIV-…", "Q-CIV-…"]`. 카드뉴스 슬라이드도 같은 `params.bank_id` 를 쓴다.
  `plan validate` 는 없는 문항·verified 가 아닌 문항(교차확인 미완료 포함)·장면 정답과 문항 정답이 다른 경우를 **오류**로 막는다
  (리허설 `STUDIO_DRY_RUN=1` 에서만 verified 아닌 문항을 경고로 낮춘다). 정답 장면에 `bank_id` 가 없으면 경고.
- 근거 배지 `params.basis` 는 시행일·선고일이 있어야 한다. 날짜 없는 법령·판례 근거는 `plan validate` 가 오류로 막는다(`docs/SCENES.md` 1장).
- 영상 quiz 와 문제은행 mcq 의 `answer` 는 **0부터** 센다.

## CardNews

```json
{
  "id": "2026W42-C01", "week": "2026-W42",
  "title": "세트 제목",
  "caption": "인스타 캡션 (2200자 이하)",
  "hashtags": ["..."],
  "slides": [
    {"kind": "cover", "title": "표지 제목", "body": "부제"},
    {"kind": "body", "title": "소제목", "body": "본문 80자 안팎"},
    {"kind": "list", "title": "소제목", "body": "항목1\n항목2\n항목3"},
    {"kind": "quiz", "title": "문제", "body": "", "params": {"choices": ["가", "나", "다"], "answer": "나"}},
    {"kind": "answer", "title": "정답: 나", "body": "해설"},
    {"kind": "quiz", "title": "OX 지문", "body": "", "params": {"choices": ["O", "X"], "answer": "X", "basis": "[공인중개사법 제33조 제1항 · 2026.1.1 시행 기준]"}},
    {"kind": "body", "title": "숫자 표", "body": "", "params": {"table": {"columns": ["구분", "기간"], "rows": [["가", "1년"], ["나", "2년"]]}}},
    {"kind": "cta", "title": "더 풀어 보기", "body": "프로필 링크의 앱에서"}
  ],
  "source_id": null
}
```
슬라이드는 2~10장.

- **카드뉴스 quiz 의 answer 는 선택지 글 그대로 쓴다**(위 예시처럼 `"나"`, OX 는 `"O"`/`"X"`). 숫자로 쓰면 카드뉴스는 1부터 세므로, 0부터 세는 영상 quiz·문제은행 mcq 와 헷갈려 정답이 뒤집힐 수 있다.
- 표 슬라이드는 body + `params.table`, 근거 배지는 `params.basis`. 자세한 레이아웃 규칙은 `studio/cardnews/layouts.py`·`studio/cardnews/exam.py` 상단 설명.

## BlogPost

```json
{
  "id": "2026W42-B01", "week": "2026-W42",
  "title": "키워드가 앞에 오는 제목",
  "keyword": "롱테일 키워드",
  "tags": ["..."],
  "meta_description": "160자 이하",
  "body_markdown": "## 소제목\n\n본문...\n\n| 표 | 필수 |\n|---|---|\n| ... | ... |",
  "related_video_id": "2026W42-S03"
}
```
