import { router, type Href } from 'expo-router';

/** Close a modal screen; opened from a link with no history, go to `fallback` instead. */
export function close(fallback: Href): void {
  if (router.canGoBack()) router.back();
  else router.replace(fallback);
}
