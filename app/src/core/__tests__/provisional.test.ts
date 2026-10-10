import type { GradingKey, GradingSubject } from '../pack/types';
import { decodePicks, encodePicks, gradeSheet, gradingStatus, isKeyMatch, keyLabel } from '../provisional';

const subject: GradingSubject = {
  code: 'RE',
  name: '부동산학개론',
  questions: 5,
  answers: { A: [1, 2, 0, [3, 4], 5], B: [5, 4, 3, 2, 1] },
};
const key: GradingKey = {
  round: 37,
  exam_date: '2026-10-31',
  status: 'provisional',
  source_note: '시행기관 공개 가답안',
  sessions: [{ code: 'p1', title: '1차', subjects: [subject] }],
};
const kst = (d: string, h = 12) => Date.parse(`${d}T${String(h).padStart(2, '0')}:00:00+09:00`);

describe('가채점', () => {
  test('상태: 시험일 전 / 당일 정답 전 / 정답 공개', () => {
    expect(gradingStatus(kst('2026-10-30'), '2026-10-31', undefined, 37)).toBe('before');
    expect(gradingStatus(kst('2026-10-31', 0), '2026-10-31', undefined, 37)).toBe('waiting');
    expect(gradingStatus(kst('2026-10-31'), '2026-10-31', key, 37)).toBe('open');
    // 다른 회차 정답은 쓰지 않는다
    expect(gradingStatus(kst('2026-10-31'), '2026-10-31', { ...key, round: 36 }, 37)).toBe('waiting');
    // 시험일 전에는 정답이 들어 있어도 열지 않는다
    expect(gradingStatus(kst('2026-10-30'), '2026-10-31', key, 37)).toBe('before');
  });

  test('전항 정답(0)·복수 정답(배열)·미기입', () => {
    expect(isKeyMatch(0, null)).toBe(true);
    expect(isKeyMatch([3, 4], 4)).toBe(true);
    expect(isKeyMatch([3, 4], 2)).toBe(false);
    expect(isKeyMatch(2, null)).toBe(false);
    expect(isKeyMatch(undefined, 1)).toBe(false);
  });

  test('형별에 맞춰 채점하고 100점 환산', () => {
    const r = gradeSheet(subject, 'A', [1, 3, null, 4, null])!;
    expect(r.correct).toBe(3);
    expect(r.marks).toEqual(['correct', 'wrong', 'correct', 'correct', 'blank']);
    expect(r.score100).toBe(60);
    expect(gradeSheet(subject, 'C', [])).toBeNull();
    expect(keyLabel(key)).toBe('가답안 기준');
    expect(keyLabel({ ...key, status: 'final' })).toBe('확정답안 기준');
  });

  test('답안 문자열 저장 형식', () => {
    expect(encodePicks([1, null, 5, 7 as number])).toBe('1050');
    expect(decodePicks('1050', 6)).toEqual([1, null, 5, null, null, null]);
    expect(decodePicks(null, 2)).toEqual([null, null]);
  });
});
