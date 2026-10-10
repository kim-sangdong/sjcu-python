/**
 * RevenueCat 결제 래퍼. 엔타이틀먼트 "premium" 하나로 유료 기능을 판정한다.
 * 키(EXPO_PUBLIC_RC_IOS_KEY / EXPO_PUBLIC_RC_ANDROID_KEY)가 없으면 configure 하지 않고
 * 결제 버튼은 안내만 한다. 앱의 나머지 기능은 그대로 동작한다.
 */
import { Linking, Platform } from 'react-native';
import Purchases, {
  LOG_LEVEL,
  PURCHASES_ERROR_CODE,
  type CustomerInfo,
  type PurchasesError,
  type PurchasesOffering,
  type PurchasesPackage,
} from 'react-native-purchases';

import { ENTITLEMENT_ID, ENV, PASS_DAYS, PASS_PACKAGE_ID } from '@/core/env';
import { isPassOnSale } from '@/core/exam';
import { periodDays, type PlanInfo, type PlanKind } from '@/core/offer';

const DAY_MS = 24 * 60 * 60 * 1000;
let configured = false;

export function rcApiKey(): string | null {
  if (Platform.OS === 'ios') return ENV.rcIosKey;
  if (Platform.OS === 'android') return ENV.rcAndroidKey;
  return null;
}

export function purchasesConfigured(): boolean {
  return configured;
}

export function initPurchases(): boolean {
  if (configured) return true;
  const apiKey = rcApiKey();
  if (!apiKey) return false;
  try {
    if (__DEV__) void Purchases.setLogLevel(LOG_LEVEL.WARN);
    // 익명 ID(appUserID: null) — 계정 기능이 없으므로 RevenueCat 이 만든 ID 를 그대로 쓴다.
    Purchases.configure({ apiKey, appUserID: null });
    configured = true;
  } catch (e) {
    if (__DEV__) console.warn('[purchases] configure 실패', e);
  }
  return configured;
}

export interface PremiumState {
  active: boolean;
  /** 만료 시각(ms). 자동 갱신 구독은 다음 갱신일, 패스는 구매일+60일 */
  expiresAt: number | null;
  productId: string | null;
  willRenew: boolean;
}

export const NOT_PREMIUM: PremiumState = { active: false, expiresAt: null, productId: null, willRenew: false };

export function premiumFromCustomerInfo(info: CustomerInfo, nowMs: number): PremiumState {
  const ent = info.entitlements.active[ENTITLEMENT_ID];
  if (!ent || !ent.isActive) return NOT_PREMIUM;
  let expiresAt = ent.expirationDateMillis ?? null;
  // 비갱신 패스에 만료일이 없게 설정된 경우의 방어: 마지막 구매일 + 60일
  if (expiresAt === null && /pass/i.test(ent.productIdentifier)) {
    expiresAt = ent.latestPurchaseDateMillis + PASS_DAYS * DAY_MS;
    if (nowMs >= expiresAt) return NOT_PREMIUM;
  }
  return { active: true, expiresAt, productId: ent.productIdentifier, willRenew: ent.willRenew };
}

export async function getAppUserId(): Promise<string | null> {
  if (!configured) return null;
  try {
    return await Purchases.getAppUserID();
  } catch {
    return null;
  }
}

export async function fetchCustomerInfo(): Promise<CustomerInfo | null> {
  if (!configured) return null;
  try {
    return await Purchases.getCustomerInfo();
  } catch {
    return null;
  }
}

export function onCustomerInfo(cb: (info: CustomerInfo) => void): () => void {
  if (!configured) return () => undefined;
  Purchases.addCustomerInfoUpdateListener(cb);
  return () => {
    Purchases.removeCustomerInfoUpdateListener(cb);
  };
}

export async function getCurrentOffering(): Promise<PurchasesOffering | null> {
  if (!configured) return null;
  const offerings = await Purchases.getOfferings();
  return offerings.current ?? null;
}

export interface PlanPackage {
  plan: PlanInfo;
  pkg: PurchasesPackage;
}

