// Contrats des intégrations Microsoft 365. Implémentés par Microsoft Graph (src/graph)
// et par une version en mémoire pour le mode démo et les tests (src/demo).

export interface DriveItem {
  driveId: string;
  id: string;
  name: string;
  isFolder: boolean;
  size: number;
  lastModified: string; // ISO
  webUrl: string;
}

export interface UserInfo {
  id: string;
  displayName: string;
  email: string;
}

export interface DriveGateway {
  /** Lien de partage OneDrive/SharePoint, ou chemin relatif à « Mon OneDrive ». */
  resolve(reference: string): Promise<DriveItem>;
  listChildren(folder: DriveItem): Promise<DriveItem[]>;
  /** Crée le dossier, ou renvoie celui qui existe déjà sous ce nom. */
  ensureFolder(parent: DriveItem, name: string): Promise<DriveItem>;
  /** Copie un fichier sous un nouveau nom ; échoue si la destination existe déjà. */
  copyFile(source: DriveItem, destination: DriveItem, name: string): Promise<DriveItem>;
  rename(item: DriveItem, name: string): Promise<DriveItem>;
  /** Supprime vers la corbeille OneDrive/SharePoint. */
  remove(item: DriveItem): Promise<void>;
  thumbnailUrl(item: DriveItem): Promise<string | null>;
  pdf(item: DriveItem): Promise<Blob>;
}

export interface UsedRange {
  /** Ligne Excel (base 0) de la première ligne renvoyée. */
  startRow: number;
  values: unknown[][];
  text: string[][];
}

export interface Workbook {
  session<T>(operation: () => Promise<T>): Promise<T>;
  worksheetNames(): Promise<string[]>;
  usedRange(sheet: string): Promise<UsedRange>;
  readRange(sheet: string, address: string): Promise<{ values: unknown[][]; text: string[][] }>;
  updateRange(sheet: string, address: string, values: unknown[][], numberFormat?: string[][]): Promise<void>;
  /** Insère une ligne vide à la ligne Excel (base 0) donnée et décale le reste vers le bas. */
  insertBlankRow(sheet: string, rowIndex: number, width: number): Promise<void>;
}

export interface WorkbookGateway {
  open(item: DriveItem): Workbook;
}

export interface PlannerPlan { id: string; title: string }
export interface PlannerBucket { id: string; name: string }
export interface PlannerMember { id: string; displayName: string; email: string }
export interface PlannerTask {
  id: string;
  title: string;
  bucketId: string;
  etag: string;
  assignments: string[];
  dueDateTime: string;
}

export interface PlannerGateway {
  listPlans(): Promise<PlannerPlan[]>;
  listBuckets(planId: string): Promise<PlannerBucket[]>;
  listMembers(planId: string): Promise<PlannerMember[]>;
  listTasks(planId: string): Promise<PlannerTask[]>;
  createTask(task: { planId: string; bucketId: string; title: string; assignments: string[]; dueDateTime: string }): Promise<PlannerTask>;
  patchTask(task: PlannerTask, patch: { title?: string; bucketId?: string; addAssignments?: string[]; dueDateTime?: string }): Promise<void>;
  deleteTask(task: PlannerTask): Promise<void>;
  taskUrl(task: { id: string }, planId: string): string;
}

export interface MailFolder { id: string; displayName: string }

export interface MailGateway {
  send(message: { to: { name: string; email: string }[]; subject: string; html: string }): Promise<void>;
  childFolders(parentId: string | null): Promise<MailFolder[]>;
  createFolder(parentId: string | null, name: string): Promise<MailFolder>;
  deleteFolder(id: string): Promise<void>;
}

export interface Gateways {
  kind: 'graph' | 'demo';
  me(): Promise<UserInfo>;
  drive: DriveGateway;
  workbooks: WorkbookGateway;
  planner: PlannerGateway;
  mail: MailGateway;
}
