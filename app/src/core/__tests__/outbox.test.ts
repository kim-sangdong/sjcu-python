import { backoffMs, classifyStatus, pickColumns, pushAnswer, shouldGiveUp, supabaseHeaders } from '../outbox';

describe('전송 큐 규칙', () => {
  test('응답 코드 분류', () => {
    expect(classifyStatus(201)).toBe('sent');
    expect(classifyStatus(204)).toBe('sent');
    expect(classifyStatus(409)).toBe('sent'); // app_installs 고유키 중복 = 이미 들어감
    expect(classifyStatus(0)).toBe('retry');
    expect(classifyStatus(429)).toBe('retry');
    expect(classifyStatus(503)).toBe('retry');
    expect(classifyStatus(400)).toBe('drop');
    expect(classifyStatus(401)).toBe('drop');
    expect(classifyStatus(403)).toBe('drop');
  });

  test('지수 백오프(30초 시작, 1시간 상한, ±20%)', () => {
    expect(backoffMs(1, () => 0.5)).toBe(30_000);
    expect(backoffMs(2, () => 0.5)).toBe(60_000);
    expect(backoffMs(30, () => 0.5)).toBe(3_600_000);
    expect(backoffMs(1, () => 0)).toBe(24_000);
    expect(backoffMs(1, () => 0.999999)).toBeLessThanOrEqual(36_000);
  });

  test('포기 조건: 20회 또는 14일', () => {
    const now = Date.UTC(2026, 9, 10);
    expect(shouldGiveUp(20, now, now)).toBe(true);
    expect(shouldGiveUp(3, now - 15 * 86_400_000, now)).toBe(true);
    expect(shouldGiveUp(3, now - 86_400_000, now)).toBe(false);
  });

  test('계약에 없는 열은 뺀다', () => {
    expect(pickColumns('app_events', { app_user_id: 'u', name: 'x', props: {}, created_at: 1, extra: 2 })).toEqual({
      app_user_id: 'u',
      name: 'x',
      props: {},
    });
    expect(Object.keys(pickColumns('app_installs', { app_user_id: 'u', os: 'android', utm_term: 'k' }))).toEqual([
      'app_user_id',
      'os',
    ]);
  });

  test('answers_batch: 20개씩 묶고 나머지는 버퍼에 남김', () => {
    let buf: { id: string; correct: boolean }[] = [];
    const batches: unknown[][] = [];
    for (let i = 0; i < 45; i++) {
      const r = pushAnswer(buf, { id: `q${i}`, correct: i % 2 === 0 });
      buf = r.buffer;
      batches.push(...r.batches);
    }
    expect(batches).toHaveLength(2);
    expect(batches[0]).toHaveLength(20);
    expect(buf).toHaveLength(5);
    expect(buf[0]).toEqual({ id: 'q40', correct: true });
  });

  test('Supabase 헤더: 레거시 JWT anon 키는 Bearer 도, 새 publishable 키는 apikey 만', () => {
    expect(supabaseHeaders('eyJhbGciOi.x.y')).toMatchObject({ apikey: 'eyJhbGciOi.x.y', Authorization: 'Bearer eyJhbGciOi.x.y' });
    const h = supabaseHeaders('sb_publishable_abc');
    expect(h.apikey).toBe('sb_publishable_abc');
    expect(h.Authorization).toBeUndefined();
    expect(h.Prefer).toBe('return=minimal');
  });
});
