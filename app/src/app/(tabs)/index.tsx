import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { View } from 'react-native';

import { collectAmendments } from '@/core/amendments';
import { DISCLAIMER_UNOFFICIAL } from '@/core/copy';
import { ddayLabel, examInfo, subjectName } from '@/core/exam';
import { gradingStatus } from '@/core/provisional';
import { displayStreak } from '@/core/quota';
import { progressBySubject } from '@/core/stats';
import { dotDateWithDay } from '@/core/time';
import { getAnsweredIds } from '@/data/repo';
import { useApp } from '@/state/AppProvider';
import { AppText, Button, Card, Notice, ProgressBar, Row, Screen } from '@/ui/primitives';
import { SPACE } from '@/ui/theme';

export default function TodayScreen() {
  const router = useRouter();
  const app = useApp();
  const { colors, pack, isPremium, freeRemaining, freeLimit, streak, dueCount, examDate, settings } = app;
  const [answered, setAnswered] = useState<Set<string>>(new Set());

  useFocusEffect(
    useCallback(() => {
      void getAnsweredIds().then(setAnswered);
    }, []),
  );

  const now = Date.now();
  const exam = examInfo(pack.manifest);
  const round = settings.targetRound ?? exam.round;
  const streakDays = displayStreak(streak, now);
  const progress = progressBySubject(answered, pack.items);
  const grading = gradingStatus(now, examDate, pack.manifest.extra?.grading, round);
  const amendments = collectAmendments(pack).slice(0, 3);
  const hasDecks = Boolean(pack.manifest.decks && Object.keys(pack.manifest.decks).length);
  const used = freeLimit - (Number.isFinite(freeRemaining) ? freeRemaining : freeLimit);

  return (
    <Screen>
      <Card>
        <AppText variant="small" color={colors.textMuted}>
          {round}회 {exam.name} 시험
        </AppText>
        <Row style={{ justifyContent: 'space-between' }}>
          <AppText variant="title" color={colors.primary} accessibilityLabel={`시험까지 ${ddayLabel(examDate, now)}`}>
            {ddayLabel(examDate, now)}
          </AppText>
          <AppText color={colors.textMuted}>{examDate ? dotDateWithDay(examDate) : '일정 공고 전'}</AppText>
        </Row>
        <AppText variant="small" color={colors.textMuted}>
          {streakDays > 0 ? `${streakDays}일째 연속 학습 중 · 최고 ${streak.best}일` : '오늘 첫 문항을 풀면 연속 학습이 시작됩니다'}
        </AppText>
      </Card>

      {app.packMessage ? <Notice text={app.packMessage} /> : null}

      <Card>
        <AppText variant="heading">오늘의 문항</AppText>
        {isPremium ? (
          <AppText color={colors.textMuted}>
            무제한 이용 중{dueCount ? ` · 복습할 문항 ${dueCount}개가 먼저 나옵니다` : ''}
          </AppText>
        ) : (
          <>
            <AppText color={colors.textMuted}>
              무료로 하루 {freeLimit}문항 · 오늘 남은 문항 {freeRemaining}개
            </AppText>
            <ProgressBar value={freeLimit ? used / freeLimit : 1} label={`오늘 ${used}문항 풀이, ${freeRemaining}문항 남음`} />
          </>
        )}
        {freeRemaining > 0 ? (
          <Button title="오늘의 문항 풀기" onPress={() => router.push({ pathname: '/quiz', params: { mode: 'daily' } })} />
        ) : (
          <>
            <AppText>오늘 무료 문항을 모두 풀었습니다. 내일 0시(한국 시간)에 다시 {freeLimit}문항이 열립니다.</AppText>
            <Button
              title="제한 없이 더 풀기"
              variant="secondary"
              onPress={() => router.push({ pathname: '/paywall', params: { from: 'daily_limit' } })}
            />
          </>
        )}
      </Card>

      {grading !== 'before' ? (
        <Card>
          <AppText variant="heading">가채점</AppText>
          <AppText color={colors.textMuted}>
            {grading === 'open' ? '공개된 정답 번호로 내 답안을 채점해 보세요.' : '정답 번호가 공개되기 전입니다. 내 답안을 먼저 적어 두세요.'}
          </AppText>
          <Button title="가채점 열기" variant="secondary" onPress={() => router.push('/grading')} />
        </Card>
      ) : null}

      {hasDecks ? (
        <Card>
          <AppText variant="heading">영상에 나온 문항</AppText>
          <AppText color={colors.textMuted}>이번 주 쇼츠에 쓰인 지문을 그대로 풀어 봅니다.</AppText>
          <Button title="영상 문항 보기" variant="secondary" onPress={() => router.push('/decks')} />
        </Card>
      ) : null}

      {amendments.length ? (
        <Card>
          <AppText variant="heading">법령 개정 알림</AppText>
          {amendments.map((a) => (
            <AppText key={a.id} color={colors.textMuted}>
              · {a.title}
            </AppText>
          ))}
          <Button title="전체 보기" variant="ghost" onPress={() => router.push('/amendments')} />
        </Card>
      ) : null}

      <Card>
        <AppText variant="heading">과목 진도</AppText>
        {pack.manifest.subjects.map((s) => {
          const p = progress.get(s.code) ?? { done: 0, total: 0 };
          return (
            <View key={s.code} style={{ gap: SPACE.xs }}>
              <Row style={{ justifyContent: 'space-between' }}>
                <AppText variant="small">{subjectName(pack.manifest, s.code)}</AppText>
                <AppText variant="small" color={colors.textMuted}>
                  {p.done}/{p.total}
                </AppText>
              </Row>
              <ProgressBar value={p.total ? p.done / p.total : 0} label={`${s.name} ${p.done}/${p.total} 문항`} />
            </View>
          );
        })}
      </Card>

      <AppText variant="small" color={colors.textMuted}>
        {pack.manifest.disclaimer?.trim() || DISCLAIMER_UNOFFICIAL}
      </AppText>
    </Screen>
  );
}
