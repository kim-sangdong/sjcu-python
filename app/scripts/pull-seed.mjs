#!/usr/bin/env node
/**
 * 내장 시드 팩 갱신: 검증을 마친 문항 팩(studio 가 만든 bank 팩)을 app/assets/seed 로 복사하고
 * src/data/seed.ts 를 다시 만든다. 출시 빌드 전에 반드시 실행한다(근거 대조 전 점검용 시드를 바꿈).
 *
 *   node scripts/pull-seed.mjs                 # ../out/bank (python -m studio bank export 결과) 사용
 *   node scripts/pull-seed.mjs --from <폴더>    # 다른 폴더
 *   node scripts/pull-seed.mjs --from <manifest URL>
 *   node scripts/pull-seed.mjs --all           # 무료 문항만이 아니라 전체를 넣음(앱 용량 증가)
 *
 * 과목 파일은 매니페스트의 sha256 과 대조한다. 기본은 무료 문항만 남겨 앱 용량을 줄이고,
 * 줄인 파일의 sha256·bytes·count 와 total·free_ids 를 다시 계산해 매니페스트에 쓴다.
 */
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readdirSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const APP = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const SEED_DIR = join(APP, 'assets', 'seed');
const SEED_TS = join(APP, 'src', 'data', 'seed.ts');
const SAFE = /^[A-Za-z0-9._-]+\.json$/;

function arg(name) {
  const i = process.argv.indexOf(name);
  return i >= 0 ? process.argv[i + 1] : undefined;
}
const all = process.argv.includes('--all');
const sha = (buf) => createHash('sha256').update(buf).digest('hex');

function fail(msg) {
  console.error(`오류: ${msg}`);
  process.exit(1);
}

function defaultSource() {
  const local = resolve(APP, '..', 'out', 'bank');
  if (existsSync(join(local, 'manifest.json'))) return local;
  const url = process.env.EXPO_PUBLIC_BANK_MANIFEST_URL;
  const base = process.env.EXPO_PUBLIC_SUPABASE_URL;
  if (url) return url;
  if (base) return `${base.replace(/\/+$/, '')}/storage/v1/object/public/media/bank/latest/manifest.json`;
  fail('팩을 찾지 못했습니다. `python -m studio bank export` 를 먼저 실행하거나 --from 을 주세요.');
}

async function reader(source) {
  if (/^https?:\/\//.test(source)) {
    const dir = source.split('?')[0].replace(/[^/]+$/, '');
    const get = async (url) => {
      const res = await fetch(url, { headers: { 'Cache-Control': 'no-cache' } });
      if (!res.ok) throw new Error(`HTTP ${res.status} ${url}`);
      return Buffer.from(await res.arrayBuffer());
    };
    const manifest = await get(source);
    const version = JSON.parse(manifest.toString('utf-8')).version;
    return {
      manifest,
      async file(name) {
        // studio 업로드는 bank/<version>/ 에 고정 사본을 둔다
        const versioned = dir.replace(/\/latest\/$/, `/${encodeURIComponent(version)}/`);
        try {
          return await get(versioned + name);
        } catch {
          return await get(dir + name);
        }
      },
    };
  }
  const dir = resolve(source);
  const mp = join(dir, 'manifest.json');
  if (!existsSync(mp)) fail(`${mp} 가 없습니다`);
  return { manifest: readFileSync(mp), file: async (name) => readFileSync(join(dir, name)) };
}

const source = arg('--from') ?? defaultSource();
const src = await reader(source);
const manifest = JSON.parse(src.manifest.toString('utf-8'));
if (!manifest.version || !Array.isArray(manifest.subjects) || !Array.isArray(manifest.free_ids)) {
  fail('매니페스트 형식이 아닙니다(version/subjects/free_ids)');
}

const freeIds = new Set(manifest.free_ids);
const outFiles = new Map();
const subjects = [];
let total = 0;
for (const s of manifest.subjects) {
  if (!SAFE.test(s.file)) fail(`허용하지 않는 파일 이름: ${s.file}`);
  const buf = await src.file(s.file);
  if (sha(buf) !== s.sha256) fail(`${s.file} sha256 이 매니페스트와 다릅니다(업로드 중일 수 있음 — 잠시 뒤 다시)`);
  const pack = JSON.parse(buf.toString('utf-8'));
  const items = all ? pack.items : pack.items.filter((it) => freeIds.has(it.id) || (freeIds.size === 0 && it.free));
  const out = { ...pack, count: items.length, items };
  const data = Buffer.from(JSON.stringify(out, null, 1) + '\n', 'utf-8');
  outFiles.set(s.file, data);
  subjects.push({ ...s, count: items.length, sha256: sha(data), bytes: data.length });
  total += items.length;
}
const kept = new Set([...outFiles.values()].flatMap((b) => JSON.parse(b.toString('utf-8')).items.map((i) => i.id)));
const extra = { ...(manifest.extra ?? {}) };
delete extra.unverified;
const outManifest = { ...manifest, total, subjects, free_ids: manifest.free_ids.filter((id) => kept.has(id)) };
if (Object.keys(extra).length) outManifest.extra = extra;
else delete outManifest.extra;
if (total === 0) fail('넣을 문항이 0개입니다(무료 문항이 없으면 --all 을 쓰세요)');

mkdirSync(SEED_DIR, { recursive: true });
for (const f of readdirSync(SEED_DIR)) if (f.endsWith('.json')) unlinkSync(join(SEED_DIR, f));
for (const [name, data] of outFiles) writeFileSync(join(SEED_DIR, name), data);
writeFileSync(join(SEED_DIR, 'manifest.json'), JSON.stringify(outManifest, null, 1) + '\n', 'utf-8');

const lines = [
  '// 이 파일은 scripts/pull-seed.mjs 가 만든다. 직접 고치지 말고 `npm run seed:pull` 을 다시 실행한다.',
  '// 앱에 내장되는 시드 팩(첫 실행·오프라인용). Metro 는 require 경로가 정적이어야 번들에 넣는다.',
  "import type { SeedBundle } from '@/core/pack/sync';",
  '',
  'export const SEED: SeedBundle = {',
  "  manifest: require('../../assets/seed/manifest.json'),",
  '  files: {',
  ...[...outFiles.keys()].map((f) => `    '${f}': require('../../assets/seed/${f}'),`),
  '  },',
  '};',
  '',
];
writeFileSync(SEED_TS, lines.join('\n'), 'utf-8');

console.log(`시드 팩 갱신: ${manifest.version} · ${subjects.length}과목 · ${total}문항${all ? '' : '(무료 문항만)'}`);
console.log(`  원천: ${source}`);
console.log('  다음: npx jest src/core/__tests__/seed.test.ts 로 확인한 뒤 빌드하세요.');
