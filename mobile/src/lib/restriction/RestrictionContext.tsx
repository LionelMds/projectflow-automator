import * as Crypto from 'expo-crypto';
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

import { makeLock, parseLock, passwordMatches, passwordProblem, type Lock } from '@/lib/restriction/restriction';
import { readItem, removeItem, writeItem } from '@/lib/storage';

const STORAGE_KEY = 'projectflow.acces-limite';

const sha256 = (text: string) => Crypto.digestStringAsync(Crypto.CryptoDigestAlgorithm.SHA256, text);

type RestrictionValue = {
  /** « Accès limité » is on for this phone. */
  restricted: boolean;
  /** Turn it on with a new password; resolves to why not, or ''. */
  restrict: (password: string, confirmation: string) => Promise<string>;
  /** Turn it off; resolves to false when the password is not the one set. */
  unrestrict: (password: string) => Promise<boolean>;
};

const RestrictionContext = createContext<RestrictionValue | null>(null);

/**
 * Holds the app until the lock is read, so that a limited phone never shows, even for a moment,
 * the screens it hides.
 */
export function RestrictionProvider({ children }: { children: ReactNode }) {
  const [lock, setLock] = useState<Lock | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let alive = true;
    readItem(STORAGE_KEY)
      .then((raw) => {
        if (alive) setLock(parseLock(raw));
      })
      .catch(() => undefined)
      .finally(() => {
        if (alive) setLoaded(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  const restrict = useCallback(async (password: string, confirmation: string) => {
    const problem = passwordProblem(password, confirmation);
    if (problem) return problem;
    const next = await makeLock(password, Crypto.randomUUID(), sha256);
    await writeItem(STORAGE_KEY, JSON.stringify(next));
    setLock(next);
    return '';
  }, []);

  const unrestrict = useCallback(
    async (password: string) => {
      if (!lock) return true;
      if (!(await passwordMatches(lock, password, sha256))) return false;
      await removeItem(STORAGE_KEY);
      setLock(null);
      return true;
    },
    [lock],
  );

  const value = useMemo(() => ({ restricted: lock !== null, restrict, unrestrict }), [lock, restrict, unrestrict]);
  if (!loaded) return null;
  return <RestrictionContext.Provider value={value}>{children}</RestrictionContext.Provider>;
}

export function useRestriction(): RestrictionValue {
  const context = useContext(RestrictionContext);
  if (!context) throw new Error('useRestriction hors de RestrictionProvider');
  return context;
}
