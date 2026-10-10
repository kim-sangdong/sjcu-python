import Ionicons from '@expo/vector-icons/Ionicons';
import { useRouter, type Href } from 'expo-router';
import React from 'react';
import { Pressable, View } from 'react-native';

import { DISCLAIMER_UNOFFICIAL } from '@/core/copy';
import { useApp } from '@/state/AppProvider';
import { AppText, Card, Screen } from '@/ui/primitives';
import { MIN_TOUCH, SPACE } from '@/ui/theme';

type IconName = React.ComponentProps<typeof Ionicons>['name'];

function MenuRow({ icon, title, sub, href }: { icon: IconName; title: string; sub: string; href: Href }) {
  const router = useRouter();
  const { colors } = useApp();
  return (
    <Pressable
      onPress={() => router.push(href)}
      accessibilityRole="button"
      accessibilityLabel={`${title}. ${sub}`}
      style={({ pressed }) => ({
        minHeight: MIN_TOUCH + 12,
        flexDirection: 'row',
        alignItems: 'center',
        gap: SPACE.m,
        paddingVertical: SPACE.s,
        opacity: pressed ? 0.7 : 1,
      })}
    >
      <Ionicons name={icon} size={26} color={colors.primary} />
      <View style={{ flex: 1 }}>
        <AppText weight="700">{title}</AppText>
        <AppText variant="small" color={colors.textMuted}>
          {sub}
        </AppText>
      </View>
      <Ionicons name="chevron-forward" size={20} color={colors.textMuted} />
    </Pressable>
  );
}

export default function MoreScreen() {
  const { colors, isPremium, pack } = useApp();
  const hasDecks = Boolean(pack.manifest.decks && Object.keys(pack.manifest.decks).length);
  const lock = isPremium ? '' : ' · 유료';
  return (
    <Screen>
      <Card>
        <MenuRow icon="calculator-outline" title="숫자 암기 카드" sub={`건폐율·기간·세율 표${isPremium ? '' : ' · 일부 무료'}`} href="/cards" />
        <MenuRow icon="timer-outline" title="자체 모의고사" sub={`40문항 · 타이머 · 예상 점수(참고용)${lock}`} href="/mock" />
        <MenuRow icon="newspaper-outline" title="법령 개정 알림" sub={`제목 무료 · 상세 비교${lock}`} href="/amendments" />
        <MenuRow icon="checkmark-done-outline" title="가채점" sub="시험 당일 정답 번호로 채점" href="/grading" />
        {hasDecks ? <MenuRow icon="play-circle-outline" title="영상 연동 문항" sub="쇼츠에 나온 지문 풀기" href="/decks" /> : null}
      </Card>
      <Card>
        <MenuRow
          icon="star-outline"
          title={isPremium ? '이용권 이용 중' : '이용권'}
          sub={isPremium ? '구독 관리·복원은 설정에서' : '월·연 구독, 시험 막판 패스'}
          href={{ pathname: '/paywall', params: { from: 'more' } }}
        />
        <MenuRow icon="settings-outline" title="설정" sub="글자 크기·고대비·화면 모드·구독 관리" href="/settings" />
      </Card>
      <AppText variant="small" color={colors.textMuted}>
        {DISCLAIMER_UNOFFICIAL}
      </AppText>
    </Screen>
  );
}
