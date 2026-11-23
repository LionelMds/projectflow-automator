import { router } from 'expo-router';
import { useEffect, useMemo, useState } from 'react';
import { AppState } from 'react-native';

import { instantOf, plannedNotifications } from '@/lib/planning/alerts';
import { usePlanning } from '@/lib/planning/PlanningContext';
import { useSettings } from '@/lib/settings';

import { NOTIFICATIONS_AVAILABLE, notificationPermission, onAlertOpened, requestNotificationPermission, syncPlanned } from './schedule';

/**
 * Keeps the alerts planned on the phone in step with the planning: at each reading, and at
 * each return to the app, the alerts still to come are handed to the system. The phone asks
 * for the permission the first time there is an alert to plan, not at the first start. An
 * alert touched opens its task. Renders nothing.
 */
export function NotificationSync() {
  const { settings, loaded } = useSettings();
  const { data, loadedAt } = usePlanning();
  // Counts the returns to the app: the alerts are planned again from the time it is then.
  const [wake, setWake] = useState(0);
  const enabled = NOTIFICATIONS_AVAILABLE && loaded && settings.alerts;

  useEffect(() => {
    if (!NOTIFICATIONS_AVAILABLE) return undefined;
    const subscription = AppState.addEventListener('change', (state) => {
      if (state === 'active') setWake((count) => count + 1);
    });
    return () => subscription.remove();
  }, []);

  useEffect(() => {
    if (!NOTIFICATIONS_AVAILABLE) return undefined;
    return onAlertOpened((taskId) => router.push({ pathname: '/tache', params: { id: taskId } }));
  }, []);

  const planned = useMemo(() => {
    // Read again at each planning reading and each return (`loadedAt`, `wake`).
    void loadedAt;
    void wake;
    if (!enabled || !data) return null;
    return plannedNotifications(data, { now: instantOf(new Date()), initials: settings.initials, audience: settings.alertsAudience });
  }, [enabled, data, loadedAt, wake, settings.initials, settings.alertsAudience]);

  useEffect(() => {
    if (!NOTIFICATIONS_AVAILABLE || !loaded) return;
    // Alerts switched off: what was planned is withdrawn. Planning not read yet: left as it is.
    if (!settings.alerts) {
      void syncPlanned([]).catch(() => undefined);
      return;
    }
    if (!planned) return;
    let alive = true;
    void (async () => {
      let permission = await notificationPermission();
      if (permission === 'undetermined' && planned.length) permission = await requestNotificationPermission();
      if (alive && permission === 'granted') await syncPlanned(planned);
    })().catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [loaded, settings.alerts, planned]);

  return null;
}
