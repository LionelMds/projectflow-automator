import { useEffect, useState } from 'react';
import { errorMessage } from '../core/text';
import { Blueprint, DotsIcon, Icon, ICONS } from './kit';
import { CreerScreen } from './screens/Creer';
import { RepertoireScreen } from './screens/Repertoire';
import { SortieScreen } from './screens/Sortie';
import { isDemoRequested, resumeGraphSession, signIn, startDemo, type Session } from './session';
import { SheetHost } from './sheets/Sheets';
import { useProjectFlow, type ConfirmKind, type PF, type Tab } from './useProjectFlow';

export function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [state, setState] = useState<'loading' | 'signedOut' | 'ready'>('loading');
  const [error, setError] = useState('');

  useEffect(() => {
    if (isDemoRequested()) {
      setSession(startDemo());
      setState('ready');
      return;
    }
    resumeGraphSession().then(
      (s) => {
        setSession(s);
        setState(s ? 'ready' : 'signedOut');
      },
      (e) => {
        setError(errorMessage(e));
        setState('signedOut');
      },
    );
  }, []);

  if (state === 'ready' && session) return <Main session={session} />;
  return (
    <div className="pf-shell">
      <div className="pf-signin">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div className="pf-mark" style={{ width: 44, height: 44, fontSize: 22 }}>PF</div>
          <div>
            <div className="pf-brand" style={{ fontSize: 30 }}>ProjectFlow</div>
            <div className="pf-company">Balz Métal SA</div>
          </div>
        </div>
        {state === 'loading' ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }} className="pf-muted"><span className="pf-spinner" /> Connexion…</div>
        ) : (
          <Blueprint className="pf-card" style={{ marginBottom: 0 }}>
            <div className="pf-title" style={{ marginBottom: 6 }}>Compagnon mobile</div>
            <p style={{ fontSize: 14, margin: '0 0 16px' }} className="pf-muted">
              Créez les projets, préparez les sorties dossier et consultez le répertoire chantier directement dans OneDrive, Planner et Outlook.
            </p>
            {error && <p className="pf-small pf-danger-text" style={{ margin: '0 0 12px' }}>{error}</p>}
            <button type="button" className="btn btn-primary btn-block pf-big" onClick={() => signIn().catch((e) => setError(errorMessage(e)))}>
              Se connecter avec Microsoft
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-block"
              style={{ minHeight: 44 }}
              onClick={() => {
                setSession(startDemo());
                setState('ready');
              }}
            >
              Essayer la démonstration
            </button>
          </Blueprint>
        )}
      </div>
    </div>
  );
}

function Main({ session }: { session: Session }) {
  const pf = useProjectFlow(session);
  const demo = pf.gw.kind === 'demo';
  return (
    <div className="pf-shell">
      <header className="pf-header">
        <div className="pf-mark">PF</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="pf-brand">ProjectFlow</div>
          <div className="pf-company">Balz Métal SA</div>
        </div>
        <span className="tag tag-accent" style={{ gap: 6 }} title={demo ? 'Données de démonstration' : 'Connecté à OneDrive'}>
          <span className="pf-dot" />
          {demo ? 'Démo' : 'OneDrive'}
        </span>
        <button type="button" className="btn btn-secondary btn-icon" aria-label="Paramètres" onClick={() => pf.setSheet('settings')} style={{ width: 40, height: 40 }}>
          <Icon d={ICONS.sliders} />
        </button>
      </header>

      <main className="pf-scroll">
        {pf.tab === 'creer' && <CreerScreen pf={pf} />}
        {pf.tab === 'sortie' && <SortieScreen pf={pf} />}
        {pf.tab === 'rep' && <RepertoireScreen pf={pf} />}
      </main>

      <ActionBar pf={pf} />
      <TabBar pf={pf} />

      {pf.toastText && (
        <div className="pf-toast" role="status">
          <Icon d={ICONS.check} size={18} />
          {pf.toastText}
        </div>
      )}
      <SheetHost pf={pf} />
      <ConfirmDialog pf={pf} />
    </div>
  );
}

