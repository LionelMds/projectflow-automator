import { sharingToken } from '@/lib/base64';
import { driveItemByPath, drivePathFromSetting, GraphError, graphRequest, type TokenSource } from '@/lib/graph';
import {
  buildSnapshot,
  planUpsert,
  rowAddress,
  sameCells,
  TABLE_WIDTH,
  tableRowAddress,
  tableRowIndexForSheetRow,
  WRITABLE_WIDTH,
  type Cell,
  type ProjectRowInput,
  type RepertoireRow,
  type RepertoireSnapshot,
  type UpsertPlan,
  type WorkbookTable,
} from '@/lib/repertoire/rows';

/** Where the répertoire workbook lives: a drive and an item of Microsoft Graph. */
export type WorkbookTarget = { driveId: string; itemId: string; name: string };

type DriveItem = {
  id: string;
  name?: string;
  parentReference?: { driveId?: string };
  remoteItem?: { id?: string; parentReference?: { driveId?: string } };
};

type UsedRange = { address: string; values: Cell[][]; text?: string[][] };

export class RepertoireError extends Error {}

/**
 * Find the workbook from what the settings hold: a OneDrive/SharePoint link (as the
 * desktop accepts in its settings) or a path in the signed-in account's OneDrive.
 */
export async function resolveWorkbook(token: TokenSource, location: string): Promise<WorkbookTarget> {
  const raw = location.trim();
  if (!raw) {
    throw new RepertoireError(
      'Répertoire chantier non configuré. Collez son lien OneDrive / SharePoint dans les paramètres.',
    );
  }
  const select = '$select=id,name,parentReference,remoteItem';
  const notFound = new RepertoireError('Classeur du répertoire introuvable. Vérifiez le lien dans les paramètres.');
  let item: DriveItem | null;
  try {
    item = raw.includes('://')
      ? await graphRequest<DriveItem>(token, `/shares/${sharingToken(raw)}/driveItem?${select}`, {
          headers: { Prefer: 'redeemSharingLink' },
        })
      : await driveItemByPath<DriveItem>(token, drivePathFromSetting(raw), select);
  } catch (exc) {
    if (exc instanceof GraphError && exc.status === 404) throw notFound;
    throw exc;
  }
  if (!item) throw notFound;
  const driveId = item.remoteItem?.parentReference?.driveId ?? item.parentReference?.driveId;
  const itemId = item.remoteItem?.id ?? item.id;
  if (!driveId || !itemId) throw new RepertoireError('Le lien du répertoire ne désigne pas un classeur.');
  return { driveId, itemId, name: item.name ?? 'Répertoire chantier' };
}

function worksheetPath(target: WorkbookTarget, year: number): string {
  const workbook = `/drives/${encodeURIComponent(target.driveId)}/items/${encodeURIComponent(target.itemId)}/workbook`;
  return `${workbook}/worksheets/${encodeURIComponent(String(year))}`;
}

async function usedRange(token: TokenSource, target: WorkbookTarget, year: number): Promise<UsedRange> {
  try {
    return await graphRequest<UsedRange>(
      token,
      `${worksheetPath(target, year)}/usedRange(valuesOnly=true)?$select=address,values,text`,
    );
  } catch (exc) {
    if (exc instanceof GraphError && (exc.status === 404 || exc.code === 'ItemNotFound')) {
      throw new RepertoireError(`Onglet introuvable dans le répertoire : ${year}`);
    }
    throw exc;
  }
}

export async function readSnapshot(
  token: TokenSource,
  target: WorkbookTarget,
  year: number,
): Promise<RepertoireSnapshot> {
  return buildSnapshot(year, await usedRange(token, target, year));
}

/**
 * Write A:E of one row, after reading it again: when the row changed in the shared
 * workbook since it was shown, nothing is written (`update_editable_row`).
 */
export async function saveRow(
  token: TokenSource,
  target: WorkbookTarget,
  year: number,
  row: RepertoireRow,
  values: Cell[],
): Promise<void> {
  const current = buildSnapshot(year, await usedRange(token, target, year));
  const now = current.rows.find((candidate) => candidate.sheetRow === row.sheetRow);
  if (!now || now.number !== row.number) {
    throw new RepertoireError("La ligne du répertoire n'existe plus. Actualisez le répertoire.");
  }
  if (!sameCells(now.values, row.values)) {
    throw new RepertoireError(
      'La ligne a été modifiée dans le fichier partagé. Actualisez le répertoire avant de recommencer.',
    );
  }
  if (values.length !== WRITABLE_WIDTH || String(values[0]) !== String(row.values[0])) {
    throw new RepertoireError('Le numéro du projet ne peut pas être modifié ici.');
  }
  await graphRequest(token, `${worksheetPath(target, year)}/range(address='${rowAddress(row.sheetRow)}')`, {
    method: 'PATCH',
    body: { values: [values] },
  });
}

type Session = Record<string, string>;

