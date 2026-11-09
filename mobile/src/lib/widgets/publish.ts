/**
 * Android and the web have no ProjectFlow widget: nothing is sent.
 * (`publish.ios.ts` sends them to the iPhone's home screen.)
 */

import type { PlanningWidgetProps, RecentWidgetProps, WidgetChoiceOption } from '@/widgets/types';

export const WIDGETS_AVAILABLE = false;

export function publishPlanning(_entries: { date: Date; props: PlanningWidgetProps }[]): void {}

export function publishPlanningChoices(_options: WidgetChoiceOption[]): void {}

export function publishRecent(_props: RecentWidgetProps): void {}
