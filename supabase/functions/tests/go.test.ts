// deno test supabase/functions/tests/   (네트워크·권한 없이 돈다 — fetch 는 가짜)
import { strict as assert } from "node:assert";

import {
  appStoreUrl,
  chooseDestination,
  clientIp,
  detectOs,
  handleGo,
  isBot,
  parseParams,
  playStoreUrl,
  readStoreConfig,
  type StoreConfig,
  type Utm,
} from "../go/logic.ts";
import { serviceKey } from "../_shared/rest.ts";

const ANDROID_UA = "Mozilla/5.0 (Linux; Android 14; SM-S918N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Mobile Safari/537.36";
const IOS_UA =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1";
const DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36";

const ENV: Record<string, string> = {
  SUPABASE_URL: "https://ref.supabase.co",
  SUPABASE_SERVICE_ROLE_KEY: "eyJ.service.key",
  ANDROID_PACKAGE: "kr.example.app",
  IOS_APP_ID: "1234567890",
  APPLE_PROVIDER_TOKEN: "123456",
  LANDING_URL: "https://example.com/app",
  IP_HASH_SALT: "pepper",
};

function fakeFetch(status = 201) {
  const calls: { url: string; init: RequestInit }[] = [];
  const fn = ((url: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(url), init: init ?? {} });
    return Promise.resolve(new Response(null, { status }));
  }) as typeof fetch;
  return { fn, calls };
}

function req(query: string, ua: string, extra: Record<string, string> = {}, method = "GET") {
  return new Request(`https://ref.supabase.co/functions/v1/go${query}`, {
    method,
    headers: { "user-agent": ua, ...extra },
  });
}

const UTM: Utm = { source: "youtube", medium: "description", campaign: "2026-W42", content: "2026W42-L1" };

Deno.test("쿼리 검증: 규칙에 맞는 값만 남기고 s·m 은 소문자", () => {
  const u = parseParams(new URL("https://x/go?s=YouTube&m=shorts&c=2026-W42&a=2026W42-S03"));
  assert.deepEqual(u, { source: "youtube", medium: "shorts", campaign: "2026-W42", content: "2026W42-S03" });
  const bad = parseParams(new URL("https://x/go?s=you%20tube&m=" + "a".repeat(40) + "&c=<x>&a=../etc"));
  assert.deepEqual(bad, { source: null, medium: null, campaign: null, content: null });
  assert.deepEqual(parseParams(new URL("https://x/go")), { source: null, medium: null, campaign: null, content: null });
});

Deno.test("OS·봇 판별", () => {
  assert.equal(detectOs(ANDROID_UA), "android");
  assert.equal(detectOs(IOS_UA), "ios");
  assert.equal(detectOs(DESKTOP_UA), "other");
  assert.equal(isBot("Mozilla/5.0 (compatible; Googlebot/2.1)"), true);
  assert.equal(isBot("facebookexternalhit/1.1; kakaotalk-scrap/1.0"), true);
  assert.equal(isBot("Slack-LinkPreview"), true);
  assert.equal(isBot("SomeCrawler/1.0"), true);
  assert.equal(isBot(""), true);
  assert.equal(isBot(ANDROID_UA), false);
});

Deno.test("Play 링크: referrer 에 UTM 을 한 번 URL 인코딩해서 싣는다", () => {
  const url = new URL(playStoreUrl("kr.example.app", UTM));
  assert.equal(url.origin + url.pathname, "https://play.google.com/store/apps/details");
  assert.equal(url.searchParams.get("id"), "kr.example.app");
  assert.equal(
    url.searchParams.get("referrer"),
    "utm_source=youtube&utm_medium=description&utm_campaign=2026-W42&utm_content=2026W42-L1",
  );
  assert.ok(url.toString().includes("referrer=utm_source%3Dyoutube%26utm_medium%3D"));
  // 값이 없으면 referrer 를 붙이지 않는다
  assert.equal(
    playStoreUrl("kr.example.app", { source: null, medium: null, campaign: null, content: null }),
    "https://play.google.com/store/apps/details?id=kr.example.app",
  );
});

