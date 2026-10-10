/**
 * 오답 간격 반복(SRS) — SM-2 변형.
 *
 * 근거: P. A. Wozniak, "Optimization of learning" (1990), SuperMemo SM-2 알고리즘.
 *   EF' = EF + (0.1 - (5 - q) × (0.08 + (5 - q) × 0.02)),  EF ≥ 1.3
 *   q < 3  → 반복 횟수 0으로 되돌리고 처음부터(간격 1일)
 *   q ≥ 3  → 간격 I(1)=1일, I(2)=6일, I(n)=I(n-1)×EF
 *
 * 이 앱에서 바꾼 점(이유):
 * 1. 채점형 문제라 사용자가 0~5점을 직접 매기지 않는다. 정답·오답과 "헷갈림" 표시로 q 를 정한다.
 *    오답 → q=1, 정답이지만 헷갈림 → q=3, 정답 → q=4.  (q=5 는 쓰지 않아 EF 가 과하게 커지지 않게 한다)
 * 2. 틀린 문항은 10분 뒤 한 번 더(같은 학습 묶음 안에서 재확인) 나오게 한다. SM-2 의 "q<4 인 항목은
 *    그날 다시 복습" 규칙을 단순화한 것이다.
 * 3. 시험일이 정해져 있으므로 간격이 시험일을 넘지 않게 자른다(남은 일수의 절반, 최소 1일).
 *    시험 뒤로 미뤄진 복습은 쓸모가 없기 때문이다.
 */

export const MIN_EF = 1.3;
export const INITIAL_EF = 2.5;
export const RELEARN_DELAY_MS = 10 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;

export interface SrsCard {
  itemId: string;
  ef: number;
  /** 연속 성공 횟수 */
  reps: number;
  intervalDays: number;
  dueAt: number;
  lapses: number;
  lastReviewedAt: number | null;
  /** 마지막 응답이 오답이었는지(오답노트 표시용) */
  lastWrong: boolean;
}

export type Grade = 'wrong' | 'unsure' | 'right';

export function gradeToQuality(g: Grade): number {
  return g === 'wrong' ? 1 : g === 'unsure' ? 3 : 4;
}

export function newCard(itemId: string, nowMs: number): SrsCard {
  return {
    itemId,
    ef: INITIAL_EF,
    reps: 0,
    intervalDays: 0,
    dueAt: nowMs,
    lapses: 0,
    lastReviewedAt: null,
    lastWrong: false,
  };
}

export function nextEf(ef: number, q: number): number {
  const v = ef + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02));
  return Math.max(MIN_EF, Math.round(v * 1000) / 1000);
}

export interface ReviewOptions {
  /** 시험까지 남은 일수(없거나 지났으면 상한 없음) */
  daysToExam?: number | null;
  /** 상한(기본 180일) */
  maxIntervalDays?: number;
}

export function intervalCap(opts: ReviewOptions): number {
  const max = opts.maxIntervalDays ?? 180;
  const d = opts.daysToExam;
  if (d === null || d === undefined || d <= 0) return max;
  return Math.max(1, Math.min(max, Math.floor(d / 2)));
}

/** 응답 하나를 반영한 새 카드 */
export function review(card: SrsCard, grade: Grade, nowMs: number, opts: ReviewOptions = {}): SrsCard {
  const q = gradeToQuality(grade);
  const ef = nextEf(card.ef, q);
  if (q < 3) {
    return {
      ...card,
      ef,
      reps: 0,
      intervalDays: 0,
      dueAt: nowMs + RELEARN_DELAY_MS,
      lapses: card.lapses + 1,
      lastReviewedAt: nowMs,
      lastWrong: true,
    };
  }
  const reps = card.reps + 1;
  let interval: number;
  if (reps === 1) interval = 1;
  else if (reps === 2) interval = 6;
  else interval = Math.round(Math.max(1, card.intervalDays) * ef);
  interval = Math.min(interval, intervalCap(opts));
  return {
    ...card,
    ef,
    reps,
    intervalDays: interval,
    dueAt: nowMs + interval * DAY_MS,
    lastReviewedAt: nowMs,
    lastWrong: false,
  };
}

/** 복습 대기열: 기한이 지난 카드 중 오래 밀린 순, 같은 날이면 실수 많은 순 */
export function dueCards(cards: SrsCard[], nowMs: number, limit = 50): SrsCard[] {
  return cards
    .filter((c) => c.lastReviewedAt !== null && c.dueAt <= nowMs)
    .sort((a, b) => a.dueAt - b.dueAt || b.lapses - a.lapses)
    .slice(0, limit);
}
