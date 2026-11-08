/**
 * Where a dossier lives: `<racine projets>/<year>/<YYYY-NNNN>`, a subproject in its
 * parent's folder (`ProjectService._project_dir` / `project_folder_name`).
 */

import { findChildFolder, type DriveGateway, type DriveItem } from '@/lib/projects/drive';
import { projectFolderName, type ProjectNumber } from '@/lib/projects/numbers';

export const RACINE_LABEL = 'Racine projets';
export const REFERENCE_LABEL = 'Dossier de référence';

export type ProjectLocation = {
  racine: DriveItem;
  yearFolder: DriveItem | null;
  projectFolder: DriveItem | null;
  folderName: string;
  /** « Projets/2026/2026-4995 », for the journal. */
  projectPath: string;
  yearPath: string;
};

export async function locateProject(drive: DriveGateway, racineSetting: string, number: ProjectNumber): Promise<ProjectLocation> {
  const racine = await drive.resolve(racineSetting, RACINE_LABEL);
  const folderName = projectFolderName(number);
  const yearName = String(number.year);
  const yearFolder = findChildFolder(await drive.children(racine), yearName);
  const projectFolder = yearFolder ? findChildFolder(await drive.children(yearFolder), folderName) : null;
  const yearPath = `${racine.name}/${yearFolder?.name ?? yearName}`;
  return {
    racine,
    yearFolder,
    projectFolder,
    folderName,
    yearPath,
    projectPath: `${yearPath}/${projectFolder?.name ?? folderName}`,
  };
}
