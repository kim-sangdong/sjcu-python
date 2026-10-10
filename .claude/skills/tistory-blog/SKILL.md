---
description: 이번 주 티스토리 SEO 롱테일 글(기본 3편)을 BlogPost JSON(마크다운 본문)으로 쓴다.
argument-hint: "[--week 2026-W42]"
allowed-tools: Bash(python -m studio *) Bash(python3 -m studio *) Read Write Edit Glob Grep
---

# /tistory-blog

인자: `$ARGUMENTS`

1. 주차 W 와 `plan.json` 의 blog 항목(`...-B01` ~, `keyword` 필드), `docs/CONTENT_GUIDE.md` 의 "블로그 구성", `docs/SCRIPT_FORMAT.md` 의 BlogPost 규격을 읽는다.
2. 키워드: 검색 의도가 분명한 롱테일(3어절 이상)을 쓴다. 제목 앞쪽에 키워드를 넣고, 같은 키워드를 과거 글과 겹치지 않게 한다(`brief.md` 목록 확인).
3. 본문(body_markdown)
   - 1,500~2,500자. 도입 2~3문장에 결론을 먼저 쓴다.
   - 소제목(##) 4~6개, 표 1개 이상(필수 — 렌더러가 없으면 경고), 마지막에 자주 묻는 질문 3개.
   - 사실·수치마다 출처(공식 기관 링크)를 본문에 링크로 단다. 날짜가 바뀌는 정보는 "YYYY년 M월 기준"을 붙인다.
   - 앱 안내 박스와 관련 영상은 렌더러가 자동으로 넣는다. 본문에 앱 링크를 직접 쓰지 않는다.
   - 쇼츠·롱폼 대본 문장을 그대로 옮기지 않는다.
4. meta_description 120자 안팎, tags 5~10개, related_video_id(관련 쇼츠·롱폼 id).
5. `content/weeks/W/blog/<id>.json` 으로 저장하고 검증 명령을 돌린다.
