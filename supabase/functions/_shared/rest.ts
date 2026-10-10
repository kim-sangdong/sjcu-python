/**
 * Edge Function 공통 도우미 — PostgREST 로 한 행 넣기, 서버 키 고르기, 해시·상수 시간 비교.
 * 외부 패키지를 쓰지 않는다(콜드 스타트를 짧게, 배포 때 의존성 문제 없게).
 */

export type EnvGet = (name: string) => string | undefined;

/** PostgREST 경로 (공식 문서가 바뀌면 여기만 고친다) */
export const REST_PATH = "/rest/v1/";
export const DEFAULT_TIMEOUT_MS = 4000;

export class RestError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "RestError";
  }
}

/**
 * RLS 를 우회하는 서버 키.
 * 새 키 체계: SUPABASE_SECRET_KEYS = {"default": "sb_secret_..."} (JSON) → 레거시 SUPABASE_SERVICE_ROLE_KEY 순서.
 * 둘 다 Supabase 가 함수 환경에 자동으로 넣는다(docs/research/facts-app-stack.md 5-1).
 */
export function serviceKey(env: EnvGet): string | null {
  const raw = env("SUPABASE_SECRET_KEYS");
  if (raw) {
    try {
      const keys = JSON.parse(raw) as Record<string, unknown>;
      const k = keys?.["default"] ?? Object.values(keys ?? {})[0];
      if (typeof k === "string" && k.length > 0) return k;
    } catch {
      // 형식이 다르면 레거시 키로 넘어간다
    }
  }
  const legacy = env("SUPABASE_SERVICE_ROLE_KEY");
  return legacy && legacy.length > 0 ? legacy : null;
}

/** 새 API 키(sb_...)는 JWT 가 아니라 apikey 헤더로만 보낸다. 레거시 JWT 키는 Authorization 에도 넣는다. */
export function restHeaders(key: string, prefer: string): Record<string, string> {
  const h: Record<string, string> = {
    apikey: key,
    "Content-Type": "application/json",
    Prefer: prefer,
  };
  if (key.startsWith("eyJ")) h.Authorization = `Bearer ${key}`;
  return h;
}

export interface InsertOptions {
  url: string; // SUPABASE_URL
  key: string; // 서버 키
  table: string;
  row: Record<string, unknown>;
  onConflict?: string; // 이 열이 겹치면 무시(ON CONFLICT DO NOTHING)
  timeoutMs?: number;
  fetchFn?: typeof fetch;
}

/** 한 행 INSERT. 응답 본문은 받지 않는다(return=minimal). 실패하면 RestError. */
export async function insertRow(opts: InsertOptions): Promise<void> {
  const f = opts.fetchFn ?? fetch;
  const base = opts.url.replace(/\/+$/, "");
  let endpoint = `${base}${REST_PATH}${encodeURIComponent(opts.table)}`;
  const prefer = ["return=minimal"];
  if (opts.onConflict) {
    endpoint += `?on_conflict=${encodeURIComponent(opts.onConflict)}`;
    prefer.push("resolution=ignore-duplicates");
  }
  const res = await f(endpoint, {
    method: "POST",
    headers: restHeaders(opts.key, prefer.join(",")),
    body: JSON.stringify(opts.row),
    signal: AbortSignal.timeout(opts.timeoutMs ?? DEFAULT_TIMEOUT_MS),
  });
  if (!res.ok) {
    // 본문에 값이 섞일 수 있으니 앞부분만, 키는 절대 넣지 않는다
    const text = (await res.text().catch(() => "")).slice(0, 300);
    throw new RestError(`${opts.table} INSERT 실패: HTTP ${res.status} ${text}`, res.status);
  }
  await res.body?.cancel().catch(() => {});
}

export async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * 상수 시간 문자열 비교. 두 값을 SHA-256 으로 같은 길이로 만든 뒤 모든 바이트를 XOR 로 누적한다
 * (길이·첫 불일치 위치가 응답 시간으로 새지 않게).
 */
export async function timingSafeEqual(a: string, b: string): Promise<boolean> {
  const enc = new TextEncoder();
  const [da, db] = await Promise.all([
    crypto.subtle.digest("SHA-256", enc.encode(a)),
    crypto.subtle.digest("SHA-256", enc.encode(b)),
  ]);
  const x = new Uint8Array(da);
  const y = new Uint8Array(db);
  let diff = 0;
  for (let i = 0; i < x.length; i++) diff |= x[i] ^ y[i];
  return diff === 0;
}
