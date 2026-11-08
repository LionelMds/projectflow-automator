/**
 * Opening a file of a dossier as the phone opens files: an Office file in its app (edited in
 * place on OneDrive), any other one in the system viewer (Quick Look on iOS, the app chosen
 * for its type on Android), with the share sheet to save it in Files or send it.
 */

import { Directory, File, Paths } from 'expo-file-system';
import * as Sharing from 'expo-sharing';
import { Linking, Platform } from 'react-native';

import type { DriveGateway, DriveItem } from '@/lib/projects/drive';
import { localName, officeLink } from '@/lib/projects/fileKinds';

type Openable = { name: string; webUrl: string; davUrl?: string };

/**
 * An Office file in its app, at the file's own address; false when the app is not there, or
 * without that address (the Office for the web page of `webUrl` keeps the app loading).
 */
async function openInOffice(item: Openable): Promise<boolean> {
  const link = Platform.OS === 'web' ? null : officeLink(item.name, item.davUrl ?? '');
  if (!link) return false;
  try {
    await Linking.openURL(link);
    return true;
  } catch {
    return false;
  }
}

/** The fiche (or any workbook) in Excel, else Excel for the web. */
export async function openWorkbook(item: Openable): Promise<void> {
  if (!item.webUrl) throw new Error(`Mode démo : ${item.name}, rien à ouvrir chez Microsoft.`);
  if (await openInOffice(item)) return;
  await Linking.openURL(item.webUrl);
}

/** A copy of the file in the app's cache, replaced at each opening. */
async function download(drive: DriveGateway, item: DriveItem): Promise<File> {
  const url = await drive.downloadUrl(item);
  const folder = new Directory(Paths.cache, 'dossiers');
  folder.create({ idempotent: true, intermediates: true });
  return File.downloadFileAsync(url, new File(folder, localName(item.name)), { idempotent: true });
}

/** The file in the system viewer, or the share sheet when the phone has no viewer for it. */
export async function openDriveFile(drive: DriveGateway, item: DriveItem): Promise<void> {
  if (await openInOffice(item)) return;
  if (Platform.OS === 'web') {
    if (!item.webUrl) throw new Error('Mode démo : les fichiers d’exemple n’ont pas de contenu à ouvrir.');
    await Linking.openURL(item.webUrl);
    return;
  }
  const file = await download(drive, item);
  try {
    if (typeof File.preview === 'function' && (await File.canPreview(file))) {
      await File.preview(file, { title: item.name });
      return;
    }
  } catch {
    // An older Expo Go without the viewer: the share sheet below offers « Ouvrir dans… ».
  }
  await Sharing.shareAsync(file.uri, { dialogTitle: item.name });
}

/** The share sheet of the phone: « Enregistrer dans Fichiers », mail, Messages, AirDrop… */
export async function shareDriveFile(drive: DriveGateway, item: DriveItem): Promise<void> {
  if (Platform.OS === 'web') {
    if (!item.webUrl) throw new Error('Mode démo : les fichiers d’exemple n’ont pas de contenu à ouvrir.');
    await Linking.openURL(item.webUrl);
    return;
  }
  const file = await download(drive, item);
  await Sharing.shareAsync(file.uri, { dialogTitle: item.name });
}

/** The item on OneDrive for the web. */
export async function openOnWeb(item: { name: string; webUrl: string }): Promise<void> {
  if (!item.webUrl) throw new Error(`Mode démo : ${item.name}, rien à ouvrir chez Microsoft.`);
  await Linking.openURL(item.webUrl);
}
