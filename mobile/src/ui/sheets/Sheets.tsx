import { useMemo } from 'react';
import { makeNumber } from '../../core/numero';
import { notificationRows, notificationSubject } from '../../core/notification';
import { folderPathLabel } from '../../core/project';
import { fileMeta, initialsOf } from '../../core/text';
import { Blueprint, Check, CheckRow, Icon, ICONS } from '../kit';
import type { PF } from '../useProjectFlow';
import { SettingsSheet } from './Settings';

export function SheetHost({ pf }: { pf: PF }) {
  const { sheet, progress } = pf;
  if (!sheet) return null;
  const running = sheet === 'progress' && progress && !progress.done;
  const close = () => {
    if (running) return;
    pf.setSheet(null);
    if (sheet === 'picker') pf.setPicker(null);
  };
  const titles: Record<string, string> = {
    more: 'Actions',
    row: pf.selRow?.text[2] || pf.selRow?.number || '',
    settings: 'Paramètres',
    progress: progress?.title ?? '',
    mail: 'Aperçu e-mail',
    picker: pf.picker?.kind === 'photos' ? 'Photos du projet' : 'Plans d’exécution',
  };
  return (
    <>
      <div className="pf-scrim" onClick={close} />
      <div className="pf-sheet" role="dialog" aria-modal="true" aria-label={titles[sheet]}>
        <div className="pf-sheet-head">
          <span className="pf-sheet-title">{titles[sheet]}</span>
          {!running && (
            <button type="button" className="btn btn-ghost btn-icon" style={{ width: 44, height: 44 }} aria-label="Fermer" onClick={close}>
              <Icon d={ICONS.close} size={22} />
            </button>
          )}
        </div>
        <div className="pf-sheet-body">
          {sheet === 'more' && <MoreSheet pf={pf} />}
          {sheet === 'row' && <RowSheet pf={pf} />}
          {sheet === 'settings' && <SettingsSheet pf={pf} />}
          {sheet === 'progress' && <ProgressSheet pf={pf} />}
          {sheet === 'mail' && <MailSheet pf={pf} />}
          {sheet === 'picker' && <PickerSheet pf={pf} />}
        </div>
      </div>
    </>
  );
}

function MoreSheet({ pf }: { pf: PF }) {
  return (
    <>
      {pf.moreActions.map((a) => (
        <button key={a.label} type="button" className="pf-row ruled" style={{ fontSize: 16 }} onClick={a.go}>
          <span style={{ flex: 1 }}>{a.label}</span>
          <span className="pf-small">{a.hint}</span>
        </button>
      ))}
      <CheckRow
        ruled
        titleSize={16}
        on={pf.settings.printAfterCreate}
        onToggle={() => pf.setSettings((s) => ({ ...s, printAfterCreate: !s.printAfterCreate }))}
        title="Imprimer fiche après Créer"
        sub="A4, une page · PDF ouvert pour l’impression"
      />
    </>
  );
}

function RowSheet({ pf }: { pf: PF }) {
  const row = pf.selRow;
  if (!row) return null;
  const labels = ['Date', 'Client', 'Contact', 'Désignation'];
  const dirty = labels.map((_, i) => (pf.draft[i] ?? '') !== (row.text[i + 1] ?? ''));
  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
        <span className="tag tag-accent">{row.number}</span>
        <span className="pf-small">Ligne Excel {row.rowIndex + 1} · colonnes A–E</span>
      </div>
      <div className="pf-stack">
        {labels.map((label, i) => (
          <div className="field" key={label}>
            <label htmlFor={`row-${i}`}>{label}{dirty[i] ? ' · modifié' : ''}</label>
            <input
              id={`row-${i}`}
              className="input pf-input"
              value={pf.draft[i] ?? ''}
              onChange={(e) => { const v = e.target.value; pf.setDraft((d) => d.map((x, j) => (j === i ? v : x))); }}
              style={{ background: dirty[i] ? 'var(--color-accent-200)' : 'var(--color-surface)' }}
            />
          </div>
        ))}
      </div>
      <div className="pf-grid" style={{ gridTemplateColumns: '1fr 1fr', marginTop: 16 }}>
        <button type="button" className="btn btn-secondary" style={{ minHeight: 44 }} onClick={pf.rowLoad}>Charger le projet</button>
        <button type="button" className="btn btn-secondary" style={{ minHeight: 44 }} onClick={pf.rowSub}>Créer sous-projet</button>
        <button type="button" className="btn btn-secondary" style={{ minHeight: 44 }} onClick={pf.rowDup}>Dupliquer</button>
        <button type="button" className="btn btn-secondary" style={{ minHeight: 44 }} onClick={() => pf.setConfirm('sync')}>Mettre à jour le projet</button>
      </div>
      <button type="button" className="btn btn-primary btn-block pf-big" disabled={!dirty.some(Boolean)} onClick={pf.saveRow}>Enregistrer la ligne</button>
      <button type="button" className="btn btn-block" style={{ minHeight: 44, color: '#B42318', borderColor: 'color-mix(in srgb,#B42318 40%,transparent)' }} onClick={() => pf.setConfirm('del')}>
        Supprimer avec éléments liés
      </button>
    </>
  );
}

