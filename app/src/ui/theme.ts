/**
 * 색·글자 크기. 브랜드 색은 channel.yaml brand 와 같다.
 * 고대비 모드는 본문 대비 7:1 이상(WCAG AAA)을 목표로 흑백 위주로 바꾼다.
 */
export interface Palette {
  bg: string;
  surface: string;
  surfaceAlt: string;
  text: string;
  textMuted: string;
  primary: string;
  onPrimary: string;
  accent: string;
  border: string;
  correct: string;
  wrong: string;
  warning: string;
  badgeBg: string;
  badgeText: string;
  focus: string;
}

const light: Palette = {
  bg: '#FFF8F2',
  surface: '#FFFFFF',
  surfaceAlt: '#F6EDE6',
  text: '#1A1717',
  textMuted: '#5C5352',
  primary: '#8C2129',
  onPrimary: '#FFFFFF',
  accent: '#F09E6B',
  border: '#E3D6CC',
  correct: '#1B6E3A',
  wrong: '#B3261E',
  warning: '#8A5300',
  badgeBg: '#F3E7DD',
  badgeText: '#4A2A1C',
  focus: '#1F5FBF',
};

const dark: Palette = {
  bg: '#141011',
  surface: '#1F1A1B',
  surfaceAlt: '#2A2324',
  text: '#F5EFEA',
  textMuted: '#BFB3AE',
  primary: '#F08A90',
  onPrimary: '#2A0A0D',
  accent: '#F09E6B',
  border: '#3A3132',
  correct: '#6FD08F',
  wrong: '#FF8A80',
  warning: '#F2C46D',
  badgeBg: '#2E2526',
  badgeText: '#F1DCCD',
  focus: '#8AB4F8',
};

const lightHC: Palette = {
  ...light,
  bg: '#FFFFFF',
  surface: '#FFFFFF',
  surfaceAlt: '#F0F0F0',
  text: '#000000',
  textMuted: '#1F1F1F',
  primary: '#5E0A12',
  border: '#000000',
  correct: '#004D1A',
  wrong: '#7A0000',
  warning: '#5A3500',
  badgeBg: '#FFFFFF',
  badgeText: '#000000',
  focus: '#0033CC',
};

const darkHC: Palette = {
  ...dark,
  bg: '#000000',
  surface: '#000000',
  surfaceAlt: '#141414',
  text: '#FFFFFF',
  textMuted: '#EDEDED',
  primary: '#FFD54F',
  onPrimary: '#000000',
  border: '#FFFFFF',
  correct: '#7CFC9A',
  wrong: '#FF9E9E',
  warning: '#FFE082',
  badgeBg: '#000000',
  badgeText: '#FFFFFF',
  focus: '#FFFF00',
};

export function palette(scheme: 'light' | 'dark', highContrast: boolean): Palette {
  if (scheme === 'dark') return highContrast ? darkHC : dark;
  return highContrast ? lightHC : light;
}

/** 기본 글자 크기(sp). 사용자 배율(설정)과 기기 글자 크기가 함께 곱해진다 */
export const TYPE = {
  title: 26,
  heading: 20,
  body: 17,
  small: 14,
  stem: 19,
} as const;

export const SPACE = { xs: 4, s: 8, m: 12, l: 16, xl: 24, xxl: 32 } as const;

/** 손가락으로 누르기 쉬운 최소 크기 */
export const MIN_TOUCH = 48;
