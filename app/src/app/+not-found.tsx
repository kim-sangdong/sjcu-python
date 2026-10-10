import { Stack, useRouter } from 'expo-router';

import { AppText, Button, Card, Screen } from '@/ui/primitives';

export default function NotFound() {
  const router = useRouter();
  return (
    <Screen>
      <Stack.Screen options={{ title: '찾을 수 없음' }} />
      <Card>
        <AppText variant="heading">없는 화면입니다</AppText>
        <AppText>링크가 바뀌었거나 앱을 업데이트해야 열 수 있는 화면일 수 있습니다.</AppText>
        <Button title="처음 화면으로" onPress={() => router.replace('/')} />
      </Card>
    </Screen>
  );
}
