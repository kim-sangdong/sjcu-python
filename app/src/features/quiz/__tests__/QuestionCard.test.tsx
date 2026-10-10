/**
 * 문항 카드 렌더·조작 스모크 테스트: 5개 유형이 그려지고, 누르면 올바른 응답이 나가며,
 * 제출 뒤 정답·해설·근거 배지가 보이는지 확인한다(react-test-renderer, 네이티브 모듈은 목).
 */
import { Text, TextInput } from 'react-native';
import renderer, { act, type ReactTestInstance } from 'react-test-renderer';

import type { Response } from '@/core/grading';
import { isCorrect } from '@/core/grading';
import type { Item } from '@/core/pack/types';

jest.mock('@/data/telemetry', () => ({ track: jest.fn(async () => undefined) }));
jest.mock('react-native-safe-area-context', () => {
  const { View } = jest.requireActual('react-native');
  return { SafeAreaView: View, SafeAreaProvider: View };
});

let mockUnverified = false;
jest.mock('@/state/AppProvider', () => ({
  useApp: () => ({
    colors: jest.requireActual('@/ui/theme').palette('light', false),
    fontScale: 1,
    settings: { highContrast: false },
    pack: {
      manifest: {
        subjects: [{ code: 'CIV', name: '민법 및 민사특별법' }],
        extra: mockUnverified ? { unverified: true } : {},
      },
    },
  }),
}));

// eslint-disable-next-line import/first
import { QuestionCard } from '../QuestionCard';

const basis = [
  {
    kind: 'law',
    law_name: '주택임대차보호법',
    article: '제3조 제1항',
    effective_date: '2023-07-19',
    quote: '…그 다음 날부터 제삼자에 대하여 효력이 생긴다.',
    badge: '[주택임대차보호법 제3조 제1항 · 2023.7.19 시행 기준]',
  },
];
const base = { subject: 'CIV', unit: '임대차', explanation: '해설입니다', trap: '기간혼동', basis, difficulty: 1, free: true };
const mk = (x: Partial<Item>) => ({ id: 'Q-CIV-00000000', stem: '지문', ...base, ...x }) as Item;

function texts(root: ReactTestInstance): string {
  return root
    .findAllByType(Text)
    .map((t) => {
      const c = t.props.children;
      return Array.isArray(c) ? c.filter((x) => typeof x === 'string' || typeof x === 'number').join('') : typeof c === 'string' ? c : '';
    })
    .join('|');
}

function pressByLabel(root: ReactTestInstance, re: RegExp) {
  const target = root.findAll(
    (n) => typeof n.props.onPress === 'function' && re.test(String(n.props.accessibilityLabel ?? '')),
  )[0];
  if (!target) throw new Error('버튼 없음: ' + re);
  act(() => target.props.onPress());
}

function renderCard(item: Item, result: { correct: boolean } | null, response: Response | null, onSubmit = jest.fn()) {
  let tree!: renderer.ReactTestRenderer;
  act(() => {
    tree = renderer.create(<QuestionCard item={item} response={response} result={result} onSubmit={onSubmit} />);
  });
  return { tree, onSubmit };
}

