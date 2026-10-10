/**
 * RevenueCat 웹훅 → purchases 테이블.
 *
 *   POST /functions/v1/revenuecat-webhook
 *   Authorization: <RevenueCat 대시보드에 넣은 값 그대로>   (== 함수 비밀값 REVENUECAT_WEBHOOK_AUTH, 상수 시간 비교)
 *   본문: {"api_version": "1.0", "event": {...}}
 *
 * - event.id 로 멱등 처리(purchases.event_id unique, 겹치면 무시) → 같은 이벤트를 다시 받아도 200.
 * - DB 기록이 실패하면 500 을 돌려 RevenueCat 이 다시 보내게 한다.
 * - 금액: RevenueCat 의 price 는 USD, price_in_purchased_currency 는 구매 통화 금액(currency) [3rd-party·기억, 공식 문서 미확인]
 *   → purchases.price_in_usd = event.price, purchases.price = event.price_in_purchased_currency, purchases.currency = event.currency
 * - raw 에 event 전체를 넣는다(분석이 raw->>'is_trial_conversion', raw->>'cancel_reason' 을 읽는다).
 */

import { type EnvGet, insertRow, serviceKey, timingSafeEqual } from "../_shared/rest.ts";

// ---------------------------------------------------------------- 상수

export const AUTH_ENV = "REVENUECAT_WEBHOOK_AUTH";
export const PURCHASES_TABLE = "purchases";
export const MAX_BODY_BYTES = 256 * 1024;
// 이벤트 필드 이름(docs/research/facts-app-stack.md 2-3 — 3rd-party 처리기 기준, 공식 문서와 대조 못 함)
export const F = {
  id: "id",
  type: "type",
  appUserId: "app_user_id",
  originalAppUserId: "original_app_user_id",
  transferredTo: "transferred_to",
  productId: "product_id",
  periodType: "period_type",
  priceUsd: "price",
  priceLocal: "price_in_purchased_currency",
  currency: "currency",
  store: "store",
  environment: "environment",
  eventTimestampMs: "event_timestamp_ms",
  purchasedAtMs: "purchased_at_ms",
} as const;

// ---------------------------------------------------------------- 변환

function str(v: unknown, max: number): string | null {
  if (typeof v !== "string") return null;
  const t = v.trim();
  return t ? t.slice(0, max) : null;
}

function num(v: unknown): number | null {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

const MIN_MS = Date.UTC(2000, 0, 1);
const MAX_MS = Date.UTC(2100, 0, 1);

export function msToIso(v: unknown): string | null {
  const n = num(v);
  if (n === null || n < MIN_MS || n > MAX_MS) return null;
  return new Date(n).toISOString();
}

/** event → purchases 행. id 가 없으면 null(받을 수 없는 본문). */
export function toPurchaseRow(event: Record<string, unknown>): Record<string, unknown> | null {
  const eventId = str(event[F.id], 200);
  if (!eventId) return null;
  const transferred = Array.isArray(event[F.transferredTo]) ? (event[F.transferredTo] as unknown[]) : [];
  const appUserId = str(event[F.appUserId], 200) ?? str(event[F.originalAppUserId], 200) ?? str(transferred[0], 200);
  const currency = str(event[F.currency], 8)?.toUpperCase() ?? null;
  const priceUsd = num(event[F.priceUsd]);
  let price = num(event[F.priceLocal]);
  if (price === null && currency === "USD") price = priceUsd;
  return {
    event_id: eventId,
    event_type: str(event[F.type], 64)?.toUpperCase() ?? null,
    app_user_id: appUserId,
    product_id: str(event[F.productId], 200),
    period_type: str(event[F.periodType], 32)?.toUpperCase() ?? null,
    price,
    currency,
    price_in_usd: priceUsd,
    store: str(event[F.store], 32),
    environment: str(event[F.environment], 16)?.toUpperCase() ?? null,
    event_timestamp: msToIso(event[F.eventTimestampMs]) ?? msToIso(event[F.purchasedAtMs]),
    raw: event,
  };
}

/** Authorization 헤더 확인. 대시보드 값 그대로가 기본이고, 앞에 "Bearer " 를 붙여 보내도 받아 준다. */
export async function isAuthorized(header: string | null, secret: string): Promise<boolean> {
  const got = (header ?? "").trim();
  const exact = await timingSafeEqual(got, secret);
  const bearer = await timingSafeEqual(got, `Bearer ${secret}`);
  return exact || bearer;
}

// ---------------------------------------------------------------- 처리기

export interface WebhookDeps {
  env: EnvGet;
  fetchFn?: typeof fetch;
  log?: (msg: string) => void;
}

function json(status: number, body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
}

export async function handleWebhook(req: Request, deps: WebhookDeps): Promise<Response> {
  const log = deps.log ?? console.error;
  if (req.method !== "POST") {
    return new Response("Method Not Allowed", { status: 405, headers: { Allow: "POST" } });
  }
  const secret = deps.env(AUTH_ENV)?.trim();
  if (!secret) {
    // 설정 전에는 아무 요청도 받지 않는다. 500 이면 RevenueCat 이 나중에 다시 보낸다.
    log(`[revenuecat] ${AUTH_ENV} 가 설정되지 않았습니다(supabase secrets set ${AUTH_ENV}=...).`);
    return json(500, { ok: false, error: "not_configured" });
  }
  if (!(await isAuthorized(req.headers.get("authorization"), secret))) {
    return json(401, { ok: false, error: "unauthorized" });
  }

  const declared = Number(req.headers.get("content-length") ?? "0");
  if (declared > MAX_BODY_BYTES) return json(413, { ok: false, error: "too_large" });
  const buf = await req.arrayBuffer();
  if (buf.byteLength > MAX_BODY_BYTES) return json(413, { ok: false, error: "too_large" });

  let body: unknown;
  try {
    body = JSON.parse(new TextDecoder().decode(buf));
  } catch {
    return json(400, { ok: false, error: "invalid_json" });
  }
  const event = (body as { event?: unknown } | null)?.event;
  if (!event || typeof event !== "object" || Array.isArray(event)) {
    return json(400, { ok: false, error: "missing_event" });
  }
  const row = toPurchaseRow(event as Record<string, unknown>);
  if (!row) return json(400, { ok: false, error: "missing_event_id" });

  const url = deps.env("SUPABASE_URL");
  const key = serviceKey(deps.env);
  if (!url || !key) {
    log("[revenuecat] SUPABASE_URL 또는 서버 키가 없어 기록하지 못했습니다.");
    return json(500, { ok: false, error: "storage_not_configured" });
  }
  try {
    await insertRow({ url, key, table: PURCHASES_TABLE, row, onConflict: "event_id", fetchFn: deps.fetchFn });
  } catch (e) {
    log(`[revenuecat] ${row.event_id} 기록 실패: ${e instanceof Error ? e.message : String(e)}`);
    return json(500, { ok: false, error: "db_error" });
  }
  log(`[revenuecat] 기록 ${row.event_type ?? "?"} ${row.event_id} (${row.environment ?? "?"})`);
  return json(200, { ok: true, event_id: row.event_id });
}
