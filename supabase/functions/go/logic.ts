/**
 * 추적 링크 /functions/v1/go 의 판단 로직(순수 함수 + 처리기). index.ts 가 Deno.serve 에 연결한다.
 *
 *   GET /functions/v1/go?s=youtube&m=description&c=2026-W42&a=2026W42-L1
 *     1) s(utm_source)·m(utm_medium)·c(utm_campaign)·a(utm_content=콘텐츠 ID)를 길이·문자 규칙으로 거른다(틀리면 버림).
 *     2) 봇이 아니면 link_clicks 에 기록한다(IP 는 IP_HASH_SALT 로 SHA-256 해시만). 기록이 실패해도 이동은 한다.
 *     3) User-Agent 로 OS 를 보고 302:
 *          Android → Play 스토어(referrer=utm_...)   iOS → App Store 캠페인 링크(pt·ct)   그 외 → LANDING_URL 또는 Play
 *
 * 함수 비밀값(supabase secrets set): ANDROID_PACKAGE, IOS_APP_ID, APPLE_PROVIDER_TOKEN, LANDING_URL, IP_HASH_SALT
 * 자동 주입: SUPABASE_URL, SUPABASE_SECRET_KEYS 또는 SUPABASE_SERVICE_ROLE_KEY
 */

import { type EnvGet, insertRow, serviceKey, sha256Hex } from "../_shared/rest.ts";

// ---------------------------------------------------------------- 상수 (스토어 규칙이 바뀌면 여기만 고친다)

export const PLAY_STORE_URL = "https://play.google.com/store/apps/details";
// Apple 예시는 https://apps.apple.com/app/apple-store/id<ID>?pt=..&ct=..&mt=8 — /app/id<ID> 도 같은 상품 페이지로 간다.
export const APP_STORE_URL = "https://apps.apple.com/app/id";
// Apple 공식 문서(2026-10-10 확인): campaign token(ct)은 최대 30자(영숫자·공백·일부 기호).
export const IOS_CT_MAX = 30;
export const UA_MAX = 512;
export const REFERER_MAX = 1024;
export const CLICK_TABLE = "link_clicks";

/** 쿼리 값 규칙. s·m 은 소문자로 바꾼 뒤 검사한다. */
export const PARAM_RULES = {
  s: /^[a-z0-9][a-z0-9_.-]{0,31}$/,
  m: /^[a-z0-9][a-z0-9_.-]{0,31}$/,
  c: /^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$/,
  a: /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/,
} as const;

// 링크 미리보기·검색 수집기 등(기록하지 않는다). 빈 User-Agent 도 봇으로 본다.
export const BOT_RE = /bot|crawl|spider|preview|facebookexternalhit|scrap|slurp|headless|lighthouse|python-requests|curl\/|wget\//i;

const ANDROID_PACKAGE_RE = /^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$/;
const DIGITS_RE = /^[0-9]{1,20}$/;
const IP_RE = /^[0-9A-Fa-f:.]{2,45}$/;

// ---------------------------------------------------------------- 입력 정리

export interface Utm {
  source: string | null;
  medium: string | null;
  campaign: string | null;
  content: string | null;
}

function pick(url: URL, key: keyof typeof PARAM_RULES, lower: boolean): string | null {
  let v = url.searchParams.get(key);
  if (v === null) return null;
  v = v.trim();
  if (lower) v = v.toLowerCase();
  return PARAM_RULES[key].test(v) ? v : null;
}

export function parseParams(url: URL): Utm {
  return {
    source: pick(url, "s", true),
    medium: pick(url, "m", true),
    campaign: pick(url, "c", false),
    content: pick(url, "a", false),
  };
}

export type Os = "android" | "ios" | "other";

export function detectOs(ua: string): Os {
  if (/android/i.test(ua)) return "android";
  if (/iphone|ipad|ipod/i.test(ua)) return "ios";
  return "other";
}

export function isBot(ua: string): boolean {
  return ua.trim() === "" || BOT_RE.test(ua);
}

