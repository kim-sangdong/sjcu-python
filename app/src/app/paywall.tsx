/**
 * 이용권 화면. 결제 버튼 바로 위에 상품마다 자동 갱신 가격·주기·해지 방법을 적는다.
 * 기본은 앱이 직접 그리는 화면(custom). EXPO_PUBLIC_PAYWALL_MODE=revenuecat 이면
 * RevenueCat 대시보드에서 만든 Paywall(react-native-purchases-ui)을 아래에 붙인다.
 */
import { useLocalSearchParams, useRouter } from 'expo-router';
import * as WebBrowser from 'expo-web-browser';
import { useCallback, useEffect, useState } from 'react';
import { Alert, Platform, View } from 'react-native';
import RevenueCatUI from 'react-native-purchases-ui';
import type { CustomerInfo, PurchasesOffering } from 'react-native-purchases';

import { ENTITLEMENT_ID, ENV } from '@/core/env';
import { COMMON_PURCHASE_NOTES, cancelHowTo, passPeriodLabel, planDisclosure, planTitle, periodLabel } from '@/core/offer';
import {
  buyPackage,
  getCurrentOffering,
  openManageSubscriptions,
  plansFromOffering,
  purchasesConfigured,
  restorePurchases,
  type PlanPackage,
} from '@/data/purchases';
import { track } from '@/data/telemetry';
import { useApp } from '@/state/AppProvider';
import { AppText, Button, Card, Loading, Notice, Screen } from '@/ui/primitives';
import { SPACE } from '@/ui/theme';

const PLATFORM: 'ios' | 'android' = Platform.OS === 'ios' ? 'ios' : 'android';

const FEATURES = [
  '문항 은행 무제한(모든 문항에 근거 조문)',
  '오답 간격 반복 복습과 오답노트',
  '과목·단원·함정 유형별 약점 진단',
  '숫자 암기 카드 전체',
  '법령 개정 전후 비교 상세',
  '자체 모의고사 40문항 · 예상 점수(참고용) · 과락 경고',
  '전체 문항 오프라인 이용',
];

function Links() {
  const open = (url: string | null) => {
    if (url) void WebBrowser.openBrowserAsync(url);
    else Alert.alert('링크 준비 중', '정책 링크가 아직 설정되지 않았습니다.');
  };
  return (
    <View style={{ flexDirection: 'row', gap: SPACE.s, flexWrap: 'wrap' }}>
      <Button title="이용약관" variant="ghost" onPress={() => open(ENV.termsUrl)} />
      <Button title="개인정보처리방침" variant="ghost" onPress={() => open(ENV.privacyUrl)} />
    </View>
  );
}

