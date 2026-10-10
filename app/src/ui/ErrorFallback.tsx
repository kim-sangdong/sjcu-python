/**
 * 에러 바운더리 화면. 테마 컨텍스트가 깨졌을 수도 있으므로 useApp 없이 고정 색으로 그린다.
 */
import type { ErrorBoundaryProps } from 'expo-router';
import { Pressable, ScrollView, Text, View } from 'react-native';

export function ErrorFallback({ error, retry }: ErrorBoundaryProps) {
  return (
    <View style={{ flex: 1, backgroundColor: '#FFF8F2', padding: 24, justifyContent: 'center', gap: 16 }}>
      <Text accessibilityRole="header" style={{ fontSize: 22, fontWeight: '700', color: '#1A1717' }}>
        화면을 여는 중 문제가 생겼습니다
      </Text>
      <Text style={{ fontSize: 17, color: '#1A1717', lineHeight: 25 }}>
        학습 기록은 기기에 그대로 남아 있습니다. 다시 시도해도 같은 문제가 생기면 앱을 껐다 켜 주세요.
      </Text>
      {__DEV__ ? (
        <ScrollView style={{ maxHeight: 160 }}>
          <Text style={{ fontSize: 12, color: '#5C5352' }}>{String(error?.message ?? error)}</Text>
        </ScrollView>
      ) : null}
      <Pressable
        onPress={retry}
        accessibilityRole="button"
        accessibilityLabel="다시 시도"
        style={{ minHeight: 48, borderRadius: 12, backgroundColor: '#8C2129', alignItems: 'center', justifyContent: 'center' }}
      >
        <Text style={{ color: '#FFFFFF', fontSize: 17, fontWeight: '700' }}>다시 시도</Text>
      </Pressable>
    </View>
  );
}
