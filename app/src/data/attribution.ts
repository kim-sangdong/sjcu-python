/**
 * 첫 실행 어트리뷰션.
 * Android: Play 설치 리퍼러(expo-application getInstallReferrerAsync, 설치 후 첫 실행에 한 번) → utm 파싱
 *          → app_installs INSERT + RevenueCat 예약 속성($mediaSource 등)과 utm_* 커스텀 속성.
 * iOS:     앱이 캠페인 값을 읽을 방법이 없어 os=ios 로만 기록한다(온보딩 설문으로 보완).
 */
import * as Application from 'expo-application';
import { Platform } from 'react-native';

import { parseReferrer, revenueCatAttributes, truncateRaw, type Utm } from '@/core/referrer';
import { getKV, setKV } from './repo';
import { setAttributes } from './purchases';
import { installRow, track } from './telemetry';

export const KV_INSTALL_REPORTED = 'install_reported';

async function readReferrer(): Promise<string | null> {
  if (Platform.OS !== 'android') return null;
  try {
    return await Application.getInstallReferrerAsync();
  } catch (e) {
    // Play 스토어가 없는 에뮬레이터 등: ERR_APPLICATION_INSTALL_REFERRER_UNAVAILABLE
    if (__DEV__) console.warn('[attribution] 리퍼러 없음', e);
    return null;
  }
}

/** 설치 후 처음 한 번만 실행. 전송은 outbox 가 재시도한다. */
export async function reportInstallOnce(): Promise<void> {
  if (await getKV<boolean>(KV_INSTALL_REPORTED, false)) return;
  const raw = await readReferrer();
  const utm: Utm = parseReferrer(raw);
  await installRow({
    utm_source: utm.utm_source,
    utm_medium: utm.utm_medium,
    utm_campaign: utm.utm_campaign,
    utm_content: utm.utm_content,
    raw_referrer: truncateRaw(raw),
  });
  await setAttributes(revenueCatAttributes(utm));
  await setKV('install_utm', utm);
  await setKV(KV_INSTALL_REPORTED, true);
}

/** 온보딩 "어디서 알게 되셨나요?" — 리퍼러가 없을 때(iOS 등)만 $mediaSource 로 쓴다 */
export async function recordSurveySource(source: string): Promise<void> {
  const utm = await getKV<Utm | null>('install_utm', null);
  const attrs: Record<string, string> = { survey_source: source };
  if (!utm?.utm_source || utm.utm_source === 'google-play') attrs.$mediaSource = `survey_${source}`;
  await setAttributes(attrs);
  await track('attribution_survey', { source });
}
