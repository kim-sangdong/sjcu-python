/**
 * 공통 화면 부품. 모든 글자는 AppText 로 그려 사용자 글자 배율과 기기 글자 크기를 함께 따른다.
 */
import React from 'react';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type TextProps,
  type TextStyle,
  type ViewStyle,
} from 'react-native';
import { SafeAreaView, type Edge } from 'react-native-safe-area-context';

import { useApp } from '@/state/AppProvider';
import { MIN_TOUCH, SPACE, TYPE } from './theme';

type Variant = keyof typeof TYPE;

export function AppText({
  variant = 'body',
  color,
  weight,
  style,
  ...rest
}: TextProps & { variant?: Variant; color?: string; weight?: TextStyle['fontWeight'] }) {
  const { colors, fontScale } = useApp();
  const size = Math.round(TYPE[variant] * fontScale);
  const isHeading = variant === 'title' || variant === 'heading';
  return (
    <Text
      maxFontSizeMultiplier={2}
      accessibilityRole={isHeading ? 'header' : undefined}
      style={[
        {
          color: color ?? colors.text,
          fontSize: size,
          lineHeight: Math.round(size * 1.45),
          fontWeight: weight ?? (isHeading ? '700' : '400'),
        },
        style,
      ]}
      {...rest}
    />
  );
}

export function Screen({
  children,
  scroll = true,
  edges = ['bottom', 'left', 'right'],
  contentStyle,
}: {
  children: React.ReactNode;
  scroll?: boolean;
  edges?: Edge[];
  contentStyle?: StyleProp<ViewStyle>;
}) {
  const { colors } = useApp();
  const inner = scroll ? (
    <ScrollView
      contentContainerStyle={[styles.content, contentStyle]}
      keyboardShouldPersistTaps="handled"
      keyboardDismissMode="on-drag"
    >
      {children}
    </ScrollView>
  ) : (
    <View style={[styles.content, { flex: 1 }, contentStyle]}>{children}</View>
  );
  return (
    <SafeAreaView edges={edges} style={{ flex: 1, backgroundColor: colors.bg }}>
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        {inner}
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

export function Card({ children, style }: { children: React.ReactNode; style?: StyleProp<ViewStyle> }) {
  const { colors, settings } = useApp();
  return (
    <View
      style={[
        styles.card,
        {
          backgroundColor: colors.surface,
          borderColor: colors.border,
          borderWidth: settings.highContrast ? 2 : StyleSheet.hairlineWidth,
        },
        style,
      ]}
    >
      {children}
    </View>
  );
}

export function Button({
  title,
  onPress,
  variant = 'primary',
  disabled,
  loading,
  hint,
  style,
  accessibilityLabel,
}: {
  title: string;
  onPress: () => void;
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  disabled?: boolean;
  loading?: boolean;
  hint?: string;
  style?: StyleProp<ViewStyle>;
  accessibilityLabel?: string;
}) {
  const { colors } = useApp();
  const bg =
    variant === 'primary' ? colors.primary : variant === 'danger' ? colors.wrong : 'transparent';
  const fg = variant === 'primary' || variant === 'danger' ? colors.onPrimary : colors.primary;
  const border = variant === 'secondary' ? colors.primary : 'transparent';
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled || loading}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? title}
      accessibilityHint={hint}
      accessibilityState={{ disabled: !!(disabled || loading), busy: !!loading }}
      style={({ pressed }) => [
        styles.button,
        { backgroundColor: bg, borderColor: border, opacity: disabled ? 0.45 : pressed ? 0.8 : 1 },
        style,
      ]}
    >
      {loading ? (
        <ActivityIndicator color={fg} />
      ) : (
        <AppText color={fg} weight="700" style={{ textAlign: 'center' }}>
          {title}
        </AppText>
      )}
    </Pressable>
  );
}

export function Chip({
  label,
  selected,
  onPress,
  accessibilityLabel,
}: {
  label: string;
  selected: boolean;
  onPress: () => void;
  accessibilityLabel?: string;
}) {
  const { colors } = useApp();
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="checkbox"
      accessibilityLabel={accessibilityLabel ?? label}
      accessibilityState={{ checked: selected }}
      style={({ pressed }) => [
        styles.chip,
        {
          backgroundColor: selected ? colors.primary : colors.surface,
          borderColor: selected ? colors.primary : colors.border,
          opacity: pressed ? 0.8 : 1,
        },
      ]}
    >
      <AppText variant="small" color={selected ? colors.onPrimary : colors.text} weight={selected ? '700' : '400'}>
        {selected ? '✓ ' : ''}
        {label}
      </AppText>
    </Pressable>
  );
}

export function Row({ children, style }: { children: React.ReactNode; style?: StyleProp<ViewStyle> }) {
  return <View style={[styles.row, style]}>{children}</View>;
}

export function Notice({ text, tone = 'info' }: { text: string; tone?: 'info' | 'warning' }) {
  const { colors } = useApp();
  return (
    <View
      accessibilityRole="text"
      style={[
        styles.notice,
        { backgroundColor: colors.surfaceAlt, borderLeftColor: tone === 'warning' ? colors.warning : colors.primary },
      ]}
    >
      <AppText variant="small" color={colors.textMuted}>
        {text}
      </AppText>
    </View>
  );
}

export function ProgressBar({ value, label }: { value: number; label: string }) {
  const { colors } = useApp();
  const pct = Math.max(0, Math.min(1, value));
  return (
    <View
      accessibilityRole="progressbar"
      accessibilityLabel={label}
      accessibilityValue={{ min: 0, max: 100, now: Math.round(pct * 100) }}
      style={[styles.barTrack, { backgroundColor: colors.surfaceAlt, borderColor: colors.border }]}
    >
      <View style={{ width: `${pct * 100}%`, backgroundColor: colors.primary, height: '100%', borderRadius: 6 }} />
    </View>
  );
}

export function Loading({ label = '불러오는 중' }: { label?: string }) {
  const { colors } = useApp();
  return (
    <View style={{ padding: SPACE.xl, alignItems: 'center' }} accessibilityLabel={label}>
      <ActivityIndicator color={colors.primary} />
    </View>
  );
}

export const styles = StyleSheet.create({
  content: { padding: SPACE.l, gap: SPACE.m, paddingBottom: SPACE.xxl },
  card: { borderRadius: 14, padding: SPACE.l, gap: SPACE.s },
  button: {
    minHeight: MIN_TOUCH,
    borderRadius: 12,
    paddingHorizontal: SPACE.l,
    paddingVertical: SPACE.s,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 2,
  },
  chip: {
    minHeight: MIN_TOUCH - 4,
    borderRadius: 22,
    paddingHorizontal: SPACE.m,
    justifyContent: 'center',
    borderWidth: 1.5,
  },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: SPACE.s, alignItems: 'center' },
  notice: { borderLeftWidth: 4, borderRadius: 8, padding: SPACE.m },
  barTrack: { height: 12, borderRadius: 6, overflow: 'hidden', borderWidth: StyleSheet.hairlineWidth },
});
