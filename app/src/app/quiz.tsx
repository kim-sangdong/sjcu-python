/**
 * 문제 풀이. 파라미터:
 *   mode=daily            오늘의 문항(무료 한도 적용)
 *   mode=review           SRS 복습(유료)
 *   mode=wrong            오답노트 다시 풀기(유료)
 *   mode=deck&deck=<id>   영상 연동 덱(무료)
 *   mode=subject&subject=<code>  과목별(유료: 전체, 무료: 무료 풀만 — 한도 적용)
 */
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect, useMemo, useState } from 'react';
import { View } from 'react-native';

import { examInfo } from '@/core/exam';
import type { Item } from '@/core/pack/types';
import { deckItemIds } from '@/core/pack/validate';
import { mulberry32, seedFromString } from '@/core/random';
import { buildDailySession, itemsByIds } from '@/core/session';
import { freePool } from '@/core/pack/sync';
import { kstDateKey } from '@/core/time';
import { getAnsweredIds, getDueIds, getWrongCards, getWrongIdSet } from '@/data/repo';
import { QuestionCard } from '@/features/quiz/QuestionCard';
import { useQuizSession, type QuizMode } from '@/features/quiz/useQuizSession';
import { useApp } from '@/state/AppProvider';
import { AppText, Button, Card, Loading, ProgressBar, Row, Screen } from '@/ui/primitives';
import { SPACE } from '@/ui/theme';

const MODES: QuizMode[] = ['daily', 'review', 'wrong', 'deck', 'subject'];
const SESSION_SIZE = 20;

export default function QuizScreen() {
  const params = useLocalSearchParams<{ mode?: string; deck?: string; subject?: string }>();
  const mode: QuizMode = MODES.includes(params.mode as QuizMode) ? (params.mode as QuizMode) : 'daily';
  const app = useApp();
  const { pack, isPremium, freeRemaining, settings } = app;
  const [items, setItems] = useState<Item[] | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      const now = Date.now();
      const rand = mulberry32(seedFromString(`${kstDateKey(now)}:${mode}:${now}`));
      let list: Item[] = [];
      if (mode === 'deck') {
        const ids = deckItemIds(pack.manifest.decks?.[params.deck ?? '']) ?? [];
        list = itemsByIds(ids, pack.byId);
      } else if (mode === 'review') {
        list = isPremium ? itemsByIds(await getDueIds(now, SESSION_SIZE * 2), pack.byId).slice(0, SESSION_SIZE) : [];
      } else if (mode === 'wrong') {
        list = isPremium ? itemsByIds((await getWrongCards(SESSION_SIZE)).map((c) => c.itemId), pack.byId) : [];
      } else {
        const pool = isPremium ? pack.items : freePool(pack);
        const scoped = mode === 'subject' && params.subject ? pool.filter((it) => it.subject === params.subject) : pool;
        const size = isPremium ? SESSION_SIZE : Math.min(SESSION_SIZE, freeRemaining);
        list = buildDailySession({
          pool: scoped,
          answered: await getAnsweredIds(),
          wrong: await getWrongIdSet(),
          due: isPremium ? await getDueIds(now) : [],
          weakSubjects: settings.weakSubjects,
          size,
          rand,
        });
      }
      if (alive) setItems(list);
    })();
    return () => {
      alive = false;
    };
    // 묶음은 화면에 들어올 때 한 번만 만든다
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!items) {
    return (
      <Screen>
        <Loading />
      </Screen>
    );
  }
  return <QuizRunner items={items} mode={mode} />;
}

