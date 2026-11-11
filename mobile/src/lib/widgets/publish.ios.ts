/**
 * The iPhone's widgets, given what they show. Expo Go and builds made before the widgets have
 * no widget module: nothing is sent there, and the app works as before.
 */

import { requireOptionalNativeModule } from 'expo';

import { storable } from '@/lib/widgets/storable';
import type { PlanningWidgetProps, RecentWidgetProps, WidgetChoiceOption } from '@/widgets/types';

type WidgetHandle<T extends object> = {
  updateTimeline: (entries: { date: Date; props: T }[]) => void;
  updateSnapshot: (props: T) => void;
  setConfigurationParameterEnum: (parameterName: string, options?: WidgetChoiceOption[]) => void;
};

export const WIDGETS_AVAILABLE = requireOptionalNativeModule('ExpoWidgets') !== null;

let widgets: { planning: WidgetHandle<PlanningWidgetProps>; recent: WidgetHandle<RecentWidgetProps> } | null = null;

function loaded() {
  if (!WIDGETS_AVAILABLE) return null;
  if (!widgets) {
    // Required here only: the widget module must not be loaded where it does not exist.
    /* eslint-disable @typescript-eslint/no-require-imports */
    widgets = {
      planning: require('@/widgets/PlanningJour').default,
      recent: require('@/widgets/DossiersRecents').default,
    };
    /* eslint-enable @typescript-eslint/no-require-imports */
  }
  return widgets;
}

// The same content is not sent twice: each send redraws the widget.
let lastPlanning = '';
let lastRecent = '';
let lastChoices = '';

/** The « Afficher » list of the « Planning » widget (« Modifier le widget » on the iPhone). */
export function publishPlanningChoices(options: WidgetChoiceOption[]): void {
  const key = JSON.stringify(options);
  if (key === lastChoices) return;
  try {
    loaded()?.planning.setConfigurationParameterEnum('afficher', storable(options));
    lastChoices = key;
  } catch {
    // The widget keeps its last list; the app goes on.
  }
}

export function publishPlanning(entries: { date: Date; props: PlanningWidgetProps }[]): void {
  const key = JSON.stringify([entries[0]?.date.toDateString(), entries.map((entry) => entry.props)]);
  if (key === lastPlanning) return;
  try {
    loaded()?.planning.updateTimeline(entries.map((entry) => ({ date: entry.date, props: storable(entry.props) })));
    lastPlanning = key;
  } catch {
    // A widget that cannot be updated keeps what it showed; the app goes on.
  }
}

export function publishRecent(props: RecentWidgetProps): void {
  const key = JSON.stringify(props);
  if (key === lastRecent) return;
  try {
    loaded()?.recent.updateSnapshot(storable(props));
    lastRecent = key;
  } catch {
    // As above.
  }
}
