// Suggestions société/contact tirées du répertoire (repris de core/client_directory.py).
import type { RepertoireRow } from './repertoire';
import { clientKey } from './text';

export interface ClientRecord {
  societe: string;
  contact: string;
}

export function clientRecords(rows: RepertoireRow[]): ClientRecord[] {
  const companies = new Map<string, string>();
  const contacts = new Map<string, string>();
  const seen = new Set<string>();
  const out: ClientRecord[] = [];
  for (const row of rows) {
    let societe = (row.text[2] ?? '').split(/\s+/).filter(Boolean).join(' ');
    let contact = (row.text[3] ?? '').split(/\s+/).filter(Boolean).join(' ');
    const ck = clientKey(societe);
    const pk = clientKey(contact);
    if (ck) societe = companies.get(ck) ?? (companies.set(ck, societe), societe);
    if (pk) contact = contacts.get(pk) ?? (contacts.set(pk, contact), contact);
    const key = ck + '\u0000' + pk;
    if ((!ck && !pk) || seen.has(key)) continue;
    seen.add(key);
    out.push({ societe, contact });
  }
  return out;
}

/** Jusqu'à trois sociétés contenant la saisie (dès 2 caractères), avec leur contact le plus récent. */
export function suggestClients(records: ClientRecord[], query: string, limit = 3): ClientRecord[] {
  const q = clientKey(query);
  if (q.length < 2) return [];
  const byCompany = new Map<string, ClientRecord>();
  for (const r of records) {
    const k = clientKey(r.societe);
    if (!k || !k.includes(q) || r.societe === query) continue;
    byCompany.set(k, r);
  }
  return [...byCompany.values()].slice(0, limit);
}
