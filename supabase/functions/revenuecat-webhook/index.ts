// RevenueCat 웹훅 수신 — https://<project-ref>.supabase.co/functions/v1/revenuecat-webhook
// RevenueCat 서버가 부르므로 supabase/config.toml 에서 verify_jwt = false. 인증은 Authorization 헤더(REVENUECAT_WEBHOOK_AUTH).

import { handleWebhook } from "./logic.ts";

Deno.serve((req: Request) => handleWebhook(req, { env: (name) => Deno.env.get(name) }));