function ProgressSheet({ pf }: { pf: PF }) {
  const p = pf.progress;
  if (!p) return null;
  const finished = p.states.filter((s) => s.status !== 'wait' && s.status !== 'run').length;
  const pct = p.done ? 100 : Math.round((finished / Math.max(1, p.states.length)) * 100);
  return (
    <>
      <div className="pf-bar"><div style={{ width: `${pct}%` }} /></div>
      {p.states.map((st, i) => (
        <div className="pf-step" key={i}>
          <span className={`pf-step-icon ${st.status}`}>
            {st.status === 'done' && <Icon d={ICONS.check} size={13} stroke={2.2} />}
            {st.status === 'run' && <span />}
            {st.status === 'warn' && '!'}
            {st.status === 'error' && <Icon d={ICONS.close} size={13} stroke={2.2} />}
          </span>
          <span style={{ flex: 1, minWidth: 0 }}>
            <span style={{ display: 'block', fontSize: 14, color: st.status === 'wait' || st.status === 'skip' ? 'var(--color-neutral-600)' : undefined, textDecoration: st.status === 'skip' ? 'line-through' : undefined }}>
              {st.label}
            </span>
            {st.detail && <span className={`pf-small${st.status === 'error' ? ' pf-danger-text' : ''}`} style={{ display: 'block', marginTop: 2 }}>{st.detail}</span>}
          </span>
        </div>
      ))}
      {p.done && (
        <>
          <Blueprint className={`pf-result${p.failed ? ' fail' : ''}`}>
            <div style={{ fontFamily: 'var(--font-heading)', fontWeight: 600, fontSize: 20, color: p.failed ? '#B42318' : 'var(--color-accent-900)' }}>{p.result}</div>
            <div style={{ fontSize: 13, color: p.failed ? '#B42318' : 'var(--color-accent-800)', marginTop: 2 }}>{p.detail}</div>
          </Blueprint>
          <div className="pf-grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
            <button type="button" className="btn btn-secondary pf-big" style={{ fontSize: 14 }} onClick={() => pf.setSheet(null)}>Fermer</button>
            <button
              type="button"
              className="btn btn-primary pf-big"
              style={{ fontSize: 14 }}
              disabled={!p.target && p.cta !== 'Fermer'}
              onClick={() => {
                pf.setSheet(null);
                if (p.target) pf.openUrl(p.target, 'dossier ouvert');
              }}
            >
              {p.cta}
            </button>
          </div>
          {p.print && (
            <button type="button" className="btn btn-secondary btn-block" style={{ minHeight: 44 }} onClick={() => pf.printFiche(p.print!)}>
              Imprimer la fiche
            </button>
          )}
        </>
      )}
    </>
  );
}

