import * as Haptics from 'expo-haptics';
import { Platform } from 'react-native';

/** A light knock when a long press opens a menu, as the phone's own menus do. */
export function pressFeedback(): void {
  if (Platform.OS === 'web') return;
  Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => undefined);
}

/** A tick when a choice changes (status, person, type). */
export function selectionFeedback(): void {
  if (Platform.OS === 'web') return;
  Haptics.selectionAsync().catch(() => undefined);
}
