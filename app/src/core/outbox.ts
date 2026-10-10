/**
 * Supabase 로 보내는 행(app_installs, app_events)의 재전송 규칙과 answers_batch 묶음.
 * 네트워크가 실패하면 SQLite outbox 에 남겨 두고 지수 백오프로 다시 보낸다.
 */

export type OutboxTable = 'app_installs' | 'app_events';

export const ALLOWED_COLUMNS: Record<OutboxTable, readonly string[]> = {
  app_installs: [
    'app_user_id',
    'os',
    'utm_source',
    'utm_medium',
    'utm_campaign',
    'utm_content',
    'raw_referrer',
    'app_version',
  ],
  app_events: ['app_user_id', 'name', 'props'],
};

/** 계약에 없는 열을 빼서 INSERT 가 열 오류로 실패하지 않게 한다 */
export function pickColumns(table: OutboxTable, row: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const k of ALLOWED_COLUMNS[table]) if (row[k] !== undefined) out[k] = row[k];
  return out;
}

export type SendOutcome = 'sent' | 'retry' | 'drop';

/**
 * HTTP 상태 → 처리.
 * 2xx: 성공. 409: 고유키 중복(app_installs 재전송) → 이미 들어간 것으로 본다.
 * 408/425/429/5xx/네트워크 오류(0): 다시 보낸다. 그 밖의 4xx: 다시 보내도 같으니 버린다.
 */
export function classifyStatus(status: number): SendOutcome {
  if (status >= 200 && status < 300) return 'sent';
  if (status === 409) return 'sent';
  if (status === 0 || status === 408 || status === 425 || status === 429 || status >= 500) return 'retry';
  return 'drop';
}

export const MAX_ATTEMPTS = 20;
export const MAX_AGE_MS = 14 * 24 * 60 * 60 * 1000;
export const MAX_QUEUE = 2000;

/** 30초부터 2배씩, 최대 1시간. ±20% 흔들림으로 한꺼번에 몰리는 것을 막는다 */
export function backoffMs(attempts: number, rand: () => number = Math.random): number {
  const base = Math.min(60 * 60 * 1000, 30 * 1000 * 2 ** Math.max(0, attempts - 1));
  const jitter = 0.8 + rand() * 0.4;
  return Math.round(base * jitter);
}

export function shouldGiveUp(attempts: number, createdAt: number, nowMs: number): boolean {
  return attempts >= MAX_ATTEMPTS || nowMs - createdAt > MAX_AGE_MS;
}

export interface AnswerMark {
  id: string;
  correct: boolean;
}

export const ANSWERS_BATCH_SIZE = 20;

/**
 * 응답을 버퍼에 더하고, 20개가 차면 묶음을 꺼낸다.
 * 남은 버퍼는 다음 실행까지 저장해 둔다(앱을 꺼도 잃지 않음).
 */
export function pushAnswer(
  buffer: AnswerMark[],
  mark: AnswerMark,
  size = ANSWERS_BATCH_SIZE,
): { buffer: AnswerMark[]; batches: AnswerMark[][] } {
  const all = [...buffer, mark];
  const batches: AnswerMark[][] = [];
  while (all.length >= size) batches.push(all.splice(0, size));
  return { buffer: all, batches };
}

/** Supabase 요청 헤더. 새 publishable 키(sb_publishable_…)는 JWT 가 아니라 apikey 헤더로만 보낸다 */
export function supabaseHeaders(key: string): Record<string, string> {
  const h: Record<string, string> = {
    apikey: key,
    'Content-Type': 'application/json',
    Prefer: 'return=minimal',
  };
  if (key.startsWith('eyJ')) h.Authorization = `Bearer ${key}`;
  return h;
}
