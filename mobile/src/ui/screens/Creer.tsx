import { initialsOf } from '../../core/text';
import { Blueprint, CheckRow, Check, MailIcon, SectionHead } from '../kit';
import type { FormState, PF } from '../useProjectFlow';

export function CreerScreen({ pf }: { pf: PF }) {
  const { f, setF, settings, planner, setPlanner } = pf;
  const set = (k: keyof FormState) => (e: { target: { value: string } }) => {
    const v = e.target.value;
    pf.setError('');
    setF((x) => ({ ...x, [k]: v }));
  };
  const plannerReady = settings.planner.enabled && !!settings.planner.planId;
  const notifySub = pf.assignees.length
    ? `À ${pf.assignees.map((m) => initialsOf(m.displayName, m.email)).join(', ')} · e-mail via Outlook`
    : 'Choisissez au moins un membre ci-dessus';

  return (
    <>
      <h1 className="pf-h1">Nouveau projet</h1>
      <p className="pf-lead">
        Prochain numéro libre : <b>{pf.nextNo}</b> · ligne Excel {pf.nextExcel}
      </p>

      <Blueprint className="pf-card">
        <SectionHead num="01" title="Identification" />
        <div className="pf-grid" style={{ gridTemplateColumns: '86px 1fr 92px' }}>
          <div className="field">
            <label htmlFor="f-year">Année</label>
            <select id="f-year" className="input" value={f.year} onChange={set('year')}>
              {pf.years.map((y) => <option key={y}>{y}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="f-id">ID projet</label>
            <input id="f-id" className="input" inputMode="numeric" placeholder={pf.nextNo.split('-')[1] ?? '4995'} value={f.id} onChange={set('id')} />
          </div>
          <div className="field">
            <label htmlFor="f-sub">Sous-projet</label>
            <input id="f-sub" className="input" inputMode="numeric" placeholder="Optionnel" value={f.sub} onChange={set('sub')} />
          </div>
        </div>
        <button
          type="button"
          className="btn btn-secondary btn-block"
          style={{ margin: '10px 0 14px', minHeight: 44 }}
          disabled={pf.nextNo === '—'}
          onClick={() => {
            pf.setError('');
            const [year, id] = pf.nextNo.split('-');
            setF((x) => ({ ...x, year, id, sub: '' }));
          }}
        >
          Suivant disponible · {pf.nextNo}
        </button>
        <div className="field">
          <label htmlFor="f-des">Désignation</label>
          <input id="f-des" className="input pf-input" placeholder="Ex. Garde-corps passerelle" value={f.des} onChange={set('des')} />
        </div>
      </Blueprint>

      <Blueprint className="pf-card">
        <SectionHead num="02" title="Client" />
        <div className="pf-stack">
          <div className="field">
            <label htmlFor="f-soc">Société</label>
            <input id="f-soc" className="input pf-input" autoComplete="off" value={f.soc} onChange={set('soc')} />
            {pf.suggestions.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
                {pf.suggestions.map((sg) => (
                  <button key={sg.societe} type="button" className="btn btn-secondary" style={{ fontSize: 13, padding: '6px 10px', minHeight: 36 }} onClick={() => setF((x) => ({ ...x, soc: sg.societe, con: sg.contact || x.con }))}>
                    {sg.societe}{sg.contact ? ` · ${sg.contact}` : ''}
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="field">
            <label htmlFor="f-con">Contact</label>
            <input id="f-con" className="input pf-input" value={f.con} onChange={set('con')} />
          </div>
          <div className="field">
            <label htmlFor="f-loc">Localisation</label>
            <input id="f-loc" className="input pf-input" value={f.loc} onChange={set('loc')} />
          </div>
          <div className="pf-grid" style={{ gridTemplateColumns: '1fr 92px' }}>
            <div className="field">
              <label htmlFor="f-ger">Géré par → C6</label>
              <input id="f-ger" className="input pf-input" value={f.ger} onChange={set('ger')} />
            </div>
            <div className="field">
              <label htmlFor="f-ini">Initiales → C9</label>
              <input id="f-ini" className="input pf-input pf-readonly" readOnly value={settings.initials} />
            </div>
          </div>
        </div>
      </Blueprint>

      <Blueprint className="pf-card">
        <SectionHead num="03" title="Intégrations" mb={6} />
        <CheckRow
          on={planner.on && plannerReady}
          disabled={!plannerReady}
          onToggle={() => (plannerReady ? setPlanner((p) => ({ ...p, on: !p.on })) : pf.setSheet('settings'))}
          title="Créer une tâche Planner"
          sub={plannerReady ? `Plan « ${settings.planner.planName} »` : 'Non configuré · ouvrez Paramètres'}
        />
        {planner.on && plannerReady && (
          <div className="pf-sub">
            {pf.plannerError && <div className="pf-small pf-danger-text">{pf.plannerError}</div>}
            <div className="field">
              <label htmlFor="f-col">Colonne</label>
              <select id="f-col" className="input pf-input" value={planner.bucketId} onChange={(e) => { const v = e.target.value; setPlanner((p) => ({ ...p, bucketId: v })); }}>
                {pf.buckets.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
              </select>
            </div>
            <div className="field">
              <label>Membres assignés</label>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {pf.members.map((m) => {
                  const on = planner.members.includes(m.id);
                  return (
                    <button
                      key={m.id}
                      type="button"
                      title={m.displayName || m.email}
                      aria-pressed={on}
                      className={`btn ${on ? 'btn-primary' : 'btn-secondary'} pf-chip`}
                      onClick={() => setPlanner((p) => ({ ...p, members: on ? p.members.filter((x) => x !== m.id) : [...p.members, m.id] }))}
                    >
                      {initialsOf(m.displayName, m.email)}
                    </button>
                  );
                })}
              </div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <button type="button" role="checkbox" aria-checked={planner.due} className="pf-row" style={{ flex: 1, minHeight: 44, gap: 10 }} onClick={() => setPlanner((p) => ({ ...p, due: !p.due }))}>
                <Check on={planner.due} />
                <span style={{ fontSize: 14 }}>Échéance</span>
              </button>
              {planner.due && (
                <div style={{ display: 'flex', alignItems: 'center' }}>
                  <button type="button" className="btn btn-secondary btn-icon" style={{ width: 44, height: 44 }} aria-label="Moins" onClick={() => setPlanner((p) => ({ ...p, days: Math.max(1, p.days - 1) }))}>−</button>
                  <span style={{ minWidth: 74, textAlign: 'center', fontSize: 14 }}>{planner.days} jours</span>
                  <button type="button" className="btn btn-secondary btn-icon" style={{ width: 44, height: 44 }} aria-label="Plus" onClick={() => setPlanner((p) => ({ ...p, days: Math.min(365, p.days + 1) }))}>+</button>
                </div>
              )}
            </div>
            <CheckRow ruled on={planner.notify} onToggle={() => setPlanner((p) => ({ ...p, notify: !p.notify }))} title="Notifier les personnes assignées" sub={notifySub} />
            {planner.notify && (
              <div style={{ padding: '0 0 4px 34px', display: 'flex', flexDirection: 'column', gap: 8, marginTop: -8 }}>
                <div style={{ fontSize: 13, color: 'var(--color-neutral-700)' }}>
                  E-mail envoyé depuis votre Outlook à la création, avec les informations générales et un lien pour en prendre connaissance.
                </div>
                <button type="button" className="btn btn-secondary" style={{ minHeight: 44, gap: 8 }} onClick={() => pf.setSheet('mail')}>
                  <MailIcon />
                  Aperçu du message
                </button>
              </div>
            )}
          </div>
        )}
        <CheckRow
          ruled
          on={pf.sw}
          disabled={!settings.cad.solidworks}
          onToggle={() => (settings.cad.solidworks ? pf.setSw(!pf.sw) : pf.setSheet('settings'))}
          title="Ajouter arborescence SolidWorks"
          sub={settings.cad.solidworks ? `Modèles 20XX-XXXX renommés au numéro · sans écrasement` : 'Dossier modèle non configuré · Paramètres'}
        />
        <CheckRow
          ruled
          on={pf.ac}
          disabled={!settings.cad.autocad}
          onToggle={() => (settings.cad.autocad ? pf.setAc(!pf.ac) : pf.setSheet('settings'))}
          title="Ajouter modèle AutoCAD"
          sub={settings.cad.autocad ? '20XX-XXXX-ENS-100.dwg' : 'Dossier modèle non configuré · Paramètres'}
        />
      </Blueprint>

      {pf.error && (
        <Blueprint className="pf-error" style={{ marginBottom: 22 }}>
          {pf.error}
        </Blueprint>
      )}

      {pf.logs.length > 0 && (
        <>
          <div className="pf-kicker" style={{ marginBottom: 8 }}>Journal</div>
          <div className="pf-journal">
            {pf.logs.map((l, i) => (
              <div key={i}>
                <span style={{ color: 'var(--color-neutral-600)' }}>{l.t}</span>&nbsp; {l.m}
              </div>
            ))}
          </div>
        </>
      )}
    </>
  );
}
