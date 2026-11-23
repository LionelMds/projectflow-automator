/**
 * The web has no system notification here: nothing is planned.
 * (`schedule.native.ts` hands the alerts to iOS and Android.)
 */

import type { PlannedNotification } from '@/lib/planning/alerts';

export const NOTIFICATIONS_AVAILABLE = false;

/** `granted`, `denied`, or `undetermined` while the phone has not asked yet. */
export type Permission = 'granted' | 'denied' | 'undetermined';

export async function notificationPermission(): Promise<Permission> {
  return 'denied';
}

export async function requestNotificationPermission(): Promise<Permission> {
  return 'denied';
}

/** Make the alerts planned on the phone those of `planned`; returns how many are planned. */
export async function syncPlanned(_planned: readonly PlannedNotification[]): Promise<number> {
  return 0;
}

/** Call `open` with the task of an alert the user touched; returns what stops listening. */
export function onAlertOpened(_open: (taskId: string) => void): () => void {
  return () => undefined;
}
