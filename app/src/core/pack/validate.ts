/**
 * 팩 검증. 네트워크로 받은 JSON 을 그대로 믿지 않는다.
 * - 매니페스트 구조가 틀리면 팩 전체를 버린다(이전 캐시·시드를 계속 쓴다).
 * - 문항 하나가 틀리면 그 문항만 버린다(화면이 깨지는 것을 막는다).
 * - 근거(조문 또는 판례)가 없는 문항은 버린다(콘텐츠 규칙 1).
 */
import type {
  Basis,
  DeckEntry,
  Item,
  ItemType,
  Manifest,
  ManifestSubject,
  RawAnswer,
} from './types';

export type NormalizedAnswer =
  | { type: 'ox'; value: boolean }
  | { type: 'mcq'; index: number }
  | { type: 'blank'; accepted: string[]; choiceIndex?: number }
  | { type: 'order'; sequence: number[] }
  | { type: 'calc'; choiceIndex?: number; value?: number; tolerance: number; unit?: string };

export interface ValidationResult<T> {
  ok: boolean;
  value?: T;
  errors: string[];
}

const ITEM_TYPES: readonly ItemType[] = ['ox', 'mcq', 'blank', 'order', 'calc'];
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const SHA256_RE = /^[0-9a-f]{64}$/;
const SAFE_FILE_RE = /^[A-Za-z0-9._/-]+$/;

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}
function isStr(v: unknown): v is string {
  return typeof v === 'string' && v.trim().length > 0;
}
function isStrArr(v: unknown): v is string[] {
  return Array.isArray(v) && v.every((x) => typeof x === 'string');
}
function isInt(v: unknown): v is number {
  return typeof v === 'number' && Number.isInteger(v);
}

export function isValidDate(s: unknown): s is string {
  if (typeof s !== 'string' || !DATE_RE.test(s)) return false;
  const [y, m, d] = s.split('-').map(Number) as [number, number, number];
  const dt = new Date(Date.UTC(y, m - 1, d));
  return dt.getUTCFullYear() === y && dt.getUTCMonth() === m - 1 && dt.getUTCDate() === d;
}

/** 서브 경로 탈출(../)과 절대 경로를 막는다. */
export function isSafeRelativePath(p: string): boolean {
  if (!SAFE_FILE_RE.test(p)) return false;
  if (p.startsWith('/')) return false;
  return !p.split('/').some((seg) => seg === '..' || seg === '' || seg === '.');
}

export function validateManifest(raw: unknown): ValidationResult<Manifest> {
  const errors: string[] = [];
  if (!isObj(raw)) return { ok: false, errors: ['manifest: 객체가 아님'] };

  if (!isStr(raw.version)) errors.push('manifest.version 없음');
  if (!isStr(raw.generated_at)) errors.push('manifest.generated_at 없음');

  const exam = raw.exam;
  if (!isObj(exam)) errors.push('manifest.exam 없음');
  else {
    if (!isStr(exam.name)) errors.push('exam.name 없음');
    if (!isInt(exam.round) || exam.round <= 0) errors.push('exam.round 가 양의 정수가 아님');
    if (!isValidDate(exam.next_exam_date)) errors.push('exam.next_exam_date 형식 오류(YYYY-MM-DD)');
    if (exam.result_date !== undefined && !isValidDate(exam.result_date))
      errors.push('exam.result_date 형식 오류');
  }

  const subjects = raw.subjects;
  if (!Array.isArray(subjects) || subjects.length === 0) errors.push('manifest.subjects 비어 있음');
  else {
    const codes = new Set<string>();
    subjects.forEach((s, i) => {
      if (!isObj(s)) {
        errors.push(`subjects[${i}] 객체 아님`);
        return;
      }
      if (!isStr(s.code)) errors.push(`subjects[${i}].code 없음`);
      else if (codes.has(s.code)) errors.push(`subjects[${i}].code 중복: ${s.code}`);
      else codes.add(s.code);
      if (!isStr(s.name)) errors.push(`subjects[${i}].name 없음`);
      if (!isStr(s.file) || !isSafeRelativePath(s.file)) errors.push(`subjects[${i}].file 경로 오류`);
      if (typeof s.sha256 !== 'string' || !SHA256_RE.test(s.sha256.toLowerCase()))
        errors.push(`subjects[${i}].sha256 형식 오류`);
      if (!isInt(s.count) || s.count < 0) errors.push(`subjects[${i}].count 오류`);
    });
  }

  if (!isStrArr(raw.free_ids)) errors.push('manifest.free_ids 가 문자열 배열이 아님');

  if (raw.decks !== undefined) {
    if (!isObj(raw.decks)) errors.push('manifest.decks 객체 아님');
    else {
      for (const [k, v] of Object.entries(raw.decks)) {
        if (!deckItemIds(v as DeckEntry)) errors.push(`decks.${k} 형식 오류`);
      }
    }
  }
  if (raw.min_app_version !== undefined && !isStr(raw.min_app_version))
    errors.push('min_app_version 형식 오류');
  if (raw.extra !== undefined && !isObj(raw.extra)) errors.push('manifest.extra 객체 아님');

  if (errors.length) return { ok: false, errors };
  const m = raw as unknown as Manifest;
  const normalized: Manifest = {
    ...m,
    subjects: m.subjects.map(
      (s): ManifestSubject => ({ ...s, sha256: s.sha256.toLowerCase() }),
    ),
  };
  return { ok: true, value: normalized, errors };
}

