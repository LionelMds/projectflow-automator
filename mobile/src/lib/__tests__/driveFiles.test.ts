import { graphDrive } from '@/lib/projects/drive';

type Reply = { status: number; body?: unknown };

/** Fake Graph: each request path (without the host) gets its reply; anything else is a 404. */
function fakeGraph(replies: Record<string, Reply>) {
  const calls: string[] = [];
  globalThis.fetch = jest.fn(async (url: string) => {
    const path = url.replace('https://graph.microsoft.com/v1.0', '');
    calls.push(path);
    const reply = replies[path] ?? { status: 404, body: { error: { code: 'itemNotFound' } } };
    const text = reply.body === undefined ? '' : JSON.stringify(reply.body);
    return {
      ok: reply.status < 400,
      status: reply.status,
      json: async () => JSON.parse(text),
      text: async () => text,
    } as Response;
  }) as unknown as typeof fetch;
  return calls;
}

const drive = graphDrive(async () => 'token');
const item = { driveId: 'D', id: 'I' };
const SELECTED = '/drives/D/items/I?select=id,@microsoft.graph.downloadUrl';

describe('fichiers des dossiers (Microsoft Graph)', () => {
  it('asks the download address as Microsoft documents it', async () => {
    const calls = fakeGraph({ [SELECTED]: { status: 200, body: { id: 'I', '@microsoft.graph.downloadUrl': 'https://dl/1' } } });
    await expect(drive.downloadUrl(item)).resolves.toBe('https://dl/1');
    expect(calls).toEqual([SELECTED]);
  });

  it('reads the whole item when the selection comes back without the address', async () => {
    const calls = fakeGraph({
      [SELECTED]: { status: 200, body: { id: 'I' } },
      '/drives/D/items/I': { status: 200, body: { id: 'I', name: 'Plan.pdf', '@microsoft.graph.downloadUrl': 'https://dl/2' } },
    });
    await expect(drive.downloadUrl(item)).resolves.toBe('https://dl/2');
    expect(calls).toEqual([SELECTED, '/drives/D/items/I']);
  });

  it('says so when Microsoft gives no address at all', async () => {
    fakeGraph({ [SELECTED]: { status: 200, body: { id: 'I' } }, '/drives/D/items/I': { status: 200, body: { id: 'I' } } });
    await expect(drive.downloadUrl(item)).rejects.toThrow('Microsoft ne donne pas ce fichier à télécharger.');
  });

  it('keeps the file’s own address for the Office apps', async () => {
    fakeGraph({
      '/drives/D/items/F/children?$top=999&$select=id,name,size,webUrl,webDavUrl,lastModifiedDateTime,folder,file,parentReference,remoteItem': {
        status: 200,
        body: {
          value: [
            {
              id: 'X',
              name: 'Fiche.xlsx',
              webUrl: 'https://balz-my.sharepoint.com/personal/x/_layouts/15/Doc.aspx?sourcedoc=%7B1%7D&file=Fiche.xlsx',
              webDavUrl: 'https://balz-my.sharepoint.com/personal/x/Documents/Clients/Fiche.xlsx',
              parentReference: { driveId: 'D' },
              file: {},
            },
          ],
        },
      },
    });
    const [fiche] = await drive.children({ driveId: 'D', id: 'F' });
    expect(fiche.davUrl).toBe('https://balz-my.sharepoint.com/personal/x/Documents/Clients/Fiche.xlsx');
  });
});
