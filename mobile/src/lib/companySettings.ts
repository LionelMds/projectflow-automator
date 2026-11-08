/**
 * Balz Métal's settings, the same places as on the poste: filled in after a Microsoft sign-in,
 * and only those the signed-in account can reach, so that a colleague has nothing to paste.
 * A field already filled is never replaced. The initials stay each person's own.
 */

import type { Settings } from '@/lib/settings';

export const COMPANY_SETTINGS = {
  repertoire: 'Entreprise/REPERTOIR CHANTIER ET FACTURES.xlsx',
  racine: 'Clients',
  reference: 'Entreprise/00-Bibliothèque CAO/10-Modèles et documents/10-Racine dossier',
} as const;

export type CompanyField = keyof typeof COMPANY_SETTINGS;

export const COMPANY_LABELS: Record<CompanyField, string> = {
  repertoire: 'Répertoire chantier',
  racine: 'Racine projets',
  reference: 'Dossier de référence',
};

export const COMPANY_FIELDS = Object.keys(COMPANY_SETTINGS) as CompanyField[];

/** The company fields still empty in `settings`. */
export function emptyCompanyFields(settings: Settings): CompanyField[] {
  return COMPANY_FIELDS.filter((field) => !settings[field].trim());
}

/**
 * The company values for the empty fields, each kept only once `reachable` has found it with
 * the signed-in account; the others are named in `missing`.
 */
export async function companySettingsFor(
  settings: Settings,
  reachable: (field: CompanyField, value: string) => Promise<boolean>,
): Promise<{ filled: Partial<Pick<Settings, CompanyField>>; missing: CompanyField[] }> {
  const filled: Partial<Pick<Settings, CompanyField>> = {};
  const missing: CompanyField[] = [];
  for (const field of emptyCompanyFields(settings)) {
    const value = COMPANY_SETTINGS[field];
    let found = false;
    try {
      found = await reachable(field, value);
    } catch {
      found = false;
    }
    if (found) filled[field] = value;
    else missing.push(field);
  }
  return { filled, missing };
}
