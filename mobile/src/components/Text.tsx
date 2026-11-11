import type { ReactNode } from 'react';
import { StyleSheet, Text, type StyleProp, type TextStyle } from 'react-native';

import { colors, fonts } from '@/theme/tokens';

type Props = { children: ReactNode; style?: StyleProp<TextStyle>; numberOfLines?: number };

/** Small uppercase condensed label above a title: « CRÉATION PROJET ». */
export function Kicker({ children, style }: Props) {
  return <Text style={[styles.kicker, style]}>{children}</Text>;
}

/** Field caption, as `caps` on the desktop. */
export function Caps({ children, style }: Props) {
  return <Text style={[styles.caps, style]}>{children}</Text>;
}

export function Title({ children, style, numberOfLines }: Props) {
  return (
    <Text accessibilityRole="header" numberOfLines={numberOfLines} style={[styles.title, style]}>
      {children}
    </Text>
  );
}

/** Numbered section heading of the desktop forms: « 01 IDENTIFICATION ». */
export function SectionHeading({ number, children }: { number: string; children: ReactNode }) {
  return (
    <Text accessibilityRole="header" style={styles.section}>
      <Text style={styles.sectionNumber}>{number}  </Text>
      {children}
    </Text>
  );
}

/** A dossier number, in the condensed face and the deep accent. */
export function DossierNumber({ children, style }: Props) {
  return <Text style={[styles.number, style]}>{children}</Text>;
}

export function Body({ children, style, numberOfLines }: Props) {
  return (
    <Text numberOfLines={numberOfLines} style={[styles.body, style]}>
      {children}
    </Text>
  );
}

export function Muted({ children, style, numberOfLines }: Props) {
  return (
    <Text numberOfLines={numberOfLines} style={[styles.muted, style]}>
      {children}
    </Text>
  );
}

const styles = StyleSheet.create({
  kicker: {
    fontFamily: fonts.heading,
    fontSize: 13,
    letterSpacing: 1.5,
    textTransform: 'uppercase',
    color: colors.muted,
  },
  caps: {
    fontFamily: fonts.heading,
    fontSize: 12,
    letterSpacing: 1.2,
    textTransform: 'uppercase',
    color: colors.muted,
  },
  title: {
    fontFamily: fonts.headingBold,
    fontSize: 30,
    lineHeight: 32,
    textTransform: 'uppercase',
    color: colors.text,
  },
  section: {
    fontFamily: fonts.headingBold,
    fontSize: 20,
    textTransform: 'uppercase',
    color: colors.text,
  },
  sectionNumber: {
    fontFamily: fonts.headingBold,
    fontSize: 14,
    letterSpacing: 0.8,
    color: colors.accent700,
  },
  number: {
    fontFamily: fonts.heading,
    fontSize: 16,
    letterSpacing: 0.6,
    color: colors.accent700,
  },
  body: {
    fontFamily: fonts.body,
    fontSize: 16,
    color: colors.text,
  },
  muted: {
    fontFamily: fonts.body,
    fontSize: 14,
    color: colors.muted,
  },
});
