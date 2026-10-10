import { DEFAULT_RULES, ddayLabel, daysToExam, examRules, freeDailyLimit, isPassOnSale, mockSetsFromPapers, targetExamDate } from '../exam';
import { availableMockSets, buildMock, partAverage, scoreMock } from '../mock';
import type { Item, Manifest } from '../pack/types';
import { mulberry32 } from '../random';

const mk = (id: string, subject: string, type: Item['type'] = 'mcq') =>
  ({ id, subject, type, unit: '', stem: '', answer: 1, choices: ['a', 'b'], explanation: '', trap: '', basis: [], difficulty: 1, free: true }) as Item;

const kst = (d: string, h = 12) => Date.parse(`${d}T${String(h).padStart(2, '0')}:00:00+09:00`);

describe('시험 일정', () => {
  test('D-day 는 KST 날짜 기준', () => {
    expect(daysToExam('2026-10-31', kst('2026-10-10'))).toBe(21);
    expect(ddayLabel('2026-10-31', kst('2026-10-10'))).toBe('D-21');
    expect(ddayLabel('2026-10-31', kst('2026-10-31', 0))).toBe('D-DAY');
    expect(ddayLabel('2026-10-31', kst('2026-11-01'))).toBe('시험 종료');
    expect(ddayLabel(null, kst('2026-10-10'))).toBe('일정 공고 전');
    // UTC 로는 10-30 15:30 이지만 KST 로는 10-31 00:30 → D-DAY
    expect(ddayLabel('2026-10-31', Date.UTC(2026, 9, 30, 15, 30))).toBe('D-DAY');
  });

  test('목표 회차: 매니페스트 회차면 날짜, 다음 회차면 미정', () => {
    const m = { exam: { name: '공인중개사', round: 37, next_exam_date: '2026-10-31' } } as Manifest;
    expect(targetExamDate(m, null)).toBe('2026-10-31');
    expect(targetExamDate(m, 37)).toBe('2026-10-31');
    expect(targetExamDate(m, 38)).toBeNull();
  });

  test('막판 60일 패스는 8월 1일부터 시험일까지만', () => {
    expect(isPassOnSale(kst('2026-07-31'), '2026-10-31')).toBe(false);
    expect(isPassOnSale(kst('2026-08-01', 0), '2026-10-31')).toBe(true);
    expect(isPassOnSale(kst('2026-10-31', 23), '2026-10-31')).toBe(true);
    expect(isPassOnSale(kst('2026-11-01'), '2026-10-31')).toBe(false);
    expect(isPassOnSale(kst('2026-09-01'), null)).toBe(false);
  });

  test('papers → 모의고사 세트(과목당 40문항·50분, 공시법·세법 24·16)', () => {
    expect(DEFAULT_RULES.mock_sets.map((s) => [s.code, s.part, s.minutes])).toEqual([
      ['RE', 1, 50],
      ['CIV', 1, 50],
      ['BRK', 2, 50],
      ['PUB', 2, 50],
      ['REG+TAX', 2, 50],
    ]);
    expect(DEFAULT_RULES.mock_sets[4]!.compose).toEqual([
      { subject: 'REG', count: 24 },
      { subject: 'TAX', count: 16 },
    ]);
    const odd = mockSetsFromPapers([{ name: '합본', stage: 2, subjects: ['A', 'B', 'C'], questions: 40 }]);
    expect(odd[0]!.compose.map((c) => c.count)).toEqual([14, 13, 13]);
    const m = { exam: { name: 'x', round: 1, next_exam_date: '2026-10-31', papers: [{ name: '민법', stage: 1, subjects: ['CIV'], questions: 20 }] } } as Manifest;
    expect(examRules(m).mock_sets).toEqual([{ code: 'CIV', title: '1차 민법', part: 1, minutes: 25, compose: [{ subject: 'CIV', count: 20 }] }]);
    expect(freeDailyLimit({ free_daily: 10 } as Manifest)).toBe(10);
    expect(freeDailyLimit(null)).toBe(20);
  });

  test('시험 규칙: 매니페스트 값이 있으면 덮어쓴다', () => {
    expect(examRules(null)).toEqual(DEFAULT_RULES);
    const m = { extra: { exam_rules: { fail_line: 50 } } } as unknown as Manifest;
    expect(examRules(m).fail_line).toBe(50);
    expect(examRules(m).mock_sets).toBe(DEFAULT_RULES.mock_sets);
  });
});

describe('모의고사', () => {
  const items = [
    ...Array.from({ length: 30 }, (_, i) => mk(`r${i}`, 'REG', i % 3 === 0 ? 'ox' : 'mcq')),
    ...Array.from({ length: 10 }, (_, i) => mk(`t${i}`, 'TAX')),
  ];
  const rule = DEFAULT_RULES.mock_sets.find((s) => s.code === 'REG+TAX')!;

  test('과목 구성대로 뽑고(공시 24 + 세법 16), 5지선다를 먼저 쓴다, 부족하면 기록', () => {
    const b = buildMock(rule, items, mulberry32(1));
    expect(b.itemIds.filter((id) => id.startsWith('r'))).toHaveLength(24);
    expect(b.itemIds.filter((id) => id.startsWith('t'))).toHaveLength(10);
    expect(b.shortages).toEqual([{ subject: 'TAX', wanted: 16, got: 10 }]);
    const ox = items.filter((i) => i.type === 'ox').map((i) => i.id);
    // mcq 20개가 모두 들어가고 ox 는 4개만
    expect(b.itemIds.filter((id) => ox.includes(id))).toHaveLength(4);
    expect(new Set(b.itemIds).size).toBe(b.itemIds.length);
  });

  test('같은 시드면 같은 시험지', () => {
    expect(buildMock(rule, items, mulberry32(7)).itemIds).toEqual(buildMock(rule, items, mulberry32(7)).itemIds);
  });

  test('쓸 수 있는 세트만 보인다', () => {
    expect(availableMockSets(DEFAULT_RULES.mock_sets, items).map((s) => s.code)).toEqual(['REG+TAX']);
  });

  test('채점: 100점 환산, 과락선, 과목별', () => {
    const ids = Array.from({ length: 40 }, (_, i) => `q${i}`);
    const results: Record<string, boolean | null> = {};
    ids.forEach((id, i) => (results[id] = i < 15 ? true : i < 35 ? false : null));
    const s = scoreMock(ids, results, (id) => (Number(id.slice(1)) < 24 ? 'REG' : 'TAX'), 40);
    expect(s.correct).toBe(15);
    expect(s.answered).toBe(35);
    expect(s.score100).toBe(37.5);
    expect(s.belowFailLine).toBe(true);
    expect(s.bySubject).toEqual([
      { subject: 'REG', total: 24, correct: 15 },
      { subject: 'TAX', total: 16, correct: 0 },
    ]);
    expect(scoreMock(ids.slice(0, 16), results, () => 'x', 40).score100).toBe(93.8);
  });

  test('차수 평균(참고용)', () => {
    expect(partAverage([{ part: 1, score100: 70 }, { part: 1, score100: 55 }, { part: 2, score100: 10 }], 1)).toBe(62.5);
    expect(partAverage([], 2)).toBeNull();
  });
});
