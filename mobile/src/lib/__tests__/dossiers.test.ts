import { dossierCache, dossierDesignation, resolveDossierNumber, withDossiers } from '@/lib/planning/dossiers';
import type { PlanningData, PlanningTask } from '@/lib/planning/models';

const projects = new Map([
  ['2026-4996', { number: '2026-4996', client: 'Garage du Lac', contact: 'P. Rochat', designation: 'Structure auvent parking' }],
  ['2025-4996', { number: '2025-4996', client: 'Ancien', designation: 'Ancien dossier' }],
  ['2026-5231', { number: '2026-5231', client: 'Commune', designation: 'Main courante' }],
]);

function data(tasks: PlanningTask[]): PlanningData {
  return { tasks, resources: [], absences: [], meta: null };
}

describe('dossier des tâches (planning_controller.py)', () => {
  it('copies the répertoire details into a task', () => {
    expect(dossierCache(projects.get('2026-4996')!, new Date(Date.UTC(2026, 10, 8, 12)))).toEqual({
      client: 'Garage du Lac',
      contact: 'P. Rochat',
      designation: 'Structure auvent parking',
      managed_by: '',
      read_at: '2026-11-08T12:00:00Z',
    });
  });

  it('shows the répertoire designation and client of a task that has none', () => {
    const bare = { id: 'a', project_number: '2026-4996', title: 'Soudure' } as PlanningTask;
    const own = { id: 'b', project_number: '2026-4996', project_cache: { designation: 'Gardée', client: 'Gardé' } } as PlanningTask;
    const shown = withDossiers(data([bare, own]), projects);
    expect(shown.tasks[0].project_cache).toEqual({ designation: 'Structure auvent parking', client: 'Garage du Lac', contact: 'P. Rochat' });
    expect(shown.tasks[1]).toBe(own);
    // Nothing to fill: the same data, for the memoised board.
    const nothing = data([own]);
    expect(withDossiers(nothing, projects)).toBe(nothing);
  });

  it('completes a short number when one dossier ends with it', () => {
    expect(resolveDossierNumber('5231', projects)).toBe('2026-5231');
    // Two years have a 4996: the number stays as typed.
    expect(resolveDossierNumber('4996', projects)).toBe('4996');
    expect(resolveDossierNumber(' 2026-4996 ', projects)).toBe('2026-4996');
  });

  it('reads the designation from the répertoire, else from the task', () => {
    expect(dossierDesignation({ id: 'a', project_number: '2026-4996' } as PlanningTask, projects)).toBe('Structure auvent parking');
    expect(dossierDesignation({ id: 'a', project_number: '2026-9999', project_cache: { designation: 'Copie' } } as PlanningTask, projects)).toBe(
      'Copie',
    );
    expect(dossierDesignation({ id: 'a' } as PlanningTask, projects)).toBe('');
  });
});
