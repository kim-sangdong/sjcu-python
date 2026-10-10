---
description: 이번 주 인스타 카드뉴스(기본 3세트, 세트당 5~10장)를 CardNews JSON 으로 쓴다.
argument-hint: "[--week 2026-W42]"
allowed-tools: Bash(python -m studio *) Bash(python3 -m studio *) Read Write Edit Glob Grep
---

# /card-news

인자: `$ARGUMENTS`

1. 주차 W 와 `plan.json` 의 cardnews 항목(`...-C01` ~), `docs/CONTENT_GUIDE.md` 의 "카드뉴스 구성", `docs/SCRIPT_FORMAT.md` 의 CardNews 규격을 읽는다.
2. 세트 구성: 표지(cover) 1장 → 본문(body·list·quiz·answer) → 마지막 cta 1장. 5~10장.
3. 한 장에 메시지 하나. 제목 20자 안팎, 본문 80자 안팎(넘치면 렌더러가 글자를 줄이다가 오류를 낸다).
4. 저장 → 넘김을 유도하는 흐름(앞장에서 질문, 다음 장에서 답). 퀴즈 장은 `params.choices` 와 `params.answer` — answer 는 **선택지 글 그대로**(OX 는 "O"/"X"). 숫자로 쓰지 않는다. 숫자 표는 body 장의 `params.table`, 근거는 `params.basis`(CONTENT_GUIDE 1장 배지 형식).
5. caption: 첫 줄에 핵심, 저장·공유할 이유 한 줄. 해시태그는 `hashtags` 에 3~5개(캡션 본문에 직접 쓰지 않는다). 링크는 쓰지 않는다(프로필 링크 안내는 자동).
6. 쇼츠·롱폼과 소재가 같아도 문장은 새로 쓴다(복붙 금지 — 독창성 검사가 잡는다).
7. `content/weeks/W/cardnews/<id>.json` 으로 저장하고 검증 명령을 돌린다.
