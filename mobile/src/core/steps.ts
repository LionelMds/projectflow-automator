// Exécution séquentielle d'étapes affichées dans la feuille de progression.
import { errorMessage } from './text';

export type StepStatus = 'wait' | 'run' | 'done' | 'warn' | 'error' | 'skip';

export interface StepState {
  label: string;
  status: StepStatus;
  detail?: string;
}

export interface Step {
  label: string;
  /** Une intégration (Outlook, Planner, e-mail, CAO) n'annule jamais le projet : l'erreur est notée et on continue. */
  isolated?: boolean;
  run: () => Promise<string | void | { warn: string }>;
}

export interface RunOutcome {
  states: StepState[];
  failed: boolean;
  warnings: number;
}

export async function runSteps(steps: Step[], onChange: (states: StepState[]) => void): Promise<RunOutcome> {
  const states: StepState[] = steps.map((s) => ({ label: s.label, status: 'wait' }));
  const emit = () => onChange(states.map((s) => ({ ...s })));
  emit();
  let failed = false;
  let warnings = 0;
  for (let i = 0; i < steps.length; i++) {
    if (failed) {
      states[i].status = 'skip';
      continue;
    }
    states[i].status = 'run';
    emit();
    try {
      const result = await steps[i].run();
      if (result && typeof result === 'object') {
        states[i] = { ...states[i], status: 'warn', detail: result.warn };
        warnings++;
      } else {
        states[i] = { ...states[i], status: 'done', label: typeof result === 'string' && result ? result : states[i].label };
      }
    } catch (error) {
      states[i] = { ...states[i], status: 'error', detail: errorMessage(error) };
      if (steps[i].isolated) warnings++;
      else failed = true;
    }
    emit();
  }
  emit();
  return { states, failed, warnings };
}
