/**
 * 무료 일일 한도(기본 20문항)와 연속 학습일 계산. 날짜는 KST 기준이고 자정에 초기화된다.
 * 상태는 작은 객체로 저장소(SQLite kv)에 그대로 저장한다.
 */
import { addDaysKey, kstDateKey } from './time';

export const FREE_DAILY_LIMIT = 20;

export interface QuotaState {
  /** 이 카운터가 속한 KST 날짜 */
  date: string;
  used: number;
}

/** 날짜가 바뀌었으면 0으로 초기화한 상태를 돌려준다. */
export function currentQuota(state: QuotaState | null | undefined, nowMs: number): QuotaState {
  const today = kstDateKey(nowMs);
  if (!state || state.date !== today) return { date: today, used: 0 };
  return { date: state.date, used: Math.max(0, Math.floor(state.used)) };
}

export function remaining(
  state: QuotaState | null | undefined,
  nowMs: number,
  limit = FREE_DAILY_LIMIT,
): number {
  return Math.max(0, limit - currentQuota(state, nowMs).used);
}

/**
 * 한 문항을 소비한다. 유료 사용자는 소비하지 않고 항상 허용.
 * 한도를 넘으면 allowed=false 이고 상태는 바뀌지 않는다.
 */
export function consume(
  state: QuotaState | null | undefined,
  nowMs: number,
  opts: { premium: boolean; limit?: number },
): { state: QuotaState; allowed: boolean } {
  const cur = currentQuota(state, nowMs);
  if (opts.premium) return { state: cur, allowed: true };
  const limit = opts.limit ?? FREE_DAILY_LIMIT;
  if (cur.used >= limit) return { state: cur, allowed: false };
  return { state: { date: cur.date, used: cur.used + 1 }, allowed: true };
}

export interface StreakState {
  /** 마지막으로 학습한 KST 날짜 */
  last: string | null;
  count: number;
  best: number;
}

/** 오늘 학습했음을 기록한다. 어제 이어서 했으면 +1, 하루 이상 빠졌으면 1부터. */
export function touchStreak(state: StreakState | null | undefined, nowMs: number): StreakState {
  const today = kstDateKey(nowMs);
  const s: StreakState = state ?? { last: null, count: 0, best: 0 };
  if (s.last === today) return s;
  const count = s.last && addDaysKey(s.last, 1) === today ? s.count + 1 : 1;
  return { last: today, count, best: Math.max(s.best, count) };
}

/** 화면 표시용: 어제·오늘 학습했으면 이어지는 중, 아니면 0 */
export function displayStreak(state: StreakState | null | undefined, nowMs: number): number {
  if (!state || !state.last) return 0;
  const today = kstDateKey(nowMs);
  if (state.last === today || addDaysKey(state.last, 1) === today) return state.count;
  return 0;
}
