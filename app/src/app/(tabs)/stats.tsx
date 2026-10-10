import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useMemo, useState } from 'react';
import { View } from 'react-native';

import { subjectName } from '@/core/exam';
import { diagnose, type Bucket } from '@/core/stats';
import { getAnswerRows } from '@/data/repo';
import { useApp } from '@/state/AppProvider';
import { PremiumGate } from '@/ui/PremiumGate';
import { AppText, Button, Card, ProgressBar, Row, Screen } from '@/ui/primitives';
import { SPACE } from '@/ui/theme';

function pct(r: number): string {
  return `${Math.round(r * 100)}%`;
}

function BucketList({ title, buckets, empty }: { title: string; buckets: Bucket[]; empty: string }) {
  const { colors } = useApp();
  return (
    <Card>
      <AppText variant="heading">{title}</AppText>
      {buckets.length === 0 ? <AppText color={colors.textMuted}>{empty}</AppText> : null}
      {buckets.slice(0, 10).map((b) => (
        <View key={b.key} style={{ gap: SPACE.xs }}>
          <Row style={{ justifyContent: 'space-between' }}>
            <AppText variant="small" style={{ flex: 1 }}>
              {b.label}
            </AppText>
            <AppText variant="small" weight="700" color={b.rate < 0.6 ? colors.wrong : colors.text}>
              {pct(b.rate)} ({b.correct}/{b.attempts})
            </AppText>
          </Row>
          <ProgressBar value={b.rate} label={`${b.label} 정답률 ${pct(b.rate)}, ${b.attempts}문항`} />
        </View>
      ))}
    </Card>
  );
}

export default function StatsScreen() {
  const router = useRouter();
  const { colors, pack } = useApp();
  const [rows, setRows] = useState<{ itemId: string; correct: boolean }[]>([]);

  useFocusEffect(
    useCallback(() => {
      void getAnswerRows().then(setRows);
    }, []),
  );

  const d = useMemo(() => diagnose(rows, pack.byId, (c) => subjectName(pack.manifest, c)), [rows, pack]);
  const weakest = d.bySubject[0];

  const teaser = (
    <Card>
      <AppText variant="heading">전체 정답률</AppText>
      <AppText variant="title" color={colors.primary}>
        {d.overall.attempts ? pct(d.overall.rate) : '-'}
      </AppText>
      <AppText color={colors.textMuted}>지금까지 {d.overall.attempts}문항을 풀었습니다.</AppText>
    </Card>
  );

  return (
    <Screen>
      <PremiumGate
        feature="diagnosis"
        teaser={teaser}
        bullets={['과목·단원·함정 유형별 정답률', '가장 약한 곳부터 골라 풀기', '자체 모의고사와 예상 점수(참고용)']}
      >
        {teaser}
        {weakest ? (
          <Card>
            <AppText variant="heading">먼저 보강할 과목</AppText>
            <AppText>
              {weakest.label} · 정답률 {pct(weakest.rate)}
            </AppText>
            <Button
              title={`${weakest.label} 풀기`}
              variant="secondary"
              onPress={() => router.push({ pathname: '/quiz', params: { mode: 'subject', subject: weakest.key } })}
            />
          </Card>
        ) : null}
        <BucketList title="과목별" buckets={d.bySubject} empty="문항을 풀면 과목별 정답률이 나옵니다." />
        <BucketList title="단원별(3문항 이상)" buckets={d.byUnit} empty="단원마다 3문항 이상 풀면 나옵니다." />
        <BucketList title="함정 유형별(3문항 이상)" buckets={d.byTrap} empty="함정 유형마다 3문항 이상 풀면 나옵니다." />
        <AppText variant="small" color={colors.textMuted}>
          순서는 표본 수를 함께 고려한 하한값(윌슨 95%) 기준입니다. 자체 문항 기준이며 실제 시험 결과와 다를 수 있습니다.
        </AppText>
      </PremiumGate>
    </Screen>
  );
}
