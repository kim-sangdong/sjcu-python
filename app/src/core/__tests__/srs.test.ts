import { dueCards, intervalCap, newCard, nextEf, review, RELEARN_DELAY_MS } from '../srs';

const DAY = 24 * 60 * 60 * 1000;
const T0 = Date.UTC(2026, 9, 10, 0, 0, 0);

describe('SM-2 변형', () => {
  test('EF 공식: q=4 는 그대로, q=1 은 0.54 감소, 1.3 아래로 내려가지 않음', () => {
    expect(nextEf(2.5, 4)).toBeCloseTo(2.5, 3);
    expect(nextEf(2.5, 3)).toBeCloseTo(2.36, 3);
    expect(nextEf(2.5, 1)).toBeCloseTo(1.96, 3);
    expect(nextEf(1.4, 1)).toBe(1.3);
  });

  test('정답 연속: 1일 → 6일 → 6×EF', () => {
    let c = newCard('a', T0);
    c = review(c, 'right', T0);
    expect(c.reps).toBe(1);
    expect(c.intervalDays).toBe(1);
    expect(c.dueAt).toBe(T0 + DAY);
    c = review(c, 'right', c.dueAt);
    expect(c.intervalDays).toBe(6);
    c = review(c, 'right', c.dueAt);
    expect(c.intervalDays).toBe(Math.round(6 * c.ef));
    expect(c.lastWrong).toBe(false);
  });

  test('오답: 반복 0, 10분 뒤 재출제, 실수 횟수 증가', () => {
    let c = review(newCard('a', T0), 'right', T0);
    c = review(c, 'right', T0 + DAY);
    const w = review(c, 'wrong', T0 + 2 * DAY);
    expect(w.reps).toBe(0);
    expect(w.lapses).toBe(1);
    expect(w.dueAt).toBe(T0 + 2 * DAY + RELEARN_DELAY_MS);
    expect(w.lastWrong).toBe(true);
    expect(w.ef).toBeLessThan(c.ef);
  });

  test('헷갈림(unsure)은 간격을 유지하되 EF 를 낮춘다', () => {
    const c = review(newCard('a', T0), 'unsure', T0);
    expect(c.reps).toBe(1);
    expect(c.ef).toBeLessThan(2.5);
  });

  test('시험일 상한: 남은 일수의 절반을 넘지 않음', () => {
    expect(intervalCap({ daysToExam: 21 })).toBe(10);
    expect(intervalCap({ daysToExam: 1 })).toBe(1);
    expect(intervalCap({ daysToExam: -3 })).toBe(180);
    expect(intervalCap({ daysToExam: null })).toBe(180);
    let c = newCard('a', T0);
    for (let i = 0; i < 6; i++) c = review(c, 'right', c.dueAt, { daysToExam: 8 });
    expect(c.intervalDays).toBeLessThanOrEqual(4);
  });

  test('복습 대기열: 기한 지난 것만, 오래 밀린 순', () => {
    const a = { ...review(newCard('a', T0), 'right', T0), dueAt: T0 + 5 };
    const b = { ...review(newCard('b', T0), 'wrong', T0), dueAt: T0 + 1 };
    const fresh = newCard('c', T0); // 한 번도 안 푼 카드는 제외
    const later = { ...a, itemId: 'd', dueAt: T0 + DAY };
    expect(dueCards([a, b, fresh, later], T0 + 10).map((c) => c.itemId)).toEqual(['b', 'a']);
  });
});
