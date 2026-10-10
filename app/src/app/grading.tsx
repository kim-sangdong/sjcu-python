/**
 * 시험 당일 가채점(무료). 정답 번호는 매니페스트 extra.grading 으로 받는다(문항 원문은 쓰지 않음).
 * 정답 공개 전에는 내 답안만 적어 두고, 공개되면 같은 화면에서 바로 채점한다.
 */
import { useFocusEffect } from 'expo-router';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Pressable, TextInput, View } from 'react-native';

import { GRADING_NOTICE } from '@/core/copy';
import { examInfo, examPapers, examRules, paperCode } from '@/core/exam';
import type { GradingSession, GradingSubject } from '@/core/pack/types';
import { decodePicks, encodePicks, gradeSheet, gradingStatus, keyLabel } from '@/core/provisional';
import { dotDateWithDay } from '@/core/time';
import { getKV, setKV } from '@/data/repo';
import { track } from '@/data/telemetry';
import { useApp } from '@/state/AppProvider';
import { AppText, Button, Card, Chip, Notice, Row, Screen } from '@/ui/primitives';
import { SPACE } from '@/ui/theme';

interface Sheet {
  form: string;
  picks: string;
}

/**
 * 정답이 아직 없을 때 쓰는 기본 틀: 매니페스트 exam.papers(없으면 기본 5과목).
 * 과목 코드는 papers[].subjects 를 '+' 로 이은 값 — extra.grading 의 subjects[].code 도 같은 규칙을 쓴다.
 */
function defaultSessions(papers: ReturnType<typeof examPapers>): GradingSession[] {
  const stages = [...new Set(papers.map((p, i) => p.stage ?? (i < 2 ? 1 : 2)))].sort();
  return stages.map((stage) => ({
    code: `stage${stage}`,
    title: `${stage}차`,
    subjects: papers
      .filter((p, i) => (p.stage ?? (i < 2 ? 1 : 2)) === stage)
      .map((p) => ({ code: paperCode(p), name: p.name, questions: p.questions, answers: {} })),
  }));
}

function SubjectSheet({
  subject,
  round,
  open,
  failLine,
}: {
  subject: GradingSubject;
  round: number;
  open: boolean;
  failLine: number;
}) {
  const { colors, fontScale } = useApp();
  const storageKey = `grading:${round}:${subject.code}`;
  const forms = Object.keys(subject.answers).length ? Object.keys(subject.answers) : ['A', 'B'];
  const [sheet, setSheet] = useState<Sheet>({ form: forms[0]!, picks: '' });
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    void getKV<Sheet | null>(storageKey, null).then((s) => {
      if (s) setSheet(s);
    });
  }, [storageKey]);

  const picks = decodePicks(sheet.picks, subject.questions);
  const save = (next: Sheet) => {
    setSheet(next);
    void setKV(storageKey, next);
  };
  const setPick = (i: number, v: number) => {
    const p = picks.slice();
    p[i] = p[i] === v ? null : v;
    save({ ...sheet, picks: encodePicks(p).replace(/0+$/, '') });
  };
  const result = open ? gradeSheet(subject, sheet.form, picks) : null;
  const filled = picks.filter((p) => p !== null).length;

  return (
    <Card>
      <AppText variant="heading">{subject.name}</AppText>
      <Row>
        <AppText variant="small" color={colors.textMuted}>
          문제지 형별
        </AppText>
        {forms.map((f) => (
          <Chip key={f} label={`${f}형`} selected={sheet.form === f} onPress={() => save({ ...sheet, form: f })} />
        ))}
      </Row>
      <TextInput
        value={sheet.picks}
        onChangeText={(t) => save({ ...sheet, picks: t.replace(/[^0-5]/g, '').slice(0, subject.questions) })}
        keyboardType="number-pad"
        placeholder="답안 번호를 이어서 입력(예: 31425…, 빈 문항은 0)"
        placeholderTextColor={colors.textMuted}
        accessibilityLabel={`${subject.name} 답안 번호 한꺼번에 입력`}
        maxLength={subject.questions}
        style={{
          minHeight: 48,
          borderWidth: 1.5,
          borderColor: colors.border,
          borderRadius: 10,
          paddingHorizontal: SPACE.m,
          color: colors.text,
          backgroundColor: colors.surface,
          fontSize: Math.round(17 * fontScale),
          letterSpacing: 2,
        }}
      />
      <AppText variant="small" color={colors.textMuted}>
        입력한 문항 {filled}/{subject.questions}
      </AppText>
      {result ? (
        <View style={{ gap: SPACE.xs }}>
          <AppText weight="700" color={result.score100 < failLine ? colors.wrong : colors.primary}>
            {result.correct}/{result.total} 정답 · {result.score100}점
          </AppText>
          {result.score100 < failLine ? (
            <AppText variant="small" color={colors.wrong}>
              과락 기준({failLine}점)보다 낮습니다.
            </AppText>
          ) : null}
        </View>
      ) : null}
      <Button
        title={expanded ? '문항별 입력 접기' : '문항별로 입력·확인'}
        variant="ghost"
        onPress={() => setExpanded((v) => !v)}
      />
      {expanded
        ? picks.map((p, i) => {
            const mark = result?.marks[i];
            return (
              <View key={i} style={{ flexDirection: 'row', alignItems: 'center', gap: SPACE.xs }}>
                <AppText
                  variant="small"
                  weight="700"
                  style={{ width: 44 }}
                  color={mark === 'correct' ? colors.correct : mark === 'wrong' ? colors.wrong : colors.text}
                >
                  {i + 1}
                  {mark === 'correct' ? ' O' : mark === 'wrong' ? ' X' : ''}
                </AppText>
                {[1, 2, 3, 4, 5].map((v) => (
                  <Pressable
                    key={v}
                    onPress={() => setPick(i, v)}
                    accessibilityRole="radio"
                    accessibilityLabel={`${i + 1}번 문항 ${v}번`}
                    accessibilityState={{ checked: p === v }}
                    style={{
                      width: 44,
                      height: 44,
                      borderRadius: 22,
                      alignItems: 'center',
                      justifyContent: 'center',
                      borderWidth: 1.5,
                      borderColor: p === v ? colors.primary : colors.border,
                      backgroundColor: p === v ? colors.primary : colors.surface,
                    }}
                  >
                    <AppText variant="small" color={p === v ? colors.onPrimary : colors.text}>
                      {v}
                    </AppText>
                  </Pressable>
                ))}
              </View>
            );
          })
        : null}
    </Card>
  );
}

