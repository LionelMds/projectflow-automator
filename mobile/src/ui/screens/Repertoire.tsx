import { Blueprint, Icon, ICONS, SearchIcon } from '../kit';
import type { PF } from '../useProjectFlow';

export function RepertoireScreen({ pf }: { pf: PF }) {
  const snap = pf.snapshots[pf.repYear];
  const q = pf.search.trim().toLowerCase();
  const rows = (snap?.rows ?? []).filter((r) => !q || [r.number, ...r.text.slice(1)].join(' ').toLowerCase().includes(q));
  const status = pf.repError
    ? pf.repError
    : !snap
      ? pf.repLoading ? 'Chargement du répertoire…' : 'Répertoire non chargé.'
      : `${snap.rows.length} lignes chargées. Prochaine ligne disponible : ${snap.next?.number ?? '—'}${snap.next ? ` (ligne Excel ${snap.next.rowIndex + 1})` : ''}. Colonnes A à E modifiables.`;

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
        <h1 className="pf-h1" style={{ margin: '0 0 14px', flex: 1 }}>Répertoire chantier</h1>
        <button type="button" className="btn btn-ghost btn-icon" style={{ width: 44, height: 44 }} aria-label="Actualiser" disabled={pf.repLoading} onClick={() => pf.loadSnapshot(pf.repYear)}>
          {pf.repLoading ? <span className="pf-spinner" /> : <Icon d={ICONS.refresh} />}
        </button>
      </div>
      <div className="pf-grid" style={{ gridTemplateColumns: '96px 1fr', marginBottom: 12 }}>
        <select className="input pf-input" aria-label="Année" value={pf.repYear} onChange={(e) => pf.setRepYear(e.target.value)}>
          {pf.years.map((y) => <option key={y}>{y}</option>)}
        </select>
        <div style={{ position: 'relative' }}>
          <SearchIcon />
          <input className="input pf-input" type="search" aria-label="Rechercher" placeholder="Numéro, client, contact…" value={pf.search} onChange={(e) => pf.setSearch(e.target.value)} style={{ paddingLeft: 34 }} />
        </div>
      </div>
      <p className={`pf-lead${pf.repError ? ' pf-danger-text' : ''}`} style={{ margin: '0 0 16px' }}>{status}</p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {rows.map((r) => {
          const occupied = r.text.slice(1).some((v) => v.trim());
          if (occupied) {
            return (
              <Blueprint key={r.rowIndex} as="button" type="button" className="pf-rep-card" onClick={() => pf.tapRow(r)}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
                  <span className="pf-rep-n">{r.number}</span>
                  <span className="pf-small" style={{ marginLeft: 'auto' }}>{r.text[1]}</span>
                </div>
                <div style={{ fontSize: 15, fontWeight: 500, marginTop: 2 }}>{r.text[2]}</div>
                <div style={{ fontSize: 14, marginTop: 1 }}>{r.text[4]}</div>
                <div className="pf-small" style={{ marginTop: 4 }}>{r.text[3]}</div>
              </Blueprint>
            );
          }
          if (snap?.next?.rowIndex === r.rowIndex) {
            return (
              <button key={r.rowIndex} type="button" className="pf-next" onClick={() => pf.tapRow(r)}>
                <span className="pf-rep-n" style={{ color: 'var(--color-accent-800)' }}>{r.number}</span>
                <span className="tag tag-accent" style={{ background: 'var(--color-bg)' }}>Prochaine ligne disponible</span>
                <Icon d={ICONS.chevron} size={18} style={{ marginLeft: 'auto', color: 'var(--color-accent-800)' }} />
              </button>
            );
          }
          return (
            <button key={r.rowIndex} type="button" className="pf-free" onClick={() => pf.tapRow(r)}>
              <span style={{ fontFamily: 'var(--font-heading)', fontWeight: 600, fontSize: 18 }}>{r.number}</span>
              <span style={{ fontSize: 13 }}>Disponible</span>
            </button>
          );
        })}
      </div>
    </>
  );
}
