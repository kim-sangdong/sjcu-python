---
description: 이번 주 롱폼(25~30분) 대본을 VideoScript JSON 으로 쓴다. 유튜브 채널의 팬덤 단계를 맡는다.
argument-hint: "[--week 2026-W42]"
allowed-tools: Bash(python -m studio *) Bash(python3 -m studio *) Read Write Edit Glob Grep
---

# /longform

인자: `$ARGUMENTS`

1. 주차 W 를 정한다(`--week` 가 없으면 `python -m studio weekly prepare` 출력 마지막 줄). `content/weeks/W/plan.json` 의 longform 항목(id 가 `...-L1`)과 `brief.md`, `docs/CONTENT_GUIDE.md` 의 "롱폼 순환", `docs/SCRIPT_FORMAT.md` 를 읽는다.
2. 이번 회차의 순환 유형은 CONTENT_GUIDE 의 규칙(ISO 주 번호 기준)을 따른다.
3. 길이: `channel.yaml` 의 `video.longform_target_minutes` 분. 한국어 내레이션은 공백을 뺀 글자 기준 1초에 약 5.5자(말하기 속도 1.0)다. `hold_seconds`(문제 푸는 시간 등)도 길이에 들어간다. 목표의 ±15% 안에 들게 한다.
4. 구성 원칙
   - 첫 15초 안에 이 영상을 끝까지 보면 무엇을 얻는지 말한다. 인사말·채널 소개로 시작하지 않는다.
   - 챕터 5~8개. 챕터 제목은 설명란 타임스탬프로 쓰이므로 구체적으로 쓴다.
   - 장면 종류를 다양하게 쓴다. 같은 kind 가 4번 넘게 연속되지 않게 하고, 한 영상에 최소 5가지 kind 를 쓴다.
   - 문장은 짧게(한 문장 40자 안팎) 쓴다. 한 장면의 narration 은 화면에 하나씩 드러나는 요소 수와 맞춘다(SCRIPT_FORMAT 의 kind 별 단계 규칙).
   - 앱 안내는 중간 1회(자연스러운 지점), 끝 1회만 한다. 과장 없이, 영상에서 다룬 내용의 "다음 단계"로 소개한다.
   - 모든 사실 주장은 CONTENT_GUIDE 의 검증 가능한 원천에 근거한다. 근거가 필요한 장면은 params 의 basis 에 출처를 쓴다.
5. 메타데이터: title(100자 이하, 연번 금지, 검색어를 앞쪽에), description(첫 2줄에 핵심, 앱 링크는 자동으로 붙으니 쓰지 않는다), tags 10~15개, hashtags 3개, thumbnail(headline 14자 안팎, subline), cta, synthetic_media=true.
6. `content/weeks/W/longform/<id>.json` 에 저장하고 `python -m studio plan validate --week W` 로 이 파일 관련 오류가 없는지 확인한다.
