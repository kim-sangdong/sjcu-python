import { createHash } from 'crypto';

import { buildPack, checkForUpdate, freePool, loadFromCache, loadSeed, resolveRelative, subjectUrls, type PackIO } from '../pack/sync';
import type { Item, Manifest } from '../pack/types';
import { checkItem, compareVersions, deckItemIds, isSafeRelativePath, normalizeAnswer, validateManifest, validateSubjectFile } from '../pack/validate';

const enc = (s: string) => new Uint8Array(Buffer.from(s, 'utf-8'));
const sha = (b: Uint8Array) => createHash('sha256').update(b).digest('hex');

function law(article = '제3조 제1항') {
  return { kind: 'law', law_name: '주택임대차보호법', article, effective_date: '2026-01-01' };
}

function item(id: string, subject: string, extra: Partial<Item> = {}): Item {
  return {
    id,
    subject,
    unit: '단원',
    type: 'ox',
    stem: `${id} 지문`,
    answer: true,
    explanation: '해설',
    trap: '함정',
    basis: [law()],
    difficulty: 1,
    free: true,
    ...extra,
  } as Item;
}

function makeRemote(version: string, subjects: Record<string, Item[]>, extra: Partial<Manifest> = {}) {
  const files: Record<string, Uint8Array> = {};
  const list = Object.entries(subjects).map(([code, items]) => {
    const file = `subjects/${code}.json`;
    files[file] = enc(JSON.stringify({ items }));
    return { code, name: code, file, sha256: sha(files[file]!), count: items.length };
  });
  const manifest: Manifest = {
    version,
    generated_at: '2026-10-10T00:00:00Z',
    exam: { name: '공인중개사', round: 37, next_exam_date: '2026-10-31' },
    subjects: list,
    free_ids: Object.values(subjects).flat().filter((i) => i.free).map((i) => i.id),
    ...extra,
  };
  files['manifest.json'] = enc(JSON.stringify(manifest));
  return { manifest, files };
}

const BASE = 'https://x.supabase.co/storage/v1/object/public/media/bank/latest/manifest.json';

function memoryIO(remote: { files: Record<string, Uint8Array> }) {
  const disk = new Map<string, Uint8Array>();
  const fetched: string[] = [];
  const io: PackIO = {
    async fetchBytes(url) {
      fetched.push(url);
      const clean = url.split('?')[0]!;
      const latestDir = BASE.replace('manifest.json', '');
      const path = clean.startsWith(latestDir)
        ? clean.slice(latestDir.length)
        : '../' + clean.slice(latestDir.replace(/latest\/$/, '').length);
      const f = remote.files[path];
      if (!f) throw new Error('HTTP 404 ' + url);
      return f;
    },
    async sha256Hex(b) {
      return sha(b);
    },
    decodeUtf8: (b) => Buffer.from(b).toString('utf-8'),
    encodeUtf8: enc,
    async readFile(p) {
      return disk.get(p) ?? null;
    },
    async writeFile(p, b) {
      disk.set(p, b);
    },
    async removeDir(p) {
      for (const k of [...disk.keys()]) if (k.startsWith(p + '/')) disk.delete(k);
    },
    async listDirs(p) {
      const dirs = new Set<string>();
      for (const k of disk.keys()) if (k.startsWith(p + '/')) dirs.add(k.slice(p.length + 1).split('/')[0]!);
      return [...dirs];
    },
  };
  return { io, disk, fetched };
}