Deno.test("App Store 링크: pt·ct(30자 이하)·mt=8", () => {
  assert.equal(appStoreUrl("1234567890", "123456", UTM), "https://apps.apple.com/app/id1234567890?pt=123456&ct=2026W42-L1&mt=8");
  const long = { ...UTM, content: "A".repeat(64) };
  const ct = new URL(appStoreUrl("1", "2", long)).searchParams.get("ct");
  assert.equal(ct?.length, 30);
  // provider token 이 없으면 캠페인 값 없이 상품 페이지만
  assert.equal(appStoreUrl("1234567890", null, UTM), "https://apps.apple.com/app/id1234567890");
});

Deno.test("목적지 우선순위와 설정 검증", () => {
  const cfg = readStoreConfig((k) => ENV[k]);
  assert.deepEqual(cfg.problems, []);
  assert.ok(chooseDestination("android", UTM, cfg)!.startsWith("https://play.google.com/"));
  assert.ok(chooseDestination("ios", UTM, cfg)!.startsWith("https://apps.apple.com/app/id1234567890?pt=123456"));
  const other = new URL(chooseDestination("other", UTM, cfg)!);
  assert.equal(other.origin + other.pathname, "https://example.com/app");
  assert.equal(other.searchParams.get("utm_content"), "2026W42-L1");

  const onlyPlay: StoreConfig = {
    androidPackage: "kr.example.app",
    iosAppId: null,
    appleProviderToken: null,
    landingUrl: null,
    problems: [],
  };
  assert.ok(chooseDestination("ios", UTM, onlyPlay)!.startsWith("https://play.google.com/"));
  assert.ok(chooseDestination("other", UTM, onlyPlay)!.startsWith("https://play.google.com/"));
  const none: StoreConfig = { androidPackage: null, iosAppId: null, appleProviderToken: null, landingUrl: null, problems: [] };
  assert.equal(chooseDestination("android", UTM, none), null);

  const broken = readStoreConfig((k) =>
    ({ ANDROID_PACKAGE: "not a package", IOS_APP_ID: "id12", LANDING_URL: "http://x" } as Record<string, string>)[k]
  );
  assert.equal(broken.androidPackage, null);
  assert.equal(broken.iosAppId, null);
  assert.equal(broken.landingUrl, null);
  assert.equal(broken.problems.length, 3);
  assert.ok(broken.problems.every((p) => !p.includes("not a package"))); // 값은 메시지에 넣지 않는다
});

Deno.test("클라이언트 IP: x-forwarded-for 첫 값", () => {
  assert.equal(clientIp(new Headers({ "x-forwarded-for": "203.0.113.7, 10.0.0.1" })), "203.0.113.7");
  assert.equal(clientIp(new Headers({ "x-real-ip": "2001:db8::1" })), "2001:db8::1");
  assert.equal(clientIp(new Headers({ "x-forwarded-for": "<script>" })), null);
});

Deno.test("서버 키: 새 키(SUPABASE_SECRET_KEYS) 우선, 없으면 레거시", () => {
  assert.equal(
    serviceKey((k) =>
      ({ SUPABASE_SECRET_KEYS: '{"default":"sb_secret_x"}', SUPABASE_SERVICE_ROLE_KEY: "eyJ" } as Record<string, string>)[k]
    ),
    "sb_secret_x",
  );
  assert.equal(serviceKey((k) => ({ SUPABASE_SECRET_KEYS: "oops", SUPABASE_SERVICE_ROLE_KEY: "eyJ" } as Record<string, string>)[k]), "eyJ");
  assert.equal(serviceKey(() => undefined), null);
});

