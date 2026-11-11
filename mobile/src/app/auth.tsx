import { Redirect } from 'expo-router';

/**
 * Microsoft sends the sign-in back to `projectflow://auth`. The auth session usually
 * catches it first; when the link opens the app instead, go back to the modules.
 */
export default function AuthRedirect() {
  return <Redirect href="/" />;
}
