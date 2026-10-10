import { useRouter } from 'expo-router';
import React from 'react';
import { View } from 'react-native';

import { useApp } from '@/state/AppProvider';
import { AppText, Button, Card } from './primitives';
import { SPACE } from './theme';

/** 유료 기능 입구. 무료 사용자에게는 무엇을 얻는지 보여 주고 결제 화면으로 안내한다 */
export function PremiumGate({
  feature,
  bullets,
  children,
  teaser,
}: {
  /** 결제 화면 진입 경로 기록용(paywall_view props.source) */
  feature: string;
  bullets: string[];
  children: React.ReactNode;
  /** 무료 사용자에게도 보여 줄 맛보기 */
  teaser?: React.ReactNode;
}) {
  const { isPremium, colors } = useApp();
  const router = useRouter();
  if (isPremium) return <>{children}</>;
  return (
    <View style={{ gap: SPACE.m }}>
      {teaser}
      <Card>
        <AppText variant="heading">유료 기능입니다</AppText>
        {bullets.map((b) => (
          <AppText key={b} color={colors.textMuted}>
            · {b}
          </AppText>
        ))}
        <Button
          title="이용권 살펴보기"
          hint="가격과 자동 갱신 조건을 확인하는 화면으로 이동합니다"
          onPress={() => router.push({ pathname: '/paywall', params: { from: feature } })}
        />
      </Card>
    </View>
  );
}
