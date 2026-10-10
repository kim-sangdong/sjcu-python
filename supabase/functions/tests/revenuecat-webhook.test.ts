// deno test supabase/functions/tests/
import { strict as assert } from "node:assert";

import { handleWebhook, isAuthorized, msToIso, toPurchaseRow } from "../revenuecat-webhook/logic.ts";
import { timingSafeEqual } from "../_shared/rest.ts";

const SECRET = "rc-webhook-secret-123";
const ENV: Record<string, string> = {
  SUPABASE_URL: "https://ref.supabase.co/",
  SUPABASE_SERVICE_ROLE_KEY: "eyJ.service.key",
  REVENUECAT_WEBHOOK_AUTH: SECRET,
};

const EVENT = {
  id: "CDD0A7E7-2C1A-4E4C-9B7B-1F0E7D2A0001",
  type: "INITIAL_PURCHASE",
  app_id: "app1234",
  app_user_id: "local:7a1c",
  original_app_user_id: "$RCAnonymousID:abc",
  product_id: "premium_monthly",
  period_type: "NORMAL",
  price: 4.12,
  price_in_purchased_currency: 5900,
  currency: "KRW",
  store: "PLAY_STORE",
  environment: "PRODUCTION",
  event_timestamp_ms: 1760083200000,
  purchased_at_ms: 1760083100000,
  is_trial_conversion: false,
  subscriber_attributes: { $mediaSource: { value: "youtube", updated_at_ms: 1760000000000 } },
};

function post(body: unknown, auth: string | null = SECRET, method = "POST") {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (auth !== null) headers.authorization = auth;
  return new Request("https://ref.supabase.co/functions/v1/revenuecat-webhook", {
    method,
    headers,
    body: method === "POST" ? (typeof body === "string" ? body : JSON.stringify(body)) : undefined,
  });
}

function fakeFetch(status = 201) {
  const calls: { url: string; init: RequestInit }[] = [];
  const fn = ((url: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(url), init: init ?? {} });
    return Promise.resolve(new Response(status >= 400 ? '{"message":"boom"}' : null, { status }));
  }) as typeof fetch;
  return { fn, calls };
}

Deno.test("상수 시간 비교", async () => {
  assert.equal(await timingSafeEqual("abc", "abc"), true);
  assert.equal(await timingSafeEqual("abc", "abd"), false);
  assert.equal(await timingSafeEqual("abc", "abcd"), false);
  assert.equal(await isAuthorized(SECRET, SECRET), true);
  assert.equal(await isAuthorized(`Bearer ${SECRET}`, SECRET), true);
  assert.equal(await isAuthorized(null, SECRET), false);
  assert.equal(await isAuthorized("wrong", SECRET), false);
});

Deno.test("event → purchases 행: 통화·USD·시각·raw", () => {
  const row = toPurchaseRow(EVENT)!;
  assert.equal(row.event_id, EVENT.id);
  assert.equal(row.event_type, "INITIAL_PURCHASE");
  assert.equal(row.app_user_id, "local:7a1c");
  assert.equal(row.product_id, "premium_monthly");
  assert.equal(row.period_type, "NORMAL");
  assert.equal(row.price, 5900);
  assert.equal(row.currency, "KRW");
  assert.equal(row.price_in_usd, 4.12);
  assert.equal(row.store, "PLAY_STORE");
  assert.equal(row.environment, "PRODUCTION");
  assert.equal(row.event_timestamp, "2025-10-10T08:00:00.000Z");
  assert.deepEqual(row.raw, EVENT);
});

Deno.test("event 변환: 빠진 값·USD 대체·TRANSFER", () => {
  assert.equal(toPurchaseRow({ type: "TEST" }), null);
  const usd = toPurchaseRow({ id: "e2", price: "9.99", currency: "usd" })!;
  assert.equal(usd.price, 9.99);
  assert.equal(usd.price_in_usd, 9.99);
  assert.equal(usd.currency, "USD");
  assert.equal(usd.event_timestamp, null);
  const transfer = toPurchaseRow({ id: "e3", type: "TRANSFER", transferred_to: ["new-user"], event_timestamp_ms: "1760083200000" })!;
  assert.equal(transfer.app_user_id, "new-user");
  assert.equal(transfer.event_timestamp, "2025-10-10T08:00:00.000Z");
  assert.equal(transfer.price, null);
  assert.equal(msToIso(12), null);
  assert.equal(msToIso("abc"), null);
});

Deno.test("웹훅: 정상 → upsert(event_id 중복 무시) + 200", async () => {
  const { fn, calls } = fakeFetch();
  const res = await handleWebhook(post({ api_version: "1.0", event: EVENT }), { env: (k) => ENV[k], fetchFn: fn, log: () => {} });
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { ok: true, event_id: EVENT.id });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "https://ref.supabase.co/rest/v1/purchases?on_conflict=event_id");
  const h = calls[0].init.headers as Record<string, string>;
  assert.equal(h.Prefer, "return=minimal,resolution=ignore-duplicates");
  const row = JSON.parse(String(calls[0].init.body));
  assert.equal(row.event_id, EVENT.id);
  assert.equal(row.raw.is_trial_conversion, false);
});

Deno.test("웹훅: 인증 실패 401, 설정 없음 500, 메서드 405", async () => {
  const { fn, calls } = fakeFetch();
  const deps = { env: (k: string) => ENV[k], fetchFn: fn, log: () => {} };
  assert.equal((await handleWebhook(post({ event: EVENT }, "nope"), deps)).status, 401);
  assert.equal((await handleWebhook(post({ event: EVENT }, null), deps)).status, 401);
  assert.equal((await handleWebhook(post(null, SECRET, "GET"), deps)).status, 405);
  const noSecret = { ...ENV, REVENUECAT_WEBHOOK_AUTH: "" } as Record<string, string>;
  assert.equal((await handleWebhook(post({ event: EVENT }), { ...deps, env: (k: string) => noSecret[k] })).status, 500);
  assert.equal(calls.length, 0);
});

Deno.test("웹훅: 잘못된 본문 400, 너무 큼 413", async () => {
  const { fn, calls } = fakeFetch();
  const deps = { env: (k: string) => ENV[k], fetchFn: fn, log: () => {} };
  assert.equal((await handleWebhook(post("{not json"), deps)).status, 400);
  assert.equal((await handleWebhook(post({ api_version: "1.0" }), deps)).status, 400);
  assert.equal((await handleWebhook(post({ event: { type: "TEST" } }), deps)).status, 400);
  assert.equal((await handleWebhook(post({ event: EVENT, pad: "x".repeat(300 * 1024) }), deps)).status, 413);
  assert.equal(calls.length, 0);
});

Deno.test("웹훅: DB 실패면 500(RevenueCat 이 다시 보냄), 로그에 비밀값 없음", async () => {
  const { fn } = fakeFetch(503);
  const logs: string[] = [];
  const res = await handleWebhook(post({ event: EVENT }), { env: (k) => ENV[k], fetchFn: fn, log: (m) => logs.push(m) });
  assert.equal(res.status, 500);
  assert.ok(logs.some((l) => l.includes("기록 실패")));
  assert.ok(logs.every((l) => !l.includes(SECRET) && !l.includes("eyJ.service.key")));
});
