import { useEffect, useState } from 'react';
import { Keyboard, Platform, type KeyboardEvent } from 'react-native';

/**
 * How far the keyboard covers a modal screen above its bottom inset. Such a screen is a
 * sheet: it does not start at the top of the display, which a `KeyboardAvoidingView` does
 * not know (and measuring a view « in the window » answers within the sheet too), but it
 * does end at the bottom of the display, where the keyboard's own height says what it hides.
 * The foot of the screen is padded by as much, so that its buttons stay in reach.
 */
export function useKeyboardLift(inset: number): number {
  const [height, setHeight] = useState(0);
  useEffect(() => {
    // Android resizes the window itself; a browser has no keyboard of its own.
    if (Platform.OS !== 'ios') return undefined;
    const rise = (event: KeyboardEvent) => setHeight(event.endCoordinates.height);
    const fall = () => setHeight(0);
    const subscriptions = [Keyboard.addListener('keyboardWillShow', rise), Keyboard.addListener('keyboardWillHide', fall)];
    return () => subscriptions.forEach((subscription) => subscription.remove());
  }, []);
  return Math.max(0, height - inset);
}
