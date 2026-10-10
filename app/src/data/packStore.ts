/**
 * 문항 팩의 실제 입출력(expo-file-system 새 API, expo-crypto, fetch).
 * 로직은 core/pack/sync.ts 에 있고 여기서는 PackIO 만 구현한다.
 */
import * as Crypto from 'expo-crypto';
import { Directory, File, Paths } from 'expo-file-system';

import { checkForUpdate, loadFromCache, loadSeed, type PackIO, type UpdateResult } from '@/core/pack/sync';
import type { LoadedPack } from '@/core/pack/types';
import { SEED } from './seed';

const FETCH_TIMEOUT_MS = 20_000;

function root(): Directory {
  return new Directory(Paths.document, 'bank');
}

function parts(path: string): string[] {
  return path.split('/').filter(Boolean);
}

function toHex(buf: ArrayBuffer): string {
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}

export const fileIO: PackIO = {
  async fetchBytes(url) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
    try {
      const res = await fetch(url, { signal: ctrl.signal, headers: { 'Cache-Control': 'no-cache' } });
      if (!res.ok) throw new Error(`HTTP ${res.status} ${url}`);
      return new Uint8Array(await res.arrayBuffer());
    } finally {
      clearTimeout(timer);
    }
  },
  async sha256Hex(bytes) {
    return toHex(await Crypto.digest(Crypto.CryptoDigestAlgorithm.SHA256, new Uint8Array(bytes)));
  },
  decodeUtf8(bytes) {
    return new TextDecoder('utf-8').decode(bytes);
  },
  encodeUtf8(text) {
    return new TextEncoder().encode(text);
  },
  async readFile(path) {
    const f = new File(root(), ...parts(path));
    if (!f.exists) return null;
    return await f.bytes();
  },
  async writeFile(path, bytes) {
    const p = parts(path);
    const name = p.pop()!;
    const dir = new Directory(root(), ...p);
    dir.create({ intermediates: true, idempotent: true });
    const f = new File(dir, name);
    if (f.exists) f.delete();
    f.create();
    f.write(bytes);
  },
  async removeDir(path) {
    const d = new Directory(root(), ...parts(path));
    if (d.exists) d.delete();
  },
  async listDirs(path) {
    const d = new Directory(root(), ...parts(path));
    if (!d.exists) return [];
    return d
      .list()
      .filter((x): x is Directory => x instanceof Directory)
      .map((x) => x.name);
  },
};

/** 캐시가 있으면 캐시, 없거나 손상되면 내장 시드 */
export async function loadInitialPack(): Promise<LoadedPack> {
  const cached = await loadFromCache(fileIO);
  if (cached) return cached;
  return loadSeed(SEED);
}

export async function updatePack(opts: {
  manifestUrl: string;
  appVersion: string;
  current: LoadedPack | null;
}): Promise<UpdateResult> {
  return checkForUpdate(fileIO, { ...opts, bust: String(Math.floor(Date.now() / 60_000)) });
}

/** 데이터 초기화 때 캐시도 지운다(다음 실행에 다시 받음) */
export function clearPackCache(): void {
  const d = root();
  if (d.exists) d.delete();
}
