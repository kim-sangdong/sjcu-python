/**
 * 학습 묶음 출제 순서.
 * 오늘의 문항: (유료) 복습 기한이 된 문항 → 안 푼 문항(약한 과목 가중) → 예전에 틀린 문항 → 나머지.
 * 무료는 무료 풀에서만, 남은 한도만큼만 낸다.
 */
import type { Item } from './pack/types';
import { shuffle } from './random';

export interface SessionInput {
  pool: Item[];
  /** 지금까지 한 번이라도 푼 문항 */
  answered: Set<string>;
  /** 마지막 응답이 오답인 문항 */
  wrong: Set<string>;
  /** 복습 기한이 된 문항(유료일 때만 채운다) */
  due: string[];
  weakSubjects: string[];
  size: number;
  rand: () => number;
}

export function buildDailySession(input: SessionInput): Item[] {
  const { pool, answered, wrong, due, weakSubjects, size, rand } = input;
  if (size <= 0 || pool.length === 0) return [];
  const byId = new Map(pool.map((it) => [it.id, it]));
  const picked: Item[] = [];
  const used = new Set<string>();
  const take = (it: Item | undefined) => {
    if (!it || used.has(it.id) || picked.length >= size) return;
    used.add(it.id);
    picked.push(it);
  };

  // 1) 복습 기한(앞쪽 절반까지만 — 새 문항도 섞이게)
  const dueLimit = Math.ceil(size / 2);
  for (const id of due.slice(0, dueLimit)) take(byId.get(id));

  // 2) 안 푼 문항: 약한 과목을 두 배 확률로 앞에 둔다
  const weak = new Set(weakSubjects);
  const unseen = pool.filter((it) => !answered.has(it.id));
  const weighted = unseen
    .map((it) => ({ it, k: rand() / (weak.has(it.subject) ? 2 : 1) }))
    .sort((a, b) => a.k - b.k)
    .map((x) => x.it);
  for (const it of weighted) take(it);

  // 3) 예전에 틀린 문항
  for (const it of shuffle(pool.filter((it) => wrong.has(it.id)), rand)) take(it);

  // 4) 나머지
  for (const it of shuffle(pool, rand)) take(it);
  return picked;
}

export function itemsByIds(ids: string[], byId: Map<string, Item>): Item[] {
  const out: Item[] = [];
  const seen = new Set<string>();
  for (const id of ids) {
    const it = byId.get(id);
    if (it && !seen.has(id)) {
      seen.add(id);
      out.push(it);
    }
  }
  return out;
}