export default function GradingScreen() {
  const { colors, pack, examDate, settings } = useApp();
  const exam = examInfo(pack.manifest);
  const round = settings.targetRound ?? exam.round;
  const key = pack.manifest.extra?.grading;
  const rules = examRules(pack.manifest);
  const status = gradingStatus(Date.now(), examDate, key, round);
  const sessions = useMemo(
    () => (status === 'open' && key ? key.sessions : defaultSessions(examPapers(pack.manifest))),
    [status, key, pack.manifest],
  );
  const [sessionCode, setSessionCode] = useState<string | null>(null);
  const current = sessions.find((s) => s.code === sessionCode) ?? sessions[0];

  useFocusEffect(
    useCallback(() => {
      void track('grading_open', { status, round });
    }, [status, round]),
  );

  if (status === 'before') {
    return (
      <Screen>
        <Card>
          <AppText variant="heading">가채점은 시험 당일에 열립니다</AppText>
          <AppText color={colors.textMuted}>
            {round}회 시험일 {examDate ? dotDateWithDay(examDate) : '(공고 전)'}부터 내 답안을 적고, 정답 번호가 공개되면 바로
            채점할 수 있습니다.
          </AppText>
        </Card>
        <Notice text={GRADING_NOTICE} />
      </Screen>
    );
  }

  return (
    <Screen>
      <Card>
        <AppText variant="heading">{round}회 가채점</AppText>
        <AppText color={status === 'open' ? colors.primary : colors.textMuted} weight="700">
          {status === 'open' && key ? keyLabel(key) : '정답 번호 공개 전 — 답안만 저장됩니다'}
        </AppText>
        {status === 'open' && key ? (
          <AppText variant="small" color={colors.textMuted}>
            {key.source_note}
          </AppText>
        ) : null}
      </Card>
      <Row>
        {sessions.map((s) => (
          <Chip key={s.code} label={s.title} selected={current?.code === s.code} onPress={() => setSessionCode(s.code)} />
        ))}
      </Row>
      {current?.subjects.map((sub) => (
        <SubjectSheet key={`${current.code}-${sub.code}`} subject={sub} round={round} open={status === 'open'} failLine={rules.fail_line} />
      ))}
      <Notice text={GRADING_NOTICE} />
    </Screen>
  );
}
