// Classeurs Excel via l'API workbook de Microsoft Graph (repris de graph/excel.py).
import type { DriveItem, UsedRange, Workbook, WorkbookGateway } from '../core/gateways';
import { excelColumn } from '../core/text';
import { graph, GraphError, odata, seg } from './client';

interface RangePayload {
  address?: string;
  rowIndex?: number;
  values?: unknown[][];
  text?: string[][];
}

class GraphWorkbook implements Workbook {
  private sessionId: string | null = null;
  private depth = 0;
  private queue: Promise<unknown> = Promise.resolve();

  constructor(private item: DriveItem) {}

  private get base() {
    return `/drives/${seg(this.item.driveId)}/items/${seg(this.item.id)}/workbook`;
  }
  private sheet(name: string) {
    return `${this.base}/worksheets/${seg(name)}`;
  }
  private headers(): Record<string, string> {
    return this.sessionId ? { 'workbook-session-id': this.sessionId } : {};
  }

  /** Une session par opération (lectures et écritures liées), fermée même en cas d'erreur. */
  session<T>(operation: () => Promise<T>): Promise<T> {
    if (this.depth > 0) return operation();
    const run = async () => {
      const created = await graph<{ id?: string }>('POST', `${this.base}/createSession`, { body: { persistChanges: true } });
      if (!created.id) throw new GraphError('Microsoft Graph n’a pas ouvert de session Excel valide. Aucune écriture n’a été effectuée.');
      this.sessionId = created.id;
      this.depth++;
      let failure: unknown = null;
      try {
        return await operation();
      } catch (error) {
        failure = error;
        throw error;
      } finally {
        const headers = this.headers();
        this.depth--;
        this.sessionId = null;
        if (!(failure instanceof GraphError && failure.invalidSession)) {
          await graph('POST', `${this.base}/closeSession`, { headers }).catch((closeError) => {
            if (!failure) {
              throw new GraphError('La fermeture de la session Excel a échoué. Des modifications peuvent déjà être enregistrées. Actualisez avant de recommencer. ' + String(closeError?.message ?? ''));
            }
          });
        }
      }
    };
    const next = this.queue.then(run, run);
    this.queue = next.catch(() => undefined);
    return next;
  }

  async worksheetNames(): Promise<string[]> {
    const r = await graph<{ value?: { name: string; position: number; visibility?: string }[] }>('GET', `${this.base}/worksheets?$select=name,position,visibility`, { headers: this.headers() });
    return (r.value ?? [])
      .filter((w) => !w.visibility || w.visibility === 'Visible')
      .sort((a, b) => a.position - b.position)
      .map((w) => w.name);
  }

  async usedRange(sheet: string): Promise<UsedRange> {
    const r = await graph<RangePayload>('GET', `${this.sheet(sheet)}/usedRange(valuesOnly=true)?$select=address,rowIndex,values,text`, { headers: this.headers() });
    if (!Array.isArray(r.values)) throw new GraphError('Microsoft Graph n’a pas retourné les lignes du classeur Excel.');
    return { startRow: r.rowIndex ?? 0, values: r.values, text: r.text ?? r.values.map((row) => row.map((v) => (v == null ? '' : String(v)))) };
  }

  async readRange(sheet: string, address: string) {
    const r = await graph<RangePayload>('GET', `${this.sheet(sheet)}/range(address='${odata(address)}')?$select=values,text`, { headers: this.headers() });
    return { values: r.values ?? [], text: r.text ?? [] };
  }

  async updateRange(sheet: string, address: string, values: unknown[][], numberFormat?: string[][]) {
    await graph('PATCH', `${this.sheet(sheet)}/range(address='${odata(address)}')`, {
      body: numberFormat ? { values, numberFormat } : { values },
      headers: this.headers(),
    });
  }

  async insertBlankRow(sheet: string, rowIndex: number, width: number) {
    // Si la ligne tombe dans un tableau Excel, on ajoute la ligne au tableau pour garder sa mise en forme.
    const target = rowIndex + 1;
    const tables = await graph<{ value?: { id?: string; name?: string; showHeaders?: boolean; showTotals?: boolean }[] }>('GET', `${this.sheet(sheet)}/tables`, { headers: this.headers() });
    for (const t of tables.value ?? []) {
      const id = t.id ?? t.name;
      if (!id) continue;
      const range = await graph<{ rowIndex: number; rowCount: number; columnIndex: number; columnCount: number }>('GET', `${this.sheet(sheet)}/tables/${seg(id)}/range`, { headers: this.headers() });
      if (range.columnIndex !== 0 || range.columnCount < width) continue;
      const header = t.showHeaders === false ? 0 : 1;
      const totals = t.showTotals ? 1 : 0;
      const dataStart = range.rowIndex + 1 + header;
      const dataEnd = dataStart + Math.max(0, range.rowCount - header - totals) - 1;
      if (target < dataStart || target > dataEnd + 1) continue;
      await graph('POST', `${this.sheet(sheet)}/tables/${seg(id)}/rows/add`, {
        body: { index: target - dataStart, values: [Array(range.columnCount).fill('')] },
        headers: this.headers(),
      });
      return;
    }
    const address = `A${target}:${excelColumn(width)}${target}`;
    await graph('POST', `${this.sheet(sheet)}/range(address='${odata(address)}')/insert`, { body: { shift: 'Down' }, headers: this.headers() });
    await this.updateRange(sheet, address, [Array(width).fill('')]);
  }
}

export const graphWorkbooks: WorkbookGateway = {
  open: (item) => new GraphWorkbook(item),
};
