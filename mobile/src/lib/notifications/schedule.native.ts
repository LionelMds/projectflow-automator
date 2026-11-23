/**
 * The alerts of the planning as local notifications of the phone (expo-notifications).
 * Nothing goes through a server: the phone plans, at each reading of the planning, the
 * alerts still to come, and the system rings them even with the app closed.
 *
 * Only the notifications this file planned are touched: their identifier starts with
 * `PREFIX`. A task that moves gets a new identifier (it holds the time), so its old alert
 * is cancelled and the new one planned.
 */

import * as Notifications from 'expo-notifications';
import { AndroidImportance, IosAuthorizationStatus } from 'expo-notifications';
import { Platform } from 'react-native';

import { dateOf, type PlannedNotification } from '@/lib/planning/alerts';

import type { Permission } from './schedule';

export const NOTIFICATIONS_AVAILABLE = true;

const PREFIX = 'pf-alert:';
const CHANNEL = 'rappels';

// Shown as a banner even while ProjectFlow is on screen.
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
});

let channelReady: Promise<unknown> | null = null;

/** Android shows nothing, and does not even ask, before a channel exists. */
function ensureChannel(): Promise<unknown> {
  if (Platform.OS !== 'android') return Promise.resolve();
  channelReady ??= Notifications.setNotificationChannelAsync(CHANNEL, {
    name: 'Alertes du planning',
    importance: AndroidImportance.HIGH,
  });
  return channelReady;
}

function permissionOf(status: Notifications.NotificationPermissionsStatus): Permission {
  if (status.granted || status.ios?.status === IosAuthorizationStatus.PROVISIONAL) return 'granted';
  return status.canAskAgain && status.status === 'undetermined' ? 'undetermined' : 'denied';
}

export async function notificationPermission(): Promise<Permission> {
  return permissionOf(await Notifications.getPermissionsAsync());
}

export async function requestNotificationPermission(): Promise<Permission> {
  await ensureChannel();
  return permissionOf(await Notifications.requestPermissionsAsync());
}

/** Make the alerts planned on the phone those of `planned`; returns how many are planned. */
export async function syncPlanned(planned: readonly PlannedNotification[]): Promise<number> {
  const wanted = new Map(planned.map((alert) => [PREFIX + alert.id, alert]));
  const existing = await Notifications.getAllScheduledNotificationsAsync();
  const kept = new Set<string>();
  for (const request of existing) {
    if (!request.identifier.startsWith(PREFIX)) continue;
    if (wanted.has(request.identifier)) kept.add(request.identifier);
    else await Notifications.cancelScheduledNotificationAsync(request.identifier);
  }
  if (!wanted.size) return 0;
  await ensureChannel();
  for (const [identifier, alert] of wanted) {
    if (kept.has(identifier)) continue;
    await Notifications.scheduleNotificationAsync({
      identifier,
      content: {
        title: alert.title,
        body: alert.body,
        sound: true,
        data: { taskId: alert.taskId },
      },
      trigger: { type: Notifications.SchedulableTriggerInputTypes.DATE, date: dateOf(alert.due), channelId: CHANNEL },
    });
  }
  return wanted.size;
}

function taskOf(response: Notifications.NotificationResponse | null): string {
  const request = response?.notification.request;
  if (!request?.identifier.startsWith(PREFIX)) return '';
  const taskId = request.content.data?.taskId;
  return typeof taskId === 'string' ? taskId : '';
}

/** Call `open` with the task of an alert the user touched; returns what stops listening. */
export function onAlertOpened(open: (taskId: string) => void): () => void {
  // The alert that started the app, if it was started by one.
  const first = taskOf(Notifications.getLastNotificationResponse());
  if (first) {
    Notifications.clearLastNotificationResponse();
    open(first);
  }
  const subscription = Notifications.addNotificationResponseReceivedListener((response) => {
    const taskId = taskOf(response);
    if (!taskId) return;
    Notifications.clearLastNotificationResponse();
    open(taskId);
  });
  return () => subscription.remove();
}
