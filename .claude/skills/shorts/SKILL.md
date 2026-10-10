---
description: 이번 주 쇼츠(기본 14편, 59초 이하) 대본을 VideoScript JSON 으로 쓴다. 유튜브·인스타·페북·틱톡 발견 단계를 맡는다.
argument-hint: "[--week 2026-W42]"
allowed-tools: Bash(python -m studio *) Bash(python3 -m studio *) Read Write Edit Glob Grep
---

# /shorts

인자: `$ARGUMENTS`

1. 주차 W 와 `content/weeks/W/plan.json` 의 short 항목들(id `...-S01` ~), `brief.md`, `docs/CONTENT_GUIDE.md` 의 "쇼츠 형식표", `docs/SCRIPT_FORMAT.md` 를 읽는다. 롱폼 원고가 이미 있으면 그것도 읽는다(파생 쇼츠는 `source_id` 를 롱폼 id 로).
2. 형식 배치: 같은 형식이 연속되지 않게, 한 형식이 주 3편을 넘지 않게. 각 편의 형식은 `notes` 첫 줄에 `형식: <이름>` 으로 적는다.
3. 길이: 25~50초 목표, 59초 이하(검증기가 막는다). 내레이션 합계는 공백 제외 약 140~260자.
   화면 글은 세로 폭이 좁다 — 함정 OX 는 `hook` 이 있으면 지문 30자 안팎·해설 18자 이하(`docs/SCENES.md` ox 의 "세로 쇼츠 길이" 표). 넘치면 렌더가 멈추고 줄일 글을 알려 준다.
4. 원칙
   - 첫 2초(첫 문장)에 질문이나 반전으로 붙잡는다. "안녕하세요"로 시작하지 않는다.
   - 한 편에 핵심 하나. 14편이 서로 다른 소재·다른 도입·다른 결말이어야 한다(같은 문장을 여러 편에 쓰지 않는다).
   - 마지막 문장은 다음 편이나 롱폼으로 이어지는 한마디 또는 "프로필 링크의 앱"을 가볍게 언급(14편 중 절반 이하).
   - 사실은 CONTENT_GUIDE 의 검증 가능한 원천에 근거하고, 퀴즈·비교 장면은 basis 를 채운다.
5. 메타데이터: title(검색어 포함, 40자 안팎, 연번·"#1" 금지), description 2~3문장, hashtags 3~5개(#Shorts 는 넣지 않아도 된다), synthetic_media=true. thumbnail 은 넣으면 인스타 커버로 쓰인다.
6. 파일마다 `content/weeks/W/shorts/<id>.json` 으로 저장하고 `python -m studio plan validate --week W` 로 확인한다.
