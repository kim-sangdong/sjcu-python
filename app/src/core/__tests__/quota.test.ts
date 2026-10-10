import { consume, currentQuota, displayStreak, remaining, touchStreak } from '../quota';
import { kstDateKey, nextKstMidnight } from '../time';

// 2026-10-10 23:59:59 KST = 2026-10-10 14:59:59 UTC
const BEFORE_MIDNIGHT = Date.UTC(2026, 9, 10, 14, 59, 59);
const AFTER_MIDNIGHT = Date.UTC(2026, 9, 10, 15, 0, 0);

describe('KST 날짜', () => {
  test('UTC 15:00 이 KST 자정', () => {
    expect(kstDateKey(BEFORE_MIDNIGHT)).toBe('2026-10-10');
    expect(kstDateKey(AFTER_MIDNIGHT)).toBe('2026-10-11');
    expect(nextKstMidnight(BEFORE_MIDNIGHT)).toBe(AFTER_MIDNIGHT);
  });
});

describe('무료 일일 한도', () => {
  test('20문항까지 허용, 21번째 거부', () => {
    let s = currentQuota(null, BEFORE_MIDNIGHT);
    for (let i = 0; i < 20; i++) {
      const r = consume(s, BEFORE_MIDNIGHT, { premium: false });
      expect(r.allowed).toBe(true);
      s = r.state;
    }
    expect(remaining(s, BEFORE_MIDNIGHT)).toBe(0);
    const r = consume(s, BEFORE_MIDNIGHT, { premium: false });
    expect(r.allowed).toBe(false);
    expect(r.state.used).toBe(20);
  });

  test('KST 자정이 지나면 0으로 초기화', () => {
    const s = { date: '2026-10-10', used: 20 };
    expect(remaining(s, AFTER_MIDNIGHT)).toBe(20);
    const r = consume(s, AFTER_MIDNIGHT, { premium: false });
    expect(r).toEqual({ allowed: true, state: { date: '2026-10-11', used: 1 } });
  });

  test('기기 시간대와 무관(UTC 날짜가 같아도 KST 로 판정)', () => {
    // UTC 로는 같은 10-10 이지만 KST 로는 다른 날
    const s = { date: kstDateKey(Date.UTC(2026, 9, 10, 1, 0)), used: 20 };
    expect(remaining(s, Date.UTC(2026, 9, 10, 16, 0))).toBe(20);
  });

  test('유료는 소비하지 않고 항상 허용', () => {
    const s = { date: '2026-10-10', used: 20 };
    const r = consume(s, BEFORE_MIDNIGHT, { premium: true });
    expect(r.allowed).toBe(true);
    expect(r.state.used).toBe(20);
  });

  test('한도 값을 바꿀 수 있음', () => {
    const r = consume({ date: '2026-10-10', used: 5 }, BEFORE_MIDNIGHT, { premium: false, limit: 5 });
    expect(r.allowed).toBe(false);
  });
});

describe('연속 학습', () => {
  const day = (d: number) => Date.UTC(2026, 9, d, 3, 0); // KST 정오
  test('어제 이어서 하면 +1, 같은 날 여러 번은 그대로, 하루 빠지면 1부터', () => {
    let s = touchStreak(null, day(1));
    expect(s.count).toBe(1);
    s = touchStreak(s, day(1));
    expect(s.count).toBe(1);
    s = touchStreak(s, day(2));
    s = touchStreak(s, day(3));
    expect(s.count).toBe(3);
    expect(s.best).toBe(3);
    s = touchStreak(s, day(5));
    expect(s.count).toBe(1);
    expect(s.best).toBe(3);
  });
  test('표시: 어제까지 했으면 유지, 이틀 이상 쉬면 0', () => {
    const s = { last: '2026-10-02', count: 4, best: 4 };
    expect(displayStreak(s, day(3))).toBe(4);
    expect(displayStreak(s, day(4))).toBe(0);
  });
});
