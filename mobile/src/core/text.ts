/** Compare les noms comme un utilisateur Windows les tape : sans accents ni casse. */
export function folderKey(name: string): string {
  return name
    .trim()
    .normalize('NFKD')
    .replace(/\p{M}/gu, '')
    .toLowerCase();
}

/** Clé de comparaison des clients (espaces compactés, sans accents ni casse). */
export function clientKey(value: string): string {
  return folderKey(value.split(/\s+/).filter(Boolean).join(' '));
}

export function cellText(value: unknown): string {
  return value == null ? '' : String(value).trim();
}

export const pad2 = (n: number) => String(n).padStart(2, '0');

/** « 30.09.2026 », format utilisé par la fiche et le répertoire. */
export function swissDate(d: Date): string {
  return `${pad2(d.getDate())}.${pad2(d.getMonth() + 1)}.${d.getFullYear()}`;
}

export function hhmm(d: Date): string {
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

/** Numéro de série Excel (jours depuis le 30.12.1899) d'une date locale. */
export function excelSerial(d: Date): number {
  const utc = Date.UTC(d.getFullYear(), d.getMonth(), d.getDate());
  return Math.round((utc - Date.UTC(1899, 11, 30)) / 86_400_000);
}

export function excelColumn(width: number): string {
  let name = '';
  let n = width;
  while (n > 0) {
    const r = (n - 1) % 26;
    name = String.fromCharCode(65 + r) + name;
    n = Math.floor((n - 1) / 26);
  }
  return name;
}

/** « 48,2 Ko · 24.09.2026 14:12 » */
export function fileMeta(size: number, iso: string): string {
  const ko = size / 1024;
  const sizeText = ko >= 1024 ? `${(ko / 1024).toFixed(1).replace('.', ',')} Mo` : `${ko.toFixed(1).replace('.', ',')} Ko`;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? sizeText : `${sizeText} · ${swissDate(d)} ${hhmm(d)}`;
}

/** Initiales d'un nom : « Lionel Martin » → « LM ». */
export function initialsOf(name: string, email = ''): string {
  const parts = name.replace(/[<(].*$/, '').split(/[\s.\-_]+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return email.slice(0, 2).toUpperCase();
}

export function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message.trim() || error.name;
  return String(error);
}
