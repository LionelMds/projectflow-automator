import { instantDay } from '@/lib/planning/instant';
import { ABSENCE_LABELS, type PlanningData } from '@/lib/planning/models';
import { parseDate, weekday } from '@/lib/planning/walltime';
import type { When } from '@/lib/planning/when';

/** Who is away at some point of `when`, and why (an absence, or a day off every week). */
export function awayOver(data: PlanningData, when: When): Map<string, string> {
  const reasons = new Map<string, string>();
  const first = instantDay(when.start);
  const last = instantDay(when.end - 1);
  for (const record of data.absences) {
    if (record.deleted_at) continue;
    const from = parseDate(record.first_day);
    const to = parseDate(record.last_day);
    if (from === null || to === null || from > last || to < first) continue;
    reasons.set(record.resource_id, ABSENCE_LABELS[record.reason ?? 'absent'] ?? 'Absent');
  }
  for (const resource of data.resources) {
    for (const off of resource.recurring_off ?? []) {
      for (let day = first; day <= last; day += 1) {
        if (weekday(day) === off.weekday && !reasons.has(resource.id)) reasons.set(resource.id, ABSENCE_LABELS[off.reason ?? 'ecole'] ?? 'Absent');
      }
    }
  }
  return reasons;
}
