/**
 * 앱 전역 상태: 설정, 문항 팩, 유료 여부, 무료 한도·연속 학습, 복습 대기 수.
 * 시작 순서: DB → 설정 → 팩(캐시/시드) → 결제 SDK·익명 ID → (준비 완료) → 백그라운드로 설치 보고·팩 갱신.
 */
import * as SplashScreen from 'expo-splash-screen';
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { AppState, useColorScheme, type AppStateStatus } from 'react-native';
import type { CustomerInfo } from 'react-native-purchases';

import { ENV } from '@/core/env';
import { daysToExam, examInfo, freeDailyLimit, targetExamDate } from '@/core/exam';
import { buildPack } from '@/core/pack/sync';
import type { LoadedPack } from '@/core/pack/types';
import { consume, currentQuota, FREE_DAILY_LIMIT, touchStreak, type QuotaState, type StreakState } from '@/core/quota';
import { reportInstallOnce } from '@/data/attribution';
import { getDb } from '@/data/db';
import { flushOutbox } from '@/data/outboxStore';
import { clearPackCache, loadInitialPack, updatePack } from '@/data/packStore';
import {
  fetchCustomerInfo,
  initPurchases,
  NOT_PREMIUM,
  onCustomerInfo,
  premiumFromCustomerInfo,
  purchasesConfigured,
  type PremiumState,
} from '@/data/purchases';
import { countDue, getKV, resetLearningData, setKV } from '@/data/repo';
import { APP_VERSION, KV_APP_USER_ID, KV_LOCAL_ID, resolveIdentity, setPackVersion, track } from '@/data/telemetry';
import { KV_INSTALL_REPORTED } from '@/data/attribution';
import { palette, type Palette } from '@/ui/theme';
import { DEFAULT_SETTINGS, type Settings } from './settings';

type PackStatus = 'idle' | 'checking' | 'updated' | 'up-to-date' | 'error' | 'incompatible' | 'offline-config';

interface PremiumCache extends PremiumState {
  checkedAt: number;
}

export interface AppContextValue {
  ready: boolean;
  settings: Settings;
  updateSettings: (patch: Partial<Settings>) => Promise<void>;
  colors: Palette;
  scheme: 'light' | 'dark';
  fontScale: number;
  pack: LoadedPack;
  packStatus: PackStatus;
  packMessage: string | null;
  refreshPack: () => Promise<void>;
  premium: PremiumState;
  isPremium: boolean;
  purchasesReady: boolean;
  applyCustomerInfo: (info: CustomerInfo) => void;
  quota: QuotaState;
  /** 무료 하루 문항 수(매니페스트 free_daily, 기본 20) */
  freeLimit: number;
  freeRemaining: number;
  /** 무료 한도에서 한 문항을 쓴다. 유료면 항상 true */
  consumeQuota: () => Promise<boolean>;
  streak: StreakState;
  markStudied: () => Promise<void>;
  dueCount: number;
  refreshDue: () => Promise<void>;
  examDate: string | null;
  daysLeft: number | null;
  resetAll: () => Promise<void>;
}

const Ctx = createContext<AppContextValue | null>(null);

const EMPTY_PACK: LoadedPack = buildPack(
  {
    version: 'empty',
    generated_at: '',
    exam: examInfo(null),
    subjects: [],
    free_ids: [],
  },
  [],
  'seed',
);

const SESSION_GAP_MS = 30 * 60 * 1000;
const PACK_CHECK_GAP_MS = 6 * 60 * 60 * 1000;

void SplashScreen.preventAutoHideAsync().catch(() => undefined);

