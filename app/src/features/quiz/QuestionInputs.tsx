/**
 * 유형별 입력 부품(ox · mcq · blank · order · calc).
 * ox·mcq·보기형 blank/calc 는 누르면 바로 제출하고, 직접 입력·순서 맞추기는 제출 버튼을 쓴다.
 * 제출 뒤(revealed)에는 정답을 초록, 내가 고른 오답을 빨강으로 표시한다(색 + 글자 표시 함께).
 */
import { useMemo, useState } from 'react';
import { Pressable, TextInput, View } from 'react-native';

import type { Response } from '@/core/grading';
import type { Item } from '@/core/pack/types';
import { normalizeAnswer } from '@/core/pack/validate';
import { seedFromString, mulberry32, shuffle } from '@/core/random';
import { useApp } from '@/state/AppProvider';
import { AppText, Button } from '@/ui/primitives';
import { MIN_TOUCH, SPACE } from '@/ui/theme';

interface InputProps {
  item: Item;
  response: Response | null;
  revealed: boolean;
  onSubmit: (r: Response) => void;
}

const CIRCLED = ['①', '②', '③', '④', '⑤', '⑥', '⑦', '⑧'];

function ChoiceButton({
  label,
  index,
  state,
  disabled,
  onPress,
}: {
  label: string;
  index: number;
  state: 'idle' | 'selected' | 'correct' | 'wrong' | 'dim';
  disabled: boolean;
  onPress: () => void;
}) {
  const { colors, settings } = useApp();
  const borderColor =
    state === 'correct'
      ? colors.correct
      : state === 'wrong'
        ? colors.wrong
        : state === 'selected'
          ? colors.primary
          : colors.border;
  const suffix = state === 'correct' ? '  (정답)' : state === 'wrong' ? '  (내 답)' : state === 'selected' ? '  (선택)' : '';
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      accessibilityRole="button"
      accessibilityLabel={`${index}번 ${label}${suffix}`}
      accessibilityState={{ disabled, selected: state === 'selected' || state === 'wrong' }}
      style={({ pressed }) => ({
        minHeight: MIN_TOUCH + 4,
        borderRadius: 12,
        borderWidth: state === 'idle' || state === 'dim' ? (settings.highContrast ? 2 : 1.5) : 3,
        borderColor,
        backgroundColor: colors.surface,
        paddingHorizontal: SPACE.m,
        paddingVertical: SPACE.s,
        flexDirection: 'row',
        alignItems: 'center',
        gap: SPACE.s,
        opacity: state === 'dim' ? 0.6 : pressed ? 0.85 : 1,
      })}
    >
      <AppText weight="700" color={borderColor === colors.border ? colors.primary : borderColor}>
        {CIRCLED[index - 1] ?? `${index}.`}
      </AppText>
      <AppText style={{ flex: 1 }}>
        {label}
        {suffix ? <AppText weight="700" color={borderColor}>{suffix}</AppText> : null}
      </AppText>
    </Pressable>
  );
}

function choiceState(i: number, correct: number | undefined, picked: number | undefined, revealed: boolean) {
  if (!revealed) return i === picked ? ('selected' as const) : ('idle' as const);
  if (i === correct) return 'correct' as const;
  if (i === picked) return 'wrong' as const;
  return 'dim' as const;
}

export function OxInput({ item, response, revealed, onSubmit }: InputProps) {
  const { colors } = useApp();
  const ans = normalizeAnswer(item);
  const correct = ans?.type === 'ox' ? ans.value : undefined;
  const picked = response?.type === 'ox' ? response.value : undefined;
  const btn = (value: boolean) => {
    const isCorrect = revealed && value === correct;
    const isWrongPick = revealed && value === picked && value !== correct;
    const isSelected = !revealed && value === picked;
    const color = isCorrect ? colors.correct : isWrongPick ? colors.wrong : colors.primary;
    return (
      <Pressable
        key={String(value)}
        onPress={() => onSubmit({ type: 'ox', value })}
        disabled={revealed}
        accessibilityRole="button"
        accessibilityLabel={`${value ? 'O, 맞다' : 'X, 틀리다'}${isCorrect ? ', 정답' : ''}${isWrongPick ? ', 내 답' : ''}${isSelected ? ', 선택함' : ''}`}
        accessibilityState={{ selected: isSelected, disabled: revealed }}
        style={({ pressed }) => ({
          flex: 1,
          minHeight: 96,
          borderRadius: 16,
          borderWidth: isCorrect || isWrongPick || isSelected ? 4 : 2,
          borderColor: color,
          backgroundColor: isSelected ? colors.surfaceAlt : colors.surface,
          alignItems: 'center',
          justifyContent: 'center',
          opacity: revealed && !isCorrect && !isWrongPick ? 0.55 : pressed ? 0.85 : 1,
        })}
      >
        <AppText variant="title" color={color} style={{ fontSize: 44, lineHeight: 52 }}>
          {value ? 'O' : 'X'}
        </AppText>
      </Pressable>
    );
  };
  return <View style={{ flexDirection: 'row', gap: SPACE.m }}>{[btn(true), btn(false)]}</View>;
}

