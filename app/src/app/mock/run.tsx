/**
 * 모의고사 진행: 타이머(시간이 끝나면 자동 제출) → 채점 → 예상 점수(참고용)·과락 경고 → 기록.
 * 진행 중에 화면을 나가려 하면 확인을 받는다.
 */
import { Stack, useLocalSearchParams, useNavigation, useRouter } from 'expo-router';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Pressable, View } from 'react-native';

import { MOCK_NOTICE } from '@/core/copy';
import { examRules, subjectName } from '@/core/exam';
import { answerLabel, isCorrect, type Response } from '@/core/grading';
import { buildMock, scoreMock, type MockScore } from '@/core/mock';
import type { Item } from '@/core/pack/types';
import { mulberry32 } from '@/core/random';
import { newCard, review } from '@/core/srs';
import { formatClock } from '@/core/time';
import { getCard, recordAnswer, saveCard, saveMockResult } from '@/data/repo';
import { trackAnswer } from '@/data/telemetry';
import { QuestionCard } from '@/features/quiz/QuestionCard';
import { useApp } from '@/state/AppProvider';
import { PremiumGate } from '@/ui/PremiumGate';
import { AppText, Button, Card, Notice, Row, Screen } from '@/ui/primitives';
import { MIN_TOUCH, SPACE } from '@/ui/theme';

export default function MockRun() {
  const { set } = useLocalSearchParams<{ set?: string }>();
  const { pack, isPremium } = useApp();
  const rule = examRules(pack.manifest).mock_sets.find((s) => s.code === set);
  if (!isPremium) {
    return (
      <Screen>
        <PremiumGate feature="mock_exam" bullets={['과목별 40문항 모의고사와 예상 점수(참고용)']}>
          {null}
        </PremiumGate>
      </Screen>
    );
  }
  if (!rule) {
    return (
      <Screen>
        <AppText>모의고사 정보를 찾지 못했습니다.</AppText>
      </Screen>
    );
  }
  return <MockSession ruleCode={rule.code} />;
}

