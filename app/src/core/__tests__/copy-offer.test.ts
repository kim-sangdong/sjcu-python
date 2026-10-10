import * as fs from 'fs';
import * as path from 'path';

import { DISCLAIMER_UNOFFICIAL, findBanned } from '../copy';
import { cancelHowTo, periodDays, periodLabel, planDisclosure } from '../offer';

const SRC = path.resolve(__dirname, '../..');

function walk(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) return e.name === '__tests__' ? [] : walk(p);
    return /\.(ts|tsx)$/.test(e.name) ? [p] : [];
  });
}

describe('금지 표현', () => {
  test('비공식 고지는 들어 있고, "비공식"은 금지어로 잡지 않는다', () => {
    expect(DISCLAIMER_UNOFFICIAL).toContain('비공식 학습 앱');
    expect(DISCLAIMER_UNOFFICIAL).toContain('한국산업인력공단·큐넷과 무관한');
    expect(findBanned(DISCLAIMER_UNOFFICIAL)).toEqual([]);
  });

  test('금지어를 잡는다', () => {
    expect(findBanned('합격 보장 이벤트')).not.toEqual([]);
    expect(findBanned('적중률 90%')).not.toEqual([]);
    expect(findBanned('공식 앱')).not.toEqual([]);
    expect(findBanned('큐넷 제공 문제')).not.toEqual([]);
    expect(findBanned('합격 가능성 80%')).not.toEqual([]);
  });

  test('src 의 모든 화면 문구에 금지 표현이 없다(copy.ts 목록 자체는 제외)', () => {
    const offenders: string[] = [];
    const files = walk(SRC);
    expect(files.length).toBeGreaterThan(30);
    expect(files.some((f) => f.endsWith('paywall.tsx'))).toBe(true);
    for (const f of files) {
      if (f.endsWith(path.join('core', 'copy.ts'))) continue;
      const text = fs.readFileSync(f, 'utf-8');
      // 문자열 리터럴·JSX 텍스트만 대략 추출(주석 포함 — 주석에도 쓰지 않는다)
      const hits = findBanned(text);
      if (hits.length) offenders.push(`${path.relative(SRC, f)}: ${hits.join(', ')}`);
    }
    expect(offenders).toEqual([]);
  });

  test('시드 팩 문구에도 금지 표현이 없다', () => {
    const seedDir = path.resolve(SRC, '../assets/seed');
    const files = fs.readdirSync(seedDir).filter((f) => f.endsWith('.json')).map((f) => path.join(seedDir, f));
    expect(files.length).toBeGreaterThan(1);
    for (const f of files) expect([path.basename(f), findBanned(fs.readFileSync(f, 'utf-8'))]).toEqual([path.basename(f), []]);
  });
});

describe('결제 전 고지', () => {
  test('기간 표기', () => {
    expect(periodLabel('P1M')).toBe('1개월');
    expect(periodLabel('P1Y')).toBe('1년');
    expect(periodLabel('P2W')).toBe('14일');
    expect(periodLabel(null)).toBe('');
    expect(periodDays('P14D')).toBe(14);
    expect(periodDays('P2W')).toBe(14);
    expect(periodDays('bogus')).toBeNull();
  });

  test('자동 갱신 상품: 가격·주기·자동 갱신·해지 방법이 모두 들어감(iOS/Android)', () => {
    for (const platform of ['ios', 'android'] as const) {
      const lines = planDisclosure({ kind: 'monthly', priceString: '₩9,900', period: 'P1M', trialDays: null }, platform).join(' ');
      expect(lines).toContain('₩9,900');
      expect(lines).toContain('1개월');
      expect(lines).toContain('자동');
      expect(lines).toContain('해지');
      expect(lines).toContain(cancelHowTo(platform));
    }
  });

  test('체험 있는 연 구독: 체험 일수와 체험 후 결제 금액', () => {
    const lines = planDisclosure({ kind: 'annual', priceString: '₩69,000', period: 'P1Y', trialDays: 14 }, 'ios').join(' ');
    expect(lines).toContain('무료 체험 14일');
    expect(lines).toContain('₩69,000');
    expect(lines).toContain('24시간 전');
  });

  test('패스: 자동 갱신되지 않음을 명시, 기간은 스토어 값(없으면 60일)', () => {
    const lines = planDisclosure({ kind: 'pass', priceString: '₩29,000', period: null, trialDays: null }, 'android').join(' ');
    expect(lines).toContain('자동 갱신되지 않으며');
    expect(lines).toContain('60일');
    const prepaid = planDisclosure({ kind: 'pass', priceString: '₩29,000', period: 'P2M', trialDays: null }, 'android').join(' ');
    expect(prepaid).toContain('2개월 동안');
    expect(prepaid).not.toContain('60일');
  });
});
