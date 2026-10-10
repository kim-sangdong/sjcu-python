import Ionicons from '@expo/vector-icons/Ionicons';
import { Tabs } from 'expo-router/js-tabs';
import React from 'react';
import type { ColorValue } from 'react-native';

import { useApp } from '@/state/AppProvider';

type IconName = React.ComponentProps<typeof Ionicons>['name'];

function icon(name: IconName, focusedName: IconName) {
  return ({ color, focused, size }: { color: ColorValue; focused: boolean; size: number }) => (
    <Ionicons name={focused ? focusedName : name} color={color} size={size} />
  );
}

export default function TabsLayout() {
  const { colors, dueCount, isPremium } = useApp();
  return (
    <Tabs
      screenOptions={{
        tabBarActiveTintColor: colors.primary,
        tabBarInactiveTintColor: colors.textMuted,
        tabBarStyle: { backgroundColor: colors.surface, borderTopColor: colors.border },
        tabBarLabelStyle: { fontSize: 12, fontWeight: '600' },
        headerStyle: { backgroundColor: colors.surface },
        headerTintColor: colors.text,
        headerTitleStyle: { fontWeight: '700' },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{ title: '오늘', tabBarAccessibilityLabel: '오늘의 학습', tabBarIcon: icon('today-outline', 'today') }}
      />
      <Tabs.Screen
        name="review"
        options={{
          title: '복습',
          tabBarAccessibilityLabel: `오답 복습${isPremium && dueCount ? `, 복습 ${dueCount}개` : ''}`,
          tabBarBadge: isPremium && dueCount ? (dueCount > 99 ? '99+' : dueCount) : undefined,
          tabBarIcon: icon('repeat-outline', 'repeat'),
        }}
      />
      <Tabs.Screen
        name="stats"
        options={{ title: '진단', tabBarAccessibilityLabel: '약점 진단', tabBarIcon: icon('analytics-outline', 'analytics') }}
      />
      <Tabs.Screen
        name="more"
        options={{ title: '더보기', tabBarAccessibilityLabel: '더보기', tabBarIcon: icon('grid-outline', 'grid') }}
      />
    </Tabs>
  );
}