export function McqInput({ item, response, revealed, onSubmit }: InputProps) {
  const ans = normalizeAnswer(item);
  const correct = ans?.type === 'mcq' ? ans.index : undefined;
  const picked = response?.type === 'mcq' ? response.index : undefined;
  return (
    <View style={{ gap: SPACE.s }}>
      {(item.choices ?? []).map((c, i) => (
        <ChoiceButton
          key={`${item.id}-${i}`}
          label={c}
          index={i + 1}
          disabled={revealed}
          state={choiceState(i + 1, correct, picked, revealed)}
          onPress={() => onSubmit({ type: 'mcq', index: i + 1 })}
        />
      ))}
    </View>
  );
}

function FreeTextInput({
  placeholder,
  keyboard,
  unit,
  revealed,
  saved,
  initial,
  onSubmit,
}: {
  placeholder: string;
  keyboard: 'default' | 'decimal-pad';
  unit?: string;
  revealed: boolean;
  /** 제출(저장)된 값이 있는지 — 모의고사에서 표시용 */
  saved: boolean;
  initial?: string;
  onSubmit: (text: string) => void;
}) {
  const { colors, fontScale } = useApp();
  const [text, setText] = useState(initial ?? '');
  const submit = () => {
    if (text.trim()) onSubmit(text);
  };
  return (
    <View style={{ gap: SPACE.s }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: SPACE.s }}>
        <TextInput
          value={text}
          onChangeText={setText}
          editable={!revealed}
          placeholder={placeholder}
          placeholderTextColor={colors.textMuted}
          keyboardType={keyboard}
          returnKeyType="done"
          onSubmitEditing={submit}
          accessibilityLabel={placeholder}
          maxLength={60}
          style={{
            flex: 1,
            minHeight: MIN_TOUCH + 4,
            borderRadius: 12,
            borderWidth: 2,
            borderColor: colors.border,
            backgroundColor: colors.surface,
            color: colors.text,
            paddingHorizontal: SPACE.m,
            fontSize: Math.round(18 * fontScale),
          }}
        />
        {unit ? <AppText weight="700">{unit}</AppText> : null}
      </View>
      {!revealed ? <Button title={saved ? '답 고치기' : '제출'} onPress={submit} disabled={!text.trim()} /> : null}
      {!revealed && saved ? (
        <AppText variant="small" color={colors.textMuted}>
          답을 저장했습니다.
        </AppText>
      ) : null}
    </View>
  );
}

export function BlankInput({ item, response, revealed, onSubmit }: InputProps) {
  const ans = normalizeAnswer(item);
  if (item.choices?.length) {
    const correct = ans?.type === 'blank' ? ans.choiceIndex : undefined;
    const picked = response?.type === 'blank' ? response.choiceIndex : undefined;
    return (
      <View style={{ gap: SPACE.s }}>
        {item.choices.map((c, i) => (
          <ChoiceButton
            key={`${item.id}-${i}`}
            label={c}
            index={i + 1}
            disabled={revealed}
            state={choiceState(i + 1, correct, picked, revealed)}
            onPress={() => onSubmit({ type: 'blank', choiceIndex: i + 1 })}
          />
        ))}
      </View>
    );
  }
  return (
    <FreeTextInput
      key={item.id}
      placeholder="빈칸에 들어갈 말"
      keyboard="default"
      revealed={revealed}
      saved={response?.type === 'blank' && Boolean(response.text)}
      initial={response?.type === 'blank' ? response.text : undefined}
      onSubmit={(text) => onSubmit({ type: 'blank', text })}
    />
  );
}

export function CalcInput({ item, response, revealed, onSubmit }: InputProps) {
  const ans = normalizeAnswer(item);
  if (item.choices?.length) {
    const correct = ans?.type === 'calc' ? ans.choiceIndex : undefined;
    const picked = response?.type === 'calc' ? response.choiceIndex : undefined;
    return (
      <View style={{ gap: SPACE.s }}>
        {item.choices.map((c, i) => (
          <ChoiceButton
            key={`${item.id}-${i}`}
            label={c}
            index={i + 1}
            disabled={revealed}
            state={choiceState(i + 1, correct, picked, revealed)}
            onPress={() => onSubmit({ type: 'calc', choiceIndex: i + 1 })}
          />
        ))}
      </View>
    );
  }
  const unit = ans?.type === 'calc' ? ans.unit ?? item.unit_label : item.unit_label;
  return (
    <FreeTextInput
      key={item.id}
      placeholder="계산한 값(숫자)"
      keyboard="decimal-pad"
      unit={unit}
      revealed={revealed}
      saved={response?.type === 'calc' && response.value !== undefined}
      initial={response?.type === 'calc' && response.value !== undefined ? String(response.value) : undefined}
      onSubmit={(text) => onSubmit({ type: 'calc', value: text })}
    />
  );
}

