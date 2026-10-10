/**
 * Supabase REST 전송 큐. 행은 먼저 SQLite outbox 에 넣고, flush() 가 보낸다.
 * anon(공개) 키는 app_installs·app_events 에 INSERT 만 할 수 있다(계약). 그래서 읽기·수정 없이
 * INSERT 만 하고, 같은 설치를 다시 보내 409(고유키 충돌)가 나면 이미 들어간 것으로 본다.
 */
import {
  backoffMs,
  classifyStatus,
  MAX_QUEUE,
  pickColumns,
  shouldGiveUp,
  supabaseHeaders,
  type OutboxTable,
} from '@/core/outbox';
import { ENV } from '@/core/env';
import { getDb } from './db';

const SEND_TIMEOUT_MS = 15_000;
let flushing: Promise<void> | null = null;

export function remoteEnabled(): boolean {
  return Boolean(ENV.supabaseUrl && ENV.supabaseAnonKey);
}

export async function enqueue(table: OutboxTable, row: Record<string, unknown>): Promise<void> {
  if (!remoteEnabled()) return; // 개발 환경(키 없음): 쌓지 않는다
  const db = await getDb();
  const now = Date.now();
  await db.runAsync(
    'INSERT INTO outbox (tbl, payload, attempts, next_at, created_at) VALUES (?, ?, 0, ?, ?)',
    table,
    JSON.stringify(pickColumns(table, row)),
    now,
    now,
  );
  // 무한히 커지지 않게 오래된 것부터 버린다
  await db.runAsync(
    'DELETE FROM outbox WHERE id IN (SELECT id FROM outbox ORDER BY id DESC LIMIT -1 OFFSET ?)',
    MAX_QUEUE,
  );
}

async function post(table: OutboxTable, rows: unknown[]): Promise<number> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), SEND_TIMEOUT_MS);
  try {
    const res = await fetch(`${ENV.supabaseUrl}/rest/v1/${table}`, {
      method: 'POST',
      headers: supabaseHeaders(ENV.supabaseAnonKey!),
      body: JSON.stringify(rows.length === 1 ? rows[0] : rows),
      signal: ctrl.signal,
    });
    return res.status;
  } catch {
    return 0; // 네트워크 오류·시간 초과
  } finally {
    clearTimeout(timer);
  }
}

interface OutRow {
  id: number;
  tbl: OutboxTable;
  payload: string;
  attempts: number;
  created_at: number;
}

async function flushOnce(): Promise<void> {
  if (!remoteEnabled()) return;
  const db = await getDb();
  const now = Date.now();
  const rows = await db.getAllAsync<OutRow>(
    'SELECT id, tbl, payload, attempts, created_at FROM outbox WHERE next_at <= ? ORDER BY id LIMIT 100',
    now,
  );
  if (!rows.length) return;

  const byTable = new Map<OutboxTable, OutRow[]>();
  for (const r of rows) byTable.set(r.tbl, [...(byTable.get(r.tbl) ?? []), r]);

  for (const [table, list] of byTable) {
    // app_installs 는 고유키가 있어 한 행씩(409 를 행 단위로 판정), app_events 는 묶어서 보낸다
    const groups: OutRow[][] = table === 'app_installs' ? list.map((r) => [r]) : [list];
    for (const g of groups) {
      const status = await post(
        table,
        g.map((r) => JSON.parse(r.payload)),
      );
      const outcome = classifyStatus(status);
      if (outcome === 'sent') {
        await db.runAsync(`DELETE FROM outbox WHERE id IN (${g.map(() => '?').join(',')})`, ...g.map((r) => r.id));
      } else if (outcome === 'drop' && g.length > 1) {
        // 묶음 중 한 행만 잘못됐을 수 있다 → 바로 한 행씩 다시 보내 잘못된 행만 버린다
        for (const r of g) await sendSingle(table, r);
      } else {
        for (const r of g) await markFailed(r, outcome, now);
      }
    }
  }
}

async function sendSingle(table: OutboxTable, r: OutRow): Promise<void> {
  const db = await getDb();
  const outcome = classifyStatus(await post(table, [JSON.parse(r.payload)]));
  if (outcome === 'sent') await db.runAsync('DELETE FROM outbox WHERE id = ?', r.id);
  else await markFailed(r, outcome, Date.now());
}

async function markFailed(r: OutRow, outcome: 'retry' | 'drop' | 'sent', now: number): Promise<void> {
  const db = await getDb();
  const attempts = r.attempts + 1;
  if (outcome === 'drop' || shouldGiveUp(attempts, r.created_at, now)) {
    if (__DEV__) console.warn('[outbox] 버림', r.tbl, r.payload.slice(0, 200));
    await db.runAsync('DELETE FROM outbox WHERE id = ?', r.id);
    return;
  }
  await db.runAsync('UPDATE outbox SET attempts = ?, next_at = ? WHERE id = ?', attempts, now + backoffMs(attempts), r.id);
}

/** 동시에 여러 번 불려도 한 번만 돈다 */
export function flushOutbox(): Promise<void> {
  if (!flushing) {
    flushing = flushOnce()
      .catch((e) => {
        if (__DEV__) console.warn('[outbox] flush 실패', e);
      })
      .finally(() => {
        flushing = null;
      });
  }
  return flushing;
}

export async function pendingCount(): Promise<number> {
  const db = await getDb();
  const r = await db.getFirstAsync<{ n: number }>('SELECT COUNT(*) AS n FROM outbox');
  return r?.n ?? 0;
}
