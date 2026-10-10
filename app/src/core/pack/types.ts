/**
 * 문항 팩 형식.
 *
 * 기준: studio/bank/pack_schema.json (python -m studio bank export 가 만드는 팩, schema_version 1).
 * 앱은 그 형식을 그대로 읽고, 스키마에 아직 없는 선택 필드(decks, extra)는 있으면 쓰고 없으면 기능을 숨긴다.
 *
 * 매니페스트: <SUPABASE_URL>/storage/v1/object/public/media/bank/latest/manifest.json
 * 과목 파일:  bank/<version>/<subjects[].file>  (버전 고정 사본, 없으면 매니페스트와 같은 폴더)
 */

export type ItemType = 'ox' | 'mcq' | 'blank' | 'order' | 'calc';

/** 문제은행 과목 코드(studio/bank/models.py SUBJECTS) */
export type SubjectCode = 'RE' | 'CIV' | 'BRK' | 'PUB' | 'REG' | 'TAX' | string;

/** 함정 유형(pack_schema item.trap enum) */
export const TRAP_TYPES = ['숫자바꿈', '주체바꿈', '예외누락', '기간혼동', '요건혼동', '용어혼동', '기타'] as const;

/** 근거. 조문(law)은 law_name·article·effective_date, 판례(precedent)는 case_no·decided_at 이 필수다. */
export interface Basis {
  /** 'law' | 'precedent' | 'calc' ('case' 는 옛 이름으로 함께 받는다) */
  kind: string;
  law_name?: string;
  law_id?: string;
  /** 예: "제3조 제1항" */
  article?: string;
  /** YYYY-MM-DD. 이 날짜에 시행된 조문 기준이라는 뜻. */
  effective_date?: string;
  quote?: string;
  case_no?: string;
  decided_at?: string;
  note?: string;
  /** 화면 근거 배지(팩이 만들어 줌). 예: [공인중개사법 제33조 제1항 · 2026.1.1 시행 기준] */
  badge?: string;
}

/** 문항별 경고: 시험 전에 시행될 개정이 있음 */
export interface ItemAlert {
  kind: 'upcoming_amendment' | string;
  law_name: string;
  article: string;
  effective_date: string;
}

/**
 * 정답 형식(pack_schema 규칙):
 * - ox:    boolean(true=O)
 * - mcq:   정답 보기 인덱스(0부터)
 * - blank: 빈칸에 들어갈 문자열(앱은 허용 답 배열도 받는다)
 * - order: choices 인덱스(0부터)의 정답 순서
 * - calc:  숫자(앱은 {value, unit?, tolerance?} 도 받는다)
 */
export type RawAnswer =
  | boolean
  | number
  | string
  | (number | string)[]
  | { value: number; unit?: string; tolerance?: number };

export interface Item {
  id: string;
  subject: SubjectCode;
  unit: string;
  type: ItemType;
  stem: string;
  choices?: string[];
  answer: RawAnswer;
  explanation: string;
  /** 함정 유형(TRAP_TYPES) 또는 함정 설명 */
  trap: string;
  basis: Basis[];
  difficulty: number;
  free: boolean;
  version?: number;
  updated_at?: string;
  alerts?: ItemAlert[];
  /** (앱 확장, 선택) 계산 단계 */
  steps?: string[];
  /** (앱 확장, 선택) 계산 단위. 예: "원" */
  unit_label?: string;
}

export interface SubjectFile {
  kind?: 'subject';
  schema_version?: number;
  version?: string;
  subject?: string;
  name?: string;
  count?: number;
  items: Item[];
}

export interface ManifestSubject {
  code: SubjectCode;
  name: string;
  file: string;
  sha256: string;
  count: number;
  bytes?: number;
}

/** 시험 묶음(과목 단위 채점). 모의고사·과락·가채점 화면이 쓴다 */
export interface ExamPaper {
  name: string;
  stage?: number;
  subjects: SubjectCode[];
  questions: number;
}