export function AppProvider({ children }: { children: React.ReactNode }) {
  const systemScheme = useColorScheme();
  const [ready, setReady] = useState(false);
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
  const [pack, setPack] = useState<LoadedPack>(EMPTY_PACK);
  const [packStatus, setPackStatus] = useState<PackStatus>('idle');
  const [packMessage, setPackMessage] = useState<string | null>(null);
  const [premium, setPremium] = useState<PremiumState>(NOT_PREMIUM);
  const [purchasesReady, setPurchasesReady] = useState(false);
  const [quota, setQuota] = useState<QuotaState>(currentQuota(null, Date.now()));
  const [streak, setStreak] = useState<StreakState>({ last: null, count: 0, best: 0 });
  const [dueCount, setDueCount] = useState(0);

  const packRef = useRef(pack);
  packRef.current = pack;
  const settingsRef = useRef(settings);
  settingsRef.current = settings;
  const quotaRef = useRef(quota);
  quotaRef.current = quota;
  const streakRef = useRef(streak);
  streakRef.current = streak;
  const lastSessionAt = useRef(0);
  const lastBackgroundAt = useRef(0);
  const lastPackCheck = useRef(0);

  const applyCustomerInfo = useCallback((info: CustomerInfo) => {
    const p = premiumFromCustomerInfo(info, Date.now());
    setPremium(p);
    void setKV('premium_cache', { ...p, checkedAt: Date.now() } satisfies PremiumCache);
  }, []);

  const refreshPack = useCallback(async () => {
    if (!ENV.manifestUrl) {
      setPackStatus('offline-config');
      return;
    }
    setPackStatus('checking');
    lastPackCheck.current = Date.now();
    const res = await updatePack({ manifestUrl: ENV.manifestUrl, appVersion: APP_VERSION, current: packRef.current });
    if (res.status === 'updated') {
      setPack(res.pack);
      setPackVersion(res.pack.manifest.version);
      setPackStatus('updated');
      setPackMessage(`새 문항 팩(${res.pack.items.length}문항)을 받았습니다.`);
      if (__DEV__ && res.dropped.length) console.warn('[pack] 버린 문항', res.dropped.slice(0, 20));
    } else if (res.status === 'incompatible') {
      setPackStatus('incompatible');
      setPackMessage(`새 문항을 받으려면 앱을 ${res.required} 이상으로 업데이트하세요.`);
    } else if (res.status === 'error') {
      setPackStatus('error');
      if (__DEV__) console.warn('[pack] 갱신 실패', res.error);
    } else setPackStatus('up-to-date');
  }, []);

  const refreshDue = useCallback(async () => {
    setDueCount(await countDue(Date.now()));
  }, []);

  const premiumActiveRef = useRef(false);
  premiumActiveRef.current = premium.active;
  const trackSessionStart = useCallback(() => {
    lastSessionAt.current = Date.now();
    void track('session_start', { premium: premiumActiveRef.current });
  }, []);

  // 시작
  useEffect(() => {
    let cancelled = false;
    let unsubscribe: (() => void) | undefined;
    (async () => {
      try {
        // 팩은 DB 와 무관하게 먼저(캐시 → 없으면 내장 시드). DB 가 실패해도 문항은 보인다.
        const p = await loadInitialPack();
        if (cancelled) return;
        setPack(p);
        setPackVersion(p.manifest.version);
        await getDb();
        const s = { ...DEFAULT_SETTINGS, ...(await getKV<Partial<Settings>>('settings', {})) };
        const q = await getKV<QuotaState | null>('quota', null);
        const st = await getKV<StreakState | null>('streak', null);
        const cache = await getKV<PremiumCache | null>('premium_cache', null);
        if (cancelled) return;
        setSettings(s);
        setQuota(currentQuota(q, Date.now()));
        if (st) setStreak(st);
        // 오프라인에서도 직전 확인 결과로 유료 기능을 연다(만료일이 지났으면 닫는다)
        if (cache?.active && (cache.expiresAt === null || cache.expiresAt > Date.now())) setPremium(cache);

        const configured = initPurchases();
        await resolveIdentity();
        if (configured) {
          unsubscribe = onCustomerInfo(applyCustomerInfo);
          const info = await fetchCustomerInfo();
          if (info) applyCustomerInfo(info);
        }
        setPurchasesReady(true);
        await refreshDue();
      } catch (e) {
        if (__DEV__) console.error('[app] 시작 실패', e);
      } finally {
        if (!cancelled) {
          setReady(true);
          void SplashScreen.hideAsync().catch(() => undefined);
        }
      }
      // 화면이 뜬 뒤 백그라운드 작업
      void reportInstallOnce().catch(() => undefined);
      void flushOutbox();
      void refreshPack();
    })();
    return () => {
      cancelled = true;
      unsubscribe?.();
    };
  }, [applyCustomerInfo, refreshDue, refreshPack]);

  // session_start: 준비된 뒤 한 번, 이후 백그라운드에서 30분 넘게 있다가 돌아오면 다시
  useEffect(() => {
    if (ready) trackSessionStart();
  }, [ready, trackSessionStart]);

  useEffect(() => {
    if (!ready) return;
    const onChange = (state: AppStateStatus) => {
      if (state === 'active') {
        if (lastBackgroundAt.current && Date.now() - lastBackgroundAt.current > SESSION_GAP_MS) trackSessionStart();
        lastBackgroundAt.current = 0;
        void flushOutbox();
        void refreshDue();
        setQuota((q) => {
          const next = currentQuota(q, Date.now());
          quotaRef.current = next;
          return next;
        });
        if (Date.now() - lastPackCheck.current > PACK_CHECK_GAP_MS) void refreshPack();
      } else if (state === 'background') {
        lastBackgroundAt.current = Date.now();
        void flushOutbox();
      }
    };
    const sub = AppState.addEventListener('change', onChange);
    const timer = setInterval(() => {
      if (AppState.currentState === 'active') void flushOutbox();
    }, 60_000);
    return () => {
      sub.remove();
      clearInterval(timer);
    };
  }, [ready, trackSessionStart, refreshDue, refreshPack]);

  const updateSettings = useCallback(async (patch: Partial<Settings>) => {
    const next: Settings = { ...settingsRef.current, ...patch };
    settingsRef.current = next;
    setSettings(next);
    await setKV('settings', next);
  }, []);

  const isPremium = premium.active || (__DEV__ && !purchasesConfigured() && settings.devPremium);

  const freeLimit = freeDailyLimit(pack.manifest, FREE_DAILY_LIMIT);
  const freeLimitRef = useRef(freeLimit);
  freeLimitRef.current = freeLimit;

  const consumeQuota = useCallback(async () => {
    const res = consume(quotaRef.current, Date.now(), { premium: isPremium, limit: freeLimitRef.current });
    quotaRef.current = res.state;
    setQuota(res.state);
    if (res.allowed && !isPremium) await setKV('quota', res.state);
    return res.allowed;
  }, [isPremium]);

  const markStudied = useCallback(async () => {
    const prev = streakRef.current;
    const next = touchStreak(prev, Date.now());
    if (next !== prev) {
      streakRef.current = next;
      setStreak(next);
      await setKV('streak', next);
    }
  }, []);

  const resetAll = useCallback(async () => {
    await resetLearningData([KV_APP_USER_ID, KV_LOCAL_ID, KV_INSTALL_REPORTED, 'install_utm', 'premium_cache']);
    try {
      clearPackCache();
    } catch {
      /* noop */
    }
    settingsRef.current = DEFAULT_SETTINGS;
    setSettings(DEFAULT_SETTINGS);
    const q = currentQuota(null, Date.now());
    quotaRef.current = q;
    setQuota(q);
    const st = { last: null, count: 0, best: 0 };
    streakRef.current = st;
    setStreak(st);
    setDueCount(0);
  }, []);

  const scheme: 'light' | 'dark' =
    settings.theme === 'system' ? (systemScheme === 'dark' ? 'dark' : 'light') : settings.theme;
  const colors = useMemo(() => palette(scheme, settings.highContrast), [scheme, settings.highContrast]);
  const examDate = targetExamDate(pack.manifest, settings.targetRound);
  const now = Date.now();
  const cur = currentQuota(quota, now);

  const value: AppContextValue = {
    ready,
    settings,
    updateSettings,
    colors,
    scheme,
    fontScale: settings.fontScale,
    pack,
    packStatus,
    packMessage,
    refreshPack,
    premium,
    isPremium,
    purchasesReady,
    applyCustomerInfo,
    quota: cur,
    freeLimit,
    freeRemaining: isPremium ? Infinity : Math.max(0, freeLimit - cur.used),
    consumeQuota,
    streak,
    markStudied,
    dueCount,
    refreshDue,
    examDate,
    daysLeft: daysToExam(examDate, now),
    resetAll,
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useApp(): AppContextValue {
  const v = useContext(Ctx);
  if (!v) throw new Error('useApp 은 AppProvider 안에서만 쓸 수 있습니다');
  return v;
}
