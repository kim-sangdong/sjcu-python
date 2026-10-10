/**
 * 루트 레이아웃: 전역 상태 → 테마 → 스택.
 * 온보딩을 마치기 전에는 (tabs) 를 막고 onboarding 으로 보낸다. 영상 덱 딥링크(/deck/<asset_id>)는
 * 온보딩 전에도 열린다(영상에서 바로 들어온 사람이 문항부터 풀 수 있게).
 */
import { DarkTheme, DefaultTheme, Stack, ThemeProvider, type ErrorBoundaryProps } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { AppProvider, useApp } from '@/state/AppProvider';
import { ErrorFallback } from '@/ui/ErrorFallback';

export function ErrorBoundary(props: ErrorBoundaryProps) {
  return <ErrorFallback {...props} />;
}

export const unstable_settings = {
  screenErrorBoundary: ErrorFallback,
};

function RootStack() {
  const { ready, settings, colors, scheme } = useApp();
  if (!ready) return null; // 스플래시가 덮고 있다
  const base = scheme === 'dark' ? DarkTheme : DefaultTheme;
  const navTheme = {
    ...base,
    colors: {
      ...base.colors,
      primary: colors.primary,
      background: colors.bg,
      card: colors.surface,
      text: colors.text,
      border: colors.border,
    },
  };
  return (
    <ThemeProvider value={navTheme}>
      <StatusBar style={scheme === 'dark' ? 'light' : 'dark'} />
      <Stack
        screenOptions={{
          headerTitleStyle: { fontWeight: '700' },
          headerBackButtonDisplayMode: 'minimal',
          contentStyle: { backgroundColor: colors.bg },
        }}
      >
        <Stack.Protected guard={!settings.onboarded}>
          <Stack.Screen name="onboarding" options={{ headerShown: false }} />
        </Stack.Protected>
        <Stack.Protected guard={settings.onboarded}>
          <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
        </Stack.Protected>
        <Stack.Screen name="quiz" options={{ title: '문제 풀이' }} />
        <Stack.Screen name="deck/[assetId]" options={{ title: '영상 문항' }} />
        <Stack.Screen name="cards" options={{ title: '숫자 암기 카드' }} />
        <Stack.Screen name="mock/index" options={{ title: '자체 모의고사' }} />
        <Stack.Screen name="mock/run" options={{ title: '모의고사', gestureEnabled: false }} />
        <Stack.Screen name="amendments/index" options={{ title: '법령 개정 알림' }} />
        <Stack.Screen name="amendments/[id]" options={{ title: '개정 상세' }} />
        <Stack.Screen name="grading" options={{ title: '가채점' }} />
        <Stack.Screen name="decks" options={{ title: '영상 연동 문항' }} />
        <Stack.Screen name="settings" options={{ title: '설정' }} />
        <Stack.Screen name="paywall" options={{ title: '이용권', presentation: 'modal' }} />
      </Stack>
    </ThemeProvider>
  );
}

export default function RootLayout() {
  return (
    <SafeAreaProvider>
      <AppProvider>
        <RootStack />
      </AppProvider>
    </SafeAreaProvider>
  );
}
