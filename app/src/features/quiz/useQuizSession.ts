/**
 * 학습 묶음 진행: 제출 → 채점 → 기록(SQLite 응답 이력 + SRS) → answers_batch 버퍼 → 다음.
 * 무료 한도를 쓰는 묶음(오늘의 문항)은 제출할 때 한도를 소비한다.
 */
import { useCallback, useRef, useState } from 'react';

import { isCorrect, type Response } from '@/core/grading';
import type { Item } from '@/core/pack/types';
import { newCard, review, type Grade, type SrsCard } from '@/core/srs';
import { getCard, recordAnswer, saveCard } from '@/data/repo';
import { trackAnswer } from '@/data/telemetry';
import { useApp } from '@/state/AppProvider';

export type QuizMode = 'daily' | 'review' | 'wrong' | 'deck' | 'subject';

export interface QuizResult {
  correct: boolean;
  unsure: boolean;
}

export function useQuizSession(items: Item[], mode: QuizMode) {
  const { consumeQuota, markStudied, daysLeft, pack, refreshDue } = useApp();
  const [index, setIndex] = useState(0);
  const [response, setResponse] = useState<Response | null>(null);
  const [result, setResult] = useState<QuizResult | null>(null);
  const [score, setScore] = useState({ answered: 0, correct: 0, wrongIds: [] as string[] });
  const [blocked, setBlocked] = useState(false);
  const [busy, setBusy] = useState(false);
  const prevCard = useRef<SrsCard | null>(null);

  const item = items[index] ?? null;
  const finished = index >= items.length;
  const consumes = mode === 'daily' || mode === 'subject';

  const submit = useCallback(
    async (r: Response) => {
      if (!item || result || busy) return;
      setBusy(true);
      try {
        if (consumes && !(await consumeQuota())) {
          setBlocked(true);
          return;
        }
        const now = Date.now();
        const correct = isCorrect(item, r);
        const prev = (await getCard(item.id)) ?? newCard(item.id, now);
        prevCard.current = prev;
        const grade: Grade = correct ? 'right' : 'wrong';
        await saveCard(review(prev, grade, now, { daysToExam: daysLeft }));
        await recordAnswer({ itemId: item.id, correct, mode, packVersion: pack.manifest.version, at: now });
        void trackAnswer({ id: item.id, correct });
        void markStudied();
        setResponse(r);
        setResult({ correct, unsure: false });
        setScore((s) => ({
          answered: s.answered + 1,
          correct: s.correct + (correct ? 1 : 0),
          wrongIds: correct ? s.wrongIds : [...s.wrongIds, item.id],
        }));
      } finally {
        setBusy(false);
      }
    },
    [item, result, busy, consumes, consumeQuota, daysLeft, mode, pack.manifest.version, markStudied],
  );

  /** 맞혔지만 헷갈렸다 → SRS 를 'unsure' 로 다시 계산(간격을 짧게) */
  const markUnsure = useCallback(async () => {
    if (!item || !result?.correct || result.unsure || !prevCard.current) return;
    await saveCard(review(prevCard.current, 'unsure', Date.now(), { daysToExam: daysLeft }));
    setResult({ ...result, unsure: true });
  }, [item, result, daysLeft]);

  const next = useCallback(() => {
    setResponse(null);
    setResult(null);
    prevCard.current = null;
    setIndex((i) => {
      const n = i + 1;
      if (n >= items.length) void refreshDue();
      return n;
    });
  }, [items.length, refreshDue]);

  return { item, index, total: items.length, response, result, score, finished, blocked, busy, submit, markUnsure, next };
}
