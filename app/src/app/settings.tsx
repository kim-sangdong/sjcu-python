import { useRouter } from 'expo-router';
import * as WebBrowser from 'expo-web-browser';
import { useState } from 'react';
import { Alert, Linking, Switch, View } from 'react-native';

import { DISCLAIMER_LAW, DISCLAIMER_UNOFFICIAL } from '@/core/copy';
import { ENV } from '@/core/env';
import { examInfo, subjectName } from '@/core/exam';
import { dotDate, kstDateKey } from '@/core/time';
import { openManageSubscriptions, purchasesConfigured, restorePurchases } from '@/data/purchases';
import { APP_VERSION, currentUserId } from '@/data/telemetry';
import { useApp } from '@/state/AppProvider';
import { FONT_SCALES, type ThemePref } from '@/state/settings';
import { AppText, Button, Card, Chip, Notice, Row, Screen } from '@/ui/primitives';
import { SPACE } from '@/ui/theme';

const THEMES: { value: ThemePref; label: string }[] = [
  { value: 'system', label: '기기 설정' },
  { value: 'light', label: '밝게' },
  { value: 'dark', label: '어둡게' },
];

async function openUrl(url: string | null, what: string) {
  if (!url) {
    Alert.alert(`${what} 준비 중`, '링크가 아직 설정되지 않았습니다.');
    return;
  }
  try {
    await WebBrowser.openBrowserAsync(url);
  } catch {
    await Linking.openURL(url);
  }
}

