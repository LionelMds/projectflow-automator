/** What a file of a dossier is, for its icon and the app that opens it. */

export type FileKind = 'excel' | 'word' | 'powerpoint' | 'pdf' | 'image' | 'cad' | 'text' | 'other';

const KINDS: Record<string, FileKind> = {
  xls: 'excel',
  xlsx: 'excel',
  xlsm: 'excel',
  xlsb: 'excel',
  doc: 'word',
  docx: 'word',
  docm: 'word',
  ppt: 'powerpoint',
  pptx: 'powerpoint',
  pptm: 'powerpoint',
  pdf: 'pdf',
  jpg: 'image',
  jpeg: 'image',
  png: 'image',
  heic: 'image',
  gif: 'image',
  bmp: 'image',
  tif: 'image',
  tiff: 'image',
  webp: 'image',
  dwg: 'cad',
  dxf: 'cad',
  sldprt: 'cad',
  sldasm: 'cad',
  slddrw: 'cad',
  step: 'cad',
  stp: 'cad',
  txt: 'text',
  csv: 'text',
  msg: 'text',
  eml: 'text',
};

/** Office URI schemes: the file opens in its app, for editing, on OneDrive itself. */
const OFFICE_SCHEMES: Partial<Record<FileKind, string>> = {
  excel: 'ms-excel',
  word: 'ms-word',
  powerpoint: 'ms-powerpoint',
};

export function extensionOf(name: string): string {
  const dot = name.lastIndexOf('.');
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : '';
}

export function fileKind(name: string): FileKind {
  return KINDS[extensionOf(name)] ?? 'other';
}

/** `ms-excel:ofe|u|<address>` for an Office file, else null. */
export function officeLink(name: string, address: string): string | null {
  const scheme = OFFICE_SCHEMES[fileKind(name)];
  return scheme && address ? `${scheme}:ofe|u|${address}` : null;
}

/** « Excel », « Word »… : the app a file opens in, for the menus. */
export function openerName(name: string): string {
  const kind = fileKind(name);
  if (kind === 'excel') return 'Excel';
  if (kind === 'word') return 'Word';
  if (kind === 'powerpoint') return 'PowerPoint';
  return 'Aperçu';
}

/** A name the phone accepts for a downloaded copy. */
export function localName(name: string): string {
  const cleaned = name.replace(/[\\/:*?"<>|]/g, '-').trim();
  return cleaned || 'fichier';
}

/** « 245 Ko », « 3,4 Mo ». */
export function sizeText(bytes: number): string {
  if (!bytes) return '';
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} Ko`;
  return `${(bytes / (1024 * 1024)).toFixed(1).replace('.', ',')} Mo`;
}

/** « 03.11.2026 » from an ISO date. */
export function dateText(iso: string): string {
  const date = new Date(iso);
  if (!iso || Number.isNaN(date.getTime())) return '';
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${pad(date.getDate())}.${pad(date.getMonth() + 1)}.${date.getFullYear()}`;
}
