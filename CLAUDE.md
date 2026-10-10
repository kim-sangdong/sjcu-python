# 이 저장소에서 Claude Code 가 지킬 것

쇼츠 발견 → 유튜브 구독 → 롱폼 팬덤 → 앱 설치 → 앱 결제로 이어지는 1인 콘텐츠 비즈니스를 운영하는 저장소다.
사람이 손대지 않고 매주 돌아가는 것이 목표다. 그래서 명령어(스킬)는 질문하지 말고 문서의 규칙으로 판단한다.

## 구조

- `studio/` — 파이썬 패키지. `python -m studio --help` 로 명령 목록을 본다.
  - 원고 검증 `plan`, 렌더 `render video|cardnews|blog`, 독창성 검사 `check originality`·`archive add`,
    발행 `publish schedule|due|status|mark|retry|upload-media`, 인증 `auth <플랫폼>`, 댓글 `comments fetch|post`,
    분석 `analytics collect|report|ad-data`, 문제은행 `bank add|verify|crosscheck|list|export|upload|watch-laws|mark-used|law`,
    주간 묶음 `weekly prepare|build|ship|status`, 점검 `doctor`·`secrets`.
- `.claude/skills/` — 슬래시 명령어 11종(`/question-bank` 포함). `/weekly-publish start` 가 한 주 전체를 돌린다.
- `content/bank/*.jsonl` — 문제은행(과목별). 퀴즈형 장면은 verified 문항만 쓴다.
- `content/weeks/<주차>/` — 원고·계획·검사 결과·발행 대기열(JSON, 커밋 대상). 렌더 결과물은 `out/`(커밋 안 함).
- `archive/index.json` — 발행한 콘텐츠의 지문. 독창성 검사가 과거와 비교할 때 쓴다.
- `reports/<주차>/` — 성과·매출·광고 판단·전략 보고서.
- `app/` — 앱(Expo). `supabase/` — 추적 링크·결제 웹훅·DB. `.github/workflows/` — 정기 실행.
- `docs/CONTENT_GUIDE.md` — 채널 주제·형식·말투·금지 표현·검증 원천. **원고를 쓰기 전에 반드시 읽는다.**
- `docs/SCENES.md` — 영상 장면 kind 별 params·단계 규칙. `docs/APP.md` — 앱 구조·출시 절차.
- `docs/SCRIPT_FORMAT.md` — 원고 JSON 규격. `docs/SETUP.md` — 계정·키 준비. `docs/research/` — 구현 근거 조사.

## 규칙

1. 원고 JSON 을 쓰면 반드시 `python -m studio plan validate --week <주차>` 로 확인한다.
2. `channel.yaml` 의 `rules.banned_phrases` 는 어디에도 쓰지 않는다. 사실 주장은 CONTENT_GUIDE 의 검증 가능한 원천으로 확인되는 것만 쓴다.
3. 독창성 검사(`check originality`)가 차단한 콘텐츠는 발행하지 않는다. 검사 기준을 느슨하게 바꿔서 통과시키지 않는다.
4. 숫자 보고는 `analytics` 명령이 계산한 값만 쓴다. 직접 계산하지 않는다.
5. 비밀값(토큰·키)을 파일에 쓰거나 출력하지 않는다. `.env` 와 `.secrets/` 는 커밋하지 않는다.
6. 리허설은 `STUDIO_DRY_RUN=1` 과 `--tts silent` 로 한다(실제 업로드·유료 음성 합성 없음).
7. 테스트: `python -m pytest`.
