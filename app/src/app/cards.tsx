/**
 * 숫자 암기 카드(유료 전체, 무료는 첫 세트만). 값은 가려 두고 눌러서 확인한다.
 */
import { useRouter } from 'expo-router';
import { useState } from 'react';
import { Pressable, View } from 'react-native';

import { subjectName } from '@/core/exam';
import type { NumberCardSet } from '@/core/pack/types';
import { BasisList } from '@/features/quiz/BasisBadge';
import { useApp } from '@/state/AppProvider';
import { AppText, Button, Card, Row, Screen } from '@/ui/primitives';
import { MIN_TOUCH, SPACE } from '@/ui/theme';

const FREE_SETS = 1;

function CardSet({ set }: { set: NumberCardSet }) {
  const { colors, pack } = useApp();
  const [shown, setShown] = useState<Set<number>>(new Set());
  const all = shown.size === set.rows.length;
  const toggle = (i: number) =>
    setShown((s) => {
      const n = new Set(s);
      if (n.has(i)) n.delete(i);
      else n.add(i);
      return n;
    });
  return (
    <Card>
      <AppText variant="small" color={colors.textMuted}>
        {subjectName(pack.manifest, set.subject)}
      </AppText>
      <AppText variant="heading">{set.title}</AppText>
      <BasisList basis={set.basis} />
      {set.rows.map((r, i) => {
        const open = shown.has(i);
        return (
          <Pressable
            key={`${set.id}-${i}`}
            onPress={() => toggle(i)}
            accessibilityRole="button"
            accessibilityLabel={open ? `${r.label}: ${r.value}` : `${r.label}, 값 가림. 눌러서 확인`}
            style={{
              minHeight: MIN_TOUCH,
              flexDirection: 'row',
              alignItems: 'center',
              justifyContent: 'space-between',
              borderTopWidth: 1,
              borderTopColor: colors.border,
              paddingVertical: SPACE.xs,
              gap: SPACE.s,
            }}
          >
            <AppText style={{ flex: 1 }}>{r.label}</AppText>
            <AppText weight="700" color={open ? colors.primary : colors.textMuted}>
              {open ? r.value : '눌러서 확인'}
            </AppText>
          </Pressable>
        );
      })}
      <Row>
        <Button
          title={all ? '모두 가리기' : '모두 보기'}
          variant="ghost"
          onPress={() => setShown(all ? new Set() : new Set(set.rows.map((_, i) => i)))}
        />
      </Row>
    </Card>
  );
}

export default function CardsScreen() {
  const router = useRouter();
  const { colors, pack, isPremium } = useApp();
  const sets = pack.manifest.extra?.number_cards ?? [];
  const visible = isPremium ? sets : sets.slice(0, FREE_SETS);
  return (
    <Screen>
      {sets.length === 0 ? (
        <Card>
          <AppText color={colors.textMuted}>아직 숫자 카드가 없습니다. 문항 팩이 갱신되면 나타납니다.</AppText>
        </Card>
      ) : null}
      {visible.map((s) => (
        <CardSet key={s.id} set={s} />
      ))}
      {!isPremium && sets.length > FREE_SETS ? (
        <Card>
          <AppText variant="heading">카드 {sets.length - FREE_SETS}세트 더 있음</AppText>
          <View style={{ gap: 2 }}>
            {sets.slice(FREE_SETS, FREE_SETS + 5).map((s) => (
              <AppText key={s.id} color={colors.textMuted}>
                · {s.title}
              </AppText>
            ))}
          </View>
          <Button title="전체 카드 열기" onPress={() => router.push({ pathname: '/paywall', params: { from: 'number_cards' } })} />
        </Card>
      ) : null}
    </Screen>
  );
}
