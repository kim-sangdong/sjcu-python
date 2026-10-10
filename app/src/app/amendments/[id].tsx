import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { View } from 'react-native';

import { collectAmendments } from '@/core/amendments';
import { DISCLAIMER_LAW } from '@/core/copy';
import { subjectName } from '@/core/exam';
import { dotDate } from '@/core/time';
import { BasisList } from '@/features/quiz/BasisBadge';
import { useApp } from '@/state/AppProvider';
import { PremiumGate } from '@/ui/PremiumGate';
import { AppText, Button, Card, Notice, Screen } from '@/ui/primitives';
import { SPACE } from '@/ui/theme';

export default function AmendmentDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const { colors, pack } = useApp();
  const a = collectAmendments(pack).find((x) => x.id === id);
  if (!a) {
    return (
      <Screen>
        <AppText>개정 알림을 찾지 못했습니다.</AppText>
        <Button title="목록으로" variant="secondary" onPress={() => router.back()} />
      </Screen>
    );
  }
  const head = (
    <Card>
      <AppText variant="heading">{a.title}</AppText>
      <AppText color={colors.textMuted}>
        {a.law_name} · {dotDate(a.effective_date)} 시행
        {a.published_at ? ` · ${dotDate(a.published_at)} 공포` : ''}
      </AppText>
    </Card>
  );
  return (
    <Screen>
      <Stack.Screen options={{ title: '개정 상세' }} />
      {head}
      <PremiumGate feature="amendment_detail" bullets={['바뀐 숫자·요건을 개정 전후로 나란히 비교', '개정에 걸린 문항은 자동으로 바꿔 출제']}>
        {a.summary ? (
          <Card>
            <AppText>{a.summary}</AppText>
          </Card>
        ) : null}
        {(a.changes ?? []).map((c, i) => (
          <Card key={`${a.id}-${i}`}>
            <AppText weight="700">{c.label}</AppText>
            <View style={{ flexDirection: 'row', gap: SPACE.s }}>
              <View style={{ flex: 1, gap: 2 }} accessibilityLabel={`개정 전: ${c.before}`}>
                <AppText variant="small" color={colors.textMuted}>
                  개정 전
                </AppText>
                <AppText style={{ textDecorationLine: 'line-through' }} color={colors.textMuted}>
                  {c.before}
                </AppText>
              </View>
              <View style={{ flex: 1, gap: 2 }} accessibilityLabel={`개정 후: ${c.after}`}>
                <AppText variant="small" color={colors.textMuted}>
                  개정 후
                </AppText>
                <AppText weight="700" color={colors.primary}>
                  {c.after}
                </AppText>
              </View>
            </View>
          </Card>
        ))}
        {a.basis?.length ? <BasisList basis={a.basis} /> : null}
        {a.affected_item_ids?.length ? (
          <Card>
            <AppText weight="700">관련 문항 {a.affected_item_ids.length}개</AppText>
            {a.affected_item_ids.slice(0, 20).map((iid) => {
              const it = pack.byId.get(iid);
              return it ? (
                <AppText key={iid} variant="small" color={colors.textMuted} numberOfLines={3}>
                  · [{subjectName(pack.manifest, it.subject)}] {it.stem}
                </AppText>
              ) : null;
            })}
            <AppText variant="small" color={colors.textMuted}>
              관련 문항은 시행일 기준으로 다시 검증해 고쳐 출제합니다.
            </AppText>
          </Card>
        ) : null}
      </PremiumGate>
      <Notice text={DISCLAIMER_LAW} />
    </Screen>
  );
}
