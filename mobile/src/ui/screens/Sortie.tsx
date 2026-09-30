import { fileMeta } from '../../core/text';
import { Blueprint, Icon, ICONS, SectionHead } from '../kit';
import type { PF } from '../useProjectFlow';

export function SortieScreen({ pf }: { pf: PF }) {
  const { so, setSo } = pf;
  const inv = so.inv;
  const photos = inv ? so.photos.map((id) => inv.photos.find((p) => p.id === id)!).filter(Boolean) : [];
  const plans = inv ? so.plans.map((id) => inv.plans.find((p) => p.id === id)!).filter(Boolean) : [];
  const selected = so.selPhoto >= 0 ? photos[so.selPhoto] : undefined;

  return (
    <>
      <h1 className="pf-h1">Sortie dossier</h1>
      <p className="pf-lead">Prépare un dossier à transmettre ou imprimer. Les fichiers sources ne sont jamais modifiés.</p>

      <Blueprint className="pf-card">
        <SectionHead title="Projet à sortir" />
        <div className="pf-grid" style={{ gridTemplateColumns: '86px 1fr' }}>
          <div className="field">
            <label htmlFor="so-year">Année</label>
            <select id="so-year" className="input pf-input" value={so.year} onChange={(e) => { const v = e.target.value; setSo((x) => ({ ...x, year: v })); }}>
              {pf.years.map((y) => <option key={y}>{y}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="so-id">Numéro</label>
            <input
              id="so-id"
              className="input pf-input"
              placeholder={`${so.year}-4995`}
              value={so.id}
              onChange={(e) => { const v = e.target.value; setSo((x) => ({ ...x, id: v, err: '' })); }}
              onKeyDown={(e) => e.key === 'Enter' && pf.loadSortie()}
            />
          </div>
        </div>
        <button type="button" className="btn btn-secondary btn-block" style={{ minHeight: 44, marginTop: 12 }} disabled={so.loading} onClick={pf.loadSortie}>
          {so.loading ? 'Chargement…' : 'Charger'}
        </button>
        {so.err && <div style={{ marginTop: 10, fontSize: 13 }} className="pf-danger-text">{so.err}</div>}
      </Blueprint>

      {!inv && <div className="pf-empty">Chargez un projet pour préparer les documents de sortie.</div>}

      {inv && (
        <>
          <p className="pf-lead" style={{ margin: '0 0 18px' }}>
            {inv.fiches.length} fiche(s), {inv.mesures.length} prise(s) de cote, {inv.photos.length} photo(s), {inv.plans.length} plan(s) disponible(s).
          </p>

          <Blueprint className="pf-card">
            <SectionHead num="01" title="Fiche dossier" mb={10} right={<span className="tag tag-accent">Obligatoire</span>} />
            {inv.fiches.length === 0 && <div className="pf-small" style={{ padding: '6px 0' }}>Aucune fiche Excel dans le dossier projet.</div>}
            {inv.fiches.map((fi, i) => (
              <label key={fi.id} className="radio pf-radio-row">
                <input type="radio" name="fiche" checked={so.fiche === i} onChange={() => setSo((x) => ({ ...x, fiche: i }))} />
                <span className="dot" style={{ marginTop: 2 }} />
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ display: 'block', fontSize: 14, wordBreak: 'break-word' }}>{fi.name}</span>
                  <span className="pf-small" style={{ display: 'block' }}>{fileMeta(fi.size, fi.lastModified)}</span>
                </span>
              </label>
            ))}
          </Blueprint>

          <Blueprint className="pf-card">
            <SectionHead num="02" title="Prise de cote initiale" mb={10} right={<span className="tag tag-neutral">PDF</span>} />
            {inv.mesures.length === 0 && <div className="pf-small" style={{ padding: '6px 0' }}>Aucun PDF à la racine du dossier projet.</div>}
            {inv.mesures.map((me, i) => (
              <label key={me.id} className="radio pf-radio-row">
                <input type="radio" name="mesure" checked={so.mesure === i} onChange={() => setSo((x) => ({ ...x, mesure: i }))} />
                <span className="dot" style={{ marginTop: 2 }} />
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ display: 'block', fontSize: 14, wordBreak: 'break-word' }}>{me.name}</span>
                  <span className="pf-small" style={{ display: 'block' }}>{fileMeta(me.size, me.lastModified)}</span>
                </span>
              </label>
            ))}
            {inv.mesures.length > 0 && (
              <label className="radio pf-radio-row">
                <input type="radio" name="mesure" checked={so.mesure === -1} onChange={() => setSo((x) => ({ ...x, mesure: -1 }))} />
                <span className="dot" style={{ marginTop: 2 }} />
                <span style={{ fontSize: 14 }}>Aucune</span>
              </label>
            )}
          </Blueprint>

          <Blueprint className="pf-card">
            <SectionHead num="03" title="Photos" mb={12} right={<span className="pf-small">{photos.length} sélectionnée(s)</span>} />
            <div className="pf-preview pf-hatch">
              {selected ? (
                <>
                  {pf.thumbs[selected.id] && <img src={pf.thumbs[selected.id]!} alt="" />}
                  <div className="pf-preview-label">
                    <div className="pf-num" style={{ fontSize: 12, textTransform: 'uppercase', marginBottom: 4 }}>Aperçu</div>
                    <div style={{ color: 'var(--color-text)', fontSize: 14 }}>{selected.name}</div>
                  </div>
                </>
              ) : (
                <span>Aucune photo sélectionnée</span>
              )}
            </div>
            {photos.length > 0 && (
              <div className="pf-tiles">
                {photos.map((p, i) => (
                  <button
                    key={p.id}
                    type="button"
                    className="pf-tile pf-hatch"
                    style={pf.thumbs[p.id] ? { backgroundImage: `url("${pf.thumbs[p.id]}")` } : undefined}
                    aria-pressed={so.selPhoto === i}
                    onClick={() => setSo((x) => ({ ...x, selPhoto: i }))}
                  >
                    <span className="pf-tile-name">{p.name}</span>
                    {so.selPhoto === i && <span className="pf-sel" />}
                  </button>
                ))}
              </div>
            )}
            <div className="pf-grid" style={{ gridTemplateColumns: '1fr 1fr', marginTop: 12 }}>
              <button type="button" className="btn btn-secondary" style={{ minHeight: 44 }} onClick={() => { pf.setPicker({ kind: 'photos', sel: [] }); pf.setSheet('picker'); }}>Parcourir</button>
              <button
                type="button"
                className="btn btn-secondary"
                style={{ minHeight: 44 }}
                disabled={so.selPhoto < 0}
                onClick={() => setSo((x) => ({ ...x, photos: x.photos.filter((_, i) => i !== x.selPhoto), selPhoto: -1 }))}
              >
                Retirer
              </button>
            </div>
          </Blueprint>

          <Blueprint className="pf-card">
            <SectionHead num="04" title="Plans d’exécution" mb={10} right={<span className="tag tag-neutral">PDF</span>} />
            {plans.length === 0 && <div className="pf-small" style={{ fontSize: 13, padding: '6px 0 12px' }}>Aucun plan ajouté.</div>}
            {plans.map((pl) => (
              <div key={pl.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0', borderTop: '1px solid var(--color-divider)' }}>
                <span style={{ flex: 1, minWidth: 0, fontSize: 14, wordBreak: 'break-word' }}>{pl.name}</span>
                <button type="button" className="btn btn-ghost btn-icon" style={{ width: 44, height: 44 }} aria-label={`Retirer ${pl.name}`} onClick={() => setSo((x) => ({ ...x, plans: x.plans.filter((id) => id !== pl.id) }))}>
                  <Icon d={ICONS.close} size={18} />
                </button>
              </div>
            ))}
            <button type="button" className="btn btn-secondary btn-block" style={{ minHeight: 44 }} onClick={() => { pf.setPicker({ kind: 'plans', sel: [] }); pf.setSheet('picker'); }}>Parcourir</button>
          </Blueprint>
        </>
      )}
    </>
  );
}