function MailSheet({ pf }: { pf: PF }) {
  const { f, settings } = pf;
  const noStr = `${f.year}-${f.id.trim() || 'XXXX'}${f.sub.trim() ? '-' + f.sub.trim() : ''}`;
  const rows = useMemo(() => {
    let folder = `…\\${settings.racine?.name ?? 'Projets'}\\${f.year}\\${f.year}-${f.id.trim() || 'XXXX'}`;
    try {
      folder = folderPathLabel(pf.ctx, makeNumber(f.year, f.id, f.sub));
    } catch {
      // numéro incomplet : chemin indicatif
    }
    return notificationRows({
      number: noStr,
      designation: f.des,
      societe: f.soc,
      contact: f.con,
      localisation: f.loc,
      gerePar: f.ger,
      initials: settings.initials,
      planName: settings.planner.planName,
      bucketName: pf.bucketName,
      due: pf.planner.due ? new Date(Date.now() + pf.planner.days * 86_400_000) : null,
      folderPath: folder,
      folderUrl: '',
      taskUrl: '',
      senderName: pf.me.displayName,
    });
  }, [f, settings, pf.bucketName, pf.planner.due, pf.planner.days, pf.me.displayName, pf.ctx, noStr]);
  const to = pf.assignees.length ? pf.assignees.map((m) => m.displayName || m.email).join(', ') : 'Aucun membre assigné';
  return (
    <>
      <div style={{ border: '1px solid var(--color-divider)', fontSize: 13, marginBottom: 14 }}>
        <div style={{ display: 'flex', gap: 10, padding: '8px 12px', borderBottom: '1px solid var(--color-divider)' }}>
          <span style={{ width: 40, flex: 'none', color: 'var(--color-neutral-700)' }}>De</span>
          <span>{pf.me.displayName} · Balz Métal SA</span>
        </div>
        <div style={{ display: 'flex', gap: 10, padding: '8px 12px', borderBottom: '1px solid var(--color-divider)' }}>
          <span style={{ width: 40, flex: 'none', color: 'var(--color-neutral-700)' }}>À</span>
          <span style={{ flex: 1 }}>{to}</span>
        </div>
        <div style={{ display: 'flex', gap: 10, padding: '8px 12px' }}>
          <span style={{ width: 40, flex: 'none', color: 'var(--color-neutral-700)' }}>Objet</span>
          <span style={{ flex: 1, fontWeight: 500 }}>{notificationSubject(noStr, f.des)}</span>
        </div>
      </div>
      <Blueprint style={{ padding: '18px 16px', background: '#fff' }}>
        <div className="pf-num" style={{ fontSize: 12, textTransform: 'uppercase' }}>Nouvelle assignation</div>
        <div style={{ fontFamily: 'var(--font-heading)', fontWeight: 600, fontSize: 26, lineHeight: 1.1, margin: '4px 0 10px' }}>{noStr}</div>
        <p style={{ fontSize: 14, margin: '0 0 14px' }}>Bonjour, vous avez été assigné(e) à ce projet. Voici les informations générales pour en prendre connaissance.</p>
        <div style={{ borderTop: '1px solid var(--color-divider)' }}>
          {rows.map((r) => (
            <div key={r.k} style={{ display: 'flex', gap: 12, padding: '8px 0', borderBottom: '1px solid var(--color-divider)', fontSize: 13 }}>
              <span style={{ width: 96, flex: 'none', color: 'var(--color-neutral-700)' }}>{r.k}</span>
              <span style={{ flex: 1, minWidth: 0, wordBreak: 'break-word' }}>{r.v}</span>
            </div>
          ))}
        </div>
        <button type="button" className="btn btn-primary btn-block pf-big" style={{ marginTop: 16 }} onClick={() => pf.toast('Aperçu : ouvre le dossier projet dans OneDrive')}>
          Prendre connaissance du projet
        </button>
        <div style={{ fontSize: 11, color: 'var(--color-neutral-700)', marginTop: 12 }}>Message automatique · ProjectFlow Automator, Balz Métal SA</div>
      </Blueprint>
      <p className="pf-small" style={{ margin: '12px 0 4px' }}>
        Envoyé uniquement aux membres nouvellement assignés à la tâche ({pf.assignees.map((m) => initialsOf(m.displayName, m.email)).join(', ') || 'aucun'}).
      </p>
    </>
  );
}

function PickerSheet({ pf }: { pf: PF }) {
  const pk = pf.picker;
  const inv = pf.so.inv;
  if (!pk || !inv) return null;
  const all = pk.kind === 'photos' ? inv.photos : inv.plans;
  const taken = new Set(pk.kind === 'photos' ? pf.so.photos : pf.so.plans);
  const items = all.filter((x) => !taken.has(x.id));
  const toggle = (id: string) => pf.setPicker((p) => (p ? { ...p, sel: p.sel.includes(id) ? p.sel.filter((x) => x !== id) : [...p.sel, id] } : p));
  return (
    <>
      <p className="pf-lead" style={{ margin: '0 0 6px' }}>
        {pk.kind === 'photos' ? 'Sous-dossier « Photos » du projet. Seules les images ajoutées sont reprises.' : 'Dossier « Plans / Plan d’exécution » du projet.'}
      </p>
      {items.length === 0 && <div className="pf-empty" style={{ marginTop: 8 }}>Tout est déjà ajouté.</div>}
      {items.map((x) => {
        const on = pk.sel.includes(x.id);
        return (
          <button key={x.id} type="button" role="checkbox" aria-checked={on} className="pf-row ruled" onClick={() => toggle(x.id)}>
            <Check on={on} />
            <span style={{ flex: 1, minWidth: 0 }}>
              <span style={{ display: 'block', fontSize: 15, wordBreak: 'break-word' }}>{x.name}</span>
              <span className="pf-small" style={{ display: 'block' }}>{fileMeta(x.size, x.lastModified)}</span>
            </span>
          </button>
        );
      })}
      <button
        type="button"
        className="btn btn-primary btn-block pf-big"
        style={{ marginTop: 14 }}
        disabled={!pk.sel.length}
        onClick={() => {
          const key = pk.kind;
          pf.setSo((s) => ({
            ...s,
            [key]: [...s[key], ...pk.sel],
            selPhoto: key === 'photos' && pk.sel.length ? s.photos.length : s.selPhoto,
          }));
          pf.setPicker(null);
          pf.setSheet(null);
        }}
      >
        Ajouter ({pk.sel.length})
      </button>
    </>
  );
}
