import { memo, useEffect, useMemo, useRef, useState, type ComponentRef } from 'react';
import {
  Animated,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import { selectionFeedback } from '@/lib/haptics';
import { colors, fonts } from '@/theme/tokens';

/** Height of a row, and the rows seen: the chosen one in the middle, two above, two below. */
export const WHEEL_ROW = 38;
const VISIBLE = 5;
const PAD = (WHEEL_ROW * (VISIBLE - 1)) / 2;
/** Rows drawn on each side of the middle; the others are only room to scroll. */
const DRAWN = 14;
/** Quiet time after which the wheel has stopped and its row is chosen. */
const SETTLE_MS = 110;
const NATIVE = Platform.OS !== 'web';

export type WheelItem = { key: string; label: string; muted?: boolean };

type Props = {
  items: readonly WheelItem[];
  index: number;
  onChange: (index: number) => void;
  accessibilityLabel: string;
  align?: 'left' | 'center' | 'right';
  flex?: number;
};

/**
 * A wheel as the iPhone's date and time pickers turn: rows bending away from the middle
 * band, a tick at each row passed, the row under the band chosen once the wheel stops.
 * A touched row turns to the band.
 */
export function Wheel({ items, index, onChange, accessibilityLabel, align = 'center', flex = 1 }: Props) {
  const count = items.length;
  const target = Math.min(Math.max(index, 0), Math.max(count - 1, 0));
  const scroll = useRef<ComponentRef<typeof ScrollView>>(null);
  // Follows the scroll on the UI thread; the rows bend from it.
  const [offset] = useState(() => new Animated.Value(target * WHEEL_ROW));
  const onScroll = useMemo(
    () => Animated.event([{ nativeEvent: { contentOffset: { y: offset } } }], { useNativeDriver: NATIVE }),
    [offset],
  );
  // The latest props, for the listeners made once.
  const latest = useRef({ count, onChange });
  useEffect(() => {
    latest.current = { count, onChange };
  });
  const chosen = useRef(target);
  const passing = useRef(target);
  // Turned by the app (to a value given from outside): no tick, no new choice.
  const quiet = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const placed = useRef(false);
  const [around, setAround] = useState(target);
  const aroundRef = useRef(target);

  const rowAt = (y: number) => Math.min(Math.max(Math.round(y / WHEEL_ROW), 0), Math.max(latest.current.count - 1, 0));

  const turnTo = (row: number, animated = true) => {
    scroll.current?.scrollTo({ y: row * WHEEL_ROW, animated });
  };

  /** The wheel has stopped: the row under the band is the one chosen. */
  const settle = (y: number) => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    const row = rowAt(y);
    quiet.current = false;
    if (Math.abs(y - row * WHEEL_ROW) > 0.5) turnTo(row);
    if (row !== chosen.current) {
      chosen.current = row;
      latest.current.onChange(row);
    }
  };
  const settleRef = useRef(settle);
  useEffect(() => {
    settleRef.current = settle;
  });

  // While turning: a tick at each row passed, the rows drawn around the band, the choice
  // once the wheel has been still for a moment.
  useEffect(() => {
    const id = offset.addListener(({ value }) => {
      const row = rowAt(value);
      if (row !== passing.current) {
        passing.current = row;
        if (!quiet.current) selectionFeedback();
        if (Math.abs(row - aroundRef.current) > DRAWN / 2) {
          aroundRef.current = row;
          setAround(row);
        }
      }
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => settleRef.current(value), SETTLE_MS);
    });
    return () => {
      offset.removeListener(id);
      if (timer.current) clearTimeout(timer.current);
    };
  }, [offset]);

  // A value changed from outside (the end follows the start): the wheel turns to it.
  useEffect(() => {
    if (target === chosen.current) return;
    chosen.current = target;
    quiet.current = true;
    aroundRef.current = target;
    setAround(target);
    scroll.current?.scrollTo({ y: target * WHEEL_ROW, animated: true });
  }, [target]);

  const rows = [];
  for (let row = Math.max(0, around - DRAWN); row <= Math.min(count - 1, around + DRAWN); row += 1) {
    rows.push(<WheelRow key={items[row].key} item={items[row]} row={row} offset={offset} align={align} onPress={turnTo} />);
  }
  const shown = items[target]?.label ?? '';

  return (
    <View
      style={[styles.wheel, { flex }]}
      accessible
      accessibilityRole="adjustable"
      accessibilityLabel={accessibilityLabel}
      accessibilityValue={{ text: shown }}
      accessibilityActions={[{ name: 'increment' }, { name: 'decrement' }]}
      onAccessibilityAction={(event) => {
        const step = event.nativeEvent.actionName === 'increment' ? 1 : -1;
        const row = Math.min(Math.max(chosen.current + step, 0), count - 1);
        aroundRef.current = row;
        setAround(row);
        turnTo(row);
      }}
    >
      <Animated.ScrollView
        ref={scroll}
        showsVerticalScrollIndicator={false}
        snapToInterval={WHEEL_ROW}
        decelerationRate="fast"
        scrollEventThrottle={16}
        nestedScrollEnabled
        contentOffset={{ x: 0, y: target * WHEEL_ROW }}
        onContentSizeChange={() => {
          // Android and the web place the first offset only once the rows are laid out.
          if (placed.current) return;
          placed.current = true;
          turnTo(chosen.current, false);
        }}
        onScroll={onScroll}
        onMomentumScrollEnd={(event) => settle(event.nativeEvent.contentOffset.y)}
        contentContainerStyle={{ height: count * WHEEL_ROW + 2 * PAD }}
      >
        {rows}
      </Animated.ScrollView>
    </View>
  );
}