describe('매니페스트 검증', () => {
  const ok = makeRemote('2026.10.10-1', { civil: [item('c1', 'civil')] }).manifest;

  test('정상 매니페스트 통과, sha256 소문자 정규화', () => {
    const m = { ...ok, subjects: ok.subjects.map((s) => ({ ...s, sha256: s.sha256.toUpperCase() })) };
    const r = validateManifest(m);
    expect(r.ok).toBe(true);
    expect(r.value!.subjects[0]!.sha256).toBe(ok.subjects[0]!.sha256);
  });

  test('필수값 누락·날짜 오류·경로 탈출을 거부', () => {
    expect(validateManifest({ ...ok, version: '' }).ok).toBe(false);
    expect(validateManifest({ ...ok, exam: { ...ok.exam, next_exam_date: '2026-02-30' } }).ok).toBe(false);
    expect(validateManifest({ ...ok, subjects: [{ ...ok.subjects[0]!, file: '../secret.json' }] }).ok).toBe(false);
    expect(validateManifest({ ...ok, subjects: [{ ...ok.subjects[0]!, sha256: 'abc' }] }).ok).toBe(false);
    expect(validateManifest({ ...ok, decks: { a1: 'x' } }).ok).toBe(false);
    expect(validateManifest(null).ok).toBe(false);
  });

  test('decks 는 배열 또는 {item_ids} 객체', () => {
    expect(validateManifest({ ...ok, decks: { a1: ['c1'], a2: { title: 't', item_ids: ['c1'] } } }).ok).toBe(true);
  });

  test('문제은행 내보내기 형식(decks 배열)을 asset_id 객체로 바꿔 읽는다', () => {
    const r = validateManifest({
      ...ok,
      decks: [{ asset_id: '2026W42-S03', week: '2026-W42', kind: 'short', items: ['c1', 'c2'] }],
    });
    expect(r.ok).toBe(true);
    expect(deckItemIds(r.value!.decks!['2026W42-S03'])).toEqual(['c1', 'c2']);
    expect(validateManifest({ ...ok, decks: [{ asset_id: 'x' }] }).ok).toBe(false);
  });

  test('안전한 상대 경로', () => {
    expect(isSafeRelativePath('subjects/civil.json')).toBe(true);
    expect(isSafeRelativePath('/etc/passwd')).toBe(false);
    expect(isSafeRelativePath('a/../b.json')).toBe(false);
    expect(isSafeRelativePath('a//b.json')).toBe(false);
  });
});

describe('문항 검증·정답 정규화', () => {
  test('근거 없는 문항, 판례 필수값 누락은 버림', () => {
    expect(checkItem(item('a', 's', { basis: [] })).ok).toBe(false);
    expect(checkItem(item('a', 's', { basis: [{ kind: 'law', law_name: '민법' }] })).ok).toBe(false);
    expect(checkItem(item('a', 's', { basis: [{ kind: 'precedent', case_no: '2012다1', decided_at: '2013-01-24' }] })).ok).toBe(true);
    expect(checkItem(item('a', 's', { basis: [{ kind: 'precedent', case_no: '2012다1' }] })).ok).toBe(false);
    expect(checkItem(item('a', 's', { basis: [{ kind: 'case', case_no: '2012다1', decided_at: '2013-01-24' }] })).ok).toBe(true);
    expect(checkItem(item('a', 's', { basis: [{ kind: 'calc', quote: 'x' }] })).ok).toBe(true);
  });

  test('과목 코드가 다르면 버림', () => {
    expect(checkItem(item('a', 'civil'), 'tax').ok).toBe(false);
  });

  test('유형별 정답 형식', () => {
    expect(normalizeAnswer({ type: 'ox', answer: 'X' })).toEqual({ type: 'ox', value: false });
    expect(normalizeAnswer({ type: 'ox', answer: 1 })).toBeNull();
    // 팩 인덱스는 0부터, 앱 표준형은 1부터(화면 보기 번호)
    expect(normalizeAnswer({ type: 'mcq', answer: 4, choices: ['1', '2', '3', '4', '5'] })).toEqual({ type: 'mcq', index: 5 });
    expect(normalizeAnswer({ type: 'mcq', answer: 5, choices: ['1', '2', '3', '4', '5'] })).toBeNull();
    expect(normalizeAnswer({ type: 'mcq', answer: 0, choices: ['1', '2'] })).toEqual({ type: 'mcq', index: 1 });
    expect(normalizeAnswer({ type: 'mcq', answer: -1, choices: ['1', '2'] })).toBeNull();
    expect(normalizeAnswer({ type: 'mcq', answer: 1.5, choices: ['1', '2'] })).toBeNull();
    expect(normalizeAnswer({ type: 'blank', answer: ['2년', '이년'] })).toEqual({ type: 'blank', accepted: ['2년', '이년'] });
    expect(normalizeAnswer({ type: 'blank', answer: '60일', choices: ['30일', '60일'] })).toEqual({
      type: 'blank',
      accepted: ['60일'],
      choiceIndex: 2,
    });
    expect(normalizeAnswer({ type: 'blank', answer: '90일', choices: ['30일', '60일'] })).toBeNull();
    expect(normalizeAnswer({ type: 'order', answer: [2, 0, 1], choices: ['a', 'b', 'c'] })).toEqual({ type: 'order', sequence: [3, 1, 2] });
    expect(normalizeAnswer({ type: 'order', answer: [3, 1, 2], choices: ['a', 'b', 'c'] })).toBeNull();
    expect(normalizeAnswer({ type: 'order', answer: ['c', 'a', 'b'], choices: ['a', 'b', 'c'] })).toEqual({
      type: 'order',
      sequence: [3, 1, 2],
    });
    expect(normalizeAnswer({ type: 'order', answer: [0, 0, 1], choices: ['a', 'b', 'c'] })).toBeNull();
    expect(normalizeAnswer({ type: 'calc', answer: { value: 1.25, tolerance: 0.001 } })).toEqual({
      type: 'calc',
      value: 1.25,
      tolerance: 0.001,
      unit: undefined,
    });
    expect(normalizeAnswer({ type: 'calc', answer: 1, choices: ['a', 'b'] })).toEqual({ type: 'calc', choiceIndex: 2, tolerance: 0 });
    expect(normalizeAnswer({ type: 'calc', answer: 3600000 })).toEqual({ type: 'calc', value: 3600000, tolerance: 0 });
    expect(normalizeAnswer({ type: 'blank', answer: '2년' })).toEqual({ type: 'blank', accepted: ['2년'] });
  });

  test('과목 파일: 잘못된 문항·중복 id 만 빼고 나머지는 살림', () => {
    const r = validateSubjectFile({ items: [item('a', 's'), item('a', 's'), { id: 'bad' }, item('b', 's')] }, 's');
    expect(r.ok).toBe(true);
    expect(r.items.map((i) => i.id)).toEqual(['a', 'b']);
    expect(r.dropped).toHaveLength(2);
    expect(validateSubjectFile({ nope: [] }).ok).toBe(false);
  });

  test('버전 비교', () => {
    expect(compareVersions('1.2.0', '1.10.0')).toBeLessThan(0);
    expect(compareVersions('1.0.0', '1.0')).toBe(0);
    expect(compareVersions('2.0.1', '2.0.0')).toBeGreaterThan(0);
  });
});