function QuizRunner({ items, mode }: { items: Item[]; mode: QuizMode }) {
  const router = useRouter();
  const { colors, isPremium, pack } = useApp();
  const s = useQuizSession(items, mode);
  const examName = examInfo(pack.manifest).name;
  const title = useMemo(
    () =>
      ({ daily: '오늘의 문항', review: '복습', wrong: '오답노트', deck: '영상 문항', subject: '과목별 풀이' })[mode],
    [mode],
  );

  if (items.length === 0) {
    return (
      <Screen>
        <Stack.Screen options={{ title }} />
        <Card>
          <AppText variant="heading">풀 문항이 없습니다</AppText>
          <AppText color={colors.textMuted}>
            {mode === 'review'
              ? '지금 복습할 문항이 없습니다. 오늘의 문항을 풀면 틀린 문항이 알맞은 때에 다시 나옵니다.'
              : mode === 'deck'
                ? '이 영상의 문항을 찾지 못했습니다. 인터넷에 연결한 뒤 다시 열어 주세요.'
                : '조건에 맞는 문항이 없습니다.'}
          </AppText>
          <Button title="돌아가기" variant="secondary" onPress={() => router.back()} />
        </Card>
      </Screen>
    );
  }

  if (s.blocked) {
    return (
      <Screen>
        <Stack.Screen options={{ title }} />
        <Card>
          <AppText variant="heading">오늘 무료 문항을 모두 풀었습니다</AppText>
          <AppText color={colors.textMuted}>내일 0시(한국 시간)에 다시 열립니다. 이용권이 있으면 제한 없이 풀 수 있습니다.</AppText>
          <Button title="이용권 살펴보기" onPress={() => router.replace({ pathname: '/paywall', params: { from: 'daily_limit' } })} />
          <Button title="그만하기" variant="ghost" onPress={() => router.back()} />
        </Card>
      </Screen>
    );
  }

  if (s.finished || !s.item) {
    const rate = s.score.answered ? Math.round((s.score.correct / s.score.answered) * 100) : 0;
    return (
      <Screen>
        <Stack.Screen options={{ title }} />
        <Card>
          <AppText variant="heading">수고했습니다</AppText>
          <AppText accessibilityLabel={`${s.score.answered}문항 중 ${s.score.correct}문항 정답, 정답률 ${rate}퍼센트`}>
            {s.score.answered}문항 중 {s.score.correct}문항 정답 ({rate}%)
          </AppText>
          {s.score.wrongIds.length ? (
            <AppText color={colors.textMuted}>
              틀린 {s.score.wrongIds.length}문항은 오답노트에 저장했습니다.
              {isPremium ? ' 알맞은 때에 복습으로 다시 나옵니다.' : ' 오답 복습은 이용권으로 열립니다.'}
            </AppText>
          ) : null}
          <Button title="처음 화면으로" onPress={() => router.back()} />
        </Card>
        <AppText variant="small" color={colors.textMuted}>
          {examName} 학습용 자체 문항입니다.
        </AppText>
      </Screen>
    );
  }

  const header = (
    <View style={{ gap: SPACE.xs }}>
      <Row style={{ justifyContent: 'space-between' }}>
        <AppText variant="small" color={colors.textMuted}>
          {s.index + 1} / {s.total}
        </AppText>
        <AppText variant="small" color={colors.textMuted}>
          정답 {s.score.correct}
        </AppText>
      </Row>
      <ProgressBar value={s.index / s.total} label={`${s.total}문항 중 ${s.index + 1}번째`} />
    </View>
  );

  return (
    <Screen>
      <Stack.Screen options={{ title }} />
      <QuestionCard
        key={s.item.id}
        item={s.item}
        response={s.response}
        result={s.result}
        onSubmit={(r) => void s.submit(r)}
        header={header}
      />
      {s.result ? (
        <View style={{ gap: SPACE.s }}>
          {s.result.correct && !s.result.unsure ? (
            <Button
              title="맞혔지만 헷갈렸어요"
              variant="ghost"
              hint="복습 간격을 짧게 잡습니다"
              onPress={() => void s.markUnsure()}
            />
          ) : null}
          <Button title={s.index + 1 >= s.total ? '결과 보기' : '다음 문항'} onPress={s.next} />
        </View>
      ) : null}
    </Screen>
  );
}
