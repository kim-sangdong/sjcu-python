/**
 * 익명 식별자와 이벤트 기록(app_events). 개인정보는 보내지 않는다.
 * app_user_id = RevenueCat appUserID(익명). RevenueCat 키가 없는 개발 환경에서는 기기 로컬 UUID.
 *
 * 이벤트 이름: session_start, paywall_view, purchase_success(클라이언트 관측), deck_open, report,
 *             answers_batch(20개씩 {id, correct}), attribution_survey, grading_open
 */
import * as Application from 'expo-application';
import * as Crypto from 'expo-crypto';
import { Platform } from 'react-native';

import { pushAnswer, type AnswerMark } from '@/core/outbox';
import { getKV, setKV } from './repo';
import { enqueue, flushOutbox } from './outboxStore';
import { getAppUserId } from './purchases';

export const KV_APP_USER_ID = 'app_user_id';
export const KV_LOCAL_ID = 'local_user_id';
const KV_ANSWER_BUFFER = 'answers_buffer';

let appUserId: string | null = null;
let packVersion = '';
let flushTimer: ReturnType<typeof setTimeout> | null = null;

export const APP_VERSION = Application.nativeApplicationVersion ?? '0.0.0';

export async function resolveIdentity(): Promise<string> {
  const rcId = await getAppUserId();
  if (rcId) {
    appUserId = rcId;
  } else {
    let local = await getKV<string | null>(KV_LOCAL_ID, null);
    if (!local) {
      local = Crypto.randomUUID();
      await setKV(KV_LOCAL_ID, local);
    }
    appUserId = `local:${local}`;
  }
  await setKV(KV_APP_USER_ID, appUserId);
  return appUserId;
}

export function currentUserId(): string | null {
  return appUserId;
}

export function setPackVersion(v: string): void {
  packVersion = v;
}

function scheduleFlush(delayMs = 5000): void {
  if (flushTimer) return;
  flushTimer = setTimeout(() => {
    flushTimer = null;
    void flushOutbox();
  }, delayMs);
}

export async function track(name: string, props: Record<string, unknown> = {}): Promise<void> {
  if (!appUserId) return;
  try {
    await enqueue('app_events', {
      app_user_id: appUserId,
      name,
      props: { ...props, ts: Date.now(), app_version: APP_VERSION, os: Platform.OS, pack_version: packVersion },
    });
    scheduleFlush();
  } catch (e) {
    if (__DEV__) console.warn('[telemetry] track 실패', name, e);
  }
}

/** 응답 하나를 묶음 버퍼에 넣고 20개가 차면 answers_batch 로 보낸다 */
export async function trackAnswer(mark: AnswerMark): Promise<void> {
  const buffer = await getKV<AnswerMark[]>(KV_ANSWER_BUFFER, []);
  const res = pushAnswer(buffer, mark);
  await setKV(KV_ANSWER_BUFFER, res.buffer);
  for (const batch of res.batches) await track('answers_batch', { items: batch });
}

export async function installRow(extra: Record<string, unknown>): Promise<void> {
  if (!appUserId) return;
  await enqueue('app_installs', { app_user_id: appUserId, os: Platform.OS, app_version: APP_VERSION, ...extra });
  scheduleFlush(1000);
}
