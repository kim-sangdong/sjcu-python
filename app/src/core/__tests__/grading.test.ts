import { answerLabel, formatNumber, isCorrect, normalizeText, parseNumber } from '../grading';
import type { Item } from '../pack/types';

const base = {
  subject: 's',
  unit: 'u',
  explanation: '',
  trap: '',
  basis: [{ kind: 'calc' }],
  difficulty: 1,
  free: true,
};
const it = (x: Partial<Item>) => ({ id: 'i', stem: 'q', ...base, ...x }) as Item;

describe('채점', () => {
  test('OX', () => {
    const q = it({ type: 'ox', answer: false });
    expect(isCorrect(q, { type: 'ox', value: false })).toBe(true);
    expect(isCorrect(q, { type: 'ox', value: true })).toBe(false);
    expect(isCorrect(q, { type: 'mcq', index: 1 })).toBe(false);
  });

  test('5지선다(보기 번호 1부터)', () => {
    const q = it({ type: 'mcq', choices: ['a', 'b', 'c', 'd', 'e'], answer: 4 }); // 팩 인덱스 0부터
    expect(isCorrect(q, { type: 'mcq', index: 5 })).toBe(true);
    expect(isCorrect(q, { type: 'mcq', index: 4 })).toBe(false);
    expect(answerLabel(q)).toBe('5번 e');
  });

  test('빈칸: 공백·전각·쉼표·마침표 차이는 같은 답', () => {
    const q = it({ type: 'blank', answer: ['1,500퍼센트', '1500%'] });
    expect(isCorrect(q, { type: 'blank', text: ' 1500 퍼센트. ' })).toBe(true);
    expect(isCorrect(q, { type: 'blank', text: '１５００％' })).toBe(true);
    expect(isCorrect(q, { type: 'blank', text: '150퍼센트' })).toBe(false);
    expect(isCorrect(q, { type: 'blank', text: '' })).toBe(false);
  });

  test('빈칸(보기형)', () => {
    const q = it({ type: 'blank', choices: ['30일', '60일'], answer: '60일' });
    expect(isCorrect(q, { type: 'blank', choiceIndex: 2 })).toBe(true);
    expect(isCorrect(q, { type: 'blank', text: '60일' })).toBe(false);
  });

  test('순서', () => {
    const q = it({ type: 'order', choices: ['a', 'b', 'c'], answer: [2, 1, 0] });
    expect(isCorrect(q, { type: 'order', sequence: [3, 2, 1] })).toBe(true);
    expect(isCorrect(q, { type: 'order', sequence: [3, 1, 2] })).toBe(false);
    expect(answerLabel(q)).toBe('c → b → a');
  });

  test('계산: 허용 오차, "원"·쉼표 섞인 입력', () => {
    const q = it({ type: 'calc', answer: { value: 1.25, tolerance: 0.001 } });
    expect(isCorrect(q, { type: 'calc', value: '1.25' })).toBe(true);
    expect(isCorrect(q, { type: 'calc', value: '1.2505' })).toBe(true);
    expect(isCorrect(q, { type: 'calc', value: '0.8' })).toBe(false);
    const won = it({ type: 'calc', answer: 3600000, unit_label: '원' });
    expect(isCorrect(won, { type: 'calc', value: '3,600,000원' })).toBe(true);
    expect(isCorrect(won, { type: 'calc', value: 'abc' })).toBe(false);
    expect(answerLabel(won)).toBe('3,600,000원');
  });

  test('계산: 소수 정답은 둘째 자리 이상 반올림 입력을 인정, 정수(금액)는 정확히', () => {
    const third = it({ type: 'calc', answer: 1 / 3 });
    expect(isCorrect(third, { type: 'calc', value: '0.33' })).toBe(true);
    expect(isCorrect(third, { type: 'calc', value: '0.333' })).toBe(true);
    expect(isCorrect(third, { type: 'calc', value: '0.3' })).toBe(false);
    expect(isCorrect(third, { type: 'calc', value: '0.34' })).toBe(false);
    const half = it({ type: 'calc', answer: 12.5 });
    expect(isCorrect(half, { type: 'calc', value: '12.5' })).toBe(true);
    expect(isCorrect(half, { type: 'calc', value: '12.50' })).toBe(true);
    expect(isCorrect(half, { type: 'calc', value: '13' })).toBe(false);
    const won = it({ type: 'calc', answer: 3600000 });
    expect(isCorrect(won, { type: 'calc', value: '3,600,000' })).toBe(true);
    expect(isCorrect(won, { type: 'calc', value: '3,600,001' })).toBe(false);
  });

  test('계산(보기형)', () => {
    const q = it({ type: 'calc', choices: ['1.25', '0.8'], answer: 0 });
    expect(isCorrect(q, { type: 'calc', choiceIndex: 1 })).toBe(true);
  });

  test('보조 함수', () => {
    expect(normalizeText('  제 3 조. ')).toBe('제3조');
    expect(parseNumber('-12.5%')).toBe(-12.5);
    expect(parseNumber(undefined)).toBeNull();
    expect(formatNumber(1234567.5)).toBe('1,234,567.5');
  });
});
