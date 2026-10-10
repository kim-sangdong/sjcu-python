import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { View } from 'react-native';

import { answerLabel } from '@/core/grading';
import { subjectName } from '@/core/exam';
import type { SrsCard } from '@/core/srs';
import { getWrongCards } from '@/data/repo';
import { useApp } from '@/state/AppProvider';
import { PremiumGate } from '@/ui/PremiumGate';
import { AppText, Button, Card, Screen } from '@/ui/primitives';
import { SPACE } from '@/ui/theme';

export default function ReviewScreen() {
  const router = useRouter();
  const { colors, pack, dueCount, refreshDue } = useApp();
  const [wrong, setWrong] = useState<SrsCard[]>([]);

  useFocusEffect(
    useCallback(() => {
      void refreshDue();
      void getWrongCards(200).then(setWrong);
    }, [refreshDue]),
  );

  const wrongItems = wrong.map((c) => ({ card: c, item: pack.byId.get(c.itemId) })).filter((x) => x.item);

  const teaser = (
    <Card>
      <AppText variant="heading">오답 복습</AppText>
      <AppText color={colors.textMuted}>
        지금 복습할 문항 {dueCount}개 · 오답노트 {wrongItems.length}개
      </AppText>
      <AppText variant="small" color={colors.textMuted}>
        틀린 문항을 잊어버릴 즈음 다시 내 주는 간격 반복(SM-2 방식)으로, 시험일 전까지 복습 일정을 맞춥니다.
      </AppText>
    </Card>
  );

  return (
    <Screen>
      <PremiumGate
        feature="review"
        teaser={teaser}
        bullets={['틀린 문항을 알맞은 때에 다시 출제(간격 반복)', '오답노트 전체 보기와 다시 풀기', '문항 은행 무제한']}
      >
        <Card>
          <AppText variant="heading">지금 복습할 문항 {dueCount}개</AppText>
          <AppText color={colors.textMuted}>틀린 문항은 10분 뒤, 맞힌 문항은 1일 → 6일 → 점점 긴 간격으로 다시 나옵니다.</AppText>
          <Button
            title="복습 시작"
            disabled={dueCount === 0}
            onPress={() => router.push({ pathname: '/quiz', params: { mode: 'review' } })}
          />
        </Card>
        <Card>
          <AppText variant="heading">오답노트 {wrongItems.length}개</AppText>
          {wrongItems.length ? (
            <Button
              title="오답 다시 풀기"
              variant="secondary"
              onPress={() => router.push({ pathname: '/quiz', params: { mode: 'wrong' } })}
            />
          ) : (
            <AppText color={colors.textMuted}>아직 틀린 문항이 없습니다.</AppText>
          )}
          {wrongItems.slice(0, 50).map(({ card, item }) => (
            <View
              key={card.itemId}
              style={{ borderTopWidth: 1, borderTopColor: colors.border, paddingTop: SPACE.s, gap: 2 }}
            >
              <AppText variant="small" color={colors.textMuted}>
                {subjectName(pack.manifest, item!.subject)} · 틀린 횟수 {card.lapses}
                {card.lastWrong ? ' · 최근 오답' : ''}
              </AppText>
              <AppText numberOfLines={3}>{item!.stem}</AppText>
              <AppText variant="small" weight="700" color={colors.correct}>
                정답: {answerLabel(item!)}
              </AppText>
            </View>
          ))}
        </Card>
      </PremiumGate>
    </Screen>
  );
}
