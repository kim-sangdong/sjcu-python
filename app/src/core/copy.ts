/**
 * 화면 고정 문구와 금지 표현.
 * 금지 표현 근거: docs/research/round2/judge.md (4) "반드시 지킬 콘텐츠 규칙" — 표시광고·사칭·저작권·투자·법률상담.
 * src 아래 모든 화면 문구는 __tests__/copy.test.ts 가 이 목록으로 검사한다.
 */

export const DISCLAIMER_UNOFFICIAL =
  '함정노트는 한국산업인력공단·큐넷과 무관한 비공식 학습 앱입니다. 문항은 법령 조문을 근거로 자체 제작했으며, 시험 문제 원문을 싣지 않습니다.';

export const DISCLAIMER_LAW =
  '법령은 표시된 시행일 기준입니다. 개별 사안은 대한법률구조공단(132)이나 변호사와 상담하세요.';

export const MOCK_NOTICE =
  '자체 제작 문항으로 계산한 참고용 점수입니다. 실제 시험의 난이도·배점과 다르며 시험 결과를 뜻하지 않습니다.';

export const GRADING_NOTICE =
  '가채점은 공개된 정답 번호와 내가 적은 번호만 비교합니다. 최종 결과는 시행기관의 발표로만 확인하세요.';

export const SEED_NOTICE =
  '기본 내장 문항(점검용)입니다. 인터넷에 연결하면 근거 대조를 마친 최신 문항으로 바뀝니다.';

/**
 * 금지 표현(정규식). '공식'은 '비공식'을 뺀 뒤 검사한다.
 */
export const BANNED_PATTERNS: { re: RegExp; why: string }[] = [
  { re: /합격\s*보장/, why: '표시광고' },
  { re: /100\s*%\s*합격/, why: '표시광고' },
  { re: /무조건\s*합격/, why: '표시광고' },
  { re: /합격률/, why: '표시광고(근거·기간 없는 합격률)' },
  { re: /합격자\s*수\s*1위|업계\s*1위/, why: '표시광고' },
  { re: /적중/, why: '표시광고' },
  { re: /이것만\s*보면/, why: '표시광고' },
  { re: /\d+\s*일\s*만에\s*합격/, why: '표시광고' },
  { re: /점수\s*\S*\s*상승\s*보장/, why: '표시광고' },
  { re: /합격\s*(예측|가능성|확률)/, why: '합격 예측 표현' },
  { re: /국가공인/, why: '사칭·오인' },
  { re: /큐넷\s*제공/, why: '사칭·오인' },
  { re: /공단\s*(인증|제휴)/, why: '사칭·오인' },
  { re: /국토교통부\s*지정/, why: '사칭·오인' },
  { re: /정답\s*확정/, why: '가답안 단계 단정' },
  { re: /족보|복원\s*문제/, why: '저작권' },
  { re: /수익\s*보장|시세\s*예측|갭투자/, why: '투자 권유' },
  { re: /무조건\s*떨어/, why: '불안 조장' },
  { re: /돌려받는\s*법/, why: '법률상담' },
];

export function findBanned(text: string): string[] {
  const t = text.replace(/비공식/g, '');
  const hits = BANNED_PATTERNS.filter((p) => p.re.test(t)).map((p) => `${p.re.source} (${p.why})`);
  if (/공식/.test(t)) hits.push('공식 (사칭·오인 — "비공식" 외 사용 금지)');
  return hits;
}
