/**
 * 문항 채점. 응답 형태는 유형별로 다르다.
 */
import type { Item } from './pack/types';
import { normalizeAnswer, type NormalizedAnswer } from './pack/validate';

export type Response =
  | { type: 'ox'; value: boolean }
  | { type: 'mcq'; index: number }
  | { type: 'blank'; text?: string; choiceIndex?: number }
  | { type: 'order'; sequence: number[] }
  | { type: 'calc'; value?: number | string; choiceIndex?: number };

const FULLWIDTH_RE = /[！-～]/g;

/** 빈칸 답 비교용 정규화: 전각→반각, 공백·쉼표·마침표 끝 제거, 소문자 */
export function normalizeText(s: string): string {
  let t = s;
  try {
    t = t.normalize('NFKC');
  } catch {
    t = t.replace(FULLWIDTH_RE, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0));
  }
  return t
    .replace(/\s+/g, '')
    .replace(/(\d),(?=\d{3}(\D|$))/g, '$1')
    .replace(/[.。]$/, '')
    .toLowerCase();
}

/** "3,600,000원" → 3600000, "12.5%" → 12.5. 숫자가 없으면 null */
export function parseNumber(v: number | string | undefined): number | null {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null;
  if (typeof v !== 'string') return null;
  let t = v;
  try {
    t = t.normalize('NFKC');
  } catch {
    /* noop */
  }
  const m = t.replace(/[,\s]/g, '').match(/-?\d+(\.\d+)?/);
  if (!m) return null;
  const n = Number(m[0]);
  return Number.isFinite(n) ? n : null;
}

export function isCorrect(item: Item, response: Response): boolean {
  const ans = normalizeAnswer(item);
  if (!ans || ans.type !== response.type) return false;
  return matches(ans, response);
}

function matches(ans: NormalizedAnswer, r: Response): boolean {
  switch (ans.type) {
    case 'ox':
      return r.type === 'ox' && r.value === ans.value;
    case 'mcq':
      return r.type === 'mcq' && r.index === ans.index;
    case 'blank': {
      if (r.type !== 'blank') return false;
      if (ans.choiceIndex !== undefined) return r.choiceIndex === ans.choiceIndex;
      if (typeof r.text !== 'string' || !r.text.trim()) return false;
      const got = normalizeText(r.text);
      return ans.accepted.some((a) => normalizeText(a) === got);
    }
    case 'order':
      return (
        r.type === 'order' &&
        r.sequence.length === ans.sequence.length &&
        r.sequence.every((v, i) => v === ans.sequence[i])
      );
    case 'calc': {
      if (r.type !== 'calc') return false;
      if (ans.choiceIndex !== undefined) return r.choiceIndex === ans.choiceIndex;
      const got = parseNumber(r.value);
      if (got === null || ans.value === undefined) return false;
      if (Math.abs(got - ans.value) <= ans.tolerance + 1e-9) return true;
      return roundingMatch(ans.value, r.value);
    }
    default:
      return false;
  }
}

function decimalsOf(n: number): number {
  const s = String(n);
  if (/e/i.test(s)) return 20;
  const i = s.indexOf('.');
  return i < 0 ? 0 : s.length - i - 1;
}

/**
 * 소수 정답의 반올림 입력 허용: 정답이 0.3333… 이면 "0.33"(소수 둘째 자리 이상) 을 맞게 본다.
 * 정수 정답(금액 등)은 정확히 같아야 한다. 입력이 정답보다 거칠면(0.3) 틀린다.
 */
export function roundingMatch(value: number, input: number | string | undefined): boolean {
  const got = parseNumber(input);
  if (got === null || Number.isInteger(value)) return false;
  const typed = typeof input === 'string' ? (/[.,](\d+)/.exec(input.replace(/,(?=\d{3}(\D|$))/g, ''))?.[1]?.length ?? 0) : decimalsOf(got);
  const need = Math.min(2, decimalsOf(value));
  if (typed < need) return false;
  const f = 10 ** typed;
  return Math.round(value * f) === Math.round(got * f);
}

/** 해설 화면에 보여줄 정답 문자열 */
export function answerLabel(item: Item): string {
  const ans = normalizeAnswer(item);
  if (!ans) return '';
  const c = item.choices ?? [];
  switch (ans.type) {
    case 'ox':
      return ans.value ? 'O' : 'X';
    case 'mcq':
      return `${ans.index}번 ${c[ans.index - 1] ?? ''}`.trim();
    case 'blank':
      return ans.accepted[0] ?? '';
    case 'order':
      return ans.sequence.map((i) => c[i - 1] ?? String(i)).join(' → ');
    case 'calc':
      if (ans.choiceIndex !== undefined) return `${ans.choiceIndex}번 ${c[ans.choiceIndex - 1] ?? ''}`.trim();
      return `${formatNumber(ans.value ?? 0)}${ans.unit ?? item.unit_label ?? ''}`;
    default:
      return '';
  }
}

export function formatNumber(n: number): string {
  const [i, f] = String(n).split('.');
  const withComma = (i ?? '').replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return f ? `${withComma}.${f}` : withComma;
}