/** 처음 배치: 문항 id 로 고정된 섞기(정답 순서와 같으면 한 칸 돌림) */
function initialOrder(item: Item): number[] {
  const n = item.choices?.length ?? 0;
  const base = Array.from({ length: n }, (_, i) => i + 1);
  let order = shuffle(base, mulberry32(seedFromString(item.id)));
  const ans = normalizeAnswer(item);
  if (ans?.type === 'order' && order.every((v, i) => v === ans.sequence[i]) && n > 1) {
    order = [...order.slice(1), order[0]!];
  }
  return order;
}

export function OrderInput({ item, response, revealed, onSubmit }: InputProps) {
  const { colors } = useApp();
  const [order, setOrder] = useState<number[]>(() =>
    response?.type === 'order' && response.sequence.length ? response.sequence : initialOrder(item),
  );
  const ans = normalizeAnswer(item);
  const correctSeq = ans?.type === 'order' ? ans.sequence : [];
  const shown = revealed && response?.type === 'order' ? response.sequence : order;
  const choices = item.choices ?? [];
  const move = (pos: number, delta: number) => {
    const to = pos + delta;
    if (to < 0 || to >= order.length) return;
    const next = order.slice();
    [next[pos], next[to]] = [next[to]!, next[pos]!];
    setOrder(next);
  };
  const rows = useMemo(() => shown, [shown]);
  return (
    <View style={{ gap: SPACE.s }}>
      {rows.map((choiceNo, pos) => {
        const ok = revealed ? correctSeq[pos] === choiceNo : null;
        const borderColor = ok === null ? colors.border : ok ? colors.correct : colors.wrong;
        return (
          <View
            key={`${item.id}-${choiceNo}`}
            accessibilityLabel={`${pos + 1}번째: ${choices[choiceNo - 1] ?? ''}${ok === null ? '' : ok ? ', 맞는 자리' : ', 틀린 자리'}`}
            style={{
              flexDirection: 'row',
              alignItems: 'center',
              gap: SPACE.s,
              borderWidth: ok === null ? 1.5 : 3,
              borderColor,
              borderRadius: 12,
              backgroundColor: colors.surface,
              padding: SPACE.s,
            }}
          >
            <AppText weight="700" color={colors.primary} style={{ width: 28, textAlign: 'center' }}>
              {pos + 1}
            </AppText>
            <AppText style={{ flex: 1 }}>{choices[choiceNo - 1]}</AppText>
            {!revealed ? (
              <View style={{ flexDirection: 'row', gap: SPACE.xs }}>
                <Pressable
                  onPress={() => move(pos, -1)}
                  disabled={pos === 0}
                  accessibilityRole="button"
                  accessibilityLabel={`${choices[choiceNo - 1]} 위로`}
                  style={{ minWidth: MIN_TOUCH, minHeight: MIN_TOUCH, alignItems: 'center', justifyContent: 'center', opacity: pos === 0 ? 0.3 : 1 }}
                >
                  <AppText variant="heading" color={colors.primary}>↑</AppText>
                </Pressable>
                <Pressable
                  onPress={() => move(pos, 1)}
                  disabled={pos === rows.length - 1}
                  accessibilityRole="button"
                  accessibilityLabel={`${choices[choiceNo - 1]} 아래로`}
                  style={{
                    minWidth: MIN_TOUCH,
                    minHeight: MIN_TOUCH,
                    alignItems: 'center',
                    justifyContent: 'center',
                    opacity: pos === rows.length - 1 ? 0.3 : 1,
                  }}
                >
                  <AppText variant="heading" color={colors.primary}>↓</AppText>
                </Pressable>
              </View>
            ) : null}
          </View>
        );
      })}
      {!revealed ? (
        <Button
          title={response?.type === 'order' ? '이 순서로 다시 저장' : '이 순서로 제출'}
          onPress={() => onSubmit({ type: 'order', sequence: order })}
        />
      ) : null}
    </View>
  );
}

export function QuestionInput(props: InputProps) {
  switch (props.item.type) {
    case 'ox':
      return <OxInput {...props} />;
    case 'mcq':
      return <McqInput {...props} />;
    case 'blank':
      return <BlankInput {...props} />;
    case 'order':
      return <OrderInput key={props.item.id} {...props} />;
    case 'calc':
      return <CalcInput {...props} />;
    default:
      return null;
  }
}
