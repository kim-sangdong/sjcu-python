/**
 * 결제 전 고지문(자동 갱신 가격·주기·해지 방법). 스토어 규정과 국내 전자상거래 관행에 맞춰
 * 상품마다 같은 화면에 가격 바로 옆에 보여 준다. 가격은 스토어가 준 문자열(priceString)을 그대로 쓴다.
 */

export type PlanKind = 'monthly' | 'annual' | 'pass';

export interface PlanInfo {
  kind: PlanKind;
  /** 스토어 표시 가격 예: "₩9,900" */
  priceString: string;
  /** ISO 8601 기간 예: P1M, P1Y. 비갱신 패스는 null 가능 */
  period: string | null;
  /** 무료 체험 일수(없으면 null) */
  trialDays: number | null;
}

export function periodLabel(iso: string | null | undefined): string {
  if (!iso) return '';
  const m = /^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?$/.exec(iso);
  if (!m) return iso;
  const [, y, mo, w, d] = m;
  if (y) return `${y}년`;
  if (mo) return `${mo}개월`;
  if (w) return `${Number(w) * 7}일`;
  if (d) return `${d}일`;
  return iso;
}

/** ISO 기간 → 일수(체험 기간 표시용, 대략값) */
export function periodDays(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const m = /^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?$/.exec(iso);
  if (!m) return null;
  const [, y, mo, w, d] = m;
  return Number(y ?? 0) * 365 + Number(mo ?? 0) * 30 + Number(w ?? 0) * 7 + Number(d ?? 0) || null;
}

export function planTitle(kind: PlanKind): string {
  return kind === 'monthly' ? '월 구독' : kind === 'annual' ? '연 구독' : '막판 집중 패스';
}

/** 패스 이용 기간 표기: 스토어가 준 기간(예: P2M → 2개월), 없으면 60일 */
export function passPeriodLabel(p: Pick<PlanInfo, 'period'>): string {
  return periodLabel(p.period) || '60일';
}

export function cancelHowTo(platform: 'ios' | 'android'): string {
  return platform === 'ios'
    ? '해지: iPhone 설정 > 내 이름(Apple 계정) > 구독에서 언제든 해지할 수 있습니다.'
    : '해지: Google Play 스토어 앱 > 프로필 > 결제 및 정기 결제 > 정기 결제에서 언제든 해지할 수 있습니다.';
}

/** 상품 하나의 고지 문장들 */
export function planDisclosure(p: PlanInfo, platform: 'ios' | 'android'): string[] {
  if (p.kind === 'pass') {
    const per = passPeriodLabel(p);
    return [
      `${p.priceString} 1회 결제, 결제한 날부터 ${per} 동안 유료 기능을 쓸 수 있습니다.`,
      `자동 갱신되지 않으며, ${per}이 지나면 추가 결제 없이 무료 이용으로 돌아갑니다.`,
    ];
  }
  const per = periodLabel(p.period) || (p.kind === 'monthly' ? '1개월' : '1년');
  const lines: string[] = [];
  if (p.trialDays) {
    lines.push(
      `무료 체험 ${p.trialDays}일이 끝나면 ${p.priceString}이 결제되고, 이후 ${per}마다 같은 금액이 자동 결제됩니다.`,
    );
    lines.push(
      platform === 'ios'
        ? '체험이 끝나기 최소 24시간 전에 해지하면 요금이 청구되지 않습니다.'
        : '체험이 끝나기 전에 해지하면 요금이 청구되지 않습니다.',
    );
  } else {
    lines.push(`${per}마다 ${p.priceString}이 자동 결제되는 구독입니다.`);
  }
  lines.push(
    platform === 'ios'
      ? '현재 이용 기간이 끝나기 최소 24시간 전에 해지하지 않으면 자동 갱신되며, 갱신 요금은 기간 종료 전 24시간 안에 청구됩니다.'
      : '해지하지 않으면 매 결제 주기마다 자동 갱신됩니다. 해지해도 이미 결제한 기간이 끝날 때까지 이용할 수 있습니다.',
  );
  lines.push(cancelHowTo(platform));
  return lines;
}

export const COMMON_PURCHASE_NOTES = [
  '가격은 스토어에 표시된 최종 결제 금액이며, 결제는 App Store 또는 Google Play 계정으로 청구됩니다.',
  '결제 취소·환불은 각 스토어의 환불 정책을 따릅니다.',
  '같은 스토어 계정으로 다른 기기에서도 "구매 복원"으로 이용권을 되살릴 수 있습니다.',
];
