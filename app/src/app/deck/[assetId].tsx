/**
 * 영상 연동 덱. 진입 경로:
 *   hamjeongnote://deck/<asset_id>?utm_source=yt_shorts&utm_campaign=...
 *   https://<APP_LINK_DOMAIN>/deck/<asset_id>  (유니버설 링크·앱 링크, 도메인 설정 시)
 * 매니페스트 decks 에 없으면 안내만 한다.
 */
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect, useRef } from 'react';

import { subjectName } from '@/core/exam';
import { deckItemIds, deckTitle } from '@/core/pack/validate';
import { utmFromParams } from '@/core/referrer';
import { itemsByIds } from '@/core/session';
import { track } from '@/data/telemetry';
import { useApp } from '@/state/AppProvider';
import { AppText, Button, Card, Screen } from '@/ui/primitives';

export default function DeckScreen() {
  const params = useLocalSearchParams<Record<string, string>>();
  const assetId = String(params.assetId ?? '');
  const router = useRouter();
  const { colors, pack, settings } = useApp();
  const entry = pack.manifest.decks?.[assetId];
  const items = itemsByIds(deckItemIds(entry) ?? [], pack.byId);
  const title = deckTitle(entry) ?? '영상에 나온 문항';
  const logged = useRef(false);

  useEffect(() => {
    if (logged.current) return;
    logged.current = true;
    void track('deck_open', { asset_id: assetId, found: items.length > 0, items: items.length, ...utmFromParams(params) });
  }, [assetId, items.length, params]);

  const subjects = [...new Set(items.map((it) => it.subject))].map((c) => subjectName(pack.manifest, c));

  return (
    <Screen>
      <Stack.Screen options={{ title: '영상 문항' }} />
      {items.length === 0 ? (
        <Card>
          <AppText variant="heading">이 영상의 문항을 찾지 못했습니다</AppText>
          <AppText color={colors.textMuted}>
            {pack.source === 'seed'
              ? '인터넷에 연결되면 최신 문항을 받아 다시 보여 드립니다.'
              : '영상이 오래되었거나 문항이 바뀌었을 수 있습니다. 오늘의 문항으로 같은 범위를 풀어 보세요.'}
          </AppText>
          <Button title="처음 화면으로" variant="secondary" onPress={() => router.replace('/')} />
        </Card>
      ) : (
        <Card>
          <AppText variant="heading">{title}</AppText>
          <AppText color={colors.textMuted}>
            {items.length}문항 · {subjects.join(', ')}
          </AppText>
          <AppText variant="small" color={colors.textMuted}>
            영상 연동 문항은 무료이며 하루 무료 한도에서 빠지지 않습니다.
          </AppText>
          <Button title="바로 풀기" onPress={() => router.push({ pathname: '/quiz', params: { mode: 'deck', deck: assetId } })} />
          {!settings.onboarded ? (
            <Button title="시험 정보 설정하고 매일 문항 받기" variant="ghost" onPress={() => router.replace('/onboarding')} />
          ) : null}
        </Card>
      )}
    </Screen>
  );
}
