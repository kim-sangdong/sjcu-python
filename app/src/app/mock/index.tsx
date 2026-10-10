import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { View } from 'react-native';

import { MOCK_NOTICE } from '@/core/copy';
import { examRules } from '@/core/exam';
import { availableMockSets, partAverage } from '@/core/mock';
import { dotDate, kstDateKey } from '@/core/time';
import { listMockResults, type MockResultRow } from '@/data/repo';
import { useApp } from '@/state/AppProvider';
import { PremiumGate } from '@/ui/PremiumGate';
import { AppText, Button, Card, Notice, Row, Screen } from '@/ui/primitives';
import { SPACE } from '@/ui/theme';

export default function MockHome() {
  const router = useRouter();
  const { colors, pack } = useApp();
  const rules = examRules(pack.manifest);
  const sets = availableMockSets(rules.mock_sets, pack.items);
  const [history, setHistory] = useState<MockResultRow[]>([]);

  useFocusEffect(
    useCallback(() => {
      void listMockResults(30).then(setHistory);
    }, []),
  );

  // 세트별 가장 최근 점수 → 차수 평균(참고용)
  const latest = new Map<string, MockResultRow>();
  for (const h of history) if (!latest.has(h.setCode)) latest.set(h.setCode, h);
  const latestScores = [...latest.values()].map((h) => ({ part: h.part, score100: h.score100 }));
  const titleOf = (code: string) => rules.mock_sets.find((s) => s.code === code)?.title ?? code;

  return (
    <Screen>
      <PremiumGate
        feature="mock_exam"
        bullets={['과목별 40문항 · 실제 시험 시간에 맞춘 타이머', '예상 점수(참고용)와 과락 경고', '틀린 문항은 오답 복습으로 이어짐']}
        teaser={<Notice text={MOCK_NOTICE} />}
      >
        <Notice text={MOCK_NOTICE} />
        {sets.length === 0 ? (
          <Card>
            <AppText color={colors.textMuted}>모의고사를 만들 문항이 아직 없습니다. 문항 팩이 갱신되면 열립니다.</AppText>
          </Card>
        ) : null}
        {sets.map((s) => {
          const last = latest.get(s.code);
          const total = s.compose.reduce((a, c) => a + c.count, 0);
          return (
            <Card key={s.code}>
              <AppText variant="heading">{s.title}</AppText>
              <AppText color={colors.textMuted}>
                {total}문항 · {s.minutes}분
              </AppText>
              {last ? (
                <AppText variant="small" color={last.belowFail ? colors.wrong : colors.textMuted}>
                  최근 {dotDate(kstDateKey(last.finishedAt))} · 예상 점수 {last.score100}점(참고용)
                  {last.belowFail ? ` · 과락 기준(${rules.fail_line}점) 미만` : ''}
                </AppText>
              ) : null}
              <Button title="시작" onPress={() => router.push({ pathname: '/mock/run', params: { set: s.code } })} />
            </Card>
          );
        })}
        {[1, 2].map((part) => {
          const avg = partAverage(latestScores, part);
          return avg === null ? null : (
            <Card key={part}>
              <AppText variant="heading">{part}차 과목 평균(참고용)</AppText>
              <AppText>
                과목별 최근 점수 평균 {avg}점 · 과락 기준은 과목마다 {rules.fail_line}점입니다.
              </AppText>
            </Card>
          );
        })}
        {history.length ? (
          <Card>
            <AppText variant="heading">지난 기록</AppText>
            {history.slice(0, 10).map((h) => (
              <View key={h.id} style={{ borderTopWidth: 1, borderTopColor: colors.border, paddingTop: SPACE.xs }}>
                <Row style={{ justifyContent: 'space-between' }}>
                  <AppText variant="small" style={{ flex: 1 }}>
                    {titleOf(h.setCode)}
                  </AppText>
                  <AppText variant="small" weight="700" color={h.belowFail ? colors.wrong : colors.text}>
                    {h.score100}점
                  </AppText>
                </Row>
                <AppText variant="small" color={colors.textMuted}>
                  {dotDate(kstDateKey(h.finishedAt))} · {h.correct}/{h.total}
                </AppText>
              </View>
            ))}
          </Card>
        ) : null}
      </PremiumGate>
    </Screen>
  );
}
