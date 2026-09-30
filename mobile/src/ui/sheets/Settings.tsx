import { useEffect, useState } from 'react';
import type { PlannerBucket, PlannerPlan } from '../../core/gateways';
import type { ItemRef, Settings } from '../../core/settings';
import { errorMessage } from '../../core/text';
import { signIn } from '../session';
import { CheckRow } from '../kit';
import type { PF } from '../useProjectFlow';

type PathKey = 'racine' | 'reference' | 'repertoire' | 'solidworks' | 'autocad';

const PATHS: { key: PathKey; label: string; folder: boolean }[] = [
  { key: 'racine', label: 'Racine projets', folder: true },
  { key: 'reference', label: 'Dossier de référence', folder: true },
  { key: 'repertoire', label: 'Répertoire chantier', folder: false },
];
const CAD_PATHS: { key: PathKey; label: string; folder: boolean }[] = [
  { key: 'solidworks', label: 'Modèles SolidWorks', folder: true },
  { key: 'autocad', label: 'Modèles AutoCAD', folder: true },
];

const getRef = (s: Settings, key: PathKey) => (key === 'solidworks' || key === 'autocad' ? s.cad[key] : s[key]);
const setRef = (s: Settings, key: PathKey, ref: ItemRef | null): Settings =>
  key === 'solidworks' || key === 'autocad' ? { ...s, cad: { ...s.cad, [key]: ref } } : { ...s, [key]: ref };

function PathField({ pf, def }: { pf: PF; def: { key: PathKey; label: string; folder: boolean } }) {
  const current = getRef(pf.settings, def.key);
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(current?.reference ?? '');
  const [state, setState] = useState<{ busy: boolean; err: string }>({ busy: false, err: '' });

  async function link() {
    setState({ busy: true, err: '' });
    try {
      const item = await pf.gw.drive.resolve(value);
      if (def.folder && !item.isFolder) throw new Error('Choisissez un dossier, pas un fichier.');
      if (!def.folder && (item.isFolder || !item.name.toLowerCase().endsWith('.xlsx'))) throw new Error('Choisissez le classeur Excel (.xlsx), pas son dossier.');
      pf.setSettings((s) => setRef(s, def.key, { reference: value.trim(), driveId: item.driveId, id: item.id, name: item.name, webUrl: item.webUrl }));
      setEditing(false);
      setState({ busy: false, err: '' });
      pf.toast(`${def.label} relié : ${item.name}`);
    } catch (e) {
      setState({ busy: false, err: errorMessage(e) });
    }
  }

  return (
    <div className="field">
      <label htmlFor={`path-${def.key}`}>{def.label}</label>
      {!editing ? (
        <button type="button" id={`path-${def.key}`} className={`pf-path${current ? '' : ' unset'}`} onClick={() => { setValue(current?.reference ?? ''); setEditing(true); }}>
          {current ? current.reference || current.name : 'Non configuré · touchez pour relier'}
        </button>
      ) : (
        <div className="pf-stack" style={{ gap: 8 }}>
          <input
            id={`path-${def.key}`}
            className="input pf-input"
            autoFocus
            placeholder={def.folder ? 'Lien de partage, ou Dossier/Sous-dossier' : 'Lien de partage du classeur .xlsx'}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && link()}
          />
          <div className="pf-small">Collez le lien de partage OneDrive/SharePoint, ou un chemin depuis la racine de « Mon OneDrive ».</div>
          {state.err && <div className="pf-small pf-danger-text">{state.err}</div>}
          <div className="pf-grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
            <button type="button" className="btn btn-secondary" style={{ minHeight: 44 }} onClick={() => setEditing(false)}>Annuler</button>
            <button type="button" className="btn btn-primary" style={{ minHeight: 44 }} disabled={state.busy || !value.trim()} onClick={link}>{state.busy ? 'Vérification…' : 'Relier'}</button>
          </div>
        </div>
      )}
    </div>
  );
}

