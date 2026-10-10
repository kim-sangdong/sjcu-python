---
description: 콘텐츠별 전환 데이터로 유료 광고(부스트)를 할지 말지 결정하고 예산안을 낸다. 광고 집행 자체는 사람이 광고 관리자에서 한다.
argument-hint: "[--week 2026-W42] [--budget 300000]"
allowed-tools: Bash(python -m studio *) Bash(python3 -m studio *) Read Write Edit Glob Grep
---

# /ad-decision

인자: `$ARGUMENTS` (`--budget` 은 이번 주 광고 예산 원화, 없으면 `channel.yaml` 의 `ads.weekly_budget_krw` 또는 0)

1. 주차 W 를 정하고 `python -m studio analytics ad-data --week W` 로 `reports/W/ad-decision-data.json` 을 만든다.
2. JSON 의 `rule_recommendation`(boost|hold|stop|insufficient_data)을 출발점으로 삼되, 다음을 따져 최종 판단한다.
   - 표본이 부족(`sample_ok: false`)하면 boost 하지 않는다.
   - 추정 ROAS 가 1 미만이면 boost 하지 않는다. 가정값(CPM·CPI)을 보고서에 그대로 밝힌다.
   - 같은 소재를 여러 플랫폼에 동시에 부스트하지 않는다(가장 효율 좋은 한 곳).
   - 예산이 0 이면 "광고 없이 유기적으로 밀 콘텐츠" 순위만 낸다.
3. `reports/W/ad-decision.md` 를 쓴다: 결론 2문장 → 결정 표(콘텐츠, 플랫폼, 결정, 예산, 기대 설치·매출, 근거) → 가정과 한계 → 사람이 할 일(광고 관리자에서 집행할 항목 체크리스트).
4. 결론 2문장을 출력한다.
