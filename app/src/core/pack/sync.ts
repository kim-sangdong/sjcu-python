/**
 * 문항 팩 적재·갱신 로직(입출력은 주입받는다 → 단위 테스트 가능).
 *
 * 캐시 배치(앱 문서 폴더 아래 bank/):
 *   current.json                 {version, dir} — 마지막에 써서 교체를 원자적으로 만든다
 *   v/<dir>/manifest.json
 *   v/<dir>/<subjects[].file>
 *
 * 갱신 순서: 원격 매니페스트 → 검증 → 바뀐 과목 파일만 내려받기 → sha256 대조 → 구조 검증
 *           → 새 폴더에 모두 쓰기 → current.json 교체 → 옛 폴더 삭제.
 * 어느 단계든 실패하면 지금 쓰는 팩을 그대로 둔다.
 */
import type { Item, LoadedPack, Manifest } from './types';
import { compareVersions, validateManifest, validateSubjectFile } from './validate';

export interface PackIO {
  /** 2xx 가 아니면 예외 */
  fetchBytes(url: string): Promise<Uint8Array>;
  sha256Hex(bytes: Uint8Array): Promise<string>;
  decodeUtf8(bytes: Uint8Array): string;
  encodeUtf8(text: string): Uint8Array;
  readFile(path: string): Promise<Uint8Array | null>;
  writeFile(path: string, bytes: Uint8Array): Promise<void>;
  removeDir(path: string): Promise<void>;
  listDirs(path: string): Promise<string[]>;
}

export interface SeedBundle {
  manifest: unknown;
  /** subjects[].file → 파싱된 JSON */
  files: Record<string, unknown>;
}

export type UpdateResult =
  | { status: 'up-to-date' }
  | { status: 'updated'; pack: LoadedPack; dropped: string[] }
  | { status: 'incompatible'; required: string }
  | { status: 'error'; error: string };

const POINTER = 'current.json';

export function versionDirName(version: string): string {
  return version.replace(/[^A-Za-z0-9._-]/g, '_').slice(0, 80) || 'unknown';
}

