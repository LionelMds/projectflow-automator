import { useMemo } from 'react';

import { useAuth } from '@/lib/auth';
import { demoDrive, demoPlanner } from '@/lib/demoProjects';
import { graphDrive, type DriveGateway } from '@/lib/projects/drive';
import { graphPlanner, type PlannerGateway } from '@/lib/projects/planner';
import { useSettings } from '@/lib/settings';

export type ProjectsBackend = { drive: DriveGateway; planner: PlannerGateway; demo: boolean };

/** Microsoft Graph, or the in-memory demo drive and plan in « Mode démo ». */
export function useProjectsBackend(): ProjectsBackend {
  const { getAccessToken } = useAuth();
  const { settings } = useSettings();
  return useMemo(
    () =>
      settings.demo
        ? { drive: demoDrive(), planner: demoPlanner(), demo: true }
        : { drive: graphDrive(getAccessToken), planner: graphPlanner(getAccessToken), demo: false },
    [settings.demo, getAccessToken],
  );
}
