// E-mail « Nouvelle assignation » envoyé aux membres assignés à la tâche Planner.

export interface NotificationInput {
  number: string;
  designation: string;
  societe: string;
  contact: string;
  localisation: string;
  gerePar: string;
  initials: string;
  planName: string;
  bucketName: string;
  due: Date | null;
  folderPath: string;
  folderUrl: string;
  taskUrl: string;
  senderName: string;
}

const dash = (v: string) => v.trim() || '—';
const two = (n: number) => String(n).padStart(2, '0');

export function notificationRows(n: NotificationInput): { k: string; v: string }[] {
  return [
    ['N° projet', n.number],
    ['Désignation', dash(n.designation)],
    ['Client', dash(n.societe)],
    ['Contact', dash(n.contact)],
    ['Localisation', dash(n.localisation)],
    ['Géré par', `${dash(n.gerePar)}${n.initials ? ` (${n.initials})` : ''}`],
    ['Planner', `${n.planName || '—'} · ${n.bucketName || '—'}`],
    ['Échéance', n.due ? `${two(n.due.getDate())}.${two(n.due.getMonth() + 1)}.${n.due.getFullYear()}` : 'Non définie'],
    ['Dossier', n.folderPath],
  ].map(([k, v]) => ({ k, v }));
}

export function notificationSubject(number: string, designation: string): string {
  return `[ProjectFlow] Vous êtes assigné(e) au projet ${number}${designation.trim() ? ' – ' + designation.trim() : ''}`;
}

const esc = (v: string) => v.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);

/** Corps HTML aux couleurs Industry : tableaux et styles en ligne pour Outlook. */
export function notificationHtml(n: NotificationInput): string {
  const rows = notificationRows(n)
    .map(
      (r) =>
        `<tr><td style="width:110px;padding:8px 0;border-bottom:1px solid #d4d4d7;color:#5d5d60;font-size:13px;vertical-align:top">${esc(r.k)}</td>` +
        `<td style="padding:8px 0;border-bottom:1px solid #d4d4d7;font-size:13px;color:#1d1f20">${esc(r.v)}</td></tr>`,
    )
    .join('');
  const planner = n.taskUrl
    ? `<p style="margin:10px 0 0;font-size:13px"><a href="${esc(n.taskUrl)}" style="color:#416180">Ouvrir la tâche Planner</a></p>`
    : '';
  return `<!doctype html><html><body style="margin:0;padding:24px;background:#f2f2f3;font-family:Barlow,Segoe UI,Arial,sans-serif;color:#1d1f20">
<table role="presentation" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;margin:0 auto;background:#ffffff;border:1px solid #c9c9cc">
<tr><td style="padding:22px 20px">
<div style="font-family:'Barlow Condensed',Arial Narrow,Arial,sans-serif;font-weight:600;font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:#416180">Nouvelle assignation</div>
<div style="font-family:'Barlow Condensed',Arial Narrow,Arial,sans-serif;font-weight:600;font-size:26px;line-height:1.1;margin:4px 0 10px">${esc(n.number)}</div>
<p style="font-size:14px;margin:0 0 14px">Bonjour, vous avez été assigné(e) à ce projet. Voici les informations générales pour en prendre connaissance.</p>
<table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;border-top:1px solid #d4d4d7">${rows}</table>
<table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;margin-top:16px"><tr><td style="background:#5980a6;text-align:center">
<a href="${esc(n.folderUrl)}" style="display:block;padding:14px 16px;color:#f2f2f3;text-decoration:none;font-size:16px;font-weight:500">Prendre connaissance du projet</a></td></tr></table>
${planner}
<div style="font-size:11px;color:#5d5d60;margin-top:12px">Message automatique · ProjectFlow Automator, Balz Métal SA · envoyé par ${esc(n.senderName)}</div>
</td></tr></table></body></html>`;
}
