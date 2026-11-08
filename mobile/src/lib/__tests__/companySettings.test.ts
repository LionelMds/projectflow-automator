import { COMPANY_SETTINGS, companySettingsFor, emptyCompanyFields } from '@/lib/companySettings';
import { DEFAULT_SETTINGS } from '@/lib/settings';

describe('réglages de l’entreprise', () => {
  it('fills the empty fields the signed-in account reaches, and only them', async () => {
    const checked: string[] = [];
    const result = await companySettingsFor({ ...DEFAULT_SETTINGS, racine: 'Mes projets' }, async (field) => {
      checked.push(field);
      return field !== 'reference';
    });
    // A field already filled is neither checked nor replaced.
    expect(checked).toEqual(['repertoire', 'reference']);
    expect(result.filled).toEqual({ repertoire: COMPANY_SETTINGS.repertoire });
    expect(result.missing).toEqual(['reference']);
  });

  it('counts a failed check as not found', async () => {
    const result = await companySettingsFor(DEFAULT_SETTINGS, async (field) => {
      if (field === 'racine') throw new Error('Réseau indisponible');
      return true;
    });
    expect(Object.keys(result.filled)).toEqual(['repertoire', 'reference']);
    expect(result.missing).toEqual(['racine']);
  });

  it('has nothing to do once everything is set', async () => {
    const full = { ...DEFAULT_SETTINGS, repertoire: 'a', racine: 'b', reference: 'c' };
    expect(emptyCompanyFields(full)).toEqual([]);
    const result = await companySettingsFor(full, async () => true);
    expect(result).toEqual({ filled: {}, missing: [] });
  });
});
