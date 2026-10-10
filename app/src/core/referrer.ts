/**
 * Google Play 설치 리퍼러 파싱.
 * expo-application getInstallReferrerAsync() 는 "utm_source=google-play&utm_medium=organic" 같은 문자열을 준다.
 * 링크를 만들 때 referrer 값을 한 번 더 URL 인코딩한 경우("utm_source%3Dyt_shorts%26...")도 받아들인다.
 * 채널 규칙: utm_source ∈ {yt_shorts, yt_long, ig_reels, ig_card, fb_reels, tiktok, tistory}
 */

export interface Utm {
  utm_source: string | null;
  utm_medium: string | null;
  utm_campaign: string | null;
  utm_content: string | null;
  utm_term: string | null;
}

const MAX_LEN = 100;
const MAX_RAW = 500;

function safeDecode(s: string): string {
  try {
    return decodeURIComponent(s.replace(/\+/g, ' '));
  } catch {
    return s;
  }
}

function clean(v: string | undefined): string | null {
  if (v === undefined) return null;
  // 제어 문자 제거, 앞뒤 공백 제거, 길이 제한
  // eslint-disable-next-line no-control-regex
  const t = v.replace(/[\u0000-\u001f\u007f]/g, '').trim().slice(0, MAX_LEN);
  return t.length ? t : null;
}

export function parseReferrer(raw: string | null | undefined): Utm {
  const empty: Utm = { utm_source: null, utm_medium: null, utm_campaign: null, utm_content: null, utm_term: null };
  if (!raw) return empty;
  let s = raw.trim();
  // 이중 인코딩: '=' 이 없고 '%3D' 가 있으면 한 번 더 푼다
  for (let i = 0; i < 2 && !s.includes('=') && /%3d/i.test(s); i++) s = safeDecode(s);
  if (s.startsWith('?')) s = s.slice(1);
  const out: Record<string, string> = {};
  for (const part of s.split('&')) {
    const eq = part.indexOf('=');
    if (eq <= 0) continue;
    const k = safeDecode(part.slice(0, eq)).trim().toLowerCase();
    const v = safeDecode(part.slice(eq + 1));
    if (k.startsWith('utm_') && !(k in out)) out[k] = v;
  }
  return {
    utm_source: clean(out.utm_source),
    utm_medium: clean(out.utm_medium),
    utm_campaign: clean(out.utm_campaign),
    utm_content: clean(out.utm_content),
    utm_term: clean(out.utm_term),
  };
}

export function truncateRaw(raw: string | null | undefined): string | null {
  if (!raw) return null;
  return raw.slice(0, MAX_RAW);
}

/** Play 스토어 기본값(직접 검색 설치)인지 */
export function isOrganic(utm: Utm): boolean {
  return (
    !utm.utm_source ||
    (utm.utm_source === 'google-play' && (utm.utm_medium === 'organic' || utm.utm_medium === null))
  );
}

/**
 * RevenueCat 속성. 예약 키($mediaSource 등)와 같은 값을 utm_* 커스텀 키로도 남긴다.
 * 값이 없는 키는 넣지 않는다(빈 문자열을 넣으면 RevenueCat 에서 삭제로 처리됨).
 */
export function revenueCatAttributes(utm: Utm): Record<string, string> {
  const attrs: Record<string, string> = {};
  const put = (k: string, v: string | null) => {
    if (v) attrs[k] = v;
  };
  put('$mediaSource', utm.utm_source);
  put('$campaign', utm.utm_campaign);
  put('$adGroup', utm.utm_medium);
  put('$ad', utm.utm_content);
  put('$keyword', utm.utm_term);
  put('utm_source', utm.utm_source);
  put('utm_medium', utm.utm_medium);
  put('utm_campaign', utm.utm_campaign);
  put('utm_content', utm.utm_content);
  return attrs;
}

/** 딥링크 쿼리(useLocalSearchParams 결과)에서 utm 만 꺼낸다 */
export function utmFromParams(params: Record<string, string | string[] | undefined>): Partial<Utm> {
  const pick = (k: keyof Utm) => {
    const v = params[k];
    return clean(Array.isArray(v) ? v[0] : v);
  };
  const out: Partial<Utm> = {};
  (['utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term'] as const).forEach((k) => {
    const v = pick(k);
    if (v) out[k] = v;
  });
  return out;
}