const WheelRow = memo(function WheelRow({
  item,
  row,
  offset,
  align,
  onPress,
}: {
  item: WheelItem;
  row: number;
  offset: Animated.Value;
  align: 'left' | 'center' | 'right';
  onPress: (row: number) => void;
}) {
  // Offsets at which this row is 3, 2, 1 rows below the band, under it, then above it.
  const middle = row * WHEEL_ROW;
  const inputRange = [-3, -2, -1, 0, 1, 2, 3].map((step) => middle + step * WHEEL_ROW);
  const style = {
    opacity: offset.interpolate({ inputRange, outputRange: [0.12, 0.3, 0.55, 1, 0.55, 0.3, 0.12], extrapolate: 'clamp' }),
    transform: [
      { perspective: 500 },
      {
        rotateX: offset.interpolate({
          inputRange,
          outputRange: ['-66deg', '-44deg', '-21deg', '0deg', '21deg', '44deg', '66deg'],
          extrapolate: 'clamp',
        }),
      },
      { scale: offset.interpolate({ inputRange, outputRange: [0.84, 0.9, 0.96, 1, 0.96, 0.9, 0.84], extrapolate: 'clamp' }) },
    ],
  };
  return (
    <Animated.View style={[styles.row, { top: PAD + middle }, style]}>
      <Pressable onPress={() => onPress(row)} style={[styles.press, styles[align]]} importantForAccessibility="no">
        <Text style={[styles.label, item.muted && styles.muted]} numberOfLines={1}>
          {item.label}
        </Text>
      </Pressable>
    </Animated.View>
  );
});

/** The band of the chosen row, behind the wheels of one picker. */
export function WheelBand() {
  return <View pointerEvents="none" style={styles.band} />;
}

export const WHEEL_HEIGHT = WHEEL_ROW * VISIBLE;

const styles = StyleSheet.create({
  wheel: { height: WHEEL_HEIGHT },
  band: {
    position: 'absolute',
    left: 0,
    right: 0,
    top: PAD,
    height: WHEEL_ROW,
    backgroundColor: 'rgba(65,97,128,0.08)',
    borderTopWidth: StyleSheet.hairlineWidth,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderColor: colors.line,
  },
  row: { position: 'absolute', left: 0, right: 0, height: WHEEL_ROW },
  press: { flex: 1, justifyContent: 'center', paddingHorizontal: 8 },
  left: { alignItems: 'flex-start' },
  center: { alignItems: 'center' },
  right: { alignItems: 'flex-end' },
  label: { fontFamily: fonts.bodyMedium, fontSize: 21, color: colors.text, fontVariant: ['tabular-nums'] },
  muted: { color: colors.muted },
});
