/**
 * 한국 표준시(KST, UTC+9) 날짜 계산. 한국은 일광절약시간이 없어 고정 오프셋으로 충분하다.
 * 기기 시간대와 상관없이 "오늘"은 KST 기준이다(무료 한도는 KST 자정에 초기화).
 */
export const KST_OFFSET_MS = 9 * 60 * 60 * 1000;
export const DAY_MS = 24 * 60 * 60 * 1000;

/** 'YYYY-MM-DD' (KST) */
export function kstDateKey(nowMs: number): string {
  const d = new Date(nowMs + KST_OFFSET_MS);
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, '0');
  const day = String(d.getUTCDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

/** KST 날짜 'YYYY-MM-DD' 의 0시 시각(ms, UTC 기준 epoch) */
export function kstMidnightOf(dateKey: string): number {
  const [y, m, d] = dateKey.split('-').map(Number) as [number, number, number];
  return Date.UTC(y, m - 1, d) - KST_OFFSET_MS;
}

/** 다음 KST 자정(ms) */
export function nextKstMidnight(nowMs: number): number {
  return kstMidnightOf(kstDateKey(nowMs)) + DAY_MS;
}

/** 두 KST 날짜 사이 일수(b - a) */
export function daysBetween(a: string, b: string): number {
  return Math.round((kstMidnightOf(b) - kstMidnightOf(a)) / DAY_MS);
}

export function addDaysKey(dateKey: string, days: number): string {
  return kstDateKey(kstMidnightOf(dateKey) + days * DAY_MS);
}

/** '2026-10-31' → '2026.10.31' */
export function dotDate(dateKey: string | undefined | null): string {
  if (!dateKey) return '';
  const [y, m, d] = dateKey.split('-');
  if (!y || !m || !d) return dateKey;
  return `${y}.${Number(m)}.${Number(d)}`;
}

const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토'];
/** '2026-10-31' → '2026.10.31(토)' */
export function dotDateWithDay(dateKey: string): string {
  const d = new Date(kstMidnightOf(dateKey) + KST_OFFSET_MS);
  return `${dotDate(dateKey)}(${WEEKDAYS[d.getUTCDay()]})`;
}

/** 남은 시간 'H:MM:SS' 또는 'M:SS' */
export function formatClock(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const ss = String(s).padStart(2, '0');
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
}
