/**
 * 런타임 공개 설정. Metro 는 `process.env.EXPO_PUBLIC_이름` 을 빌드 때 문자열로 바꿔 넣으므로
 * 반드시 아래처럼 정적인 형태로만 읽는다(동적 키 접근 금지).
 * 비밀 키(service_role, RevenueCat secret)는 절대 여기 두지 않는다.
 */

function clean(v: string | undefined): string | null {
  const t = (v ?? '').trim();
  return t.length ? t : null;
}

const SUPABASE_URL = clean(process.env.EXPO_PUBLIC_SUPABASE_URL)?.replace(/\/+$/, '') ?? null;

export const ENV = {
  appEnv: clean(process.env.EXPO_PUBLIC_APP_ENV) ?? 'development',
  supabaseUrl: SUPABASE_URL,
  supabaseAnonKey: clean(process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY),
  manifestUrl:
    clean(process.env.EXPO_PUBLIC_BANK_MANIFEST_URL) ??
    (SUPABASE_URL ? `${SUPABASE_URL}/storage/v1/object/public/media/bank/latest/manifest.json` : null),
  rcIosKey: clean(process.env.EXPO_PUBLIC_RC_IOS_KEY),
  rcAndroidKey: clean(process.env.EXPO_PUBLIC_RC_ANDROID_KEY),
  paywallMode: clean(process.env.EXPO_PUBLIC_PAYWALL_MODE) === 'revenuecat' ? 'revenuecat' : 'custom',
  privacyUrl: clean(process.env.EXPO_PUBLIC_PRIVACY_URL),
  termsUrl: clean(process.env.EXPO_PUBLIC_TERMS_URL),
  supportEmail: clean(process.env.EXPO_PUBLIC_SUPPORT_EMAIL),
} as const;

export const ENTITLEMENT_ID = 'premium';

/** 60일 패스 패키지 식별자(RevenueCat 오퍼링의 커스텀 패키지) */
export const PASS_PACKAGE_ID = 'pass_60d';
export const PASS_DAYS = 60;