function MockSession({ ruleCode }: { ruleCode: string }) {
  const router = useRouter();
  const navigation = useNavigation();
  const { colors, pack, daysLeft, markStudied, refreshDue } = useApp();
  const rules = examRules(pack.manifest);
  const rule = rules.mock_sets.find((s) => s.code === ruleCode)!;

  const [startedAt] = useState(() => Date.now());
  const build = useMemo(() => buildMock(rule, pack.items, mulberry32(startedAt >>> 0)), [rule, pack.items, startedAt]);
  const items = useMemo(() => build.itemIds.map((id) => pack.byId.get(id)!).filter(Boolean) as Item[], [build, pack.byId]);
  const deadline = startedAt + rule.minutes * 60 * 1000;

  const [index, setIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<string, Response>>({});
  const [now, setNow] = useState(Date.now());
  const [score, setScore] = useState<MockScore | null>(null);
  const [results, setResults] = useState<Record<string, boolean | null>>({});
  const submitting = useRef(false);

  const finish = useCallback(async () => {
    if (submitting.current) return;
    submitting.current = true;
    const t = Date.now();
    const res: Record<string, boolean | null> = {};
    for (const it of items) {
      const r = answers[it.id];
      res[it.id] = r ? isCorrect(it, r) : null;
    }
    const sc = scoreMock(
      items.map((it) => it.id),
      res,
      (id) => pack.byId.get(id)?.subject ?? '',
      rules.fail_line,
    );
    // 기록: 모의고사 결과 + 응답 이력·SRS(답한 문항만)
    await saveMockResult({
      setCode: rule.code,
      part: rule.part,
      startedAt,
      finishedAt: t,
      total: sc.total,
      correct: sc.correct,
      score100: sc.score100,
      belowFail: sc.belowFailLine,
      detail: { itemIds: items.map((i) => i.id), results: res },
    });
    for (const it of items) {
      const ok = res[it.id];
      if (ok === null || ok === undefined) continue;
      const prev = (await getCard(it.id)) ?? newCard(it.id, t);
      await saveCard(review(prev, ok ? 'right' : 'wrong', t, { daysToExam: daysLeft }));
      await recordAnswer({ itemId: it.id, correct: ok, mode: 'mock', packVersion: pack.manifest.version, at: t });
      await trackAnswer({ id: it.id, correct: ok });
    }
    void markStudied();
    void refreshDue();
    setResults(res);
    setScore(sc);
  }, [items, answers, pack, rules.fail_line, rule, startedAt, daysLeft, markStudied, refreshDue]);

  // 타이머
  useEffect(() => {
    if (score) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [score]);
  useEffect(() => {
    if (!score && items.length > 0 && now >= deadline) {
      void finish().then(() => Alert.alert('시간이 끝났습니다', '적은 답안으로 채점했습니다.'));
    }
  }, [now, deadline, score, finish, items.length]);

  // 진행 중 나가기 확인
  useEffect(() => {
    if (score || items.length === 0) return;
    const unsub = navigation.addListener('beforeRemove', (e: { preventDefault: () => void; data: { action: unknown } }) => {
      e.preventDefault();
      Alert.alert('모의고사를 그만둘까요?', '지금 나가면 이번 답안은 저장되지 않습니다.', [
        { text: '계속 풀기', style: 'cancel' },
        {
          text: '나가기',
          style: 'destructive',
          onPress: () => navigation.dispatch(e.data.action as Parameters<typeof navigation.dispatch>[0]),
        },
      ]);
    });
    return unsub;
  }, [navigation, score, items.length]);

  const answeredCount = Object.keys(answers).length;
  const confirmSubmit = () => {
    const left = items.length - answeredCount;
    Alert.alert('제출할까요?', left ? `아직 ${left}문항을 풀지 않았습니다.` : '모든 문항에 답했습니다.', [
      { text: '더 풀기', style: 'cancel' },
      { text: '제출', onPress: () => void finish() },
    ]);
  };

  if (items.length === 0) {
    return (
      <Screen>
        <AppText>문항이 부족해 모의고사를 만들 수 없습니다.</AppText>
      </Screen>
    );
  }

  if (score) {
    const wrongItems = items.filter((it) => results[it.id] !== true);
    return (
      <Screen>
        <Stack.Screen options={{ title: '모의고사 결과' }} />
        <Card>
          <AppText variant="small" color={colors.textMuted}>
            {rule.title}
          </AppText>
          <AppText variant="heading">예상 점수(참고용)</AppText>
          <AppText variant="title" color={score.belowFailLine ? colors.wrong : colors.primary}>
            {score.score100}점
          </AppText>
          <AppText>
            {score.total}문항 중 {score.correct}문항 정답 · 답한 문항 {score.answered}개
          </AppText>
          {score.belowFailLine ? (
            <Notice
              tone="warning"
              text={`과락 경고: 과락 기준(${rules.fail_line}점)보다 낮습니다. 틀린 문항을 오답 복습으로 먼저 보강하세요.`}
            />
          ) : null}
          {score.bySubject.length > 1
            ? score.bySubject.map((b) => (
                <AppText key={b.subject} variant="small" color={colors.textMuted}>
                  {subjectName(pack.manifest, b.subject)} {b.correct}/{b.total}
                </AppText>
              ))
            : null}
          {build.shortages.length ? (
            <AppText variant="small" color={colors.textMuted}>
              문항이 부족해 {build.shortages.map((s) => `${subjectName(pack.manifest, s.subject)} ${s.got}/${s.wanted}`).join(', ')}로
              구성했습니다.
            </AppText>
          ) : null}
        </Card>
        <Notice text={MOCK_NOTICE} />
        <Card>
          <AppText variant="heading">틀리거나 비운 문항 {wrongItems.length}개</AppText>
          {wrongItems.map((it) => (
            <View key={it.id} style={{ borderTopWidth: 1, borderTopColor: colors.border, paddingTop: SPACE.s, gap: 2 }}>
              <AppText numberOfLines={4}>
                {items.indexOf(it) + 1}. {it.stem}
              </AppText>
              <AppText variant="small" weight="700" color={colors.correct}>
                정답: {answerLabel(it)}
              </AppText>
              <AppText variant="small" color={colors.textMuted}>
                {it.explanation}
              </AppText>
            </View>
          ))}
        </Card>
        <Button title="닫기" onPress={() => router.back()} />
      </Screen>
    );
  }

  const item = items[index]!;
  const remaining = deadline - now;
  return (
    <Screen>
      <Stack.Screen options={{ title: rule.title }} />
      <Row style={{ justifyContent: 'space-between' }}>
        <AppText weight="700" accessibilityLabel={`${items.length}문항 중 ${index + 1}번`}>
          {index + 1} / {items.length}
        </AppText>
        <AppText
          weight="700"
          color={remaining < 5 * 60 * 1000 ? colors.wrong : colors.text}
          accessibilityLabel={`남은 시간 ${formatClock(remaining)}`}
        >
          남은 시간 {formatClock(remaining)}
        </AppText>
      </Row>
      <QuestionCard
        key={item.id}
        item={item}
        response={answers[item.id] ?? null}
        result={null}
        showFeedback={false}
        onSubmit={(r) => setAnswers((a) => ({ ...a, [item.id]: r }))}
      />
      <Row>
        <Button title="이전" variant="secondary" disabled={index === 0} onPress={() => setIndex((i) => i - 1)} style={{ flex: 1 }} />
        <Button
          title="다음"
          variant="secondary"
          disabled={index >= items.length - 1}
          onPress={() => setIndex((i) => i + 1)}
          style={{ flex: 1 }}
        />
      </Row>
      <Card>
        <AppText variant="small" color={colors.textMuted}>
          문항 바로 가기 · 답한 문항 {answeredCount}/{items.length}
        </AppText>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: SPACE.xs }}>
          {items.map((it, i) => {
            const done = Boolean(answers[it.id]);
            const current = i === index;
            return (
              <Pressable
                key={it.id}
                onPress={() => setIndex(i)}
                accessibilityRole="button"
                accessibilityLabel={`${i + 1}번 문항${done ? ', 답함' : ', 안 풂'}${current ? ', 현재' : ''}`}
                style={{
                  width: MIN_TOUCH,
                  height: MIN_TOUCH,
                  borderRadius: 8,
                  alignItems: 'center',
                  justifyContent: 'center',
                  borderWidth: current ? 3 : 1,
                  borderColor: current ? colors.focus : colors.border,
                  backgroundColor: done ? colors.surfaceAlt : colors.surface,
                }}
              >
                <AppText variant="small" weight={done ? '700' : '400'}>
                  {i + 1}
                </AppText>
              </Pressable>
            );
          })}
        </View>
      </Card>
      <Button title="제출하고 채점" onPress={confirmSubmit} />
    </Screen>
  );
}