export default function PaywallScreen() {
  const { from } = useLocalSearchParams<{ from?: string }>();
  const router = useRouter();
  const { colors, isPremium, applyCustomerInfo, examDate } = useApp();
  const [offering, setOffering] = useState<PurchasesOffering | null>(null);
  const [plans, setPlans] = useState<PlanPackage[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const configured = purchasesConfigured();

  useEffect(() => {
    void track('paywall_view', { source: from ?? 'unknown', mode: ENV.paywallMode, configured });
  }, [from, configured]);

  const load = useCallback(async () => {
    if (!configured) {
      setPlans([]);
      return;
    }
    setError(null);
    setPlans(null);
    try {
      const o = await getCurrentOffering();
      setOffering(o);
      setPlans(plansFromOffering(o, Date.now(), examDate));
    } catch {
      setError('상품 정보를 불러오지 못했습니다. 인터넷 연결을 확인해 주세요.');
      setPlans([]);
    }
  }, [configured, examDate]);

  useEffect(() => {
    void load();
  }, [load]);

  const onPurchased = (info: CustomerInfo, productId: string | null) => {
    applyCustomerInfo(info);
    void track('purchase_success', { product_id: productId, source: from ?? 'unknown' });
    if (info.entitlements.active[ENTITLEMENT_ID]) {
      Alert.alert('이용권이 적용되었습니다', '이제 모든 기능을 쓸 수 있습니다.');
      router.back();
    }
  };

  const buy = async (p: PlanPackage) => {
    setBusy(p.pkg.identifier);
    const r = await buyPackage(p.pkg);
    setBusy(null);
    if (r.status === 'success') onPurchased(r.info, p.pkg.product.identifier);
    else if (r.status === 'pending') Alert.alert('결제 승인 대기 중', '결제가 승인되면 이용권이 자동으로 적용됩니다.');
    else if (r.status === 'error') Alert.alert('결제하지 못했습니다', r.message);
  };

  const restore = async () => {
    if (!configured) {
      Alert.alert('결제 설정 전', '이 빌드에는 결제 키가 없어 구매 복원을 할 수 없습니다.');
      return;
    }
    setBusy('restore');
    const r = await restorePurchases();
    setBusy(null);
    if (r.ok) {
      applyCustomerInfo(r.info);
      if (r.info.entitlements.active[ENTITLEMENT_ID]) {
        Alert.alert('이용권을 복원했습니다');
        router.back();
      } else Alert.alert('복원할 이용권이 없습니다', '같은 스토어 계정으로 구매했는지 확인해 주세요.');
    } else Alert.alert('복원하지 못했습니다', r.message);
  };

  if (isPremium) {
    return (
      <Screen>
        <Card>
          <AppText variant="heading">이용권 이용 중입니다</AppText>
          <AppText color={colors.textMuted}>{cancelHowTo(PLATFORM)}</AppText>
          <Button title="구독 관리·해지" variant="secondary" onPress={() => void openManageSubscriptions()} />
        </Card>
      </Screen>
    );
  }

  // RevenueCat 대시보드 Paywall 모드
  if (ENV.paywallMode === 'revenuecat' && configured) {
    return (
      <Screen scroll={false} contentStyle={{ padding: 0, gap: 0 }}>
        <View style={{ padding: SPACE.m, gap: SPACE.xs }}>
          <AppText variant="small" color={colors.textMuted}>
            자동 갱신 구독은 해지하지 않으면 같은 금액으로 계속 결제됩니다. {cancelHowTo(PLATFORM)}
          </AppText>
        </View>
        <RevenueCatUI.Paywall
          style={{ flex: 1 }}
          options={{ offering, displayCloseButton: false }}
          onPurchaseCompleted={({ customerInfo, storeTransaction }) => onPurchased(customerInfo, storeTransaction.productIdentifier)}
          onRestoreCompleted={({ customerInfo }) => applyCustomerInfo(customerInfo)}
          onDismiss={() => router.back()}
        />
        <View style={{ paddingHorizontal: SPACE.m }}>
          <Links />
        </View>
      </Screen>
    );
  }

  return (
    <Screen>
      <Card>
        <AppText variant="heading">이용권으로 열리는 기능</AppText>
        {FEATURES.map((f) => (
          <AppText key={f}>· {f}</AppText>
        ))}
        <AppText variant="small" color={colors.textMuted}>
          무료로도 하루 20문항, 영상 연동 문항, D-day, 개정 알림 제목, 시험 당일 가채점을 쓸 수 있습니다.
        </AppText>
      </Card>

      {!configured ? (
        <Notice
          tone="warning"
          text="이 빌드에는 결제 키(EXPO_PUBLIC_RC_IOS_KEY / EXPO_PUBLIC_RC_ANDROID_KEY)가 없어 결제할 수 없습니다. 출시 빌드에서 스토어 가격과 함께 표시됩니다."
        />
      ) : null}
      {error ? (
        <Card>
          <AppText>{error}</AppText>
          <Button title="다시 시도" variant="secondary" onPress={() => void load()} />
        </Card>
      ) : null}
      {configured && plans === null ? <Loading label="상품 정보를 불러오는 중" /> : null}
      {configured && plans?.length === 0 && !error ? (
        <Card>
          <AppText color={colors.textMuted}>지금 구매할 수 있는 상품이 없습니다. 잠시 뒤 다시 확인해 주세요.</AppText>
        </Card>
      ) : null}

      {(plans ?? []).map((p) => (
        <Card key={p.pkg.identifier} style={p.plan.kind === 'annual' ? { borderColor: colors.primary, borderWidth: 2 } : undefined}>
          <AppText variant="heading">
            {planTitle(p.plan.kind)}
            {p.plan.kind === 'annual' ? ' · 추천' : ''}
          </AppText>
          <AppText variant="title" color={colors.primary}>
            {p.plan.priceString}
            <AppText color={colors.textMuted}>
              {p.plan.kind === 'pass'
                ? ` / ${passPeriodLabel(p.plan)}(1회 결제)`
                : ` / ${periodLabel(p.plan.period) || (p.plan.kind === 'monthly' ? '1개월' : '1년')}`}
            </AppText>
          </AppText>
          {p.plan.trialDays ? (
            <AppText weight="700" color={colors.correct}>
              무료 체험 {p.plan.trialDays}일
            </AppText>
          ) : null}
          {p.plan.kind === 'annual' && p.pkg.product.pricePerMonthString ? (
            <AppText variant="small" color={colors.textMuted}>
              월 환산 {p.pkg.product.pricePerMonthString}
            </AppText>
          ) : null}
          {planDisclosure(p.plan, PLATFORM).map((line) => (
            <AppText key={line} variant="small" color={colors.textMuted}>
              {line}
            </AppText>
          ))}
          <Button
            title={p.plan.kind === 'pass' ? '패스 구매' : p.plan.trialDays ? '무료 체험 시작' : '구독하기'}
            loading={busy === p.pkg.identifier}
            disabled={busy !== null && busy !== p.pkg.identifier}
            accessibilityLabel={`${planTitle(p.plan.kind)} ${p.plan.priceString} ${p.plan.kind === 'pass' ? '구매' : '구독'}`}
            onPress={() => void buy(p)}
          />
        </Card>
      ))}

      {!configured ? (
        <Button
          title="구독하기"
          onPress={() => Alert.alert('결제 설정 전', 'RevenueCat 키를 넣은 빌드에서 결제할 수 있습니다. 앱의 다른 기능은 그대로 쓸 수 있습니다.')}
        />
      ) : null}

      <Button title="구매 복원" variant="secondary" loading={busy === 'restore'} onPress={() => void restore()} />
      <View style={{ gap: SPACE.xs }}>
        {COMMON_PURCHASE_NOTES.map((n) => (
          <AppText key={n} variant="small" color={colors.textMuted}>
            · {n}
          </AppText>
        ))}
      </View>
      <Links />
    </Screen>
  );
}