function trialDaysOf(pkg: PurchasesPackage): number | null {
  const p = pkg.product;
  if (Platform.OS === 'android') {
    const iso = p.defaultOption?.freePhase?.billingPeriod?.iso8601;
    return iso ? periodDays(iso) : null;
  }
  if (p.introPrice && p.introPrice.price === 0) return periodDays(p.introPrice.period);
  return null;
}

/** 오퍼링 → 화면에 보일 상품(월·연·패스). 패스는 판매 기간(8/1~시험일)에만 */
export function plansFromOffering(
  offering: PurchasesOffering | null,
  nowMs: number,
  examDate: string | null,
): PlanPackage[] {
  if (!offering) return [];
  const out: PlanPackage[] = [];
  const add = (kind: PlanKind, pkg: PurchasesPackage | null | undefined) => {
    if (!pkg) return;
    out.push({
      pkg,
      plan: {
        kind,
        priceString: pkg.product.priceString,
        period: pkg.product.subscriptionPeriod,
        trialDays: kind === 'pass' ? null : trialDaysOf(pkg),
      },
    });
  };
  add('annual', offering.annual);
  add('monthly', offering.monthly);
  if (isPassOnSale(nowMs, examDate)) {
    add(
      'pass',
      offering.availablePackages.find((p) => p.identifier === PASS_PACKAGE_ID),
    );
  }
  return out;
}

export type BuyResult =
  | { status: 'success'; info: CustomerInfo }
  | { status: 'cancelled' }
  | { status: 'pending' }
  | { status: 'error'; message: string };

function errorMessage(e: unknown): string {
  const err = e as Partial<PurchasesError>;
  switch (err.code) {
    case PURCHASES_ERROR_CODE.NETWORK_ERROR:
      return '인터넷 연결을 확인한 뒤 다시 시도해 주세요.';
    case PURCHASES_ERROR_CODE.STORE_PROBLEM_ERROR:
      return '스토어에 일시적인 문제가 있습니다. 잠시 뒤 다시 시도해 주세요.';
    case PURCHASES_ERROR_CODE.PRODUCT_ALREADY_PURCHASED_ERROR:
      return '이미 구매한 상품입니다. "구매 복원"을 눌러 주세요.';
    default:
      return err.message ?? '결제를 완료하지 못했습니다.';
  }
}

export async function buyPackage(pkg: PurchasesPackage): Promise<BuyResult> {
  try {
    const { customerInfo } = await Purchases.purchasePackage(pkg);
    return { status: 'success', info: customerInfo };
  } catch (e) {
    const err = e as Partial<PurchasesError>;
    if (err.userCancelled || err.code === PURCHASES_ERROR_CODE.PURCHASE_CANCELLED_ERROR) return { status: 'cancelled' };
    if (err.code === PURCHASES_ERROR_CODE.PAYMENT_PENDING_ERROR) return { status: 'pending' };
    return { status: 'error', message: errorMessage(e) };
  }
}

export async function restorePurchases(): Promise<{ ok: true; info: CustomerInfo } | { ok: false; message: string }> {
  try {
    return { ok: true, info: await Purchases.restorePurchases() };
  } catch (e) {
    return { ok: false, message: errorMessage(e) };
  }
}

const STORE_SUBSCRIPTIONS_URL =
  Platform.OS === 'ios'
    ? 'https://apps.apple.com/account/subscriptions'
    : 'https://play.google.com/store/account/subscriptions';

export async function openManageSubscriptions(): Promise<void> {
  if (configured) {
    try {
      await Purchases.showManageSubscriptions();
      return;
    } catch {
      /* 아래 링크로 */
    }
  }
  await Linking.openURL(STORE_SUBSCRIPTIONS_URL);
}

export async function setAttributes(attrs: Record<string, string>): Promise<void> {
  if (!configured || Object.keys(attrs).length === 0) return;
  try {
    await Purchases.setAttributes(attrs);
  } catch (e) {
    if (__DEV__) console.warn('[purchases] setAttributes 실패', e);
  }
}