/** 덱 값에서 문항 id 배열을 꺼낸다. 형식이 틀리면 null. */
export function deckItemIds(entry: DeckEntry | undefined): string[] | null {
  if (entry === undefined) return null;
  if (isStrArr(entry)) return entry;
  if (isObj(entry) && isStrArr((entry as { item_ids?: unknown }).item_ids))
    return (entry as { item_ids: string[] }).item_ids;
  return null;
}

export function deckTitle(entry: DeckEntry | undefined): string | null {
  if (entry && !Array.isArray(entry) && isStr(entry.title)) return entry.title;
  return null;
}

function validateBasis(b: unknown): b is Basis {
  if (!isObj(b) || !isStr(b.kind)) return false;
  if (b.kind === 'precedent' || b.kind === 'case') return isStr(b.case_no) && isValidDate(b.decided_at);
  if (b.kind === 'calc') return true; // 순수 계산(학개론 수익률 등)
  return isStr(b.law_name) && isStr(b.article) && isValidDate(b.effective_date);
}

/**
 * 정답을 유형별 표준 형태로 바꾼다. 형식이 틀리면 null.
 * 팩(pack_schema)의 인덱스는 0부터, 앱 안의 표준형은 화면 보기 번호와 같은 1부터다.
 */
export function normalizeAnswer(item: {
  type: ItemType;
  answer: RawAnswer;
  choices?: string[];
}): NormalizedAnswer | null {
  const { type, answer, choices } = item;
  const nChoices = Array.isArray(choices) ? choices.length : 0;
  const isIndex = (n: unknown): n is number => isInt(n) && n >= 0 && n < nChoices;

  switch (type) {
    case 'ox': {
      if (typeof answer === 'boolean') return { type, value: answer };
      if (typeof answer === 'string') {
        const s = answer.trim().toUpperCase();
        if (s === 'O' || s === 'TRUE') return { type, value: true };
        if (s === 'X' || s === 'FALSE') return { type, value: false };
      }
      return null;
    }
    case 'mcq': {
      if (nChoices < 2) return null;
      return isIndex(answer) ? { type, index: answer + 1 } : null;
    }
    case 'blank': {
      const accepted = (Array.isArray(answer) ? answer : [answer]).filter(
        (a): a is string => typeof a === 'string' && a.trim().length > 0,
      );
      if (!accepted.length) {
        if (typeof answer === 'number' && Number.isFinite(answer)) accepted.push(String(answer));
        else return null;
      }
      if (nChoices > 0) {
        const idx = choices!.findIndex((c) => accepted.includes(c));
        if (idx < 0) return null;
        return { type, accepted, choiceIndex: idx + 1 };
      }
      return { type, accepted };
    }
    case 'order': {
      if (nChoices < 2 || !Array.isArray(answer) || answer.length !== nChoices) return null;
      let seq: number[];
      if (answer.every((a) => typeof a === 'number')) {
        if (!(answer as number[]).every(isIndex)) return null;
        seq = (answer as number[]).map((a) => a + 1);
      } else if (answer.every((a) => typeof a === 'string')) seq = (answer as string[]).map((a) => choices!.indexOf(a) + 1);
      else return null;
      if (!seq.every((v) => v >= 1 && v <= nChoices) || new Set(seq).size !== nChoices) return null;
      return { type, sequence: seq };
    }
    case 'calc': {
      if (nChoices > 0) return isIndex(answer) ? { type, choiceIndex: answer + 1, tolerance: 0 } : null;
      if (typeof answer === 'number' && Number.isFinite(answer)) return { type, value: answer, tolerance: 0 };
      if (isObj(answer) && typeof answer.value === 'number' && Number.isFinite(answer.value)) {
        const tol = typeof answer.tolerance === 'number' && answer.tolerance >= 0 ? answer.tolerance : 0;
        const unit = typeof answer.unit === 'string' ? answer.unit : undefined;
        return { type, value: answer.value, tolerance: tol, unit };
      }
      return null;
    }
    default:
      return null;
  }
}

