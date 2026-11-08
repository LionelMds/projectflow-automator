type Props = {
  url: string;
  onRedirect: (url: string) => void;
  onCancel: () => void;
};

/** No in-app page in the browser version: Microsoft sign-in is done from the phone (`canSignIn` is false). */
export function MicrosoftSignIn(_props: Props) {
  return null;
}
