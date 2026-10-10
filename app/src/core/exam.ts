/**
 * 시험 일정·과목·규칙. 매니페스트 exam(papers 포함) / extra.exam_rules 가 있으면 그 값을 쓴다.
 *
 * [미확인] 과목당 40문항, 2차 3교시 공시법·세법 한 과목(문항 배분 24·16), 과목당 50분, 과락 40점·평균 60점은
 * 일반 지식이다. 큐넷 시행계획 공고로 확인한 뒤 channel.yaml exam.papers / 매니페스트 extra.exam_rules 로 고친다.
 */
import type { ExamPaper, ExamRules, Manifest, ManifestExam, MockSetRule } from './pack/types';
import { daysBetween, kstDateKey } from './time';

export const DEFAULT_EXAM: ManifestExam = {
  name: '공인중개사',
  round: 37,
  next_exam_date: '2026-10-31',
  result_date: '2026-12-02',
};

/** 문제은행 과목 코드 기본 이름(매니페스트 subjects[].name 이 우선) */
export const SUBJECT_NAMES: Record<string, string> = {
  RE: '부동산학개론',
  CIV: '민법 및 민사특별법',
  BRK: '공인중개사법령 및 중개실무',
  PUB: '부동산공법',
  REG: '부동산공시법령',
  TAX: '부동산세법',
};

/** studio/bank/exam.py DEFAULT_PAPERS 와 같다 */
export const DEFAULT_PAPERS: ExamPaper[] = [
  { name: '부동산학개론', stage: 1, subjects: ['RE'], questions: 40 },
  { name: '민법 및 민사특별법', stage: 1, subjects: ['CIV'], questions: 40 },
  { name: '공인중개사법령 및 중개실무', stage: 2, subjects: ['BRK'], questions: 40 },
  { name: '부동산공법', stage: 2, subjects: ['PUB'], questions: 40 },
  { name: '부동산공시법령 및 부동산세법', stage: 2, subjects: ['REG', 'TAX'], questions: 40 },
];

/** 여러 과목이 한 시험 과목일 때 문항 배분([미확인] 일반 지식). 없으면 고르게 나눈다 */
const KNOWN_SPLITS: Record<string, Record<string, number>> = {
  'REG+TAX': { REG: 24, TAX: 16 },
};

export const MINUTES_PER_QUESTION = 1.25; // 40문항 50분

export function paperCode(p: ExamPaper): string {
  return p.subjects.join('+');
}

export function mockSetsFromPapers(papers: ExamPaper[]): MockSetRule[] {
  return papers.map((p, i) => {
    const code = paperCode(p);
    const known = KNOWN_SPLITS[code];
    const knownTotal = known ? Object.values(known).reduce((a, b) => a + b, 0) : 0;
    let compose: MockSetRule['compose'];
    if (known && knownTotal === p.questions) compose = p.subjects.map((s) => ({ subject: s, count: known[s] ?? 0 }));
    else {
      const base = Math.floor(p.questions / p.subjects.length);
      compose = p.subjects.map((s, j) => ({
        subject: s,
        count: base + (j < p.questions - base * p.subjects.length ? 1 : 0),
      }));
    }
    const stage = p.stage ?? (i < 2 ? 1 : 2);
    return {
      code,
      title: `${stage}차 ${p.name}`,
      part: stage,
      minutes: Math.round(p.questions * MINUTES_PER_QUESTION),
      compose,
    };
  });
}

export const DEFAULT_RULES: ExamRules = {
  fail_line: 40,
  average_line: 60,
  mock_sets: mockSetsFromPapers(DEFAULT_PAPERS),
};

export function examRules(manifest: Manifest | null | undefined): ExamRules {
  const r = manifest?.extra?.exam_rules ?? {};
  const papers = manifest?.exam?.papers;
  const fromPapers = Array.isArray(papers) && papers.length ? mockSetsFromPapers(papers) : null;
  return {
    fail_line: typeof r.fail_line === 'number' ? r.fail_line : DEFAULT_RULES.fail_line,
    average_line: typeof r.average_line === 'number' ? r.average_line : DEFAULT_RULES.average_line,
    mock_sets:
      Array.isArray(r.mock_sets) && r.mock_sets.length
        ? (r.mock_sets as MockSetRule[])
        : (fromPapers ?? DEFAULT_RULES.mock_sets),
  };
}

export function examPapers(manifest: Manifest | null | undefined): ExamPaper[] {
  const p = manifest?.exam?.papers;
  return Array.isArray(p) && p.length ? p : DEFAULT_PAPERS;
}

export function examInfo(manifest: Manifest | null | undefined): ManifestExam {
  return manifest?.exam ?? DEFAULT_EXAM;
}

export function freeDailyLimit(manifest: Manifest | null | undefined, fallback = 20): number {
  const n = manifest?.free_daily;
  return typeof n === 'number' && Number.isInteger(n) && n >= 0 ? n : fallback;
}

export function subjectName(manifest: Manifest | null | undefined, code: string): string {
  return manifest?.subjects.find((s) => s.code === code)?.name ?? SUBJECT_NAMES[code] ?? code;
}

/** 시험일까지 남은 일수(오늘이 시험일이면 0, 지났으면 음수). 날짜가 없으면 null */
export function daysToExam(examDate: string | null | undefined, nowMs: number): number | null {
  if (!examDate) return null;
  return daysBetween(kstDateKey(nowMs), examDate);
}

export function ddayLabel(examDate: string | null | undefined, nowMs: number): string {
  const d = daysToExam(examDate, nowMs);
  if (d === null) return '일정 공고 전';
  if (d > 0) return `D-${d}`;
  if (d === 0) return 'D-DAY';
  return '시험 종료';
}

/**
 * 사용자가 고른 목표 회차의 시험일. 매니페스트 회차와 같으면 그 날짜, 다음 회차면 아직 모른다(null).
 */
export function targetExamDate(manifest: Manifest | null | undefined, targetRound: number | null): string | null {
  const exam = examInfo(manifest);
  if (targetRound === null || targetRound === exam.round) return exam.next_exam_date;
  return null;
}

/** 막판 60일 패스 판매 기간: 시험이 있는 해 8월 1일 ~ 시험일(KST) */
export function isPassOnSale(nowMs: number, examDate: string | null | undefined): boolean {
  if (!examDate) return false;
  const today = kstDateKey(nowMs);
  const year = examDate.slice(0, 4);
  return today >= `${year}-08-01` && today <= examDate;
}
