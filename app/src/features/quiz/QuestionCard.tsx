/**
 * 문제 한 개: 과목·단원 → 근거 배지 → 지문 → 입력 → (제출 뒤) 정답·해설·함정·근거 원문·신고.
 */
import React, { useState } from 'react';
import { Pressable, View } from 'react-native';

import { answerLabel, type Response } from '@/core/grading';
import { subjectName } from '@/core/exam';
import { TRAP_TYPES, type Item } from '@/core/pack/types';
import { dotDate } from '@/core/time';
import { useApp } from '@/state/AppProvider';
import { AppText, Card, Notice } from '@/ui/primitives';
import { SPACE } from '@/ui/theme';
import { SEED_NOTICE } from '@/core/copy';
import { BasisList } from './BasisBadge';
import { QuestionInput } from './QuestionInputs';
import { ReportSheet } from './ReportSheet';

const TYPE_LABEL: Record<Item['type'], string> = {
  ox: 'OX',
  mcq: '5지선다',
  blank: '빈칸',
  order: '순서 맞추기',
  calc: '계산',
};

export function QuestionCard({
  item,
  response,
  result,
  onSubmit,
  showFeedback = true,
  header,
}: {
  item: Item;
  response: Response | null;
  /** null = 아직 제출 전 */
  result: { correct: boolean } | null;
  onSubmit: (r: Response) => void;
  /** 모의고사처럼 정답을 바로 보이지 않을 때 false */
  showFeedback?: boolean;
  header?: React.ReactNode;
}) {
  const { colors, pack } = useApp();
  const [reportOpen, setReportOpen] = useState(false);
  const revealed = showFeedback && result !== null;
  const unverified = pack.manifest.extra?.unverified === true;

  return (
    <View style={{ gap: SPACE.m }}>
      {header}
      {unverified ? <Notice text={SEED_NOTICE} tone="warning" /> : null}
      <AppText variant="small" color={colors.textMuted}>
        {subjectName(pack.manifest, item.subject)} · {item.unit || '기타'} · {TYPE_LABEL[item.type]}
      </AppText>
      <BasisList basis={item.basis} quoteEnabled={revealed} />
      {(item.alerts ?? []).map((a) => (
        <Notice
          key={`${a.law_name}-${a.article}-${a.effective_date}`}
          tone="warning"
          text={`시험 전 개정 예정: ${a.law_name} ${a.article} (${dotDate(a.effective_date)} 시행). 시행일부터 기준이 바뀔 수 있습니다.`}
        />
      ))}
      <AppText variant="stem" weight="600" accessibilityRole="text">
        {item.stem}
      </AppText>
      {item.type === 'ox' || item.type === 'order' ? (
        <AppText variant="small" color={colors.textMuted}>
          {item.type === 'ox' ? '맞으면 O, 틀리면 X 를 누르세요.' : '↑ ↓ 버튼으로 순서를 바꾼 뒤 제출하세요.'}
        </AppText>
      ) : null}

      <QuestionInput item={item} response={response} revealed={revealed} onSubmit={onSubmit} />

      {revealed && result ? (
        <Card style={{ borderColor: result.correct ? colors.correct : colors.wrong, borderWidth: 2 }}>
          <AppText
            variant="heading"
            color={result.correct ? colors.correct : colors.wrong}
            accessibilityLiveRegion="polite"
          >
            {result.correct ? '정답입니다' : '오답입니다'}
          </AppText>
          <AppText weight="700">정답: {answerLabel(item)}</AppText>
          {item.steps?.length ? (
            <View style={{ gap: 2 }}>
              {item.steps.map((s, i) => (
                <AppText key={i} color={colors.textMuted}>
                  {i + 1}. {s}
                </AppText>
              ))}
            </View>
          ) : null}
          <AppText>{item.explanation}</AppText>
          {item.trap ? (
            (TRAP_TYPES as readonly string[]).includes(item.trap) ? (
              <AppText variant="small" weight="700" color={colors.warning}>
                함정 유형 · {item.trap}
              </AppText>
            ) : (
              <View style={{ gap: 2 }}>
                <AppText variant="small" weight="700" color={colors.warning}>
                  함정 포인트
                </AppText>
                <AppText variant="small">{item.trap}</AppText>
              </View>
            )
          ) : null}
          <Pressable
            onPress={() => setReportOpen(true)}
            accessibilityRole="button"
            accessibilityLabel="이 문항 신고하기"
            style={{ alignSelf: 'flex-end', minHeight: 44, justifyContent: 'center', paddingHorizontal: SPACE.s }}
          >
            <AppText variant="small" color={colors.textMuted} style={{ textDecorationLine: 'underline' }}>
              문항 신고
            </AppText>
          </Pressable>
        </Card>
      ) : null}
      <ReportSheet itemId={item.id} visible={reportOpen} onClose={() => setReportOpen(false)} />
    </View>
  );
}
