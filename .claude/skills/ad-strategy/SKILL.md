---
description: 이번 주 성과·광고 판단·독창성 검사 결과를 종합해 다음 주 콘텐츠·광고 전략을 쓴다. 다음 주 /weekly-publish 의 기획 입력이 된다.
argument-hint: "[--week 2026-W42]"
allowed-tools: Bash(python -m studio *) Bash(python3 -m studio *) Read Write Edit Glob Grep
---

# /ad-strategy

인자: `$ARGUMENTS`

1. 주차 W 의 `reports/W/channel-revenue.md`, `reports/W/ad-decision.md`, `content/weeks/W/originality.md`, `content/weeks/W/RUN_REPORT.md`, 최근 댓글 정정 목록(`content/comments/*/corrections.md` 중 최근 7일)을 읽는다. 없는 파일은 "없음"으로 두고 진행한다.
2. `reports/W/ad-strategy.md` 를 쓴다. 다음 주 `python -m studio plan init` 이 이 파일을 기획 입력(brief.md)에 그대로 넣는다.
   - 결론 2문장.
   - 다음 주 테마 후보 3개와 추천 1개(근거: 이번 주 숫자).
   - 쇼츠 형식 조정: 늘릴 형식, 줄일 형식, 새로 시험할 형식(최대 1개).
   - 도입(첫 2초) 실험 2가지, 앱 안내 문구 실험 1가지.
   - 롱폼 순환에서 이번 주 성과가 다음 회차에 주는 시사점.
   - 독창성 경고에서 나온 반복 패턴과 피할 방법.
   - 정정해야 할 내용(있다면)과 반영 방법.
   - 광고: 다음 주 예산 배분안(ad-decision 기준).
3. 결론 2문장을 출력한다.
