// OneDrive / SharePoint via Microsoft Graph (repris de graph/onedrive.py).
import type { DriveGateway, DriveItem } from '../core/gateways';
import { graph, graphAll, graphFetch, GraphError, seg } from './client';

const SELECT = '$select=id,name,size,folder,file,lastModifiedDateTime,webUrl,parentReference,remoteItem';
const LINK_GUIDANCE = 'Dans OneDrive ou SharePoint, copiez le lien de partage de l’élément d’origine et collez-le ici.';

interface RawItem {
  id?: string;
  name?: string;
  size?: number;
  folder?: unknown;
  lastModifiedDateTime?: string;
  webUrl?: string;
  parentReference?: { driveId?: string };
  remoteItem?: RawItem;
}

function toItem(raw: RawItem, fallbackDrive = ''): DriveItem {
  if (raw.remoteItem) return toItem({ ...raw.remoteItem, name: raw.remoteItem.name ?? raw.name });
  const driveId = raw.parentReference?.driveId ?? fallbackDrive;
  if (!raw.id || !driveId) throw new GraphError('Réponse OneDrive incomplète. ' + LINK_GUIDANCE);
  return {
    driveId,
    id: raw.id,
    name: raw.name ?? '',
    isFolder: !!raw.folder,
    size: raw.size ?? 0,
    lastModified: raw.lastModifiedDateTime ?? '',
    webUrl: raw.webUrl ?? '',
  };
}

const itemPath = (i: { driveId: string; id: string }) => `/drives/${seg(i.driveId)}/items/${seg(i.id)}`;

function shareToken(url: string): string {
  const bytes = new TextEncoder().encode(url);
  let bin = '';
  bytes.forEach((b) => (bin += String.fromCharCode(b)));
  return 'u!' + btoa(bin).replace(/=+$/, '').replace(/\//g, '_').replace(/\+/g, '-');
}

export const graphDrive: DriveGateway = {
  async resolve(reference) {
    const ref = reference.trim();
    if (!ref) throw new GraphError('Saisissez un lien de partage ou un chemin OneDrive.');
    if (/^https?:\/\//i.test(ref)) {
      const url = new URL(ref);
      const host = url.hostname.toLowerCase();
      if (url.protocol !== 'https:' || !(host === '1drv.ms' || host === 'onedrive.live.com' || host.endsWith('.sharepoint.com'))) {
        throw new GraphError('Le lien doit être un lien HTTPS OneDrive ou SharePoint. ' + LINK_GUIDANCE);
      }
      const raw = await graph<RawItem>('GET', `/shares/${shareToken(ref)}/driveItem?${SELECT}`, { headers: { Prefer: 'redeemSharingLink' } });
      return toItem(raw);
    }
    const path = ref.replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
    const encoded = path.split('/').map(seg).join('/');
    try {
      return toItem(await graph<RawItem>('GET', `/me/drive/root:/${encoded}:?${SELECT}`));
    } catch (error) {
      if (!(error instanceof GraphError) || error.status !== 404) throw error;
    }
    // Raccourci « Ajouter à mon OneDrive » vers une bibliothèque partagée.
    const [first, ...rest] = path.split('/');
    if (rest.length) {
      const shortcut = await graph<RawItem>('GET', `/me/drive/root:/${seg(first)}:?${SELECT}`).catch(() => null);
      if (shortcut?.remoteItem) {
        const base = toItem(shortcut);
        return toItem(await graph<RawItem>('GET', `${itemPath(base)}:/${rest.map(seg).join('/')}:?${SELECT}`));
      }
    }
    throw new GraphError('Ce chemin n’existe pas dans le compte Microsoft connecté. ' + LINK_GUIDANCE, 404);
  },

  async listChildren(folder) {
    const raw = await graphAll<RawItem>(`${itemPath(folder)}/children?${SELECT}&$top=999`);
    return raw.map((r) => toItem(r, folder.driveId));
  },

  async ensureFolder(parent, name) {
    try {
      const raw = await graph<RawItem>('POST', `${itemPath(parent)}/children`, {
        body: { name, folder: {}, '@microsoft.graph.conflictBehavior': 'fail' },
      });
      return toItem(raw, parent.driveId);
    } catch (error) {
      if (!(error instanceof GraphError) || error.status !== 409) throw error;
      const existing = (await graphDrive.listChildren(parent)).find((c) => c.isFolder && c.name.toLowerCase() === name.toLowerCase());
      if (!existing) throw error;
      return existing;
    }
  },

  async copyFile(source, destination, name) {
    const response = await graphFetch('POST', `${itemPath(source)}/copy?@microsoft.graph.conflictBehavior=fail`, {
      body: { parentReference: { driveId: destination.driveId, id: destination.id }, name },
    });
    const monitor = response.headers.get('Location');
    const deadline = Date.now() + 120_000;
    let delay = 400;
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, delay));
      delay = Math.min(delay * 1.5, 3000);
      if (monitor) {
        const status = await fetch(monitor).then((r) => r.json()).catch(() => null);
        if (status?.status === 'failed') throw new GraphError(`Copie impossible de ${source.name}.`);
        if (status?.status === 'completed' && status.resourceId) {
          return toItem(await graph<RawItem>('GET', `/drives/${seg(destination.driveId)}/items/${seg(status.resourceId)}?${SELECT}`), destination.driveId);
        }
        if (status) continue;
      }
      // Sans moniteur lisible : on attend que le fichier apparaisse dans le dossier cible.
      const hit = (await graphDrive.listChildren(destination)).find((c) => c.name.toLowerCase() === name.toLowerCase());
      if (hit) return hit;
    }
    throw new GraphError(`La copie de ${source.name} n’est pas terminée après 2 minutes. Actualisez avant de recommencer.`);
  },

  async rename(item, name) {
    return toItem(await graph<RawItem>('PATCH', itemPath(item), { body: { name } }), item.driveId);
  },

  async remove(item) {
    await graph('DELETE', itemPath(item));
  },

  async thumbnailUrl(item) {
    const r = await graph<{ value?: { medium?: { url?: string } }[] }>('GET', `${itemPath(item)}/thumbnails?select=medium`).catch(() => null);
    return r?.value?.[0]?.medium?.url ?? null;
  },

  async pdf(item) {
    const response = await graphFetch('GET', `${itemPath(item)}/content?format=pdf`, { headers: { Accept: '*/*' } });
    return response.blob();
  },
};
