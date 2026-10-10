import { useRouter } from 'expo-router';
import { Pressable } from 'react-native';

import { collectAmendments } from '@/core/amendments';
import { DISCLAIMER_LAW } from '@/core/copy';
import { dotDate } from '@/core/time';
import { useApp } from '@/state/AppProvider';
import { AppText, Card, Notice, Screen } from '@/ui/primitives';
import { MIN_TOUCH, SPACE } from '@/ui/theme';

/** 개정 알림 목록: 제목·시행일은 무료, 상세 비교는 유료 */
export default function AmendmentsScreen() {
  const router = useRouter();
  const { colors, pack, isPremium } = useApp();
  const list = collectAmendments(pack);
  return (
    <Screen>
      {list.length === 0 ? (
        <Card>
          <AppText color={colors.textMuted}>아직 등록된 개정 알림이 없습니다. 시험 범위 법령이 바뀌면 여기에 올립니다.</AppText>
        </Card>
      ) : null}
      {list.map((a) => (
        <Pressable
          key={a.id}
          onPress={() => router.push({ pathname: '/amendments/[id]', params: { id: a.id } })}
          accessibilityRole="button"
          accessibilityLabel={`${a.title}. ${a.law_name}, ${dotDate(a.effective_date)} 시행.${isPremium ? '' : ' 상세는 유료'}`}
          style={({ pressed }) => ({ opacity: pressed ? 0.8 : 1, minHeight: MIN_TOUCH })}
        >
          <Card>
            <AppText weight="700">{a.title}</AppText>
            <AppText variant="small" color={colors.textMuted}>
              {a.law_name} · {dotDate(a.effective_date)} 시행{isPremium ? '' : ' · 상세 비교는 이용권'}
            </AppText>
          </Card>
        </Pressable>
      ))}
      <Notice text={DISCLAIMER_LAW} />
      <AppText variant="small" color={colors.textMuted} style={{ marginTop: SPACE.s }}>
        개정 내용은 국가법령정보 공동활용 자료로 확인한 뒤 올립니다.
      </AppText>
    </Screen>
  );
}
