import type { Item } from '../pack/types';
import { mulberry32 } from '../random';
import { buildDailySession, itemsByIds } from '../session';
import { diagnose, progressBySubject, trapKey, wilsonLower } from '../stats';

const LONG = '긴 함정 설명입니다 아주 길게 써서 잘리는지 확인하는 문장';
const mk = (id: string, subject: string, unit = 'u', trap: string = LONG) =>
  ({ id, subject, unit, type: 'ox', stem: '', answer: true, explanation: '', trap, basis: [], difficulty: 1, free: true }) as Item;

describe('약점 진단', () => {
  const items = [mk('a1', 'civil', '임대차', '기간혼동'), mk('a2', 'civil', '임대차', '기간혼동'), mk('b1', 'tax', '취득세', '주체바꿈'), mk('b2', 'tax', '취득세')];
  const byId = new Map(items.map((i) => [i.id, i]));
  const rows = [
    ...Array(5).fill({ itemId: 'a1', correct: true }),
    ...Array(5).fill({ itemId: 'a2', correct: false }),
    ...Array(4).fill({ itemId: 'b1', correct: false }),
    { itemId: 'b2', correct: true },
    { itemId: 'gone', correct: false }, // 팩에서 사라진 문항은 무시
  ];

  test('과목·단원·함정 유형별 정답률, 약한 순 정렬', () => {
    const d = diagnose(rows, byId, (c) => ({ civil: '민법', tax: '세법' })[c] ?? c);
    expect(d.overall).toEqual({ attempts: 15, correct: 6, rate: 6 / 15 });
    expect(d.bySubject[0]!.key).toBe('tax');
    expect(d.bySubject.map((b) => [b.key, b.correct, b.attempts])).toEqual([
      ['tax', 1, 5],
      ['civil', 5, 10],
    ]);
    expect(d.byUnit.map((b) => b.label)).toEqual(['세법 · 취득세', '민법 · 임대차']);
    expect(d.byTrap.map((b) => b.key)).toEqual(['주체바꿈', '기간혼동']); // 긴 설명 trap 인 b2 는 1문항이라 빠짐
  });

  test('함정 유형 키: 유형 이름은 그대로, 긴 설명은 앞부분', () => {
    expect(trapKey(mk('x', 's', 'u', '숫자바꿈'))).toBe('숫자바꿈');
    expect(trapKey(mk('x', 's')).endsWith('…')).toBe(true);
  });

  test('윌슨 하한: 표본이 작으면 낮게', () => {
    expect(wilsonLower(0, 0)).toBe(0);
    expect(wilsonLower(2, 2)).toBeLessThan(wilsonLower(95, 100));
  });

  test('과목 진도', () => {
    const p = progressBySubject(new Set(['a1', 'b1']), items);
    expect(p.get('civil')).toEqual({ done: 1, total: 2 });
  });
});

describe('오늘의 문항 출제', () => {
  const pool = Array.from({ length: 30 }, (_, i) => mk(`q${i}`, i < 10 ? 'weak' : 'other'));
  test('복습 기한 문항이 먼저(최대 절반), 안 푼 문항, 크기 제한, 중복 없음', () => {
    const due = ['q20', 'q21', 'q22', 'q23', 'q24', 'q25', 'q26', 'q27', 'q28', 'q29', 'q11', 'q12'];
    const s = buildDailySession({
      pool,
      answered: new Set(due),
      wrong: new Set(),
      due,
      weakSubjects: ['weak'],
      size: 20,
      rand: mulberry32(3),
    });
    expect(s).toHaveLength(20);
    expect(s.slice(0, 10).map((i) => i.id)).toEqual(due.slice(0, 10));
    expect(new Set(s.map((i) => i.id)).size).toBe(20);
  });

  test('약한 과목이 앞쪽에 더 많이 나온다', () => {
    let weakFirst = 0;
    for (let seed = 1; seed <= 50; seed++) {
      const s = buildDailySession({ pool, answered: new Set(), wrong: new Set(), due: [], weakSubjects: ['weak'], size: 10, rand: mulberry32(seed) });
      weakFirst += s.filter((i) => i.subject === 'weak').length;
    }
    // 가중치가 없으면 평균 10×(10/30)≈3.3개
    expect(weakFirst / 50).toBeGreaterThan(4.5);
  });

  test('남은 한도 0 이면 빈 묶음, 풀이 작으면 있는 만큼', () => {
    expect(buildDailySession({ pool, answered: new Set(), wrong: new Set(), due: [], weakSubjects: [], size: 0, rand: mulberry32(1) })).toEqual([]);
    expect(buildDailySession({ pool: pool.slice(0, 3), answered: new Set(), wrong: new Set(), due: [], weakSubjects: [], size: 20, rand: mulberry32(1) })).toHaveLength(3);
  });

  test('id 순서 유지·없는 id 건너뜀', () => {
    const byId = new Map(pool.map((i) => [i.id, i]));
    expect(itemsByIds(['q2', 'nope', 'q1', 'q2'], byId).map((i) => i.id)).toEqual(['q2', 'q1']);
  });
});
