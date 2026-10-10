/**
 * 시험 당일 가채점. 정답 번호만 쓰고 문항 원문은 쓰지 않는다(콘텐츠 규칙 2).
 * 가답안 공개 전에는 정답을 단정하지 않는다(규칙 3) → 'waiting' 상태에서는 입력만 받는다.
 */
import type { GradingKey, GradingSubject } from './pack/types';
import { kstDateKey } from './time';

export type GradingStatus = 'before' | 'waiting' | 'open';

/**
 * before:  시험일 전(화면은 안내만)
 * waiting: 시험일 이후, 가답안이 아직 매니페스트에 없음(답안 입력·저장만)
 * open:    가답안/확정답안이 있어 채점 가능
 */
export function gradingStatus(
  nowMs: number,
  examDate: string | null | undefined,
  key: GradingKey | null | undefined,
  round: number,
): GradingStatus {
  const today = kstDateKey(nowMs);
  if (key && key.round === round && key.sessions.length > 0 && today >= key.exam_date) return 'open';
  if (!examDate || today < examDate) return 'before';
  return 'waiting';
}

export function keyLabel(key: GradingKey): string {
  return key.status === 'final' ? '확정답안 기준' : '가답안 기준';
}

export type Mark = 'correct' | 'wrong' | 'blank';

export interface SheetResult {
  total: number;
  correct: number;
  score100: number;
  marks: Mark[];
}

/** 정답 하나 판정: 0 = 전항 정답, 배열 = 복수 정답 */
export function isKeyMatch(key: number | number[] | undefined, pick: number | null | undefined): boolean {
  if (key === undefined) return false;
  if (key === 0) return true;
  if (pick === null || pick === undefined) return false;
  return Array.isArray(key) ? key.includes(pick) : key === pick;
}

export function gradeSheet(subject: GradingSubject, form: string, picks: (number | null)[]): SheetResult | null {
  const key = subject.answers[form];
  if (!key) return null;
  const total = subject.questions;
  const marks: Mark[] = [];
  let correct = 0;
  for (let i = 0; i < total; i++) {
    const p = picks[i] ?? null;
    const k = key[i];
    if (isKeyMatch(k, p)) {
      correct += 1;
      marks.push('correct');
    } else marks.push(p === null ? 'blank' : 'wrong');
  }
  const score100 = total ? Math.round((correct / total) * 1000) / 10 : 0;
  return { total, correct, score100, marks };
}

/** 저장해 둔 답안 문자열 "12345 0..." ↔ 배열. 0 또는 공백은 미기입 */
export function encodePicks(picks: (number | null)[]): string {
  return picks.map((p) => (p && p >= 1 && p <= 5 ? String(p) : '0')).join('');
}

export function decodePicks(s: string | null | undefined, total: number): (number | null)[] {
  const out: (number | null)[] = [];
  for (let i = 0; i < total; i++) {
    const c = s?.[i];
    const n = c ? Number(c) : 0;
    out.push(n >= 1 && n <= 5 ? n : null);
  }
  return out;
}