export interface ItemCheck {
  ok: boolean;
  reason?: string;
}

export function checkItem(raw: unknown, subjectCode?: string): ItemCheck {
  if (!isObj(raw)) return { ok: false, reason: '객체 아님' };
  const id = isStr(raw.id) ? raw.id : '?';
  const fail = (reason: string): ItemCheck => ({ ok: false, reason: `${id}: ${reason}` });
  if (!isStr(raw.id)) return fail('id 없음');
  if (!isStr(raw.subject)) return fail('subject 없음');
  if (subjectCode && raw.subject !== subjectCode) return fail(`subject 불일치(${String(raw.subject)})`);
  if (typeof raw.unit !== 'string') return fail('unit 없음');
  if (!ITEM_TYPES.includes(raw.type as ItemType)) return fail(`type 오류(${String(raw.type)})`);
  if (!isStr(raw.stem)) return fail('stem 없음');
  if (raw.choices !== undefined) {
    if (!isStrArr(raw.choices) || raw.choices.some((c) => !c.trim())) return fail('choices 형식 오류');
    if (raw.choices.length > 8) return fail('choices 가 8개 초과');
  }
  if (typeof raw.explanation !== 'string') return fail('explanation 없음');
  if (typeof raw.trap !== 'string') return fail('trap 없음');
  if (!Array.isArray(raw.basis) || raw.basis.length === 0) return fail('basis 없음');
  if (!raw.basis.every(validateBasis)) return fail('basis 필수 필드 누락');
  if (typeof raw.difficulty !== 'number') return fail('difficulty 없음');
  if (typeof raw.free !== 'boolean') return fail('free 없음');
  const norm = normalizeAnswer(raw as unknown as Item);
  if (!norm) return fail('answer 형식이 유형과 맞지 않음');
  return { ok: true };
}

export interface SubjectValidation {
  ok: boolean;
  items: Item[];
  dropped: string[];
  errors: string[];
}

export function validateSubjectFile(raw: unknown, subjectCode?: string): SubjectValidation {
  if (!isObj(raw) || !Array.isArray(raw.items))
    return { ok: false, items: [], dropped: [], errors: ['과목 파일: items 배열 없음'] };
  const items: Item[] = [];
  const dropped: string[] = [];
  const seen = new Set<string>();
  for (const it of raw.items) {
    const c = checkItem(it, subjectCode);
    if (!c.ok) {
      dropped.push(c.reason ?? '?');
      continue;
    }
    const item = it as Item;
    if (seen.has(item.id)) {
      dropped.push(`${item.id}: id 중복`);
      continue;
    }
    seen.add(item.id);
    items.push(item);
  }
  return { ok: true, items, dropped, errors: [] };
}

/** "1.2.10" 형식 비교. a<b 면 음수. */
export function compareVersions(a: string, b: string): number {
  const pa = a.split(/[.-]/).map((x) => parseInt(x, 10) || 0);
  const pb = b.split(/[.-]/).map((x) => parseInt(x, 10) || 0);
  const n = Math.max(pa.length, pb.length);
  for (let i = 0; i < n; i++) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0);
    if (d !== 0) return d;
  }
  return 0;
}