export function clientIp(headers: Headers): string | null {
  const candidates = [
    headers.get("x-forwarded-for")?.split(",")[0],
    headers.get("cf-connecting-ip"),
    headers.get("x-real-ip"),
  ];
  for (const c of candidates) {
    const v = c?.trim();
    if (v && IP_RE.test(v)) return v;
  }
  return null;
}

// ---------------------------------------------------------------- 목적지

export interface StoreConfig {
  androidPackage: string | null;
  iosAppId: string | null;
  appleProviderToken: string | null;
  landingUrl: string | null;
  problems: string[]; // 형식이 틀려 무시한 설정(값은 담지 않는다)
}

export function readStoreConfig(env: EnvGet): StoreConfig {
  const problems: string[] = [];
  const get = (name: string, re: RegExp | null): string | null => {
    const v = env(name)?.trim();
    if (!v) return null;
    if (re && !re.test(v)) {
      problems.push(`${name} 형식이 틀려 무시했습니다`);
      return null;
    }
    return v;
  };
  let landing = get("LANDING_URL", null);
  if (landing) {
    try {
      const u = new URL(landing);
      if (u.protocol !== "https:") throw new Error("https 아님");
    } catch {
      problems.push("LANDING_URL 은 https:// 로 시작하는 주소여야 합니다");
      landing = null;
    }
  }
  return {
    androidPackage: get("ANDROID_PACKAGE", ANDROID_PACKAGE_RE),
    iosAppId: get("IOS_APP_ID", DIGITS_RE),
    appleProviderToken: get("APPLE_PROVIDER_TOKEN", DIGITS_RE),
    landingUrl: landing,
    problems,
  };
}

/** "utm_source=..&utm_medium=..&utm_campaign=..&utm_content=.." (있는 값만, 값은 URL 인코딩) */
export function utmQuery(utm: Utm): string {
  const pairs: [string, string | null][] = [
    ["utm_source", utm.source],
    ["utm_medium", utm.medium],
    ["utm_campaign", utm.campaign],
    ["utm_content", utm.content],
  ];
  return pairs
    .filter((p): p is [string, string] => p[1] !== null)
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
    .join("&");
}

export function playStoreUrl(pkg: string, utm: Utm): string {
  let url = `${PLAY_STORE_URL}?id=${encodeURIComponent(pkg)}`;
  const q = utmQuery(utm);
  if (q) url += `&referrer=${encodeURIComponent(q)}`;
  return url;
}

/** iOS 캠페인 토큰: 콘텐츠 ID(없으면 source_medium) 앞 IOS_CT_MAX 자 */
export function campaignToken(utm: Utm): string | null {
  const base = utm.content ?? [utm.source, utm.medium].filter(Boolean).join("_");
  const ct = base.slice(0, IOS_CT_MAX);
  return ct.length > 0 ? ct : null;
}

export function appStoreUrl(appId: string, providerToken: string | null, utm: Utm): string {
  let url = `${APP_STORE_URL}${appId}`;
  const ct = campaignToken(utm);
  if (providerToken && ct) {
    url += `?pt=${encodeURIComponent(providerToken)}&ct=${encodeURIComponent(ct)}&mt=8`;
  }
  return url;
}

export function landingPageUrl(base: string, utm: Utm): string {
  const u = new URL(base);
  const pairs: [string, string | null][] = [
    ["utm_source", utm.source],
    ["utm_medium", utm.medium],
    ["utm_campaign", utm.campaign],
    ["utm_content", utm.content],
  ];
  for (const [k, v] of pairs) if (v !== null && !u.searchParams.has(k)) u.searchParams.set(k, v);
  return u.toString();
}

/** OS 별 우선순위: Android → Play / iOS → App Store / 그 외 → 랜딩. 없으면 다음 후보. */
export function chooseDestination(os: Os, utm: Utm, cfg: StoreConfig): string | null {
  const play = () => (cfg.androidPackage ? playStoreUrl(cfg.androidPackage, utm) : null);
  const apple = () => (cfg.iosAppId ? appStoreUrl(cfg.iosAppId, cfg.appleProviderToken, utm) : null);
  const landing = () => (cfg.landingUrl ? landingPageUrl(cfg.landingUrl, utm) : null);
  const order = os === "android" ? [play, landing, apple] : os === "ios" ? [apple, landing, play] : [landing, play, apple];
  for (const f of order) {
    const url = f();
    if (url) return url;
  }
  return null;
}

