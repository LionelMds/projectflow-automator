import {
  addressStart,
  buildSnapshot,
  changedFields,
  editedValues,
  editFromRow,
  filterRows,
  isProjectNumber,
  rowAddress,
  sameCells,
  visibleRows,
} from '@/lib/repertoire/rows';

const usedRange = {
  address: "'2026'!A1:L6",
  values: [
    ['No.', 'Date', 'Client', 'Contact', 'Désignation', 'Montant'],
    ['2026-5247', 46317, 'Garage du Lac', 'P. Rochat', 'Structure auvent parking', 1200],
    ['2026-5249', 46324, 'Régie Favre', 'M. Dupasquier', 'Garde-corps escalier extérieur', ''],
    ['2026-5249-2', 46329, 'Régie Favre', 'M. Dupasquier', 'Garde-corps palier', ''],
    ['2026-5250', '', '', '', '', ''],
    ['2026-5251', '', '', '', '', ''],
  ],
  text: [
    ['No.', 'Date', 'Client', 'Contact', 'Désignation', 'Montant'],
    ['2026-5247', '22.10.2026', 'Garage du Lac', 'P. Rochat', 'Structure auvent parking', '1200'],
    ['2026-5249', '29.10.2026', 'Régie Favre', 'M. Dupasquier', 'Garde-corps escalier extérieur', ''],
    ['2026-5249-2', '03.11.2026', 'Régie Favre', 'M. Dupasquier', 'Garde-corps palier', ''],
    ['2026-5250', '', '', '', '', ''],
    ['2026-5251', '', '', '', '', ''],
  ],
};

describe('répertoire chantier', () => {
  it('reads the start of a range address', () => {
    expect(addressStart("'2026'!A1:L400")).toEqual({ row: 1, column: 1 });
    expect(addressStart('Feuil1!$C$12:$L$40')).toEqual({ row: 12, column: 3 });
    expect(addressStart('AB7')).toEqual({ row: 7, column: 28 });
  });

  it('recognises dossier numbers like the desktop', () => {
    expect(isProjectNumber('2026-5245')).toBe(true);
    expect(isProjectNumber('2026-5245-2')).toBe(true);
    expect(isProjectNumber('2026–5245')).toBe(true);
    expect(isProjectNumber('No.')).toBe(false);
    expect(isProjectNumber('2026-12')).toBe(false);
  });

  it('keeps A:E only, with sheet rows and the next free main row', () => {
    const snapshot = buildSnapshot(2026, usedRange);
    expect(snapshot.rows.map((row) => row.number)).toEqual([
      '2026-5247',
      '2026-5249',
      '2026-5249-2',
      '2026-5250',
      '2026-5251',
    ]);
    expect(snapshot.rows[0].values).toHaveLength(5);
    expect(snapshot.rows[0].sheetRow).toBe(2);
    expect(snapshot.rows[1].texts[1]).toBe('29.10.2026');
    expect(snapshot.nextAvailable).toEqual({ number: '2026-5250', sheetRow: 5 });
  });

  it('shifts a used range that does not start in column A', () => {
    const snapshot = buildSnapshot(2026, {
      address: "'2026'!B3:F3",
      values: [['2026-5300', 46000, 'Client', 'Contact', 'Désignation']],
    });
    // Column A was outside the used range: the number read from B is not in column A.
    expect(snapshot.rows).toHaveLength(0);
  });

  it('lists filled rows and searches without accents or case', () => {
    const rows = visibleRows(buildSnapshot(2026, usedRange));
    expect(rows).toHaveLength(3);
    expect(filterRows(rows, 'regie palier').map((row) => row.number)).toEqual(['2026-5249-2']);
    expect(filterRows(rows, '5247')).toHaveLength(1);
    expect(filterRows(rows, '')).toHaveLength(3);
  });

  it('writes back untouched cells raw and retyped cells as text', () => {
    const row = buildSnapshot(2026, usedRange).rows[1];
    const edit = { ...editFromRow(row), contact: 'M. Dupasquier, 026 000 00 00' };
    expect([...changedFields(row, edit)]).toEqual(['contact']);
    expect(editedValues(row, edit)).toEqual([
      '2026-5249',
      46324,
      'Régie Favre',
      'M. Dupasquier, 026 000 00 00',
      'Garde-corps escalier extérieur',
    ]);
    expect(editedValues(row, { ...edit, date: '30.10.2026' })[1]).toBe('30.10.2026');
    expect(rowAddress(row.sheetRow)).toBe('A3:E3');
  });

  it('compares rows by trimmed text, as the conflict check of the desktop', () => {
    expect(sameCells(['2026-1', 46000, 'A ', '', null], ['2026-1', '46000', 'A', '', ''])).toBe(true);
    expect(sameCells(['2026-1', 46000, 'A', '', ''], ['2026-1', 46000, 'B', '', ''])).toBe(false);
  });
});
