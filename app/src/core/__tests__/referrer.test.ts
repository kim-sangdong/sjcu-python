import { isOrganic, parseReferrer, revenueCatAttributes, truncateRaw, utmFromParams } from '../referrer';

describe('Play 설치 리퍼러 파싱', () => {
  test('일반 형식', () => {
    expect(
      parseReferrer('utm_source=yt_shorts&utm_medium=pinned_comment&utm_campaign=2026w41&utm_content=s03'),
    ).toEqual({
      utm_source: 'yt_shorts',
      utm_medium: 'pinned_comment',
      utm_campaign: '2026w41',
      utm_content: 's03',
      utm_term: null,
    });
  });

  test('한 번 더 URL 인코딩된 형식', () => {
    const r = parseReferrer('utm_source%3Dyoutube%26utm_medium%3Dpinned_comment%26utm_campaign%3D2026w41');
    expect(r.utm_source).toBe('youtube');
    expect(r.utm_medium).toBe('pinned_comment');
    expect(r.utm_campaign).toBe('2026w41');
  });

  test('Play 기본값(organic)', () => {
    const r = parseReferrer('utm_source=google-play&utm_medium=organic');
    expect(isOrganic(r)).toBe(true);
    expect(isOrganic(parseReferrer('utm_source=tiktok'))).toBe(false);
    expect(isOrganic(parseReferrer(''))).toBe(true);
  });

  test('값 디코딩·공백·제어문자·길이 제한, 중복 키는 처음 값, utm 아닌 키 무시', () => {
    const long = 'a'.repeat(300);
    const r = parseReferrer(`?utm_source=ig_card&utm_source=x&gclid=abc&utm_campaign=%ED%95%A8%EC%A0%95+%EB%85%B8%ED%8A%B8&utm_content=${long}&utm_medium=%0A`);
    expect(r.utm_source).toBe('ig_card');
    expect(r.utm_campaign).toBe('함정 노트');
    expect(r.utm_content).toHaveLength(100);
    expect(r.utm_medium).toBeNull();
  });

  test('깨진 퍼센트 인코딩에도 예외 없음', () => {
    expect(() => parseReferrer('utm_source=%E0%A4%A&utm_medium=x')).not.toThrow();
    expect(parseReferrer('utm_source=%E0%A4%A').utm_source).toBe('%E0%A4%A');
  });

  test('null·빈값', () => {
    expect(parseReferrer(null).utm_source).toBeNull();
    expect(truncateRaw(null)).toBeNull();
    expect(truncateRaw('x'.repeat(900))).toHaveLength(500);
  });

  test('RevenueCat 속성: 예약 키 + utm_* 커스텀 키, 빈 값은 넣지 않음', () => {
    const attrs = revenueCatAttributes(parseReferrer('utm_source=yt_shorts&utm_medium=desc&utm_campaign=w41'));
    expect(attrs).toEqual({
      $mediaSource: 'yt_shorts',
      $campaign: 'w41',
      $adGroup: 'desc',
      utm_source: 'yt_shorts',
      utm_medium: 'desc',
      utm_campaign: 'w41',
    });
    expect(revenueCatAttributes(parseReferrer(''))).toEqual({});
  });

  test('딥링크 쿼리에서 utm 만 추출', () => {
    expect(utmFromParams({ assetId: 'a1', utm_source: 'yt_shorts', utm_campaign: ['w41', 'x'], foo: 'bar' })).toEqual({
      utm_source: 'yt_shorts',
      utm_campaign: 'w41',
    });
  });
});