export interface ManifestExam {
  name: string;
  round: number;
  /** YYYY-MM-DD (KST 날짜) */
  next_exam_date: string;
  papers?: ExamPaper[];
  /** (앱 확장, 선택) 합격자 발표일 */
  result_date?: string;
}

/** decks 값: 문항 id 배열, 또는 제목이 붙은 객체 */
export type DeckEntry = string[] | { title?: string; published_at?: string; item_ids: string[] };

export interface Manifest {
  kind?: 'manifest';
  schema_version?: number;
  version: string;
  content_hash?: string;
  generated_at: string;
  exam: ManifestExam;
  disclaimer?: string;
  /** 무료 하루 문항 수(없으면 20) */
  free_daily?: number;
  total?: number;
  subjects: ManifestSubject[];
  free_ids: string[];
  /** (스키마 확장 요청) 영상 연동 덱: asset_id → 문항 id. 없으면 덱 기능을 숨긴다. */
  decks?: Record<string, DeckEntry>;
  /** (스키마 확장 요청) 이 팩을 읽을 수 있는 최소 앱 버전 */
  min_app_version?: string;
  /** (스키마 확장 요청) 부가 데이터 */
  extra?: ManifestExtra;
}

export interface Amendment {
  id: string;
  /** 무료로 보이는 제목 */
  title: string;
  law_name: string;
  /** 시행일 YYYY-MM-DD */
  effective_date: string;
  published_at?: string;
  /** 이하 상세(유료) */
  summary?: string;
  changes?: { label: string; before: string; after: string }[];
  basis?: Basis[];
  affected_item_ids?: string[];
}

export interface NumberCardRow {
  label: string;
  value: string;
  note?: string;
}

export interface NumberCardSet {
  id: string;
  subject: SubjectCode;
  title: string;
  rows: NumberCardRow[];
  basis: Basis[];
}

export interface GradingSubject {
  /** 시험 묶음 코드 = papers[].subjects 를 '+' 로 이은 값(예: "RE", "REG+TAX") */
  code: string;
  name: string;
  /** 문항 수(보통 40) */
  questions: number;
  /** 문제지 형별(A/B 등) → 정답 번호 배열(1~5). 0 은 "전항 정답", 배열은 복수 정답 */
  answers: Record<string, (number | number[])[]>;
}

export interface GradingSession {
  code: string;
  title: string;
  subjects: GradingSubject[];
}

export interface GradingKey {
  round: number;
  exam_date: string;
  /** provisional = 가답안, final = 확정답안 */
  status: 'provisional' | 'final';
  /** 예: "시행기관 공개 가답안" */
  source_note: string;
  published_at?: string;
  sessions: GradingSession[];
}

export interface MockCompose {
  subject: SubjectCode;
  count: number;
}

export interface MockSetRule {
  code: string;
  title: string;
  /** 1차 | 2차 */
  part: number;
  minutes: number;
  compose: MockCompose[];
}

export interface ExamRules {
  /** 과목 100점 만점 기준 과락선 */
  fail_line: number;
  /** 평균 기준선(참고 표시용) */
  average_line: number;
  mock_sets: MockSetRule[];
}

export interface ManifestExtra {
  amendments?: Amendment[];
  number_cards?: NumberCardSet[];
  grading?: GradingKey;
  exam_rules?: Partial<ExamRules>;
  /** 개정·오류로 내린 문항. 앱에서 출제하지 않는다. */
  disabled_ids?: string[];
  /** 내장 시드 팩 표시. true 면 근거 대조 전 점검용 문항이라는 배너를 띄운다. */
  unverified?: boolean;
  [key: string]: unknown;
}

/** 앱이 메모리에 들고 있는 팩 */
export interface LoadedPack {
  manifest: Manifest;
  items: Item[];
  byId: Map<string, Item>;
  source: 'seed' | 'cache' | 'remote';
}
