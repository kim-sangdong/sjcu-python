/**
 * 앱 설정 (Expo SDK 57, CNG). 네이티브 영역(아이콘·scheme·plugins·권한)을 바꾸면
 * runtimeVersion(fingerprint)이 바뀌므로 OTA 가 아니라 새 빌드가 필요하다.
 *
 * 빌드 시점 환경변수(EAS 의 environment 변수 또는 로컬 .env):
 *   EAS_PROJECT_ID        EAS 프로젝트 ID(UUID). 있으면 updates.url 과 extra.eas.projectId 를 채운다.
 *   EAS_OWNER             (선택) Expo 계정/조직 이름
 *   APP_ANDROID_PACKAGE   Android applicationId (기본 kr.hamjeongnote.app)
 *   APP_IOS_BUNDLE_ID     iOS bundle identifier (기본 kr.hamjeongnote.app)
 *   APP_DISPLAY_NAME      홈 화면 이름 (기본 "함정노트")
 *   APP_LINK_DOMAIN       (선택) https 유니버설 링크/앱 링크 도메인. 예: go.example.kr
 *   APP_VERSION           (선택) 사용자에게 보이는 버전. 기본 package.json 과 같은 1.0.0
 *
 * 런타임(EXPO_PUBLIC_*) 변수는 src/core/env.ts 에서 읽는다.
 */
import type { ConfigContext, ExpoConfig } from 'expo/config';

const DEFAULT_ID = 'kr.hamjeongnote.app';

function env(name: string): string | undefined {
  const v = process.env[name];
  return v && v.trim() ? v.trim() : undefined;
}

export default ({ config }: ConfigContext): ExpoConfig => {
  const projectId = env('EAS_PROJECT_ID');
  const linkDomain = env('APP_LINK_DOMAIN');
  const androidPackage = env('APP_ANDROID_PACKAGE') ?? DEFAULT_ID;
  const iosBundleId = env('APP_IOS_BUNDLE_ID') ?? DEFAULT_ID;

  return {
    ...config,
    name: env('APP_DISPLAY_NAME') ?? '함정노트',
    slug: 'hamjeong-note',
    owner: env('EAS_OWNER'),
    scheme: 'hamjeongnote',
    version: env('APP_VERSION') ?? '1.0.0',
    orientation: 'portrait',
    icon: './assets/images/icon.png',
    userInterfaceStyle: 'automatic',
    runtimeVersion: { policy: 'fingerprint' },
    updates: projectId
      ? {
          url: `https://u.expo.dev/${projectId}`,
          // 시작을 막지 않는다. 받은 업데이트는 다음 실행에 적용된다.
          checkAutomatically: 'ON_LOAD',
          fallbackToCacheTimeout: 0,
        }
      : { enabled: false },
    ios: {
      bundleIdentifier: iosBundleId,
      supportsTablet: true,
      associatedDomains: linkDomain ? [`applinks:${linkDomain}`] : undefined,
      infoPlist: {
        // HTTPS 외 자체 암호화를 쓰지 않는다(수출 규정 질문 생략).
        ITSAppUsesNonExemptEncryption: false,
        CFBundleDevelopmentRegion: 'ko',
      },
      // 의존 패키지의 PrivacyInfo.xcprivacy 를 합친 값(node_modules/*/ios/PrivacyInfo.xcprivacy).
      privacyManifests: {
        NSPrivacyTracking: false,
        NSPrivacyAccessedAPITypes: [
          {
            NSPrivacyAccessedAPIType: 'NSPrivacyAccessedAPICategoryUserDefaults',
            NSPrivacyAccessedAPITypeReasons: ['CA92.1'],
          },
          {
            NSPrivacyAccessedAPIType: 'NSPrivacyAccessedAPICategoryFileTimestamp',
            NSPrivacyAccessedAPITypeReasons: ['C617.1', '0A2A.1', '3B52.1'],
          },
          {
            NSPrivacyAccessedAPIType: 'NSPrivacyAccessedAPICategoryDiskSpace',
            NSPrivacyAccessedAPITypeReasons: ['E174.1', '85F4.1'],
          },
          {
            NSPrivacyAccessedAPIType: 'NSPrivacyAccessedAPICategorySystemBootTime',
            NSPrivacyAccessedAPITypeReasons: ['35F9.1'],
          },
        ],
      },
    },
    android: {
      package: androidPackage,
      adaptiveIcon: {
        foregroundImage: './assets/images/adaptive-foreground.png',
        monochromeImage: './assets/images/adaptive-monochrome.png',
        backgroundColor: '#8C2129',
      },
      // 광고 SDK·위치·연락처 등은 쓰지 않는다. 인터넷·결제 권한은 각 라이브러리 매니페스트가 넣는다.
      blockedPermissions: [
        'android.permission.READ_EXTERNAL_STORAGE',
        'android.permission.WRITE_EXTERNAL_STORAGE',
        'android.permission.SYSTEM_ALERT_WINDOW',
        'com.google.android.gms.permission.AD_ID',
      ],
      intentFilters: linkDomain
        ? [
            {
              action: 'VIEW',
              autoVerify: true,
              data: [{ scheme: 'https', host: linkDomain, pathPrefix: '/deck' }],
              category: ['BROWSABLE', 'DEFAULT'],
            },
          ]
        : undefined,
    },
    plugins: [
      'expo-router',
      'expo-sqlite',
      'expo-font',
      'expo-web-browser',
      [
        'expo-splash-screen',
        {
          image: './assets/images/splash-icon.png',
          imageWidth: 160,
          backgroundColor: '#FFF8F2',
          dark: { image: './assets/images/splash-icon.png', backgroundColor: '#141011' },
        },
      ],
    ],
    experiments: {
      typedRoutes: false,
    },
    extra: {
      ...(config.extra ?? {}),
      storeTitle: '함정노트: 공인중개사 OX·숫자암기',
      linkDomain: linkDomain ?? null,
      eas: projectId ? { projectId } : undefined,
    },
  };
};
