// Outlook via Graph : envoi de la notification et dossiers de courrier du projet.
import type { MailGateway } from '../core/gateways';
import { graph, graphAll, seg } from './client';

const scopes = ['base', 'mail'] as ('base' | 'mail')[];
const folder = (id: string | null) => (id ? `/me/mailFolders/${seg(id)}` : '/me/mailFolders/msgfolderroot');

export const graphMail: MailGateway = {
  async send({ to, subject, html }) {
    await graph('POST', '/me/sendMail', {
      scopes,
      body: {
        message: {
          subject,
          body: { contentType: 'HTML', content: html },
          toRecipients: to.map((r) => ({ emailAddress: { address: r.email, name: r.name } })),
        },
        saveToSentItems: true,
      },
    });
  },

  async childFolders(parentId) {
    const raw = await graphAll<{ id: string; displayName: string }>(`${folder(parentId)}/childFolders?$select=id,displayName&$top=250`, { scopes });
    return raw.map((f) => ({ id: f.id, displayName: f.displayName }));
  },

  async createFolder(parentId, name) {
    const f = await graph<{ id: string; displayName: string }>('POST', `${folder(parentId)}/childFolders`, { scopes, body: { displayName: name } });
    return { id: f.id, displayName: f.displayName };
  },

  async deleteFolder(id) {
    await graph('DELETE', `/me/mailFolders/${seg(id)}`, { scopes });
  },
};
