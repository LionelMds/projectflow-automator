import type { ReactNode } from 'react';
import { StyleSheet, View, type StyleProp, type ViewStyle } from 'react-native';

import { colors } from '@/theme/tokens';

const MARK = 9;
const OFFSET = -5;

function Corner({ position }: { position: ViewStyle }) {
  return (
    <View pointerEvents="none" style={[styles.corner, position]}>
      <View style={styles.vertical} />
      <View style={styles.horizontal} />
    </View>
  );
}

/** The four "+" registration marks, placed over the corners of their parent. */
export function CornerMarks() {
  return (
    <>
      <Corner position={{ top: OFFSET, left: OFFSET }} />
      <Corner position={{ top: OFFSET, right: OFFSET }} />
      <Corner position={{ bottom: OFFSET, left: OFFSET }} />
      <Corner position={{ bottom: OFFSET, right: OFFSET }} />
    </>
  );
}

/**
 * The wireframe frame of the Industry system: square, hairline-bordered, with "+"
 * registration marks at the four corners.
 */
export function Blueprint({
  children,
  style,
  marks = true,
}: {
  children?: ReactNode;
  style?: StyleProp<ViewStyle>;
  marks?: boolean;
}) {
  return (
    <View style={[styles.frame, style]}>
      {children}
      {marks ? <CornerMarks /> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  frame: {
    borderWidth: 1,
    borderColor: colors.line,
  },
  corner: {
    position: 'absolute',
    width: MARK,
    height: MARK,
  },
  vertical: {
    position: 'absolute',
    left: 4,
    top: 0,
    width: 1,
    height: MARK,
    backgroundColor: colors.accent,
  },
  horizontal: {
    position: 'absolute',
    top: 4,
    left: 0,
    width: MARK,
    height: 1,
    backgroundColor: colors.accent,
  },
});
