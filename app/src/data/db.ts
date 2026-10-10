/**
 * 기기 로컬 학습 기록(expo-sqlite). 서버로 보내지 않는다(익명 집계용 answers_batch 만 별도 전송).
 * 스키마 버전은 PRAGMA user_version 으로 관리하고, 앞으로 바꿀 때는 MIGRATIONS 에 단계만 덧붙인다.
 */
import * as SQLite from 'expo-sqlite';

const DB_NAME = 'hamjeong.db';

const MIGRATIONS: string[] = [
  // v1
  `
  CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY NOT NULL,
    value TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    correct INTEGER NOT NULL,
    mode TEXT NOT NULL,
    answered_at INTEGER NOT NULL,
    pack_version TEXT
  );
  CREATE INDEX IF NOT EXISTS idx_answers_item ON answers(item_id);
  CREATE INDEX IF NOT EXISTS idx_answers_time ON answers(answered_at);
  CREATE TABLE IF NOT EXISTS srs (
    item_id TEXT PRIMARY KEY NOT NULL,
    ef REAL NOT NULL,
    reps INTEGER NOT NULL,
    interval_days INTEGER NOT NULL,
    due_at INTEGER NOT NULL,
    lapses INTEGER NOT NULL,
    last_reviewed_at INTEGER,
    last_wrong INTEGER NOT NULL DEFAULT 0
  );
  CREATE INDEX IF NOT EXISTS idx_srs_due ON srs(due_at);
  CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tbl TEXT NOT NULL,
    payload TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_outbox_next ON outbox(next_at);
  CREATE TABLE IF NOT EXISTS mock_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    set_code TEXT NOT NULL,
    part INTEGER NOT NULL,
    started_at INTEGER NOT NULL,
    finished_at INTEGER NOT NULL,
    total INTEGER NOT NULL,
    correct INTEGER NOT NULL,
    score100 REAL NOT NULL,
    below_fail INTEGER NOT NULL,
    detail TEXT NOT NULL
  );
  `,
];

let dbPromise: Promise<SQLite.SQLiteDatabase> | null = null;

async function migrate(db: SQLite.SQLiteDatabase): Promise<void> {
  await db.execAsync('PRAGMA journal_mode = WAL;');
  const row = await db.getFirstAsync<{ user_version: number }>('PRAGMA user_version');
  let v = row?.user_version ?? 0;
  while (v < MIGRATIONS.length) {
    const sql = MIGRATIONS[v]!;
    await db.withTransactionAsync(async () => {
      await db.execAsync(sql);
    });
    v += 1;
    await db.execAsync(`PRAGMA user_version = ${v}`);
  }
}

export function getDb(): Promise<SQLite.SQLiteDatabase> {
  if (!dbPromise) {
    dbPromise = (async () => {
      const db = await SQLite.openDatabaseAsync(DB_NAME);
      await migrate(db);
      return db;
    })().catch((e) => {
      dbPromise = null;
      throw e;
    });
  }
  return dbPromise;
}
