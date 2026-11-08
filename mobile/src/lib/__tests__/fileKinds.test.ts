import { dateText, fileKind, localName, officeLink, openerName, sizeText } from '@/lib/projects/fileKinds';

describe('fichiers d’un dossier', () => {
  it('knows the kind of a file from its extension, whatever the case', () => {
    expect(fileKind('2026-4996 - Fiche dossier clients.xlsx')).toBe('excel');
    expect(fileKind('Offre.DOCX')).toBe('word');
    expect(fileKind('Plan facade.pdf')).toBe('pdf');
    expect(fileKind('IMG_0412.HEIC')).toBe('image');
    expect(fileKind('Garde-corps.SLDASM')).toBe('cad');
    expect(fileKind('sans extension')).toBe('other');
    expect(fileKind('.gitignore')).toBe('other');
  });

  it('opens Office files in their app, for editing on OneDrive', () => {
    const address = 'https://balz-my.sharepoint.com/personal/x/Documents/Clients/2026/2026-4996/Fiche.xlsx';
    expect(officeLink('Fiche.xlsx', address)).toBe(`ms-excel:ofe|u|${address}`);
    expect(officeLink('Offre.docx', address)).toBe(`ms-word:ofe|u|${address}`);
    expect(officeLink('Plan.pdf', address)).toBeNull();
    // The demo has no address: nothing to open.
    expect(officeLink('Fiche.xlsx', '')).toBeNull();
    expect(openerName('Fiche.xlsm')).toBe('Excel');
    expect(openerName('Plan.pdf')).toBe('Aperçu');
  });

  it('writes sizes, dates and local names as the phone shows them', () => {
    expect(sizeText(0)).toBe('');
    expect(sizeText(300)).toBe('1 Ko');
    expect(sizeText(250 * 1024)).toBe('250 Ko');
    expect(sizeText(3.4 * 1024 * 1024)).toBe('3,4 Mo');
    expect(dateText('2026-11-03T08:15:00Z')).toMatch(/^0[34]\.11\.2026$/);
    expect(dateText('')).toBe('');
    expect(localName('Plan: façade/nord?.pdf')).toBe('Plan- façade-nord-.pdf');
  });
});