export function resolveRelative(manifestUrl: string, file: string): string {
  const noQuery = manifestUrl.split(/[?#]/)[0] ?? manifestUrl;
  const base = noQuery.slice(0, noQuery.lastIndexOf('/') + 1);
  return base + file;
}

/**
 * 과목 파일 주소 후보. studio 업로드는 bank/<version>/ 에 버전 고정 사본을 두므로 그것을 먼저 쓰고
 * (latest/ 사본은 새 버전 업로드 중에 바뀔 수 있다), 없으면 매니페스트와 같은 폴더를 쓴다.
 */
export function subjectUrls(manifestUrl: string, version: string, file: string): string[] {
  const noQuery = manifestUrl.split(/[?#]/)[0] ?? manifestUrl;
  const same = resolveRelative(noQuery, file);
  const m = /^(.*\/)latest\/[^/]+$/.exec(noQuery);
  if (!m) return [same];
  const versioned = `${m[1]}${encodeURIComponent(version)}/${file}`;
  return versioned === same ? [same] : [versioned, same];
}

export function withCacheBust(url: string, token: string): string {
  return url + (url.includes('?') ? '&' : '?') + 'v=' + encodeURIComponent(token);
}

/** 매니페스트·문항으로 메모리 팩을 만든다. 비활성 문항은 뺀다. */
export function buildPack(
  manifest: Manifest,
  itemsBySubject: Item[][],
  source: LoadedPack['source'],
): LoadedPack {
  const disabled = new Set(manifest.extra?.disabled_ids ?? []);
  const items: Item[] = [];
  const byId = new Map<string, Item>();
  for (const list of itemsBySubject) {
    for (const it of list) {
      if (disabled.has(it.id) || byId.has(it.id)) continue;
      byId.set(it.id, it);
      items.push(it);
    }
  }
  return { manifest, items, byId, source };
}

/** 무료 출제 풀: free_ids 우선, 비어 있으면 문항의 free 플래그 */
export function freePool(pack: LoadedPack): Item[] {
  const ids = pack.manifest.free_ids.filter((id) => pack.byId.has(id));
  if (ids.length) return ids.map((id) => pack.byId.get(id)!);
  return pack.items.filter((it) => it.free);
}

function parseJson(io: PackIO, bytes: Uint8Array): unknown {
  return JSON.parse(io.decodeUtf8(bytes));
}

export function loadSeed(seed: SeedBundle): LoadedPack {
  const mv = validateManifest(seed.manifest);
  if (!mv.ok || !mv.value) throw new Error('내장 시드 매니페스트 오류: ' + mv.errors.join(', '));
  const lists = mv.value.subjects.map((s) => {
    const raw = seed.files[s.file];
    const v = validateSubjectFile(raw, s.code);
    return v.items;
  });
  return buildPack(mv.value, lists, 'seed');
}

interface Pointer {
  version: string;
  dir: string;
}

export async function loadFromCache(io: PackIO): Promise<LoadedPack | null> {
  try {
    const pb = await io.readFile(POINTER);
    if (!pb) return null;
    const ptr = parseJson(io, pb) as Pointer;
    if (!ptr || typeof ptr.dir !== 'string') return null;
    const mb = await io.readFile(`v/${ptr.dir}/manifest.json`);
    if (!mb) return null;
    const mv = validateManifest(parseJson(io, mb));
    if (!mv.ok || !mv.value) return null;
    const lists: Item[][] = [];
    for (const s of mv.value.subjects) {
      const fb = await io.readFile(`v/${ptr.dir}/${s.file}`);
      if (!fb) return null;
      if ((await io.sha256Hex(fb)) !== s.sha256) return null; // 손상된 캐시는 쓰지 않는다
      const sv = validateSubjectFile(parseJson(io, fb), s.code);
      if (!sv.ok) return null;
      lists.push(sv.items);
    }
    return buildPack(mv.value, lists, 'cache');
  } catch {
    return null;
  }
}

export interface UpdateOptions {
  manifestUrl: string;
  appVersion: string;
  current: LoadedPack | null;
  /** 캐시 우회용 토큰(보통 현재 시각) */
  bust: string;
}

export async function checkForUpdate(io: PackIO, opts: UpdateOptions): Promise<UpdateResult> {
  const { manifestUrl, appVersion, current } = opts;
  try {
    const mb = await io.fetchBytes(withCacheBust(manifestUrl, opts.bust));
    const mv = validateManifest(parseJson(io, mb));
    if (!mv.ok || !mv.value) return { status: 'error', error: 'manifest: ' + mv.errors.join('; ') };
    const remote = mv.value;

    if (current && current.source !== 'seed' && current.manifest.version === remote.version)
      return { status: 'up-to-date' };
    if (remote.min_app_version && compareVersions(appVersion, remote.min_app_version) < 0)
      return { status: 'incompatible', required: remote.min_app_version };

    // 같은 sha256 의 과목 파일은 지금 캐시에서 다시 쓴다(데이터 절약).
    let currentDir: string | null = null;
    if (current && current.source !== 'seed') {
      const pb = await io.readFile(POINTER);
      if (pb) currentDir = (parseJson(io, pb) as Pointer).dir ?? null;
    }
    const prevByFile = new Map(
      (current && current.source !== 'seed' ? current.manifest.subjects : []).map((s) => [s.file, s.sha256]),
    );

    const dir = versionDirName(remote.version);
    const files: { path: string; bytes: Uint8Array }[] = [];
    const lists: Item[][] = [];
    const dropped: string[] = [];
    for (const s of remote.subjects) {
      let bytes: Uint8Array | null = null;
      if (currentDir && prevByFile.get(s.file) === s.sha256) {
        bytes = await io.readFile(`v/${currentDir}/${s.file}`);
        if (bytes && (await io.sha256Hex(bytes)) !== s.sha256) bytes = null;
      }
      if (!bytes) {
        let lastErr = '';
        for (const url of subjectUrls(manifestUrl, remote.version, s.file)) {
          try {
            const b = await io.fetchBytes(url);
            if ((await io.sha256Hex(b)) === s.sha256) {
              bytes = b;
              break;
            }
            lastErr = `${s.file}: sha256 불일치`;
          } catch (e) {
            lastErr = e instanceof Error ? e.message : String(e);
          }
        }
        if (!bytes) return { status: 'error', error: lastErr || `${s.file}: 받기 실패` };
      }
      const sv = validateSubjectFile(parseJson(io, bytes), s.code);
      if (!sv.ok) return { status: 'error', error: `${s.file}: ${sv.errors.join('; ')}` };
      dropped.push(...sv.dropped);
      lists.push(sv.items);
      files.push({ path: `v/${dir}/${s.file}`, bytes });
    }

    // 받은 팩이 비어 있으면(0문항 — 검증 문항이 없는 채로 내보낸 팩 등) 지금 쓰는 팩을 그대로 둔다.
    // 그대로 바꾸면 사용자가 풀던 문항이 모두 사라진다(studio 리허설 2026-W42 에서 빈 팩 업로드 경로 확인).
    const remoteCount = lists.reduce((n, l) => n + l.length, 0);
    if (remoteCount === 0 && current && current.items.length > 0)
      return { status: 'error', error: '원격 팩이 비어 있어(0문항) 지금 팩을 유지합니다' };

    for (const f of files) await io.writeFile(f.path, f.bytes);
    await io.writeFile(`v/${dir}/manifest.json`, mb);
    await io.writeFile(POINTER, io.encodeUtf8(JSON.stringify({ version: remote.version, dir })));

    // 옛 버전 폴더 정리(실패해도 무시)
    try {
      for (const d of await io.listDirs('v')) if (d !== dir) await io.removeDir(`v/${d}`);
    } catch {
      /* noop */
    }
    return { status: 'updated', pack: buildPack(remote, lists, 'remote'), dropped };
  } catch (e) {
    return { status: 'error', error: e instanceof Error ? e.message : String(e) };
  }
}
