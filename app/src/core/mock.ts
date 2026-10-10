/**
 * 자체 모의고사(유료): 과목 규칙대로 문항을 뽑고, 예상 점수(참고용)와 과락 경고를 계산한다.
 * 합격 여부를 예측하는 문구는 만들지 않는다. 점수는 "자체 문항 기준 참고용"으로만 표시한다.
 */
import type { Item, MockSetRule } from './pack/types';
import { shuffle } from './random';

/** 모의고사에 적합한 유형(실제 시험과 같은 5지선다 우선) */
const TYPE_PRIORITY: Record<string, number> = { mcq: 0, calc: 1, ox: 2, blank: 3, order: 4 };

export interface MockBuild {
  itemIds: string[];
  /** 규칙보다 문항이 부족했던 과목 */
  shortages: { subject: string; wanted: number; got: number }[];
}

export function availableMockSets(rules: MockSetRule[], items: Item[]): MockSetRule[] {
  const counts = new Map<string, number>();
  for (const it of items) counts.set(it.subject, (counts.get(it.subject) ?? 0) + 1);
  return rules.filter((r) => r.compose.every((c) => (counts.get(c.subject) ?? 0) > 0));
}

export function buildMock(rule: MockSetRule, items: Item[], rand: () => number): MockBuild {
  const ids: string[] = [];
  const shortages: MockBuild['shortages'] = [];
  for (const c of rule.compose) {
    const pool = shuffle(
      items.filter((it) => it.subject === c.subject),
      rand,
    ).sort((a, b) => (TYPE_PRIORITY[a.type] ?? 9) - (TYPE_PRIORITY[b.type] ?? 9));
    const picked = pool.slice(0, c.count);
    if (picked.length < c.count) shortages.push({ subject: c.subject, wanted: c.count, got: picked.length });
    ids.push(...shuffle(picked, rand).map((it) => it.id));
  }
  return { itemIds: ids, shortages };
}

export interface MockScore {
  total: number;
  correct: number;
  answered: number;
  /** 100점 만점 환산(소수 첫째 자리) */
  score100: number;
  /** 과락선 미만 */
  belowFailLine: boolean;
  bySubject: { subject: string; total: number; correct: number }[];
}

export function scoreMock(
  itemIds: string[],
  results: Record<string, boolean | null | undefined>,
  subjectOf: (id: string) => string,
  failLine: number,
): MockScore {
  const total = itemIds.length;
  let correct = 0;
  let answered = 0;
  const per = new Map<string, { total: number; correct: number }>();
  for (const id of itemIds) {
    const r = results[id];
    const s = subjectOf(id);
    const e = per.get(s) ?? { total: 0, correct: 0 };
    e.total += 1;
    if (r === true || r === false) answered += 1;
    if (r === true) {
      correct += 1;
      e.correct += 1;
    }
    per.set(s, e);
  }
  const score100 = total ? Math.round((correct / total) * 1000) / 10 : 0;
  return {
    total,
    correct,
    answered,
    score100,
    belowFailLine: score100 < failLine,
    bySubject: [...per.entries()].map(([subject, v]) => ({ subject, ...v })),
  };
}

/** 같은 차수(1차/2차) 최근 점수들의 평균(참고용). 과목이 하나도 없으면 null */
export function partAverage(latestScores: { part: number; score100: number }[], part: number): number | null {
  const s = latestScores.filter((x) => x.part === part);
  if (!s.length) return null;
  return Math.round((s.reduce((a, b) => a + b.score100, 0) / s.length) * 10) / 10;
}
