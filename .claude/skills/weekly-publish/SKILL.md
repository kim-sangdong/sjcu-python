---
description: 주간 제작·발행 파이프라인. start = 기획 → 원고 → 렌더 → 독창성 검사 → 예약 발행까지 사람 손 없이 끝낸다. 매주 일요일 12:00 KST 에 GitHub Actions 가 부른다.
argument-hint: "[start|build|ship|status] [--week 2026-W42] [--dry-run]"
allowed-tools: Bash(python -m studio *) Bash(python3 -m studio *) Read Write Edit Glob Grep
---

# /weekly-publish

인자: `$ARGUMENTS` (비어 있으면 `status`)

- `start`  : 아래 1~8단계를 모두 실행한다.
- `build`  : 5~6단계만(원고는 이미 있음).
- `ship`   : 7단계만.
- `status` : `python -m studio weekly status` 결과를 요약해 보여 준다.
- `--dry-run` 이 있으면 모든 명령 앞에 `STUDIO_DRY_RUN=1` 을 붙이고 `weekly build` 에 `--tts silent` 를 준다(리허설: 실제 업로드·유료 TTS 없음).
- `--week` 가 있으면 그 주차를, 없으면 `prepare` 가 출력한 주차(마지막 줄)를 쓴다.

## 지켜야 할 것

- 사람에게 질문하지 않는다. 판단이 필요하면 `docs/CONTENT_GUIDE.md` 의 규칙으로 정하고, 정한 내용을 실행 보고서에 적는다.
- 원고는 `docs/SCRIPT_FORMAT.md` 의 JSON 규격을 정확히 따른다. 파일을 쓴 뒤에는 반드시 검증 명령을 돌린다.
- 사실 주장은 `docs/CONTENT_GUIDE.md` 의 "검증 가능한 원천"으로 확인할 수 있는 것만 쓴다. 확인할 수 없으면 그 문장을 빼거나 표현을 낮춘다.
- `channel.yaml` 의 `rules.banned_phrases` 는 어떤 필드에도 쓰지 않는다.

## 단계

1. **준비** — `python -m studio weekly prepare [--week W]` 실행. 출력 마지막 줄이 주차(W)다.
2. **입력 읽기** — `docs/CONTENT_GUIDE.md`, `docs/SCRIPT_FORMAT.md`, `channel.yaml`, `content/weeks/W/brief.md`(지난주 성과·전략, 이미 발행한 제목 목록), `content/weeks/W/plan.json` 을 읽는다.
3. **주간 기획** — `plan.json` 의 `theme`, `summary`, `brief_applied`(지난주 보고서에서 무엇을 반영했는지)와 모든 항목의 `topic`, `angle`, `keyword`(블로그), `source_id`(파생 관계)를 채운다. `id`·`kind`·`publish_at` 은 바꾸지 않는다.
   - brief.md 의 이미 발행한 제목과 주제·구성이 겹치지 않게 한다.
   - 쇼츠 14편은 CONTENT_GUIDE 의 쇼츠 형식표에서 고르되, 같은 형식이 연속되지 않고 한 형식이 주 3편을 넘지 않게 배치한다.
4. **원고 작성** — 아래 스킬 문서를 읽고 그 절차대로 파일을 쓴다(각 스킬을 따로 부를 필요 없이 문서 내용을 따른다).
   - `.claude/skills/longform/SKILL.md` → `content/weeks/W/longform/<id>.json`
   - `.claude/skills/shorts/SKILL.md` → `content/weeks/W/shorts/<id>.json` (14편)
   - `.claude/skills/card-news/SKILL.md` → `content/weeks/W/cardnews/<id>.json`
   - `.claude/skills/tistory-blog/SKILL.md` → `content/weeks/W/blog/<id>.json`
   그다음 `python -m studio plan validate --week W` 를 돌려 오류가 0건이 될 때까지 고친다.
5. **빌드** — `python -m studio weekly build --week W` (리허설이면 `--tts silent`).
   - 종료 코드 2(독창성 차단)면 `content/weeks/W/originality.md` 의 수정 지시(fixes)대로 해당 원고만 고쳐 다시 빌드한다. 최대 3회.
   - 3회 뒤에도 차단된 콘텐츠는 이번 주에서 뺀다: `plan.json` 에서 그 항목을 지우고 원고 파일을 삭제한 뒤 빌드를 한 번 더 돌린다. 뺀 이유를 실행 보고서에 적는다.
   - 종료 코드 1(검증·렌더 실패)이면 오류 메시지를 읽고 원인을 고친다. 렌더 도구 자체의 고장이면 고치려 하지 말고 실행 보고서에 정확한 오류를 남기고 멈춘다.
6. **검사 결과 확인** — `originality.md` 의 경고(warn)는 읽고, 다음 주 기획에 반영할 점을 실행 보고서에 적는다.
7. **발행 예약** — `python -m studio weekly ship --week W` (리허설이면 `--dry-run`). 결과 표에서 failed 가 있으면 원인을 요약한다. 유튜브가 감사 전(`platforms.youtube.audited: false`)이면 업로드 패키지 경로를 보고서에 적는다(사람이 Studio 에서 예약 업로드 → `python -m studio publish mark ...` 로 영상 ID 연결).
8. **실행 보고서** — `content/weeks/W/RUN_REPORT.md` 에 다음을 쓴다: 주제, 만든 콘텐츠 수, 독창성 결과(차단·경고 수와 처리), 발행 예약 현황, 사람이 해야 할 일(있다면), 다음 주에 반영할 점. 마지막에 같은 내용을 5줄 이내로 출력한다.
