/**
 * The redirect address of the desktop sign-in (`auth/browser_sign_in.py`), already in the
 * Entra registration: Microsoft accepts it on any port, so no administrator change is needed.
 * The phone never serves it: the sign-in page runs inside the app and its reply is caught
 * before loading (`components/MicrosoftSignIn.tsx`).
 */
export const REDIRECT_URI = 'http://localhost';

/** True for Microsoft's reply to the sign-in: http://localhost with any port, path or query. */
export function isRedirect(url: string): boolean {
  return /^http:\/\/localhost(?=[:/?#]|$)/i.test(url);
}
