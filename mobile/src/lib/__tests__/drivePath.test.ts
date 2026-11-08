import { driveItemByPath, drivePathFromSetting, GraphError } from '@/lib/graph';

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

const token = async () => 'token';
const SELECT = '$select=id,name';

describe('drivePathFromSetting', () => {
  it('keeps what follows the OneDrive folder of a path copied from the desktop', () => {
    expect(drivePathFromSetting('E:\\OneDrive - Balz Metal Sa\\Entreprise\\REPERTOIR CHANTIER ET FACTURES.xlsx')).toBe(
      'Entreprise/REPERTOIR CHANTIER ET FACTURES.xlsx',
    );
    expect(drivePathFromSetting('E:\\OneDrive - Balz Metal Sa\\Clients')).toBe('Clients');
    expect(drivePathFromSetting('Clients/')).toBe('Clients');
  });
});

describe('driveItemByPath', () => {
  it('reads a path of the signed-in OneDrive directly', async () => {
    const calls = fakeGraph({ [`/me/drive/root:/Clients:?${SELECT}`]: { status: 200, body: { id: 'C1', name: 'Clients' } } });
    await expect(driveItemByPath(token, 'Clients', SELECT)).resolves.toEqual({ id: 'C1', name: 'Clients' });
    expect(calls).toHaveLength(1);
  });

  it('follows a shortcut at the root into the shared drive, as the desktop does', async () => {
    const calls = fakeGraph({
      '/me/drive/root:/Entreprise:?$select=id,remoteItem': {
        status: 200,
        body: { id: 'S1', remoteItem: { id: 'R1', parentReference: { driveId: 'D1' } } },
      },
      [`/drives/D1/items/R1:/00-Biblioth%C3%A8que%20CAO/Racine:?${SELECT}`]: {
        status: 200,
        body: { id: 'X1', name: 'Racine' },
      },
    });
    await expect(driveItemByPath(token, 'Entreprise/00-Bibliothèque CAO/Racine', SELECT)).resolves.toEqual({
      id: 'X1',
      name: 'Racine',
    });
    expect(calls[0]).toBe(`/me/drive/root:/Entreprise/00-Biblioth%C3%A8que%20CAO/Racine:?${SELECT}`);
  });

  it('is null when the path exists neither directly nor through a shortcut', async () => {
    fakeGraph({ '/me/drive/root:/Entreprise:?$select=id,remoteItem': { status: 200, body: { id: 'F1' } } });
    await expect(driveItemByPath(token, 'Entreprise/Absent.xlsx', SELECT)).resolves.toBeNull();
    fakeGraph({});
    await expect(driveItemByPath(token, 'Absent', SELECT)).resolves.toBeNull();
  });

  it('lets other Microsoft errors through', async () => {
    fakeGraph({ [`/me/drive/root:/Clients:?${SELECT}`]: { status: 403, body: { error: { code: 'accessDenied' } } } });
    await expect(driveItemByPath(token, 'Clients', SELECT)).rejects.toBeInstanceOf(GraphError);
  });
});
