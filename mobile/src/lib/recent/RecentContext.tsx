import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import { useAuth } from '@/lib/auth';
import { canonicalProjectNumber, isProjectNumber } from '@/lib/projects/numbers';
import { parseRecent, withDirectory, withRecent, type RecentDossier } from '@/lib/recent/recent';
import { useRepertoire } from '@/lib/repertoire/RepertoireContext';
import { useSettings } from '@/lib/settings';
import { readItem, writeItem } from '@/lib/storage';

const RECENT_KEY = 'projectflow.recent-dossiers';

type RecentValue = {
  /** Latest first, with the répertoire's designation and client where it knows them. */
  recent: RecentDossier[];
  /** The phone's list has been read (before, `recent` only holds what was opened since). */
  ready: boolean;
  /** Puts a dossier at the top of the list (any number the app opens). */
  remember: (number: string) => void;
};

const RecentContext = createContext<RecentValue | null>(null);

export function RecentProvider({ children }: { children: ReactNode }) {
  const { settings, loaded } = useSettings();
  const { status } = useAuth();
  const { projects, wantProjects } = useRepertoire();
  const [stored, setStored] = useState<RecentDossier[]>([]);
  const [read, setRead] = useState(false);
  const list = useRef<RecentDossier[]>([]);
  const known = useRef(projects);

  useEffect(() => {
    known.current = projects;
  }, [projects]);

  useEffect(() => {
    let alive = true;
    void readItem(RECENT_KEY).then((text) => {
      if (!alive) return;
      // What was opened before the phone's list was read stays on top of it.
      const merged = [...list.current].reverse().reduce((acc, item) => withRecent(acc, item), parseRecent(text));
      list.current = merged;
      setStored(merged);
      setRead(true);
    });
    return () => {
      alive = false;
    };
  }, []);

  const remember = useCallback((raw: string) => {
    const text = raw.trim();
    const number = isProjectNumber(text) ? canonicalProjectNumber(text) : text;
    if (!number) return;
    const project = known.current.get(number);
    const next = withRecent(list.current, { number, designation: project?.designation ?? '', client: project?.client ?? '' });
    list.current = next;
    setStored(next);
    void writeItem(RECENT_KEY, JSON.stringify(next)).catch(() => undefined);
  }, []);

  // Their designations come from the répertoire, read for these years when it can be.
  const ready = loaded && (settings.demo || status === 'signedIn');
  const numbers = stored.map((item) => item.number).join(' ');
  useEffect(() => {
    if (ready && numbers) void wantProjects(numbers.split(' '));
  }, [ready, numbers, wantProjects]);

  const recent = useMemo(() => withDirectory(stored, projects), [stored, projects]);

  // The details found are kept, for the next start before the répertoire is read.
  useEffect(() => {
    if (JSON.stringify(recent) === JSON.stringify(list.current)) return;
    list.current = recent;
    void writeItem(RECENT_KEY, JSON.stringify(recent)).catch(() => undefined);
  }, [recent]);

  const value = useMemo(() => ({ recent, ready: read, remember }), [recent, read, remember]);
  return <RecentContext.Provider value={value}>{children}</RecentContext.Provider>;
}

export function useRecentDossiers(): RecentValue {
  const context = useContext(RecentContext);
  if (!context) throw new Error('useRecentDossiers hors de RecentProvider');
  return context;
}
