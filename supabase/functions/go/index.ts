// 추적 링크 리디렉트 — https://<project-ref>.supabase.co/functions/v1/go?s=..&m=..&c=..&a=..
// 판단 로직은 logic.ts. 공개 호출이므로 supabase/config.toml 에서 verify_jwt = false.

import { handleGo } from "./logic.ts";

type EdgeRuntimeLike = { waitUntil(p: Promise<unknown>): void };
const edge = (globalThis as { EdgeRuntime?: EdgeRuntimeLike }).EdgeRuntime;

Deno.serve((req: Request) =>
  handleGo(req, {
    env: (name) => Deno.env.get(name),
    // 응답(302)을 먼저 보내고 클릭 기록은 뒤에서 끝낸다. 로컬 실행 등 EdgeRuntime 이 없으면 기록을 기다린다.
    waitUntil: edge ? (p) => edge.waitUntil(p) : undefined,
  })
);
