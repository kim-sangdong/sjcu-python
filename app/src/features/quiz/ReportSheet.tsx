/**
 * 문항 신고 → app_events "report". 운영자는 24시간 안에 확인하고, 오류면 매니페스트 disabled_ids 로 내린다.
 */
import { useState } from 'react';
import { Alert, Modal, Pressable, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { track } from '@/data/telemetry';
import { useApp } from '@/state/AppProvider';
import { AppText, Button } from '@/ui/primitives';
import { MIN_TOUCH, SPACE } from '@/ui/theme';

const REASONS = [
  { code: 'wrong_answer', label: '정답이 틀린 것 같아요' },
  { code: 'wrong_basis', label: '근거 조문·판례가 맞지 않아요' },
  { code: 'law_changed', label: '법령이 바뀐 것 같아요' },
  { code: 'typo', label: '오탈자·문장이 이상해요' },
  { code: 'other', label: '기타' },
] as const;

export function ReportSheet({
  itemId,
  visible,
  onClose,
}: {
  itemId: string;
  visible: boolean;
  onClose: () => void;
}) {
  const { colors, fontScale, pack } = useApp();
  const [reason, setReason] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const [sending, setSending] = useState(false);

  const send = async () => {
    if (!reason) return;
    setSending(true);
    await track('report', { item_id: itemId, reason, note: note.trim().slice(0, 300), pack_version: pack.manifest.version });
    setSending(false);
    setReason(null);
    setNote('');
    onClose();
    Alert.alert('신고를 받았습니다', '확인한 뒤 오류면 문항을 고치거나 내립니다. 알려 주셔서 고맙습니다.');
  };

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <View style={{ flex: 1, justifyContent: 'flex-end', backgroundColor: 'rgba(0,0,0,0.45)' }}>
        <SafeAreaView edges={['bottom']} style={{ backgroundColor: colors.bg, borderTopLeftRadius: 18, borderTopRightRadius: 18 }}>
          <View style={{ padding: SPACE.l, gap: SPACE.s }}>
            <AppText variant="heading">문항 신고</AppText>
            <AppText variant="small" color={colors.textMuted}>
              문항 번호 {itemId}
            </AppText>
            {REASONS.map((r) => (
              <Pressable
                key={r.code}
                onPress={() => setReason(r.code)}
                accessibilityRole="radio"
                accessibilityState={{ checked: reason === r.code }}
                style={{
                  minHeight: MIN_TOUCH,
                  flexDirection: 'row',
                  alignItems: 'center',
                  gap: SPACE.s,
                  borderRadius: 10,
                  paddingHorizontal: SPACE.m,
                  borderWidth: reason === r.code ? 2 : 1,
                  borderColor: reason === r.code ? colors.primary : colors.border,
                  backgroundColor: colors.surface,
                }}
              >
                <AppText color={colors.primary}>{reason === r.code ? '●' : '○'}</AppText>
                <AppText>{r.label}</AppText>
              </Pressable>
            ))}
            <TextInput
              value={note}
              onChangeText={setNote}
              placeholder="자세한 내용(선택, 300자). 이름·연락처 같은 개인정보는 적지 마세요."
              placeholderTextColor={colors.textMuted}
              multiline
              maxLength={300}
              accessibilityLabel="신고 내용"
              style={{
                minHeight: 80,
                borderRadius: 10,
                borderWidth: 1,
                borderColor: colors.border,
                backgroundColor: colors.surface,
                color: colors.text,
                padding: SPACE.m,
                fontSize: Math.round(16 * fontScale),
                textAlignVertical: 'top',
              }}
            />
            <View style={{ flexDirection: 'row', gap: SPACE.s }}>
              <Button title="취소" variant="secondary" onPress={onClose} style={{ flex: 1 }} />
              <Button title="보내기" onPress={send} disabled={!reason} loading={sending} style={{ flex: 1 }} />
            </View>
          </View>
        </SafeAreaView>
      </View>
    </Modal>
  );
}