// ---------------------------------------------------------------- 처리기

export interface GoDeps {
  env: EnvGet;
  fetchFn?: typeof fetch;
  /** 응답을 먼저 보내고 기록을 이어서 하게 한다(EdgeRuntime.waitUntil). 없으면 기록을 기다린 뒤 응답한다. */
  waitUntil?: (p: Promise<unknown>) => void;
  log?: (msg: string) => void;
}

export async function buildClickRow(req: Request, utm: Utm, os: Os, ua: string, salt: string | null): Promise<Record<string, unknown>> {
  const ip = clientIp(req.headers);
  const referer = req.headers.get("referer");
  return {
    source: utm.source,
    medium: utm.medium,
    campaign: utm.campaign,
    content: utm.content,
    os,
    user_agent: ua ? ua.slice(0, UA_MAX) : null,
    referer: referer ? referer.slice(0, REFERER_MAX) : null,
    ip_hash: salt && ip ? await sha256Hex(`${salt}|${ip}`) : null,
  };
}

let warnedNoSalt = false;

async function recordClick(req: Request, utm: Utm, os: Os, ua: string, deps: GoDeps): Promise<void> {
  const log = deps.log ?? console.error;
  try {
    const url = deps.env("SUPABASE_URL");
    const key = serviceKey(deps.env);
    if (!url || !key) {
      log("[go] SUPABASE_URL 또는 서버 키가 없어 클릭을 기록하지 않았습니다.");
      return;
    }
    const salt = deps.env("IP_HASH_SALT")?.trim() || null;
    if (!salt && !warnedNoSalt) {
      warnedNoSalt = true; // 인스턴스당 한 번만
      log("[go] IP_HASH_SALT 가 없어 ip_hash 를 비워 둡니다(supabase secrets set IP_HASH_SALT=...).");
    }
    const row = await buildClickRow(req, utm, os, ua, salt);
    await insertRow({ url, key, table: CLICK_TABLE, row, fetchFn: deps.fetchFn });
  } catch (e) {
    log(`[go] 클릭 기록 실패(이동은 계속): ${e instanceof Error ? e.message : String(e)}`);
  }
}

const REDIRECT_HEADERS = {
  "Cache-Control": "no-store",
  "Referrer-Policy": "no-referrer",
  "X-Robots-Tag": "noindex",
};

export async function handleGo(req: Request, deps: GoDeps): Promise<Response> {
  const log = deps.log ?? console.error;
  if (req.method !== "GET" && req.method !== "HEAD") {
    return new Response("Method Not Allowed", { status: 405, headers: { Allow: "GET, HEAD" } });
  }
  const url = new URL(req.url);
  const utm = parseParams(url);
  const ua = req.headers.get("user-agent") ?? "";
  const os = detectOs(ua);
  const cfg = readStoreConfig(deps.env);
  for (const p of cfg.problems) log(`[go] ${p}`);

  if (req.method === "GET" && !isBot(ua)) {
    const task = recordClick(req, utm, os, ua, deps);
    if (deps.waitUntil) deps.waitUntil(task);
    else await task;
  }

  const dest = chooseDestination(os, utm, cfg);
  if (!dest) {
    log("[go] ANDROID_PACKAGE·IOS_APP_ID·LANDING_URL 이 모두 없어 보낼 곳이 없습니다.");
    return new Response("앱 링크가 아직 준비되지 않았습니다. 잠시 후 다시 시도해 주세요.", {
      status: 503,
      headers: { "Content-Type": "text/plain; charset=utf-8", ...REDIRECT_HEADERS },
    });
  }
  return new Response(null, { status: 302, headers: { Location: dest, ...REDIRECT_HEADERS } });
}