Deno.test("처리기: 안드로이드 클릭 → 기록 + Play 302", async () => {
  const { fn, calls } = fakeFetch();
  const res = await handleGo(
    req("?s=youtube&m=description&c=2026-W42&a=2026W42-L1", ANDROID_UA, {
      "x-forwarded-for": "203.0.113.7",
      referer: "https://www.youtube.com/",
    }),
    {
      env: (k) => ENV[k],
      fetchFn: fn,
      log: () => {},
    },
  );
  assert.equal(res.status, 302);
  assert.ok(res.headers.get("location")!.startsWith("https://play.google.com/store/apps/details?id=kr.example.app&referrer="));
  assert.equal(res.headers.get("cache-control"), "no-store");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "https://ref.supabase.co/rest/v1/link_clicks");
  const h = calls[0].init.headers as Record<string, string>;
  assert.equal(h.apikey, "eyJ.service.key");
  assert.equal(h.Authorization, "Bearer eyJ.service.key");
  assert.equal(h.Prefer, "return=minimal");
  const row = JSON.parse(String(calls[0].init.body));
  assert.equal(row.source, "youtube");
  assert.equal(row.content, "2026W42-L1");
  assert.equal(row.os, "android");
  assert.equal(row.referer, "https://www.youtube.com/");
  assert.match(row.ip_hash, /^[0-9a-f]{64}$/);
  assert.ok(!JSON.stringify(row).includes("203.0.113.7")); // 원래 IP 는 저장하지 않는다
});

Deno.test("처리기: 새 secret 키는 apikey 헤더로만", async () => {
  const { fn, calls } = fakeFetch();
  const env = { ...ENV, SUPABASE_SECRET_KEYS: '{"default":"sb_secret_abc"}' } as Record<string, string>;
  await handleGo(req("?s=tiktok", IOS_UA), { env: (k) => env[k], fetchFn: fn, log: () => {} });
  const h = calls[0].init.headers as Record<string, string>;
  assert.equal(h.apikey, "sb_secret_abc");
  assert.equal(h.Authorization, undefined);
});

Deno.test("처리기: 봇·HEAD 는 기록하지 않고 이동만", async () => {
  const { fn, calls } = fakeFetch();
  const deps = { env: (k: string) => ENV[k], fetchFn: fn, log: () => {} };
  const r1 = await handleGo(req("?s=youtube", "Mozilla/5.0 (compatible; bingbot/2.0)"), deps);
  const r2 = await handleGo(req("?s=youtube", ANDROID_UA, {}, "HEAD"), deps);
  assert.equal(r1.status, 302);
  assert.equal(r2.status, 302);
  assert.equal(calls.length, 0);
  const r3 = await handleGo(req("", ANDROID_UA, {}, "POST"), deps);
  assert.equal(r3.status, 405);
});

Deno.test("처리기: 기록이 실패해도(DB 오류·예외) 이동한다", async () => {
  const logs: string[] = [];
  const failing = (() => Promise.reject(new Error("network down"))) as typeof fetch;
  const r1 = await handleGo(req("?s=youtube", IOS_UA), { env: (k) => ENV[k], fetchFn: failing, log: (m) => logs.push(m) });
  assert.equal(r1.status, 302);
  assert.ok(r1.headers.get("location")!.startsWith("https://apps.apple.com/"));
  const { fn } = fakeFetch(500);
  const r2 = await handleGo(req("?s=youtube", DESKTOP_UA), { env: (k) => ENV[k], fetchFn: fn, log: (m) => logs.push(m) });
  assert.equal(r2.status, 302);
  assert.ok(logs.some((l) => l.includes("클릭 기록 실패")));
  assert.ok(logs.every((l) => !l.includes("eyJ.service.key"))); // 키를 로그에 남기지 않는다
});

Deno.test("처리기: waitUntil 이 있으면 기록을 뒤로 넘긴다", async () => {
  const { fn, calls } = fakeFetch();
  const pending: Promise<unknown>[] = [];
  const res = await handleGo(req("?a=2026W42-S01", ANDROID_UA), {
    env: (k) => ENV[k],
    fetchFn: fn,
    waitUntil: (p) => pending.push(p),
    log: () => {},
  });
  assert.equal(res.status, 302);
  assert.equal(pending.length, 1);
  await Promise.all(pending);
  assert.equal(calls.length, 1);
});

Deno.test("처리기: 보낼 곳이 하나도 없으면 503", async () => {
  const { fn } = fakeFetch();
  const env = { SUPABASE_URL: ENV.SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY: "eyJ" } as Record<string, string>;
  const res = await handleGo(req("?s=youtube", ANDROID_UA), { env: (k) => env[k], fetchFn: fn, log: () => {} });
  assert.equal(res.status, 503);
});
