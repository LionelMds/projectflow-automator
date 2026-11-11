/**
 * UTF-8 and base64url helpers written out by hand: the JavaScript engines of the
 * three targets (Hermes on iOS/Android, the browser) do not all ship `TextDecoder`.
 */

const ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';

export function utf8Encode(text: string): number[] {
  const bytes: number[] = [];
  for (const char of text) {
    const code = char.codePointAt(0) ?? 0;
    if (code < 0x80) {
      bytes.push(code);
    } else if (code < 0x800) {
      bytes.push(0xc0 | (code >> 6), 0x80 | (code & 0x3f));
    } else if (code < 0x10000) {
      bytes.push(0xe0 | (code >> 12), 0x80 | ((code >> 6) & 0x3f), 0x80 | (code & 0x3f));
    } else {
      bytes.push(
        0xf0 | (code >> 18),
        0x80 | ((code >> 12) & 0x3f),
        0x80 | ((code >> 6) & 0x3f),
        0x80 | (code & 0x3f),
      );
    }
  }
  return bytes;
}

export function utf8Decode(bytes: readonly number[]): string {
  let text = '';
  let index = 0;
  while (index < bytes.length) {
    const first = bytes[index++];
    let code: number;
    if (first < 0x80) {
      code = first;
    } else if (first < 0xe0) {
      code = ((first & 0x1f) << 6) | (bytes[index++] & 0x3f);
    } else if (first < 0xf0) {
      code = ((first & 0x0f) << 12) | ((bytes[index++] & 0x3f) << 6) | (bytes[index++] & 0x3f);
    } else {
      code =
        ((first & 0x07) << 18) |
        ((bytes[index++] & 0x3f) << 12) |
        ((bytes[index++] & 0x3f) << 6) |
        (bytes[index++] & 0x3f);
    }
    text += String.fromCodePoint(code);
  }
  return text;
}

export function base64Encode(bytes: readonly number[]): string {
  let out = '';
  for (let index = 0; index < bytes.length; index += 3) {
    const a = bytes[index];
    const b = bytes[index + 1];
    const c = bytes[index + 2];
    out += ALPHABET[a >> 2];
    out += ALPHABET[((a & 0x03) << 4) | ((b ?? 0) >> 4)];
    out += b === undefined ? '=' : ALPHABET[((b & 0x0f) << 2) | ((c ?? 0) >> 6)];
    out += c === undefined ? '=' : ALPHABET[c & 0x3f];
  }
  return out;
}

export function base64Decode(text: string): number[] {
  const clean = text.replace(/-/g, '+').replace(/_/g, '/').replace(/[^A-Za-z0-9+/]/g, '');
  const bytes: number[] = [];
  let buffer = 0;
  let bits = 0;
  for (const char of clean) {
    buffer = (buffer << 6) | ALPHABET.indexOf(char);
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      bytes.push((buffer >> bits) & 0xff);
    }
  }
  return bytes;
}

/** Sharing token of a OneDrive/SharePoint link, as the desktop builds it (`u!` + base64url). */
export function sharingToken(url: string): string {
  const encoded = base64Encode(utf8Encode(url)).replace(/=+$/, '').replace(/\+/g, '-').replace(/\//g, '_');
  return `u!${encoded}`;
}

/** Claims of a JWT (the id token), without checking its signature: display only. */
export function jwtClaims(token: string): Record<string, unknown> {
  const payload = token.split('.')[1];
  if (!payload) return {};
  try {
    const data = JSON.parse(utf8Decode(base64Decode(payload)));
    return data && typeof data === 'object' ? (data as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}
