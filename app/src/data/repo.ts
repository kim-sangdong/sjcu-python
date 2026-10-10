/**
 * SQLite 저장소 함수 모음. 화면은 이 함수만 쓰고 SQL 을 직접 쓰지 않는다.
 */
import type { SrsCard } from '@/core/srs';
import { getDb } from './db';

// ---------- kv ----------

export async function getKV<T>(key: string, fallback: T): Promise<T> {
  const db = await getDb();
  const row = await db.getFirstAsync<{ value: string }>('SELECT value FROM kv WHERE key = ?', key);
  if (!row) return fallback;
  try {
    return JSON.parse(row.value) as T;
  } catch {
    return fallback;
  }
}

export async function setKV(key: string, value: unknown): Promise<void> {
  const db = await getDb();
  await db.runAsync(
    'INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',
    key,
    JSON.stringify(value),
  );
}

export async function deleteKVPrefix(prefix: string): Promise<void> {
  const db = await getDb();
  await db.runAsync("DELETE FROM kv WHERE key LIKE ? ESCAPE '\\'", prefix.replace(/[%_\\]/g, '\\$&') + '%');
}

// ---------- 응답 이력 ----------

export async function recordAnswer(a: {
  itemId: string;
  correct: boolean;
  mode: string;
  packVersion: string;
  at: number;
}): Promise<void> {
  const db = await getDb();
  await db.runAsync(
    'INSERT INTO answers (item_id, correct, mode, answered_at, pack_version) VALUES (?, ?, ?, ?, ?)',
    a.itemId,
    a.correct ? 1 : 0,
    a.mode,
    a.at,
    a.packVersion,
  );
}

/** 약점 진단용: 최근 응답 최대 n개 */
export async function getAnswerRows(limit = 5000): Promise<{ itemId: string; correct: boolean }[]> {
  const db = await getDb();
  const rows = await db.getAllAsync<{ item_id: string; correct: number }>(
    'SELECT item_id, correct FROM answers ORDER BY answered_at DESC LIMIT ?',
    limit,
  );
  return rows.map((r) => ({ itemId: r.item_id, correct: r.correct === 1 }));
}

export async function getAnsweredIds(): Promise<Set<string>> {
  const db = await getDb();
  const rows = await db.getAllAsync<{ item_id: string }>('SELECT DISTINCT item_id FROM answers');
  return new Set(rows.map((r) => r.item_id));
}

export async function countAnswersSince(sinceMs: number): Promise<{ total: number; correct: number }> {
  const db = await getDb();
  const row = await db.getFirstAsync<{ total: number; correct: number | null }>(
    'SELECT COUNT(*) AS total, SUM(correct) AS correct FROM answers WHERE answered_at >= ?',
    sinceMs,
  );
  return { total: row?.total ?? 0, correct: row?.correct ?? 0 };
}

// ---------- SRS ----------

interface SrsRow {
  item_id: string;
  ef: number;
  reps: number;
  interval_days: number;
  due_at: number;
  lapses: number;
  last_reviewed_at: number | null;
  last_wrong: number;
}

function toCard(r: SrsRow): SrsCard {
  return {
    itemId: r.item_id,
    ef: r.ef,
    reps: r.reps,
    intervalDays: r.interval_days,
    dueAt: r.due_at,
    lapses: r.lapses,
    lastReviewedAt: r.last_reviewed_at,
    lastWrong: r.last_wrong === 1,
  };
}

export async function getCard(itemId: string): Promise<SrsCard | null> {
  const db = await getDb();
  const r = await db.getFirstAsync<SrsRow>('SELECT * FROM srs WHERE item_id = ?', itemId);
  return r ? toCard(r) : null;
}

export async function saveCard(c: SrsCard): Promise<void> {
  const db = await getDb();
  await db.runAsync(
    `INSERT INTO srs (item_id, ef, reps, interval_days, due_at, lapses, last_reviewed_at, last_wrong)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?)
     ON CONFLICT(item_id) DO UPDATE SET ef = excluded.ef, reps = excluded.reps,
       interval_days = excluded.interval_days, due_at = excluded.due_at, lapses = excluded.lapses,
       last_reviewed_at = excluded.last_reviewed_at, last_wrong = excluded.last_wrong`,
    c.itemId,
    c.ef,
    c.reps,
    c.intervalDays,
    c.dueAt,
    c.lapses,
    c.lastReviewedAt,
    c.lastWrong ? 1 : 0,
  );
}