export default function SettingsScreen() {
  const router = useRouter();
  const app = useApp();
  const { colors, settings, updateSettings, pack, premium, isPremium } = app;
  const [restoring, setRestoring] = useState(false);
  const exam = examInfo(pack.manifest);

  const toggleWeak = (code: string) => {
    const set = new Set(settings.weakSubjects);
    if (set.has(code)) set.delete(code);
    else set.add(code);
    void updateSettings({ weakSubjects: [...set] });
  };

  const restore = async () => {
    if (!purchasesConfigured()) {
      Alert.alert('결제 설정 전', '이 빌드에는 결제 키가 없어 구매 복원을 할 수 없습니다.');
      return;
    }
    setRestoring(true);
    const r = await restorePurchases();
    setRestoring(false);
    if (r.ok) {
      app.applyCustomerInfo(r.info);
      const active = Boolean(r.info.entitlements.active.premium);
      Alert.alert(active ? '이용권을 복원했습니다' : '복원할 이용권이 없습니다', active ? '유료 기능을 쓸 수 있습니다.' : '같은 스토어 계정으로 구매했는지 확인해 주세요.');
    } else Alert.alert('복원하지 못했습니다', r.message);
  };

  const confirmReset = () => {
    Alert.alert(
      '학습 기록을 모두 지울까요?',
      '응답 이력, 오답노트, 복습 일정, 모의고사 기록, 설정이 이 기기에서 지워집니다. 이용권은 지워지지 않으며 "구매 복원"으로 되살릴 수 있습니다.',
      [
        { text: '취소', style: 'cancel' },
        {
          text: '모두 지우기',
          style: 'destructive',
          onPress: () => {
            void app.resetAll().then(() => router.replace('/'));
          },
        },
      ],
    );
  };

  return (
    <Screen>
      <Card>
        <AppText variant="heading">화면</AppText>
        <AppText variant="small" color={colors.textMuted}>
          글자 크기
        </AppText>
        <Row>
          {FONT_SCALES.map((f) => (
            <Chip
              key={f.value}
              label={f.label}
              selected={settings.fontScale === f.value}
              onPress={() => void updateSettings({ fontScale: f.value })}
            />
          ))}
        </Row>
        <AppText variant="small" color={colors.textMuted}>
          화면 모드
        </AppText>
        <Row>
          {THEMES.map((t) => (
            <Chip key={t.value} label={t.label} selected={settings.theme === t.value} onPress={() => void updateSettings({ theme: t.value })} />
          ))}
        </Row>
        <Row style={{ justifyContent: 'space-between' }}>
          <AppText style={{ flex: 1 }}>고대비(글자와 테두리를 더 진하게)</AppText>
          <Switch
            value={settings.highContrast}
            onValueChange={(v) => void updateSettings({ highContrast: v })}
            accessibilityLabel="고대비 모드"
            trackColor={{ true: colors.primary, false: colors.border }}
          />
        </Row>
      </Card>

      <Card>
        <AppText variant="heading">시험</AppText>
        <Row>
          <Chip
            label={`${exam.round}회 (${dotDate(exam.next_exam_date)})`}
            selected={settings.targetRound === null || settings.targetRound === exam.round}
            onPress={() => void updateSettings({ targetRound: null })}
          />
          <Chip
            label={`${exam.round + 1}회 (일정 공고 전)`}
            selected={settings.targetRound === exam.round + 1}
            onPress={() => void updateSettings({ targetRound: exam.round + 1 })}
          />
        </Row>
        <AppText variant="small" color={colors.textMuted}>
          약한 과목(오늘의 문항에 더 자주 나옵니다)
        </AppText>
        <Row>
          {pack.manifest.subjects.map((s) => (
            <Chip
              key={s.code}
              label={subjectName(pack.manifest, s.code)}
              selected={settings.weakSubjects.includes(s.code)}
              onPress={() => toggleWeak(s.code)}
            />
          ))}
        </Row>
      </Card>

      <Card>
        <AppText variant="heading">이용권</AppText>
        <AppText color={colors.textMuted}>
          {isPremium
            ? premium.expiresAt
              ? `이용 중 · ${premium.willRenew ? '다음 갱신일' : '이용 종료일'} ${dotDate(kstDateKey(premium.expiresAt))}`
              : '이용 중'
            : '무료 이용 중'}
        </AppText>
        {!isPremium ? (
          <Button title="이용권 살펴보기" onPress={() => router.push({ pathname: '/paywall', params: { from: 'settings' } })} />
        ) : null}
        <Button title="구독 관리·해지" variant="secondary" onPress={() => void openManageSubscriptions()} />
        <Button title="구매 복원" variant="secondary" loading={restoring} onPress={() => void restore()} />
        {__DEV__ && !purchasesConfigured() ? (
          <Row style={{ justifyContent: 'space-between' }}>
            <AppText variant="small" style={{ flex: 1 }}>
              (개발 빌드 전용) 유료 기능 미리보기
            </AppText>
            <Switch value={settings.devPremium} onValueChange={(v) => void updateSettings({ devPremium: v })} />
          </Row>
        ) : null}
      </Card>

      <Card>
        <AppText variant="heading">안내</AppText>
        <Button title="개인정보처리방침" variant="ghost" onPress={() => void openUrl(ENV.privacyUrl, '개인정보처리방침')} />
        <Button title="이용약관" variant="ghost" onPress={() => void openUrl(ENV.termsUrl, '이용약관')} />
        {ENV.supportEmail ? (
          <Button
            title="문의하기"
            variant="ghost"
            onPress={() =>
              void Linking.openURL(
                `mailto:${ENV.supportEmail}?subject=${encodeURIComponent('함정노트 문의')}&body=${encodeURIComponent(
                  `\n\n---\n앱 ${APP_VERSION} · 문항 팩 ${pack.manifest.version} · 지원 ID ${currentUserId() ?? '-'}`,
                )}`,
              )
            }
          />
        ) : null}
        <Notice text={DISCLAIMER_UNOFFICIAL} />
        <Notice text={DISCLAIMER_LAW} />
        <AppText variant="small" color={colors.textMuted}>
          이 앱은 광고 SDK를 쓰지 않으며, 이름·연락처를 받지 않습니다. 학습 기록은 기기에 저장되고, 문항 개선을 위해 익명 ID와
          함께 정답 여부·화면 이용 기록만 보냅니다.
        </AppText>
      </Card>

      <Card>
        <AppText variant="heading">데이터</AppText>
        <Button title="학습 기록 초기화" variant="danger" onPress={confirmReset} />
        <View style={{ gap: SPACE.xs }}>
          <AppText variant="small" color={colors.textMuted}>
            앱 {APP_VERSION} · 문항 팩 {pack.manifest.version} ({pack.items.length}문항)
          </AppText>
          <AppText variant="small" color={colors.textMuted} selectable>
            지원 ID {currentUserId() ?? '-'}
          </AppText>
        </View>
      </Card>
    </Screen>
  );
}
