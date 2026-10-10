/**
 * studio(파이썬) `bank export` 가 실제로 만든 팩을 앱 검증기에 그대로 넣어 본다 — 두 쪽 계약 확인.
 *
 * fixtures/studio-pack/ 는 2026-W42 리허설에서 `python -m studio bank export --allow-unchecked` 로 만든 파일이다
 * (시드 39문항을 형식 확인용으로 verified 표시한 스크래치 사본에서 — 문항 내용은 검증 전). 파이썬 쪽은
 * tests/test_bank_export.py::test_app_contract_fixture_matches_pack_schema 가 같은 파일을 pack_schema.json 으로 검사한다.
 * 내보내기 형식을 바꾸면 같은 명령으로 다시 만들어 두 테스트를 함께 통과시킨다.
 */
import { createHash } from 'crypto';
import { readFileSync, readdirSync } from 'fs';
import { join } from 'path';

import { loadSeed } from '../pack/sync';
import { checkItem, deckItemIds, normalizeAnswer, validateManifest, validateSubjectFile } from '../pack/validate';

const DIR = join(__dirname, 'fixtures', 'studio-pack');
const read = (f: string) => readFileSync(join(DIR, f));
const manifestRaw = JSON.parse(read('manifest.json').toString('utf-8'));

describe('studio bank export 산출물 ↔ 앱 검증 규칙', () => {
  test('매니페스트가 그대로 통과(decks 배열 → asset_id 객체 변환 포함)', () => {
    const mv = validateManifest(manifestRaw);
    expect(mv.errors).toEqual([]);
    expect(mv.ok).toBe(true);
  });

  test('과목 파일: sha256 일치, 버리는 문항 없음, 모든 정답이 표준형으로 바뀜', () => {
    const mv = validateManifest(manifestRaw);
    let total = 0;
    const problems: string[] = [];
    for (const s of mv.value!.subjects) {
      const bytes = read(s.file);
      expect(createHash('sha256').update(bytes).digest('hex')).toBe(s.sha256);
      const raw = JSON.parse(bytes.toString('utf-8'));
      const v = validateSubjectFile(raw, s.code);
      problems.push(...v.dropped);
      expect(v.items).toHaveLength(s.count);
      for (const it of raw.items) {
        if (normalizeAnswer(it) === null) problems.push(`${it.id}: 정답 형식`);
        const c = checkItem(it, s.code);
        if (!c.ok) problems.push(c.reason!);
      }
      total += v.items.length;
    }
    expect(problems).toEqual([]);
    expect(total).toBe(manifestRaw.total);
    expect(total).toBeGreaterThan(0);
  });

  test('무료 id·영상 연동 덱이 실제 문항을 가리킴', () => {
    const files: Record<string, unknown> = {};
    for (const f of readdirSync(DIR)) if (f !== 'manifest.json') files[f] = JSON.parse(read(f).toString('utf-8'));
    const pack = loadSeed({ manifest: manifestRaw, files });
    expect(pack.items).toHaveLength(manifestRaw.total);
    const mv = validateManifest(manifestRaw);
    for (const id of mv.value!.free_ids) expect(pack.byId.has(id)).toBe(true);
    const decks = Object.entries(mv.value!.decks ?? {});
    expect(decks.length).toBeGreaterThan(0);
    for (const [, v] of decks) for (const id of deckItemIds(v as never)!) expect(pack.byId.has(id)).toBe(true);
  });
});
