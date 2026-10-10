---
description: 이번 주 콘텐츠별 조회·클릭·설치·결제를 모아 "어떤 콘텐츠가 매출을 만들었는지" 보고서를 만든다. 매주 토요일 밤 GitHub Actions 가 부른다.
argument-hint: "[--week 2026-W42] [--fake]"
allowed-tools: Bash(python -m studio *) Bash(python3 -m studio *) Read Write Edit Glob Grep
---

# /channel-revenue

인자: `$ARGUMENTS`

1. 주차 W: `--week` 가 없으면 지금 발행 중인 주차(`python -m studio plan status` 가 보여 주는 최근 주차, 또는 오늘이 속한 ISO 주)를 쓴다.
2. `reports/W/metrics.json` 이 없거나 6시간보다 오래됐으면 `python -m studio analytics collect --week W` 를 실행한다(`--fake` 인자가 있으면 그대로 넘긴다 — 형식 확인용 합성 데이터).
3. `python -m studio analytics report --week W` 로 `reports/W/channel-revenue.md` 와 `.csv` 를 만든다. 숫자는 이 명령이 계산한 값만 쓴다. 직접 계산하지 않는다.
4. 보고서를 읽고 맨 아래에 `## 해석` 절을 덧붙인다.
   - 결론 2문장: 이번 주 매출을 만든 콘텐츠·플랫폼과 그 규모.
   - 잘된 것 3개와 그 이유(가설이면 "가설"이라고 표시), 안된 것 3개와 이유.
   - 데이터 결손 경고가 있으면 그 결손 때문에 말할 수 없는 것을 분명히 적는다.
   - 다음 주에 시험할 것 3개(형식·주제·도입·앱 안내 방식 중).
5. 해석 절의 결론 2문장을 출력한다.
