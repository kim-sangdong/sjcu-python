/**
 * 첫 실행 온보딩: 소개·비공식 고지 → 시험 회차·D-day → 약한 과목 → (선택) 어디서 알게 되었는지.
 * 마치면 settings.onboarded=true 가 되어 (tabs) 로 넘어간다(루트의 Stack.Protected).
 */
import { useState } from 'react';
import { View } from 'react-native';

import { DISCLAIMER_UNOFFICIAL } from '@/core/copy';
import { ddayLabel, examInfo, subjectName } from '@/core/exam';
import { dotDateWithDay } from '@/core/time';
import { recordSurveySource } from '@/data/attribution';
import { useApp } from '@/state/AppProvider';
import { AppText, Button, Card, Chip, Notice, Row, Screen } from '@/ui/primitives';
import { SPACE } from '@/ui/theme';

/** 값은 채널 UTM 규칙(utm_source)과 맞춘다 */
const SOURCES = [
  { code: 'yt_shorts', label: '유튜브 쇼츠' },
  { code: 'yt_long', label: '유튜브 영상' },
  { code: 'ig', label: '인스타그램' },
  { code: 'fb', label: '페이스북' },
  { code: 'tiktok', label: '틱톡' },
  { code: 'tistory', label: '블로그' },
  { code: 'search', label: '스토어 검색' },
  { code: 'friend', label: '지인 추천' },
  { code: 'other', label: '기타' },
];

export default function Onboarding() {
  const { colors, pack, updateSettings, settings, freeLimit } = useApp();
  const exam = examInfo(pack.manifest);
  const [step, setStep] = useState(0);
  const [round, setRound] = useState<number>(settings.targetRound ?? exam.round);
  const [weak, setWeak] = useState<string[]>(settings.weakSubjects);
  const [source, setSource] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const finish = async () => {
    setSaving(true);
    if (source) void recordSurveySource(source);
    await updateSettings({
      onboarded: true,
      targetRound: round === exam.round ? null : round,
      weakSubjects: weak,
    });
  };

  const stepLabel = `${step + 1}/4 단계`;
  const now = Date.now();

  return (
    <Screen edges={['top', 'bottom', 'left', 'right']}>
      <AppText variant="small" color={colors.textMuted} accessibilityLabel={`온보딩 ${stepLabel}`}>
        {stepLabel}
      </AppText>

      {step === 0 ? (
        <>
          <AppText variant="title">함정노트</AppText>
          <AppText>
            {exam.name} 시험에 자주 나오는 함정 지문을 OX·빈칸·계산 문제로 풉니다. 모든 문항에 근거 조문과 시행일이 붙어 있습니다.
          </AppText>
          <Card>
            <AppText>· 무료: 하루 {freeLimit}문항, 영상 연동 문항, D-day, 시험 당일 가채점</AppText>
            <AppText>· 이용권: 문항 무제한, 오답 복습, 약점 진단, 숫자 카드, 모의고사</AppText>
          </Card>
          <Notice text={DISCLAIMER_UNOFFICIAL} />
          <Button title="시작하기" onPress={() => setStep(1)} />
        </>
      ) : null}

      {step === 1 ? (
        <>
          <AppText variant="heading">어느 시험을 준비하나요?</AppText>
          <View style={{ gap: SPACE.s }}>
            <Chip
              label={`${exam.round}회 · ${dotDateWithDay(exam.next_exam_date)} · ${ddayLabel(exam.next_exam_date, now)}`}
              selected={round === exam.round}
              onPress={() => setRound(exam.round)}
            />
            <Chip label={`${exam.round + 1}회 · 일정 공고 전`} selected={round === exam.round + 1} onPress={() => setRound(exam.round + 1)} />
          </View>
          <AppText variant="small" color={colors.textMuted}>
            시험일은 시행기관 공고를 기준으로 하며, 설정에서 언제든 바꿀 수 있습니다.
          </AppText>
          <Row>
            <Button title="이전" variant="secondary" onPress={() => setStep(0)} style={{ flex: 1 }} />
            <Button title="다음" onPress={() => setStep(2)} style={{ flex: 1 }} />
          </Row>
        </>
      ) : null}

      {step === 2 ? (
        <>
          <AppText variant="heading">약한 과목을 골라 주세요</AppText>
          <AppText color={colors.textMuted}>고른 과목이 오늘의 문항에 더 자주 나옵니다. 건너뛰어도 됩니다.</AppText>
          <Row>
            {pack.manifest.subjects.map((s) => (
              <Chip
                key={s.code}
                label={subjectName(pack.manifest, s.code)}
                selected={weak.includes(s.code)}
                onPress={() => setWeak((w) => (w.includes(s.code) ? w.filter((x) => x !== s.code) : [...w, s.code]))}
              />
            ))}
          </Row>
          <Row>
            <Button title="이전" variant="secondary" onPress={() => setStep(1)} style={{ flex: 1 }} />
            <Button title="다음" onPress={() => setStep(3)} style={{ flex: 1 }} />
          </Row>
        </>
      ) : null}

      {step === 3 ? (
        <>
          <AppText variant="heading">함정노트를 어디서 알게 되셨나요?</AppText>
          <AppText color={colors.textMuted}>선택 사항입니다. 어떤 영상이 도움이 되는지 알아보는 데만 씁니다.</AppText>
          <Row>
            {SOURCES.map((s) => (
              <Chip key={s.code} label={s.label} selected={source === s.code} onPress={() => setSource(source === s.code ? null : s.code)} />
            ))}
          </Row>
          <Row>
            <Button title="이전" variant="secondary" onPress={() => setStep(2)} style={{ flex: 1 }} />
            <Button title={source ? '완료' : '건너뛰고 완료'} loading={saving} onPress={() => void finish()} style={{ flex: 1 }} />
          </Row>
        </>
      ) : null}
    </Screen>
  );
}
