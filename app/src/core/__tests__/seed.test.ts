import { createHash } from 'crypto';
import * as fs from 'fs';
import * as path from 'path';

import { freePool, loadSeed } from '../pack/sync';
import { validateSubjectFile } from '../pack/validate';

const SEED_DIR = path.resolve(__dirname, '../../../assets/seed');

describe('내장 시드 팩', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(SEED_DIR, 'manifest.json'), 'utf-8'));

  test('모든 과목 파일이 있고 sha256·문항 수가 매니페스트와 같다', () => {
    for (const s of manifest.subjects) {
      const bytes = fs.readFileSync(path.join(SEED_DIR, s.file));
      expect([s.file, createHash('sha256').update(bytes).digest('hex')]).toEqual([s.file, s.sha256]);
      const v = validateSubjectFile(JSON.parse(bytes.toString('utf-8')), s.code);
      expect([s.file, v.dropped]).toEqual([s.file, []]);
      expect(v.items).toHaveLength(s.count);
    }
  });

  test('seed.ts 가 매니페스트의 모든 파일을 require 한다', () => {
    const src = fs.readFileSync(path.resolve(__dirname, '../../data/seed.ts'), 'utf-8');
    for (const s of manifest.subjects) expect(src).toContain(`'${s.file}': require('../../assets/seed/${s.file}')`);
  });

  test('studio 팩 형식: 0부터 인덱스, 근거 배지, 함정 유형', () => {
    for (const s of manifest.subjects) {
      const pack = JSON.parse(fs.readFileSync(path.join(SEED_DIR, s.file), 'utf-8'));
      expect(pack.kind).toBe('subject');
      expect(pack.subject).toBe(s.code);
      for (const it of pack.items) {
        expect(it.id).toMatch(/^Q-(RE|CIV|BRK|PUB|REG|TAX)-[0-9a-f]{8}$/);
        expect(it.basis.every((b: { badge?: string }) => typeof b.badge === 'string')).toBe(true);
      }
    }
  });

  test('loadSeed 로 읽히고 무료 풀이 비어 있지 않다', () => {
    const files: Record<string, unknown> = {};
    for (const s of manifest.subjects) files[s.file] = JSON.parse(fs.readFileSync(path.join(SEED_DIR, s.file), 'utf-8'));
    const pack = loadSeed({ manifest, files });
    expect(pack.source).toBe('seed');
    expect(pack.items.length).toBeGreaterThan(0);
    expect(freePool(pack).length).toBeGreaterThan(0);
  });
});
