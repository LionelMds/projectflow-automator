import { useEffect, useMemo, useRef } from 'react';
import { AppState } from 'react-native';

import { useAuth } from '@/lib/auth';
import { withDossiers } from '@/lib/planning/dossiers';
import { usePlanning } from '@/lib/planning/PlanningContext';
import { useRecentDossiers } from '@/lib/recent/RecentContext';
import { recentWidgetProps } from '@/lib/recent/recent';
import { useRepertoire } from '@/lib/repertoire/RepertoireContext';
import { useSettings } from '@/lib/settings';
import { planningMessage, planningTimeline, timelineNumbers, widgetChoiceOptions } from '@/lib/widgets/planningWidget';
import { publishPlanning, publishPlanningChoices, publishRecent, WIDGETS_AVAILABLE } from '@/lib/widgets/publish';

/** A planning older than this is read again when the app comes back to the screen. */
const STALE_MS = 2 * 60 * 1000;

/**
 * Keeps the home-screen widgets in step with the app: the planning read at start-up and on
 * every return to the app, everybody's next two weeks written to the « Planning » widget with
 * its « Afficher » list (me, my selection, teams, people), the recent dossiers to theirs.
 * Renders nothing.
 */
export function WidgetSync() {
  const { settings, loaded } = useSettings();
  const { status } = useAuth();
  const { data, loadedAt, load } = usePlanning();
  const { projects, wantProjects } = useRepertoire();
  const { recent, ready: recentRead } = useRecentDossiers();
  const ready = WIDGETS_AVAILABLE && loaded && (settings.demo || status === 'signedIn');

  const lastRead = useRef<Date | null>(null);
  useEffect(() => {
    lastRead.current = loadedAt;
  }, [loadedAt]);

  useEffect(() => {
    if (!ready) return undefined;
    const refresh = () => {
      const read = lastRead.current;
      if (!read || Date.now() - read.getTime() > STALE_MS) void load();
    };
    refresh();
    const subscription = AppState.addEventListener('change', (state) => {
      if (state === 'active') refresh();
    });
    return () => subscription.remove();
  }, [ready, load]);

  // The designations of the dossiers the widget may show, from the répertoire, as the planning shows them.
  const numbers = useMemo(() => (data ? timelineNumbers(data).join(' ') : ''), [data]);
  useEffect(() => {
    if (ready && numbers) void wantProjects(numbers.split(' '));
  }, [ready, numbers, wantProjects]);

  // « Moi » and « Ma sélection », as set in Paramètres.
  const people = useMemo(
    () => ({ initials: settings.initials, selection: settings.widgetPeople }),
    [settings.initials, settings.widgetPeople],
  );

  useEffect(() => {
    if (!WIDGETS_AVAILABLE || !loaded) return;
    if (!settings.demo && status === 'signedOut') {
      publishPlanning([{ date: new Date(), props: planningMessage('Connectez-vous à Microsoft dans ProjectFlow.') }]);
      return;
    }
    // Until the planning is read, the widget keeps what it showed.
    if (!data || !loadedAt) return;
    publishPlanningChoices(widgetChoiceOptions(data, people));
    publishPlanning(planningTimeline(withDossiers(data, projects), people, loadedAt));
  }, [loaded, settings.demo, people, status, data, loadedAt, projects]);

  useEffect(() => {
    if (WIDGETS_AVAILABLE && recentRead) publishRecent(recentWidgetProps(recent));
  }, [recentRead, recent]);

  return null;
}