/** A workbook session for related calls (`GraphExcelWorkbookGateway.session`). */
async function withSession<T>(token: TokenSource, target: WorkbookTarget, run: (headers: Session) => Promise<T>): Promise<T> {
  const workbook = `/drives/${encodeURIComponent(target.driveId)}/items/${encodeURIComponent(target.itemId)}/workbook`;
  const opened = await graphRequest<{ id?: string }>(token, `${workbook}/createSession`, {
    method: 'POST',
    body: { persistChanges: true },
  });
  if (!opened?.id) {
    throw new RepertoireError("Microsoft Graph n'a pas ouvert de session Excel valide. Aucune écriture n'a été effectuée.");
  }
  const headers = { 'workbook-session-id': opened.id };
  let failed = false;
  try {
    return await run(headers);
  } catch (exc) {
    failed = true;
    throw exc;
  } finally {
    try {
      await graphRequest(token, `${workbook}/closeSession`, { method: 'POST', headers });
    } catch (exc) {
      if (!failed) {
        const detail = exc instanceof Error ? ` ${exc.message}` : '';
        throw new RepertoireError(
          `La fermeture de la session Excel a échoué. Des modifications peuvent déjà être enregistrées. Actualisez le répertoire avant de recommencer.${detail}`,
        );
      }
    }
  }
}

/** The répertoire table that holds a sheet row, when the rows are in an Excel table. */
async function findTable(
  token: TokenSource,
  target: WorkbookTarget,
  year: number,
  sheetRow: number,
  headers: Session,
): Promise<WorkbookTable | null> {
  const sheet = worksheetPath(target, year);
  const payload = await graphRequest<{ value?: { id?: string; name?: string; showHeaders?: boolean; showTotals?: boolean }[] }>(
    token,
    `${sheet}/tables`,
    { headers },
  );
  for (const raw of payload.value ?? []) {
    const id = raw.id ?? raw.name;
    if (!id) throw new RepertoireError('Microsoft Graph a retourné un tableau Excel sans identifiant.');
    const range = await graphRequest<{ rowIndex?: number; rowCount?: number; columnIndex?: number; columnCount?: number }>(
      token,
      `${sheet}/tables/${encodeURIComponent(id)}/range`,
      { headers },
    );
    if (
      typeof range.rowIndex !== 'number' ||
      typeof range.rowCount !== 'number' ||
      typeof range.columnIndex !== 'number' ||
      typeof range.columnCount !== 'number'
    ) {
      throw new RepertoireError("Microsoft Graph n'a pas retourné les dimensions du tableau Excel.");
    }
    const table: WorkbookTable = {
      id,
      rowIndex: range.rowIndex,
      rowCount: range.rowCount,
      columnIndex: range.columnIndex,
      columnCount: range.columnCount,
      showHeaders: raw.showHeaders ?? true,
      showTotals: raw.showTotals ?? false,
    };
    if (table.columnIndex !== 0 || table.columnCount < TABLE_WIDTH) continue;
    if (tableRowIndexForSheetRow(table, sheetRow) !== null) return table;
  }
  return null;
}

/**
 * Insert a blank line before `sheetRow` (`insert_blank_row`): a row added to the
 * répertoire's Excel table when it has one, which carries the table's format; else
 * A:L inserted with a shift down, then emptied.
 */
async function insertBlankRow(
  token: TokenSource,
  target: WorkbookTarget,
  year: number,
  sheetRow: number,
  headers: Session,
): Promise<void> {
  const sheet = worksheetPath(target, year);
  const table = await findTable(token, target, year, sheetRow, headers);
  const index = table ? tableRowIndexForSheetRow(table, sheetRow) : null;
  if (table && index !== null) {
    await graphRequest(token, `${sheet}/tables/${encodeURIComponent(table.id)}/rows/add`, {
      method: 'POST',
      body: { index, values: [Array(table.columnCount).fill('')] },
      headers,
    });
    return;
  }
  const address = tableRowAddress(sheetRow);
  await graphRequest(token, `${sheet}/range(address='${address}')/insert`, {
    method: 'POST',
    body: { shift: 'Down' },
    headers,
  });
  await graphRequest(token, `${sheet}/range(address='${address}')`, {
    method: 'PATCH',
    body: { values: [Array(TABLE_WIDTH).fill('')] },
    headers,
  });
}

/**
 * Write a created or updated project into the répertoire (`upsert_project`), in one
 * workbook session: the worksheet is read again, the write planned on what it holds now.
 */
export async function upsertProjectRow(
  token: TokenSource,
  target: WorkbookTarget,
  year: number,
  project: ProjectRowInput,
  options: { today: Date; forceOverwrite: boolean },
): Promise<UpsertPlan> {
  return withSession(token, target, async (headers) => {
    const sheet = worksheetPath(target, year);
    try {
      await graphRequest(token, `${sheet}?$select=name`, { headers });
    } catch (exc) {
      if (exc instanceof GraphError && exc.status === 404) {
        throw new RepertoireError(`Onglet introuvable dans le répertoire : ${year}`);
      }
      throw exc;
    }
    const range = await graphRequest<UsedRange>(token, `${sheet}/usedRange(valuesOnly=true)?$select=address,values`, {
      headers,
    });
    const plan = planUpsert(range, project, options);
    if (plan.kind === 'insert') await insertBlankRow(token, target, year, plan.sheetRow, headers);
    await graphRequest(token, `${sheet}/range(address='${rowAddress(plan.sheetRow)}')`, {
      method: 'PATCH',
      body: { values: [plan.values] },
      headers,
    });
    return plan;
  });
}