function PlannerOptions({ pf }: { pf: PF }) {
  const p = pf.settings.planner;
  const [plans, setPlans] = useState<PlannerPlan[]>(p.planId ? [{ id: p.planId, title: p.planName }] : []);
  const [buckets, setBuckets] = useState<PlannerBucket[]>(pf.buckets);
  const [err, setErr] = useState('');

  useEffect(() => {
    let cancelled = false;
    pf.session
      .ensureScopes(['planner'])
      .then(() => pf.gw.planner.listPlans())
      .then((list) => !cancelled && (setPlans(list), setErr('')))
      .catch((e) => !cancelled && setErr(errorMessage(e)));
    return () => {
      cancelled = true;
    };
  }, [pf.gw, pf.session]);

  useEffect(() => {
    if (!p.planId) return setBuckets([]);
    let cancelled = false;
    pf.gw.planner.listBuckets(p.planId).then((b) => {
      if (cancelled) return;
      setBuckets(b);
      if (!b.some((x) => x.id === p.bucketId) && b[0]) pf.setSettings((s) => ({ ...s, planner: { ...s.planner, bucketId: b[0].id, bucketName: b[0].name } }));
    }, (e) => !cancelled && setErr(errorMessage(e)));
    return () => {
      cancelled = true;
    };
  }, [p.planId]);

  const update = (patch: Partial<Settings['planner']>) => pf.setSettings((s) => ({ ...s, planner: { ...s.planner, ...patch } }));
  return (
    <div className="pf-sub" style={{ paddingBottom: 8 }}>
      {err && <div className="pf-small pf-danger-text">{err}</div>}
      <div className="field">
        <label htmlFor="set-plan">Plan</label>
        <select id="set-plan" className="input pf-input" value={p.planId} onChange={(e) => { const plan = plans.find((x) => x.id === e.target.value); update({ planId: plan?.id ?? '', planName: plan?.title ?? '', bucketId: '', bucketName: '' }); }}>
          <option value="">Choisir un plan…</option>
          {plans.map((x) => <option key={x.id} value={x.id}>{x.title}</option>)}
        </select>
      </div>
      <div className="pf-grid" style={{ gridTemplateColumns: '1fr 110px' }}>
        <div className="field">
          <label htmlFor="set-bucket">Colonne par défaut</label>
          <select id="set-bucket" className="input pf-input" value={p.bucketId} onChange={(e) => { const b = buckets.find((x) => x.id === e.target.value); update({ bucketId: b?.id ?? '', bucketName: b?.name ?? '' }); }}>
            {buckets.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
          </select>
        </div>
        <div className="field">
          <label htmlFor="set-due">Échéance (j)</label>
          <input id="set-due" className="input pf-input" inputMode="numeric" value={p.dueDays} onChange={(e) => update({ dueDays: Math.min(365, Math.max(0, Number(e.target.value.replace(/\D/g, '')) || 0)) })} />
        </div>
      </div>
    </div>
  );
}

export function SettingsSheet({ pf }: { pf: PF }) {
  const { settings, setSettings } = pf;
  const demo = pf.gw.kind === 'demo';

  async function checkUpdate() {
    pf.setSheet(null);
    try {
      const reg = await navigator.serviceWorker?.getRegistration();
      if (!reg) return pf.toast('Vous utilisez la dernière version');
      await reg.update();
      pf.toast(reg.installing || reg.waiting ? 'Nouvelle version téléchargée · relancez l’application' : 'Vous utilisez la dernière version');
    } catch {
      pf.toast('Vous utilisez la dernière version');
    }
  }

  return (
    <>
      <p className="pf-small" style={{ margin: '0 0 14px' }}>
        {demo ? 'Mode démonstration · données fictives, rien n’est écrit dans Microsoft 365.' : `Connecté : ${pf.me.displayName}${pf.me.email ? ` · ${pf.me.email}` : ''}`}
      </p>
      <div className="pf-stack" style={{ gap: 14, paddingBottom: 6 }}>
        {PATHS.map((d) => <PathField key={d.key} pf={pf} def={d} />)}
        <div className="field">
          <label htmlFor="set-ini">Initiales utilisateur</label>
          <input id="set-ini" className="input pf-input" value={settings.initials} onChange={(e) => { const v = e.target.value.toUpperCase().slice(0, 6); setSettings((s) => ({ ...s, initials: v })); }} />
        </div>
      </div>

      <div style={{ borderTop: '1px solid var(--color-divider)', marginTop: 8, paddingTop: 8 }}>
        <CheckRow
          minHeight={56}
          titleSize={16}
          on={settings.outlook.enabled}
          onToggle={() => setSettings((s) => ({ ...s, outlook: { ...s.outlook, enabled: !s.outlook.enabled } }))}
          title="Créer les dossiers Outlook"
          sub="Boîte aux lettres du compte Microsoft · via Outlook sur le web"
        />
        {settings.outlook.enabled && (
          <div className="pf-sub" style={{ paddingBottom: 8 }}>
            <div className="field">
              <label htmlFor="set-arbo">Arborescence (un chemin par ligne)</label>
              <textarea id="set-arbo" className="input" style={{ minHeight: 70 }} value={settings.outlook.arborescence} onChange={(e) => { const v = e.target.value; setSettings((s) => ({ ...s, outlook: { ...s.outlook, arborescence: v } })); }} />
            </div>
            <div className="pf-small">Jetons : [YYYY] [XXXX] [NUMERO] [DESIGNATION] [PROJECT_FOLDER]</div>
          </div>
        )}
      </div>

      <div style={{ borderTop: '1px solid var(--color-divider)', marginTop: 8, paddingTop: 8 }}>
        <CheckRow
          minHeight={56}
          titleSize={16}
          on={settings.planner.enabled}
          onToggle={() => setSettings((s) => ({ ...s, planner: { ...s.planner, enabled: !s.planner.enabled } }))}
          title="Microsoft Planner"
          sub={settings.planner.planName ? `Plan « ${settings.planner.planName} » · échéance ${settings.planner.dueDays} jours` : 'Aucun plan choisi'}
        />
        {settings.planner.enabled && <PlannerOptions pf={pf} />}
      </div>

      <div style={{ borderTop: '1px solid var(--color-divider)', marginTop: 8, paddingTop: 14 }} className="pf-stack">
        <div className="pf-kicker">Modèles CAO</div>
        {CAD_PATHS.map((d) => <PathField key={d.key} pf={pf} def={d} />)}
        <div className="field">
          <label htmlFor="set-cadsub">Sous-dossier de destination</label>
          <input id="set-cadsub" className="input pf-input" value={settings.cad.subfolder} onChange={(e) => { const v = e.target.value; setSettings((s) => ({ ...s, cad: { ...s.cad, subfolder: v } })); }} />
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 18 }}>
        {!demo && <button type="button" className="btn btn-secondary" style={{ minHeight: 44 }} onClick={() => signIn()}>Se reconnecter au compte Microsoft</button>}
        <button type="button" className="btn btn-secondary" style={{ minHeight: 44 }} onClick={checkUpdate}>Rechercher une mise à jour</button>
        <button type="button" className="btn btn-ghost" style={{ minHeight: 44 }} onClick={() => pf.session.signOut()}>{demo ? 'Quitter la démonstration' : 'Se déconnecter'}</button>
      </div>
    </>
  );
}