export async function getDueIds(nowMs: number, limit = 200): Promise<string[]> {
  const db = await getDb();
  const rows = await db.getAllAsync<{ item_id: string }>(
    'SELECT item_id FROM srs WHERE last_reviewed_at IS NOT NULL AND due_at <= ? ORDER BY due_at ASC, lapses DESC LIMIT ?',
    nowMs,
    limit,
  );
  return rows.map((r) => r.item_id);
}

export async function countDue(nowMs: number): Promise<number> {
  const db = await getDb();
  const r = await db.getFirstAsync<{ n: number }>(
    'SELECT COUNT(*) AS n FROM srs WHERE last_reviewed_at IS NOT NULL AND due_at <= ?',
    nowMs,
  );
  return r?.n ?? 0;
}

/** 오답노트: 한 번이라도 틀린 문항(최근 틀린 순) */
export async function getWrongCards(limit = 500): Promise<SrsCard[]> {
  const db = await getDb();
  const rows = await db.getAllAsync<SrsRow>(
    'SELECT * FROM srs WHERE lapses > 0 ORDER BY last_wrong DESC, last_reviewed_at DESC LIMIT ?',
    limit,
  );
  return rows.map(toCard);
}

export async function getWrongIdSet(): Promise<Set<string>> {
  const db = await getDb();
  const rows = await db.getAllAsync<{ item_id: string }>('SELECT item_id FROM srs WHERE last_wrong = 1');
  return new Set(rows.map((r) => r.item_id));
}

// ---------- 모의고사 ----------

export interface MockResultRow {
  id: number;
  setCode: string;
  part: number;
  startedAt: number;
  finishedAt: number;
  total: number;
  correct: number;
  score100: number;
  belowFail: boolean;
  detail: { itemIds: string[]; results: Record<string, boolean | null> };
}

export async function saveMockResult(r: Omit<MockResultRow, 'id'>): Promise<number> {
  const db = await getDb();
  const res = await db.runAsync(
    `INSERT INTO mock_results (set_code, part, started_at, finished_at, total, correct, score100, below_fail, detail)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    r.setCode,
    r.part,
    r.startedAt,
    r.finishedAt,
    r.total,
    r.correct,
    r.score100,
    r.belowFail ? 1 : 0,
    JSON.stringify(r.detail),
  );
  return res.lastInsertRowId;
}

export async function listMockResults(limit = 30): Promise<MockResultRow[]> {
  const db = await getDb();
  const rows = await db.getAllAsync<{
    id: number;
    set_code: string;
    part: number;
    started_at: number;
    finished_at: number;
    total: number;
    correct: number;
    score100: number;
    below_fail: number;
    detail: string;
  }>('SELECT * FROM mock_results ORDER BY finished_at DESC LIMIT ?', limit);
  return rows.map((r) => {
    let detail: MockResultRow['detail'] = { itemIds: [], results: {} };
    try {
      detail = JSON.parse(r.detail);
    } catch {
      /* noop */
    }
    return {
      id: r.id,
      setCode: r.set_code,
      part: r.part,
      startedAt: r.started_at,
      finishedAt: r.finished_at,
      total: r.total,
      correct: r.correct,
      score100: r.score100,
      belowFail: r.below_fail === 1,
      detail,
    };
  });
}

// ---------- 초기화 ----------

/** 학습 기록·설정을 지운다. 설치 보고 여부와 익명 ID 는 남긴다(중복 설치 집계 방지). */
export async function resetLearningData(keepKeys: string[]): Promise<void> {
  const db = await getDb();
  await db.withTransactionAsync(async () => {
    await db.execAsync('DELETE FROM answers; DELETE FROM srs; DELETE FROM mock_results;');
    const placeholders = keepKeys.map(() => '?').join(',');
    if (keepKeys.length) await db.runAsync(`DELETE FROM kv WHERE key NOT IN (${placeholders})`, ...keepKeys);
    else await db.execAsync('DELETE FROM kv;');
  });
}
