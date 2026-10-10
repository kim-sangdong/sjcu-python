import { useRouter } from 'expo-router';
import { Pressable } from 'react-native';

import { deckItemIds, deckTitle } from '@/core/pack/validate';
import { dotDate } from '@/core/time';
import type { DeckEntry } from '@/core/pack/types';
import { useApp } from '@/state/AppProvider';
import { AppText, Card, Screen } from '@/ui/primitives';
import { MIN_TOUCH } from '@/ui/theme';

function publishedAt(e: DeckEntry): string {
  return !Array.isArray(e) && e.published_at ? e.published_at : '';
}

/** 매니페스트 decks 목록(최근 순). decks 가 없으면 이 화면으로 가는 버튼 자체가 숨겨진다 */
export default function DecksScreen() {
  const router = useRouter();
  const { colors, pack } = useApp();
  const entries = Object.entries(pack.manifest.decks ?? {})
    .filter(([, e]) => (deckItemIds(e) ?? []).length > 0)
    .sort(([a, ea], [b, eb]) => publishedAt(eb).localeCompare(publishedAt(ea)) || b.localeCompare(a));
  return (
    <Screen>
      {entries.length === 0 ? (
        <Card>
          <AppText color={colors.textMuted}>아직 영상 연동 문항이 없습니다.</AppText>
        </Card>
      ) : null}
      {entries.map(([id, e], i) => {
        const n = deckItemIds(e)?.length ?? 0;
        const title = deckTitle(e) ?? `영상 문항 ${entries.length - i}`;
        const when = publishedAt(e);
        return (
          <Pressable
            key={id}
            onPress={() => router.push({ pathname: '/deck/[assetId]', params: { assetId: id } })}
            accessibilityRole="button"
            accessibilityLabel={`${title}, ${n}문항`}
            style={({ pressed }) => ({ opacity: pressed ? 0.8 : 1, minHeight: MIN_TOUCH })}
          >
            <Card>
              <AppText weight="700">{title}</AppText>
              <AppText variant="small" color={colors.textMuted}>
                {n}문항{when ? ` · ${dotDate(when.slice(0, 10))}` : ''}
              </AppText>
            </Card>
          </Pressable>
        );
      })}
    </Screen>
  );
}