describe('QuestionCard', () => {
  test('OX: 근거 배지(팩 badge)와 지문이 보이고, O 를 누르면 ox 응답', () => {
    const item = mk({ type: 'ox', answer: false });
    const { tree, onSubmit } = renderCard(item, null, null);
    const all = texts(tree.root);
    expect(all).toContain('[주택임대차보호법 제3조 제1항 · 2023.7.19 시행 기준]');
    expect(all).toContain('지문');
    expect(all).not.toContain('정답입니다');
    pressByLabel(tree.root, /^O, 맞다/);
    expect(onSubmit).toHaveBeenCalledWith({ type: 'ox', value: true });
  });

  test('제출 뒤: 오답 표시·정답·해설·함정 유형', () => {
    const item = mk({ type: 'ox', answer: false });
    const { tree } = renderCard(item, { correct: false }, { type: 'ox', value: true });
    const all = texts(tree.root);
    expect(all).toContain('오답입니다');
    expect(all).toContain('해설입니다');
    expect(all).toContain('함정 유형 · 기간혼동');
  });

  test('5지선다: 보기 번호 1부터, 팩 인덱스 0부터와 일치', () => {
    const item = mk({ type: 'mcq', choices: ['1년', '2년', '3년', '4년', '5년'], answer: 1 });
    const { tree, onSubmit } = renderCard(item, null, null);
    pressByLabel(tree.root, /^2번 2년/);
    const r = onSubmit.mock.calls[0][0] as Response;
    expect(r).toEqual({ type: 'mcq', index: 2 });
    expect(isCorrect(item, r)).toBe(true);
  });

  test('빈칸: 입력 후 제출', () => {
    const item = mk({ type: 'blank', answer: '2년' });
    const { tree, onSubmit } = renderCard(item, null, null);
    const input = tree.root.findByType(TextInput);
    act(() => input.props.onChangeText('2 년'));
    pressByLabel(tree.root, /^제출$/);
    const r = onSubmit.mock.calls[0][0] as Response;
    expect(isCorrect(item, r)).toBe(true);
  });

  test('순서: 처음 배치는 정답이 아니고, 아래로 옮기면 순서가 바뀐다', () => {
    const item = mk({ type: 'order', choices: ['A', 'B', 'C'], answer: [2, 1, 0] });
    const first = renderCard(item, null, null);
    pressByLabel(first.tree.root, /이 순서로 제출/);
    const seq1 = (first.onSubmit.mock.calls[0][0] as Extract<Response, { type: 'order' }>).sequence;
    expect([...seq1].sort()).toEqual([1, 2, 3]);
    expect(isCorrect(item, { type: 'order', sequence: seq1 })).toBe(false);

    const second = renderCard(item, null, null);
    const firstLabel = ['A', 'B', 'C'][seq1[0]! - 1];
    pressByLabel(second.tree.root, new RegExp(`^${firstLabel} 아래로$`));
    pressByLabel(second.tree.root, /이 순서로 제출/);
    const seq2 = (second.onSubmit.mock.calls[0][0] as Extract<Response, { type: 'order' }>).sequence;
    expect(seq2).toEqual([seq1[1], seq1[0], seq1[2]]);
  });

  test('계산: 숫자 입력', () => {
    const item = mk({ type: 'calc', answer: 1.25, basis: [{ kind: 'calc', note: '투자분석 비율', badge: '[계산 · 투자분석 비율]' }] });
    const { tree, onSubmit } = renderCard(item, null, null);
    act(() => tree.root.findByType(TextInput).props.onChangeText('1.25'));
    pressByLabel(tree.root, /^제출$/);
    expect(isCorrect(item, onSubmit.mock.calls[0][0])).toBe(true);
    expect(texts(tree.root)).toContain('[계산 · 투자분석 비율]');
  });

  test('시드(근거 대조 전): 배지에 날짜 대신 "시행일 대조 전"과 점검용 안내', () => {
    mockUnverified = true;
    try {
      const { tree } = renderCard(mk({ type: 'ox', answer: true }), null, null);
      const all = texts(tree.root);
      expect(all).toContain('시행일 대조 전');
      expect(all).not.toContain('2023.7.19 시행 기준');
      expect(all).toContain('기본 내장 문항(점검용)');
    } finally {
      mockUnverified = false;
    }
  });

  test('시험 전 개정 예정 경고', () => {
    const item = mk({
      type: 'ox',
      answer: true,
      alerts: [{ kind: 'upcoming_amendment', law_name: '공인중개사법', article: '제33조', effective_date: '2026-10-20' }],
    });
    const { tree } = renderCard(item, null, null);
    expect(texts(tree.root)).toContain('시험 전 개정 예정: 공인중개사법 제33조 (2026.10.20 시행)');
  });
});
