/**
 * 근거 배지: [법령명 제○조 제○항 · YYYY.M.D 시행 기준], 판례는 [판례 사건번호 · YYYY.M.D 선고].
 * 시드 팩(근거 대조 전)일 때는 날짜 대신 "시행일 대조 전"을 보인다.
 */
import { useState } from 'react';
import { Pressable, View } from 'react-native';

import type { Basis } from '@/core/pack/types';
import { dotDate } from '@/core/time';
import { useApp } from '@/state/AppProvider';
import { AppText } from '@/ui/primitives';
import { SPACE } from '@/ui/theme';

/** 팩이 만든 badge 를 그대로 쓰고, 없을 때만 같은 규칙으로 만든다. 시드(대조 전)는 날짜 대신 "시행일 대조 전" */
export function basisLabel(b: Basis, unverified: boolean): string {
  const isCase = b.kind === 'precedent' || b.kind === 'case';
  if (b.badge && !unverified) return b.badge;
  if (isCase) return `[판례 ${b.case_no ?? ''} · ${dotDate(b.decided_at)} 선고]`;
  if (b.kind === 'calc') return b.note ? `[계산 · ${b.note}]` : '[계산]';
  const when = unverified ? '시행일 대조 전' : `${dotDate(b.effective_date)} 시행 기준`;
  return `[${b.law_name ?? ''} ${b.article ?? ''} · ${when}]`;
}

function spokenLabel(b: Basis, unverified: boolean, withQuote: boolean): string {
  const d = (s?: string) => {
    if (!s) return '';
    const [y, m, day] = s.split('-');
    return `${y}년 ${Number(m)}월 ${Number(day)}일`;
  };
  if (b.kind === 'precedent' || b.kind === 'case') return `근거 판례 ${b.case_no ?? ''}, ${d(b.decided_at)} 선고`;
  if (b.kind === 'calc') return withQuote && b.quote ? `계산식 ${b.quote}` : `계산 문항${b.note ? ', ' + b.note : ''}`;
  return `근거 ${b.law_name ?? ''} ${b.article ?? ''}, ${unverified ? '시행일 대조 전' : d(b.effective_date) + ' 시행 기준'}`;
}

export function BasisBadge({ basis, quoteEnabled = true }: { basis: Basis; quoteEnabled?: boolean }) {
  const { colors, pack } = useApp();
  const [open, setOpen] = useState(false);
  const unverified = pack.manifest.extra?.unverified === true;
  const hasQuote = quoteEnabled && Boolean(basis.quote);
  return (
    <View style={{ gap: SPACE.xs }}>
      <Pressable
        disabled={!hasQuote}
        onPress={() => setOpen((v) => !v)}
        accessibilityRole={hasQuote ? 'button' : 'text'}
        accessibilityLabel={spokenLabel(basis, unverified, quoteEnabled)}
        accessibilityHint={hasQuote ? (open ? '조문 인용을 접습니다' : '조문 인용을 펼칩니다') : undefined}
        accessibilityState={hasQuote ? { expanded: open } : undefined}
        style={{
          alignSelf: 'flex-start',
          backgroundColor: colors.badgeBg,
          borderColor: colors.border,
          borderWidth: 1,
          borderRadius: 8,
          paddingHorizontal: SPACE.s,
          paddingVertical: SPACE.xs,
          minHeight: 32,
          justifyContent: 'center',
        }}
      >
        <AppText variant="small" color={colors.badgeText} weight="700">
          {basisLabel(basis, unverified)}
          {basis.kind === 'calc' && basis.quote && quoteEnabled ? ` ${basis.quote}` : ''}
          {hasQuote && basis.kind !== 'calc' ? (open ? '  접기' : '  원문') : ''}
        </AppText>
      </Pressable>
      {open && hasQuote && basis.kind !== 'calc' ? (
        <AppText variant="small" color={colors.textMuted} style={{ paddingLeft: SPACE.s }}>
          “{basis.quote}”
        </AppText>
      ) : null}
    </View>
  );
}

export function BasisList({ basis, quoteEnabled = true }: { basis: Basis[]; quoteEnabled?: boolean }) {
  return (
    <View style={{ gap: SPACE.xs }}>
      {basis.map((b, i) => (
        <BasisBadge
          key={`${b.law_name ?? b.case_no ?? b.kind}-${b.article ?? ''}-${i}`}
          basis={b}
          quoteEnabled={quoteEnabled}
        />
      ))}
    </View>
  );
}
