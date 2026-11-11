import {
  allowedActions,
  isOpenFolder,
  makeLock,
  parseLock,
  passwordMatches,
  passwordProblem,
} from '@/lib/restriction/restriction';

// The app hashes with expo-crypto's SHA-256; any digest shows how the lock uses it (FNV-1a here).
const sha256 = async (text: string) => {
  let hash = 0x811c9dc5;
  for (const char of text) {
    hash ^= char.codePointAt(0) ?? 0;
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, '0');
};

describe('accès limité', () => {
  it('keeps only the Photos and Plans folders, whatever their case or accents', () => {
    const items = [
      { name: 'Photos', folder: true },
      { name: 'plans', folder: true },
      { name: ' PLANS ', folder: true },
      { name: 'Plans', folder: false },
      { name: 'Plan', folder: true },
      { name: 'Photos chantier', folder: true },
      { name: 'Devis', folder: true },
      { name: 'Fiche.xlsx', folder: false },
    ];
    expect(items.filter(isOpenFolder).map((item) => item.name)).toEqual(['Photos', 'plans', ' PLANS ']);
  });

  it('keeps the folder, planning and number actions only once limited', () => {
    const all = ['folder', 'fiche', 'repertoire', 'creation', 'task', 'planning', 'copy'] as const;
    expect(allowedActions(all, false)).toEqual([...all]);
    expect(allowedActions(all, true)).toEqual(['folder', 'planning', 'copy']);
    expect(allowedActions(['fiche', 'creation'], true)).toEqual([]);
  });

  it('asks for a password of four characters at least, typed twice the same', () => {
    expect(passwordProblem('abc', 'abc')).toMatch(/au moins 4/);
    expect(passwordProblem('chantier', 'chantiers')).toMatch(/pas les mêmes/);
    expect(passwordProblem('chantier', 'chantier')).toBe('');
  });

  it('stores a salted hash, never the password, and checks the password against it', async () => {
    const lock = await makeLock('Balz 2026', 'sel-1', sha256);
    expect(lock.salt).toBe('sel-1');
    expect(JSON.stringify(lock)).not.toContain('Balz 2026');
    expect(lock.hash).toBe(await sha256('sel-1:Balz 2026'));
    expect(await passwordMatches(lock, 'Balz 2026', sha256)).toBe(true);
    expect(await passwordMatches(lock, 'balz 2026', sha256)).toBe(false);
    expect(await passwordMatches(lock, '', sha256)).toBe(false);
    // The same password with another salt gives another hash.
    expect((await makeLock('Balz 2026', 'sel-2', sha256)).hash).not.toBe(lock.hash);
  });

  it('reads a stored lock, and no lock from nothing or from damaged data', () => {
    expect(parseLock(JSON.stringify({ salt: 's', hash: 'h' }))).toEqual({ salt: 's', hash: 'h' });
    expect(parseLock(null)).toBeNull();
    expect(parseLock('')).toBeNull();
    expect(parseLock('{')).toBeNull();
    expect(parseLock(JSON.stringify({ salt: 's' }))).toBeNull();
    expect(parseLock(JSON.stringify({ salt: '', hash: 'h' }))).toBeNull();
  });
});