function ActionBar({ pf }: { pf: PF }) {
  if (pf.tab === 'creer') {
    return (
      <div className="pf-actionbar">
        <button type="button" className="btn btn-secondary btn-icon" style={{ width: 48, height: 48, flex: 'none' }} aria-label="Plus d’actions" onClick={() => pf.setSheet('more')}>
          <DotsIcon />
        </button>
        <button type="button" className="btn btn-secondary pf-big" style={{ flex: 1 }} disabled={pf.busy} onClick={() => pf.setConfirm('update')}>Mettre à jour</button>
        <button type="button" className="btn btn-primary pf-big" style={{ flex: 1.2 }} disabled={pf.busy} onClick={() => pf.create('create')}>Créer</button>
      </div>
    );
  }
  if (pf.tab === 'sortie') {
    return (
      <div className="pf-actionbar">
        <button type="button" className="btn btn-primary pf-big" style={{ width: '100%' }} disabled={!pf.so.inv || pf.so.loading || pf.busy || !pf.so.inv.fiches.length} onClick={pf.runSortie}>
          Créer dossier de sortie
        </button>
      </div>
    );
  }
  const snap = pf.snapshots[pf.repYear];
  const next = snap?.next?.number;
  return (
    <div className="pf-actionbar">
      <button type="button" className="btn btn-primary pf-big" style={{ width: '100%' }} disabled={!next} onClick={() => next && pf.tapRow(null, next)}>
        Nouveau projet · {next ?? '—'}
      </button>
    </div>
  );
}

const TABS: { k: Tab; label: string; d: string }[] = [
  { k: 'creer', label: 'Créer', d: ICONS.creer },
  { k: 'sortie', label: 'Sortie', d: ICONS.sortie },
  { k: 'rep', label: 'Répertoire', d: ICONS.rep },
];

function TabBar({ pf }: { pf: PF }) {
  return (
    <nav className="pf-tabbar" aria-label="Sections">
      {TABS.map((t) => (
        <button
          key={t.k}
          type="button"
          className={`pf-tab${pf.tab === t.k ? ' on' : ''}`}
          aria-current={pf.tab === t.k ? 'page' : undefined}
          onClick={() => {
            pf.setTab(t.k);
            if (pf.sheet !== 'progress' || pf.progress?.done) pf.setSheet(null);
          }}
        >
          <span className="pf-tab-bar" />
          <Icon d={t.d} size={22} />
          <span className="pf-tab-label">{t.label}</span>
        </button>
      ))}
    </nav>
  );
}

const CONFIRM: Record<ConfirmKind, (pf: PF) => [string, string, string]> = {
  update: () => ['Confirmer la mise à jour', 'Réécrire la fiche et la ligne du répertoire ? Le dossier projet n’est pas recréé.', 'Mettre à jour'],
  sync: () => ['Mettre à jour le projet', 'Reprendre désignation, client et contact du tableau, puis réappliquer fiche, répertoire et intégrations actives ?', 'Mettre à jour'],
  del: (pf) => [
    'Supprimer le projet',
    `Le projet ${pf.selRow?.number ?? ''} et ses éléments liés (sous-projets, tâches Planner, dossiers Outlook) seront supprimés et son numéro libéré. Les fichiers partent dans la corbeille OneDrive.`,
    'Supprimer',
  ],
};

function ConfirmDialog({ pf }: { pf: PF }) {
  if (!pf.confirm) return null;
  const [title, body, cta] = CONFIRM[pf.confirm](pf);
  return (
    <div className="pf-confirm">
      <div className="dialog" role="alertdialog" aria-modal="true" aria-label={title} style={{ background: 'var(--color-bg)', width: '100%' }}>
        <div className="dialog-title">{title}</div>
        <div className="dialog-body">{body}</div>
        <div className="dialog-actions">
          <button type="button" className="btn btn-secondary" style={{ minHeight: 44 }} onClick={() => pf.setConfirm(null)}>Annuler</button>
          <button
            type="button"
            className="btn btn-primary"
            style={{ minHeight: 44, ...(pf.confirm === 'del' ? { background: '#B42318', borderColor: '#B42318' } : {}) }}
            onClick={pf.doConfirm}
          >
            {cta}
          </button>
        </div>
      </div>
    </div>
  );
}