describe('팩 적재·갱신', () => {
  test('상대 경로 해석(쿼리 제거)', () => {
    expect(resolveRelative(BASE + '?v=1', 'subjects/a.json')).toBe(
      'https://x.supabase.co/storage/v1/object/public/media/bank/latest/subjects/a.json',
    );
  });

  test('과목 파일은 버전 고정 사본(bank/<version>/)을 먼저, 없으면 같은 폴더', () => {
    expect(subjectUrls(BASE, '20261010-abcd1234', 'CIV.json')).toEqual([
      'https://x.supabase.co/storage/v1/object/public/media/bank/20261010-abcd1234/CIV.json',
      'https://x.supabase.co/storage/v1/object/public/media/bank/latest/CIV.json',
    ]);
    expect(subjectUrls('https://cdn.example.kr/pack/manifest.json?x=1', 'v1', 'CIV.json')).toEqual([
      'https://cdn.example.kr/pack/CIV.json',
    ]);
  });

  test('버전 사본이 sha 가 맞으면 latest 사본은 받지 않음', async () => {
    const remote = makeRemote('v7', { civil: [item('c1', 'civil')] });
    remote.files['../v7/subjects/civil.json'] = remote.files['subjects/civil.json']!;
    const mem = memoryIO(remote);
    const r = await checkForUpdate(mem.io, { manifestUrl: BASE, appVersion: '1.0.0', current: null, bust: '1' });
    expect(r.status).toBe('updated');
    expect(mem.fetched.filter((u) => u.includes('civil.json'))).toEqual([
      'https://x.supabase.co/storage/v1/object/public/media/bank/v7/subjects/civil.json',
    ]);
  });

  test('처음 갱신: 내려받고 sha256 확인 후 캐시에 쓰고, 캐시에서 다시 읽힘', async () => {
    const remote = makeRemote('v1', { civil: [item('c1', 'civil'), item('c2', 'civil', { free: false })], tax: [item('t1', 'tax')] });
    const { io } = memoryIO(remote);
    const r = await checkForUpdate(io, { manifestUrl: BASE, appVersion: '1.0.0', current: null, bust: '1' });
    expect(r.status).toBe('updated');
    if (r.status !== 'updated') return;
    expect(r.pack.items).toHaveLength(3);
    expect(freePool(r.pack).map((i) => i.id).sort()).toEqual(['c1', 't1']);
    const cached = await loadFromCache(io);
    expect(cached?.source).toBe('cache');
    expect(cached?.manifest.version).toBe('v1');
  });

  test('같은 버전이면 up-to-date, 바뀐 과목만 다시 받음, 옛 폴더 삭제', async () => {
    const v1 = makeRemote('v1', { civil: [item('c1', 'civil')], tax: [item('t1', 'tax')] });
    const mem = memoryIO(v1);
    const first = await checkForUpdate(mem.io, { manifestUrl: BASE, appVersion: '1.0.0', current: null, bust: '1' });
    if (first.status !== 'updated') throw new Error('x');
    expect((await checkForUpdate(mem.io, { manifestUrl: BASE, appVersion: '1.0.0', current: first.pack, bust: '2' })).status).toBe(
      'up-to-date',
    );
    const v2 = makeRemote('v2', { civil: [item('c1', 'civil')], tax: [item('t1', 'tax'), item('t2', 'tax')] });
    Object.assign(v1.files, v2.files);
    mem.fetched.length = 0;
    const second = await checkForUpdate(mem.io, { manifestUrl: BASE, appVersion: '1.0.0', current: first.pack, bust: '3' });
    expect(second.status).toBe('updated');
    expect(mem.fetched.some((u) => u.includes('subjects/civil.json'))).toBe(false);
    expect(mem.fetched.some((u) => u.includes('subjects/tax.json'))).toBe(true);
    expect([...mem.disk.keys()].some((k) => k.startsWith('v/v1/'))).toBe(false);
  });

  test('sha256 가 다르면 갱신하지 않고 캐시도 바꾸지 않음', async () => {
    const remote = makeRemote('v1', { civil: [item('c1', 'civil')] });
    remote.files['subjects/civil.json'] = enc(JSON.stringify({ items: [item('c1', 'civil'), item('evil', 'civil')] }));
    const { io, disk } = memoryIO(remote);
    const r = await checkForUpdate(io, { manifestUrl: BASE, appVersion: '1.0.0', current: null, bust: '1' });
    expect(r.status).toBe('error');
    expect(disk.size).toBe(0);
  });

  test('원격 팩이 0문항이면 지금 팩(시드 포함)을 유지하고 캐시도 바꾸지 않음', async () => {
    // studio 리허설 2026-W42: 검증 문항 없이 export 한 빈 팩이 latest/ 에 올라가면 앱 문항이 모두 사라질 수 있었다
    const seed = loadSeed({
      manifest: makeRemote('seed', { civil: [item('c1', 'civil')] }).manifest,
      files: { 'subjects/civil.json': { items: [item('c1', 'civil')] } },
    });
    const empty = makeRemote('v-empty', { civil: [], tax: [] });
    const { io, disk } = memoryIO(empty);
    const r = await checkForUpdate(io, { manifestUrl: BASE, appVersion: '1.0.0', current: seed, bust: '1' });
    expect(r.status).toBe('error');
    expect(disk.size).toBe(0);
    // 지금 팩이 없으면(첫 실행) 빈 팩도 받아 둔다 — 비교할 것이 없으므로
    const first = await checkForUpdate(memoryIO(empty).io, { manifestUrl: BASE, appVersion: '1.0.0', current: null, bust: '1' });
    expect(first.status).toBe('updated');
  });

  test('min_app_version 이 더 높으면 incompatible', async () => {
    const remote = makeRemote('v9', { civil: [item('c1', 'civil')] }, { min_app_version: '2.0.0' });
    const { io } = memoryIO(remote);
    const r = await checkForUpdate(io, { manifestUrl: BASE, appVersion: '1.4.0', current: null, bust: '1' });
    expect(r).toEqual({ status: 'incompatible', required: '2.0.0' });
  });

  test('손상된 캐시는 쓰지 않음(null → 시드로 대체)', async () => {
    const remote = makeRemote('v1', { civil: [item('c1', 'civil')] });
    const mem = memoryIO(remote);
    await checkForUpdate(mem.io, { manifestUrl: BASE, appVersion: '1.0.0', current: null, bust: '1' });
    mem.disk.set('v/v1/subjects/civil.json', enc('{"items": []}'));
    expect(await loadFromCache(mem.io)).toBeNull();
  });

  test('네트워크 오류는 error 로 끝나고 예외를 던지지 않음', async () => {
    const { io } = memoryIO({ files: {} });
    const r = await checkForUpdate(io, { manifestUrl: BASE, appVersion: '1.0.0', current: null, bust: '1' });
    expect(r.status).toBe('error');
  });

  test('disabled_ids 문항은 빠짐', () => {
    const { manifest } = makeRemote('v1', { civil: [item('c1', 'civil'), item('c2', 'civil')] }, { extra: { disabled_ids: ['c2'] } });
    const p = buildPack(manifest, [[item('c1', 'civil'), item('c2', 'civil')]], 'remote');
    expect(p.items.map((i) => i.id)).toEqual(['c1']);
    expect(p.byId.has('c2')).toBe(false);
  });

  test('loadSeed: 매니페스트가 깨지면 예외', () => {
    expect(() => loadSeed({ manifest: {}, files: {} })).toThrow();
  });
});
