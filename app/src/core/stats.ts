/**
 * 약점 진단: 과목·단원·함정 유형별 정답률.
 * 표본이 적은 묶음은 순위에서 뺀다(minAttempts). 정렬은 윌슨 하한(95%)으로 해서
 * 2문항 중 0개 같은 작은 표본이 맨 위로 오지 않게 한다.
 */
import type { Item } from './pack/types';

export interface AnswerRow {
  itemId: string;
  correct: boolean;
}

export interface Bucket {
  key: string;
  label: string;
  attempts: number;
  correct: number;
  rate: number;
  /** 정렬용 윌슨 하한 */
  lower: number;
}

export interface Diagnosis {
  overall: { attempts: number; correct: number; rate: number };
  bySubject: Bucket[];
  byUnit: Bucket[];
  byTrap: Bucket[];
}

export function wilsonLower(correct: number, n: number, z = 1.96): number {
  if (n === 0) return 0;
  const p = correct / n;
  const denom = 1 + (z * z) / n;
  const centre = p + (z * z) / (2 * n);
  const margin = z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n));
  return Math.max(0, (centre - margin) / denom);
}

export function trapKey(item: Item): string {
  const raw = item.trap.trim();
  return raw.length > 24 ? raw.slice(0, 24) + '…' : raw || '기타';
}

export function diagnose(
  rows: AnswerRow[],
  byId: Map<string, Item>,
  subjectLabel: (code: string) => string,
  minAttempts = 3,
): Diagnosis {
  const acc = {
    subject: new Map<string, { label: string; n: number; c: number }>(),
    unit: new Map<string, { label: string; n: number; c: number }>(),
    trap: new Map<string, { label: string; n: number; c: number }>(),
  };
  let n = 0;
  let c = 0;
  const add = (m: Map<string, { label: string; n: number; c: number }>, key: string, label: string, ok: boolean) => {
    const e = m.get(key) ?? { label, n: 0, c: 0 };
    e.n += 1;
    if (ok) e.c += 1;
    m.set(key, e);
  };
  for (const r of rows) {
    const it = byId.get(r.itemId);
    if (!it) continue;
    n += 1;
    if (r.correct) c += 1;
    add(acc.subject, it.subject, subjectLabel(it.subject), r.correct);
    const unitKey = `${it.subject}::${it.unit || '기타'}`;
    add(acc.unit, unitKey, `${subjectLabel(it.subject)} · ${it.unit || '기타'}`, r.correct);
    const tk = trapKey(it);
    add(acc.trap, tk, tk, r.correct);
  }
  const toBuckets = (m: Map<string, { label: string; n: number; c: number }>, min: number) =>
    [...m.entries()]
      .filter(([, v]) => v.n >= min)
      .map(([key, v]) => ({
        key,
        label: v.label,
        attempts: v.n,
        correct: v.c,
        rate: v.n ? v.c / v.n : 0,
        lower: wilsonLower(v.c, v.n),
      }))
      .sort((a, b) => a.lower - b.lower || b.attempts - a.attempts);
  return {
    overall: { attempts: n, correct: c, rate: n ? c / n : 0 },
    bySubject: toBuckets(acc.subject, 1),
    byUnit: toBuckets(acc.unit, minAttempts),
    byTrap: toBuckets(acc.trap, minAttempts),
  };
}

/** 과목 진도(무료): 과목별 푼 문항 수 / 전체 */
export function progressBySubject(
  answeredIds: Set<string>,
  items: Item[],
): Map<string, { done: number; total: number }> {
  const m = new Map<string, { done: number; total: number }>();
  for (const it of items) {
    const e = m.get(it.subject) ?? { done: 0, total: 0 };
    e.total += 1;
    if (answeredIds.has(it.id)) e.done += 1;
    m.set(it.subject, e);
  }
  return m;
}
