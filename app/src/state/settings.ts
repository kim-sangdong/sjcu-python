export type ThemePref = 'system' | 'light' | 'dark';

export interface Settings {
  onboarded: boolean;
  /** 목표 회차. null 이면 매니페스트의 다음 시험 */
  targetRound: number | null;
  weakSubjects: string[];
  theme: ThemePref;
  highContrast: boolean;
  /** 글자 배율 1 | 1.15 | 1.3 */
  fontScale: number;
  /** 개발 빌드에서 RevenueCat 키가 없을 때만 쓰는 유료 미리보기 */
  devPremium: boolean;
}

export const DEFAULT_SETTINGS: Settings = {
  onboarded: false,
  targetRound: null,
  weakSubjects: [],
  theme: 'system',
  highContrast: false,
  fontScale: 1,
  devPremium: false,
};

export const FONT_SCALES = [
  { value: 1, label: '보통' },
  { value: 1.15, label: '크게' },
  { value: 1.3, label: '아주 크게' },
] as const;
